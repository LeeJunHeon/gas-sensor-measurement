"""
window.py — pywebview 창 생성·종료 처리.

server.py의 진입점에서만 쓴다. ★ server.py를 import하지 않는다(순환 import 방지) —
FastAPI 앱과 호스트/포트는 인자로 받는다.

exe 납품 대응:
  - WebView2 런타임이 없으면 콘솔이 없어 사용자에게 아무것도 안 보인다 → 메시지 박스로 안내
  - 8000 포트가 이미 쓰이면 uvicorn이 스레드에서 죽고 빈 창이 된다 → 대체 포트 자동 탐색
"""

import os
import sys
import time
import socket
import threading
import contextlib
import traceback

import logger
from paths import DATA_ROOT, check_writable

WINDOW = None         # main()에서 생성한 pywebview 창 객체를 보관
_allow_close = False  # 창 닫기 허용 플래그(종료 확인 통과 후 True). X 클릭은 모달로 되묻는다.


def _msgbox(title: str, msg: str):
    """Windows 메시지 박스(추가 의존성 없음). 다른 OS면 print로 폴백."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, title, 0x10)
    except Exception:  # noqa: BLE001
        print(f"[{title}] {msg}")


_MUTEX_HANDLE = None   # 핸들이 GC로 닫히면 뮤텍스가 풀린다 — 프로세스 수명 동안 전역에 보관


def _acquire_single_instance() -> bool:
    """이중 실행 방지. 이미 떠 있으면 False. 비Windows(개발 환경)는 항상 True.
    ★ 방지 장치 자체가 이유가 되어 실행을 막으면 안 되므로 예외 시 True."""
    if sys.platform != "win32":
        return True
    global _MUTEX_HANDLE
    # 재진입 안전: 창 경로로 실행하면 run()과 lifespan이 둘 다 부른다.
    # 이미 내가 잡은 뮤텍스면 두 번째 호출이 자기 자신에 걸려 오탐 종료하는 것을 막는다.
    if _MUTEX_HANDLE is not None:
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        h = kernel32.CreateMutexW(None, False, "VANAM_GasSensor_SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            # 실패한 핸들을 남기면 재진입 가드(_MUTEX_HANDLE is not None)가 재시도에서
            # "내가 잡은 것"으로 오판한다 → 반드시 닫고 None 으로 되돌린다.
            with contextlib.suppress(Exception):
                kernel32.CloseHandle(h)
            _MUTEX_HANDLE = None
            return False
        _MUTEX_HANDLE = h
        return True
    except Exception:  # noqa: BLE001
        return True


def find_free_port(host: str, start: int, tries: int = 10):
    """start부터 tries개까지 비어 있는 포트를 찾는다. 전부 막혔으면 None."""
    for p in range(start, start + tries):
        with socket.socket() as s:
            try:
                s.bind((host, p))
                return p
            except OSError:
                continue
    return None


_SERVER_ERROR = ""   # 서버 스레드가 죽은 사유. console=False 인 exe에서는 이 값이 유일한 단서다.


def _wait_server_ready(host: str, port: int, timeout_s: float = 20.0) -> bool:
    """서버 소켓이 바인딩될 때까지 기다린다. 시간 안에 붙으면 True.

    uvicorn은 lifespan(로거·설정·진단)을 끝낸 뒤에야 소켓을 연다. 창이 그보다 먼저
    URL을 요청하면 WebView2가 ERR_CONNECTION_REFUSED 화면을 띄우고 재시도하지 않는다(2026-08-18).
    연결 성공 = 바인딩 완료 이므로 접속 가능 여부만 본다.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            with socket.create_connection((host, port), 0.3):
                return True
        time.sleep(0.1)
    return False


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


class _JsBridge:
    """pywebview js_api — 화면 JS가 서버를 거치지 않고 프로세스를 종료시키는 직통 경로.

    ws 가 끊기면 'exit' 명령이 오프라인 차단에 걸려 종료가 불가능해진다(app.js).
    이 브리지는 WebView2 ↔ 파이썬 직결이라 서버 스레드가 죽어도 살아 있다.
    ★ 가스 안전: 여기서는 차단 프레임을 쓰지 못한다(서버가 없다). 크래시·강제종료의
      최후 방어선은 래더다 — 하트비트 10초 두절 → 트립 → NC 밸브 전체 닫힘
      (commands.py 의 exit 주석과 동일한 근거).
    """

    def force_close(self):
        request_shutdown()
        return True


