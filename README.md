# Gas Sensor Measurement System

가스 밸브·MFC를 제어해 측정 장비에 가스를 일정하게 공급하는 제어 프로그램.
LS XGB PLC(XBC-DR30SU)와 Modbus로 통신한다. (측정 데이터 기록은 별도 장비가 담당한다.)

## 폴더 구조

```
gas-sensor-measurement/
├── config.json                   # 채널 설정·PLC 통신·하드웨어 구성 (사용자 데이터)
├── recipes/                      # 저장된 레시피
├── logs/                         # 파일 로그(자동 생성)
├── build.spec                    # PyInstaller 빌드 설정
├── frontend/                     # 화면 (서버를 모름)
│   ├── index.html                #   마크업
│   ├── css/style.css             #   스타일
│   └── js/ schematic.js          #   배관도(채널/밸브) 렌더 + drawBuses
│         recipe.js               #   레시피 표 + System Setup 모달(스케일·배정 표시)
│         core.js                 #   헤더/상태/로그·서버상태 반영·fit/도크/종료모달·초기화
│         app.js                  #   서버 연동(WebSocket/명령 전송, 끊김 시 값 '—')
├── backend/                      # 서버 (상태의 주인)
│   ├── server.py                 #   진입점: FastAPI·라우트·WebSocket·lifespan
│   ├── window.py                 #   pywebview 창 생성·종료, 포트 탐색, WebView2 안내
│   ├── loops.py                  #   백그라운드 주기 태스크(telemetry·PLC 폴링·PLC 쓰기)
│   ├── paths.py                  #   실행 환경별 경로 해석(BUNDLE_ROOT / DATA_ROOT)
│   ├── state.py                  #   State(channels/system/recipe) + config 로드/저장·배정 검증
│   ├── commands.py               #   handle_command(화면 명령 처리)
│   ├── engine.py                 #   레시피 자동 실행(단계 진행·PLC 이상 감시)
│   ├── recipe_calc.py            #   한 단계 → 채널별 목표 SV 계산 + 검증
│   ├── plc.py                    #   Modbus 클라이언트(블록 읽기/쓰기·스케일 변환·하트비트)
│   ├── plc_catalog.py            #   래더가 제공하는 채널 목록(고정 계약)
│   ├── storage.py                #   레시피/설정 파일 I/O(원자적 쓰기·검증·목록)
│   ├── logger.py                 #   파일 로그(날짜별 회전·보관일수)
│   ├── connection.py             #   ConnectionManager + push_state/push_log
│   └── version.py                #   프로그램 버전(배포 시 갱신)
└── test/                         # PLC 없이 검증하는 도구
    ├── fake_plc.py               #   가짜 PLC(Modbus TCP 슬레이브 + 안전 로직 에뮬레이터)
    └── test_plc.py               #   PLC 하드웨어 테스트(터미널 메뉴, 코일/ADC/DAC 점검)
```

## 문서 지도

| 문서 | 대상 | 내용 |
|---|---|---|
| [`INTERFACE.md`](INTERFACE.md) | 개발자 | 화면 ↔ 서버 약속(DOM 표식·전역 함수·WebSocket 메시지) |
| [`CONFIG.md`](CONFIG.md) | 현장 담당자 | `config.json` 편집 — 채널 배정·스케일·하드웨어 구성 |
| [`BUILD.md`](BUILD.md) | 납품 담당자 | exe 빌드 절차·배포 구성·납품 체크리스트 |

## 실행 방법

```bash
pip install -r requirements.txt
python backend/server.py        # 프로젝트 루트에서
```

실행하면 "Gas Sensor Measurement System" 창이 최대화로 뜬다. 밸브 클릭, SV/MAX 변경,
4-way 전환, AUTO RUN/STOP/PURGE, System Setup 적용, 레시피 저장·불러오기가
서버와 실제로 명령을 주고받는다.

`127.0.0.1:8000`을 사용하며, 이미 점유 중이면 8001~8009 중 빈 포트를 자동으로 찾는다.

