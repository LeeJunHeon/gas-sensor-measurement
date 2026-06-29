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

    pv = []
    for c in state.channels:
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

    total = int(state.recipe.get("loopCount") or 0) or 1
    state.system["loop"]["total"] = total
    if state.system["running"]:
        state.system["loop"]["current"] = min(total, 1 + elapsed // 10)
    state.system["loop"]["current"] = state.system["loop"].get("current", 0)

    return {
        "type": "telemetry",
        "pv": pv,
        "rh": None,
        "smu": None,
        "elapsed": elapsed,
        "running": state.system["running"],
        "loop": dict(state.system["loop"]),
    }
