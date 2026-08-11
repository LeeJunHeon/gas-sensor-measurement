"""
test_plc.py — 가스센서 PLC 하드웨어 테스트 (터미널 메뉴, 단일 파일)

실제 PLC의 I/O를 하나씩 확인한다:
  · 코일(디지털) : 밸브 출력 ON/OFF, 상태 코일 읽기
  · ADC(아날로그 입력) : AD08A 채널값(PV 레지스터) 읽기
  · DAC(아날로그 출력) : DV04A 채널값(SV 레지스터) 쓰기
주소맵은 PLC 래더/HMI와 동일(base 0). 별도 UI 없이 VSCode 터미널에서 숫자 입력으로 조작.
주소 라벨은 실배선(P40~43=VA1·3·5·6) 기준 — CONFIG.md 참조.

설치:
    pip install "pymodbus==3.6.9" pyserial
실행(저장소 루트에서. 실제 PLC — 시리얼):
    python test/test_plc.py --port COM3       (PLC가 연결된 COM 포트, 115200 8N1, 국번 1)
실행(가짜 PLC 검증 — TCP):
    python test/test_plc.py --tcp 127.0.0.1:502
실행(대화형 — VSCode에서 인자 없이 실행):
    python test/test_plc.py       (연결 방식·포트·통신 속도·국번을 순서대로 물어본다)

주의:
  ⚠️ 이 도구는 실제 PLC 출력을 구동합니다. 밸브/DAC 출력은 '운전허가(arm)'가 켜져야 실제로 동작합니다
     (메뉴 4에서 arm). 가스/공압이 연결된 환경이면 안전에 각별히 유의하세요.
  · 통신 하트비트(HEARTBEAT 코일)는 백그라운드에서 자동 토글되어 PLC 통신두절 트립을 막습니다.
"""

import argparse
import threading
import time

from pymodbus.client import ModbusSerialClient, ModbusTcpClient

# ── 주소맵 (base 0 = M0000/D0000) ─────────────────────────────────────────
COILS_W = [("VA1 (에어1)", 160), ("VA2 (물1·미배선)", 161), ("VA3 (에어2)", 162),
           ("VA4 (물2·미배선)", 163), ("VA5 (가스1)", 164), ("VA6 (가스2)", 165),
           ("VA7 (가스3·미배선)", 166), ("VA8 (가스4·미배선)", 167),
           ("4WAY (배선 미확인)", 168)]                    # 쓰기: 밸브 출력 지령
HB_COIL, RESET_COIL = 176, 178                            # 하트비트 / 안전리셋(펄스)
# 320 (M00200) · 336 (M00210) — 공압 인터록 제거로 미사용. 복원 시 재사용 예약.
STATUS = [("SAFETY_STOP(안전정지)", 321),
          ("RUN_PERMIT (운전허가)", 323),
          ("ALM_MFC (MFC입력이상)", 337),
          ("ALM_IDD (입력단선검출)", 338), ("ALM_DAC (출력모듈이상)", 339)]  # 읽기: 상태
SV_REGS = [("VA1 SV (에어1)", 100), ("VA3 SV (에어2)", 101),
           ("VA5 SV (가스1)", 102), ("VA6 SV (가스2)", 103)]   # DAC1 CH0~3 = 실배선 4대
PV_REGS = [("VA1 PV (에어1)", 200), ("VA3 PV (에어2)", 202),
           ("VA5 PV (가스1)", 204), ("VA6 PV (가스2)", 205)]   # ADC CH0·2·4·5 = 실배선 4대
# ★ PV 주소는 실배선 때문에 비연속(200·202·204·205)이다. 블록 읽기는 min~max 범위를
#   한 번에 읽고 (주소 - base) 오프셋으로 꺼내야 한다 — 순서대로 zip하면 값이 어긋난다.
PV_BASE = PV_REGS[0][1]
PV_SPAN = PV_REGS[-1][1] - PV_BASE + 1

