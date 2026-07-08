"""
commands.py — 화면 명령 처리(handle_command).

서버가 상태의 주인이다: 명령으로 메모리 상태를 갱신한 뒤 갱신된 state를 push 한다.
일상 push는 recipe 미포함, recipe_new/load/save에만 recipe 포함(편집 중 초안 보존).
"""

import os

import engine
import logger
import plc
from state import state, default_recipe, DEFAULT_PARAMS, normalize_recipe, to_num
from connection import manager, push_state, push_log
from storage import (
    atomic_write_json, safe_read_json, valid_recipe_name, list_recipes, RECIPES_DIR,
)

# 종료 처리기: server.py가 주입한다(순환 import + __main__/server 모듈 이중화 회피).
_shutdown_handler = None


def set_shutdown_handler(fn):
    global _shutdown_handler
    _shutdown_handler = fn


async def handle_command(data: dict):
    try:
        cmd = data.get("cmd")
        # 자동 실행 중에는 수동 채널 조작 차단(엔진과 충돌 방지)
        if state.system.get("running") and cmd in ("set_valve", "set_sv", "set_max", "apply_setup"):
            await push_log("자동 실행 중에는 수동 조작이 잠깁니다 (AUTO STOP 후 가능)", "warn")
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
                v = min(v, float(c["max"]))
                c["sv"] = v
                await push_state()

        elif cmd == "set_max":
            ch = int(data.get("ch", -1))
            if 0 <= ch < len(state.channels):
                c = state.channels[ch]
                c["max"] = max(0.0, to_num(data.get("value")))
                if to_num(c.get("sv")) > c["max"]:
                    c["sv"] = c["max"]
                await push_state()

        elif cmd == "set_4way":
            route = data.get("route")
            if route in ("sensor", "vent"):
                state.system["routeOut"] = route
                await push_log(
                    f"4-Way 전환 → {'Vent (배기)' if route == 'vent' else 'Sensor (측정)'}", "info")
                await push_state()

        elif cmd == "run":
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
                    state._elapsed_f = 0.0
                    state.system["elapsed"] = 0
                    started = engine.start()
                    if started:
                        await push_log("AUTO RUN 시작 — 레시피 실행", "ok")
                    else:
                        await push_log("이미 자동 실행 중입니다", "warn")
                await push_state()

        elif cmd == "stop":
            engine.stop()
            await push_log("AUTO STOP — 자동 진행 중단(현재 상태 유지)", "info")
            await push_state()

        elif cmd == "emergency":
            engine.emergency()
            await push_log("⛔ 비상정지 — 전 채널 차단", "err")
            await push_state()

        elif cmd == "purge":
            if state.system.get("running"):
                await push_log("자동 실행 중에는 PURGE 불가 (AUTO STOP 후)", "warn")
                return
            # 가스 채널 닫고 SV=0, 마른 공기 채널을 열어 일정 유량으로 라인 청소
            from state import channel_role
            PURGE_DRY_FLOW = 1000.0   # 청소용 총 마른공기 유량(sccm)
            dry_idx = [i for i, c in enumerate(state.channels)
                       if channel_role(c) == "dry_air" and c.get("en")]
            for i, c in enumerate(state.channels):
                role = channel_role(c)
                if role == "gas":
                    c["valveIn"] = False
                    c["sv"] = 0.0
                elif role == "dry_air" and c.get("en"):
                    c["valveIn"] = True
                    c["sv"] = min(PURGE_DRY_FLOW / max(1, len(dry_idx)), float(c.get("max") or 0))
                elif role == "wet_air":
                    c["sv"] = 0.0
            state.system["routeOut"] = "sensor"
            await push_log("PURGE — 순수 Air로 라인 청소", "info")
            await push_state()

        elif cmd == "apply_setup":
            chans = data.get("channels") or []
            for item in chans:
                i = int(item.get("ch", -1))
                if not (0 <= i < len(state.channels)):
                    continue
                c = state.channels[i]
                was_en = c["en"]
                en = bool(item.get("en", c["en"]))
                c["en"] = en
                c["grp"] = item.get("grp", c["grp"])
                c["route"] = item.get("route", c["route"])
                c["max"] = max(0.0, to_num(item.get("max"), c["max"]))
                c["sv"] = min(max(0.0, to_num(item.get("sv"), c["sv"])), c["max"])
                if en and not was_en:
                    c["valveIn"] = True
                elif not en:
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
            plc.load_addresses(state.channels, state.plc_system)   # 채널 plc 변경분 즉시 반영
            state.save_config()
            await push_log("System Setup 적용 — 채널 설정 저장됨", "ok")
            if plc_changed:
                await push_log("PLC 통신 설정 저장됨 — 재연결해야 적용됩니다", "info")
                await plc.plc.reconnect()          # 새 설정으로 재연결(port 비면 no-op)
            await push_state()

        elif cmd == "plc_ports":
            # System Setup 모달의 포트 드롭다운 채우기용(pyserial 없으면 빈 목록)
            await manager.broadcast({"type": "plc_ports", "ports": plc.list_serial_ports()})

        elif cmd == "plc_reset":
            # 안전리셋(M112) 순간 펄스. 공압·통신 정상이면 PLC가 운전허가를 재가동.
            try:
                await plc.safety_reset()
                await push_log("안전리셋 펄스 전송 — 공압·통신 정상이면 운전허가 재가동", "ok")
            except Exception as e:  # noqa: BLE001
                await push_log(f"안전리셋 실패 — PLC 미연결/통신오류 ({e})", "err")

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
                    {"type": "ack", "of": "recipe_save", "ok": False, "reason": "invalid", "name": name})
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
            await push_log("프로그램 종료", "info")
            if _shutdown_handler is not None:
                _shutdown_handler()
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 명령 처리 실패: {data.get('cmd')} ({e})")
        try:
            await push_log(f"명령 처리 오류 — {data.get('cmd')}", "err")
        except Exception:  # noqa: BLE001
            pass
