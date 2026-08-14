"""
engine.py — 레시피 단계 진행 엔진.
P1→P2→… 순서로: 계산→SV적용→준비(prep)대기→측정(meas)유지→다음. Loop Count 반복.
측정 하드웨어가 없으므로 측정 구간은 값 유지하며 시간만 흐른다.
진행이 끝나면(정상 완료·STOP·PLC 이상 중단) 항상 가스를 차단한다 — 유량을 남기지 않는다.
"""

import asyncio
import math
import time

import plc_catalog
from state import state, channel_role
from recipe_calc import compute_step_setpoints
from connection import push_state, push_log

_task = None

AIR_HANDOFF_S = 1.0
# 4-way 를 sensor 로 돌린 뒤 이 시간 동안 단독 에어를 유지하고 닫는다.
# 겹침이 없으면 전환 순간 센서가 잠깐 무풍이 되고, 계속 열어두면 측정 내내
# 에어가 vent 로 낭비된다(운용 결정 2026-08-13: 전환 1초 후 차단).


def _pure_dry_idx():
    """단독(pure) 마른공기 채널 인덱스 — 퍼지 단계가 열고, 가스 단계가 전환 후 닫는다."""
    return [i for i, c in enumerate(state.channels)
            if channel_role(c) == "dry_air" and c.get("en") and c.get("route") == "pure"]


def _close_pure_air() -> bool:
    """열려 있던 단독 에어를 닫는다. 실제로 닫은 게 있으면 True(로그·push 용)."""
    hit = False
    for i in _pure_dry_idx():
        c = state.channels[i]
        if c.get("valveIn") or c.get("sv"):
            c["sv"] = 0.0
            c["valveIn"] = False
            hit = True
    return hit


def _apply_purge_step(proc):
    """퍼지 단계: 혼합 라인(가스+혼합에어) 전부 닫고 단독 에어만 flow 로 연다."""
    flow = float(proc.get("flow") or 0)
    for i, c in enumerate(state.channels):
        if c.get("route") == "pure":
            continue
        c["sv"] = 0.0
        c["valveIn"] = False
    for i in _pure_dry_idx():
        c = state.channels[i]
        c["sv"] = min(flow, float(c.get("max") or 0))
        c["valveIn"] = True


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
        if proc.get("type") == "purge":
            # 퍼지 단계는 희석 계산 대상이 아니다 — 에어 유량·시간·대상 채널만 본다.
            pure = _pure_dry_idx()
            flow = float(proc.get("flow") or 0)
            if flow <= 0:
                problems.append(f"P{n + 1}(퍼지) — 에어 유량이 0입니다")
            # 퍼지 시간 = 준비(s) 단독(2026-08-14 계약 개정 — 합산 폐기).
            # 구파일이 측정에만 시간을 넣어둔 경우 여기서 막히고 문구가 옮길 곳을 안내한다.
            if float(proc.get("prep") or 0) <= 0:
                problems.append(
                    f"P{n + 1}(퍼지) 준비 시간이 0입니다 — 퍼지 시간은 준비(s)에 입력하세요")
            if not pure:
                problems.append(f"P{n + 1}(퍼지) — 단독 마른공기 채널(VA3)이 꺼져 있거나 없습니다")
            for i in pure:
                c = state.channels[i]
                mx = float(c.get("max") or 0)
                fs = float(((c.get("plc") or {}).get("fs_sccm")) or 0)
                lim = min(x for x in (mx, fs) if x > 0) if (mx > 0 or fs > 0) else 0
                if lim > 0 and flow > lim + 1e-6:
                    problems.append(
                        f"P{n + 1}(퍼지) — {c.get('id', '?')}: 에어 유량 {flow:g} sccm이 "
                        f"상한 {lim:g} sccm을 넘습니다")
                if plc_catalog.dac_reg((c.get("plc") or {}).get("sv_out")) is None:
                    problems.append(
                        f"P{n + 1}(퍼지) — {c.get('id', '?')}: SV 출력 채널이 없어 "
                        f"실행할 수 없습니다 (config.json의 channels[].plc.sv_out 확인)")
            continue
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
    ★ 단독(route=pure) 라인은 희석 '배분'에서 제외한다. 단, 단계 시퀀스는 별도로
      관리한다 — 퍼지 단계가 열고, 가스 단계는 4-way 전환 1초 뒤 닫는다(_close_pure_air).
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
    state.close_all_channels()
    # 진행이 끝나면 4-way도 안전 방향(vent)으로 되돌린다 — 다음 준비 단계의 기본 상태.
    state.system["routeOut"] = "vent"


