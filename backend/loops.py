"""
loops.py — 백그라운드 주기 태스크 3종.

- telemetry_loop : 시뮬레이션 측정값을 초당 TELEMETRY_HZ회 브로드캐스트
- plc_poll_loop  : PLC 읽기(PV/상태) 폴링 → state.plc_live 갱신 후 push
- plc_write_loop : 앱의 '원하는 상태'(밸브/SV/4-way)를 블록 쓰기로 PLC에 반영

server.py의 lifespan에서 start_all()/stop_all()로만 쓴다.
★ server.py를 import하지 않는다(순환 import 방지). 필요한 값은 인자로 받는다.
"""

import asyncio
import contextlib
import time

import logger
import plc
import plc_catalog as cat
from state import state
from connection import manager, push_state, push_log

# ===================== 주기 설정 =====================
TELEMETRY_HZ = 5            # 측정값 전송 빈도(초당 횟수). 숫자만 바꾸면 조절된다.
PLC_POLL_INTERVAL_S = 0.7  # PLC 읽기(PV/상태) 폴링 주기(초).
PLC_WRITE_INTERVAL_S = 0.25  # PLC 쓰기(밸브/SV 반영) 동기화 주기(초).


def _build_telemetry(dt: float) -> dict:
    """실측 기반 telemetry. PV는 PLC 실측만 싣는다 — 없으면 None(화면 '—').
    경과시간 진행과 엔진 진행 상태 전달은 시뮬레이션이 아니라 실기능이라 여기 남는다."""
    if state.system["running"]:
        state._elapsed_f += dt
    elapsed = int(state._elapsed_f)
    state.system["elapsed"] = elapsed
    live_pv = ((state.plc_live or {}).get("pv") or {}) if state.plc_connected() else {}
    pv = []
    for c in state.channels:
        r = live_pv.get(c["id"])
        v = round(float(r), 2) if r is not None else None
        c["pv"] = v
        pv.append(v)
    return {
        "type": "telemetry", "pv": pv, "rh": None, "smu": None,
        "elapsed": elapsed, "running": state.system["running"],
        "loop": dict(state.system["loop"]),
        "phase": state.system.get("phase", "idle"),
        "stepIndex": state.system.get("stepIndex", 0),
        "stepTotal": state.system.get("stepTotal", 0),
        "stepRemain": state.system.get("stepRemain", 0),
    }


async def telemetry_loop():
    # ★ dt 는 '실측 경과'를 쓴다. 명목값(1/HZ)을 그대로 더하면 sleep 지연이 누적돼
    #   경과시간 표시가 벽시계보다 느려진다(Windows 타이머 해상도 15.6ms에서 특히).
    nominal = 1.0 / TELEMETRY_HZ
    last = time.monotonic()
    # 같은 실패가 초당 TELEMETRY_HZ회 반복될 수 있다 → 첫 1회만 남기고 이후는 개수만 센다.
    last_err, err_count = "", 0
    while True:
        await asyncio.sleep(nominal)
        now = time.monotonic()
        dt, last = now - last, now
        try:
            t = _build_telemetry(dt)
            await manager.broadcast(t)
            if err_count:
                logger.write("info", f"telemetry tick 복구 (억제된 반복 {err_count}회)")
                last_err, err_count = "", 0
        except Exception as e:  # noqa: BLE001
            msg = f"telemetry tick 실패: {e}"
            if msg != last_err:
                logger.write("warn", msg)
                last_err, err_count = msg, 0
            else:
                err_count += 1


