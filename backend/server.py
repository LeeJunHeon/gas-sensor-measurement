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
import time
import json
import asyncio
import logging
import threading
import contextlib

# pymodbus 연결 실패("Connection ... failed: timed out") 반복 출력 소음 낮추기.
logging.getLogger("pymodbus").setLevel(logging.WARNING)

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import logger
import plc
from storage import PROJECT_ROOT
from state import state
from simulation import sim_tick
from connection import manager, push_state, push_log
import commands
from commands import handle_command

# ===================== 설정 =====================
TELEMETRY_HZ = 5            # 측정값 전송 빈도(초당 횟수). 숫자만 바꾸면 조절된다.
PLC_POLL_INTERVAL_S = 0.7  # PLC 읽기(PV/상태) 폴링 주기(초).
PLC_WRITE_INTERVAL_S = 0.25  # PLC 쓰기(밸브/SV 반영) 동기화 주기(초).
HOST = "127.0.0.1"
PORT = 8000

# 경로는 스크립트 위치 기준(프로젝트 루트)으로 계산 — CWD 비의존.
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")
INDEX_PATH = os.path.join(FRONTEND_DIR, "index.html")

# ===================== 종료 =====================
WINDOW = None         # main()에서 생성한 pywebview 창 객체를 보관
_allow_close = False  # 창 닫기 허용 플래그(종료 확인 통과 후 True). X 클릭은 모달로 되묻는다.


def request_shutdown():
    """PROGRAM END / 창 X 종료확인 통과 → 확실히 프로세스를 종료한다."""
    global _allow_close
    _allow_close = True
    # 어떤 경로(PROGRAM END·창 X 모달)에서든 확실히 종료: destroy 시도 + 강제종료 백업
    def _force_exit():
        time.sleep(0.3)   # ack/정리 flush 여유 후 강제 종료(데드락과 무관하게 무조건 종료)
        os._exit(0)
    threading.Thread(target=_force_exit, daemon=True).start()
    if WINDOW is not None:
        try:
            WINDOW.destroy()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 창 종료 실패: {e}")


def _on_closing():
    """창 우상단 X → 앱 내부 종료확인 모달로 되묻는다(확인 전엔 닫기 취소).
    ★ WebView2 데드락 방지: evaluate_js를 closing 핸들러에서 '동기' 호출하면 GUI 스레드가
      재진입 데드락에 빠져 멈춘다('응답 없음'). 반드시 별도 스레드에서 호출하고 즉시 반환해야 한다."""
    if _allow_close:
        return True       # 종료확인 통과(또는 destroy 진행 중) → 닫기 허용
    def _ask():
        try:
            WINDOW.evaluate_js("window.requestExitConfirm && window.requestExitConfirm()")
        except Exception:  # noqa: BLE001
            pass
    threading.Thread(target=_ask, daemon=True).start()
    return False          # 확인 전에는 닫기 취소(창 유지). evaluate_js는 위 스레드가 처리.


# commands의 "exit" 명령이 위 종료 함수를 호출하도록 주입.
commands.set_shutdown_handler(request_shutdown)