def _on_closing():
    """창 우상단 X → 앱 내부 종료확인 모달로 되묻는다(확인 전엔 닫기 취소).
    ★ WebView2 데드락 방지: evaluate_js를 closing 핸들러에서 '동기' 호출하면 GUI 스레드가
      재진입 데드락에 빠져 멈춘다('응답 없음'). 반드시 별도 스레드에서 호출하고 즉시 반환해야 한다."""
    if _allow_close:
        return True       # 종료확인 통과(또는 destroy 진행 중) → 닫기 허용

    # 모달을 띄웠다는 '확증'이 있을 때만 창을 유지한다. 오류 페이지·크래시 페이지처럼
    # 앱 JS가 없는 화면에서는 X가 영원히 무시돼 작업 관리자로만 끌 수 있었다(2026-08-18 실발생).
    # 판매 제품에서 닫히지 않는 창은 허용하지 않는다 → 확증이 없으면 닫는다.
    asked = []

    def _ask():
        try:
            ok = WINDOW.evaluate_js(
                "(function(){ if(window.requestExitConfirm){window.requestExitConfirm();"
                " return true;} return false; })()")
            if ok:
                asked.append(True)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 종료확인 모달 호출 실패: {e}")

    def _confirm_or_close():
        # 앱 JS 없음 / evaluate_js 예외 / WebView 무응답(행) — 어느 쪽이든 5초 뒤 강제 종료.
        # ★ 가스 안전: 이 경로는 차단 프레임을 못 보낸다. 하트비트 10초 두절 → 래더 트립 →
        #   NC 밸브 전체 닫힘이 최후 방어선이다.
        t = threading.Thread(target=_ask, daemon=True)
        t.start()
        t.join(5.0)
        if not asked:
            print("[warn] 앱 화면이 응답하지 않습니다 — 강제 종료합니다"
                  " (밸브는 PLC 안전로직이 닫습니다)")
            logger.write("warn", "창 닫기: 앱 화면 무응답 → 강제 종료"
                                 " (밸브는 PLC 안전로직이 닫습니다)")
            request_shutdown()

    # ★ WebView2 데드락 방지: 대기·evaluate_js는 전부 별도 스레드. _on_closing은 즉시 반환한다.
    threading.Thread(target=_confirm_or_close, daemon=True).start()
    return False          # 확인 전에는 닫기 취소(창 유지). 위 스레드가 확증/폴백을 처리.


