# BUILD.md — 납품용 exe 빌드 안내

Gas Sensor Measurement System을 PyInstaller로 묶어 고객사에 납품하는 절차.

## 1. 빌드

**깨끗한 가상환경에서 빌드할 것** (번들 크기·의존성 오염 방지).
개발 PC에 설치된 무관한 패키지(matplotlib, Jupyter 등)가 번들에 딸려 들어가면
결과물이 수십 MB 커진다.

```
python -m venv .venv-build
.venv-build\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller build.spec --clean --noconfirm
```

> ⚠️ **의존성은 반드시 `requirements.txt`의 버전 그대로 설치한다. 특히 pymodbus는 3.6.9 고정 —
> 이후 버전은 호출 규약이 달라 PLC 통신이 되지 않는다.**
> 버전을 올려 빌드하면 exe는 정상으로 뜨지만 현장에서 통신만 안 되는, 원인을 찾기 어려운
> 상태가 된다. `pip install -U` 로 임의 갱신하지 말 것.

`dist/GasSensor/` 아래에 결과물이 생성된다.

> `build.spec` 의 `excludes` 목록(PyQt5/PyQt6, matplotlib, tkinter 등)은 개발 PC에서
> 그대로 빌드할 때를 위한 안전장치다. 위처럼 깨끗한 venv에서 빌드하면 애초에
> 설치돼 있지 않으므로 대부분 불필요해진다. 그래도 남겨 둔다(해가 없다).

## 1-2. 버전 관리

버전 문자열은 `backend/version.py` 한 곳에서 관리한다. **빌드 전에 갱신할 것.**

```python
APP_VERSION = "1.0.0"
BUILD_DATE  = "2026-08-05"
```

이 값은 세 곳에 나타난다 — 기동 콘솔 로그, 파일 로그(`logs/measurement-*.log`) 첫 줄,
화면 헤더 우측의 작은 `v1.0.0` 표시. 고객이 로그만 보내와도 어느 빌드인지 특정할 수 있다.

## 2. 결과물 구조

```
dist/GasSensor/
├── GasSensor.exe
├── _internal/          ← PyInstaller 내부(건드리지 말 것)
│   └── calib/          ← 빌드에 동봉된 기본 보정표(읽기 전용)
├── config.json         ← ★ 수동 동봉 (미리 세팅한 것)
├── recipes/            ← ★ 수동 동봉 (기본 레시피)
└── calib/              ← 현장 실측 보정표(있으면 _internal/calib/ 보다 우선)
```

`config.json` · `recipes/` · `calib/` 는 **빌드에 포함되지 않는다.** 번들에 넣으면
읽기 전용 임시 폴더로 들어가 저장한 값이 종료 시 사라진다(`backend/paths.py` 참고).
반드시 위 위치에 손으로 복사해 동봉한다.

프로그램이 읽고 쓰는 위치는 다음과 같다.

| 대상 | 위치 | 성격 |
|---|---|---|
| `frontend/` (HTML·CSS·JS) | `_internal/frontend/` | 번들 자원, 읽기 전용 |
| `config.json` | exe와 같은 폴더 | 사용자 데이터, 쓰기 |
| `recipes/`, `logs/` | exe와 같은 폴더 | 사용자 데이터, 쓰기 |
| `calib/` (현장 보정표) | exe와 같은 폴더 | 현장 교체본, **우선 적용** |
| `_internal/calib/` (기본 보정표) | 번들 안 | 빌드 동봉, 읽기 전용 폴백 |

`recipes/`·`logs/`·`calib/` 는 없으면 프로그램이 자동으로 만든다.
`calib/` 가 비어 있으면 PV 는 기존 선형 변환을 쓴다(CONFIG.md 의 "PV 보정표" 절 참고).

## 3. 설치 위치 주의

`C:\Program Files\` 아래는 일반 사용자 쓰기 권한이 막혀 있어 **설정 저장이 실패한다.**
다음과 같은 경로에 설치할 것을 권장한다.

```
C:\VANAM\GasSensor\
```

설치 위치는 쓰기 가능한 폴더(권장: `C:\VANAM\GasSensor` — 사내 관례가 없으면 `C:\GasSensor` 도 무방)로 한다 —
`Program Files` 는 config/logs 저장이 막혀 시작 시 경고가 뜬다(관리자 권한 강제 대신 이 방식.
정책상 관리자 실행이 필요하면 `build.spec` 의 `uac_admin=True` 주석을 해제한다).

## 4. 선행 조건

- **Microsoft Edge WebView2 런타임** — 화면 표시에 필요하다.
  Windows 11에는 대개 기본 포함돼 있으나, 없으면 실행 시 안내 창이 뜬다.
  미설치 시 Microsoft 배포 페이지에서 Evergreen Runtime을 설치한다.
- 프로그램은 `127.0.0.1:8000` 을 사용한다. 이미 점유 중이면 8001~8009 중
  비어 있는 포트를 자동으로 찾아 쓴다.

## 5. 콘솔 창 숨기기

`build.spec` 의 `console=True` 는 디버깅용이다. 실행 시 검은 콘솔 창이 함께 뜬다.
최종 배포판은 `console=False` 로 바꿔 다시 빌드한다.

문제 진단 중에는 `True`로 두면 오류 메시지를 볼 수 있어 유리하다.

## 6. 납품 전 체크리스트

- [ ] `backend/version.py` 의 `APP_VERSION` · `BUILD_DATE` 갱신 (빌드 전)
- [ ] `config.json` 의 `plc.port` 에 현장 COM 포트 입력
- [ ] `plc.mode` 가 `serial` 인지 확인
- [ ] `config.json` 의 `sv_out`/`pv_in` 이 실배선과 일치하는지 확인 (→ `CONFIG.md`)
- [ ] 채널별 스케일(`fs_sccm`/`sv_full`/`pv_zero`/`pv_full`)이 MFC 명판과 일치
- [ ] exe 실행 → 설정 변경 → 종료 후 재실행 → 설정 유지 확인  ★핵심
- [ ] `logs/` 폴더에 로그 파일이 생기는지 확인
- [ ] **현장 실측 보정표가 최신인지 확인** — 로그의 `PV 보정표 로드 — VA5 21점 (전체경로)`
      줄로 exe 옆 `calib/` 이 쓰였는지, `_internal/calib/` 기본값이 쓰였는지 판별한다
      (재빌드 후 옛 현장 파일이 새 번들값을 덮는 혼선 방지)
