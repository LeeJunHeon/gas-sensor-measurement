"""
commands.py — 화면 명령 처리(handle_command).

서버가 상태의 주인이다: 명령으로 메모리 상태를 갱신한 뒤 갱신된 state를 push 한다.
일상 push는 recipe 미포함, recipe_new/load/save에만 recipe 포함(편집 중 초안 보존).
"""

import os

from state import state, default_recipe, DEFAULT_PARAMS
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
    cmd = data.get("cmd")

    if cmd == "set_valve":
        ch = int(data.get("ch", -1))
        side = data.get("side")
        is_open = bool(data.get("open"))
        if 0 <= ch < len(state.channels):
            c = state.channels[ch]
            if not c["en"]:
                return  # 비활성 채널 밸브는 잠김
            if side == "in":
                c["valveIn"] = is_open
            elif side == "out":
                c["valveOut"] = is_open
            label = "MFC" if side == "in" else "SOL"
            await push_log(f"{c['id']} {label} 밸브 {'열림' if is_open else '닫힘'}",
                           "ok" if is_open else "warn")
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
            state.channels[ch]["max"] = max(0.0, float(data.get("value") or 0))
            await push_state()

    elif cmd == "set_4way":
        route = data.get("route")
        if route in ("sensor", "vent"):
            state.system["routeOut"] = route
            await push_log(
                f"4-Way 전환 → {'Vent (배기)' if route == 'vent' else 'Sensor (측정)'}", "info")
            await push_state()

    elif cmd == "run":
        state.system["running"] = True
        state.system["safeStop"] = False
        state._elapsed_f = 0.0
        state.system["elapsed"] = 0
        state.system["loop"]["current"] = 0
        await push_log("AUTO RUN 시작 — 레시피 실행", "ok")
        await push_state()

    elif cmd == "stop":
        state.system["running"] = False
        await push_log("AUTO STOP — 시퀀스 정지", "warn")
        await push_state()

    elif cmd == "purge":
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
            c["max"] = item.get("max", c["max"])
            c["sv"] = item.get("sv", c["sv"])
            if en and not was_en:
                c["valveIn"] = True
                c["valveOut"] = True
            elif not en:
                c["valveIn"] = False
                c["valveOut"] = False
        if isinstance(data.get("params"), dict):
            state.params = {**state.params, **data["params"]}
            state.recipe["params"] = dict(state.params)
        state.save_config()
        await push_log("System Setup 적용 — 채널 설정 저장됨", "ok")
        await push_state()

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
        loaded["name"] = name
        loaded.setdefault("useHumidity", True)
        loaded.setdefault("loopCount", 1)
        loaded.setdefault("procs", [])
        loaded.setdefault("params", dict(state.params))
        state.recipe = loaded
        await push_log(f"레시피 불러옴 — {name}", "ok")
        await push_state(include_recipe=True)   # Open은 레시피 교체

    elif cmd == "recipe_list":
        await manager.broadcast({"type": "recipe_list", "names": list_recipes()})

    elif cmd == "exit":
        await push_log("프로그램 종료", "warn")
        if _shutdown_handler is not None:
            _shutdown_handler()
