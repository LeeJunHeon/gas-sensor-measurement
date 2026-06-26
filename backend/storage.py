"""
storage.py — 파일 I/O (레시피/설정).

- atomic_write_json: 임시 파일에 쓰고 rename → 원자적 저장.
- safe_read_json: 읽기 실패 시 예외로 죽지 않고 None.
- valid_recipe_name / list_recipes: 레시피 이름 검증 + 목록.

경로는 스크립트 위치 기준으로 프로젝트 루트를 계산한다(현재 작업 디렉터리에 비의존).
"""

import os
import json
from typing import Any

# backend/ 의 상위 = 프로젝트 루트. config.json·recipes/ 는 루트에 있다.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
RECIPES_DIR = os.path.join(PROJECT_ROOT, "recipes")

os.makedirs(RECIPES_DIR, exist_ok=True)


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
