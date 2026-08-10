"""
recipe_calc.py — 한 프로세스 단계 → 각 채널 목표 SV 계산 + MAX/구성 검증.

표준 동적 희석:
  가스 SV = 전체유량 × (목표ppm / 봄베ppm)
  공기 = 전체유량 − 가스합  → 습도 비율로 젖은/마른 공기 분배(물탱크 통과=100%RH 가정)
공기는 역할별 담당 채널(en=True)에 균등 분배. MAX 초과/구성 불가 시 위반 목록 반환.
★ 희석에 쓰는 공기는 가스 매니폴드에 합류하는 혼합(route=mix) 라인만이다.
  단독(route=pure) 에어는 4-way로 직행해 센서로만 가므로 희석 계산 소관 밖이다
  (레시피가 건드리지 않고 사람이 설정한 값을 그대로 유지한다).
가스는 봄베가 물리 배관에 고정돼 있어 G1~G4 ↔ 가스 채널 순서로 고정 대응한다(대체 없음).
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

    # 이 단계가 실제로 흘릴 게 있는지 검증(전부 0이면 무의미한 단계)
    any_gas = any(float(x or 0) > 0 for x in g[:4])
    if total <= 0:
        errors.append("전체 유량(Gas Flow)이 0")
    # 가스도 습도도 0인 단계(에어만 흘리는 베이스라인/세정 단계)는 유효하다 —
    # 혼합 마른공기가 total 을 그대로 받는다. 받을 채널이 없으면 아래 dry 검사가 잡는다.

    # 역할별 채널 인덱스(사용 중인 것만)
    # ★ route=="pure"(4-way 직행 단독 라인)는 희석 배분에서 제외한다 — 실물 배관상
    #   가스와 섞이지 않으므로, 여기에 유량을 나눠주면 실제 농도가 계산과 달라진다.
    wet_idx = [i for i, c in enumerate(channels)
               if channel_role(c) == "wet_air" and c.get("en") and c.get("route") != "pure"]
    dry_idx = [i for i, c in enumerate(channels)
               if channel_role(c) == "dry_air" and c.get("en") and c.get("route") != "pure"]
    # ★ 가스는 en을 보지 않는다 — 봄베가 물리적으로 특정 밸브에 연결돼 있으므로
    #   G(k)는 가스 역할 채널의 k번째에 고정 대응한다. 꺼져 있다고 다음 채널로
    #   밀면 다른 농도의 봄베가 열려 계산과 실제가 어긋난다(조용한 오측정).
    gas_all = [i for i, c in enumerate(channels) if channel_role(c) == "gas"]

    sv = {i: 0.0 for i, _ in enumerate(channels)}

    # 1) 가스: 목표 농도별 필요 유량. G(k) → gas_all[k] 고정 배정.
    gas_flows = []
    for k in range(4):
        tgt = float(g[k] or 0)
        bot = float(b[k] or 0)
        if tgt <= 0:
            continue
        if k >= len(gas_all):
            errors.append(f"G{k+1} 목표 {tgt}ppm 인데 대응할 가스 채널이 없습니다")
            continue
        gi = gas_all[k]
        gid = channels[gi].get("id")
        if not channels[gi].get("en"):
            errors.append(f"G{k+1}({gid}) 목표 {tgt}ppm 인데 해당 채널이 꺼져 있습니다. "
                          f"봄베 {k+1}은 {gid}에 연결돼 있어 다른 채널로 대체할 수 없습니다")
            continue
        if bot <= 0:
            errors.append(f"G{k+1} 목표 {tgt}ppm 인데 봄베 농도가 0 또는 비어있음")
            continue
        if tgt > bot:
            errors.append(f"G{k+1} 목표 {tgt}ppm 이 봄베 {bot}ppm 보다 큼(불가능)")
            continue
        flow = total * (tgt / bot)
        sv[gi] = flow
        gas_flows.append(flow)

    gas_sum = sum(gas_flows)
    air_total = total - gas_sum
    if air_total < -1e-9:
        errors.append(f"가스 유량 합({gas_sum:.1f})이 전체 유량({total:.1f})을 초과")
        air_total = 0.0

    # 2) 공기: 습도 비율로 젖은/마른 분배
    wet_total = air_total * (rh / 100.0)
    dry_total = air_total - wet_total
    if wet_total > 1e-9 and not wet_idx:
        errors.append("혼합용 젖은 공기(단독 제외)가 필요한데 물탱크 채널이 꺼져 있거나 없음")
    if dry_total > 1e-9 and not dry_idx:
        errors.append("혼합용 마른 공기(단독 제외)가 필요한데 해당 채널이 꺼져 있거나 없음")
    if wet_idx and wet_total > 0:
        per = wet_total / len(wet_idx)
        for i in wet_idx:
            sv[i] = per
    if dry_idx and dry_total > 0:
        per = dry_total / len(dry_idx)
        for i in dry_idx:
            sv[i] = per

    # 3) MAX 초과 검증 + 풀스케일 초과 검증
    #    MAX는 운전 상한, fs_sccm은 MFC 하드웨어 한계라 별개다. 풀스케일을 넘으면
    #    DAC 카운트가 포화돼 화면 값과 실제 유량이 달라지므로 함께 막는다.
    for i, c in enumerate(channels):
        if sv[i] > float(c.get("max") or 0) + 1e-6:
            errors.append(f"{c.get('id')} 필요 {sv[i]:.1f} sccm 이 MAX {c.get('max')} 초과")
        fs = float((c.get("plc") or {}).get("fs_sccm") or 0)
        if fs > 0 and sv[i] > fs + 1e-6:
            errors.append(f"{c.get('id')} 필요 {sv[i]:.1f} sccm 이 풀스케일 {fs:g} 초과")

    return {"sv": sv, "errors": errors}
