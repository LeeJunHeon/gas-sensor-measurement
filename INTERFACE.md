# INTERFACE.md — 화면 ↔ 서버 통신 명세 (계약)

## 0. 범위
- 이 문서는 화면(index.html + app.js) ↔ 서버(server.py) 사이의 약속만 정의한다.
- 서버 ↔ PLC는 Modbus(RTU/TCP). 측정값(PV)은 PLC 실측만 보내며, 없으면 null(화면 '—')이다.
- 통신 방식: WebSocket(양방향 실시간), 메시지는 JSON.

## 1. 역할 분담
- 화면 = `frontend/` (index.html + css/style.css + js/) : 화면 그리기 전담. 서버를 모름.
  - js/schematic.js : 배관도(channels/밸브) 렌더 + drawBuses
  - js/recipe.js : 레시피 표(procs) + System Setup 모달
  - js/core.js : 헤더/상태/로그, 서버상태 반영(applyState/applyTelemetry), fit/도크/종료모달, 전역 노출, 초기화
  - js/app.js : 서버 연결, 명령 전송, 측정값 수신 → 화면 반영. 서버 끊기면 모든 값 '—'.
- 서버 = `backend/` : 화면 명령 수신 → 상태 갱신 → PLC 실측 측정값 주기적 전송.
  - server.py(진입점) · state.py(상태+config) · commands.py(명령) · storage.py(파일) · loops.py(주기 태스크) · connection.py(연결)

> 논리적 약속(아래 DOM 표식·전역 함수·메시지)은 파일이 어떻게 나뉘든 그대로 유효하다.

## 2. 핵심 원칙
- 서버가 상태의 주인. 사용자 동작은 "요청"이며, 서버가 돌려준 상태가 와야 화면에 반영된다.
- 측정값(PV·RH 등)은 서버가 먼저 밀어주는 방식으로 빠르게 갱신한다.
- 서버 끊기면 화면은 "서버 연결 끊김" 표시 + 모든 측정값 '—'(가짜 값 없음) + 2초마다 재연결.

## 3. 상태 스키마

### 3.1 채널 (channels) — 8개
```
{
  id:       "VA1"             // 채널 식별자 VA1~VA8
  grp:      "air" | "gas"     // 그룹
  route:    "pure" | "mix"    // pure=순수 Air, mix=혼합
  en:       true | false      // 사용 여부 (false면 비활성·잠김)
  valveIn:  true | false      // MFC 밸브(VA) 개폐
  max:      2000              // MFC 최대 용량 (sccm)
  sv:       0                 // 설정 유량 (sccm)
  pv:       0                 // 현재 유량 (sccm) — 측정값, telemetry로 갱신
}
```

### 3.2 시스템 (system)
```
{
  running:   true | false             // 자동 실행 중 여부
  routeOut:  "sensor" | "vent"   // 4-way: **혼합(mix) 라인**이 가는 곳.
                                 //   "vent"   = 가스→Vent / 단독에어→Sensor (코일 OFF·무전원 기본)
                                 //   "sensor" = 가스→Sensor / 단독에어→Vent (코일 ON)
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
      // ※ "rep"(단계 반복)는 폐지됐다 — 엔진이 읽은 적이 없고 화면에서도 제거했다.
      //    구파일에 남아 있어도 normalize_recipe 가 조용히 버린다(에러 없음).
      type: "gas" | "purge"            // 단계 종류. 없거나 모르는 값이면 "gas"
                                       //   (타입 없는 구파일 레시피가 그대로 동작한다)
                                       // "purge": 희석 계산 없이 단독(pure) 에어만
                                       //   flow sccm 으로 연다. 4-way 는 OFF(vent) 유지
                                       //   — 그 위치가 곧 에어→Sensor. g·rh 는 안 쓴다.
                                       //   ★ 시간은 prep(s) 단독 사용이며 meas 는
                                       //     normalize_recipe 가 0 으로 강제한다
                                       //     (2026-08-14, 이전 'prep+meas 합산' 계약 폐기).
                                       //     구파일이 meas 에만 시간을 넣어뒀다면 prep 이
                                       //     0 이므로 run 이 거부되고 안내 문구가 뜬다.
    }
  ]
  // 화면 표기: type "gas" → "Gas→Sensor", "purge" → "Air→Sensor" (저장값은 불변)
  //   퍼지 행의 측정 칸은 화면에서 readonly(준비 칸만 입력).
  // 기본 레시피(부팅·New)는 3단계 골격 — purge(prep 60) / gas(60·60) / purge(prep 60).
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
plc_live — PLC 실측(읽기 폴링 결과). state 메시지에 항상 포함된다.
```
{ "plc_live": {
    "connected": true,
    "pv":     { "VA1": 1000.0 },     // 유량(sccm) — 채널 스케일로 환산한 값
    "pv_raw": { "VA1": 2000 },       // ADC 원시 카운트(스케일 진단용)
    "status": { "SAFETY_STOP": false, "RUN_PERMIT": true,
                "ALM_MFC": false, "ALM_IDD": false, "ALM_DAC": false } } }
                // AIR_OK·ALM_AIR 은 공압 인터록 제거로 사라졌다(CONFIG.md ⑥-2).
