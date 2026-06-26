# INTERFACE.md — 화면 ↔ 서버 통신 명세 (계약)

## 0. 범위
- 이 문서는 화면(index.html + app.js) ↔ 서버(server.py) 사이의 약속만 정의한다.
- 서버 ↔ 하드웨어(보드/MFC) 통신은 범위 밖(추후 별도 정의). 1단계에서 서버는 시뮬레이션 값을 보낸다.
- 통신 방식: WebSocket(양방향 실시간), 메시지는 JSON.

## 1. 역할 분담
- index.html : 화면 그리기 전담. channels·procs 상태 배열 + 렌더 함수만. 서버를 모름.
- app.js : 서버 연결, 명령 전송, 측정값 수신 → 화면 반영. 서버 끊기면 시뮬레이션 대체.
- server.py : 화면 명령 수신 → (1단계는 시뮬레이션) 상태 갱신 → 측정값 주기적 전송.

## 2. 핵심 원칙
- 서버가 상태의 주인. 사용자 동작은 "요청"이며, 서버가 돌려준 상태가 와야 화면에 반영된다.
- 측정값(PV·RH 등)은 서버가 먼저 밀어주는 방식으로 빠르게 갱신한다.
- 서버 끊기면 화면은 "연결 끊김" 표시 + 마지막 값 유지 + 시뮬레이션 대체.

## 3. 상태 스키마

### 3.1 채널 (channels) — 8개
```
{
  id:       "VA1"             // 채널 식별자 VA1~VA8
  grp:      "air" | "gas"     // 그룹
  route:    "pure" | "mix"    // pure=순수 Air, mix=혼합
  en:       true | false      // 사용 여부 (false면 비활성·잠김)
  valveIn:  true | false      // MFC 밸브(VA) 개폐
  valveOut: true | false      // 솔레노이드 밸브(SOL) 개폐
  max:      2000              // MFC 최대 용량 (sccm)
  sv:       0                 // 설정 유량 (sccm)
  pv:       0                 // 현재 유량 (sccm) — 측정값, telemetry로 갱신
}
```

### 3.2 시스템 (system)
```
{
  running:   true | false             // 자동 실행 중 여부
  routeOut:  "sensor" | "vent"         // 4-way 출력 방향
  loop:      { current: 0, total: 7 }  // 전체 반복 진행
  elapsed:   0                         // 경과 시간(초)
  rh:        40.0                      // 현재 습도(%)
  smu:       "+1.16398E-05"            // SMU 측정값 표시 문자열
  connected: true | false              // 서버↔하드웨어 연결 (1단계는 시뮬)
  safeStop:  false                     // 안전 정지 발동 여부
}
```

### 3.3 레시피 (recipe)
```
{
  name:        ""                      // 레시피 이름 (빈 값으로 시작)
  useHumidity: true | false
  loopCount:   7
  procs: [
    {
      flow: 1000,                      // 전체 가스 유량 (sccm)
      rh:   40,                        // 습도(%)
      g:    [5, 0, 0, 0],              // 측정 MFC 농도 G1~G4 (ppm)
      prep: 3600,                      // 준비 시간(초)
      meas: 300,                       // 측정 시간(초)
      way:  "sensor" | "vent",         // 이 단계의 4-way 방향
      rep:  false                      // 이 단계 반복 여부
    }
  ]
  params: {                            // 측정/전압/SMU 파라미터
    vStart: 0.5, vEnd: 0.0, vStep: 0,
    grafInterval: 1,
    smuMode: "Source V, Measure I",
    smuSource: 0.5, smuCompliance: 1.0,
    chFrom: 1, chTo: 1
  }
}
```

## 4. WebSocket 메시지

### 4.1 서버 → 화면 (받음)

