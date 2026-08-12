"""
plc.py — LS XGB 내장 Cnet Modbus RTU 서버와의 통신(최소 골격).

확정 사실(하드웨어):
  - RS232, 8N1, 기본 115200bps, 국번(Unit ID)=1.
  - Modbus base = M0000/D0000 → 접근은 전부
      코일: 읽기 FC01 / 쓰기 FC05·15,
      홀딩 레지스터: 읽기 FC03 / 쓰기 FC06·16.  (FC02/FC04 안 씀)

구조는 챔버 프로젝트 device/plc.py 참고(비동기 직렬화·재연결·하트비트·unit/slave 키 자동판별).
단 여기선 시리얼(RTU)이므로 ModbusTcpClient → ModbusSerialClient 로 바꿨다.

동작 요약:
  - 동기 pymodbus 클라이언트를 asyncio.to_thread로 감싸고, asyncio.Lock으로 요청을 직렬화한다.
  - 요청 사이에 inter_cmd_gap_s 만큼 간격을 둔다(Cnet 서버 안정성).
  - port가 비어 있으면 연결을 시도하지 않는다(설정 전 무해). port가 있으면 start()로
    연결 유지 루프(연결→하트비트→끊기면 reconnect_delay_s 후 재연결)를 돈다.

주소맵(레시피/밸브/센서 ↔ M/D 비트·워드)은 하드웨어 확정 후 아래 TODO에 채운다.
"""

import asyncio
import inspect
from dataclasses import dataclass, asdict, fields

import plc_catalog as cat
# ★ 상태 싱글턴이 아니라 '배선 판정' 순수 함수만 가져온다(계층 역전 방지·순환 없음).
from state import plc_mapped

try:
    from pymodbus.client import ModbusSerialClient
except Exception:  # noqa: BLE001 — pymodbus 미설치 환경에서도 앱은 떠야 함
    ModbusSerialClient = None

try:
    from pymodbus.client import ModbusTcpClient
except Exception:  # noqa: BLE001
    ModbusTcpClient = None

try:
    from serial.tools import list_ports as _list_ports
except Exception:  # noqa: BLE001
    _list_ports = None


# ===================== 연결 설정 =====================
@dataclass
class PlcConfig:
    # --- 전송 방식 ---
    mode: str = "serial"       # "serial"(RTU) | "tcp"
    host: str = "127.0.0.1"    # tcp 호스트
    tcp_port: int = 502        # tcp 포트(1~65535)
    # --- 시리얼(RTU) ---
    port: str = ""              # 예: "COM3"(Windows), "/dev/ttyUSB0"(Linux). 비면 연결 안 함.
    baudrate: int = 115200
    bytesize: int = 8
    stopbits: int = 1
    parity: str = "N"          # "N" | "E" | "O"
    unit_id: int = 1           # 국번(1~247). 0 금지.
    timeout_s: float = 1.5
    inter_cmd_gap_s: float = 0.1
    heartbeat_s: float = 1.0
    reconnect_delay_s: float = 1.0


