"""
recipe_calc.py — 한 프로세스 단계 → 각 채널 목표 SV 계산 + MAX/구성 검증.

표준 동적 희석:
  가스 SV = 전체유량 × (목표ppm / 봄베ppm)
  공기 = 전체유량 − 가스합  → 습도 비율로 젖은/마른 공기 분배(물탱크 통과=100%RH 가정)
역할별 담당 채널(en=True)에 균등 분배. MAX 초과/구성 불가 시 위반 목록 반환.
"""

from state import channel_role


def compute_step_setpoints(channels, proc, bottle, use_humidity=True):
    """
    channels: state.channels (각 dict: id/grp/route/en/max/sv...)
    proc: {flow, rh, g:[g1..g4], prep, meas, rep}
    bottle: [b1..b4] 봄베 농도(ppm)
    반환: {"sv": {channel_index: 목표값}, "errors": [문자열...]}
      errors가 비어있지 않으면 실행 불가(차단 대상).
    """
    errors = []
    total = float(proc.get("flow") or 0)
    rh = float(proc.get("rh") or 0) if use_humidity else 0.0
    g = list(proc.get("g") or [])
    while len(g) < 4:
        g.append(0)
    b = list(bottle or [])
    while len(b) < 4:
        b.append(0)

    # 역할별 채널 인덱스(사용 중인 것만)
    gas_idx = [i for i, c in enumerate(channels) if channel_role(c) == "gas" and c.get("en")]
    wet_idx = [i for i, c in enumerate(channels) if channel_role(c) == "wet_air" and c.get("en")]
    dry_idx = [i for i, c in enumerate(channels) if channel_role(c) == "dry_air" and c.get("en")]

    sv = {i: 0.0 for i, _ in enumerate(channels)}

    # 1) 가스: 목표 농도별 필요 유량. 가스 채널에 순서대로 1:1 배정(G1→첫 가스채널 ...).
    gas_flows = []
    for k in range(4):
        tgt = float(g[k] or 0)
        bot = float(b[k] or 0)
        if tgt <= 0:
            continue
        if bot <= 0:
            errors.append(f"G{k+1} 목표 {tgt}ppm 인데 봄베 농도가 0 또는 비어있음")
            continue
        if tgt > bot:
            errors.append(f"G{k+1} 목표 {tgt}ppm 이 봄베 {bot}ppm 보다 큼(불가능)")
            continue
        gas_flows.append(total * (tgt / bot))
    if len(gas_flows) > len(gas_idx):
        errors.append(f"가스 {len(gas_flows)}종 필요한데 사용 중인 가스 채널은 {len(gas_idx)}개")
    for n, flow in enumerate(gas_flows):
        if n < len(gas_idx):
            sv[gas_idx[n]] = flow

    gas_sum = sum(gas_flows)
    air_total = total - gas_sum
    if air_total < -1e-9:
        errors.append(f"가스 유량 합({gas_sum:.1f})이 전체 유량({total:.1f})을 초과")
        air_total = 0.0

    # 2) 공기: 습도 비율로 젖은/마른 분배
    wet_total = air_total * (rh / 100.0)
    dry_total = air_total - wet_total
    if wet_total > 1e-9 and not wet_idx:
        errors.append("젖은 공기가 필요한데 물탱크(습한 공기) 채널이 꺼져 있거나 없음")
    if dry_total > 1e-9 and not dry_idx:
        errors.append("마른 공기가 필요한데 마른 공기 채널이 꺼져 있거나 없음")
    if wet_idx and wet_total > 0:
        per = wet_total / len(wet_idx)
        for i in wet_idx:
            sv[i] = per
    if dry_idx and dry_total > 0:
        per = dry_total / len(dry_idx)
        for i in dry_idx:
            sv[i] = per

    # 3) MAX 초과 검증
    for i, c in enumerate(channels):
        if sv[i] > float(c.get("max") or 0) + 1e-6:
            errors.append(f"{c.get('id')} 필요 {sv[i]:.1f} sccm 이 MAX {c.get('max')} 초과")

    return {"sv": sv, "errors": errors}
