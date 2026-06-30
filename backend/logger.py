"""
logger.py — 파일 로그(날짜별 회전). push_log가 화면 로그와 함께 파일에도 남긴다.
설정(state.settings)으로 on/off·폴더·레벨·보관일수를 제어한다.
"""

import os
import time
import glob
import datetime

from storage import PROJECT_ROOT

_LEVELS = {"info": 0, "ok": 0, "warn": 1, "err": 2}   # ok/info 동급, 그 이상만 필터

_cfg = {"enabled": True, "dir": "logs", "level": "info", "keep": 30}
_abs_dir = None


def _resolve_dir(d: str) -> str:
    if not d:
        d = "logs"
    return d if os.path.isabs(d) else os.path.join(PROJECT_ROOT, d)


def configure(settings: dict):
    """state.settings로 로거 재설정. 폴더 생성 + 오래된 파일 정리."""
    global _abs_dir
    _cfg["enabled"] = bool(settings.get("logEnabled", True))
    _cfg["dir"] = settings.get("logDir", "logs") or "logs"
    _cfg["level"] = settings.get("logLevel", "info") or "info"
    try:
        _cfg["keep"] = max(0, int(settings.get("logKeepDays", 30)))
    except (TypeError, ValueError):
        _cfg["keep"] = 30
    _abs_dir = _resolve_dir(_cfg["dir"])
    if _cfg["enabled"]:
        try:
            os.makedirs(_abs_dir, exist_ok=True)
            _cleanup_old()
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 로그 폴더 준비 실패: {e}")


def _cleanup_old():
    if _cfg["keep"] <= 0 or not _abs_dir:
        return
    cutoff = time.time() - _cfg["keep"] * 86400
    for f in glob.glob(os.path.join(_abs_dir, "measurement-*.log")):
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except Exception:  # noqa: BLE001
            pass


def write(level: str, message: str):
    """레벨 필터 통과 시 오늘자 로그 파일에 한 줄 기록. 실패해도 앱에 영향 없음."""
    if not _cfg["enabled"] or not _abs_dir:
        return
    if _LEVELS.get(level, 0) < _LEVELS.get(_cfg["level"], 0):
        return
    try:
        ts = datetime.datetime.now()
        path = os.path.join(_abs_dir, f"measurement-{ts:%Y%m%d}.log")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(f"{ts:%Y-%m-%d %H:%M:%S} [{level.upper()}] {message}\n")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 로그 기록 실패: {e}")


def current_dir() -> str:
    return _abs_dir or _resolve_dir(_cfg["dir"])