def config_from_dict(d: dict) -> PlcConfig:
    """dict(state.plc)에서 PlcConfig 생성. 알 수 없는 키는 무시, 타입은 안전 변환."""
    d = d or {}
    valid = {f.name for f in fields(PlcConfig)}
    out = PlcConfig()
    for k in valid:
        if k not in d or d[k] is None:
            continue
        cur = getattr(out, k)
        try:
            if isinstance(cur, bool):
                setattr(out, k, bool(d[k]))
            elif isinstance(cur, int):
                setattr(out, k, int(d[k]))
            elif isinstance(cur, float):
                setattr(out, k, float(d[k]))
            else:
                setattr(out, k, str(d[k]))
        except (TypeError, ValueError):
            pass
    # 방어적 보정(프론트 검증과 별개로 파일에 잘못된 값이 있어도 안전하게)
    out.unit_id = min(247, max(1, out.unit_id))
    if out.parity not in ("N", "E", "O"):
        out.parity = "N"
    if out.mode not in ("serial", "tcp"):
        out.mode = "serial"
    out.tcp_port = min(65535, max(1, out.tcp_port))
    # 방어적 클램프 — config.json 손편집으로도 통신 붕괴 값이 들어오지 못하게 한다.
    #   heartbeat_s: PLC COMM_TMR(3초)보다 길면 연결하자마자 반복 트립된다.
    #   gap/timeout: 요청 1건이 락을 (timeout+gap) 만큼 잡는다 — 크면 하트비트가
    #   밀려 같은 증상. 프론트 검증(recipe.js collectSetup)과 이중 방어이며,
    #   commands.py apply_setup 도 저장 전에 같은 값으로 클램프한다(범위 동기 유지).
    out.heartbeat_s = min(2.5, max(0.1, out.heartbeat_s))
    out.inter_cmd_gap_s = min(1.0, max(0.0, out.inter_cmd_gap_s))
    out.timeout_s = min(2.5, max(0.1, out.timeout_s))
    out.reconnect_delay_s = min(60.0, max(0.1, out.reconnect_delay_s))
    return out


def _s16(v: int) -> int:
    """Modbus 레지스터(무부호 16비트) → 부호 있는 값.
    AD08A는 개방/잡음에서 음수(-48 등)를 내며, 무부호로 읽으면 65488이 되어
    스케일 변환에서 16372 sccm 같은 허수 유량이 표시된다."""
    v = int(v) & 0xFFFF
    return v - 0x10000 if v > 0x7FFF else v