(1) state — 전체 상태 동기화 (연결 직후 + 밸브/설정/레시피 등 변화 시)
```
{ "type": "state",
  "channels": [ /* 3.1 객체 8개 */ ],
  "system":   { /* 3.2 */ },
  "recipe":   { /* 3.3 */ } }
```

(2) telemetry — 실시간 측정값 (초당 5회)
```
{ "type": "telemetry",
  "pv": [0,0,0,0,0,0,0,0],
  "rh": 40.1, "smu": "+1.16398E-05",
  "elapsed": 123, "running": true,
  "loop": { "current": 3, "total": 7 } }
```

(3) log — 서버 발생 로그 (level: ok|info|warn|err)
```
{ "type": "log", "msg": "AUTO RUN 시작", "level": "ok" }
```

(4) recipe_list — 저장된 레시피 목록
```
{ "type": "recipe_list", "names": ["recipe_NO2", "test_H2S"] }
```

(5) ack — 명령 처리 결과 (저장 등)
```
{ "type": "ack", "of": "recipe_save", "ok": true,  "name": "recipe_NO2" }
{ "type": "ack", "of": "recipe_save", "ok": false, "reason": "exists", "name": "recipe_NO2" }
```

### 4.2 화면 → 서버 (보냄)
```
{ "cmd": "set_valve", "ch": 0, "side": "in", "open": true }   // side: in(VA) | out(SOL)
{ "cmd": "set_sv",    "ch": 4, "value": 5 }
{ "cmd": "set_max",   "ch": 0, "value": 2000 }
{ "cmd": "set_4way",  "route": "vent" }                        // sensor | vent
{ "cmd": "run" }
{ "cmd": "stop" }
{ "cmd": "purge" }
{ "cmd": "apply_setup",  "channels": [ /* {ch,en,grp,route,max,sv} ... */ ], "params": { } }
{ "cmd": "recipe_new" }
{ "cmd": "recipe_save",  "name": "recipe_NO2", "overwrite": false, "recipe": { /* 3.3 */ } }
{ "cmd": "recipe_load",  "name": "recipe_NO2" }
{ "cmd": "recipe_list" }
```

## 5. 화면 쪽 고정 인터페이스 (UI를 새로 디자인해도 지켜야 할 약속)

### 5.1 DOM 핸들 (app.js가 참조)
```
[data-pv="N"]    채널 N PV 표시 (textContent)
[data-sv="N"]    채널 N SV 입력 (value)
[data-max="N"]   채널 N MAX 입력 (value)
[data-v="N-in"] / [data-v="N-out"]   밸브 (클릭)
.way[data-out="sensor|vent"]         4-way 버튼 (클릭)
#rh                  RH 표시
#activeCh #totalFlow #clk #hdrLoop #runtxt #measVal   상단 상태 표시
#connStatus          연결 상태 표시 pill (신규)
#logBody             로그 컨테이너
#recipePicker        레시피 선택창 (신규)
레시피: [data-f="키-i"] [data-g="i-gi"] [data-way="i"] [data-rep="i"] [data-del="i"] #addProc
```

### 5.2 전역 함수/상태 (index.html이 제공)
```
window.channels / window.procs        상태 배열
renderLanes()      배관도 재렌더        renderRecipe()   레시피 표 재렌더
drawBuses()        배관(SVG) 재드로우   updateSystem()   상단 통계 갱신
logMsg(msg, level) 로그 한 줄 추가
applyState(state)      서버 state를 반영 후 렌더 — app.js가 호출
applyTelemetry(t)      빠른 측정값만 가볍게 반영(재렌더 없이) — app.js가 호출
```

### 5.3 상호작용 규칙
- 사용자 동작 → app.js가 명령 전송. 화면 상태는 서버 state가 와야 갱신(요청-반영 분리).
- telemetry는 applyTelemetry()로 숫자만 가볍게 갱신(배관 재드로우 금지 → 빠른 표시 유지).
- 서버 끊김 → #connStatus "연결 끊김" + 시뮬레이션 대체.