def run(app, host: str, port: int):
    """서버를 별도 스레드로 띄우고 창을 연다. 창을 닫으면 반환된다.

    app  : FastAPI 앱 객체(server.py가 넘긴다)
    host : 바인드 주소
    port : 희망 포트. 이미 쓰이고 있으면 port+1 … 순으로 최대 10개까지 대체한다.
    """
    import uvicorn

    # 이중 실행 차단. 인스턴스가 2개면 포트 회피로 둘 다 정상 기동해 창이 2개 뜨고,
    # TCP 모드에서는 둘 다 PLC에 붙어 서로 밸브 명령을 덮어쓴다.
    # 종료 직후 재실행은 직전 인스턴스의 정리(~1초)가 끝나기 전이라 뮤텍스가 아직 잡혀 있다.
    # 사용자에겐 "닫았는데 이미 실행 중"으로 보이므로 최대 3초 기다렸다가 판정한다.
    # (진짜 중복 실행은 3초 뒤에도 잡혀 있어 그대로 팝업으로 막힌다)
    _got = _acquire_single_instance()
    _deadline = time.monotonic() + 3.0
    while not _got and time.monotonic() < _deadline:
        time.sleep(0.25)
        _got = _acquire_single_instance()
    if not _got:
        _msgbox("Gas Sensor Measurement System",
                "프로그램이 이미 실행 중입니다.\n작업 표시줄에서 기존 창을 확인하세요.")
        return

    # 쓰기 불가면 설정·레시피가 저장되지 않는다. exe는 콘솔이 없고 UI 로그를 안 볼 수도 있어
    # 한 번은 창으로 알린다. ★ 중단하지 않는다 — 읽기 전용이어도 운전은 가능해야 한다.
    ok_w, _why = check_writable()
    if not ok_w:
        _msgbox("Gas Sensor Measurement System",
                "데이터 폴더에 쓸 수 없습니다.\n"
                f"{DATA_ROOT}\n\n"
                "설정과 레시피가 저장되지 않습니다.\n"
                "프로그램을 C:\\VANAM\\GasSensor\\ 같은 쓰기 가능한 경로로\n"
                "옮겨서 실행하세요.")

    free = find_free_port(host, port)
    if free is None:
        _msgbox("Gas Sensor Measurement System",
                f"사용 가능한 포트를 찾지 못했습니다 ({port}~{port + 9}).\n"
                "다른 프로그램을 종료한 뒤 다시 실행하세요.")
        return
    if free != port:
        # 콘솔 없는 exe에서는 print가 증발한다 → 파일 로그에도 남긴다.
        # (아직 logger.configure 전이라 early 버퍼로. lifespan에서 flush된다)
        print(f"[info] 포트 {port} 사용 중 → {free} 사용")
        logger.early("info", f"포트 {port} 사용 중 → {free} 사용")
    port = free

    def run_server():
        # 스레드에서 죽으면 console=False 인 exe에서는 아무 흔적도 남지 않는다 → 사유를 남긴다.
        global _SERVER_ERROR
        try:
            # log_config=None: uvicorn 자체 로깅 dictConfig 를 타지 않는다.
            # 창 전용 exe 에서 그 구성이 sys.stdout.isatty() 로 죽었다(server.py 가드와 짝).
            uvicorn.run(app, host=host, port=port, log_level="warning",
                        log_config=None)
        except Exception as e:  # noqa: BLE001
            _SERVER_ERROR = f"{type(e).__name__}: {e}"
            detail = traceback.format_exc()
            print(f"[error] 내부 서버가 중단되었습니다: {detail}")
            # configure 전에 죽었을 수도 있어 early(버퍼)와 write(파일) 둘 다 남긴다.
            logger.early("err", f"내부 서버 중단: {_SERVER_ERROR}")
            logger.write("err", f"내부 서버 중단: {detail}")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    try:
        import webview  # pywebview
    except Exception as e:  # noqa: BLE001
        print(f"[info] pywebview를 불러올 수 없습니다 ({e}).")
        print(f"[info] 브라우저에서 http://{host}:{port} 를 열어 사용하세요. (Ctrl+C 종료)")
        logger.early("warn", f"pywebview를 불러올 수 없습니다 ({e}) — "
                             f"브라우저에서 http://{host}:{port} 로 접속하세요")
        _msgbox("Gas Sensor Measurement System",
                "화면을 표시할 수 없습니다.\n"
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 http://{host}:{port} 로 접속하세요.")
        with contextlib.suppress(KeyboardInterrupt):
            server_thread.join()
        return

    # 창을 만들기 전에 서버가 실제로 뜰 때까지 기다린다(ERR_CONNECTION_REFUSED 화면 방지).
    print(f"[info] 내부 서버 준비를 기다리는 중... http://{host}:{port}")
    logger.early("info", f"내부 서버 준비 대기 시작 (http://{host}:{port})")
    if not _wait_server_ready(host, port):
        if _SERVER_ERROR:
            reason = "내부 서버가 시작되지 못했습니다.\n\n" + _SERVER_ERROR
        else:
            reason = "내부 서버가 시간 안에 시작되지 않았습니다."
        print(f"[error] {reason}")
        flat = reason.replace("\n", " ")
        logger.early("err", flat)
        logger.write("err", flat)
        _msgbox("Gas Sensor Measurement System",
                reason + "\n\n로그 폴더를 확인하세요.\n" + logger.current_dir())
        return

    global WINDOW
    WINDOW = webview.create_window(
        "Gas Sensor Measurement System",
        f"http://{host}:{port}",
        width=1480, height=1020,   # 최대화 해제 시 사용할 기본 크기
        maximized=True,            # 실행 시 최대화(타이틀바·작업표시줄 유지)
        js_api=_JsBridge(),        # 서버가 죽어도 화면에서 종료할 수 있는 직통 경로
    )
    # 창 X(닫기) 클릭 → _on_closing이 종료확인 모달로 되묻는다(False 반환 시 닫기 취소).
    try:
        WINDOW.events.closing += _on_closing
    except Exception as e:  # noqa: BLE001
        print(f"[warn] closing 이벤트 연결 실패: {e}")
    try:
        webview.start()   # 창을 닫으면 여기서 반환 → 데몬 스레드와 함께 종료
    except Exception as e:  # noqa: BLE001
        # WebView2 런타임 부재는 여기서 터진다(import는 되지만 창 생성/시작에서 실패).
        print(f"[error] 창을 띄우지 못했습니다: {e}")
        _msgbox("Gas Sensor Measurement System",
                "화면을 표시할 수 없습니다.\n"
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 http://{host}:{port} 로 접속하세요.")
