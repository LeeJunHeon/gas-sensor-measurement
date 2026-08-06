"""
simulation.py — 시뮬레이션 telemetry 생성.

1단계는 측정 하드웨어가 없으므로:
- 가스 유량(PV)은 MFC 흐름이라 sv 주변으로 시뮬레이션한다.
- rh·smu(측정값)는 시뮬레이션하지 않는다(None → 화면 "—").

추후 이 모듈만 실제 장비 측정값으로 교체한다.
"""

import random


def sim_tick(state, dt: float) -> dict:
    """state를 한 틱 진행시키고 telemetry dict를 반환한다."""
    if state.system["running"]:
        state._elapsed_f += dt
    elapsed = int(state._elapsed_f)
    state.system["elapsed"] = elapsed

    # PLC가 연결돼 있고 그 채널에 실측 PV가 있으면 실측값이 진실이다.
    # 시뮬레이션으로 덮으면 state.channels[i]["pv"]와 snapshot에 가짜 값이 실려 나가고,
    # 앞으로 이 값을 읽는 코드가 전부 가짜를 쓰게 된다(화면은 프론트가 덮어써서 우연히 맞을 뿐).
    live = state.plc_live or {}
    live_pv = (live.get("pv") or {}) if live.get("connected") else {}

    pv = []
    for c in state.channels:
        real = live_pv.get(c["id"])
        if real is not None:
            val = float(real)
        else:
            flowing = c["en"] and c.get("valveIn")
            if flowing:
                target = float(c.get("sv") or 0)
                amp = 1.6 if target > 0 else 0.4
                val = target + (random.random() - 0.5) * amp
                if val < 0:
                    val = 0.0
            else:
                val = 0.0
        c["pv"] = val
        pv.append(round(val, 2))

    # rh·smu(측정값)는 측정 하드웨어가 아직 없으므로 시뮬레이션하지 않는다(화면은 "—" 표시).
    # 가스 유량(PV)은 MFC 흐름이라 유효 → 위에서 계속 시뮬레이션한다.
    # loop(전체 반복)는 엔진(engine.py)이 소유 → sim은 건드리지 않고 현재 값을 그대로 실어 보낸다.

    return {
        "type": "telemetry",
        "pv": pv,
        "rh": None,
        "smu": None,
        "elapsed": elapsed,
        "running": state.system["running"],
        "loop": dict(state.system["loop"]),
        "phase": state.system.get("phase", "idle"),
        "stepIndex": state.system.get("stepIndex", 0),
        "stepTotal": state.system.get("stepTotal", 0),
        "stepRemain": state.system.get("stepRemain", 0),
    }
