"""
engine.py — 레시피 단계 진행 엔진.
P1→P2→… 순서로: 계산→SV적용→준비(prep)대기→측정(meas)유지→다음. Loop Count 반복.
측정 하드웨어가 없으므로 측정 구간은 값 유지하며 시간만 흐른다.
진행이 끝나면(정상 완료·STOP·PLC 이상 중단) 항상 가스를 차단한다 — 유량을 남기지 않는다.
"""

import asyncio

import plc_catalog
from state import state, channel_role
from recipe_calc import compute_step_setpoints
from connection import push_state, push_log

_task = None


def is_running() -> bool:
    return _task is not None and not _task.done()


def precheck(recipe) -> list:
    """모든 단계 계산·검증. 실행 불가 사유 목록 반환(비어있으면 실행 가능)."""
    procs = recipe.get("procs") or []
    bottle = recipe.get("bottle") or []
    use_h = bool(recipe.get("useHumidity", True))
    problems = []
    if not procs:
        return ["추가된 프로세스 단계가 없습니다 (＋Add Process로 단계를 추가하세요)"]
    for n, proc in enumerate(procs):
        res = compute_step_setpoints(state.channels, proc, bottle, use_h)
        for e in res["errors"]:
            problems.append(f"P{n + 1} — {e}")
        # 유량을 배분받은 채널에 SV 출력이 배정돼 있는지 확인.
        # 미배정이면 레시피는 정상으로 도는데 가스가 안 나가 측정이 통째로 무효가 된다.
        for i, v in (res.get("sv") or {}).items():
            if not v or v <= 0:
                continue
            c = state.channels[i] if 0 <= i < len(state.channels) else None
            p = (c or {}).get("plc") or {}
            if plc_catalog.dac_reg(p.get("sv_out")) is None:
                problems.append(
                    f"P{n + 1} — {(c or {}).get('id', '?')}: 유량 {v:g} sccm이 배정됐으나 "
                    f"SV 출력 채널이 없어 실행할 수 없습니다"
                    f" (config.json의 channels[].plc.sv_out 확인)")
    return problems


def _apply_setpoints(sv: dict):
    """계산된 SV를 채널에 적용하고 밸브를 자동 개폐한다.
    유량이 필요한 채널(sv>0)은 열고, 0인 채널은 닫는다(비상정지로 닫혀 있어도 자동 복구).
    꺼진 채널(en=False)은 항상 닫힘.
    ★ 단독(route=pure) 에어 라인은 건드리지 않는다 — 4-way로 직행하는 센서측 공급이라
      레시피의 희석 소관이 아니다. 실행 전에 사람이 맞춰둔 SV·밸브를 그대로 유지한다.
    4-way 방향은 단계 진행(_phase)이 준비=vent / 측정=sensor 로 전환한다."""
    for i, c in enumerate(state.channels):
        if channel_role(c) in ("dry_air", "wet_air") and c.get("route") == "pure":
            continue
        v = sv.get(i, 0.0)
        c["sv"] = v
        if c.get("en") and v > 0:
            c["valveIn"] = True
        else:
            c["valveIn"] = False


def _all_close():
    """자동 진행이 끝나면(정상 완료·STOP 공통) 가스를 차단한다 — 모든 SV=0, 모든 밸브 닫힘.
    이전 규칙('유량 유지')은 자리를 비운 사이 가스가 계속 소모되는 문제로 폐기했다."""
    for c in state.channels:
        c["sv"] = 0.0
        c["valveIn"] = False
    # 진행이 끝나면 4-way도 안전 방향(vent)으로 되돌린다 — 다음 준비 단계의 기본 상태.
    state.system["routeOut"] = "vent"


def _emergency_off():
    for c in state.channels:
        c["sv"] = 0.0
        c["valveIn"] = False