# PLC 읽기 폴링: 연결돼 있으면 주기적으로 PV/상태를 읽어 state.plc_live 갱신 후 브로드캐스트.
# 연결/끊김 '전이'만 UI 로그에 한 번씩 남긴다(반복 도배 금지).
async def plc_poll_loop():
    was_connected = False
    prev_alm = {}
    fail_streak = 0   # 연속 읽기 실패 횟수 — plc.POLL_FAIL_LIMIT 부터 '끊김'으로 본다
    async def _mark_disconnected():
        nonlocal was_connected
        if was_connected:
            await push_log("PLC 연결 끊김", "warn")
        was_connected = False
        if state.plc_live.get("connected") or state.plc_live.get("pv"):
            state.plc_live = {"connected": False, "pv": {}, "pv_raw": {}, "status": {}}
            with contextlib.suppress(Exception):
                await push_state()
    while True:
        await asyncio.sleep(PLC_POLL_INTERVAL_S)
        try:
            if plc.plc.is_connected():
                res = await plc.plc.poll()
                state.plc_live = {"connected": True, "pv": res["pv"],
                                  "pv_raw": res.get("pv_raw", {}), "status": res["status"]}
                fail_streak = 0            # 성공 1회면 관용 카운터 초기화
                if not was_connected:
                    await push_log("PLC 연결됨", "ok")   # 끊김→연결 전이 1회
                    was_connected = True
                # ── 상태 전이 로그: 알람 4종 + 운전 허가 (표시등 변화를 로그로도 남긴다) ──
                st_now = (state.plc_live.get("status") or {})
                for _k, _label in (("ALM_MFC", "MFC 입력 이상"),
                                   ("ALM_IDD", "MFC 단선검출"), ("ALM_DAC", "아날로그 출력 모듈 이상")):
                    _cur = st_now.get(_k) is True
                    if _cur != prev_alm.get(_k, False):
                        await push_log(f"{_label} 알람 {'발생' if _cur else '해제'}",
                                       "warn" if _cur else "info")
                    prev_alm[_k] = _cur
                _rp = st_now.get("RUN_PERMIT") is True
                if _rp and not prev_alm.get("_rp", False):
                    await push_log("운전 허가 켜짐 — 수동 조작 가능", "ok")
                prev_alm["_rp"] = _rp
                await push_state()
            else:
                await _mark_disconnected()
        except Exception:  # noqa: BLE001 — 읽기 실패는 연속 N회부터 끊김으로 본다
            fail_streak += 1
            if fail_streak >= plc.POLL_FAIL_LIMIT:
                with contextlib.suppress(Exception):
                    await _mark_disconnected()
            else:
                # 단발 실패: 연결 표시·밸브 상태를 유지하고 다음 주기에 재시도한다.
                # (래더 하트비트가 계속 나가므로 3초 트립도 걸리지 않는다.)
                # ★ 파일 로그에만 남긴다 — 화면 System Log 에 띄우면 도배된다.
                logger.write("warn", f"PLC 읽기 실패 {fail_streak}/{plc.POLL_FAIL_LIMIT} — 재시도")


