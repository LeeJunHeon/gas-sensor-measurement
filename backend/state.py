"""
state.py — 서버 상태(channels / system / recipe)의 단일 주인 + config.json 로드/저장.

서버가 상태의 주인이다. 시뮬레이션 telemetry 생성(sim_tick)은 simulation.py로 분리했다.
"""

import json

from storage import atomic_write_json, safe_read_json, CONFIG_PATH

# ===================== 기본값 =====================
DEFAULT_CHANNELS = [
    {"id": "VA1", "grp": "air", "route": "pure", "en": True,  "max": 2000, "sv": 0},
    {"id": "VA2", "grp": "air", "route": "pure", "en": False, "max": 2000, "sv": 0},
    {"id": "VA3", "grp": "air", "route": "mix",  "en": True,  "max": 2000, "sv": 0},
    {"id": "VA4", "grp": "air", "route": "mix",  "en": False, "max": 2000, "sv": 0},
    {"id": "VA5", "grp": "gas", "route": "mix",  "en": True,  "max": 2000, "sv": 0},
    {"id": "VA6", "grp": "gas", "route": "mix",  "en": True,  "max": 200,  "sv": 0},
    {"id": "VA7", "grp": "gas", "route": "mix",  "en": False, "max": 200,  "sv": 0},
    {"id": "VA8", "grp": "gas", "route": "mix",  "en": False, "max": 100,  "sv": 0},
]

DEFAULT_PARAMS = {
    "vStart": 0, "vEnd": 0, "vStep": 0,
    "grafInterval": 1,
    "smuMode": "Source V, Measure I",
    "smuSource": 0, "smuCompliance": 1.0,
    "chFrom": 1, "chTo": 1,
}


def default_recipe() -> dict:
    return {
        "name": "",
        "useHumidity": True,
        "loopCount": 1,
        "procs": [],
        "params": dict(DEFAULT_PARAMS),
    }


def to_num(v, d=0):
    """안전 숫자 변환: 정수면 int, 아니면 float, 실패하면 기본값 d."""
    try:
        f = float(v)
        return int(f) if f == int(f) else f
    except (TypeError, ValueError):
        return d


def normalize_recipe(r: dict) -> dict:
    """클라이언트/파일에서 온 레시피를 안전한 구조로 정규화(저장·로드 공용).
    손상되거나 타입이 틀린 데이터가 들어와도 안전한 형태로 만든다."""
    if not isinstance(r, dict):
        r = {}
    procs = []
    if isinstance(r.get("procs"), list):
        for p in r["procs"]:
            if not isinstance(p, dict):
                continue
            g_in = p.get("g")
            g = [to_num(x) for x in g_in[:4]] if isinstance(g_in, list) else []
            while len(g) < 4:
                g.append(0)
            procs.append({
                "flow": to_num(p.get("flow")),
                "rh": to_num(p.get("rh")),
                "g": g,
                "prep": to_num(p.get("prep")),
                "meas": to_num(p.get("meas")),
                "rep": bool(p.get("rep")),
            })
    params = {**DEFAULT_PARAMS, **(r.get("params") if isinstance(r.get("params"), dict) else {})}
    return {
        "name": str(r.get("name") or ""),
        "useHumidity": bool(r.get("useHumidity", True)),
        "loopCount": int(to_num(r.get("loopCount"), 1)) or 1,
        "procs": procs,
        "params": params,
    }


# ===================== 상태 (서버가 주인) =====================
class State:
    def __init__(self):
        self.channels = [dict(c) for c in DEFAULT_CHANNELS]
        self.params = dict(DEFAULT_PARAMS)
        self.system = {
            "running": False,
            "routeOut": "sensor",
            "loop": {"current": 0, "total": 1},
            "elapsed": 0,
            "rh": None,          # 측정 하드웨어 없음 → 화면 "—"
            "smu": None,         # 측정 하드웨어 없음 → 화면 "—"
            "connected": True,   # 서버↔하드웨어 (1단계는 시뮬, 항상 연결됨으로 표시)
            "safeStop": False,
        }
        self.recipe = default_recipe()
        self._elapsed_f = 0.0    # 내부 누적 경과시간(float)

    # ---- config 로드/저장 ----
    def load_config(self):
        data = safe_read_json(CONFIG_PATH)
        if not data:
            print("[info] config.json 없음 — 기본값으로 생성")
            self.save_config()
            return
        chans = data.get("channels")
        if isinstance(chans, list) and chans:
            normalized = []
            for i, c in enumerate(chans):
                base = dict(DEFAULT_CHANNELS[i]) if i < len(DEFAULT_CHANNELS) else {}
                base.update({
                    "id": c.get("id", f"VA{i + 1}"),
                    "grp": c.get("grp", base.get("grp", "air")),
                    "route": c.get("route", base.get("route", "pure")),
                    "en": bool(c.get("en", base.get("en", False))),
                    "max": c.get("max", base.get("max", 2000)),
                    "sv": c.get("sv", base.get("sv", 0)),
                })
                normalized.append(base)
            self.channels = normalized
        if isinstance(data.get("params"), dict):
            self.params = {**DEFAULT_PARAMS, **data["params"]}
            self.recipe["params"] = dict(self.params)
        # 사용 채널은 시작 시 밸브 열림으로 둔다(데모 일관성).
        for c in self.channels:
            c.setdefault("valveIn", bool(c["en"]))
            c.setdefault("pv", 0)

    def save_config(self):
        payload = {
            "channels": [
                {"id": c["id"], "grp": c["grp"], "route": c["route"],
                 "en": bool(c["en"]), "max": c["max"], "sv": c["sv"]}
                for c in self.channels
            ],
            "params": self.params,
        }
        try:
            atomic_write_json(CONFIG_PATH, payload)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] config 저장 실패: {e}")

    # ---- 외부로 내보낼 상태 스냅샷 ----
    # 레시피는 "권위 있는 변경"(연결 직후/New/Open/Save) 때만 포함한다.
    # 밸브·4-way·RUN 등 일상 push에는 recipe를 빼서, 편집 중인 레시피 초안을 덮어쓰지 않는다.
    def snapshot(self, include_recipe: bool = False) -> dict:
        snap = {
            "type": "state",
            "channels": [dict(c) for c in self.channels],
            "system": dict(self.system),
        }
        if include_recipe:
            snap["recipe"] = json.loads(json.dumps(self.recipe))
        return snap


# 서버 전역에서 공유하는 단일 상태 인스턴스
state = State()
state.load_config()
