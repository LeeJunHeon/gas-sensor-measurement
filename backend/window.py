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
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "VANAM_GasSensor_SingleInstance")
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
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


def run(app, host: str, port: int):
    """서버를 별도 스레드로 띄우고 창을 연다. 창을 닫으면 반환된다.

    app  : FastAPI 앱 객체(server.py가 넘긴다)
    host : 바인드 주소
    port : 희망 포트. 이미 쓰이고 있으면 port+1 … 순으로 최대 10개까지 대체한다.
    """
    import uvicorn

    # 이중 실행 차단. 인스턴스가 2개면 포트 회피로 둘 다 정상 기동해 창이 2개 뜨고,
    # TCP 모드에서는 둘 다 PLC에 붙어 서로 밸브 명령을 덮어쓴다.
    if not _acquire_single_instance():
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
        print(f"[info] 포트 {port} 사용 중 → {free} 사용")
    port = free

    def run_server():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    try:
        import webview  # pywebview
    except Exception as e:  # noqa: BLE001
        print(f"[info] pywebview를 불러올 수 없습니다 ({e}).")
        print(f"[info] 브라우저에서 http://{host}:{port} 를 열어 사용하세요. (Ctrl+C 종료)")
        _msgbox("Gas Sensor Measurement System",
                "화면을 표시할 수 없습니다.\n"
                "Microsoft Edge WebView2 런타임이 설치되어 있지 않을 수 있습니다.\n"
                f"설치 후 다시 실행하거나, 브라우저에서 http://{host}:{port} 로 접속하세요.")
        with contextlib.suppress(KeyboardInterrupt):
            server_thread.join()
        return

    global WINDOW
    WINDOW = webview.create_window(
        "Gas Sensor Measurement System",
        f"http://{host}:{port}",
        width=1480, height=1020,   # 최대화 해제 시 사용할 기본 크기
        maximized=True,            # 실행 시 최대화(타이틀바·작업표시줄 유지)
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
