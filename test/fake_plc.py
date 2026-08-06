"""
fake_plc.py — 가스센서 시스템용 가짜 PLC (Modbus TCP 슬레이브 + 안전 로직 에뮬레이터)

실기 CPU 없이 HMI의 통신·제어·안전 동작을 검증하기 위한 테스트 도구.
HMI가 Modbus '마스터'이므로, 붙을 '슬레이브'를 이 스크립트가 흉내 낸다.
주소맵은 HMI와 동일(base 0): 코일=M 비트, 홀딩 레지스터=D 워드.

가상 시리얼 드라이버가 필요 없는 TCP 방식(localhost). 서명/Secure Boot 문제와 무관.

설치:
    pip install "pymodbus==3.6.9"
실행(저장소 루트에서. 기본 127.0.0.1:502 에서 대기):
    python test/fake_plc.py
    (포트를 바꾸려면)  python test/fake_plc.py --port 5020
그리고 HMI는 TCP 모드로 host 127.0.0.1, port 502(또는 지정한 값), 국번 1로 연결.

콘솔 명령(실행 중 입력):
    air  → 공압 센서 있음/없음 토글(없으면 10초 뒤 운전허가 트립)
    mfc  → MFC 입력 이상 알람 토글
    idd  → MFC 입력 단선검출(ALM_IDD) 토글
    dac  → 아날로그 출력 모듈 이상(ALM_DAC) 토글
    s    → 현재 상태 한 줄 출력
    q    → 종료
"""

import argparse
import asyncio
import threading
import time

from pymodbus.datastore import (
    ModbusSlaveContext,
    ModbusServerContext,
    ModbusSequentialDataBlock,
)
from pymodbus.server import StartAsyncTcpServer

# ── 주소맵 (base 0 = M0000/D0000, HMI와 동일) ──────────────────────────────
CH_NAMES    = ["VA1", "VA2", "VA3", "VA4", "VA5", "VA6", "VA7", "VA8"]
CMD_COILS   = [160, 161, 162, 163, 164, 165, 166, 167]  # VA1~VA8 밸브 지령 (쓰기)
V4W_COIL    = 168                    # 4-way (쓰기)   ★164에서 이동
HB_COIL     = 176                    # HEARTBEAT (HMI가 토글, 쓰기)
RESET_COIL  = 178                    # SAFETY_RESET (M112 펄스, 쓰기)
AIR_OK      = 320                    # 공압 정상 (읽기)
SAFETY_STOP = 321                    # 안전정지 = NOT RUN_PERMIT (읽기)
RUN_PERMIT  = 323                    # 운전 허가 래치 (읽기) — 항상 NOT SAFETY_STOP
ALM_AIR     = 336                    # 공압 알람 (읽기)
ALM_MFC     = 337                    # MFC 입력 이상 알람 (읽기)
ALM_IDD     = 338                    # MFC 입력 단선검출 알람 (읽기)
ALM_DAC     = 339                    # 아날로그 출력 모듈 이상 알람 (읽기)
SV_REGS     = [100, 101, 102, 103, 104, 105, 106, 107]  # 목표유량 (쓰기: HMI→PLC)
PV_REGS     = [200, 201, 202, 203, 204, 205, 206, 207]  # 실측유량 (읽기: PLC→HMI)

NCH = len(CH_NAMES)

PRESS_TIMEOUT = 10.0   # 공압 상실 10초 → 트립 (PLC PRESS_TMR)
COMM_TIMEOUT  = 3.0    # 하트비트 두절 3초 → 트립 (PLC COMM_TMR)

# ★ SV와 PV는 풀스케일 카운트가 2배 다르다. 여기를 틀리면 HMI에 절반 유량으로 보인다.
#   SV(DV04A): 모듈은 0~10V/0~4000이지만 래더가 0~2000으로 클램프해 5V까지만 낸다.
#   PV(AD08A): 출력데이터타입 0~4000을 그대로 쓴다.
#   → 같은 유량이라도 PV 카운트 = SV 카운트 × 2. 수렴 목표를 이 비율로 잡는다.
SV_FULL_COUNT = 2000
PV_FULL_COUNT = 4000