```

(2) telemetry — 실시간 측정값 (초당 5회)
```
{ "type": "telemetry",
  "pv": [0,0,0,0,0,0,0,0],
  "rh": 40.1, "smu": "+1.16398E-05",
  "elapsed": 123, "running": true,
  "loop": { "current": 3, "total": 7 },
  "phase": "idle" | "prep" | "meas" | "purge",   // 현재 구간
  "stepIndex": 1, "stepTotal": 3, "stepRemain": 42 }
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
{ "cmd": "set_valve", "ch": 0, "open": true }                 // VA 밸브 개폐
{ "cmd": "set_sv",    "ch": 4, "value": 5 }
{ "cmd": "set_max",   "ch": 0, "value": 2000 }
{ "cmd": "set_4way",  "route": "vent" }                        // sensor | vent
// 의미: route 는 '혼합(mix) 라인이 가는 곳'이다(3.2 routeOut 과 같은 정의).
//   "vent"   = 가스→Vent / 단독에어→Sensor (코일 OFF·무전원 기본)
//   "sensor" = 가스→Sensor / 단독에어→Vent (코일 ON)
{ "cmd": "set_bottle", "values": [100, 0, 0, 0] }              // 봄베 농도(ppm) 4개
// 봄베는 '장비에 물린 실물'이라 레시피가 아니라 config.json(last_bottle)에 저장된다.
// 재기동·New 시 기본 레시피의 bottle 로 다시 실린다.
{ "cmd": "run" }
{ "cmd": "stop" }
{ "cmd": "purge" }
{ "cmd": "emergency" }                                         // 비상정지 — 전 채널 차단(safeStop=true)
{ "cmd": "clear_emergency" }                                   // 비상정지 해제 — 밸브는 자동으로 열리지 않는다
// safeStop=true 동안 set_valve/set_sv/purge/run은 거부된다.
{ "cmd": "apply_setup",  "channels": [ /* {ch,en,grp,route,max,sv,scale?} ... */ ], "params": { } }
// scale = {fs_sccm, sv_full, pv_zero, pv_full} — 아날로그 스케일만 반영.
// PLC 주소(cmd_coil/sv_reg/pv_reg)는 화면에서 바꿀 수 없다(서버가 무시).
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
[data-v="N-in"]                      VA 밸브 (클릭)
#wayToggle                           4-way 방향 전환 토글 (클릭: sensor↔vent)
                                     ※ routeOut 은 '혼합(mix) 라인이 가는 곳' —
                                       vent=가스→Vent·에어→Sensor / sensor=가스→Sensor·에어→Vent
#rh                  RH 표시
#activeCh #totalFlow #clk #hdrLoop #runtxt #measVal   상단 상태 표시
#connStatus          연결 상태 표시 pill (신규)
#logBody             로그 컨테이너
#recipePicker        레시피 선택창 (신규)
레시피: [data-f="키-i"] [data-g="i-gi"] [data-rep="i"] [data-del="i"] #addProc
```

### 5.2 전역 함수/상태 (화면 js가 window.*로 제공 — schematic/recipe/core.js)
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
- 서버 끊김 → #connStatus "연결 끊김" + 헤더 상태줄 "서버 연결 끊김" + 모든 값 '—'.