## 배포 구조

PyInstaller(onedir)로 묶으면 폴더가 둘로 갈라진다.

```
GasSensor/
├── GasSensor.exe
├── _internal/          PyInstaller 내부 (frontend/ 포함, 읽기 전용)
├── config.json         ★ 설정(수동 동봉)
├── recipes/            ★ 레시피
└── logs/               자동 생성
```

- **BUNDLE_ROOT** (읽기 전용) — exe에 묶여 임시 폴더에 풀린다. `frontend/`가 여기 있다.
- **DATA_ROOT** (쓰기) — exe가 놓인 폴더. `config.json`·`recipes/`·`logs/`가 여기 있고
  종료해도 보존된다.

경로 해석은 `backend/paths.py`가 담당한다. 개발 환경에서는 둘 다 프로젝트 루트로 같다.
`C:\Program Files\` 아래에 설치하면 쓰기가 막혀 설정이 저장되지 않으므로
`C:\VANAM\GasSensor\` 같은 경로를 권장한다(기동 시 경고가 뜬다).

## PLC 연동

- **통신** — Modbus RTU(시리얼) 또는 TCP. 국번 1, 115200 / 8 / N / 1.
- **주소맵** (base 0)

  | 용도 | 주소 |
  |---|---|
  | 밸브 지령 코일 VA1~VA8, 4-Way | 160~167, 168 |
  | SV(목표유량) 홀딩 레지스터 | D100~D107 |
  | PV(실측유량) 홀딩 레지스터 | D200~D207 |
  | 상태·알람 코일 | 320~323, 336~339 |

  읽기는 블록 2요청(PV 일괄 + 상태 일괄), 쓰기도 블록 2프레임(SV → 밸브)으로 묶어
  하트비트가 밀리지 않게 한다.

- **채널 배정** — 주소를 직접 쓰지 않는다. `plc_catalog.py`가 정의한 이름
  (`DAC1_CH0`, `ADC_CH4` …)을 `config.json`의 `sv_out` / `pv_in`에 지정한다.
  밸브 코일은 채널 id로 자동 결정되어 config에 없다. 자세한 내용은 [`CONFIG.md`](CONFIG.md).

- **안전 구조** (PLC 래더가 보장)
  - **RUN_PERMIT 래치** — 트립되면 수동 리셋(SAFETY_RESET 펄스) 전까지 재가동하지 않는다.
  - **하트비트 3초** — 통신이 3초 끊기면 PLC가 통신두절로 트립한다.
  - **공압 10초** — 공압 상실이 10초 지속되면 트립한다.
  - 안전정지가 걸리면 서버도 밸브·유량을 닫고, 해제해도 자동으로 다시 열지 않는다.
  - 레시피 실행 중 안전정지·통신두절이 감지되면 즉시 중단하고 "이 측정은 무효입니다"를 남긴다.

## 현재 범위

- PLC 제어 경로는 실제로 동작한다. 측정 하드웨어(RH·SMU)는 없어 화면에 "—"로 표시된다.
- **측정값은 전부 실측이다.** PLC로 읽은 값이 없으면 화면에 `—`로 표시하며, 가짜 값을
  만들지 않는다(운전자가 "유량이 흐른다"고 오인하지 않도록).
- PLC 미연결 상태에서는 AUTO RUN·PURGE가 거부된다(물리적으로 아무 일도 일어나지 않으므로).
- PLC 없이 동작을 시험하려면 `test/fake_plc.py`(가짜 PLC 슬레이브)를 띄우고 TCP로 붙인다.
- 서버 연결이 끊기면 헤더에 "서버 연결 끊김"을 크게 표시하고 모든 측정값이 `—`가 된다
  (2초마다 자동 재연결 시도). 조작을 시도하면 오프라인 경고만 나온다.
- DV04A가 1대뿐이라 SV 출력은 4채널이다. VA5~VA8은 증설 후 배정한다([`CONFIG.md`](CONFIG.md) ⑤).
