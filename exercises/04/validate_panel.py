"""references/schema.md 의 중단 조건을 전부 검사합니다.

중단 조건 7가지 — 하나라도 걸리면 산출물을 만들지 않습니다.
    1) 수집 행 수 0
    2) 표준 스키마 컬럼 누락
    3) 중복 키 (indicator_code + region_code + period)
    4) 지표별 값 범위 이탈
    5) 시도 17개 미충족
    6) 기간 연속성 위반
    7) 사유 없는 결측

기록만 하고 진행하는 것 2가지 — 원자료가 실제로 그럴 수 있기 때문입니다.
    8) 전년 대비 변화율 30% 초과
    9) 전국값과 시도 평균의 괴리

검사를 느슨하게 고쳐 통과시키지 않습니다. 임계값은 config/sources.yml 에 있고,
바꾸려면 근거를 대고 사용자 확인을 받습니다(CLAUDE.md).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# references/schema.md 의 10개 컬럼
REQUIRED_COLUMNS = {"source", "indicator_code", "region_code", "period", "value", "unit",
                    "vintage", "retrieved_at", "source_url", "missing_reason"}

# 기간 연속성 — 소스마다 담당 구간이 다릅니다.
# config 의 expected_years 는 KOSIS 를 전제로 하는데 이 패널에는 KOSIS 가 없어
# 소스별로 명시합니다. 구간을 여기 적지 않은 소스는 검사 대상이 아닙니다.
PERIOD_SPEC = {
    "WORLDBANK": (2015, 2025),
    "KOSIS": (2015, 2025),
    "INTERNAL": (2023, 2025),
    "POLICYBRIEF": (2026, 2026),
}


def _add(results, name, ok, on_fail, note=""):
    results.append({
        "검사": name,
        "결과": "통과" if ok else ("실패" if on_fail == "중단" else "확인"),
        "실패 시": on_fail,
        "비고": note,
    })
    return ok


def run(rows, config, log=print):
    v = config["validation"]
    results = []

    # 1) 행 수 0
    _add(results, "행 수 0 아님", len(rows) > 0, "중단", f"{len(rows)}행")
    if not rows:
        return results, _tally(results), [r for r in results if r["결과"] == "실패"]

    # 2) 표준 스키마 컬럼 누락
    missing_cols = REQUIRED_COLUMNS - set(rows[0].keys())
    _add(results, "표준 스키마 컬럼 존재", not missing_cols, "중단",
         f"누락 {sorted(missing_cols)}" if missing_cols else f"{len(REQUIRED_COLUMNS)}개 컬럼 확인")

    # 3) 중복 키
    seen, dups = {}, []
    for i, r in enumerate(rows):
        k = (r["indicator_code"], r["region_code"], r["period"])
        if k in seen:
            dups.append(f"{k[0]}/{k[1]}/{k[2]}")
        seen[k] = i
    _add(results, "중복 키 없음", not dups, "중단",
         f"중복 {len(dups)}건 {dups[:3]}" if dups else "")

    # 4) 지표별 값 범위
    bad, norange = [], set()
    for r in rows:
        rng = v["value_range"].get(r["indicator_code"])
        if rng is None:
            norange.add(r["indicator_code"])
            continue
        if r["value"] is not None and not (rng[0] <= r["value"] <= rng[1]):
            bad.append(f"{r['indicator_code']}/{r['region_code']}/{r['period']}={r['value']}")
    _add(results, "값 범위 확인", not bad, "중단",
         f"이탈 {len(bad)}건 {bad[:3]}" if bad else "지표별 허용 범위 내")
    # 허용 범위가 등록되지 않은 지표는 검사를 통과한 게 아니라 검사되지 않은 것입니다.
    _add(results, "허용 범위 등록", not norange, "중단",
         f"미등록 지표 {sorted(norange)} — config/sources.yml 의 value_range 에 등록하세요"
         if norange else "전 지표 등록")

    # 5) 시도 17개
    sido = {r["region_code"] for r in rows
            if r["region_code"].startswith("KR-") and r["region_code"] != "KR-00"}
    need = v["expected_regions_시도"]
    _add(results, f"시도 {need}개 전수 존재", len(sido) == need, "중단",
         f"{len(sido)}개 수집" + (f" · 누락 없음" if len(sido) == need else ""))

    # 6) 기간 연속성 — 소스별
    gaps = []
    by_source = defaultdict(set)
    for r in rows:
        by_source[r["source"]].add(r["period"])
    for src, (y0, y1) in PERIOD_SPEC.items():
        if src not in by_source:
            continue
        miss = set(range(y0, y1 + 1)) - by_source[src]
        if miss:
            gaps.append(f"{src} {sorted(miss)}")
    unchecked = sorted(set(by_source) - set(PERIOD_SPEC))
    _add(results, "기간 연속성", not gaps, "중단",
         f"누락 {gaps}" if gaps else
         " · ".join(f"{s} {a}~{b}" for s, (a, b) in PERIOD_SPEC.items() if s in by_source))
    if unchecked:
        _add(results, "기간 구간 미지정 소스", False, "중단",
             f"{unchecked} — PERIOD_SPEC 에 구간을 등록하세요")

    # 7) 사유 없는 결측
    noreason = [f"{r['source']}/{r['region_code']}/{r['period']}"
                for r in rows if r["value"] is None and not r.get("missing_reason")]
    _add(results, "결측에 사유 표기", not noreason, "중단",
         f"사유 없는 결측 {len(noreason)}건 {noreason[:3]}" if noreason else
         f"결측 {sum(1 for r in rows if r['value'] is None)}건 전부 사유 있음")

    # ── 아래는 기록만 하고 진행합니다
    # 8) 전년 대비 변화율
    series = defaultdict(list)
    for r in rows:
        if r["value"] is not None:
            series[(r["indicator_code"], r["region_code"])].append((r["period"], r["value"]))
    limit, spikes = v["yoy_change_limit"], []
    for key, pts in series.items():
        pts.sort()
        for (p0, v0), (p1, v1) in zip(pts, pts[1:]):
            if v0 and abs(v1 - v0) / abs(v0) > limit:
                spikes.append(f"{key[0]}/{key[1]} {p0}→{p1} {v0}→{v1}")
    _add(results, f"전년 대비 변화율 {int(limit * 100)}% 이내", not spikes, "기록",
         f"초과 {len(spikes)}건 — 원문 대조 필요 {spikes[:2]}" if spikes else "")

    # 9) 전국값과 시도 평균 정합
    nat = {r["period"]: r["value"] for r in rows
           if r["region_code"] == "KR-00" and r["indicator_code"] == "TFR"
           and r["value"] is not None}
    by_year = defaultdict(list)
    for r in rows:
        if (r["indicator_code"] == "TFR" and r["region_code"].startswith("KR-")
                and r["region_code"] != "KR-00" and r["value"] is not None):
            by_year[r["period"]].append(r["value"])
    dev = [y for y, nv in nat.items()
           if by_year.get(y) and abs(sum(by_year[y]) / len(by_year[y]) - nv) / nv > 0.15]
    _add(results, "전국값과 시도 평균 정합", not dev, "기록",
         f"괴리 {sorted(dev)}" if dev else ("전국값 행 없음 — 검사 생략" if not nat else ""))

    tally = _tally(results)
    blocking = [r for r in results if r["결과"] == "실패"]

    log(f"\n검증 {len(results)}개 검사 — 통과 {tally['통과']} · 확인 {tally['확인']} · 실패 {tally['실패']}")
    return results, tally, blocking


def _tally(results):
    return {
        "통과": sum(r["결과"] == "통과" for r in results),
        "확인": sum(r["결과"] == "확인" for r in results),
        "실패": sum(r["결과"] == "실패" for r in results),
    }


def print_table(results):
    print("\n| 검사 | 결과 | 실패 시 | 비고 |")
    print("|:--|:--|:--|:--|")
    for r in results:
        mark = {"통과": "통과", "확인": "**확인**", "실패": "**실패**"}[r["결과"]]
        note = r["비고"][:70]
        print(f"| {r['검사']} | {mark} | {r['실패 시']} | {note} |")


def load_config():
    import yaml
    with open(os.path.join(BASE, "config", "sources.yml"), encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_panel(path):
    """CSV → 행 목록. 빈 칸은 None 으로 되돌립니다.

    pandas 의 float 컬럼은 None 을 담지 못해 빈 칸이 NaN 으로 들어옵니다.
    그대로 두면 결측이 '값이 있는 행'으로 세어져, 값 범위 검사에서 NaN 이
    이탈로 잡히고 '사유 없는 결측' 검사는 0건을 보고합니다. 두 검사가 동시에
    거짓말을 하므로 적재 시점에 되돌립니다.
    """
    import pandas as pd
    df = pd.read_csv(path, encoding="utf-8-sig")
    rows = []
    for rec in df.to_dict("records"):
        r = {k: (None if pd.isna(v) else v) for k, v in rec.items()}
        r["period"] = int(r["period"])
        r["value"] = None if r["value"] is None else float(r["value"])
        rows.append(r)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="표준 스키마 중단 조건 검사")
    ap.add_argument("panel", nargs="?",
                    default=os.path.join(BASE, "exercises", "04", "panel.csv"))
    a = ap.parse_args(argv)

    rows = load_panel(a.panel)
    results, tally, blocking = run(rows, load_config())
    print_table(results)

    if blocking:
        print(f"\n**중단** — 중단 조건 {len(blocking)}건 위반. 산출물을 만들지 않습니다.")
        for b in blocking:
            print(f"  - {b['검사']}: {b['비고']}")
        return 1
    print("\n통과 — 산출물을 만들 수 있습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
