"""
pv_calib.py — PV(유량 측정) 보정표 로더.

실기에서 MFC 유량 출력이 풀스케일의 약 63%(≈3.2V)에서 포화한다(원인 조사 중 —
전원·유량 포화는 배제됨). 그 상태에서도 PV 가 전 구간에서 실제 유량에 대응하도록
실측 DAC-ADC 매핑을 표로 두고 구간 선형 보간한다.

★ 이 표는 '현재 하드웨어 상태'의 실측이다. 배선·모듈 원인이 해결되면 재측정해 교체할 것.
★ SV(지령) 경로에는 쓰지 않는다 — DAC 는 전 구간 선형임이 유량계로 확인됐다.

파일: calib/<채널ID>.csv   (예: calib/VA5.csv)
  탐색 순서 — ① 현장: <DATA_ROOT>/calib/ (exe 옆, 교체본)  ② 번들 동봉: <BUNDLE_ROOT>/calib/
  ★ 어느 쪽이 쓰였는지는 로그에 남는 '전체 경로'로 판별한다.
  - 인코딩 utf-8-sig(엑셀 BOM 허용), CRLF 허용
  - '#' 로 시작하는 줄과 빈 줄은 무시(엑셀이 큰따옴표로 감싼 주석도 포함)
  - 첫 필드가 숫자가 아닌 줄(제목·설명)은 조용히 건너뛴다 — 헤더 판별은 그보다 먼저 한다
  - 파싱은 표준 csv 모듈로 한다(따옴표·빈 필드 처리를 맡긴다)
  - 헤더 'dac,adc'  → 첫 열은 DAC 카운트  (sccm = dac / sv_full * fs_sccm 로 환산)
    헤더 'sccm,adc' → 첫 열을 sccm 으로 그대로 사용
    헤더가 없으면 'dac,adc' 로 본다
  - 구분자는 쉼표·탭·공백 모두 허용

★ 표가 있는 채널은 pv_zero/pv_full 을 쓰지 않는다(표가 그 역할을 대신한다).
"""

import csv
import io
import os

from paths import CALIB_DIR, CALIB_BUNDLED


def _rows(text: str):
    """표준 csv 모듈로 읽는다 — 엑셀은 주석에 쉼표가 있으면 줄 전체를 큰따옴표로 감싸고
    뒤에 빈 필드를 붙인다(실파일: "# MFC1 PV 보정 — 2026-08-19 실측, AD08A 0~5V 설정",).
    직접 split 하면 그 줄이 '#' 이 아니라 '"' 로 시작해 주석으로 안 걸린다.
    구분자는 쉼표를 기본으로 하되 탭·세미콜론만 쓰인 파일도 받는다."""
    sample = text[:4096]
    delim = ","
    if "," not in sample:
        for d in ("\t", ";"):
            if d in sample:
                delim = d
                break
    return csv.reader(io.StringIO(text), delimiter=delim)


def _cell(v: str) -> str:
    """앞뒤 공백·따옴표를 벗긴다(엑셀이 남긴 따옴표까지)."""
    return (v or "").strip().strip('"').strip("'").strip()


def path_of(channel_id: str) -> str:
    """실제로 쓸 파일 경로. 현장(exe 옆 calib/)이 번들 동봉본보다 우선한다.
    ★ 어느 쪽이 적용됐는지는 로그의 전체 경로로 판별한다 — 재빌드 후 옛 현장 파일이
      새 번들값을 덮는 혼선을 막기 위한 것이다."""
    site = os.path.join(CALIB_DIR, f"{channel_id}.csv")
    if os.path.isfile(site):
        return site
    return os.path.join(CALIB_BUNDLED, f"{channel_id}.csv")


