"""검증을 통과한 패널로만 산출물을 만듭니다.

순서가 핵심입니다. 검증 → (통과) → 산출물. 중단 조건에 하나라도 걸리면
파일을 만들지 않고 exit 1 로 끝냅니다. 부분 산출물도 남기지 않습니다.
이미 있던 산출물은 덮지 않습니다 — 실패한 실행이 지난 실행의 결과를
지워버리면, 무엇이 마지막으로 성공한 것인지 알 수 없게 됩니다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import validate_panel as V  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "03"))
from schema import COLUMNS  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
E04 = os.path.join(BASE, "exercises", "04")


def write_codebook(path, rows, results, side):
    out = []
    A = out.append
    pending, unmapped, meta = side["pending"], side["unmapped"], side["meta"]

    A(f"# 코드북 — 인구·정책 통합 패널 ({side['run_date']})\n")
    A(f"총 {len(rows)}행. 표준 스키마: `references/schema.md`\n")

    A("---\n\n## 1. 컬럼\n")
    A("| 컬럼 | 설명 |")
    A("|---|---|")
    for c, d in [
        ("source", "출처 기관 코드"), ("indicator_code", "소스의 원 지표 코드"),
        ("region_code", "국내 `KR-`+2자리 / 국외 ISO 3166-1 alpha-3"),
        ("period", "관측 연도 (정수)"), ("value", "값. 비면 missing_reason 필수"),
        ("unit", "단위"), ("vintage", "확정 / 잠정 / 추계 / 연간"),
        ("retrieved_at", "수집 시각 (ISO 8601, KST)"),
        ("source_url", "해당 값을 얻은 요청 주소"),
        ("missing_reason", "NA_NOTSURVEYED / NA_NOTAPPLICABLE / NA_CONFIDENTIAL"),
    ]:
        A(f"| `{c}` | {d} |")

    A("\n---\n\n## 2. 지표\n")
    by = defaultdict(lambda: {"행": 0, "결측": 0, "단위": set(), "연도": set(), "소스": set()})
    for r in rows:
        b = by[r["indicator_code"]]
        b["행"] += 1
        b["결측"] += r["value"] is None
        b["단위"].add(r["unit"])
        b["연도"].add(r["period"])
        b["소스"].add(r["source"])
    A("| 지표 | 소스 | 행 | 결측 | 단위 | 기간 |")
    A("|---|---|---|---|---|---|")
    for k, b in sorted(by.items()):
        A(f"| `{k}` | {', '.join(sorted(b['소스']))} | {b['행']} | {b['결측']} | "
          f"{', '.join(sorted(b['단위']))} | {min(b['연도'])}~{max(b['연도'])} |")

    A("\n### 결측 사유 분포\n")
    mr = defaultdict(int)
    for r in rows:
        if r["value"] is None:
            mr[r["missing_reason"]] += 1
    A("| 사유 | 건수 | 분석에서 |")
    A("|---|---|---|")
    guide = {"NA_NOTSURVEYED": "보간 검토 가능", "NA_NOTAPPLICABLE": "보간 금지",
             "NA_CONFIDENTIAL": "값은 존재. 다른 경로 검토"}
    for k, n in sorted(mr.items()):
        A(f"| `{k}` | {n} | {guide.get(k, '')} |")
    if not mr:
        A("| — | 0 | 결측 없음 |")

    A("\n---\n\n## 3. 수집 이력\n")
    A("| 소스 | 행 | 소요 |")
    A("|---|---|---|")
    for m in meta:
        A(f"| {m['소스']} | {m['행']} | {m['초']}초 |")

    A("\n---\n\n## 4. 판정이 필요했던 항목\n")
    if pending:
        A("규칙으로 처리하지 못해 판단이 들어간 건입니다. **확인은 연구자가 합니다.**\n")
        A("| 원문 | 제안된 분류 | 근거 | 확인 |")
        A("|---|---|---|---|")
        for p in pending:
            A(f"| {p['원문']} | {p['제안']} | {p['근거']} | {p['확인']} |")
        A("\n확인이 끝나면 `scripts/normalize.py` 의 `POLICY_TAXONOMY` 에 등록하세요. "
          "등록된 뒤에는 판단 없이 규칙으로 처리됩니다.\n")
    else:
        A("이번 실행에서는 전부 규칙으로 처리되었습니다. 판단이 들어간 건이 없습니다.\n")

    if unmapped:
        A("### 매핑하지 못한 항목\n")
        A("| 항목 | 종류 | 소스 |")
        A("|---|---|---|")
        for u in unmapped[:20]:
            A(f"| {u['항목']} | {u['종류']} | {u['소스']} |")
        A("\n사전에 없는 지역명은 임의로 매핑하지 않았습니다. 해당 행은 패널에 없습니다.\n")

    A("---\n\n## 5. 검증 결과\n")
    A("| 검사 | 결과 | 실패 시 | 비고 |")
    A("|---|---|---|---|")
    for c in results:
        A(f"| {c['검사']} | {c['결과']} | {c['실패 시']} | {c['비고']} |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def write_tables(rows, out_dir, run_date):
    os.makedirs(out_dir, exist_ok=True)
    made = []

    def table(fname, title, indicator, note):
        sel = [r for r in rows if r["indicator_code"] == indicator]
        if not sel:
            return
        years = sorted({r["period"] for r in sel})
        regions = sorted({r["region_code"] for r in sel})
        cell = {(r["region_code"], r["period"]): r for r in sel}
        unit = sorted({r["unit"] for r in sel})[0]
        src = sorted({r["source"] for r in sel})[0]

        lines = [f"# {title}\n", f"단위: {unit} · 출처: {src} · 기준시점: {run_date}\n"]
        lines.append("| 지역 | " + " | ".join(str(y) for y in years) + " |")
        lines.append("|:--|" + "---:|" * len(years))
        for g in regions:
            cells = []
            for y in years:
                r = cell.get((g, y))
                if r is None:
                    cells.append("")
                elif r["value"] is None:
                    cells.append(f"— ({r['missing_reason'].replace('NA_', '')})")
                else:
                    cells.append(f"{r['value']:,.3f}".rstrip("0").rstrip(".")
                                 if r["value"] < 10 else f"{r['value']:,.0f}")
            lines.append(f"| {g} | " + " | ".join(cells) + " |")
        lines.append(f"\n{note}\n")
        lines.append("— 표기: `—` 는 결측이며 괄호 안은 사유입니다. "
                     "`NOTSURVEYED` 미조사 · `NOTAPPLICABLE` 해당없음 · `CONFIDENTIAL` 비공개.\n")
        path = os.path.join(out_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        made.append(fname)

    table("tfr_국가별.md", "합계출산율 — 국가별", "SP.DYN.TFRT.IN",
          "World Bank Indicators (SP.DYN.TFRT.IN) 실시간 수집.")
    table("tfr_시도별.md", "합계출산율 — 시도별", "TFR",
          "내부 정리표. 2025년은 잠정치.")
    table("출생아수_시도별.md", "출생아수 — 시도별", "BIRTHS",
          "내부 정리표. 천 단위 표기 셀은 명으로 환산했습니다.")
    table("정책건수_시도별.md", "지자체 출산지원정책 건수 — 시도별", "POLICY_COUNT",
          "정책브리핑 페이지 크롤링. robots.txt 확인 후 수집.")
    return made


def main(argv=None):
    ap = argparse.ArgumentParser(description="검증 통과 시에만 산출물 생성")
    ap.add_argument("panel", nargs="?", default=os.path.join(E04, "panel.csv"))
    ap.add_argument("--out-dir", default=os.path.join(E04, "out"))
    a = ap.parse_args(argv)

    rows = V.load_panel(a.panel)
    results, tally, blocking = V.run(rows, V.load_config())
    V.print_table(results)

    if blocking:
        print(f"\n**중단** — 중단 조건 {len(blocking)}건 위반. 산출물을 만들지 않습니다.")
        for b in blocking:
            print(f"  - {b['검사']}: {b['비고']}")
        print(f"\n{a.out_dir} 는 건드리지 않았습니다.")
        return 1

    with open(os.path.join(E04, "pending.json"), encoding="utf-8") as f:
        side = json.load(f)

    os.makedirs(a.out_dir, exist_ok=True)
    import pandas as pd
    pd.DataFrame(rows, columns=COLUMNS).to_csv(
        os.path.join(a.out_dir, "panel.csv"), index=False, encoding="utf-8-sig")
    write_codebook(os.path.join(a.out_dir, "codebook.md"), rows, results, side)
    made = write_tables(rows, os.path.join(a.out_dir, "tables"), side["run_date"])

    print(f"\n산출물 — {a.out_dir}")
    print(f"  panel.csv        {len(rows)}행")
    print(f"  codebook.md      5개 절 · 4절 미확인 {len(side['pending'])}건")
    for m in made:
        print(f"  tables/{m}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
