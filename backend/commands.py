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
import gas_catalog
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


def _track_bottle(recipe) -> None:
    """서버로 들어온 레시피의 봄베 농도를 '마지막 사용값'으로 기억한다(config 에 저장).
    ★ 봄베는 레시피가 아니라 '장비에 물린 실물'이라 세션·레시피를 넘겨 유지한다."""
    b = (recipe or {}).get("bottle")
    if not isinstance(b, list):
        return
    vals = [max(0.0, to_num(x)) for x in (b + [0, 0, 0, 0])[:4]]
    if vals != list(state.last_bottle):
        state.last_bottle = vals
        state.save_config()


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
        # cwd 는 exe 자기 폴더 — 상대경로로 설정·리소스를 읽는 측정 프로그램이 많다.
        subprocess.Popen([p], close_fds=True, cwd=os.path.dirname(p) or None)   # 대기하지 않음
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
            locked_reason = None
            if not state.plc_connected():
                locked_reason = "PLC 미연결 — 조작이 잠겨 있습니다. System Setup에서 연결 후 사용하세요"
            elif state.plc_safety_stop():
                locked_reason = "PLC 안전정지 중 — 조작이 잠겨 있습니다. 안전 리셋 후 사용하세요"
            if locked_reason:
                if cmd == "run":
                    await manager.broadcast({"type": "ack", "of": "run", "ok": False,
                                             "reason": "plc_locked", "problems": [locked_reason]})
                await push_log(locked_reason, "warn")
                return

        # MFC·DAC 알람 인터록 — '신규 조작'만 막는다.
        #   닫기·SV 0·비상정지·해제·리셋·종료는 통과해야 사람이 상황을 정리할 수 있다.
        #   (IDD 단선검출은 제외 — state.alarm_lock() 주석 참고)
        if state.alarm_lock():
            blocked = (cmd in ("run", "purge", "set_4way")
                       or (cmd == "set_valve" and bool(data.get("open")))
                       or (cmd == "set_sv" and to_num(data.get("value"), 0) > 0))
            if blocked:
                await push_log(f"PLC 알람 활성({state.alarm_names()}) — 조작이 잠겨 있습니다", "warn")
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
                if not c["en"]:
                    return  # 비활성 채널 SV 잠김(밸브와 대칭)
                v = max(0.0, float(data.get("value") or 0))
                req = v
                v = min(v, float(c["max"]))
                c["sv"] = v
                # 운전 조작 이력 — 화면 System Log 와 파일 로그(logger)에 동시에 남는다.
                if v <= 0:
                    await push_log(f"{c['id']}: SV 초기화(0 sccm)", "info")
                else:
                    await push_log(f"{c['id']}: SV {v:g} sccm 적용", "info")
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
                _track_bottle(state.recipe)
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

        elif cmd == "pick_measure_app":
            # pywebview 파일 선택 대화상자. 창 객체는 window.WINDOW 가 들고 있다
            # (server.py 가 진입점이라 window 는 일반 모듈로 import 돼 있다).
            path = ""
            try:
                import window as _win
                w = getattr(_win, "WINDOW", None)
                if w is None:
                    raise RuntimeError("창이 없습니다(브라우저 모드)")
                # ★ create_file_dialog 는 사용자가 닫을 때까지 블로킹한다 —
                #   이벤트 루프에서 직접 부르면 폴링·하트비트가 멈춘다. 반드시 스레드로.
                res = await asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: w.create_file_dialog(
                        10,                     # webview.OPEN_DIALOG
                        allow_multiple=False,
                        file_types=("실행 파일 (*.exe)", "모든 파일 (*.*)")),
                )
                if res:
                    path = res[0]
            except Exception as e:  # noqa: BLE001 — 브라우저 모드 등 대화상자 불가
                await push_log(f"파일 선택 창을 열 수 없습니다 — 경로를 직접 입력하세요 ({e})",
                               "warn")
            if path:
                ma = dict(state.settings.get("measureApp") or {})
                ma["path"] = path
                state.settings["measureApp"] = ma
                state.save_config()
                await push_log(f"측정 프로그램 경로 설정 — {path}", "ok")
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
                # 재클릭 = 중단: 퍼지가 열었던 단독 에어만 닫는다
                for c in state.channels:
                    if channel_role(c) == "dry_air" and c.get("route") == "pure":
                        c["valveIn"] = False
                        c["sv"] = 0.0
                state.system["purging"] = False
                # 4-way 는 어차피 OFF(vent) 로 두고 썼지만, 다른 경로로 sensor 가 남아
                # 있었을 수 있으니 기본 위치로 되돌린다(DEC-036, 방어).
                state.system["routeOut"] = "vent"
                await push_log("PURGE 중단 — 단독 에어 밸브 닫음", "info")
                await push_state()
                return
            # ★ DEC-035 개정(2026-08-13): 퍼지 = '단독(pure) 에어를 센서로'.
            #   이전 설계는 혼합 라인(VA1)에 마른공기를 흘리고 4-way 를 sensor 로 돌려
            #   가스 배관을 세정하는 것이었다. 지금은 코일 OFF(무전원) 기본 위치에서
            #   단독 에어가 이미 센서로 가므로 방향 전환 자체가 필요 없다.
            PURGE_DRY_FLOW = 1000.0   # 퍼지 총 에어 유량(sccm) — VA3 풀스케일 1000 과 일치
            pure_idx = [i for i, c in enumerate(state.channels)
                        if channel_role(c) == "dry_air" and c.get("en")
                        and c.get("route") == "pure"]
            # ★ 퍼지할 채널이 하나도 없으면 아무 것도 만지지 않고 거부한다.
            #   그냥 두면 혼합 라인만 닫고 purging=True 로 바꿔놓아 '퍼지 중'으로
            #   보이지만 실제로는 아무것도 흐르지 않는다.
            if not pure_idx:
                await push_log("PURGE 불가 — 단독(pure) 마른공기 채널이 없거나 꺼져 있습니다 "
                               "(System Setup에서 VA3 사용을 확인하세요)", "err")
                return
            # 혼합 라인(가스 + 혼합 에어)은 전부 차단 — 센서로는 단독 에어만 간다.
            for i, c in enumerate(state.channels):
                if c.get("route") == "pure":
                    continue
                c["valveIn"] = False
                c["sv"] = 0.0
            each = 0.0
            for i in pure_idx:
                c = state.channels[i]
                each = min(PURGE_DRY_FLOW / len(pure_idx), float(c.get("max") or 0))
                c["sv"] = each
                c["valveIn"] = True
            # ★ routeOut 은 건드리지 않는다 — 코일 OFF(vent) 가 곧 '단독 에어 → 센서'다.
            state.system["purging"] = True
            await push_log(f"PURGE 시작 — 단독 에어 {each:g} sccm → Sensor (4-way OFF 유지)",
                           "info")
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
                    # C.F. 도 유량 환산을 바꾸는 값이라 스케일과 같이 취급한다
                    # (밸브가 열린 채 바뀌면 실제 유량이 순간 점프한다).
                    if "gas_cf" in sc and cur.get("gas_cf") is not None:
                        if abs(gas_catalog.clamp_cf(sc["gas_cf"], cur["gas_cf"])
                               - float(cur["gas_cf"])) > 1e-9:
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
                    # 가스 C.F.(에어 포함 전 채널). 이름은 참고용, 계산에 쓰는 값은 gas_cf.
                    # 표에 없는 가스를 직접 입력할 수 있으므로 이름으로 값을 되찾지 않는다.
                    if isinstance(sc.get("gas_name"), str) and sc["gas_name"]:
                        c["plc"]["gas_name"] = sc["gas_name"]
                    if "gas_cf" in sc:
                        _old_cf = c["plc"].get("gas_cf", gas_catalog.DEFAULT_GAS_CF)
                        c["plc"]["gas_cf"] = gas_catalog.clamp_cf(sc["gas_cf"], _old_cf)
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
            plc_effective_change = False
            if plc_changed:
                old_plc = dict(state.plc)
                incoming = {**state.plc, **data["plc"]}
                # 방어적 보정: unit_id 1~247, heartbeat는 PLC COMM_TMR(3초) 미만이어야 안전
                incoming["unit_id"] = min(247, max(1, int(to_num(incoming.get("unit_id"), 1)) or 1))
                # ★ config.json 자체도 정상 범위로 유지한다 — 범위는 plc.PLC_COMM_LIMITS
                #   단일 출처(실효값을 만드는 config_from_dict 와 같은 표라 갈라지지 않는다).
                for _k, _d in (("heartbeat_s", 1.0), ("inter_cmd_gap_s", 0.1),
                               ("timeout_s", 1.5), ("reconnect_delay_s", 1.0)):
                    incoming[_k] = plc.clamp_comm(_k, to_num(incoming.get(_k), _d))
                # ★ 실효 설정 비교: 프론트는 plc dict를 항상 통째로 보내므로 '존재 여부'로
                #   판정하면 매 적용마다 재연결된다. 재연결 중 수 ms~수백 ms의 미연결 창을
                #   write 루프가 관측하면 열린 밸브를 전부 닫는다(간헐 사고). 값이 정말
                #   바뀌었을 때만 재연결한다. 타입 차이("502" vs 502)는 config_from_dict
                #   정규화로 흡수된다(dataclass 동등 비교).
                plc_effective_change = (plc.config_from_dict(incoming)
                                        != plc.config_from_dict(old_plc))
                state.plc = incoming
                plc.configure(state.plc)           # 설정 반영(실제 연결은 재연결로)
            if assign_changed:
                # 배정이 바뀌면 유량 명령의 목적지가 바뀐다 — 흐르는 중 전환 사고 방지
                state.close_all_channels()
                await push_log("배정 변경 — 안전을 위해 모든 밸브·유량을 닫았습니다. "
                               "확인 후 다시 여세요", "warn")
            plc.load_addresses(state.channels, state.plc_system)   # 채널 plc 변경분 즉시 반영
            state.save_config()
            await push_log("System Setup 적용 — 채널 설정 저장됨", "ok")
            if plc_effective_change:
                await push_log("PLC 통신 설정 변경 저장됨 — 새 설정으로 재연결합니다", "info")
                await plc.plc.reconnect()          # port 비면 no-op
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

        elif cmd == "set_bottle":
            # 봄베 농도는 '장비에 물린 실물' 이라 레시피와 별개로 config 에 남긴다.
            vals = data.get("values") or []
            state.last_bottle = [max(0.0, to_num(v)) for v in (list(vals) + [0] * 4)[:4]]
            if isinstance(state.recipe, dict):
                state.recipe["bottle"] = list(state.last_bottle)
            state.save_config()

        elif cmd == "recipe_new":
            keep_params = dict(state.recipe.get("params", DEFAULT_PARAMS))
            state.recipe = default_recipe(state.last_bottle)   # 기본 3단계 + 마지막 봄베
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
            _track_bottle(state.recipe)
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
            _track_bottle(state.recipe)
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
                        # ★ write 루프와 같은 판정(state.plc_mapped) — plc=null 채널이 config 에
                        #   있으면 _valve_coil_of 에 키가 없어 차단 쓰기 전체가 KeyError 로 무산된다.
                        p = state.plc_mapped(c)
                        if not p:
                            continue
                        if plc_catalog.valve_coil(c["id"]) is not None:
                            valve_map[c["id"]] = False
                        if plc_catalog.dac_reg(p.get("sv_out")) is not None:
                            sv_map[c["id"]] = 0.0
                    await asyncio.wait_for(plc.plc.write_sv_block(sv_map), 1.0)
                    await asyncio.wait_for(plc.plc.write_valves_block(valve_map, False), 1.0)
                    state.close_all_channels()
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