SV_MAX_COUNT = 2000   # DV04A를 래더가 0~2000(0~5V)으로 클램프
PV_MAX_COUNT = 4000   # AD08A 출력데이터타입 0~4000

DEAD_AFTER = 5        # 연속 실패 이 횟수부터 '응답 없음'으로 보고 상태읽기를 건너뛴다


class PLC:
    """PLC와의 Modbus 통신 래퍼 (스레드 안전)."""

    def __init__(self, client, unit):
        self.cli = client
        self.unit = unit
        self.lock = threading.Lock()
        self._hb_val = False
        self._running = True
        self.offline = False    # 접속 직후 응답 확인에 실패했는가
        self.fails = 0          # 연속 실패 횟수 (성공 1회면 리셋)

    def connect(self):
        return self.cli.connect()

    def _err(self, r):
        return (r is None) or (hasattr(r, "isError") and r.isError())

    def _mark(self, ok):
        """통신 성패를 연속 실패 카운터에 반영한다."""
        self.fails = 0 if ok else self.fails + 1
        return ok

    @property
    def dead(self):
        """연속 실패가 임계치를 넘어 '무응답'으로 판단되는 상태."""
        return self.fails >= DEAD_AFTER

    def read_coil(self, addr):
        with self.lock:
            r = self.cli.read_coils(addr, 1, slave=self.unit)
        if not self._mark(not self._err(r)):
            return None
        return bool(r.bits[0])

    def write_coil(self, addr, value):
        with self.lock:
            r = self.cli.write_coil(addr, bool(value), slave=self.unit)
        return self._mark(not self._err(r))

    def read_reg(self, addr):
        with self.lock:
            r = self.cli.read_holding_registers(addr, 1, slave=self.unit)
        if not self._mark(not self._err(r)):
            return None
        return r.registers[0]

    def read_regs(self, addr, count):
        """홀딩 레지스터 블록 읽기(FC03 1회). 실패하면 None."""
        with self.lock:
            r = self.cli.read_holding_registers(addr, count, slave=self.unit)
        if not self._mark(not self._err(r)):
            return None
        return list(r.registers)

    def write_reg(self, addr, value):
        with self.lock:
            r = self.cli.write_register(addr, int(value), slave=self.unit)
        return self._mark(not self._err(r))

    def heartbeat_loop(self):
        """HEARTBEAT 코일을 1초마다 토글 → PLC 통신 감시(3초) 유지.
        무응답이 이어지면 주기를 2초로 늘려 락 경합을 줄인다(성공하면 원복)."""
        while self._running:
            self._hb_val = not self._hb_val
            try:
                self.write_coil(HB_COIL, self._hb_val)
            except Exception:
                pass
            time.sleep(2.0 if self.dead else 1.0)

    def close(self):
        self._running = False
        try:
            self.cli.close()
        except Exception:
            pass


def ask(prompt=""):
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return "0"


def ask_int(prompt, default):
    """정수를 물어본다. 빈 입력이면 기본값, 잘못된 입력이면 다시 묻는다."""
    while True:
        s = ask(prompt)
        if not s:
            return default
        try:
            return int(s)
        except ValueError:
            print("   숫자를 입력하세요.")


def ask_connection(args):
    """--port/--tcp 가 모두 없을 때 접속 파라미터를 대화형으로 받는다."""
    while True:
        mode = ask("연결 방식 [1] 시리얼(실제 PLC) / [2] TCP(가짜 PLC) (기본 1) > ") or "1"
        if mode in ("1", "2"):
            break
        if mode == "0":     # ask()는 EOF/Ctrl+C에서 "0"을 준다 — 무한 루프 방지
            raise SystemExit("취소되었습니다.")
        print("   1 또는 2를 입력하세요.")
    if mode == "2":
        args.tcp = ask("주소 (기본 127.0.0.1:502) > ") or "127.0.0.1:502"
    else:
        while True:
            args.port = ask("COM 포트 (예: COM3) > ")
            if args.port:
                break
            print("   포트를 입력하세요.")
        args.baud = ask_int("통신 속도 (기본 115200) > ", 115200)
        print("   ※ XG5000 → 파라미터 → Cnet 설정과 같아야 합니다(공장 기본 9600)")
    args.unit = ask_int("국번 (기본 1) > ", 1)
    return args


