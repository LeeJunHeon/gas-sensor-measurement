"""
Gas Sensor Measurement System — 서버 (1단계: 시뮬레이션)

역할
  - 상태(channels / system / recipe)의 단일 주인.
  - WebSocket(/ws)으로 화면과 명령/상태/측정값을 주고받는다.
  - 1단계에서는 하드웨어 대신 시뮬레이션 측정값을 초당 TELEMETRY_HZ회 push 한다.
  - pywebview로 데스크톱 창을 띄워 화면을 표시한다.

추후 단계에서 이 파일의 '시뮬레이션' 부분만 실제 장비 제어 코드로 교체한다.
통신 약속(메시지/스키마)은 INTERFACE.md 참고.
"""

import os
import json
import random
import asyncio
import threading
import contextlib
from typing import Any

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

# ===================== 설정 =====================
TELEMETRY_HZ = 5            # 측정값 전송 빈도(초당 횟수). 숫자만 바꾸면 조절된다.
HOST = "127.0.0.1"
PORT = 8000

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
RECIPES_DIR = os.path.join(BASE_DIR, "recipes")
INDEX_PATH = os.path.join(BASE_DIR, "index.html")
APPJS_PATH = os.path.join(BASE_DIR, "app.js")

os.makedirs(RECIPES_DIR, exist_ok=True)

# ===================== 기본값 =====================
DEFAULT_CHANNELS = [
    {"id": "VA1", "grp": "air", "route": "pure", "en": True,  "max": 2000, "sv": 0},
    {"id": "VA2", "grp": "air", "route": "pure", "en": True,  "max": 2000, "sv": 0},
    {"id": "VA3", "grp": "air", "route": "mix",  "en": True,  "max": 2000, "sv": 0},
    {"id": "VA4", "grp": "air", "route": "mix",  "en": True,  "max": 2000, "sv": 0},
    {"id": "VA5", "grp": "gas", "route": "mix",  "en": True,  "max": 2000, "sv": 0},
    {"id": "VA6", "grp": "gas", "route": "mix",  "en": False, "max": 200,  "sv": 0},
    {"id": "VA7", "grp": "gas", "route": "mix",  "en": False, "max": 200,  "sv": 0},
    {"id": "VA8", "grp": "gas", "route": "mix",  "en": False, "max": 100,  "sv": 0},
]

DEFAULT_PARAMS = {
    "vStart": 0, "vEnd": 0, "vStep": 0,
    "grafInterval": 1,
    "smuMode": "Source V, Measure I",
    "smuSource": 0, "smuCompliance": 1.0,
    "chFrom": 1, "chTo": 1,
}


def default_recipe() -> dict:
    return {
        "name": "",
        "useHumidity": True,
        "loopCount": 1,
        "procs": [],
        "params": dict(DEFAULT_PARAMS),
    }


# ===================== 안전한 파일 입출력 =====================
def atomic_write_json(path: str, obj: Any) -> None:
    """임시 파일에 쓰고 rename → 원자적 저장(중간에 죽어도 파일이 깨지지 않음)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def safe_read_json(path: str):
    """읽기 실패 시 예외로 죽지 않고 None 반환."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] JSON 읽기 실패: {path} ({e})")
        return None


