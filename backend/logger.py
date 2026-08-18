"""
logger.py — 파일 로그(날짜별 회전). push_log가 화면 로그와 함께 파일에도 남긴다.
설정(state.settings)으로 on/off·폴더·레벨·보관일수를 제어한다.
"""

import os
import time
import glob
import datetime

from paths import data_path

_LEVELS = {"info": 0, "ok": 0, "warn": 1, "err": 2}   # ok/info 동급, 그 이상만 필터

_cfg = {"enabled": True, "dir": "logs", "level": "info", "keep": 30}
_abs_dir = None

# 로거 설정 전(import 단계)에 발생한 진단. 파일에 쓸 수 없으니 모아뒀다가 configure에서 flush한다.
# ★ state를 import하지 않는다(순환) — 화면 알림은 서버가 drain_early()로 꺼내 간다.
_early = []            # [(level, message)]
_early_flushed = False


def early(level: str, message: str):
    """로거 설정 전 단계의 진단. 콘솔에 찍지 않고 모아뒀다가 configure()에서 flush한다."""
    _early.append((level, message))


def _flush_early():
    """모아둔 초기 진단을 파일에 기록한다(한 번만)."""
    global _early_flushed
    if _early_flushed:
        return
    _early_flushed = True
    for lv, msg in _early:
        write(lv, msg)


def drain_early() -> list:
    """초기 진단을 [(level, message)]로 돌려주고 버퍼를 비운다(화면 알림용).
    파일 기록은 configure()에서 이미 끝났다."""
    out = list(_early)
    _early.clear()
    return out


def _resolve_dir(d: str) -> str:
    if not d:
        d = "logs"
    return d if os.path.isabs(d) else data_path(d)


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
            # ★ 로그 시스템 자체의 실패라 로그로 알릴 수 없다 → print 유지(무한 재귀 방지).
            print(f"[warn] 로그 폴더 준비 실패: {e}")
    _flush_early()   # 설정이 끝났으니 그동안 모아둔 초기 진단을 파일에 남긴다


def _cleanup_old():
    if _cfg["keep"] <= 0 or not _abs_dir:
        return
    cutoff = time.time() - _cfg["keep"] * 86400
    # 구 이름(measurement-*)도 함께 지운다 — 기존 설치본에서 올라온 파일이 보관일수를
    # 넘겨도 영원히 남는 것을 막는다(파일명은 v1.1.0 이후 GasSensor-*).
    olds = (glob.glob(os.path.join(_abs_dir, "GasSensor-*.log"))
            + glob.glob(os.path.join(_abs_dir, "measurement-*.log")))
    for f in olds:
        try:
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
        except Exception:  # noqa: BLE001
            pass


def write(level: str, message: str):
    """레벨 필터 통과 시 오늘자 로그 파일에 한 줄 기록. 실패해도 앱에 영향 없음."""
    global _abs_dir
    if not _cfg["enabled"]:
        return
    if not _abs_dir:
        # configure() 전에 죽는 기동 실패(lifespan 이전)도 사유가 파일에 남아야 한다 —
        # 그래야 "로그 폴더를 확인하세요" 안내가 항상 참이 된다. 기본값(logs/)으로 지연 초기화.
        try:
            d = _resolve_dir(_cfg["dir"])
            os.makedirs(d, exist_ok=True)
            _abs_dir = d
        except Exception:  # noqa: BLE001
            return
    if _LEVELS.get(level, 0) < _LEVELS.get(_cfg["level"], 0):
        return
    try:
        ts = datetime.datetime.now()
        path = os.path.join(_abs_dir, f"GasSensor-{ts:%Y%m%d}.log")
        with open(path, "a", encoding="utf-8") as fp:
            fp.write(f"{ts:%Y-%m-%d %H:%M:%S} [{level.upper()}] {message}\n")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 로그 기록 실패: {e}")


def current_dir() -> str:
    return _abs_dir or _resolve_dir(_cfg["dir"])
