"""데이터를 일부러 망가뜨려 검증이 정말로 중단하는지 확인합니다.

검증 코드를 짜놓고 통과하는 것만 보면, 그 코드가 실제로 잡아내는지 알 수 없습니다.
검사가 통과했다는 사실과 검사가 작동한다는 사실은 다릅니다.

references/schema.md 의 중단 조건 7가지에 각각 대응하는 결함을 주입하고,
7건 모두에서 산출물이 만들어지지 않는지 확인합니다. 하나라도 통과하면
그 검사는 구멍입니다(exit 1).

원본 패널은 건드리지 않습니다. 메모리에서 복제해 망가뜨립니다.
"""
from __future__ import annotations

import argparse
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import validate_panel as V  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
E04 = os.path.join(BASE, "exercises", "04")


# ── 결함 주입기. 각각 중단 조건 하나를 겨냥합니다.
def break_empty(rows):
    """1) 수집 행 수 0 — 조용한 실패의 전형."""
    return []


def break_column(rows):
    """2) 표준 스키마 컬럼 누락 — missing_reason 열이 통째로 빠진 경우."""
    for r in rows:
        r.pop("missing_reason", None)
    return rows


def break_duplicate(rows):
    """3) 중복 키 — 같은 지표·지역·연도가 두 번. 재수집 후 이어붙이다 흔히 납니다."""
    rows.append(copy.deepcopy(rows[0]))
    return rows


def break_range(rows):
    """4) 값 범위 이탈 — 합계출산율 42.0. 단위 환산 실수의 전형입니다."""
    for r in rows:
        if r["indicator_code"] == "TFR" and r["value"] is not None:
            r["value"] = 42.0
            break
    return rows


def break_region(rows):
    """5) 시도 17개 미충족 — 크롤링이 조용히 일부만 긁어온 경우."""
    drop = {"KR-50", "KR-46", "KR-47"}
    return [r for r in rows if r["region_code"] not in drop]


def break_period(rows):
    """6) 기간 연속성 위반 — 페이징을 놓쳐 중간 연도가 빈 경우."""
    return [r for r in rows if not (r["source"] == "WORLDBANK" and r["period"] in (2019, 2020))]


def break_reason(rows):
    """7) 사유 없는 결측 — 빈칸 하나로 뭉갠 경우. 가장 조용합니다."""
    for r in rows:
        if r["value"] is None:
            r["missing_reason"] = None
            break
    return rows


def break_norange(rows):
    """보너스 — 허용 범위가 등록되지 않은 지표. 검사를 통과한 게 아니라 안 한 것."""
    for r in rows:
        if r["indicator_code"] == "TFR":
            r["indicator_code"] = "TFR_NEW"
    return rows


CASES = [
    ("① 행 수 0", "수집 0건인데 정상 종료", break_empty),
    ("② 컬럼 누락", "missing_reason 열 제거", break_column),
    ("③ 중복 키", "같은 지표·지역·연도 2행", break_duplicate),
    ("④ 값 범위 이탈", "TFR 한 건을 42.0 으로", break_range),
    ("⑤ 시도 17개 미충족", "시도 3개 삭제", break_region),
    ("⑥ 기간 연속성 위반", "WORLDBANK 2019·2020 삭제", break_period),
    ("⑦ 사유 없는 결측", "결측 한 건의 사유 제거", break_reason),
    ("＋ 허용 범위 미등록", "등록 안 된 지표 코드로 변경", break_norange),
]


def main(argv=None):
    ap = argparse.ArgumentParser(description="중단 조건 파손 시험")
    ap.add_argument("panel", nargs="?", default=os.path.join(E04, "panel.csv"))
    ap.add_argument("--show", help="이 번호의 결함만 자세히 출력 (1~8)", type=int)
    a = ap.parse_args(argv)

    base = V.load_panel(a.panel)
    config = V.load_config()

    # 원본은 먼저 통과해야 합니다. 그래야 '망가뜨려서 실패했다'가 성립합니다.
    _res, _t, blocking = V.run(base, config, log=lambda m: None)
    print(f"원본 패널 {len(base)}행 — 검증 {'통과' if not blocking else '실패'}")
    if blocking:
        print("원본이 이미 실패합니다. 파손 시험 전에 원인을 해결하세요.", file=sys.stderr)
        return 1

    print("\n| # | 주입한 결함 | 내용 | 검증 | 걸린 검사 | 산출물 |")
    print("|:--|:--|:--|:--|:--|:--|")

    leaks = []
    for i, (name, desc, fn) in enumerate(CASES, 1):
        rows = fn(copy.deepcopy(base))
        try:
            results, _tally, blocking = V.run(rows, config, log=lambda m: None)
        except Exception as e:  # noqa: BLE001 — 예외로 죽는 것도 '멈춤'입니다
            print(f"| {i} | {name} | {desc} | **예외** | {type(e).__name__} | 만들지 않음 |")
            continue

        if blocking:
            caught = ", ".join(b["검사"] for b in blocking)
            print(f"| {i} | {name} | {desc} | **중단** | {caught[:46]} | 만들지 않음 |")
        else:
            leaks.append(name)
            print(f"| {i} | {name} | {desc} | 통과 | **없음 — 구멍** | 만들어짐 |")

        if a.show == i:
            print()
            V.print_table(results)
            print()

    print(f"\n{len(CASES)}건 중 중단 {len(CASES) - len(leaks)} · 빠져나감 {len(leaks)}")
    if leaks:
        print(f"**검증에 구멍이 있습니다: {leaks}**", file=sys.stderr)
        return 1
    print("모든 결함이 산출물 생성 전에 걸렸습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
