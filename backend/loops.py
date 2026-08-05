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

import plc
import plc_catalog as cat
from state import state
from simulation import sim_tick
from connection import manager, push_state, push_log

# ===================== 주기 설정 =====================
TELEMETRY_HZ = 5            # 측정값 전송 빈도(초당 횟수). 숫자만 바꾸면 조절된다.
PLC_POLL_INTERVAL_S = 0.7  # PLC 읽기(PV/상태) 폴링 주기(초).
PLC_WRITE_INTERVAL_S = 0.25  # PLC 쓰기(밸브/SV 반영) 동기화 주기(초).


async def telemetry_loop():
    dt = 1.0 / TELEMETRY_HZ
    while True:
        await asyncio.sleep(dt)
        try:
            t = sim_tick(state, dt)
            await manager.broadcast(t)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] telemetry tick 실패: {e}")


# PLC 읽기 폴링: 연결돼 있으면 주기적으로 PV/상태를 읽어 state.plc_live 갱신 후 브로드캐스트.
# 연결/끊김 '전이'만 UI 로그에 한 번씩 남긴다(반복 도배 금지).
async def plc_poll_loop():
    was_connected = False
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
                if not was_connected:
                    await push_log("PLC 연결됨", "ok")   # 끊김→연결 전이 1회
                    was_connected = True
                await push_state()
            else:
                await _mark_disconnected()
        except Exception:  # noqa: BLE001 — 읽기 실패는 연결표시만 내리고 계속
            with contextlib.suppress(Exception):
                await _mark_disconnected()


# PLC 쓰기 동기화: 앱의 '원하는 상태'(밸브 개폐 + 목표유량 SV)를 주기적으로 PLC에 반영.
# 변경분만 쓰고(last 캐시), 안전정지·미연결이면 절대 열림 명령을 내리지 않는다(fail-safe).
# 미연결/예외 시 캐시를 비워 재연결·복구 후 전량 재기입한다.
async def plc_write_loop():
    last = None   # (밸브 튜플, SV 튜플, 4way) — 통째로 비교해 바뀔 때만 전송
    while True:
        await asyncio.sleep(PLC_WRITE_INTERVAL_S)
        try:
            if not plc.plc.is_connected():
                last = None
                continue
            # 안전정지면 무조건 닫기(열림·유량 명령 금지). status는 읽기 폴링이 채운다.
            safe = (state.plc_live.get("status") or {}).get("SAFETY_STOP") is True
            valve_map, sv_map = {}, {}
            for ch in state.channels:
                p = ch.get("plc")
                if not p:
                    continue                          # 매핑 없는 채널은 제외
                cid = ch["id"]
                closed = safe or not ch.get("en")
                # 밸브: 카탈로그에 코일이 있으면 항상 포함.
                #   ★ en=False여도 반드시 넣어야 한다. 빼면 False가 안 써져서 열린 채로 남는다.
                if cat.valve_coil(cid) is not None:
                    valve_map[cid] = False if closed else bool(ch.get("valveIn"))
                # SV: sv_out이 배정된 채널만. 미배정이면 쓸 곳이 없다.
                if cat.dac_reg(p.get("sv_out")) is not None:
                    sv_map[cid] = 0.0 if closed else float(ch.get("sv") or 0)
            # 4-way: 앱의 측정 방향(routeOut=='sensor')을 반영. 안전정지면 닫기(False).
            # TODO(하드웨어 확인): V4W 코일 ON=측정(sensor) 방향으로 가정 — 폴러리티는 실기로 검증.
            want_4w = (not safe) and (state.system.get("routeOut") == "sensor")
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
