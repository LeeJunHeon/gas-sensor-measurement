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

`dist/GasSensor/` 아래에 결과물이 생성된다.

> `build.spec` 의 `excludes` 목록(PyQt5/PyQt6, matplotlib, tkinter 등)은 개발 PC에서
> 그대로 빌드할 때를 위한 안전장치다. 위처럼 깨끗한 venv에서 빌드하면 애초에
> 설치돼 있지 않으므로 대부분 불필요해진다. 그래도 남겨 둔다(해가 없다).

## 2. 결과물 구조

```
dist/GasSensor/
├── GasSensor.exe
├── _internal/          ← PyInstaller 내부(건드리지 말 것)
├── config.json         ← ★ 수동 동봉 (미리 세팅한 것)
├── admin.json          ← ★ 수동 동봉 (장비별 관리자 인증, 추후 기능)
└── recipes/            ← ★ 수동 동봉 (기본 레시피)
```

`config.json` · `admin.json` · `recipes/` 는 **빌드에 포함되지 않는다.** 번들에 넣으면
읽기 전용 임시 폴더로 들어가 저장한 값이 종료 시 사라진다(`backend/paths.py` 참고).
반드시 위 위치에 손으로 복사해 동봉한다.

프로그램이 읽고 쓰는 위치는 다음과 같다.

| 대상 | 위치 | 성격 |
|---|---|---|
| `frontend/` (HTML·CSS·JS) | `_internal/frontend/` | 번들 자원, 읽기 전용 |
| `config.json`, `admin.json` | exe와 같은 폴더 | 사용자 데이터, 쓰기 |
| `recipes/`, `logs/` | exe와 같은 폴더 | 사용자 데이터, 쓰기 |

`recipes/` 와 `logs/` 는 없으면 프로그램이 자동으로 만든다.

## 3. 설치 위치 주의

`C:\Program Files\` 아래는 일반 사용자 쓰기 권한이 막혀 있어 **설정 저장이 실패한다.**
다음과 같은 경로에 설치할 것을 권장한다.

```
C:\VANAM\GasSensor\
```

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

- [ ] `config.json` 의 `plc.port` 에 현장 COM 포트 입력
- [ ] `plc.mode` 가 `serial` 인지 확인
- [ ] `config.json` 의 `sv_out`/`pv_in` 이 실배선과 일치하는지 확인 (→ `CONFIG.md`)
- [ ] 채널별 스케일(`fs_sccm`/`sv_full`/`pv_zero`/`pv_full`)이 MFC 명판과 일치
- [ ] exe 실행 → 설정 변경 → 종료 후 재실행 → 설정 유지 확인  ★핵심
- [ ] `logs/` 폴더에 로그 파일이 생기는지 확인