async def _run_recipe():
    recipe = state.recipe
    procs = recipe.get("procs") or []
    bottle = recipe.get("bottle") or []
    use_h = bool(recipe.get("useHumidity", True))
    loop_count = int(recipe.get("loopCount") or 1) or 1
    total_steps = len(procs)

    state.system["stepTotal"] = total_steps
    state.system["loop"]["total"] = loop_count
    # 시작 시 진행 표시를 깨끗이 초기화(이전 실행 잔상 제거)
    state.system["stepIndex"] = 0
    state.system["phase"] = "idle"
    state.system["stepRemain"] = 0
    state.system["loop"]["current"] = 0

    # PLC 감시 기준: 시작 시점에 연결돼 있었는가.
    # PLC를 아예 안 쓰는 개발/시뮬 환경에서는 통신 감시를 하지 않는다(모듈 상태 없이 지역 변수로).
    plc_was_connected = bool((state.plc_live or {}).get("connected"))

    def _plc_abort_for(step_no: int):
        def check():
            live = state.plc_live or {}
            if (live.get("status") or {}).get("SAFETY_STOP") is True:
                return (f"P{step_no} 진행 중 PLC 안전정지 — 레시피를 중단합니다. "
                        f"이 측정은 무효입니다. 밸브를 모두 닫았습니다 — 복구 후 다시 열어야 합니다")
            if plc_was_connected and not live.get("connected"):
                return (f"P{step_no} 진행 중 PLC 통신 두절 — 레시피를 중단합니다. "
                        f"이 측정은 무효입니다. 밸브를 모두 닫았습니다 — 복구 후 다시 열어야 합니다")
            return None
        return check

    try:
        for loop_i in range(loop_count):
            state.system["loop"]["current"] = loop_i + 1
            for n, proc in enumerate(procs):
                res = compute_step_setpoints(state.channels, proc, bottle, use_h)
                if res["errors"]:
                    await push_log(f"P{n+1} 실행 불가로 중단: " + " / ".join(res["errors"]), "err")
                    return
                _apply_setpoints(res["sv"])
                state.system["stepIndex"] = n + 1
                await push_log(f"P{n+1} 시작 (Loop {loop_i+1}/{loop_count})", "ok")

                abort = _plc_abort_for(n + 1)
                # 준비(prep): 값 적용 후 안정화 대기
                await _phase("prep", float(proc.get("prep") or 0), abort)
                if not is_running_flag():
                    return
                # 측정(meas): 값 유지하며 시간 흐름.
                # ── 하드웨어 연결 시 여기에 RH/SMU 측정값 기록 코드 삽입 위치 ──
                #    (예: 주기적으로 센서값을 읽어 그래프/파일에 저장)
                await _phase("meas", float(proc.get("meas") or 0), abort)
                if not is_running_flag():
                    return
        await push_log("AUTO RUN 완료 — 레시피 종료", "ok")
    finally:
        # 정상 완료/중단 공통 마무리: 가스를 차단하고(STOP·완료 동일 규칙) 자동 진행 표시 해제.
        _all_close()
        state.system["running"] = False
        state.system["phase"] = "idle"
        state.system["stepIndex"] = 0
        state.system["stepRemain"] = 0
        await push_log("자동 실행 종료 — 가스 차단(모든 밸브·유량 닫음)", "info")
        await push_state()


def is_running_flag() -> bool:
    """state.system['running']이 외부(stop/비상정지)에서 False가 되면 진행 중단."""
    return bool(state.system.get("running"))


async def _phase(name: str, seconds: float, plc_abort=None):
    """name 구간을 seconds 동안 진행. 남은시간은 telemetry(5Hz)가 전달. running 꺼지면 즉시 반환.
    1초를 0.1초 단위로 쪼개 running을 자주 확인 → STOP 반영이 최대 0.1초로 빨라짐
    (AUTO STOP 직후 AUTO RUN을 눌러도 이전 태스크가 곧바로 끝나 재시작이 정상 동작).

    plc_abort: 중단 사유 문자열(없으면 None)을 돌려주는 콜백. 1초마다 확인한다.
    PLC 이상 중에 계속 진행하면 가스가 안 흐르는데 측정이 정상 완료된 것처럼 보인다."""
    state.system["phase"] = name
    # 4-way 자동 전환: 준비는 혼합가스를 vent로 흘려 안정화하고(센서엔 단독 에어만),
    #                 측정에 들어갈 때 혼합가스를 센서로 돌린다.
    if name == "prep":
        state.system["routeOut"] = "vent"
    elif name == "meas":
        state.system["routeOut"] = "sensor"
    remain = int(round(seconds))
    state.system["stepRemain"] = remain
    await push_state()          # 구간 시작만 즉시 알림(이후 카운트다운은 telemetry)
    ticks = 0
    while remain > 0:
        if not is_running_flag():
            return
        await asyncio.sleep(0.1)
        ticks += 1
        if ticks >= 10:               # 1초마다 남은시간 감소 + PLC 이상 확인
            ticks = 0
            remain -= 1
            state.system["stepRemain"] = remain   # telemetry가 이 값을 5Hz로 내려보냄
            if plc_abort is not None:
                reason = plc_abort()
                if reason:
                    # 복구 후 자동 재개를 막는다. 안전정지든 통신두절이든 사람이 확인하고 다시 열어야 한다.
                    # ★ sv만 0으로 만들면 valveIn이 True로 남아 통신 복구 시
                    #   밸브가 다시 열린다(안전정지는 loops의 전이 감지가 닫아주지만 통신두절은 아무도 안 닫는다).
                    _emergency_off()
                    state.system["running"] = False
                    await push_log(reason, "err")
                    await push_state()
                    return
    state.system["stepRemain"] = 0


def start() -> bool:
    """엔진 시작. 이미 실행 중이면 False(시작 안 함), 시작하면 True.
    호출 전에 precheck 통과를 보장할 것."""
    global _task
    if is_running():
        return False
    state.system["running"] = True
    state.system["safeStop"] = False
    state.system["purging"] = False   # 레시피가 배관을 인수 — 퍼지 래치 해제
    _task = asyncio.create_task(_run_recipe())
    return True


def stop():
    """자동 진행 중단(가스 차단은 _run_recipe finally가 수행). running=False로 두면
    _phase/_run_recipe가 빠져나온다."""
    state.system["running"] = False


def emergency():
    """비상정지: 진행 중단 + 모든 SV=0 + 모든 밸브 닫기."""
    state.system["running"] = False
    state.system["safeStop"] = True
    _emergency_off()
