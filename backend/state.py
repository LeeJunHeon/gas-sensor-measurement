"""
state.py — 서버 상태(channels / system / recipe)의 단일 주인 + config.json 로드/저장.

서버가 상태의 주인이다. telemetry 생성(실측 PV·경과시간·진행상태)은 loops.py가 담당한다.
"""

import json

import logger
import version
from storage import atomic_write_json, safe_read_json, CONFIG_PATH

# ===================== 기본값 =====================
# 채널별 PLC 주소(레벨1: 코드 수정 없이 config로 추가·변경). 매핑 있으면 dict, 없으면 None.
# 채널별 PLC 주소 + 아날로그 스케일.
# 주소: PLC 래더와 1:1 대응(변경 금지). 스케일: 장비마다 다르므로 System Setup에서 수정 가능.
#   fs_sccm  = MFC 하드웨어 풀스케일(sccm).
#              ★명판 확인 완료 — HORIBA S48-BR221, 1000 sccm, 신호 0~5V, 4대 동일.
#   sv_full  = 풀스케일에 해당하는 DAC 카운트. DV04A 0~10V/0~4000 기준,
#              MFC 설정신호가 0~5V면 2000, 0~10V면 4000 → 이 장비는 0~5V이므로 2000.
#   pv_zero  = 유량 0일 때의 ADC 카운트(4~20mA 장비를 0~20mA 범위로 읽으면 800).
#   pv_full  = 풀스케일일 때의 ADC 카운트(AD08A 출력데이터타입 0~4000 기준 4000).
# 주소가 아니라 plc_catalog의 '채널 이름'으로 배정한다(오타·종류혼동·오배정 차단).
#   sv_out = DAC 채널 이름(DAC1_CH0 …) 또는 None(미배정)
#   pv_in  = ADC 채널 이름(ADC_CH0 …) 또는 None(미배정)
# 밸브 지령 코일은 채널 id로 카탈로그가 결정하므로 여기에 없다.
# ★ 실배선 확정(P40~43=VA1·3·5·6, DAC CH0~3, ADC CH0·2·4·5).
#   VA2·4·7·8은 미배선 — 배선 후 여기와 config를 함께 갱신.
DEFAULT_CHANNEL_PLC = {
    "VA1": {"sv_out": "DAC1_CH0", "pv_in": "ADC_CH0",
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA2": {"sv_out": None, "pv_in": None,
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA3": {"sv_out": "DAC1_CH1", "pv_in": "ADC_CH2",
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA4": {"sv_out": None, "pv_in": None,
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA5": {"sv_out": "DAC1_CH2", "pv_in": "ADC_CH4",
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA6": {"sv_out": "DAC1_CH3", "pv_in": "ADC_CH5",
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA7": {"sv_out": None, "pv_in": None,
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
    "VA8": {"sv_out": None, "pv_in": None,
            "fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000},
}

# 채널 무관 시스템 공통 주소(하트비트/안전리셋/4-way/상태·알람).
DEFAULT_PLC_SYSTEM = {
    "heartbeat":    176,   # M00110 (쓰기) 통신 생존 토글
    "safety_reset": 178,   # M00112 (쓰기, 펄스) 안전리셋
    "v4w_cmd":      168,   # M00108 (쓰기) 4-way 지령   ★164에서 이동
    # 320 (M00200) · 336 (M00210) — 공압 인터록 제거로 미사용. 복원 시 재사용 예약.
    "safety_stop":  321,   # M00201 (읽기) 안전정지 상태
    "run_permit":   323,   # M00203 (읽기) 운전 허가 래치
    "alm_mfc":      337,   # M00211 (읽기) MFC 입력 이상 알람
    "alm_idd":      338,   # M00212 (읽기) MFC 입력 단선검출 알람
    "alm_dac":      339,   # M00213 (읽기) 아날로그 출력 모듈 이상 알람
}

# 하드웨어 구성(주소가 아님). plc_system은 전부 Modbus 주소이므로 성격이 다른 값은 여기 둔다.
DEFAULT_PLC_HW = {
    "dac_modules": 1,           # DV04A 장착 수. 증설하면 2로 올린다
    "v4w_on_is_sensor": True,   # 4-way 코일 ON이 측정(sensor) 방향인가.
                                # ★ 확정(2026-08-12): 실물 무전원 위치가 가스→Vent / 에어→Sensor 이고
                                #   래더가 V4W_OUT = V4W_CMD AND RUN_PERMIT 이라 트립 시 그 위치가 된다
                                #   → true 가 정답. P48 배선 확인은 현장 검증 항목으로 남아 있다.
                                #   실기에서 반대면 false로 바꾼다(코드 수정 불필요)
}

# 스케일 키 기본값(채널 기본값에도 없을 때의 최후 방어값).
PLC_SCALE_DEFAULTS = {"fs_sccm": 1000, "sv_full": 2000, "pv_zero": 0, "pv_full": 4000}


def _default_channel_plc(cid: str):
    """채널 id의 기본 PLC 주소(사본). 매핑 없으면 None."""
    m = DEFAULT_CHANNEL_PLC.get(cid)
    return dict(m) if isinstance(m, dict) else None


def _norm_channel_plc(v, cid: str = ""):
    """채널 plc 값 정규화: dict면 사본, 그 외는 None.
    - sv_out / pv_in : 문자열이면 그대로, 없거나 null이면 None(기본값으로 채우지 않는다 —
      배정은 현장 배선 문제라 임의로 추측하면 안 된다)
    - 옛 주소 키(cmd_coil/sv_reg/pv_reg)는 버린다(이제 plc_catalog가 결정한다)
    - 스케일 4키는 채널 기본값으로 보강"""
    if not isinstance(v, dict):
        return None
    out = dict(v)
    for legacy in ("cmd_coil", "sv_reg", "pv_reg"):
        out.pop(legacy, None)
    for k in ("sv_out", "pv_in"):
        out[k] = out[k] if isinstance(out.get(k), str) else None
    base = DEFAULT_CHANNEL_PLC.get(cid) or {}
    for k, dflt in PLC_SCALE_DEFAULTS.items():
        if k not in out:
            out[k] = base.get(k, dflt)
    return out


def validate_channel_map(channels, plc_hw) -> list:
    """채널 배정(sv_out/pv_in)을 검사해 문제 목록을 돌려준다.
    반환: [{"level": "warn"|"info", "msg": str}]
      warn — 지금 당장 문제(알 수 없는 이름·종류 혼동·증설 미장착·중복·en인데 미배정·알 수 없는 id)
      info — 지금은 무해하지만 알아둘 것(en=False인데 미배정)
    ★ 예외를 던지지 않는다 — 배정이 이상해도 프로그램은 떠야 진단이 가능하다.
    ★ 경고를 무작정 늘리면 사람이 읽지 않게 되므로 심각도를 나눈다."""
    import plc_catalog as cat

    problems = []

    def warn(msg):
        problems.append({"level": "warn", "msg": msg})

    def info(msg):
        problems.append({"level": "info", "msg": msg})

    try:
        max_mod = int((plc_hw or {}).get("dac_modules", 1))
    except (TypeError, ValueError):
        max_mod = 1
    sv_used, pv_used = {}, {}
    dac_all = ", ".join(("DAC1_CH0~CH3", "DAC2_CH0~CH3"))
    adc_all = "ADC_CH0~CH7"

    for ch in channels or []:
        p = ch.get("plc")
        if not p:
            continue
        cid = ch.get("id", "?")
        if cat.valve_coil(cid) is None:      # 6) 알 수 없는 채널 id
            warn(f"{cid}: 알 수 없는 채널 id — 밸브 코일을 결정할 수 없습니다")

        sv, pv = p.get("sv_out"), p.get("pv_in")
        if sv is not None:
            if sv in cat.ADC_CHANNELS:       # 2) 종류 혼동
                warn(f"{cid}: '{sv}'는 입력 채널입니다. SV에는 DAC 채널을 지정하세요")
            elif sv not in cat.DAC_CHANNELS:  # 1) 알 수 없는 이름
                warn(f"{cid}: 알 수 없는 SV 채널 '{sv}' — 사용 가능: {dac_all}")
            else:
                if cat.DAC_CHANNELS[sv]["module"] > max_mod:   # 3) 증설 모듈 미장착
                    warn(f"{cid}: '{sv}'은 증설 모듈(DV04A #2)이 필요합니다. "
                         f"plc_hw.dac_modules를 2로 올리거나 DAC1_CH0~CH3 중에서 고르세요")
                sv_used.setdefault(sv, []).append(cid)
        elif ch.get("en"):                   # 5) 사용 중인데 미배정
            warn(f"{cid}: 사용(en) 상태인데 SV 출력이 배정되지 않았습니다. "
                 f"밸브는 열리지만 유량 지령이 나가지 않습니다")
        else:                                # 5') 지금은 en=False — 켜기 전에 알린다
            info(f"{cid}: SV 출력(sv_out)이 배정되지 않았습니다 — 현재 en=False 라 지령이 "
                 f"나가지 않습니다. 사용하려면 config.json 의 sv_out 배정이 필요합니다")

        # 스케일 유효성: 0 이하면 변환식이 무효화되거나 0으로 나누기가 된다.
        fs = float(p.get("fs_sccm") or 0)
        sv_full = float(p.get("sv_full") or 0)
        pv_full = float(p.get("pv_full") or 0)
        pv_zero = float(p.get("pv_zero") or 0)
        if fs <= 0:
            warn(f"{cid}: 풀스케일(fs_sccm)이 {p.get('fs_sccm')} 입니다. 0보다 커야 합니다")
        if sv_full <= 0:
            warn(f"{cid}: SV 풀카운트(sv_full)가 {p.get('sv_full')} 입니다. 0보다 커야 합니다")
        if pv_full <= pv_zero:
            warn(f"{cid}: PV 풀카운트({p.get('pv_full')})가 영점카운트({p.get('pv_zero')}) 이하입니다")
        # MAX(운전 상한)가 하드웨어 풀스케일을 넘으면 SV가 풀스케일에서 포화된다.
        mx = float(ch.get("max") or 0)
        if fs > 0 and mx > fs + 1e-6:
            warn(f"{cid}: MAX {mx:g} sccm이 풀스케일 {fs:g} sccm을 초과합니다. "
                 f"SV가 풀스케일에서 포화되어 화면 값과 실제 유량이 달라집니다")

        if pv is not None:
            if pv in cat.DAC_CHANNELS:       # 2) 종류 혼동
                warn(f"{cid}: '{pv}'는 출력 채널입니다. PV에는 ADC 채널을 지정하세요")
            elif pv not in cat.ADC_CHANNELS:  # 1) 알 수 없는 이름
                warn(f"{cid}: 알 수 없는 PV 채널 '{pv}' — 사용 가능: {adc_all}")
            else:
                pv_used.setdefault(pv, []).append(cid)

    for name, ids in sv_used.items():        # 4) 중복 배정
        if len(ids) > 1:
            warn(f"{', '.join(ids)}가 SV 채널 '{name}'을 함께 씁니다. 한쪽 유량이 무시됩니다")
    for name, ids in pv_used.items():
        if len(ids) > 1:
            warn(f"{', '.join(ids)}가 PV 채널 '{name}'을 함께 씁니다. 한쪽 측정값이 무시됩니다")
    return problems


def _copy_channel(c: dict) -> dict:
    """채널 dict 사본(중첩 plc까지 분리 — 기본값과 상태가 참조 공유하지 않도록)."""
    out = dict(c)
    out["plc"] = _norm_channel_plc(c.get("plc"), c.get("id", ""))
    return out


DEFAULT_CHANNELS = [
    {"id": "VA1", "grp": "air", "route": "mix",  "en": True,  "max": 1000, "sv": 0, "plc": _default_channel_plc("VA1")},
    # ★ 물1(VA2)=단독 라인측 가습 / 물2(VA4)=혼합 라인측 가습 — 위치 기준 **잠정** 페어링.
    #   실배관 확정 시 route 를 재검토할 것(희석 계산·배관도 선이 이 값을 따라간다).
    {"id": "VA2", "grp": "air", "route": "pure", "en": False, "max": 1000, "sv": 0, "plc": _default_channel_plc("VA2")},
    {"id": "VA3", "grp": "air", "route": "pure", "en": True,  "max": 1000, "sv": 0, "plc": _default_channel_plc("VA3")},
    {"id": "VA4", "grp": "air", "route": "mix",  "en": False, "max": 1000, "sv": 0, "plc": _default_channel_plc("VA4")},
    {"id": "VA5", "grp": "gas", "route": "mix",  "en": True,  "max": 1000, "sv": 0, "plc": _default_channel_plc("VA5")},
    {"id": "VA6", "grp": "gas", "route": "mix",  "en": True,  "max": 1000, "sv": 0, "plc": _default_channel_plc("VA6")},
    {"id": "VA7", "grp": "gas", "route": "mix",  "en": False, "max": 1000, "sv": 0, "plc": _default_channel_plc("VA7")},
    {"id": "VA8", "grp": "gas", "route": "mix",  "en": False, "max": 1000, "sv": 0, "plc": _default_channel_plc("VA8")},
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
    # 측정 프로그램(외부 exe) 런처. path가 비면 아무 것도 하지 않는다.
    #   autoLaunch — 프로그램 기동 후 '첫' AUTO RUN 때만 자동 실행(세션 1회).
    "measureApp": {"path": "", "autoLaunch": False},
}

# PLC 통신(LS XGB 내장 Cnet Modbus). 전송은 시리얼(RTU) 또는 TCP.
DEFAULT_PLC = {
    "mode": "serial",             # serial(RTU) | tcp
    "host": "127.0.0.1",          # tcp 호스트
    "tcp_port": 502,              # tcp 포트
    "port": "",                   # (시리얼) 예: COM3(Windows) / /dev/ttyUSB0(Linux). 비면 연결 안 함.
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
        "useHumidity": False,
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
        "useHumidity": bool(r.get("useHumidity", False)),
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
        self.plc_hw = dict(DEFAULT_PLC_HW)
        # PLC 실측 라이브(읽기 경로): 폴링 태스크가 갱신, snapshot으로 프론트에 전송.
        self.plc_live = {"connected": False, "pv": {}, "pv_raw": {}, "status": {}}
        # 기동 시점 진단(채널 배정·쓰기 권한 등). lifespan에는 접속한 클라이언트가 없어
        # push_log가 허공으로 사라지므로, 여기 보관했다가 접속할 때 전달한다.
        # ★ 지우지 않는다 — 새로고침·재접속해도 다시 보여야 한다.
        self.startup_notices = []   # [{"msg": str, "level": "warn"|"info"}]
        self.system = {
            "running": False,
            # 기본 방향은 vent — 준비 단계에서 센서에는 단독 에어만 가고,
            # 혼합가스는 측정 단계에 들어가야 센서로 흐른다.
            "routeOut": "vent",
            "loop": {"current": 0, "total": 1},
            "elapsed": 0,
            "rh": None,          # 측정 하드웨어 없음 → 화면 "—"
            "smu": None,         # 측정 하드웨어 없음 → 화면 "—"
            "safeStop": False,
            "purging": False,   # PURGE 래치(재클릭 중단) — 트립·끊김·실행 시 자동 해제
            "phase": "idle",      # idle | prep | meas
            "stepIndex": 0,       # 현재 단계(1-base, 0=대기)
            "stepTotal": 0,       # 전체 단계 수
            "stepRemain": 0,      # 현재 단계 남은 초
            # 측정 프로그램 자동 실행을 이번 세션에 이미 시도했는가(저장하지 않는 세션 플래그).
            # 서버가 다시 뜨면 False로 돌아간다 — STOP·완료로는 되돌리지 않는다.
            "measureLaunched": False,
        }
        self.recipe = default_recipe()
        self._elapsed_f = 0.0    # 내부 누적 경과시간(float)

    # ---- config 로드/저장 ----
    def load_config(self):
        data = safe_read_json(CONFIG_PATH)
        if not data:
            logger.early("info", "config.json 없음 — 기본값으로 생성")
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
                plc_val = _norm_channel_plc(c["plc"], cid) if "plc" in c else default_plc
                base.update({
                    "id": cid,
                    "grp": c.get("grp", base.get("grp", "air")),
                    "route": c.get("route", base.get("route", "mix")),
                    "en": bool(c.get("en", base.get("en", False))),
                    "max": c.get("max", base.get("max", 1000)),
                    "sv": c.get("sv", base.get("sv", 0)),
                    "plc": plc_val,
                })
                normalized.append(base)
            self.channels = normalized
        if isinstance(data.get("params"), dict):
            self.params = {**DEFAULT_PARAMS, **data["params"]}
            self.recipe["params"] = dict(self.params)
        self.settings = {**DEFAULT_SETTINGS, **(data.get("settings") or {})}
        # measureApp은 중첩 dict라 통째로 덮이므로 빠진 키를 기본값으로 보강한다.
        ma = self.settings.get("measureApp")
        self.settings["measureApp"] = {**DEFAULT_SETTINGS["measureApp"],
                                       **(ma if isinstance(ma, dict) else {})}
        self.plc = {**DEFAULT_PLC, **(data.get("plc") or {})}
        self.plc_system = {**DEFAULT_PLC_SYSTEM, **(data.get("plc_system") or {})}
        self.plc_hw = {**DEFAULT_PLC_HW, **(data.get("plc_hw") or {})}
        # 사용 채널은 시작 시 밸브 열림으로 둔다(데모 일관성).
        for c in self.channels:
            c.setdefault("valveIn", False)   # 시작 시 모든 밸브 닫힘(흐름 표시도 꺼진 상태)
            c.setdefault("pv", 0)

    def save_config(self):
        payload = {
            "channels": [
                {"id": c["id"], "grp": c["grp"], "route": c["route"],
                 "en": bool(c["en"]), "max": c["max"], "sv": c["sv"],
                 "plc": _norm_channel_plc(c.get("plc"), c["id"])}
                for c in self.channels
            ],
            "params": self.params,
            "settings": self.settings,
            "plc": self.plc,
            "plc_system": self.plc_system,
            "plc_hw": self.plc_hw,
        }
        try:
            atomic_write_json(CONFIG_PATH, payload)
        except Exception as e:  # noqa: BLE001
            # 여기는 동기 컨텍스트라 push_log(async)를 쓸 수 없다 → 파일 로그로 남긴다.
            logger.write("warn", f"config 저장 실패: {e}")

    # ---- 알람 인터록 판정(조작 게이트의 단일 출처) ----
    # ★ commands·loops 가 state.alarm_lock() 으로 부른다(둘 다 State 인스턴스를 import).
    def alarm_lock(self) -> bool:
        """MFC·DAC 알람 활성 여부(연결 중일 때만) — 조작 인터록의 단일 판정.
        IDD(단선)는 제외: 0~5V 배선에서 4~20mA 단선검출은 오검출 여지가 있어
        로깅만 한다(실기 신뢰 확인 후 승격 검토)."""
        st = (self.plc_live.get("status") or {})
        return bool(self.plc_live.get("connected")) and (
            st.get("ALM_MFC") is True or st.get("ALM_DAC") is True)

    def alarm_names(self) -> str:
        st = (self.plc_live.get("status") or {})
        out = []
        if st.get("ALM_MFC") is True:
            out.append("MFC 입력 이상")
        if st.get("ALM_DAC") is True:
            out.append("아날로그 출력 이상")
        return "·".join(out) or "알람"

    # ---- 외부로 내보낼 상태 스냅샷 ----
    # 레시피는 "권위 있는 변경"(연결 직후/New/Open/Save) 때만 포함한다.
    # 밸브·4-way·RUN 등 일상 push에는 recipe를 빼서, 편집 중인 레시피 초안을 덮어쓰지 않는다.
    def snapshot(self, include_recipe: bool = False) -> dict:
        snap = {
            "type": "state",
            "version": {"name": version.APP_NAME, "version": version.APP_VERSION,
                        "build": version.BUILD_DATE},
            "channels": [dict(c) for c in self.channels],
            "system": dict(self.system),
            "settings": dict(self.settings),
            "plc": dict(self.plc),
            "plc_system": dict(self.plc_system),
            "plc_hw": dict(self.plc_hw),
            "plc_live": {
                "connected": bool(self.plc_live.get("connected")),
                "pv": dict(self.plc_live.get("pv") or {}),
                "pv_raw": dict(self.plc_live.get("pv_raw") or {}),
                "status": dict(self.plc_live.get("status") or {}),
            },
        }
        if include_recipe:
            snap["recipe"] = json.loads(json.dumps(self.recipe))
        return snap


# 채널 역할(엔진/계산용). 물탱크(가습기)가 달린 채널 = 젖은 공기.
# ⚠️ VA2·VA4가 이제 PLC에 실제로 연결된다(이전엔 매핑 없어 계산만 됐음).
#    실제 배관에서 물탱크(가습기)가 VA2·VA4에 달려 있는지 현장 확인 필요.
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
