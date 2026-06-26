"""
server.py — 진입점.

- FastAPI 앱 + 라우트(정적 서빙 /, /css, /js, /health) + WebSocket(/ws) + lifespan(telemetry).
- pywebview 창을 띄우고(최대화), 창을 닫으면 깔끔히 종료한다.

실행: 프로젝트 루트에서  python backend/server.py

상태/명령/시뮬레이션/연결/파일은 각 모듈로 분리:
  state.py · commands.py · simulation.py · storage.py · connection.py
통신 약속(메시지/스키마)은 INTERFACE.md 참고.
"""

import os
import json
import asyncio
import threading
import contextlib

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from storage import PROJECT_ROOT
from state import state
from simulation import sim_tick
from connection import manager
import commands
from commands import handle_command

# ===================== 설정 =====================
TELEMETRY_HZ = 5            # 측정값 전송 빈도(초당 횟수). 숫자만 바꾸면 조절된다.
HOST = "127.0.0.1"
PORT = 8000

# 경로는 스크립트 위치 기준(프로젝트 루트)으로 계산 — CWD 비의존.
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")

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


# commands의 "exit" 명령이 위 종료 함수를 호출하도록 주입.
commands.set_shutdown_handler(request_shutdown)


# ===================== FastAPI =====================
@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: 시뮬레이션 telemetry 백그라운드 태스크 시작
    async def telemetry_loop():
        dt = 1.0 / TELEMETRY_HZ
        while True:
            await asyncio.sleep(dt)
            try:
                t = sim_tick(state, dt)
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


# 정적 파일: frontend/css, frontend/js. (라우트(/ /health /ws) 등록 뒤에 마운트)
app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")


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