# ===================== 상태 (서버가 주인) =====================
class State:
    def __init__(self):
        self.channels = [dict(c) for c in DEFAULT_CHANNELS]
        self.params = dict(DEFAULT_PARAMS)
        self.system = {
            "running": False,
            "routeOut": "sensor",
            "loop": {"current": 0, "total": 1},
            "elapsed": 0,
            "rh": None,          # 측정 하드웨어 없음 → 화면 "—"
            "smu": None,         # 측정 하드웨어 없음 → 화면 "—"
            "connected": True,   # 서버↔하드웨어 (1단계는 시뮬, 항상 연결됨으로 표시)
            "safeStop": False,
        }
        self.recipe = default_recipe()
        self._elapsed_f = 0.0    # 내부 누적 경과시간(float)

    # ---- config 로드/저장 ----
    def load_config(self):
        data = safe_read_json(CONFIG_PATH)
        if not data:
            print("[info] config.json 없음 — 기본값으로 생성")
            self.save_config()
            return
        chans = data.get("channels")
        if isinstance(chans, list) and chans:
            normalized = []
            for i, c in enumerate(chans):
                base = dict(DEFAULT_CHANNELS[i]) if i < len(DEFAULT_CHANNELS) else {}
                base.update({
                    "id": c.get("id", f"VA{i + 1}"),
                    "grp": c.get("grp", base.get("grp", "air")),
                    "route": c.get("route", base.get("route", "pure")),
                    "en": bool(c.get("en", base.get("en", False))),
                    "max": c.get("max", base.get("max", 2000)),
                    "sv": c.get("sv", base.get("sv", 0)),
                })
                normalized.append(base)
            self.channels = normalized
        if isinstance(data.get("params"), dict):
            self.params = {**DEFAULT_PARAMS, **data["params"]}
            self.recipe["params"] = dict(self.params)
        # 사용 채널은 시작 시 밸브 열림으로 둔다(데모 일관성).
        for c in self.channels:
            c.setdefault("valveIn", bool(c["en"]))
            c.setdefault("valveOut", bool(c["en"]))
            c.setdefault("pv", 0)

    def save_config(self):
        payload = {
            "channels": [
                {"id": c["id"], "grp": c["grp"], "route": c["route"],
                 "en": bool(c["en"]), "max": c["max"], "sv": c["sv"]}
                for c in self.channels
            ],
            "params": self.params,
        }
        try:
            atomic_write_json(CONFIG_PATH, payload)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] config 저장 실패: {e}")

    # ---- 외부로 내보낼 상태 스냅샷 ----
    # 레시피는 "권위 있는 변경"(연결 직후/New/Open/Save) 때만 포함한다.
    # 밸브·4-way·RUN 등 일상 push에는 recipe를 빼서, 편집 중인 레시피 초안을 덮어쓰지 않는다.
    def snapshot(self, include_recipe: bool = False) -> dict:
        snap = {
            "type": "state",
            "channels": [dict(c) for c in self.channels],
            "system": dict(self.system),
        }
        if include_recipe:
            snap["recipe"] = json.loads(json.dumps(self.recipe))
        return snap

    # ---- 시뮬레이션 한 틱 ----
    def sim_tick(self, dt: float) -> dict:
        if self.system["running"]:
            self._elapsed_f += dt
        elapsed = int(self._elapsed_f)
        self.system["elapsed"] = elapsed

        pv = []
        for c in self.channels:
            flowing = c["en"] and c.get("valveIn") and c.get("valveOut")
            if flowing:
                target = float(c.get("sv") or 0)
                amp = 1.6 if target > 0 else 0.4
                val = target + (random.random() - 0.5) * amp
                if val < 0:
                    val = 0.0
            else:
                val = 0.0
            c["pv"] = val
            pv.append(round(val, 2))

        # rh·smu(측정값)는 측정 하드웨어가 아직 없으므로 시뮬레이션하지 않는다(화면은 "—" 표시).
        # 가스 유량(PV)은 MFC 흐름이라 유효 → 위에서 계속 시뮬레이션한다.

        total = int(self.recipe.get("loopCount") or 0) or 1
        self.system["loop"]["total"] = total
        if self.system["running"]:
            self.system["loop"]["current"] = min(total, 1 + elapsed // 10)
        self.system["loop"]["current"] = self.system["loop"].get("current", 0)

        return {
            "type": "telemetry",
            "pv": pv,
            "rh": None,
            "smu": None,
            "elapsed": elapsed,
            "running": self.system["running"],
            "loop": dict(self.system["loop"]),
        }


state = State()
state.load_config()


# ===================== WebSocket 관리 =====================
class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        await self._send(ws, state.snapshot())
        await self._send(ws, {"type": "log", "msg": "화면 ↔ 서버 연결됨", "level": "ok"})

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def _send(self, ws: WebSocket, obj: dict):
        try:
            await ws.send_text(json.dumps(obj, ensure_ascii=False))
        except Exception:  # noqa: BLE001
            self.active.discard(ws)

    async def broadcast(self, obj: dict):
        if not self.active:
            return
        text = json.dumps(obj, ensure_ascii=False)
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_text(text)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()


async def push_state(include_recipe: bool = False):
    await manager.broadcast(state.snapshot(include_recipe=include_recipe))


async def push_log(msg: str, level: str = "info"):
    await manager.broadcast({"type": "log", "msg": msg, "level": level})


# ===================== 종료 =====================
WINDOW = None   # main()에서 생성한 pywebview 창 객체를 보관


def request_shutdown():
    """PROGRAM END → 창을 닫아 프로세스를 깔끔히 종료한다."""
    if WINDOW is not None:
        try:
            WINDOW.destroy()   # webview.start()가 반환되며 데몬 스레드와 함께 종료
            return
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 창 종료 실패: {e}")
    # 창이 없거나(브라우저 폴백) destroy 실패 → 프로세스 종료
    os._exit(0)


# ===================== 레시피 파일 =====================
def valid_recipe_name(name: str) -> bool:
    """슬래시/역슬래시/상위경로 금지, recipes 폴더 밖 금지."""
    if not name or not isinstance(name, str):
        return False
    if "/" in name or "\\" in name or ".." in name or name != os.path.basename(name):
        return False
    target = os.path.abspath(os.path.join(RECIPES_DIR, name + ".json"))
    return os.path.dirname(target) == os.path.abspath(RECIPES_DIR)


def list_recipes() -> list[str]:
    try:
        files = os.listdir(RECIPES_DIR)
    except Exception:  # noqa: BLE001
        return []
    names = [f[:-5] for f in files if f.endswith(".json")]
    names.sort()
    return names


# ===================== 명령 처리 =====================
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
        request_shutdown()


# ===================== FastAPI =====================
@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: 시뮬레이션 telemetry 백그라운드 태스크 시작
    async def telemetry_loop():
        dt = 1.0 / TELEMETRY_HZ
        while True:
            await asyncio.sleep(dt)
            try:
                t = state.sim_tick(dt)
                await manager.broadcast(t)
            except Exception as e:  # noqa: BLE001
                print(f"[warn] telemetry tick 실패: {e}")
    task = asyncio.create_task(telemetry_loop())
    try:
        yield
    finally:
        # shutdown: 태스크 정리
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return FileResponse(INDEX_PATH)


@app.get("/app.js")
async def app_js():
    return FileResponse(APPJS_PATH, media_type="application/javascript")


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and "cmd" in data:
                await handle_command(data)
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:  # noqa: BLE001
        manager.disconnect(ws)


# ===================== 실행 (서버 스레드 + 창) =====================
def run_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def main():
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    try:
        import webview  # pywebview
    except Exception as e:  # noqa: BLE001
        print(f"[info] pywebview를 불러올 수 없습니다 ({e}).")
        print(f"[info] 브라우저에서 http://{HOST}:{PORT} 를 열어 사용하세요. (Ctrl+C 종료)")
        with contextlib.suppress(KeyboardInterrupt):
            server_thread.join()
        return

    global WINDOW
    WINDOW = webview.create_window(
        "Gas Sensor Measurement System",
        f"http://{HOST}:{PORT}",
        width=1480, height=1020,   # 최대화 해제 시 사용할 기본 크기
        maximized=True,            # 실행 시 최대화(타이틀바·작업표시줄 유지)
    )
    webview.start()   # 창을 닫으면 여기서 반환 → 데몬 스레드와 함께 종료


if __name__ == "__main__":
    main()