def fmt(v, t="ON", f="OFF"):
    return "?" if v is None else (t if v else f)


def status_bar(plc):
    if plc.dead:
        # 무응답 상태에서는 항목마다 타임아웃(1초)을 물지 않고 즉시 메뉴를 띄운다.
        print("=" * 72)
        print(" ※ 실기 연결 시 주의 — 밸브가 실제로 열립니다")
        print(" 운전허가:-- (응답 없음)   안전정지:--   MFC:--  단선:--  DAC:--   통신HB:자동")
        print(" (PLC 응답이 없습니다 — 전원·배선·속도·국번 확인. 응답이 돌아오면 자동 복구됩니다)")
        print("=" * 72)
        return
    stop = plc.read_coil(321)   # SAFETY_STOP
    almm = plc.read_coil(337)
    idd = plc.read_coil(338)
    dac = plc.read_coil(339)
    run = "?" if stop is None else ("OFF (정지)" if stop else "ON")
    print("=" * 72)
    print(" ※ 실기 연결 시 주의 — 밸브가 실제로 열립니다")
    print(f" 운전허가:{run}   안전정지:{fmt(stop,'ON','x')}   "
          f"MFC:{fmt(almm,'O','x')}  단선:{fmt(idd,'O','x')}  DAC:{fmt(dac,'O','x')}   통신HB:자동")
    print("=" * 72)


# ── 서브 메뉴들 ────────────────────────────────────────────────────────────
def menu_coils(plc):
    while True:
        status_bar(plc)
        print(" [쓰기 · 밸브 출력]   (운전허가 ON이어야 실제 밸브가 동작)")
        for i, (name, addr) in enumerate(COILS_W, 1):
            v = plc.read_coil(addr)
            print(f"   {i}) {name:<12} [{fmt(v)}]   (coil {addr})")
        print(" [읽기 · 상태 코일]")
        print("   s) 상태 코일 5종 읽기 (안전정지/운전허가/알람3)")
        print("   0) 뒤로")
        s = ask("선택 > ")
        if s == "0":
            return
        if s == "s":
            print()
            for name, addr in STATUS:
                v = plc.read_coil(addr)
                print(f"     {name:<22} = {fmt(v)}   (coil {addr})")
            ask("\nEnter로 계속 > ")
        elif s in [str(i) for i in range(1, len(COILS_W) + 1)]:
            name, addr = COILS_W[int(s) - 1]
            cur = plc.read_coil(addr)
            nv = not (cur is True)
            ok = plc.write_coil(addr, nv)
            print(f"\n   → {name} 코일 {addr} = {fmt(nv)}  ({'성공' if ok else '실패'})")
            if nv and plc.read_coil(321) is True:
                print("   ⚠️ 지금 안전정지(운전허가 OFF)라, 코일은 켜졌어도 실제 밸브는 안 열립니다.")
                print("      메뉴 4에서 리셋(arm) 후 다시 시도하세요.")
            ask("\nEnter로 계속 > ")


