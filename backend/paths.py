"""
paths.py — 실행 환경별 경로 해석 (개발 / PyInstaller exe).

PyInstaller로 묶으면 폴더가 둘로 갈라진다.
  BUNDLE_ROOT : 번들 자원(읽기 전용). exe 안에 묶여 임시 폴더에 풀린다.
                → frontend/ (HTML·CSS·JS)
  DATA_ROOT   : 사용자 데이터(쓰기). exe가 놓인 폴더에 영구 보존된다.
                → config.json, admin.json, recipes/, logs/

개발 환경에서는 둘 다 프로젝트 루트로 같은 값이 된다(동작 동일).
★ 새 파일 경로를 추가할 때는 반드시 둘 중 어디에 속하는지 먼저 판단할 것.
"""
import os
import sys

if getattr(sys, "frozen", False):          # PyInstaller 실행 중
    # onefile: _MEIPASS = 임시 압축해제 폴더
    # onedir : _MEIPASS = <exe폴더>/_internal
    BUNDLE_ROOT = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    DATA_ROOT   = os.path.dirname(os.path.abspath(sys.executable))
else:                                       # 개발 환경 — 둘이 같다
    _BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_ROOT  = os.path.dirname(_BACKEND_DIR)
    DATA_ROOT    = BUNDLE_ROOT

# --- 쓰기 대상(DATA_ROOT) ---
CONFIG_PATH  = os.path.join(DATA_ROOT, "config.json")
ADMIN_PATH   = os.path.join(DATA_ROOT, "admin.json")   # 관리자 인증(납품 시 동봉). 아직 미사용
RECIPES_DIR  = os.path.join(DATA_ROOT, "recipes")

# --- 읽기 전용 자원(BUNDLE_ROOT) ---
FRONTEND_DIR = os.path.join(BUNDLE_ROOT, "frontend")
INDEX_PATH   = os.path.join(FRONTEND_DIR, "index.html")


def data_path(*parts: str) -> str:
    """DATA_ROOT 기준 경로. 로그 폴더 등 상대경로 설정을 절대경로로 바꿀 때 쓴다."""
    return os.path.join(DATA_ROOT, *parts)


def ensure_data_dirs() -> bool:
    """쓰기 폴더를 만든다. 권한이 없으면(예: Program Files 설치) False를 돌려준다.
    ★ import 시점에 예외로 죽으면 프로그램이 아예 안 뜨므로 절대 raise하지 않는다."""
    try:
        os.makedirs(RECIPES_DIR, exist_ok=True)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 데이터 폴더 생성 실패: {RECIPES_DIR} ({e})")
        return False
