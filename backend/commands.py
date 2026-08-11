"""
commands.py — 화면 명령 처리(handle_command).

서버가 상태의 주인이다: 명령으로 메모리 상태를 갱신한 뒤 갱신된 state를 push 한다.
일상 push는 recipe 미포함, recipe_new/load/save에만 recipe 포함(편집 중 초안 보존).
"""

import os
import asyncio

import engine
import logger
import plc
import plc_catalog
from state import (state, default_recipe, DEFAULT_PARAMS, normalize_recipe, to_num,
                   validate_channel_map)
from connection import manager, push_state, push_log
from storage import (
    atomic_write_json, safe_read_json, valid_recipe_name, list_recipes, RECIPES_DIR,
)

# 종료 처리기: server.py가 주입한다(순환 import + __main__/server 모듈 이중화 회피).
_shutdown_handler = None


def set_shutdown_handler(fn):
    global _shutdown_handler
    _shutdown_handler = fn


def _launch_measure_app() -> tuple[bool, str]:
    """측정 프로그램(외부 exe)을 띄운다. 성공하면 (True, 파일명), 실패하면 (False, 사유).
    ★ 가스 제어를 방해하면 안 된다 — 절대 기다리지 않고(Popen만), shell=True도 쓰지 않는다.
      실패는 예외로 올리지 않고 사유 문자열로 돌려준다(호출부가 로그만 남기고 계속 진행)."""
    import subprocess
    # ★ 실패 문구는 호출부가 "측정 프로그램 " 을 앞에 붙여 쓰므로 주어를 넣지 않는다.
    p = (state.settings.get("measureApp") or {}).get("path", "").strip()
    if not p:
        return False, "경로가 설정되지 않았습니다"
    if not os.path.isfile(p):
        return False, f"실행 파일을 찾을 수 없습니다 — {p}"
    try:
        subprocess.Popen([p], close_fds=True)   # 대기하지 않음
        return True, os.path.basename(p)
    except Exception as e:  # noqa: BLE001
        return False, f"실행 실패 — {e}"


