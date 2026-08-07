"""
plc_catalog.py — PLC 래더가 제공하는 채널 목록(고정 계약).

★ 이 파일은 PLC 래더와 1:1 대응이다. 래더를 바꾸지 않는 한 여기도 바뀌지 않는다.
  config.json은 여기 정의된 '이름'만 고를 수 있다(숫자를 직접 쓰지 않는다).
"""

# --- 밸브 지령 코일: 채널 id로 완전히 결정된다. config에서 변경 불가. ---
VALVE_COILS = {
    "VA1": 160, "VA2": 161, "VA3": 162, "VA4": 163,
    "VA5": 164, "VA6": 165, "VA7": 166, "VA8": 167,
    "V4W": 168,
}

# --- SV 출력(DV04A). module=2는 증설 모듈이 있어야 실제로 나간다. ---
DAC_CHANNELS = {
    "DAC1_CH0": {"reg": 100, "label": "DV04A #1 CH0", "module": 1},
    "DAC1_CH1": {"reg": 101, "label": "DV04A #1 CH1", "module": 1},
    "DAC1_CH2": {"reg": 102, "label": "DV04A #1 CH2", "module": 1},
    "DAC1_CH3": {"reg": 103, "label": "DV04A #1 CH3", "module": 1},
    "DAC2_CH0": {"reg": 104, "label": "DV04A #2 CH0", "module": 2},
    "DAC2_CH1": {"reg": 105, "label": "DV04A #2 CH1", "module": 2},
    "DAC2_CH2": {"reg": 106, "label": "DV04A #2 CH2", "module": 2},
    "DAC2_CH3": {"reg": 107, "label": "DV04A #2 CH3", "module": 2},
}

# --- PV 입력(AD08A). 8채널 모두 래더가 읽는다. ---
ADC_CHANNELS = {
    f"ADC_CH{i}": {"reg": 200 + i, "label": f"AD08A CH{i}"} for i in range(8)
}


def valve_coil(cid: str):
    """채널 id → 밸브 지령 코일 주소. 카탈로그에 없는 id면 None."""
    return VALVE_COILS.get(cid)


def dac_reg(name):
    """SV 채널 이름 → 홀딩 레지스터 주소. 이름이 없거나 모르면 None."""
    m = DAC_CHANNELS.get(name)
    return m["reg"] if m else None


def adc_reg(name):
    """PV 채널 이름 → 홀딩 레지스터 주소. 이름이 없거나 모르면 None."""
    m = ADC_CHANNELS.get(name)
    return m["reg"] if m else None


def dac_names(max_module: int = 2) -> list:
    """UI 드롭다운용 SV 채널 이름 목록. max_module로 증설 미장착분을 걸러낸다."""
    return [n for n, m in DAC_CHANNELS.items() if m["module"] <= max_module]


def adc_names() -> list:
    """UI 드롭다운용 PV 채널 이름 목록."""
    return list(ADC_CHANNELS.keys())


def describe_dac(name) -> str:
    """'DAC1_CH2 (D102)' 형태의 표시 문자열. 모르는 이름이면 그대로 돌려준다."""
    m = DAC_CHANNELS.get(name)
    return f"{name} (D{m['reg']})" if m else str(name)


def describe_adc(name) -> str:
    """'ADC_CH4 (D204)' 형태의 표시 문자열. 모르는 이름이면 그대로 돌려준다."""
    m = ADC_CHANNELS.get(name)
    return f"{name} (D{m['reg']})" if m else str(name)


# ── 표시 전용: 래더 VALVE 블록의 물리 출력 대응 ─────────────────────────────
# ★ 통신 계약이 아니다(코일 번호는 위 VALVE_COILS 가 계약). 화면 안내용이며,
#   PLC 래더 VALVE 블록을 수정하면 이 표도 반드시 함께 갱신한다.
#   2026-08-07 확정 배선: P40~P43 = VA1·VA3·VA5·VA6, 4-way = P48(배선 미검증).
VALVE_OUTPUTS = {
    "VA1": "P40", "VA2": None, "VA3": "P41", "VA4": None,
    "VA5": "P42", "VA6": "P43", "VA7": None, "VA8": None,
    "V4W": "P48",
}
