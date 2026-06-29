"""
engine.py — 레시피 단계 진행 엔진(시뮬레이션 단계).
P1→P2→… 순서로: 계산→SV적용→준비(prep)대기→측정(meas)유지→다음. Loop Count 반복.
측정 하드웨어가 없으므로 측정 구간은 값 유지하며 시간만 흐른다.
"""

import asyncio

from state import state
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
        return ["레시피에 단계가 없음"]
    for n, proc in enumerate(procs):
        res = compute_step_setpoints(state.channels, proc, bottle, use_h)
        for e in res["errors"]:
            problems.append(f"P{n+1}: {e}")
    return problems


def _apply_setpoints(sv: dict):
    """계산된 SV를 채널에 적용. 4-way는 측정 방향(sensor)으로."""
    for i, c in enumerate(state.channels):
        c["sv"] = sv.get(i, 0.0)
    state.system["routeOut"] = "sensor"


def _all_off():
    for c in state.channels:
        c["sv"] = 0.0


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

                # 준비(prep): 값 적용 후 안정화 대기
                await _phase("prep", float(proc.get("prep") or 0))
                if not is_running_flag():
                    return
                # 측정(meas): 값 유지하며 시간 흐름(측정 하드웨어 붙으면 여기에 기록)
                await _phase("meas", float(proc.get("meas") or 0))
                if not is_running_flag():
                    return
        await push_log("AUTO RUN 완료 — 레시피 종료", "ok")
    finally:
        # 정상 완료/중단 공통 마무리: 자동 진행 표시 해제(유량은 유지 — STOP 규칙과 동일)
        state.system["running"] = False
        state.system["phase"] = "idle"
        state.system["stepIndex"] = 0
        state.system["stepRemain"] = 0
        await push_state()


def is_running_flag() -> bool:
    """state.system['running']이 외부(stop/비상정지)에서 False가 되면 진행 중단."""
    return bool(state.system.get("running"))


async def _phase(name: str, seconds: float):
    """name 구간을 seconds 동안 진행. 1초 단위로 남은시간 갱신+push. running이 꺼지면 즉시 반환."""
    state.system["phase"] = name
    remain = int(round(seconds))
    state.system["stepRemain"] = remain
    await push_state()
    while remain > 0:
        if not is_running_flag():
            return
        await asyncio.sleep(1.0)
        remain -= 1
        state.system["stepRemain"] = remain
    state.system["stepRemain"] = 0


def start():
    """엔진 시작(이미 돌고 있으면 무시). 호출 전에 precheck 통과를 보장할 것."""
    global _task
    if is_running():
        return
    state.system["running"] = True
    state.system["safeStop"] = False
    _task = asyncio.create_task(_run_recipe())


def stop():
    """자동 진행만 중단(유량/밸브 유지). running=False로 두면 _phase/_run_recipe가 빠져나온다."""
    state.system["running"] = False


def emergency():
    """비상정지: 진행 중단 + 모든 SV=0 + 모든 밸브 닫기."""
    state.system["running"] = False
    state.system["safeStop"] = True
    _emergency_off()
