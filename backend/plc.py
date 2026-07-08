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
    from serial.tools import list_ports as _list_ports
except Exception:  # noqa: BLE001
    _list_ports = None


# ===================== 연결 설정 =====================
@dataclass
class PlcConfig:
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

    # ---- 설정 ----
    def set_config(self, cfg: PlcConfig):
        self.cfg = cfg

    def is_connected(self) -> bool:
        return bool(self._connected)

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
        if ModbusSerialClient is None:
            return False
        if not self.cfg.port:
            return False

        def _open():
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
        """살아있음 확인용 가벼운 읽기.
        TODO: 실제 하트비트 주소(예: 특정 M/D)를 아래 read로 지정한다.
              지금은 주소맵 미확정이라 no-op(연결 여부만으로 판단)."""
        # 예시(주소 확정 후 활성화):
        # await self.read_coil(PLC_COIL_MAP["heartbeat"])
        return True

    async def start(self):
        """port가 설정돼 있으면 연결 유지 루프 시작(중복 시작 방지)."""
        if not self.cfg.port:
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


# ===================== 주소맵(TODO — 하드웨어 확정 후 채움) =====================
# LS XGB Modbus base = M0000/D0000. 코일=M 비트, 레지스터=D 워드.
# 각 기능이 어떤 주소를 쓰는지 확정되면 아래에 채운다.
# TODO: PLC_COIL_MAP — 비트 신호(밸브 개폐, RUN/STOP, 비상정지, 상태 플래그 등)
PLC_COIL_MAP = {
    # "va1_open": 0,          # 예) M0000
    # "auto_run": 16,         # 예) M0016
    # "emergency": 31,
    # "heartbeat": 100,
}
# TODO: PLC_REG_MAP — 워드 값(MFC SV/PV, RH, 카운터 등)
PLC_REG_MAP = {
    # "va1_sv": 0,            # 예) D0000
    # "va1_pv": 1,
    # "rh_pv": 40,
}


# ===================== 모듈 싱글턴 + 설정 반영 =====================
plc = PlcClient()


def configure(plc_settings: dict):
    """state.plc(dict)로 클라이언트 설정을 갱신한다(로거 configure와 동일한 사용법).
    실제 연결 반영은 재연결 시점에 이뤄진다(server 시작 시 start(), apply 시 reconnect())."""
    plc.set_config(config_from_dict(plc_settings))


def list_serial_ports() -> list:
    """사용 가능한 시리얼 포트 목록(프론트 드롭다운용). pyserial 없으면 빈 목록."""
    if _list_ports is None:
        return []
    try:
        return [{"device": p.device, "desc": (p.description or "")}
                for p in _list_ports.comports()]
    except Exception:  # noqa: BLE001
        return []