def menu_adc(plc):
    while True:
        status_bar(plc)
        print(f" ADC (AD08A 아날로그 입력 = PV 레지스터, 원시 카운트 0~{PV_MAX_COUNT})")
        print("   ※ sccm 환산은 하지 않는다 — MFC 출력 사양(0~5V/4~20mA) 진단에 raw가 필요하다")
        for i, (name, addr) in enumerate(PV_REGS, 1):
            print(f"   {i}) {name} (D{addr})")
        print("   d) 배선 4채널 일괄 덤프 (D200~205 블록 1회 읽기, raw)")
        print("   m) 배선 4채널 연속 모니터 (Ctrl+C로 중단, raw)")
        print("   0) 뒤로")
        s = ask("선택 > ")
        if s == "0":
            return
        if s in [str(i) for i in range(1, len(PV_REGS) + 1)]:
            name, addr = PV_REGS[int(s) - 1]
            v = plc.read_reg(addr)
            print(f"\n   {name} (D{addr}) = {v if v is not None else '읽기 실패'} (raw)")
            ask("\nEnter로 계속 > ")
        elif s == "d":
            vals = plc.read_regs(PV_BASE, PV_SPAN)
            print()
            if vals is None:
                print("   읽기 실패")
            else:
                for name, addr in PV_REGS:
                    print(f"     {name} (D{addr}) = {vals[addr - PV_BASE]:>5}  (raw)")
            ask("\nEnter로 계속 > ")
        elif s == "m":
            print("\n   연속 모니터 raw (Ctrl+C로 중단)")
            try:
                while True:
                    vals = plc.read_regs(PV_BASE, PV_SPAN)
                    if vals is None:
                        print("   읽기 실패")
                    else:
                        print("   " + "  ".join(f"{n.split()[0]}={vals[a - PV_BASE]}"
                                                for n, a in PV_REGS))
                    time.sleep(0.5)
            except KeyboardInterrupt:
                print("\n   (중단)")


def menu_dac(plc):
    while True:
        status_bar(plc)
        print(f" DAC (DV04A 아날로그 출력 = SV 레지스터, 카운트 0~{SV_MAX_COUNT})")
        print("   ※ 입력은 카운트 그대로 — sccm 환산은 HMI가 한다. 이건 저수준 점검 도구다.")
        print(f"   (운전허가 ON이어야 실제 전압이 나옵니다. 스케일: {SV_MAX_COUNT}=5V → 값/{SV_MAX_COUNT}×5V)")
        for i, (name, addr) in enumerate(SV_REGS, 1):
            cur = plc.read_reg(addr)
            print(f"   {i}) {name} (D{addr})   현재={cur if cur is not None else '?'}")
        print("   0) 뒤로")
        s = ask("선택 > ")
        if s == "0":
            return
        if s in [str(i) for i in range(1, len(SV_REGS) + 1)]:
            name, addr = SV_REGS[int(s) - 1]
            raw = ask(f"   {name} 카운트 입력 (0~{SV_MAX_COUNT}) > ")
            try:
                val = int(raw)
            except ValueError:
                print("   숫자를 입력하세요.")
                ask("\nEnter로 계속 > ")
                continue
            val = max(0, min(SV_MAX_COUNT, val))
            ok = plc.write_reg(addr, val)
            volt = val / SV_MAX_COUNT * 5.0
            print(f"\n   → {name} (D{addr}) = {val} 씀  ({'성공' if ok else '실패'})")
            print(f"      DV04A 해당 채널 출력 ≈ {volt:.2f} V (멀티미터로 확인)")
            if plc.read_coil(321) is True:
                print("   ⚠️ 지금 안전정지라 PLC가 출력을 0으로 막습니다. 메뉴 4에서 arm 후 확인하세요.")
            ask("\nEnter로 계속 > ")


def menu_safety(plc):
    while True:
        status_bar(plc)
        print(" 안전 / 운전허가")
        print("   1) 리셋(arm) — SAFETY_RESET 펄스 (통신 정상이면 운전허가 ON)")
        print("   2) 상태 다시 읽기")
        print("   0) 뒤로")
        s = ask("선택 > ")
        if s == "0":
            return
        if s == "1":
            plc.write_coil(RESET_COIL, True)
            time.sleep(0.3)
            plc.write_coil(RESET_COIL, False)
            time.sleep(0.3)
            stop = plc.read_coil(321)
            if stop is False:
                print("\n   → 운전허가 ON (arm 성공)")
            else:
                print("\n   → 아직 운전허가 OFF. 통신(하트비트) 상태를 확인하세요.")
            ask("\nEnter로 계속 > ")