# ===================== Modbus 클라이언트(최소 골격) =====================
class PlcClient:
    """동기 pymodbus 클라이언트를 감싼 비동기 직렬화 래퍼. 실제 IO는 read_*/write_* 로."""

    def __init__(self):
        self.cfg = PlcConfig()
        self._client = None
        self._lock = asyncio.Lock()       # 요청 직렬화(한 번에 하나)
        self._unit_key = None             # 'device_id' | 'slave' | 'unit' (버전별 자동판별)
        self._task = None                 # 연결 유지 루프 태스크
        self._connected = False
        self._hb_value = False            # 하트비트 토글 상태(매 주기 반전 → PLC가 엣지로 생존 판단)
        self._last_error = ""             # 마지막 연결 실패 원인(diagnose_connection이 인용)
        # config 주도 주소맵(load_addresses로 채움). 비어있으면 하드코딩 fallback 사용.
        self._valve_coil = {}             # {채널id: cmd_coil}
        self._sv_reg = {}                 # {채널id: sv_reg}
        self._pv_reg = {}                 # {채널id: pv_reg}
        self._scale = {}                  # {채널id: plc dict 전체(스케일 포함)}
        self._enabled = {}                # {채널id: 사용여부(en)} — 폴링 대상 선별용
        self._sys = {}                    # 시스템 공통 주소(plc_system)

    # ---- 설정 ----
    def set_config(self, cfg: PlcConfig):
        self.cfg = cfg

    def load_addresses(self, channels: list, plc_system: dict):
        """config(state.channels/state.plc_system)에서 주소맵을 로드한다.
        주소는 plc_catalog가 결정한다 — 밸브는 채널 id로, SV/PV는 배정된 채널 '이름'으로.
        배정이 없거나(None) 이름을 모르면 그 채널은 해당 맵에서 빠진다(쓸 곳이 없으므로)."""
        chans = channels or []
        # ★ '배선됨' 판정은 state.plc_mapped 하나로 — 여기(_valve_coil 소속을 정하는 곳)와
        #   write 루프·exit 차단 쓰기가 갈라지면 없는 키를 찾다 KeyError 가 난다(전례 있음).
        mapped = [(ch, plc_mapped(ch)) for ch in chans]
        self._valve_coil = {ch["id"]: cat.valve_coil(ch["id"]) for ch, p in mapped
                            if p and cat.valve_coil(ch["id"]) is not None}
        self._sv_reg = {ch["id"]: cat.dac_reg(p.get("sv_out")) for ch, p in mapped
                        if p and cat.dac_reg(p.get("sv_out")) is not None}
        self._pv_reg = {ch["id"]: cat.adc_reg(p.get("pv_in")) for ch, p in mapped
                        if p and cat.adc_reg(p.get("pv_in")) is not None}
        self._scale      = {ch["id"]: dict(p)            for ch, p in mapped if p}
        self._enabled    = {ch["id"]: bool(ch.get("en")) for ch, p in mapped if p}
        self._sys = dict(plc_system or {})

    # ---- 주소 resolver(내부 맵 우선, 없으면 하드코딩 fallback) ----
    def _sys_addr(self, key: str) -> int:
        # 키 단위로 판단한다. _sys가 실려 있어도 새로 추가된 키가 없을 수 있고,
        # 그때 KeyError가 나면 read_status가 통째로 실패해 '미연결'로 오표시된다.
        if self._sys and key in self._sys:
            return self._sys[key]
        if key in _FALLBACK_SYS:
            return _FALLBACK_SYS[key]
        raise KeyError(f"PLC 시스템 주소 '{key}' 미정의 — config.json의 plc_system 확인 필요")

    def _valve_coil_of(self, name: str) -> int:
        return self._valve_coil[name] if self._valve_coil else PLC_COIL_MAP[f"{name}_CMD"]

    def _sv_reg_of(self, name: str) -> int:
        return self._sv_reg[name] if self._sv_reg else PLC_REG_MAP[f"SV_{name}"]

    def _pv_reg_items(self):
        if self._pv_reg:
            return list(self._pv_reg.items())
        return [(n, PLC_REG_MAP[f"PV_{n}"])
                for n in ("VA1", "VA2", "VA3", "VA4", "VA5", "VA6", "VA7", "VA8")]

    def is_connected(self) -> bool:
        return bool(self._connected)

    def _conn_enabled(self) -> bool:
        """연결 시도 여부: tcp면 host가 있으면(기본 127.0.0.1이라 항상), serial이면 port가 있으면."""
        if self.cfg.mode == "tcp":
            return bool(self.cfg.host)
        return bool(self.cfg.port)

    # ---- unit/slave 키 자동판별(2.x=unit, 3.x=slave/device_id) ----
    def _unit_kwargs(self, fn) -> dict:
        if self._unit_key is None:
            try:
                params = inspect.signature(fn).parameters
            except (TypeError, ValueError):
                params = {}
            for key in ("device_id", "slave", "unit"):
                if key in params:
                    self._unit_key = key
                    break
            else:
                self._unit_key = "slave"   # 합리적 기본값
        return {self._unit_key: self.cfg.unit_id}

    # ---- 연결/해제 ----
    async def connect(self) -> bool:
        is_tcp = self.cfg.mode == "tcp"
        Client = ModbusTcpClient if is_tcp else ModbusSerialClient
        if Client is None:                 # 해당 전송 라이브러리 미설치
            return False
        if not self._conn_enabled():       # tcp=host / serial=port 없으면 시도 안 함
            return False

        def _open():
            if is_tcp:
                client = ModbusTcpClient(
                    host=self.cfg.host,
                    port=self.cfg.tcp_port,
                    timeout=self.cfg.timeout_s,
                )
            else:
                client = ModbusSerialClient(
                    port=self.cfg.port,
                    baudrate=self.cfg.baudrate,
                    bytesize=self.cfg.bytesize,
                    parity=self.cfg.parity,     # 'N'/'E'/'O'
                    stopbits=self.cfg.stopbits,
                    timeout=self.cfg.timeout_s,
                )
            ok = client.connect()
            return client if ok else None

        try:
            client = await asyncio.to_thread(_open)
        except Exception as e:  # noqa: BLE001 — 실패 원인을 진단용으로 보관하고 미연결 처리
            self._last_error = f"{type(e).__name__}: {e}"
            self._client = None
            self._connected = False
            return False
        if client is None:
            self._last_error = "연결 시도가 거부되거나 응답이 없습니다(timeout)"
        else:
            self._last_error = ""
        self._client = client
        self._connected = client is not None
        return self._connected

    def diagnose_connection(self) -> str:
        """연결 실패 원인을 사람이 조치할 수 있는 문장으로 돌려준다.
        exe에는 콘솔이 없어 이 문장이 현장의 유일한 단서다. 예외를 던지지 마라."""
        is_tcp = self.cfg.mode == "tcp"
        if (ModbusTcpClient if is_tcp else ModbusSerialClient) is None:
            return ("통신 라이브러리가 없습니다(pymodbus/pyserial 미설치). "
                    "설치 후 다시 실행하세요")
        if is_tcp:
            if not self.cfg.host:
                return "PLC 주소(host)가 설정되지 않았습니다. System Setup에서 지정하세요"
            return (f"연결 실패 — 케이블·PLC 전원·주소({self.cfg.host}:{self.cfg.tcp_port})·"
                    f"국번({self.cfg.unit_id})을 확인하세요"
                    + (f" ({self._last_error})" if self._last_error else ""))

        ports = [p["device"] for p in list_serial_ports()]
        avail = ", ".join(ports) if ports else "없음"
        if not self.cfg.port:
            return (f"COM 포트가 설정되지 않았습니다. System Setup에서 포트를 지정하세요. "
                    f"사용 가능한 포트: {avail}")
        if ports and self.cfg.port not in ports:
            return f"{self.cfg.port}을 찾을 수 없습니다. 사용 가능한 포트: {avail}"
        return (f"연결 실패 — 케이블·PLC 전원·국번({self.cfg.unit_id})·"
                f"통신속도({self.cfg.baudrate})를 확인하세요"
                + (f" ({self._last_error})" if self._last_error else ""))

    async def close(self):
        client, self._client = self._client, None
        self._connected = False
        if client is not None:
            try:
                await asyncio.to_thread(client.close)
            except Exception:  # noqa: BLE001
                pass

    # ---- 공통 요청 실행(직렬화 + 명령 간격 + 오류 시 끊김 표시) ----
    async def _exec(self, method_name: str, *args, **kwargs):
        if self._client is None:
            raise ConnectionError("PLC 미연결")
        async with self._lock:
            fn = getattr(self._client, method_name)
            kwargs.update(self._unit_kwargs(fn))
            try:
                rr = await asyncio.to_thread(lambda: fn(*args, **kwargs))
            except Exception:  # noqa: BLE001
                self._connected = False
                raise
            if self.cfg.inter_cmd_gap_s > 0:
                await asyncio.sleep(self.cfg.inter_cmd_gap_s)
            if hasattr(rr, "isError") and rr.isError():
                raise IOError(f"Modbus 오류 응답: {rr}")
            return rr

    # ---- 코일(비트, M 영역): 읽기 FC01 / 쓰기 FC05·15 ----
    async def read_coil(self, address: int, count: int = 1):
        rr = await self._exec("read_coils", address, count=count)
        bits = getattr(rr, "bits", [])
        return bits[0] if count == 1 and bits else bits

    async def write_coil(self, address: int, value: bool):
        await self._exec("write_coil", address, bool(value))
        return True

    async def write_coils(self, address: int, values):
        await self._exec("write_coils", address, [bool(v) for v in values])
        return True

    # ---- 홀딩 레지스터(워드, D 영역): 읽기 FC03 / 쓰기 FC06·16 ----
    async def read_register(self, address: int, count: int = 1):
        rr = await self._exec("read_holding_registers", address, count=count)
        regs = getattr(rr, "registers", [])
        return regs[0] if count == 1 and regs else regs

    async def write_register(self, address: int, value: int):
        await self._exec("write_register", address, int(value))
        return True

    async def write_registers(self, address: int, values):
        await self._exec("write_registers", address, [int(v) for v in values])
        return True

    # ---- 연결 유지 루프(연결→하트비트→끊김 시 재연결) ----
    async def _run_loop(self):
        backoff = 0.0                          # 연속 실패 시 재시도 간격(지수 백오프)
        while True:
            if not self._connected:
                ok = await self.connect()
                if not ok:
                    # 연속 실패 시 간격을 늘려 timeout 로그 도배 방지. 최소 3초, 최대 30초.
                    base = max(3.0, self.cfg.reconnect_delay_s)
                    backoff = min(30.0, backoff * 2 if backoff else base)
                    await asyncio.sleep(backoff)
                    continue
                backoff = 0.0                  # 연결 성공 → 백오프 리셋
            # 하트비트: 살아있는지 가벼운 확인. 실패하면 끊고 즉시 1회 재연결(이후 실패는 백오프).
            try:
                await self.heartbeat()
            except Exception:  # noqa: BLE001
                await self.close()
                continue
            await asyncio.sleep(max(0.1, self.cfg.heartbeat_s))

    async def heartbeat(self):
        """하트비트 '토글 쓰기'. 매 호출마다 HEARTBEAT 코일 값을 반전시켜 쓴다.
        PLC는 이 코일의 '변화(엣지)'로 통신 생존을 판단하므로 값이 바뀌는 것이 핵심.
        실패 시 예외를 그대로 올려 _run_loop가 끊고 재연결하도록 한다(주기=cfg.heartbeat_s)."""
        self._hb_value = not self._hb_value
        await self.write_coil(self._sys_addr("heartbeat"), self._hb_value)
        return True

    async def safety_reset(self, pulse_s: float = 0.25):
        """안전리셋(M112) 순간 펄스. ON → pulse_s 대기 → OFF.
        ★ M112는 레벨접점이라 계속 켜두면 고장 해제 시 자동 재가동됨 → 반드시 펄스로만 친다.
        중간에 실패해도 OFF는 최대한 보장(finally)."""
        addr = self._sys_addr("safety_reset")
        try:
            await self.write_coil(addr, True)
            await asyncio.sleep(pulse_s)
        finally:
            await self.write_coil(addr, False)
        return True

    # ---- 아날로그 스케일 변환(sccm ↔ DAC/ADC 카운트) ----
    def _sv_to_raw(self, name: str, sccm: float) -> int:
        """유량(sccm) → DAC 카운트. 스케일 정보 없으면 값 그대로(옛 동작) 사용."""
        s = self._scale.get(name) or {}
        fs   = float(s.get("fs_sccm") or 0)
        full = int(s.get("sv_full") or 0)
        if fs <= 0 or full <= 0:
            raw = int(round(float(sccm)))
        else:
            raw = int(round(float(sccm) / fs * full))
        return max(0, min(full or 4000, raw))

    def _pv_to_sccm(self, name: str, raw: int) -> float:
        """ADC 카운트 → 유량(sccm). 스케일 정보 없으면 카운트 그대로."""
        s = self._scale.get(name) or {}
        fs   = float(s.get("fs_sccm") or 0)
        zero = int(s.get("pv_zero") or 0)
        full = int(s.get("pv_full") or 0)
        if fs <= 0 or full <= zero:
            return float(raw)
        val = (float(raw) - zero) / float(full - zero) * fs
        return round(max(0.0, val), 2)

    # ---- 명명된 헬퍼(주소맵 키 사용). 미연결/실패 시 하위 read/write처럼 예외를 올림 ----
    async def set_valve(self, name: str, on: bool):
        """밸브/4-way 지령 코일 write. name ∈ {VA1~VA8}이면 해당 cmd_coil,
        name=='V4W'이면 시스템 v4w_cmd. 매핑 없는 이름은 KeyError(명확한 에러)."""
        addr = self._sys_addr("v4w_cmd") if name == "V4W" else self._valve_coil_of(name)
        await self.write_coil(addr, bool(on))
        return True

    async def write_sv(self, name: str, sccm: float):
        """MFC 목표유량(SV) 레지스터 write. name ∈ {VA1~VA8}.
        인자는 유량(sccm) — 내부에서 채널 스케일로 DAC 카운트 변환·clamp 한다."""
        addr = self._sv_reg_of(name)
        await self.write_register(addr, self._sv_to_raw(name, sccm))
        return True

    async def write_valves_block(self, valve_map: dict, v4w: bool) -> bool:
        """밸브 지령 + 4-way를 한 프레임(FC15)으로 쓴다.
        valve_map: {채널id: bool}. 주소가 연속이 아니면 개별 쓰기로 폴백.
        ★ 블록 쓰기는 주소 사이 빈 칸까지 덮어쓰므로 연속성 검사가 필수다."""
        items = [(self._valve_coil_of(cid), bool(on)) for cid, on in valve_map.items()]
        items.append((self._sys_addr("v4w_cmd"), bool(v4w)))
        addrs = [a for a, _ in items]
        if not addrs:
            return True
        base, top = min(addrs), max(addrs)
        if top - base + 1 == len(set(addrs)) == len(addrs):
            values = [False] * (top - base + 1)
            for a, v in items:
                values[a - base] = v
            await self.write_coils(base, values)
            return True
        for a, v in items:                       # 폴백: 개별 쓰기
            await self.write_coil(a, v)
        return True

    async def write_sv_block(self, sv_map: dict) -> bool:
        """SV 레지스터를 한 프레임(FC16)으로 쓴다.
        sv_map: {채널id: sccm(float)}. 내부에서 _sv_to_raw로 변환.
        주소가 연속이 아니면 개별 쓰기로 폴백."""
        items = [(self._sv_reg_of(cid), self._sv_to_raw(cid, sccm))
                 for cid, sccm in sv_map.items()]
        addrs = [a for a, _ in items]
        if not addrs:
            return True
        base, top = min(addrs), max(addrs)
        if top - base + 1 == len(set(addrs)) == len(addrs):
            values = [0] * (top - base + 1)
            for a, v in items:
                values[a - base] = v
            await self.write_registers(base, values)
            return True
        for a, v in items:                       # 폴백: 개별 쓰기
            await self.write_register(a, v)
        return True

    async def read_pv_all(self) -> dict:
        """켜진 매핑 채널의 PV를 블록 1회 읽기 → {"pv": {id: sccm}, "pv_raw": {id: 카운트}}.
        주소 범위가 32워드를 넘으면 안전하게 개별 읽기로 폴백한다.
        원시 카운트를 함께 돌려주는 이유: 현장에서 MFC 스케일을 진단하려면 카운트가 필요.
        ★ pv_raw 는 부호 변환된 값이다(개방·잡음에서 -48 등 음수가 나온다)."""
        items = [(n, a) for n, a in self._pv_reg_items()
                 if not self._enabled or self._enabled.get(n)]
        if not items:
            return {"pv": {}, "pv_raw": {}}
        addrs = [a for _, a in items]
        base, count = min(addrs), max(addrs) - min(addrs) + 1
        raw = {}
        if count <= 32:
            regs = await self.read_register(base, count=count)
            regs = regs if isinstance(regs, list) else [regs]
            for n, a in items:
                i = a - base
                raw[n] = _s16(regs[i]) if 0 <= i < len(regs) else 0
        else:
            for n, a in items:                   # 폴백: 개별 읽기
                raw[n] = _s16(await self.read_register(a))
        return {"pv": {n: self._pv_to_sccm(n, v) for n, v in raw.items()}, "pv_raw": raw}

    # 320 (M00200) · 336 (M00210) — 공압 인터록 제거로 미사용. 복원 시 재사용 예약.
    # ★ 키에서는 빠졌지만 블록 읽기 범위에는 남긴다(요청 rc 320,20 불변).
    _STATUS_RESERVED = (320, 336)

    async def read_status(self) -> dict:
        """상태 코일을 블록 1회 읽기 →
        {"SAFETY_STOP","RUN_PERMIT","ALM_MFC","ALM_IDD","ALM_DAC"} (bool)."""
        keys = (("SAFETY_STOP", "safety_stop"),
                ("RUN_PERMIT", "run_permit"),
                ("ALM_MFC", "alm_mfc"), ("ALM_IDD", "alm_idd"), ("ALM_DAC", "alm_dac"))
        pairs = [(out_key, self._sys_addr(sys_key)) for out_key, sys_key in keys]
        addrs = [a for _, a in pairs] + list(self._STATUS_RESERVED)
        base, count = min(addrs), max(addrs) - min(addrs) + 1
        if count > 64:
            return {k: bool(await self.read_coil(a)) for k, a in pairs}
        bits = await self._exec("read_coils", base, count=count)
        bits = getattr(bits, "bits", []) or []
        # ★ pymodbus는 비트를 바이트 단위로 패딩해 돌려준다(요청 20개 → 24비트).
        #   길이를 확인하고 모자라면 False로 처리한다.
        out = {}
        for k, a in pairs:
            i = a - base
            out[k] = bool(bits[i]) if 0 <= i < len(bits) else False
        return out

    async def poll(self) -> dict:
        """PV + 상태를 한 번에 읽어 반환(state/UI로 밀지 않음 — 호출자 몫). 요청 2회."""
        pv = await self.read_pv_all()          # {"pv": {...}, "pv_raw": {...}}
        return {"pv": pv["pv"], "pv_raw": pv["pv_raw"], "status": await self.read_status()}

    async def start(self):
        """연결 대상이 있으면(tcp=host / serial=port) 연결 유지 루프 시작(중복 시작 방지)."""
        if not self._conn_enabled():
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.close()

    async def reconnect(self) -> bool:
        """설정 변경 후 재적용: 기존 연결/루프를 끊고 새 설정으로 다시 시작.
        즉시 한 번 연결을 시도해 그 결과(성공/실패)를 반환하고, 유지 루프도 다시 띄운다."""
        await self.stop()
        ok = await self.connect()   # 버튼 피드백용: 즉시 결과 확인
        await self.start()          # 유지 루프 시작(연결됐으면 그대로, 아니면 백오프 재시도)
        return ok