class PlcSim:
    """PLC 래더의 안전 로직을 흉내 내는 상태 머신."""

    def __init__(self):
        # 코일 0~399 / 홀딩 0~299 — HMI의 블록 접근 범위를 모두 덮는다.
        #   read_coils(320, 20) → 320~339,  write_coils(160, 9) → 160~168
        #   read_holding_registers(200, 8) → 200~207,  write_registers(100, 8) → 100~107
        self.store = ModbusSlaveContext(
            co=ModbusSequentialDataBlock(0, [0] * 400),
            hr=ModbusSequentialDataBlock(0, [0] * 300),
            zero_mode=True,
        )
        self.ctx = ModbusServerContext(slaves=self.store, single=True)

        self.air_present = True     # 공압 센서(기본: 있음)
        self.mfc_alarm = False
        self.idd_alarm = False      # MFC 입력 단선검출
        self.dac_alarm = False      # 아날로그 출력 모듈 이상

        self.run_permit = False
        self.last_hb = None
        self.last_hb_time = time.time()
        self.air_lost_since = None
        self.prev_reset = 0
        self.pv = [0.0] * NCH

    def _co(self, addr):
        return self.store.getValues(1, addr, 1)[0]

    def _set_co(self, addr, v):
        self.store.setValues(1, addr, [1 if v else 0])

    def _hr(self, addr):
        return self.store.getValues(3, addr, 1)[0]

    def _set_hr(self, addr, v):
        self.store.setValues(3, addr, [int(v) & 0xFFFF])

    def step(self, dt):
        now = time.time()

        hb = self._co(HB_COIL)
        if self.last_hb is None:
            self.last_hb = hb
        if hb != self.last_hb:
            self.last_hb = hb
            self.last_hb_time = now
        comm_alive = (now - self.last_hb_time) < COMM_TIMEOUT

        if self.air_present:
            self.air_lost_since = None
        elif self.air_lost_since is None:
            self.air_lost_since = now
        air_tripped = (
            self.air_lost_since is not None
            and (now - self.air_lost_since) >= PRESS_TIMEOUT
        )

        reset = self._co(RESET_COIL)
        reset_edge = (reset == 1 and self.prev_reset == 0)
        self.prev_reset = reset
        if air_tripped or not comm_alive:
            self.run_permit = False
        if reset_edge and self.air_present and comm_alive:
            self.run_permit = True

        self._set_co(AIR_OK, self.air_present)
        self._set_co(SAFETY_STOP, not self.run_permit)
        self._set_co(RUN_PERMIT, self.run_permit)   # 래더와 동일: 항상 SAFETY_STOP의 반전
        self._set_co(ALM_AIR, not self.air_present)
        self._set_co(ALM_MFC, self.mfc_alarm)
        self._set_co(ALM_IDD, self.idd_alarm)
        self._set_co(ALM_DAC, self.dac_alarm)

        for i in range(NCH):
            valve_open = self._co(CMD_COILS[i]) == 1
            sv_raw = self._hr(SV_REGS[i]) if (self.run_permit and valve_open) else 0
            # ★ SV 풀스케일 2000 → PV 풀스케일 4000. 2배로 환산해 수렴시킨다.
            target = min(PV_FULL_COUNT, sv_raw * (PV_FULL_COUNT // SV_FULL_COUNT))
            self.pv[i] += (target - self.pv[i]) * min(1.0, dt * 3.0)
            self._set_hr(PV_REGS[i], round(self.pv[i]))

    def status_line(self):
        comm = (time.time() - self.last_hb_time) < COMM_TIMEOUT
        valves = "".join(str(self._co(a)) for a in CMD_COILS) + str(self._co(V4W_COIL))
        return (
            f"[PLC] 운전허가={'ON ' if self.run_permit else 'off'} "
            f"공압={'O' if self.air_present else 'X'} "
            f"통신={'O' if comm else 'X'} "
            f"MFC={'O' if self.mfc_alarm else 'x'} "
            f"단선={'O' if self.idd_alarm else 'x'} "
            f"DAC={'O' if self.dac_alarm else 'x'} | "
            f"밸브(VA1~8/4W)={valves} "
            f"SV={[self._hr(a) for a in SV_REGS]} "
            f"PV={[self._hr(a) for a in PV_REGS]}"
        )


sim = PlcSim()


async def logic_loop():
    dt = 0.1
    tick = 0
    while True:
        await asyncio.sleep(dt)
        sim.step(dt)
        tick += 1
        if tick % 20 == 0:
            print(sim.status_line())


def console_thread():
    print("명령: air(공압) / mfc(MFC알람) / idd(단선검출) / dac(DAC이상) / s(상태) / q(종료)")
    while True:
        try:
            cmd = input().strip().lower()
        except EOFError:
            return
        if cmd == "air":
            sim.air_present = not sim.air_present
            print(f"  공압 → {'있음' if sim.air_present else '없음(10초 뒤 트립)'}")
        elif cmd == "mfc":
            sim.mfc_alarm = not sim.mfc_alarm
            print(f"  MFC 입력 이상 알람 → {'ON' if sim.mfc_alarm else 'off'}")
        elif cmd == "idd":
            sim.idd_alarm = not sim.idd_alarm
            print(f"  MFC 입력 단선검출(ALM_IDD) → {'ON' if sim.idd_alarm else 'off'}")
        elif cmd == "dac":
            sim.dac_alarm = not sim.dac_alarm
            print(f"  아날로그 출력 모듈 이상(ALM_DAC) → {'ON' if sim.dac_alarm else 'off'}")
        elif cmd == "s":
            print("  " + sim.status_line())
        elif cmd in ("q", "quit", "exit"):
            import os
            os._exit(0)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="바인드 주소(기본 127.0.0.1=localhost)")
    ap.add_argument("--port", type=int, default=502, help="TCP 포트(기본 502)")
    args = ap.parse_args()

    threading.Thread(target=console_thread, daemon=True).start()
    asyncio.create_task(logic_loop())

    print(f"가짜 PLC 시작: TCP {args.host}:{args.port} (Modbus TCP 슬레이브, 국번 무관)")
    print("HMI를 TCP 모드로 이 host/port에 연결하세요. 공압 기본 '있음' → 리셋 누르면 arm 됩니다.")
    await StartAsyncTcpServer(context=sim.ctx, address=(args.host, args.port))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass