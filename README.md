# Gas Sensor Measurement System

가스 밸브·MFC를 제어하는 가스 센서 측정 장비용 제어 프로그램.

## 구조 (역할 분리)

| 파일 | 역할 |
| --- | --- |
| `index.html` | 화면 전담 (배관도·레시피·로그 렌더링). 서버를 모름. |
| `app.js` | WebSocket 연결, 명령 전송, 측정값 수신 → 화면 반영. 끊기면 시뮬레이션 대체. |
| `server.py` | FastAPI + WebSocket 서버. 상태의 주인. 시뮬레이션 측정값을 주기적으로 push. |
| `INTERFACE.md` | 화면 ↔ 서버 통신 계약(메시지·상태 스키마) 문서. |
| `config.json` | 채널 설정 자동 저장/로드. |
| `recipes/` | 레시피 JSON 저장 폴더. |

화면 ↔ 서버 사이의 모든 약속은 [`INTERFACE.md`](INTERFACE.md)에 정의되어 있다.

## 실행 방법

### 1. 의존성 설치

```bash
pip install -r requirements.txt
```

### 2. 실행

```bash
python server.py
```

실행하면 "Gas Sensor Measurement System" 창이 뜨고, 서버가 보내는 측정값이
실시간으로 흐른다. 밸브 클릭, SV/MAX 변경, 4-way 전환, AUTO RUN/STOP/PURGE,
System Setup 적용, 레시피 저장·불러오기가 서버와 실제로 명령을 주고받는다.

## 1단계 범위 (현재)

- **하드웨어는 연결하지 않는다.** 측정값은 모두 `server.py`의 **시뮬레이션** 데이터다.
- 장비만 없을 뿐 화면 ↔ 연동 ↔ 서버 구조는 진짜로 동작한다.
- 추후 단계에서 `server.py` 안쪽(시뮬레이션 부분)만 실제 장비 제어 코드로 교체할 예정이다.

서버 연결이 끊기면 화면은 "연결 끊김"을 표시하고 마지막 값을 유지하며,
브라우저 내부 시뮬레이션 모드로 전환된다(2초마다 자동 재연결 시도).