async def handle_command(data: dict):
    try:
        cmd = data.get("cmd")
        # PLC가 명령을 실제로 수행할 수 없는 상태(미연결·안전정지)에서는 물리 조작을
        # 전면 잠근다. 화면만 바뀌고 하드웨어는 안 움직이는 거짓 상태와,
        # 안전 리셋 순간 미리 세팅된 밸브가 일괄 개방되는 사고를 함께 막는다.
        # ★ System Setup(apply_setup)·set_max·레시피·리셋/재연결·비상정지는 잠그지 않는다
        #   — 연결 설정과 안전 조작은 항상 가능해야 한다.
        if cmd in ("set_valve", "set_sv", "purge", "run", "set_4way"):
            live = state.plc_live or {}
            locked_reason = None
            if not live.get("connected"):
                locked_reason = "PLC 미연결 — 조작이 잠겨 있습니다. System Setup에서 연결 후 사용하세요"
            elif (live.get("status") or {}).get("SAFETY_STOP") is True:
                locked_reason = "PLC 안전정지 중 — 조작이 잠겨 있습니다. 안전 리셋 후 사용하세요"
            if locked_reason:
                if cmd == "run":
                    await manager.broadcast({"type": "ack", "of": "run", "ok": False,
                                             "reason": "plc_locked", "problems": [locked_reason]})
                await push_log(locked_reason, "warn")
                return

        # 자동 실행 중에는 수동 채널 조작 차단(엔진과 충돌 방지)
        # ★ set_4way 포함 — 실행 중 방향을 바꾸면 엔진의 준비/측정 전환과 충돌해
        #   측정 구간에 가스가 vent로 빠지는 조용한 오측정이 된다.
        if state.system.get("running") and cmd in ("set_valve", "set_sv", "set_max",
                                                   "apply_setup", "set_4way"):
            await push_log("자동 실행 중에는 수동 조작이 잠깁니다 (AUTO STOP 후 가능)", "warn")
            return

        # 비상정지 중에는 수동 조작·실행을 막는다. engine.emergency()가 한 번 닫아 두어도
        # 이 검사가 없으면 곧바로 다시 열 수 있어 비상정지가 지속되지 않는다.
        if state.system.get("safeStop") and cmd in ("set_valve", "set_sv", "purge", "run"):
            await push_log("비상정지 상태입니다. 해제 후 조작하세요", "warn")
            return

        if cmd == "set_valve":
            ch = int(data.get("ch", -1))
            is_open = bool(data.get("open"))
            if 0 <= ch < len(state.channels):
                c = state.channels[ch]
                if not c["en"]:
                    return  # 비활성 채널 밸브는 잠김
                c["valveIn"] = is_open
                await push_log(f"{c['id']} VA 밸브 {'열림' if is_open else '닫힘'}",
                               "ok" if is_open else "info")
                await push_state()

        elif cmd == "set_sv":
            ch = int(data.get("ch", -1))
            if 0 <= ch < len(state.channels):
                c = state.channels[ch]
                v = max(0.0, float(data.get("value") or 0))
                req = v
                v = min(v, float(c["max"]))
                c["sv"] = v
                # 조용히 깎으면 화면 값과 실제 지령이 달라진 걸 모른다 — 클램프됐으면 알린다.
                if req > v:
                    await push_log(f"{c['id']}: SV {req:g} → {v:g} sccm (MAX 제한)", "warn")
                await push_state()

        elif cmd == "set_max":
            ch = int(data.get("ch", -1))
            if 0 <= ch < len(state.channels):
                c = state.channels[ch]
                c["max"] = max(0.0, to_num(data.get("value")))
                if to_num(c.get("sv")) > c["max"]:
                    _old = to_num(c.get("sv"))
                    c["sv"] = c["max"]
                    await push_log(
                        f"{c['id']}: MAX 변경으로 SV {_old:g} → {c['max']:g} sccm", "warn")
                # MAX가 MFC 하드웨어 풀스케일을 넘으면 SV가 포화돼 화면 값과 실제 유량이 달라진다.
                # 운전 상한 자체는 사용자 판단이므로 막지 않고 경고만 남긴다.
                fs = float((c.get("plc") or {}).get("fs_sccm") or 0)
                if fs > 0 and float(c["max"]) > fs + 1e-6:
                    await push_log(
                        f"{c['id']}: MAX {c['max']:g}이 풀스케일 {fs:g}을 초과합니다. "
                        f"SV가 풀스케일에서 포화되어 화면 값과 실제 유량이 달라집니다", "warn")
                await push_state()

        elif cmd == "set_4way":
            route = data.get("route")
            if route in ("sensor", "vent"):
                state.system["routeOut"] = route
                await push_log(
                    f"4-Way 전환 → {'Vent (배기)' if route == 'vent' else 'Sensor (측정)'}", "info")
                await push_state()

        elif cmd == "run":
            # PLC 미연결·안전정지 거부는 위 잠금 게이트가 처리한다(중복 안내 금지).
            # 화면이 현재 표 레시피를 함께 보내면 그걸 실행용으로 반영(저장은 하지 않음).
            # 이름은 기존 것을 유지 → Save as 전까지 파일에 쓰지 않고 실행만.
            if isinstance(data.get("recipe"), dict):
                incoming = normalize_recipe(data["recipe"])
                incoming["name"] = state.recipe.get("name", "") or incoming.get("name", "")
                state.recipe = incoming
            problems = engine.precheck(state.recipe)
            if problems:
                await manager.broadcast({"type": "ack", "of": "run", "ok": False,
                                         "reason": "invalid", "problems": problems})
                await push_log("AUTO RUN 불가 — " + " / ".join(problems[:3])
                               + (" …" if len(problems) > 3 else ""), "err")
                await push_state()
            else:
                if engine.is_running():
                    await push_log("이미 자동 실행 중입니다", "warn")
                else:
                    # 측정 프로그램 자동 실행(옵션) — 기동 후 첫 AUTO RUN 때 1회만.
                    # ★ 실패해도 return 하지 않는다: 레시피는 계속 진행한다.
                    ma = state.settings.get("measureApp") or {}
                    if ma.get("autoLaunch") and not state.system.get("measureLaunched"):
                        ok, msg = _launch_measure_app()
                        await push_log(("측정 프로그램 실행 — " + msg) if ok
                                       else ("측정 프로그램 " + msg + " (레시피는 계속 진행)"),
                                       "ok" if ok else "warn")
                        state.system["measureLaunched"] = True   # 성공·실패 무관 1회만 시도
                    state._elapsed_f = 0.0
                    state.system["elapsed"] = 0
                    started = engine.start()
                    if started:
                        await push_log("AUTO RUN 시작 — 레시피 실행", "ok")
                    else:
                        await push_log("이미 자동 실행 중입니다", "warn")
                await push_state()

        elif cmd == "set_measure_app":
            # 측정 프로그램 경로·자동실행 여부 저장. 실행은 하지 않는다.
            ma = dict(state.settings.get("measureApp") or {})
            if "path" in data:
                ma["path"] = str(data.get("path") or "").strip()
            if "autoLaunch" in data:
                ma["autoLaunch"] = bool(data.get("autoLaunch"))
            state.settings["measureApp"] = ma
            state.save_config()
            await push_state()

        elif cmd == "launch_measure":
            # 수동 실행. ★ measureLaunched는 건드리지 않는다 —
            #   이 플래그는 'AUTO RUN 자동 실행을 썼는가'만 뜻한다.
            ok, msg = _launch_measure_app()
            await push_log(("측정 프로그램 실행 — " + msg) if ok
                           else ("측정 프로그램 " + msg), "ok" if ok else "err")

        elif cmd == "stop":
            engine.stop()
            await push_log("AUTO STOP — 자동 진행 중단 — 가스 차단", "info")
            await push_state()

        elif cmd == "emergency":
            state.system["purging"] = False
            engine.emergency()
            await push_log("⛔ 비상정지 — 전 채널 차단", "err")
            await push_state()

        elif cmd == "clear_emergency":
            # 비상정지 해제. ★ 밸브를 자동으로 열지 않는다 — 사람이 확인하고 다시 연다.
            state.system["safeStop"] = False
            await push_log("비상정지 해제 — 수동 조작이 가능합니다", "info")
            await push_state()

        elif cmd == "purge":
            if state.system.get("running"):
                await push_log("자동 실행 중에는 PURGE 불가 (AUTO STOP 후)", "warn")
                return
            # PLC 미연결·안전정지 거부는 위 잠금 게이트가 처리한다(중복 안내 금지).
            from state import channel_role
            if state.system.get("purging"):
                # 재클릭 = 중단: 퍼지가 열었던 마른공기 채널만 닫는다
                for c in state.channels:
                    if channel_role(c) == "dry_air" and c.get("route") != "pure":
                        c["valveIn"] = False
                        c["sv"] = 0.0
                state.system["purging"] = False
                await push_log("PURGE 중단 — 마른공기 밸브 닫음", "info")
                await push_state()
                return
            # 가스 채널 닫고 SV=0, 마른 공기 채널을 열어 일정 유량으로 라인 청소
            PURGE_DRY_FLOW = 1000.0   # 청소용 총 마른공기 유량(sccm)
            # ★ 청소 대상은 가스 매니폴드에 합류하는 혼합(mix) 마른공기뿐이다.
            #   단독(pure) 라인은 4-way로 직행해 가스라인을 지나지 않는다.
            dry_idx = [i for i, c in enumerate(state.channels)
                       if channel_role(c) == "dry_air" and c.get("en")
                       and c.get("route") != "pure"]
            for i, c in enumerate(state.channels):
                role = channel_role(c)
                if role == "gas":
                    c["valveIn"] = False
                    c["sv"] = 0.0
                elif role == "dry_air" and c.get("en") and c.get("route") != "pure":
                    c["valveIn"] = True
                    c["sv"] = min(PURGE_DRY_FLOW / max(1, len(dry_idx)), float(c.get("max") or 0))
                elif role == "wet_air":
                    c["sv"] = 0.0
            state.system["routeOut"] = "sensor"
            state.system["purging"] = True
            await push_log("PURGE — 순수 Air로 라인 청소", "info")
            await push_state()

        elif cmd == "apply_setup":
            chans = data.get("channels") or []

            # ── 배정(sv_out/pv_in) 후보 검증 — 문제가 있으면 통째로 거부(부분 적용 없음) ──
            def _norm(v):
                v = (v or "").strip() if isinstance(v, str) else v
                return v if v else None
            problems, sv_seen, pv_seen = [], {}, {}
            dac_ok = set(plc_catalog.dac_names(state.plc_hw.get("dac_modules", 1)))
            adc_ok = set(plc_catalog.adc_names())
            # ★ 중복은 '적용 후 최종 배정' 기준으로 본다 — 제출된 항목끼리만 비교하면
            #   화면이 안 보낸 채널이 이미 쓰는 채널을 빼앗아도 통과한다.
            _sent = {}
            for item in data.get("channels", []):
                cid = item.get("id")
                if cid is None:
                    i = int(item.get("ch", -1))
                    if 0 <= i < len(state.channels):
                        cid = state.channels[i]["id"]
                if cid is not None:
                    _sent[cid] = item
            for c in state.channels:
                cid = c["id"]
                cur = c.get("plc") or {}
                item = _sent.get(cid, {})
                sent_sv, sent_pv = "sv_out" in item, "pv_in" in item
                sv = _norm(item.get("sv_out")) if sent_sv else cur.get("sv_out")
                pv = _norm(item.get("pv_in")) if sent_pv else cur.get("pv_in")
                if sv is not None:
                    if sv not in dac_ok:
                        if sent_sv:   # 기존 값이 이상한 경우는 기동 진단이 따로 알린다
                            problems.append(f"{cid}: SV 출력 '{sv}' 은(는) 사용할 수 없습니다(미장착 모듈 또는 잘못된 이름)")
                    elif sv in sv_seen:
                        problems.append(f"{cid}: SV 출력 '{sv}' 이(가) {sv_seen[sv]} 와(과) 중복 배정")
                    else:
                        sv_seen[sv] = cid
                if pv is not None:
                    if pv not in adc_ok:
                        if sent_pv:
                            problems.append(f"{cid}: PV 입력 '{pv}' 은(는) 잘못된 이름입니다")
                    elif pv in pv_seen:
                        problems.append(f"{cid}: PV 입력 '{pv}' 이(가) {pv_seen[pv]} 와(과) 중복 배정")
                    else:
                        pv_seen[pv] = cid
            if problems:
                await manager.broadcast({"type": "ack", "of": "apply_setup",
                                         "ok": False, "problems": problems})
                for p in problems:
                    await push_log("설정 거부 — " + p, "err")
                return

            _by_id = {c["id"]: n for n, c in enumerate(state.channels)}
            # ── 스케일 변경은 밸브가 모두 닫혀 있을 때만 ──────────────────────────
            # 스케일이 바뀌면 같은 SV 숫자가 다른 카운트로 나간다. 흐르는 중에 바꾸면
            # 유량이 순간 튄다 — 값이 실제로 달라지는 항목이 있을 때만 막는다.
            def _scale_changing() -> bool:
                for item in chans:
                    sc = item.get("scale")
                    if not isinstance(sc, dict):
                        continue
                    i = int(to_num(item.get("ch"), -1))
                    if not (0 <= i < len(state.channels)):
                        i = _by_id.get(item.get("id"), -1)
                    if not (0 <= i < len(state.channels)):
                        continue
                    cur = state.channels[i].get("plc")
                    if not isinstance(cur, dict):
                        continue
                    for k in ("fs_sccm", "sv_full", "pv_zero", "pv_full"):
                        if k in sc and max(0, to_num(sc[k], cur.get(k, 0))) != cur.get(k, 0):
                            return True
                return False
            if _scale_changing() and any(c.get("valveIn") for c in state.channels):
                _p = ["밸브가 열려 있어 스케일을 변경할 수 없습니다 — 모든 밸브를 닫은 뒤 적용하세요"]
                await manager.broadcast({"type": "ack", "of": "apply_setup",
                                         "ok": False, "problems": _p})
                for p in _p:
                    await push_log("설정 거부 — " + p, "err")
                return

            assign_changed = False   # 배정이 하나라도 바뀌면 루프 뒤에서 전체를 닫는다
            for item in chans:
                i = int(item.get("ch", -1))
                if not (0 <= i < len(state.channels)):
                    i = _by_id.get(item.get("id"), -1)   # ch 없이 id로 온 항목도 받는다
                if not (0 <= i < len(state.channels)):
                    continue
                c = state.channels[i]
                en = bool(item.get("en", c["en"]))
                c["en"] = en
                c["grp"] = item.get("grp", c["grp"])
                c["route"] = item.get("route", c["route"])
                c["max"] = max(0.0, to_num(item.get("max"), c["max"]))
                c["sv"] = min(max(0.0, to_num(item.get("sv"), c["sv"])), c["max"])
                # 아날로그 스케일과 배정(sv_out/pv_in)을 화면에서 수정할 수 있다.
                # ★ 배정은 UI 편집 허용 — 카탈로그 드롭다운(오타 불가) + 중복/미장착 검증(위에서
                #   통째 거부) + 변경 시 전체 닫힘으로 보호한다.
                #   밸브 코일(plc_catalog가 채널 id로 결정)과 dac_modules는 계속 편집 불가.
                sc = item.get("scale")
                if isinstance(sc, dict) and isinstance(c.get("plc"), dict):
                    for k in ("fs_sccm", "sv_full", "pv_zero", "pv_full"):
                        if k in sc:
                            c["plc"][k] = max(0, to_num(sc[k], c["plc"].get(k, 0)))
                if isinstance(c.get("plc"), dict):
                    if "sv_out" in item and _norm(item.get("sv_out")) != c["plc"].get("sv_out"):
                        c["plc"]["sv_out"] = _norm(item.get("sv_out")); assign_changed = True
                    if "pv_in" in item and _norm(item.get("pv_in")) != c["plc"].get("pv_in"):
                        c["plc"]["pv_in"] = _norm(item.get("pv_in")); assign_changed = True
                # ★ 채널을 켜도 밸브는 자동으로 열지 않는다 — 배관도에서 사람이 연다.
                #   (끄면 닫는 것은 안전 방향이므로 유지)
                if not en:
                    c["valveIn"] = False
            if isinstance(data.get("params"), dict):
                state.params = {**state.params, **data["params"]}
                state.recipe["params"] = dict(state.params)
            if isinstance(data.get("settings"), dict):
                state.settings = {**state.settings, **data["settings"]}
                logger.configure(state.settings)   # 변경 즉시 로거 재설정
            plc_changed = isinstance(data.get("plc"), dict)
            if plc_changed:
                incoming = {**state.plc, **data["plc"]}
                # 방어적 보정: unit_id 1~247, heartbeat는 PLC COMM_TMR(3초) 미만이어야 안전
                incoming["unit_id"] = min(247, max(1, int(to_num(incoming.get("unit_id"), 1)) or 1))
                state.plc = incoming
                plc.configure(state.plc)           # 설정 반영(실제 연결은 재연결로)
            if assign_changed:
                # 배정이 바뀌면 유량 명령의 목적지가 바뀐다 — 흐르는 중 전환 사고 방지
                for c in state.channels:
                    c["valveIn"] = False
                    c["sv"] = 0.0
                await push_log("배정 변경 — 안전을 위해 모든 밸브·유량을 닫았습니다. "
                               "확인 후 다시 여세요", "warn")
            plc.load_addresses(state.channels, state.plc_system)   # 채널 plc 변경분 즉시 반영
            state.save_config()
            await push_log("System Setup 적용 — 채널 설정 저장됨", "ok")
            if plc_changed:
                await push_log("PLC 통신 설정 저장됨 — 재연결해야 적용됩니다", "info")
                await plc.plc.reconnect()          # 새 설정으로 재연결(port 비면 no-op)
            await manager.broadcast({"type": "ack", "of": "apply_setup", "ok": True})
            for n in validate_channel_map(state.channels, state.plc_hw):
                await push_log("배정 진단 — " + n["msg"], n.get("level", "info"))
            await push_state()

        elif cmd == "plc_ports":
            # System Setup 모달의 포트 드롭다운 채우기용(pyserial 없으면 빈 목록)
            await manager.broadcast({"type": "plc_ports", "ports": plc.list_serial_ports()})

        elif cmd == "plc_reset":
            # 안전리셋(M112) 순간 펄스. 통신 정상이면 PLC가 운전허가를 재가동.
            try:
                await plc.safety_reset()
                await push_log("운전 준비 신호 전송 — 통신 정상이면 운전허가 재가동", "ok")
            except Exception as e:  # noqa: BLE001
                await push_log(f"안전리셋 실패 — PLC 미연결/통신오류 ({e})", "err")

        elif cmd == "plc_reconnect":
            # PLC 연결 루프를 끊고 현재 설정으로 재시작 → 즉시 연결 결과를 로그로 돌려준다.
            try:
                ok = await plc.plc.reconnect()
                if ok:
                    await push_log("PLC 재연결 성공", "ok")
                else:
                    # 조치 가능한 원인을 구체적으로 알린다(exe에는 콘솔이 없다).
                    await push_log(f"PLC 재연결 실패 — {plc.plc.diagnose_connection()}", "warn")
            except Exception as e:  # noqa: BLE001
                await push_log(f"PLC 재연결 실패 — {plc.plc.diagnose_connection()} ({e})", "warn")

        elif cmd == "recipe_new":
            keep_params = dict(state.recipe.get("params", DEFAULT_PARAMS))
            state.recipe = default_recipe()
            state.recipe["params"] = keep_params
            await push_log("새 레시피 — 빈 레시피로 초기화", "info")
            await push_state(include_recipe=True)   # New는 레시피 교체

        elif cmd == "recipe_save":
            name = data.get("name")
            overwrite = bool(data.get("overwrite"))
            recipe = data.get("recipe") or {}
            if not valid_recipe_name(name):
                await manager.broadcast(
                    {"type": "ack", "of": "recipe_save", "ok": False, "reason": "invalid",
                     "name": name,
                     "msg": "사용할 수 없는 이름입니다. < > : \" | ? * 문자, 끝의 공백·점, "
                            "CON·COM1 같은 Windows 예약어는 쓸 수 없습니다"})
                await push_log(f"레시피 저장 실패 — 잘못된 이름: {name}", "err")
                return
            path = os.path.join(RECIPES_DIR, name + ".json")
            if os.path.exists(path) and not overwrite:
                await manager.broadcast(
                    {"type": "ack", "of": "recipe_save", "ok": False, "reason": "exists", "name": name})
                return
            recipe = normalize_recipe(recipe)
            recipe["name"] = name
            try:
                atomic_write_json(path, recipe)
            except Exception as e:  # noqa: BLE001
                await manager.broadcast(
                    {"type": "ack", "of": "recipe_save", "ok": False, "reason": "io", "name": name})
                await push_log(f"레시피 저장 실패 — {e}", "err")
                return
            state.recipe = recipe
            await manager.broadcast({"type": "ack", "of": "recipe_save", "ok": True, "name": name})
            await push_log(f"레시피 저장됨 — {name}", "ok")
            await push_state(include_recipe=True)   # Save 후 저장된 레시피로 동기화

        elif cmd == "recipe_load":
            name = data.get("name")
            if not valid_recipe_name(name):
                await push_log(f"레시피 불러오기 실패 — 잘못된 이름: {name}", "err")
                return
            loaded = safe_read_json(os.path.join(RECIPES_DIR, name + ".json"))
            if not isinstance(loaded, dict):
                await push_log(f"레시피 불러오기 실패 — 파일 없음/손상: {name}", "err")
                return
            loaded = normalize_recipe(loaded)
            loaded["name"] = name
            state.recipe = loaded
            await push_log(f"레시피 불러옴 — {name}", "ok")
            await push_state(include_recipe=True)   # Open은 레시피 교체

        elif cmd == "recipe_list":
            await manager.broadcast({"type": "recipe_list", "names": list_recipes()})

        elif cmd == "exit":
            # 정상 종료 한정 개선: 죽기 직전 가스 차단 1회 쓰기(SV 먼저 → 밸브).
            # 크래시·강제종료는 래더 하트비트 3초 트립이 최후 방어선이다(변경 불가·불필요).
            # 차단 쓰기가 실패하거나 늦어도 종료는 계속돼야 한다 → wait_for + 예외 무시.
            try:
                if plc.plc.is_connected():
                    sv_map, valve_map = {}, {}
                    for c in state.channels:
                        p = c.get("plc") or {}
                        if plc_catalog.valve_coil(c["id"]) is not None:
                            valve_map[c["id"]] = False
                        if plc_catalog.dac_reg(p.get("sv_out")) is not None:
                            sv_map[c["id"]] = 0.0
                    await asyncio.wait_for(plc.plc.write_sv_block(sv_map), 1.0)
                    await asyncio.wait_for(plc.plc.write_valves_block(valve_map, False), 1.0)
                    for c in state.channels:
                        c["sv"] = 0.0
                        c["valveIn"] = False
                    await push_log("종료 — 가스 차단(모든 밸브·유량 닫음)", "info")
            except Exception:  # noqa: BLE001
                logger.write("warn", "종료 시 가스 차단 쓰기 실패 — 래더 3초 트립이 닫는다")
            state.system["purging"] = False
            await push_log("프로그램 종료", "info")
            if _shutdown_handler is not None:
                _shutdown_handler()
    except Exception as e:  # noqa: BLE001
        logger.write("err", f"명령 처리 실패: {data.get('cmd')} ({e})")
        try:
            await push_log(f"명령 처리 오류 — {data.get('cmd')}", "err")
        except Exception:  # noqa: BLE001
            pass