# ===================== 주소맵 fallback(LS XGB Modbus base=0 확정) =====================
# ★ 실제 사용 주소는 config(state.channels[].plc / state.plc_system)에서 load_addresses로 로드한다.
#   아래 하드코딩 맵은 주소맵이 아직 안 실렸을 때만 쓰는 fallback 기본값이다.
# 코일(M 비트) = 워드번호×16 + 비트번호. LS 표기 M00abc는 워드=ab, 비트=c 로 읽는다.
#   예) M00100 → 워드10·비트0 = 10×16+0 = 160,  M00112 → 워드11·비트2 = 11×16+2 = 178,
#       M00211 → 워드21·비트1 = 21×16+1 = 337.
# 레지스터(D 워드) = D 워드번호 그대로.  예) D00100 → 100,  D00200 → 200.
# 접근은 코일(read_coil/write_coil) + 홀딩 레지스터(read_register/write_register)만 사용.
PLC_COIL_MAP = {
    "VA1_CMD": 160,       # M00100 (쓰기) 밸브/MFC 지령
    "VA2_CMD": 161,       # M00101
    "VA3_CMD": 162,       # M00102
    "VA4_CMD": 163,       # M00103
    "VA5_CMD": 164,       # M00104
    "VA6_CMD": 165,       # M00105
    "VA7_CMD": 166,       # M00106
    "VA8_CMD": 167,       # M00107
    "V4W_CMD": 168,       # M00108 4-way 지령
    "HEARTBEAT": 176,     # M00110 (쓰기) 통신 생존 토글
    "SAFETY_RESET": 178,  # M00112 (쓰기, 펄스) 안전리셋
    # 320 (M00200) · 336 (M00210) — 공압 인터록 제거로 미사용. 복원 시 재사용 예약.
    "SAFETY_STOP": 321,   # M00201 (읽기) 안전정지 상태
    "RUN_PERMIT": 323,    # M00203 (읽기) 운전 허가 래치
    "ALM_MFC": 337,       # M00211 (읽기) MFC 입력 이상 알람
    "ALM_IDD": 338,       # M00212 (읽기) MFC 입력 단선검출 알람
    "ALM_DAC": 339,       # M00213 (읽기) 아날로그 출력 모듈 이상 알람
}
PLC_REG_MAP = {
    # D00100~107 (쓰기) 목표유량
    "SV_VA1": 100, "SV_VA2": 101, "SV_VA3": 102, "SV_VA4": 103,
    "SV_VA5": 104, "SV_VA6": 105, "SV_VA7": 106, "SV_VA8": 107,
    # D00200~207 (읽기) 현재유량
    "PV_VA1": 200, "PV_VA2": 201, "PV_VA3": 202, "PV_VA4": 203,
    "PV_VA5": 204, "PV_VA6": 205, "PV_VA7": 206, "PV_VA8": 207,
}
# 시스템 공통 주소 fallback(내부 맵 _sys 미로딩 시). PlcClient._sys_addr가 참조.
_FALLBACK_SYS = {
    "heartbeat": PLC_COIL_MAP["HEARTBEAT"],
    "safety_reset": PLC_COIL_MAP["SAFETY_RESET"],
    "v4w_cmd": PLC_COIL_MAP["V4W_CMD"],
    "safety_stop": PLC_COIL_MAP["SAFETY_STOP"],
    "run_permit": PLC_COIL_MAP["RUN_PERMIT"],
    "alm_mfc": PLC_COIL_MAP["ALM_MFC"],
    "alm_idd": PLC_COIL_MAP["ALM_IDD"],
    "alm_dac": PLC_COIL_MAP["ALM_DAC"],
}


