"""
state.py — 서버 상태(channels / system / recipe)의 단일 주인 + config.json 로드/저장.

서버가 상태의 주인이다. 시뮬레이션 telemetry 생성(sim_tick)은 simulation.py로 분리했다.
"""

import json

from storage import atomic_write_json, safe_read_json, CONFIG_PATH

# ===================== 기본값 =====================
# 채널별 PLC 주소(레벨1: 코드 수정 없이 config로 추가·변경). 매핑 있으면 dict, 없으면 None.
# HMI VA1·VA3·VA5·VA6 ↔ PLC VA1·VA3·VA5·VA6 (1:1). VA2·VA4·VA7·VA8은 PLC 대응 없음.
DEFAULT_CHANNEL_PLC = {
    "VA1": {"cmd_coil": 160, "sv_reg": 100, "pv_reg": 200},
    "VA2": None,
    "VA3": {"cmd_coil": 161, "sv_reg": 101, "pv_reg": 201},
    "VA4": None,
    "VA5": {"cmd_coil": 162, "sv_reg": 102, "pv_reg": 202},
    "VA6": {"cmd_coil": 163, "sv_reg": 103, "pv_reg": 203},
    "VA7": None,
    "VA8": None,
}

# 채널 무관 시스템 공통 주소(하트비트/안전리셋/4-way/상태·알람).
DEFAULT_PLC_SYSTEM = {
    "heartbeat": 176,     # M00110 (쓰기) 통신 생존 토글
    "safety_reset": 178,  # M00112 (쓰기, 펄스) 안전리셋
    "v4w_cmd": 164,       # M00104 (쓰기) 4-way 지령
    "air_ok": 320,        # M00200 (읽기) 공기압 정상
    "safety_stop": 321,   # M00201 (읽기) 안전정지 상태
    "alm_air": 336,       # M00210 (읽기) 공기 알람
    "alm_mfc": 337,       # M00211 (읽기) MFC 알람
}


def _default_channel_plc(cid: str):
    """채널 id의 기본 PLC 주소(사본). 매핑 없으면 None."""
    m = DEFAULT_CHANNEL_PLC.get(cid)
    return dict(m) if isinstance(m, dict) else None


def _norm_channel_plc(v):
    """채널 plc 값 정규화: dict면 사본, 그 외(None 등)면 None."""
    return dict(v) if isinstance(v, dict) else None


def _copy_channel(c: dict) -> dict:
    """채널 dict 사본(중첩 plc까지 분리 — 기본값과 상태가 참조 공유하지 않도록)."""
    out = dict(c)
    out["plc"] = _norm_channel_plc(c.get("plc"))
    return out


DEFAULT_CHANNELS = [
    {"id": "VA1", "grp": "air", "route": "pure", "en": True,  "max": 2000, "sv": 0, "plc": _default_channel_plc("VA1")},
    {"id": "VA2", "grp": "air", "route": "pure", "en": False, "max": 2000, "sv": 0, "plc": _default_channel_plc("VA2")},
    {"id": "VA3", "grp": "air", "route": "mix",  "en": True,  "max": 2000, "sv": 0, "plc": _default_channel_plc("VA3")},
    {"id": "VA4", "grp": "air", "route": "mix",  "en": False, "max": 2000, "sv": 0, "plc": _default_channel_plc("VA4")},
    {"id": "VA5", "grp": "gas", "route": "mix",  "en": True,  "max": 2000, "sv": 0, "plc": _default_channel_plc("VA5")},
    {"id": "VA6", "grp": "gas", "route": "mix",  "en": True,  "max": 200,  "sv": 0, "plc": _default_channel_plc("VA6")},
    {"id": "VA7", "grp": "gas", "route": "mix",  "en": False, "max": 200,  "sv": 0, "plc": _default_channel_plc("VA7")},
    {"id": "VA8", "grp": "gas", "route": "mix",  "en": False, "max": 100,  "sv": 0, "plc": _default_channel_plc("VA8")},
]

DEFAULT_PARAMS = {
    "vStart": 0, "vEnd": 0, "vStep": 0,
    "grafInterval": 1,
    "smuMode": "Source V, Measure I",
    "smuSource": 0, "smuCompliance": 1.0,
    "chFrom": 1, "chTo": 1,
}

DEFAULT_SETTINGS = {
    "logEnabled": True,           # 파일 로그 사용
    "logDir": "logs",             # 저장 폴더(프로젝트 루트 기준 상대경로 또는 절대경로)
    "logLevel": "info",           # info | warn | err (이 레벨 이상만 파일 기록)
    "logKeepDays": 30,            # 보관 일수(이보다 오래된 로그 파일 삭제)
}

