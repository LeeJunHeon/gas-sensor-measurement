"""
storage.py — 파일 I/O (레시피/설정).

- atomic_write_json: 임시 파일에 쓰고 rename → 원자적 저장.
- safe_read_json: 읽기 실패 시 예외로 죽지 않고 None.
- valid_recipe_name / list_recipes: 레시피 이름 검증 + 목록.

경로 해석은 paths.py가 담당한다(개발/exe 환경 차이 흡수). 여기선 재노출만 한다 —
다른 모듈이 `from storage import CONFIG_PATH` 형태로 쓰고 있어 그대로 유지한다.
"""

import os
import json
from typing import Any

import logger
from paths import CONFIG_PATH, RECIPES_DIR, ensure_data_dirs  # noqa: F401 — 재노출

# 쓰기 폴더 준비. 권한이 없어도 여기서 죽지 않는다(파일 저장 시점에 개별 실패로 처리).
ensure_data_dirs()


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
        # import 단계(config 로드)에도 불릴 수 있어 early 버퍼로 보낸다(로거 설정 후 flush).
        logger.early("warn", f"JSON 읽기 실패: {path} ({e})")
        return None


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