# ===================== 모듈 싱글턴 + 설정 반영 =====================
plc = PlcClient()


def configure(plc_settings: dict):
    """state.plc(dict)로 클라이언트 설정을 갱신한다(로거 configure와 동일한 사용법).
    실제 연결 반영은 재연결 시점에 이뤄진다(server 시작 시 start(), apply 시 reconnect())."""
    plc.set_config(config_from_dict(plc_settings))


def load_addresses(channels: list, plc_system: dict):
    """state.channels/state.plc_system로 내부 주소맵을 로드(모듈 싱글턴에 위임).
    server 시작 시, 그리고 설정 저장/변경 시 호출한다."""
    plc.load_addresses(channels, plc_system)


# ---- commands.py 등에서 부르기 쉬운 얇은 래퍼(모듈 싱글턴에 위임) ----
async def safety_reset(pulse_s: float = 0.25):
    return await plc.safety_reset(pulse_s)


async def poll() -> dict:
    return await plc.poll()


def list_serial_ports() -> list:
    """사용 가능한 시리얼 포트 목록(프론트 드롭다운용). pyserial 없으면 빈 목록."""
    if _list_ports is None:
        return []
    try:
        return [{"device": p.device, "desc": (p.description or "")}
                for p in _list_ports.comports()]
    except Exception:  # noqa: BLE001
        return []