# PLC 통신(LS XGB 내장 Cnet Modbus RTU). port 비면 연결 안 함(설정 전 무해).
DEFAULT_PLC = {
    "port": "",                   # 예: COM3(Windows) / /dev/ttyUSB0(Linux). 필수.
    "baudrate": 115200,
    "bytesize": 8,
    "stopbits": 1,
    "parity": "N",               # N | E | O
    "unit_id": 1,                # 국번(1~247). 0 금지.
    "timeout_s": 1.5,
    "inter_cmd_gap_s": 0.1,
    "heartbeat_s": 1.0,          # PLC COMM_TMR(3초) 미만이어야 통신두절 트립 방지
    "reconnect_delay_s": 1.0,
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
    bottle = r.get("bottle")
    bottle = [to_num(x) for x in bottle[:4]] if isinstance(bottle, list) else []
    while len(bottle) < 4:
        bottle.append(0)
    return {
        "name": str(r.get("name") or ""),
        "useHumidity": bool(r.get("useHumidity", True)),
        "loopCount": int(to_num(r.get("loopCount"), 1)) or 1,
        "bottle": bottle,
        "procs": procs,
        "params": params,
    }


# ===================== 상태 (서버가 주인) =====================
class State:
    def __init__(self):
        self.channels = [_copy_channel(c) for c in DEFAULT_CHANNELS]
        self.params = dict(DEFAULT_PARAMS)
        self.settings = dict(DEFAULT_SETTINGS)
        self.plc = dict(DEFAULT_PLC)
        self.plc_system = dict(DEFAULT_PLC_SYSTEM)
        # PLC 실측 라이브(읽기 경로): 폴링 태스크가 갱신, snapshot으로 프론트에 전송.
        self.plc_live = {"connected": False, "pv": {}, "status": {}}
        self.system = {
            "running": False,
            "routeOut": "sensor",
            "loop": {"current": 0, "total": 1},
            "elapsed": 0,
            "rh": None,          # 측정 하드웨어 없음 → 화면 "—"
            "smu": None,         # 측정 하드웨어 없음 → 화면 "—"
            "connected": True,   # 서버↔하드웨어 (1단계는 시뮬, 항상 연결됨으로 표시)
            "safeStop": False,
            "phase": "idle",      # idle | prep | meas
            "stepIndex": 0,       # 현재 단계(1-base, 0=대기)
            "stepTotal": 0,       # 전체 단계 수
            "stepRemain": 0,      # 현재 단계 남은 초
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
                base = _copy_channel(DEFAULT_CHANNELS[i]) if i < len(DEFAULT_CHANNELS) else {}
                cid = c.get("id", f"VA{i + 1}")
                # plc: 옛 config엔 없을 수 있음 → 있으면 그 값(null 포함), 없으면 id별 기본값.
                default_plc = base.get("plc", _default_channel_plc(cid))
                plc_val = _norm_channel_plc(c["plc"]) if "plc" in c else default_plc
                base.update({
                    "id": cid,
                    "grp": c.get("grp", base.get("grp", "air")),
                    "route": c.get("route", base.get("route", "pure")),
                    "en": bool(c.get("en", base.get("en", False))),
                    "max": c.get("max", base.get("max", 2000)),
                    "sv": c.get("sv", base.get("sv", 0)),
                    "plc": plc_val,
                })
                normalized.append(base)
            self.channels = normalized
        if isinstance(data.get("params"), dict):
            self.params = {**DEFAULT_PARAMS, **data["params"]}
            self.recipe["params"] = dict(self.params)
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        self.plc = {**DEFAULT_PLC, **(data.get("plc") or {})}
        self.plc_system = {**DEFAULT_PLC_SYSTEM, **(data.get("plc_system") or {})}
        # 사용 채널은 시작 시 밸브 열림으로 둔다(데모 일관성).
        for c in self.channels:
            c.setdefault("valveIn", False)   # 시작 시 모든 밸브 닫힘(흐름 표시도 꺼진 상태)
            c.setdefault("pv", 0)

    def save_config(self):
        payload = {
            "channels": [
                {"id": c["id"], "grp": c["grp"], "route": c["route"],
                 "en": bool(c["en"]), "max": c["max"], "sv": c["sv"],
                 "plc": _norm_channel_plc(c.get("plc"))}
                for c in self.channels
            ],
            "params": self.params,
            "settings": self.settings,
            "plc": self.plc,
            "plc_system": self.plc_system,
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
            "settings": dict(self.settings),
            "plc": dict(self.plc),
            "plc_system": dict(self.plc_system),
            "plc_live": {
                "connected": bool(self.plc_live.get("connected")),
                "pv": dict(self.plc_live.get("pv") or {}),
                "status": dict(self.plc_live.get("status") or {}),
            },
        }
        if include_recipe:
            snap["recipe"] = json.loads(json.dumps(self.recipe))
        return snap


# 채널 역할(엔진/계산용). 물탱크(가습기)가 달린 채널 = 젖은 공기.
HUMID_CHANNEL_IDS = {"VA2", "VA4"}   # 물탱크 장착 채널(습한 공기)


def channel_role(c: dict) -> str:
    """'gas' | 'wet_air' | 'dry_air' — 엔진이 SV를 배분할 때 쓰는 역할."""
    if c.get("grp") == "gas":
        return "gas"
    if c.get("id") in HUMID_CHANNEL_IDS:
        return "wet_air"
    return "dry_air"


# 서버 전역에서 공유하는 단일 상태 인스턴스
state = State()
state.load_config()