# PLC 쓰기 동기화: 앱의 '원하는 상태'(밸브 개폐 + 목표유량 SV)를 주기적으로 PLC에 반영.
# 변경분만 쓰고(last 캐시), 안전정지·미연결이면 절대 열림 명령을 내리지 않는다(fail-safe).
# 미연결/예외 시 캐시를 비워 재연결·복구 후 전량 재기입한다.
async def plc_write_loop():
    last = None   # (밸브 튜플, SV 튜플, 4way) — 통째로 비교해 바뀔 때만 전송
    prev_plc_safe = False   # PLC 안전정지의 직전 값(전이 감지용)
    prev_alarm = False      # MFC·DAC 알람의 직전 값(전이 감지용)
    prev_connected = False   # 연결의 직전 값(전이 감지) — 끊김 시 앱 상태도 닫힘으로 정렬
    while True:
        await asyncio.sleep(PLC_WRITE_INTERVAL_S)
        try:
            if not plc.plc.is_connected():
                if prev_connected:
                    # 연결됨→끊김 전이 1회: 래더는 3초 내 트립으로 실제 밸브를 닫는다.
                    # 앱 상태도 함께 닫아 화면 거짓 표시와 재연결 시 일괄 재개를 막는다
                    # (수동 재투입 원칙 — 트립 발생 전이 처리와 대칭).
                    state.close_all_channels()
                    state.system["purging"] = False
                    # 4-way 도 무전원 위치로 되돌린다 — 코일은 이미 OFF(gas→vent)인데
                    # routeOut 만 남아 있으면 화면이 실제와 다른 방향을 가리킨다.
                    # 복구 후 기본 방향도 vent 여야 한다(DEC-036).
                    state.system["routeOut"] = "vent"
                    await push_log("PLC 연결 끊김 — 밸브·유량 설정을 닫힘으로 정렬했습니다. "
                                   "재연결·리셋 후 다시 여세요", "warn")
                    with contextlib.suppress(Exception):
                        await push_state()
                prev_connected = False
                last = None
                continue
            prev_connected = True
            # 안전정지면 무조건 닫기(열림·유량 명령 금지). status는 읽기 폴링이 채운다.
            # PLC 안전정지와 파이썬 비상정지(system.safeStop) 둘 다 닫힘 조건이다.
            plc_safe = state.plc_safety_stop()
            safe = plc_safe or bool(state.system.get("safeStop"))
            alarm = state.alarm_lock()
            locked = safe or alarm     # 밸브·SV·4-way 게이트는 알람도 잠근다
            # ★ 전이 감지는 PLC의 SAFETY_STOP에만 반응한다.
            #   파이썬 비상정지는 engine._emergency_off가 이미 valveIn을 닫으므로 중복 처리 불필요.
            # ★ 안전정지 전이(False→True) 시점에 앱 상태(valveIn/sv)도 닫는다.
            #   valveIn을 그대로 두면 SAFETY_RESET으로 운전을 arm하는 순간 이전에 열려 있던
            #   밸브가 전부 자동으로 다시 열린다. PLC 래더는 수동 재투입을 의도했으므로
            #   파이썬이 그걸 무너뜨리면 안 된다. 전이 시점 1회만 — 매 주기면 조작이 막히고 로그가 도배된다.
            if plc_safe and not prev_plc_safe:
                state.close_all_channels()
                state.system["purging"] = False
                # 4-way 도 무전원 위치로 되돌린다 — 코일은 이미 OFF(gas→vent)인데
                # routeOut 만 남아 있으면 화면이 실제와 다른 방향을 가리킨다.
                # 복구 후 기본 방향도 vent 여야 한다(DEC-036).
                state.system["routeOut"] = "vent"
                await push_log("PLC 안전정지 감지 — 모든 밸브·유량을 닫았습니다. "
                               "복구 후 다시 열어야 합니다", "warn")
                await push_state()
            prev_plc_safe = plc_safe
            # 알람 전이(False→True): 안전정지와 같은 방식으로 전 채널을 닫는다.
            #   해제되어도 자동으로 다시 열지 않는다(수동 재투입 원칙).
            if alarm and not prev_alarm:
                state.close_all_channels()
                state.system["purging"] = False
                state.system["routeOut"] = "vent"
                await push_log(f"PLC 알람 활성({state.alarm_names()}) — 전 채널을 닫았습니다"
                               f"(해제 후에도 자동으로 다시 열리지 않습니다)", "err")
                await push_state()
            if (not alarm) and prev_alarm:
                await push_log("PLC 알람 해제 — 밸브는 닫힘 유지(수동으로 다시 여세요)", "info")
            prev_alarm = alarm
            valve_map, sv_map = {}, {}
            for ch in state.channels:
                p = state.plc_mapped(ch)
                if not p:
                    continue                          # 매핑 없는 채널은 제외
                cid = ch["id"]
                closed = locked or not ch.get("en")
                # 밸브: 카탈로그에 코일이 있으면 항상 포함.
                #   ★ en=False여도 반드시 넣어야 한다. 빼면 False가 안 써져서 열린 채로 남는다.
                if cat.valve_coil(cid) is not None:
                    valve_map[cid] = False if closed else bool(ch.get("valveIn"))
                # SV: sv_out이 배정된 채널만. 미배정이면 쓸 곳이 없다.
                if cat.dac_reg(p.get("sv_out")) is not None:
                    sv_map[cid] = 0.0 if closed else float(ch.get("sv") or 0)
            # 4-way: 앱의 측정 방향(routeOut=='sensor')을 반영. 안전정지면 닫기(False).
            # 극성은 config(plc_hw.v4w_on_is_sensor)로 뺐다 — 실기에서 반대로 밝혀져도
            # 코드 수정·재빌드 없이 현장에서 뒤집을 수 있다.
            # ★ 안전정지·알람 시에는 극성과 무관하게 코일을 끈다(무전원 = 안전 위치라는 가정).
            #   이 가정이 실제 밸브 배관과 맞는지는 실기에서 확인이 필요하다.
            on_is_sensor = bool(state.plc_hw.get("v4w_on_is_sensor", True))
            to_sensor = (state.system.get("routeOut") == "sensor")
            want_4w = (not locked) and (to_sensor if on_is_sensor else not to_sensor)
            want = (tuple(valve_map.items()), tuple(sv_map.items()), want_4w)
            if last != want:
                # 순서 중요: SV 먼저 → 밸브 나중.
                #   열 때는 유량이 먼저 서서 순간 과유량이 없고, 닫을 때는 SV가 먼저 0으로 떨어진다.
                await plc.plc.write_sv_block(sv_map)
                await plc.plc.write_valves_block(valve_map, want_4w)
                last = want
        except Exception:  # noqa: BLE001 — 쓰기 실패(연결문제 등)는 캐시 비우고 다음 주기 재시도
            last = None


def start_all() -> list:
    """세 루프를 태스크로 띄우고 리스트로 돌려준다(정지는 stop_all에 그대로 넘긴다)."""
    return [
        asyncio.create_task(telemetry_loop()),
        asyncio.create_task(plc_poll_loop()),
        asyncio.create_task(plc_write_loop()),
    ]


async def stop_all(tasks: list) -> None:
    """start_all이 돌려준 태스크를 취소하고 정리한다."""
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
