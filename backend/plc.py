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
    return out


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
        # config 주도 주소맵(load_addresses로 채움). 비어있으면 하드코딩 fallback 사용.
        self._valve_coil = {}             # {채널id: cmd_coil}
        self._sv_reg = {}                 # {채널id: sv_reg}
        self._pv_reg = {}                 # {채널id: pv_reg}
        self._sys = {}                    # 시스템 공통 주소(plc_system)

    # ---- 설정 ----
    def set_config(self, cfg: PlcConfig):
        self.cfg = cfg

    def load_addresses(self, channels: list, plc_system: dict):
        """config(state.channels/state.plc_system)에서 주소맵을 로드한다.
        매핑 있는 채널(plc != None)만 담고, 시스템 주소는 통째로 사본으로 보관."""
        chans = channels or []
        self._valve_coil = {ch["id"]: ch["plc"]["cmd_coil"] for ch in chans if ch.get("plc")}
        self._sv_reg = {ch["id"]: ch["plc"]["sv_reg"] for ch in chans if ch.get("plc")}
        self._pv_reg = {ch["id"]: ch["plc"]["pv_reg"] for ch in chans if ch.get("plc")}
        self._sys = dict(plc_system or {})

    # ---- 주소 resolver(내부 맵 우선, 없으면 하드코딩 fallback) ----
    def _sys_addr(self, key: str) -> int:
        return self._sys[key] if self._sys else _FALLBACK_SYS[key]

    def _valve_coil_of(self, name: str) -> int:
        return self._valve_coil[name] if self._valve_coil else PLC_COIL_MAP[f"{name}_CMD"]

    def _sv_reg_of(self, name: str) -> int:
        return self._sv_reg[name] if self._sv_reg else PLC_REG_MAP[f"SV_{name}"]

    def _pv_reg_items(self):
        if self._pv_reg:
            return list(self._pv_reg.items())
        return [(n, PLC_REG_MAP[f"PV_{n}"]) for n in ("VA1", "VA3", "VA5", "VA6")]

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

        client = await asyncio.to_thread(_open)
        self._client = client
        self._connected = client is not None
        return self._connected

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
        while True:
            if not self._connected:
                ok = await self.connect()
                if not ok:
                    await asyncio.sleep(max(0.2, self.cfg.reconnect_delay_s))
                    continue
            # 하트비트: 살아있는지 가벼운 확인. 실패하면 끊고 재연결.
            try:
                await self.heartbeat()
            except Exception:  # noqa: BLE001
                await self.close()
                await asyncio.sleep(max(0.2, self.cfg.reconnect_delay_s))
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

    # ---- 명명된 헬퍼(주소맵 키 사용). 미연결/실패 시 하위 read/write처럼 예외를 올림 ----
    async def set_valve(self, name: str, on: bool):
        """밸브/4-way 지령 코일 write. name ∈ {VA1,VA3,VA5,VA6}이면 해당 cmd_coil,
        name=='V4W'이면 시스템 v4w_cmd. 매핑 없는 이름은 KeyError(명확한 에러)."""
        addr = self._sys_addr("v4w_cmd") if name == "V4W" else self._valve_coil_of(name)
        await self.write_coil(addr, bool(on))
        return True

    async def write_sv(self, name: str, raw: int):
        """MFC 목표유량(SV) 레지스터 write. name ∈ {VA1,VA3,VA5,VA6}. 값은 0~2000 clamp."""
        addr = self._sv_reg_of(name)
        val = int(min(2000, max(0, int(raw))))
        await self.write_register(addr, val)
        return True

    async def read_pv_all(self) -> dict:
        """PLC 매핑된 채널의 현재유량(PV) 일괄 읽기 → {채널id: 값}."""
        out = {}
        for name, addr in self._pv_reg_items():
            out[name] = await self.read_register(addr)
        return out

    async def read_status(self) -> dict:
        """상태 코일 읽기 → {"AIR_OK","SAFETY_STOP","ALM_AIR","ALM_MFC"} (bool)."""
        out = {}
        for out_key, sys_key in (("AIR_OK", "air_ok"), ("SAFETY_STOP", "safety_stop"),
                                 ("ALM_AIR", "alm_air"), ("ALM_MFC", "alm_mfc")):
            out[out_key] = bool(await self.read_coil(self._sys_addr(sys_key)))
        return out

    async def poll(self) -> dict:
        """PV + 상태를 한 번에 읽어 반환(state/UI로 밀지 않음 — 호출자 몫)."""
        return {"pv": await self.read_pv_all(), "status": await self.read_status()}

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

    async def reconnect(self):
        """설정 변경 후 재적용: 기존 연결/루프를 끊고 새 설정으로 다시 시작."""
        await self.stop()
        await self.start()


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
    "VA3_CMD": 161,       # M00101
    "VA5_CMD": 162,       # M00102
    "VA6_CMD": 163,       # M00103
    "V4W_CMD": 164,       # M00104 4-way 지령
    "HEARTBEAT": 176,     # M00110 (쓰기) 통신 생존 토글
    "SAFETY_RESET": 178,  # M00112 (쓰기, 펄스) 안전리셋
    "AIR_OK": 320,        # M00200 (읽기) 공기압 정상
    "SAFETY_STOP": 321,   # M00201 (읽기) 안전정지 상태
    "ALM_AIR": 336,       # M00210 (읽기) 공기 알람
    "ALM_MFC": 337,       # M00211 (읽기) MFC 알람
}
PLC_REG_MAP = {
    "SV_VA1": 100, "SV_VA3": 101, "SV_VA5": 102, "SV_VA6": 103,   # D00100~103 (쓰기) 목표유량
    "PV_VA1": 200, "PV_VA3": 201, "PV_VA5": 202, "PV_VA6": 203,   # D00200~203 (읽기) 현재유량
}
# 시스템 공통 주소 fallback(내부 맵 _sys 미로딩 시). PlcClient._sys_addr가 참조.
_FALLBACK_SYS = {
    "heartbeat": PLC_COIL_MAP["HEARTBEAT"],
    "safety_reset": PLC_COIL_MAP["SAFETY_RESET"],
    "v4w_cmd": PLC_COIL_MAP["V4W_CMD"],
    "air_ok": PLC_COIL_MAP["AIR_OK"],
    "safety_stop": PLC_COIL_MAP["SAFETY_STOP"],
    "alm_air": PLC_COIL_MAP["ALM_AIR"],
    "alm_mfc": PLC_COIL_MAP["ALM_MFC"],
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
