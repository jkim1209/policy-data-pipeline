"""collect_wb.py 를 fake_api.py 의 7개 장애 시나리오에 돌립니다.

행 수만 세지 않습니다. 조용한 실패는 행 수가 맞는데 내용이 비는 형태로 오기
때문입니다. 지역 정보 소실 · 연도 커버리지 · 호출 횟수를 함께 봅니다.

대조군으로 '나이브 수집'을 같이 돌립니다. 방어가 없을 때 무엇이 사라지는지
보이지 않으면, 견고한 쪽이 멈춘 것이 왜 좋은 결과인지 알 수 없습니다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import collect_wb as C
from fake_api import SCENARIOS, FakeAPI

EXPECTED_YEARS = list(range(2015, 2026))
EXPECTED_ROWS = len(EXPECTED_YEARS)


def naive(api):
    """방어 없는 수집. 문서만 읽고 짜면 대개 이 모양이 됩니다."""
    r = api.get("https://api.worldbank.org/v2/country/KOR/indicator/SP.DYN.TFRT.IN",
                params={"format": "json", "per_page": 500}, timeout=30)
    payload = r.json()
    rows = payload[1]
    return [{"region_code": x.get("countryiso3code"),
             "period": int(x["date"]), "value": x["value"]} for x in rows]


def inspect(rows):
    """수집 결과에서 소실 여부를 뽑습니다."""
    blank = sum(1 for r in rows if not r.get("region_code"))
    regions = sorted({r.get("region_code") for r in rows if r.get("region_code")})
    years = sorted({r["period"] for r in rows})
    lost_years = [y for y in EXPECTED_YEARS if y not in years]
    return {
        "행수": len(rows),
        "지역_빈행": blank,
        "지역_고유값": regions or ["(없음)"],
        "연도수": len(years),
        "누락연도": lost_years,
    }


def run(fn, scenario):
    api = FakeAPI(scenario)
    try:
        rows = fn(api)
        return {"결과": "정상", "호출": api.calls, "메시지": "", **inspect(rows)}
    except C.CollectError as e:
        return {"결과": "중단", "호출": api.calls, "메시지": str(e).split("\n")[0],
                "행수": 0, "지역_빈행": 0, "지역_고유값": [], "연도수": 0,
                "누락연도": EXPECTED_YEARS}
    except Exception as e:  # noqa: BLE001 — 나이브 쪽이 터지는 것도 결과입니다
        return {"결과": f"예외({type(e).__name__})", "호출": api.calls, "메시지": str(e)[:60],
                "행수": 0, "지역_빈행": 0, "지역_고유값": [], "연도수": 0,
                "누락연도": EXPECTED_YEARS}


def verdict(r):
    """조용한 실패 판정: 에러 없이 끝났는데 결과가 틀린 경우."""
    if r["결과"] != "정상":
        return "중단(안전)" if r["결과"] == "중단" else "예외(드러남)"
    bad = []
    if r["행수"] != EXPECTED_ROWS:
        bad.append(f"{EXPECTED_ROWS - r['행수']}행 유실")
    if r["지역_빈행"]:
        bad.append(f"지역 {r['지역_빈행']}행 소실")
    return "**조용한 실패** — " + ", ".join(bad) if bad else "정상"


def table(title, fn, cols):
    print(f"\n### {title}\n")
    print("| 시나리오 | " + " | ".join(cols) + " |")
    print("|:--|" + "".join("---:|" if c in ("행수", "호출", "지역_빈행", "연도수") else ":--|"
                            for c in cols))
    results = {}
    for sc in SCENARIOS:
        r = run(fn, sc)
        results[sc] = r
        cells = []
        for c in cols:
            v = r["판정"] if c == "판정" else r.get(c, "")
            if c == "지역_고유값":
                v = ", ".join(v) if v else "—"
            elif c == "누락연도":
                v = "없음" if not v else (f"{len(v)}개 {v[:4]}…" if len(v) > 4 else str(v))
            elif c == "메시지":
                v = (v[:52] + "…") if len(str(v)) > 52 else v
            cells.append(str(v))
        print("| " + sc + " | " + " | ".join(cells) + " |")
    return results


def main():
    C.RATE_LIMIT_WAIT, C.BACKOFF = 0.01, 1.0   # 테스트 시간 단축. 로직은 그대로

    def robust(api):
        return C.collect("KOR", "SP.DYN.TFRT.IN", 2015, 2025, "NA_NOTSURVEYED",
                         session=api, per_page=500, log=lambda m: None)

    print(f"기대값: {EXPECTED_ROWS}행 ({EXPECTED_YEARS[0]}~{EXPECTED_YEARS[-1]}), 지역 KOR")

    rb = table("collect_wb.py (견고한 수집)", robust,
               ["결과", "행수", "지역_빈행", "지역_고유값", "연도수", "누락연도", "호출", "메시지"])
    nv = table("대조군 · 나이브 수집 (방어 없음)", naive,
               ["결과", "행수", "지역_빈행", "지역_고유값", "연도수", "누락연도", "호출"])

    print("\n### 조용한 실패 판정\n")
    print("| 시나리오 | 나이브 | collect_wb.py |")
    print("|:--|:--|:--|")
    n_silent = r_silent = 0
    for sc in SCENARIOS:
        a, b = verdict(nv[sc]), verdict(rb[sc])
        n_silent += a.startswith("**")
        r_silent += b.startswith("**")
        print(f"| {sc} | {a} | {b} |")
    print(f"\n조용한 실패: 나이브 {n_silent}건 → collect_wb.py {r_silent}건")
    return 0 if r_silent == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
