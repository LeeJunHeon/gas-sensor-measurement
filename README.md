# Gas Sensor Measurement System

가스 밸브·MFC를 제어하는 가스 센서 측정 장비용 제어 프로그램.

## 폴더 구조 (역할 분리)

```
gas-sensor-measurement/
├── config.json / recipes/        # 채널 설정 + 레시피(프로젝트 루트)
├── frontend/                     # 화면 (서버를 모름)
│   ├── index.html                #   마크업
│   ├── css/style.css             #   스타일
│   └── js/ schematic.js          #   배관도(채널/밸브) 렌더 + drawBuses
│         recipe.js               #   레시피 표 + System Setup 모달
│         core.js                 #   헤더/상태/로그·서버상태 반영·fit/도크/종료모달·전역노출·초기화
│         app.js                  #   서버 연동(WebSocket/명령/시뮬레이션 대체)
└── backend/                      # 서버 (상태의 주인)
    ├── server.py                 #   진입점: FastAPI·라우트·WebSocket·lifespan·pywebview 창
    ├── state.py                  #   State(channels/system/recipe) + config.json 로드/저장
    ├── commands.py               #   handle_command(명령 처리)
    ├── storage.py                #   레시피/설정 파일 I/O(원자적 쓰기·검증·목록)
    ├── simulation.py             #   시뮬레이션 telemetry(sim_tick)
    └── connection.py             #   ConnectionManager + push_state/push_log
```

화면 ↔ 서버 사이의 모든 약속(DOM 표식·전역 함수·WebSocket 메시지)은
[`INTERFACE.md`](INTERFACE.md)에 정의되어 있다.

## 실행 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 실행 (프로젝트 루트에서)

```bash
python backend/server.py
```

실행하면 "Gas Sensor Measurement System" 창이 최대화로 뜨고, 서버가 보내는 측정값이
실시간으로 흐른다. 밸브 클릭, SV/MAX 변경, 4-way 전환, AUTO RUN/STOP/PURGE,
System Setup 적용, 레시피 저장·불러오기가 서버와 실제로 명령을 주고받는다.

## 1단계 범위 (현재)

- **하드웨어는 연결하지 않는다.** 측정값은 모두 `backend/simulation.py`의 **시뮬레이션** 데이터다.
  (측정 하드웨어가 없어 RH·SMU는 보내지 않으며 화면엔 "—"로 표시된다.)
- 장비만 없을 뿐 화면 ↔ 연동 ↔ 서버 구조는 진짜로 동작한다.
- 추후 단계에서 `backend/simulation.py`(및 관련 모듈)만 실제 장비 제어 코드로 교체할 예정이다.

서버 연결이 끊기면 화면은 "연결 끊김"을 표시하고 마지막 값을 유지하며,
브라우저 내부 시뮬레이션 모드로 전환된다(2초마다 자동 재연결 시도).