def load(channel_id: str, sv_full, fs_sccm, pv_zero):
    """보정표를 읽어 [(adc, sccm), ...] 로 돌려준다.

    반환: (points | None, note)
      points 가 None 이면 표를 쓰지 않는다(선형 폴백). note 는 로그용 진단 문자열이며,
      파일이 아예 없으면 note 도 "" 다(시작 로그 소음을 만들지 않는다 — 커밋 52 방침).
    """
    p = path_of(channel_id)
    if not os.path.isfile(p):
        return None, ""
    try:
        with open(p, "r", encoding="utf-8-sig", newline="") as f:
            text = f.read()
    except Exception as e:  # noqa: BLE001
        return None, f"{channel_id}: 보정표를 읽지 못했습니다 ({e}) — 선형 변환을 사용합니다"

    try:
        svf = float(sv_full or 0)
        fss = float(fs_sccm or 0)
        zero = float(pv_zero or 0)
    except (TypeError, ValueError):
        svf = fss = zero = 0.0

    mode = "dac"          # 첫 열의 뜻
    pts = []
    extra_cols = False    # 데이터 줄에 숫자 열이 3개 이상 — 여러 채널을 한 장에 담은 원본일 수 있다
    for cells in _rows(text):
        if not cells:
            continue
        first_s = _cell(cells[0])
        # 헤더 판별은 건너뛰기 '전에' 한다(sccm 형식을 놓치면 값이 통째로 틀어진다).
        head = first_s.lower()
        if head in ("dac", "sccm"):
            mode = head
            continue
        if first_s.startswith("#"):        # 주석(엑셀이 따옴표로 감싼 것도 여기서 걸린다)
            continue
        if len(cells) < 2:
            continue
        try:
            first = float(first_s)
        except ValueError:
            continue                        # 숫자가 아닌 줄(제목·설명)은 조용히 건너뛴다
        try:
            adc = float(_cell(cells[1]))
        except ValueError:
            continue
        if mode == "dac":
            if svf <= 0 or fss <= 0:
                return (None,
                        f"{channel_id}: dac 형식 보정표에는 sv_full·fs_sccm 이 필요합니다 — 선형 변환을 사용합니다")
            sccm = first / svf * fss
        else:
            sccm = first
        if not extra_cols:
            n_num = 0
            for cc in cells:
                try:
                    float(_cell(cc))
                    n_num += 1
                except ValueError:
                    pass
            if n_num > 2:
                extra_cols = True
        pts.append((adc, sccm))

    if len(pts) < 2:
        return None, f"{channel_id}: 보정표의 유효한 점이 2개 미만입니다 — 선형 변환을 사용합니다"

    # 무유량 지점이 없으면 저유량 구간이 정의되지 않는다 → (pv_zero, 0) 을 앞에 채운다.
    if pts[0][0] > zero:
        pts.insert(0, (zero, 0.0))

    for k in range(1, len(pts)):
        if pts[k][0] <= pts[k - 1][0]:
            return (None,
                    f"{channel_id}: 보정표의 adc 열이 증가하지 않습니다 "
                    f"({pts[k - 1][1]:g}sccm→{pts[k - 1][0]:g} / {pts[k][1]:g}sccm→{pts[k][0]:g}) "
                    f"— 같은 adc 가 두 유량에 대응하면 되돌릴 수 없습니다. "
                    f"포화 구간의 중복 행을 지우세요. 선형 변환을 사용합니다")
        if pts[k][1] < pts[k - 1][1]:
            return (None,
                    f"{channel_id}: 보정표의 유량 열이 감소합니다 "
                    f"({pts[k - 1][1]:g} → {pts[k][1]:g}) — 선형 변환을 사용합니다")

    note = f"PV 보정표 로드 — {channel_id} {len(pts)}점 ({p})"
    if extra_cols:
        note += (f" ⚠ 숫자 열이 3개 이상입니다 — 여러 채널을 한 장에 담은 표라면"
                 f" 앞 두 열({channel_id} 것인지) 확인하세요. 앞 두 열만 사용합니다")
    return pts, note


def interp(points, adc: float) -> float:
    """구간 선형 보간. 표 범위를 넘으면 끝 값으로 클램프한다.

    ★ 외삽하지 않는다 — 출력이 평탄해진 구간에서 기울기를 늘리면 유량이 발산한다.
    """
    import bisect
    xs = [p[0] for p in points]
    if adc <= xs[0]:
        # 첫 점 아래는 (첫 구간의 기울기로) 0 방향으로 내려가되 음수는 자르지 않는다 —
        # 최종 클램프는 호출부(_pv_to_sccm)가 한다.
        x0, y0 = points[0]
        x1, y1 = points[1]
        if x1 == x0:
            return y0
        return y0 + (adc - x0) * (y1 - y0) / (x1 - x0)
    if adc >= xs[-1]:
        return points[-1][1]
    k = bisect.bisect_right(xs, adc)
    x0, y0 = points[k - 1]
    x1, y1 = points[k]
    if x1 == x0:
        return y1
    return y0 + (adc - x0) * (y1 - y0) / (x1 - x0)
