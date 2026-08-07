"""실통신 스모크 테스트 — fake_plc(TCP)와 서버를 상대로 핵심 안전 사슬을 자동 판정한다.

사용법(터미널 3개 또는 순서 실행):
  1) python test/fake_plc.py            # 기본 127.0.0.1:502
  2) python backend/server.py           # 창이 뜨면 그대로 두거나, 창 없이:
       python -m uvicorn server:app --port 8000   (backend 폴더에서)
  3) python test/e2e_smoke.py [ws포트=8000]
판정: 전 항목 통과 시 "E2E PASS" + 종료코드 0, 실패 시 어느 단계인지 출력 + 1.
★ 이 테스트는 PLC 설정을 TCP 127.0.0.1:502 로 저장한다(config.json).
  실장비(시리얼)로 돌아갈 때 System Setup 에서 되돌릴 것.
의존성: websockets (uvicorn[standard] 에 포함 — 추가 설치 불필요)
"""
import asyncio, json, sys, time
import websockets

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
URI = f"ws://127.0.0.1:{PORT}/ws"
state, logs = {}, []

def step(n, msg):
    print(f"[{n}] {msg}")

async def drive():
    ws = None
    for _ in range(25):
        try:
            ws = await websockets.connect(URI, ping_interval=None); break
        except OSError:
            await asyncio.sleep(0.4)
    assert ws, f"서버 미기동(ws://127.0.0.1:{PORT})"

    async def send(o): await ws.send(json.dumps(o))
    async def pump(sec):
        end = time.time() + sec
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(0.05, end - time.time()))
            except asyncio.TimeoutError:
                break
            m = json.loads(raw)
            if m.get("type") == "state": state.update(m)
            elif m.get("type") == "log": logs.append(m.get("msg", ""))
    live = lambda: state.get("plc_live") or {}
    chan = lambda cid: next((c for c in state.get("channels", []) if c["id"] == cid), {})

    await pump(1.0)
    await send({"cmd": "apply_setup", "channels": [],
                "plc": {"mode": "tcp", "host": "127.0.0.1", "tcp_port": 502, "unit_id": 1}})
    await pump(0.6); await send({"cmd": "plc_reconnect"}); await pump(2.5)
    assert live().get("connected") is True, "PLC 연결 실패 — fake_plc 실행 여부 확인: " + str(logs[-5:])
    st = live().get("status") or {}
    step(1, f"TCP 연결 OK · SAFETY_STOP={st.get('SAFETY_STOP')} (부팅 트립=True 정상)")
    assert st.get("SAFETY_STOP") is True

    await send({"cmd": "set_valve", "ch": 2, "open": True}); await pump(0.9)
    assert not chan("VA3").get("valveIn"), "트립 중 밸브 조작이 잠기지 않음!"
    step(2, "트립 중 조작 잠금 OK (VA3 열림 거부)")

    await send({"cmd": "plc_reset"}); await pump(2.5)
    st = live().get("status") or {}
    assert st.get("SAFETY_STOP") is False and st.get("RUN_PERMIT") is True, "리셋 실패"
    assert not any(c.get("valveIn") for c in state.get("channels", [])), "리셋 순간 밸브 자동 개방!"
    step(3, "안전 리셋 OK · 자동 개방 없음")

    await send({"cmd": "set_valve", "ch": 2, "open": True})
    await send({"cmd": "set_sv", "ch": 2, "value": 500}); await pump(3.5)
    pv = live().get("pv") or {}
    assert pv.get("VA3", 0) > 300, f"VA3 PV 추종 실패: {pv}"
    assert abs(pv.get("VA1", 0)) < 5 and abs(pv.get("VA5", 0)) < 5, f"다른 채널 PV 오염: {pv}"
    step(4, f"VA3 SV500 → PV {pv.get('VA3', 0):.0f} 추종 OK · 타 채널 0 유지")

    await send({"cmd": "set_valve", "ch": 2, "open": False})
    await send({"cmd": "set_sv", "ch": 2, "value": 0}); await pump(2.0)
    step(5, f"닫힘 후 PV {((live().get('pv') or {}).get('VA3', 0)):.0f} (감소 중)")
    print("\nE2E PASS")

if __name__ == "__main__":
    try:
        asyncio.run(drive())
    except AssertionError as e:
        print("\nE2E FAIL —", e); sys.exit(1)