def _emergency_off():
    state.close_all_channels()


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
    plc_was_connected = state.plc_connected()

    def _plc_abort_for(step_no: int):
        def check():
            # ★ 상태 판정은 전부 state 헬퍼를 쓴다 — plc_live dict 를 여기서 다시 파지 않는다.
            # ★ 로그는 사건과 조치만 적는다 — '이 측정은 무효' 같은 판단은 사람의 몫이다.
            if state.plc_safety_stop():
                return (f"P{step_no} 진행 중 PLC 안전정지 감지 — "
                        f"자동 실행을 중단하고 전 채널을 닫았습니다(자동 재개 없음)")
            if plc_was_connected and not state.plc_connected():
                return (f"P{step_no} 진행 중 PLC 통신 두절 — "
                        f"자동 실행을 중단하고 전 채널을 닫았습니다(자동 재개 없음)")
            # MFC·DAC 알람도 중단 사유다 — 가스가 안 나가거나 지령이 안 실리는 상태에서
            # 계속 진행하면 측정이 정상 완료된 것처럼 보인다(IDD 단선은 제외).
            if state.alarm_lock():
                return (f"P{step_no} 진행 중 PLC 알람 활성 — "
                        f"자동 실행을 중단하고 전 채널을 닫았습니다(자동 재개 없음)")
            return None
        return check

    try:
        for loop_i in range(loop_count):
            state.system["loop"]["current"] = loop_i + 1
            for n, proc in enumerate(procs):
                if proc.get("type") == "purge":
                    # 퍼지 단계: 희석 계산 없이 단독 에어만 연다(4-way 는 OFF 유지).
                    _apply_purge_step(proc)
                    state.system["stepIndex"] = n + 1
                    await push_log(f"P{n+1} 퍼지 시작 — 단독 에어 → Sensor "
                                   f"(Loop {loop_i+1}/{loop_count})", "ok")
                    dur = float(proc.get("prep") or 0)   # 퍼지 = 준비(s)만 (합산 폐기)
                    await _phase("purge", dur, _plc_abort_for(n + 1))
                    if not is_running_flag():
                        return
                    continue
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
                # ★ 에어 핸드오프: 4-way 를 sensor 로 돌린 뒤 AIR_HANDOFF_S 동안 단독 에어를
                #   겹쳐 두었다가 닫는다. 겹침이 없으면 전환 순간 센서가 무풍이 된다.
                #   ※ 가스 단계는 단독 에어를 '열지 않는다' — 직전 퍼지 단계(또는 사람이)
                #     열어둔 것을 전환 +1s 까지 유지할 뿐이다. 가스 단계를 연속으로 붙이면
                #     두 번째 준비 구간은 센서 무풍이 된다(막지 않음 — 계약으로 문서화).
                #   ※ 표시 특성: 겹침 구간이 끝나면 stepRemain 이 (meas−1)부터 다시 센다.
                dur = float(proc.get("meas") or 0)
                head = min(AIR_HANDOFF_S, dur)
                await _phase("meas", head, abort)      # 4-way → sensor + 겹침 구간
                if not is_running_flag():
                    return
                if dur > 0 and _close_pure_air():
                    await push_log("단독 에어 차단 — 4-way 전환 후 1초 경과", "info")
                    await push_state()
                await _phase("meas", dur - head, abort)
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
    """name 구간을 seconds 동안 진행 — 벽시계(monotonic) 데드라인 기준.
    이전의 'sleep 0.1 × 10 = 1초' 방식은 sleep 지연이 누적돼 Linux에서도 +0.5%,
    Windows(타이머 해상도 15.6ms)에서는 +5~8%까지 단계가 길어졌다.
    running이 꺼지면 ≤0.1초 안에 반환, plc_abort는 약 1초 간격으로 확인한다.

    plc_abort: 중단 사유 문자열(없으면 None)을 돌려주는 콜백.
    PLC 이상 중에 계속 진행하면 가스가 안 흐르는데 측정이 정상 완료된 것처럼 보인다."""
    state.system["phase"] = name
    # 4-way 자동 전환: 준비는 혼합가스를 vent로 흘려 안정화하고(센서엔 단독 에어만),
    #                 측정에 들어갈 때 혼합가스를 센서로 돌린다.
    if name == "prep":
        state.system["routeOut"] = "vent"
    elif name == "meas":
        state.system["routeOut"] = "sensor"
    elif name == "purge":
        # 퍼지는 코일 OFF(vent) 기본 위치 그대로 — 단독 에어가 이미 센서로 간다.
        state.system["routeOut"] = "vent"
    total = max(0.0, float(seconds or 0))
    end = time.monotonic() + total
    remain = int(round(total))
    state.system["stepRemain"] = remain
    await push_state()          # 구간 시작만 즉시 알림(이후 카운트다운은 telemetry)
    next_abort = time.monotonic() + 1.0
    while True:
        now = time.monotonic()
        left = end - now
        if left <= 0:
            break
        if not is_running_flag():
            return
        r = max(0, math.ceil(left - 1e-9))
        if r != remain:
            remain = r
            state.system["stepRemain"] = remain   # telemetry가 이 값을 5Hz로 내려보냄
        if plc_abort is not None and now >= next_abort:
            next_abort = now + 1.0
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
        await asyncio.sleep(min(0.1, left))
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
    """비상정지: 진행 중단 + 모든 SV=0 + 모든 밸브 닫기 + 4-way 무전원 위치."""
    state.system["running"] = False
    state.system["safeStop"] = True
    _emergency_off()
    # 4-way 도 무전원 위치로 — safeStop 동안 코일은 이미 OFF(gas→vent)인데
    # routeOut 이 sensor 로 남으면 '해제' 순간 코일이 자동 ON 으로 복귀한다
    # (밸브는 닫힌 채 유지되는 것과 비대칭 — 방향은 사람이 다시 고른다. DEC-036).
    # ★ _phase 의 PLC-이상 abort 는 _emergency_off() 만 부르므로 여기 영향을 받지 않는다
    #   (그 경로의 vent 복귀는 loops 의 전이 감지가 담당한다 — 중복 처리 금지).
    state.system["routeOut"] = "vent"