# ===================== FastAPI =====================
@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: 파일 로거 구성(config의 settings 기준) + PLC 통신 설정 반영(포트가 있으면 연결 유지 루프 시작)
    #          + 시뮬레이션 telemetry 백그라운드 태스크 시작
    logger.configure(state.settings)
    plc.configure(state.plc)
    plc.load_addresses(state.channels, state.plc_system)   # config 주도 주소맵 로드
    await plc.plc.start()   # port 비어있으면 no-op(설정 전 무해)
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

    # PLC 읽기 폴링: 연결돼 있으면 주기적으로 PV/상태를 읽어 state.plc_live 갱신 후 브로드캐스트.
    # 연결/끊김 '전이'만 UI 로그에 한 번씩 남긴다(반복 도배 금지).
    async def plc_poll_loop():
        was_connected = False
        async def _mark_disconnected():
            nonlocal was_connected
            if was_connected:
                await push_log("PLC 연결 끊김", "warn")
            was_connected = False
            if state.plc_live.get("connected") or state.plc_live.get("pv"):
                state.plc_live = {"connected": False, "pv": {}, "status": {}}
                with contextlib.suppress(Exception):
                    await push_state()
        while True:
            await asyncio.sleep(PLC_POLL_INTERVAL_S)
            try:
                if plc.plc.is_connected():
                    res = await plc.plc.poll()
                    state.plc_live = {"connected": True, "pv": res["pv"], "status": res["status"]}
                    if not was_connected:
                        await push_log("PLC 연결됨", "ok")   # 끊김→연결 전이 1회
                        was_connected = True
                    await push_state()
                else:
                    await _mark_disconnected()
            except Exception:  # noqa: BLE001 — 읽기 실패는 연결표시만 내리고 계속
                with contextlib.suppress(Exception):
                    await _mark_disconnected()
    poll_task = asyncio.create_task(plc_poll_loop())

    # PLC 쓰기 동기화: 앱의 '원하는 상태'(밸브 개폐 + 목표유량 SV)를 주기적으로 PLC에 반영.
    # 변경분만 쓰고(last 캐시), 안전정지·미연결이면 절대 열림 명령을 내리지 않는다(fail-safe).
    # 미연결/예외 시 캐시를 비워 재연결·복구 후 전량 재기입한다.
    async def plc_write_loop():
        last = {}   # {채널id: (want_valve, want_sv)} + {"V4W": bool}
        while True:
            await asyncio.sleep(PLC_WRITE_INTERVAL_S)
            try:
                if not plc.plc.is_connected():
                    last.clear()
                    continue
                # 안전정지면 무조건 닫기(열림·유량 명령 금지). status는 읽기 폴링이 채운다.
                safe = (state.plc_live.get("status") or {}).get("SAFETY_STOP") is True
                for ch in state.channels:
                    if not ch.get("plc"):
                        continue                          # 매핑 없는 채널(VA2/VA4/VA7/VA8) 스킵
                    cid = ch["id"]
                    want_valve = (not safe) and bool(ch.get("valveIn"))
                    want_sv = 0 if safe else int(ch.get("sv") or 0)
                    if last.get(cid) != (want_valve, want_sv):
                        await plc.plc.set_valve(cid, want_valve)
                        await plc.plc.write_sv(cid, want_sv)
                        last[cid] = (want_valve, want_sv)
                # 4-way: 앱의 측정 방향(routeOut=='sensor')을 반영. 안전정지면 닫기(False).
                # TODO(하드웨어 확인): V4W 코일 ON=측정(sensor) 방향으로 가정 — 폴러리티는 실기로 검증.
                want_4w = (not safe) and (state.system.get("routeOut") == "sensor")
                if last.get("V4W") != want_4w:
                    await plc.plc.set_valve("V4W", want_4w)
                    last["V4W"] = want_4w
            except Exception:  # noqa: BLE001 — 쓰기 실패(연결문제 등)는 캐시 비우고 다음 주기 재시도
                last.clear()
    write_task = asyncio.create_task(plc_write_loop())

    try:
        yield
    finally:
        # shutdown: 태스크 정리 + PLC 연결 종료
        for t in (task, poll_task, write_task):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        with contextlib.suppress(Exception):
            await plc.plc.stop()


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
    # 창 X(닫기) 클릭 → _on_closing이 종료확인 모달로 되묻는다(False 반환 시 닫기 취소).
    try:
        WINDOW.events.closing += _on_closing
    except Exception as e:  # noqa: BLE001
        print(f"[warn] closing 이벤트 연결 실패: {e}")
    webview.start()   # 창을 닫으면 여기서 반환 → 데몬 스레드와 함께 종료


if __name__ == "__main__":
    main()