def menu_snapshot(plc):
    status_bar(plc)
    print(" [상태 코일]")
    for name, addr in STATUS:
        print(f"   {name:<22} = {fmt(plc.read_coil(addr))}")
    print(" [밸브 출력 코일]")
    for name, addr in COILS_W:
        print(f"   {name:<12} = {fmt(plc.read_coil(addr))}")
    pv = plc.read_regs(PV_BASE, PV_SPAN)
    sv = plc.read_regs(SV_REGS[0][1], len(SV_REGS)) or ["?"] * len(SV_REGS)
    print(" [PV (ADC, D200·202·204·205, raw 카운트)]")
    print("   " + "  ".join(f"{n.split()[0]}={pv[a - PV_BASE] if pv else '?'}"
                            for n, a in PV_REGS))
    print(" [SV (DAC, D100~103, raw 카운트)]")
    print("   " + "  ".join(f"{n.split()[0]}={v}" for (n, _), v in zip(SV_REGS, sv)))
    ask("\nEnter로 계속 > ")


def main_menu(plc):
    while True:
        status_bar(plc)
        print(" 1) 코일(디지털)      — 밸브 출력 ON/OFF · 상태 코일 읽기")
        print(" 2) ADC(아날로그 입력) — AD08A 채널값(PV) 읽기")
        print(" 3) DAC(아날로그 출력) — DV04A 채널값(SV) 쓰기")
        print(" 4) 안전 / 운전허가    — 리셋(arm) · 상태")
        print(" 5) 전체 상태 스냅샷")
        print(" 0) 종료")
        s = ask("선택 > ")
        if s == "1":
            menu_coils(plc)
        elif s == "2":
            menu_adc(plc)
        elif s == "3":
            menu_dac(plc)
        elif s == "4":
            menu_safety(plc)
        elif s == "5":
            menu_snapshot(plc)
        elif s == "0":
            return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", help="시리얼 포트 (예: COM3, /dev/ttyUSB0) — 실제 PLC")
    ap.add_argument("--tcp", help="TCP 주소 host:port (예: 127.0.0.1:502) — 가짜 PLC 검증용")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--unit", type=int, default=1, help="국번(기본 1)")
    args = ap.parse_args()

    if not args.port and not args.tcp:
        args = ask_connection(args)

    if args.tcp:
        host, _, p = args.tcp.partition(":")
        client = ModbusTcpClient(host, port=int(p or 502), timeout=1.0)
        where = f"TCP {args.tcp}"
    else:
        if not args.port:
            args.port = ask("PLC 시리얼 포트 입력 (예: COM3) > ")
        client = ModbusSerialClient(port=args.port, baudrate=args.baud,
                                    bytesize=8, parity="N", stopbits=1, timeout=1.0)
        where = f"시리얼 {args.port} @ {args.baud} 8N1"

    plc = PLC(client, args.unit)
    print(f"\nPLC 연결 시도: {where}, 국번 {args.unit} ...")
    if not plc.connect():
        print("연결 실패 — 포트/전원/배선을 확인하세요.")
        return
    print("연결 성공.")
    if plc.read_coil(320) is None:      # 포트만 열리고 PLC가 응답하지 않는 경우
        plc.offline = True
        plc.fails = DEAD_AFTER   # 항목마다 1초씩 기다리지 않고 곧바로 메뉴를 띄운다
        print("⚠️ 포트는 열렸지만 PLC 응답이 없습니다 — 전원·배선·속도·국번을 확인하세요.")
        print("   (메뉴는 계속 사용할 수 있으나 값은 -- 로 표시됩니다)")
    print()
    print("⚠️ 이 도구는 실제 PLC 출력을 구동합니다. 밸브/DAC는 운전허가(메뉴4 arm)가 켜져야 동작합니다.")
    print("   가스/공압이 연결된 환경이면 안전에 유의하세요.\n")

    threading.Thread(target=plc.heartbeat_loop, daemon=True).start()
    try:
        main_menu(plc)
    finally:
        plc.close()
        print("종료.")


if __name__ == "__main__":
    main()