"""
server.py — 진입점.

- FastAPI 앱 + 라우트(정적 서빙 /, /css, /js, /health) + WebSocket(/ws) + lifespan.
- 백그라운드 주기 태스크는 loops.py, pywebview 창은 window.py가 담당한다.

실행: 프로젝트 루트에서  python backend/server.py

상태/명령/시뮬레이션/연결/파일/경로는 각 모듈로 분리:
  state.py · commands.py · simulation.py · storage.py · connection.py · paths.py
통신 약속(메시지/스키마)은 INTERFACE.md 참고.
"""

import os
import json
import logging
import contextlib

# pymodbus 연결 실패("Connection ... failed: timed out") 반복 출력 소음 낮추기.
logging.getLogger("pymodbus").setLevel(logging.WARNING)

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import logger
import plc
import loops
import window
import plc_catalog
import version
from paths import FRONTEND_DIR, INDEX_PATH, DATA_ROOT, check_writable
from state import state, validate_channel_map
from connection import manager
import commands
from commands import handle_command

# ===================== 설정 =====================
HOST = "127.0.0.1"
PORT = 8000

# 화면 자원 경로는 paths.py가 해석한다(개발=프로젝트 루트, exe=번들 폴더).
# 주기 상수(TELEMETRY_HZ / PLC_POLL_INTERVAL_S / PLC_WRITE_INTERVAL_S)는 loops.py에 있다.

# commands의 "exit" 명령이 창 종료 함수를 호출하도록 주입.
commands.set_shutdown_handler(window.request_shutdown)


# ===================== FastAPI =====================
@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    # startup: 파일 로거 구성(config의 settings 기준) + PLC 통신 설정 반영(포트가 있으면 연결 유지 루프 시작)
    #          + 백그라운드 주기 태스크 시작
    logger.configure(state.settings)
    # 버전은 진단이 아니라 정보 — 콘솔과 파일 로그 맨 앞에 남긴다(로그만 받아도 버전을 알 수 있게).
    _ver = f"{version.APP_NAME} v{version.APP_VERSION} ({version.BUILD_DATE})"
    print(f"[info] {_ver}")
    logger.write("info", _ver)
    plc.configure(state.plc)
    plc.load_addresses(state.channels, state.plc_system)   # config 주도 주소맵 로드
    # 채널 배정 검사: 문제가 있어도 중단하지 않는다(진단 우선).
    # ★ 여기서 push_log를 부르면 안 된다 — 기동 시점엔 접속한 클라이언트가 0개라 사라진다.
    #   state.startup_notices에 쌓아 두면 접속 시 connection.py가 전달한다.
    state.startup_notices.clear()
    # 쓰기 권한 확인: Program Files 등에 설치하면 저장이 조용히 실패해
    # "설정을 바꿔도 재시작하면 초기값" 증상이 난다(exe 경로 문제와 증상이 같다).
    ok_w, why = check_writable()
    if not ok_w:
        print(f"[warn] 데이터 폴더 쓰기 불가: {DATA_ROOT} ({why})")
        state.startup_notices.append({
            "msg": f"데이터 폴더에 쓸 수 없습니다: {DATA_ROOT} — "
                   f"설정·레시피가 저장되지 않습니다. 쓰기 가능한 경로로 옮겨서 실행하세요.",
            "level": "warn"})
    for n in validate_channel_map(state.channels, state.plc_hw):
        lv, msg = n.get("level", "warn"), n["msg"]
        print(f"[{lv}] 채널 배정 — {msg}")
        state.startup_notices.append({"msg": f"채널 배정 확인 필요 — {msg}", "level": lv})
    # 연결 대상이 설정돼 있지 않으면 기동 시점에 알린다.
    # exe를 처음 켠 사용자가 무엇을 해야 하는지 바로 알 수 있게 한다.
    if not plc.plc._conn_enabled():
        msg = plc.plc.diagnose_connection()
        print(f"[warn] PLC 연결 안 함 — {msg}")
        state.startup_notices.append({"msg": f"PLC {msg}", "level": "warn"})
    await plc.plc.start()   # port 비어있으면 no-op(설정 전 무해)
    tasks = loops.start_all()

    try:
        yield
    finally:
        # shutdown: 태스크 정리 + PLC 연결 종료
        await loops.stop_all(tasks)
        with contextlib.suppress(Exception):
            await plc.plc.stop()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def root():
    return FileResponse(INDEX_PATH)


@app.get("/health")
async def health():
    return JSONResponse({"ok": True})


@app.get("/plc_catalog")
async def plc_catalog_route():
    """래더가 제공하는 채널 목록(고정값). 화면의 '하드웨어 배정' 표가 이름+레지스터를
    함께 보여주기 위해 쓴다. 값이 변하지 않으므로 state 스냅샷에 매번 싣지 않고 여기서 준다."""
    return JSONResponse({
        "valve": plc_catalog.VALVE_COILS,
        "dac": plc_catalog.DAC_CHANNELS,
        "adc": plc_catalog.ADC_CHANNELS,
        "dac_modules": state.plc_hw.get("dac_modules", 1),
    })


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
# 폴더가 없으면(빌드 시 frontend 누락 등) import 단계에서 죽지 않도록 존재 검사 후 마운트.
_css = os.path.join(FRONTEND_DIR, "css")
_js = os.path.join(FRONTEND_DIR, "js")
if os.path.isdir(_css):
    app.mount("/css", StaticFiles(directory=_css), name="css")
else:
    print(f"[error] 정적 폴더 없음: {_css} — 빌드 시 frontend가 누락됐을 수 있습니다")
if os.path.isdir(_js):
    app.mount("/js", StaticFiles(directory=_js), name="js")
else:
    print(f"[error] 정적 폴더 없음: {_js} — 빌드 시 frontend가 누락됐을 수 있습니다")


# ===================== 실행 (서버 스레드 + 창) =====================
def main():
    window.run(app, HOST, PORT)


if __name__ == "__main__":
    main()
