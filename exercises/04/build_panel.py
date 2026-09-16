"""3교시 세 소스를 하나의 패널로 합칩니다.

    API      exercises/03/collect_wb.py    World Bank · 국가 ISO3
    크롤링   exercises/03/crawl_policy.py  정책브리핑 픽스처 · KR-NN
    내부자료 exercises/03/convert_xlsx.py  내부 정리표 · KR-NN

맞추는 축
  - 지역 코드: references/region-codes.csv (국내) / ISO 3166-1 alpha-3 (국외)
  - 기간: 연 단위 정수
  - 단위: 인구 지표는 '명'. POLICY_COUNT 는 정책 '건수'라 명으로 환산하지 않습니다
          (환산할 수 있는 양이 아닙니다. 아래 UNIT_POLICY 주석 참고)

판단이 들어간 건은 확정하지 않습니다. 코드북 4절에 미확인으로 남깁니다.
  - 사전에 없는 지역명 → 행을 만들지 않고 '매핑하지 못한 항목'으로
  - 규칙으로 분류 안 되는 정책명 → '판정이 필요했던 항목'으로
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

E03 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "03")
sys.path.insert(0, E03)

import collect_kosis                   # noqa: E402
import collect_wb                      # noqa: E402
import convert_xlsx                    # noqa: E402
import crawl_policy                    # noqa: E402
from schema import COLUMNS, KST, to_rows  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 인구 지표의 목표 단위. 세 소스 모두 이미 '명'이라 환산은 일어나지 않습니다.
UNIT_TARGET = "명"
# POLICY_COUNT 는 건수입니다. '명'으로 맞추라는 지시가 있었으나 정책 건수를
# 사람 수로 바꿀 수 있는 환산 계수가 존재하지 않습니다. 단위를 보존하고 보고합니다.
UNIT_POLICY = "건"


def quiet(_):
    pass


def _blank_to_none(rows):
    """빈 값 표현을 None 으로 통일합니다.

    collect_wb 는 빈 값을 None 으로, schema.Record.as_row() 는 '' 로 냅니다.
    섞인 채로 합치면 결측 집계가 한쪽만 세고, scripts/validate.py 의
    '사유 없는 결측' 검사(value is None 기준)도 절반을 놓칩니다.
    CSV 로 쓸 때 pandas 가 None 을 빈 칸으로 내보내므로 출력은 동일합니다.
    """
    for r in rows:
        for k in ("value", "missing_reason"):
            if r.get(k) == "":
                r[k] = None
    return rows


def gather(missing_reason, offline=False, log=print):
    """세 소스를 각각 표준 스키마로 받아옵니다."""
    parts, unmapped, pending, meta = {}, [], [], []

    log("[1/4] API · World Bank")
    t0 = datetime.now()
    api = collect_wb.collect("KOR;JPN;FRA;DEU;ITA;ESP;USA", "SP.DYN.TFRT.IN",
                             2015, 2025, missing_reason, log=log)
    # collect_wb 는 dict 를 돌려줍니다. 나머지 둘과 형태를 맞춥니다.
    parts["API"] = _blank_to_none(api)
    meta.append({"소스": "World Bank (API)", "행": len(api),
                 "초": round((datetime.now() - t0).total_seconds(), 2)})

    log("[2/4] 크롤링 · 정책 페이지")
    t0 = datetime.now()
    rec, unm, pend, _detail = crawl_policy.crawl(log=log)
    parts["크롤링"] = _blank_to_none(to_rows(rec))
    unmapped += unm
    pending += pend
    meta.append({"소스": "정책브리핑 (크롤링)", "행": len(rec),
                 "초": round((datetime.now() - t0).total_seconds(), 2)})

    log("[3/4] KOSIS · 시도/출생아수")
    t0 = datetime.now()
    rec, unm = collect_kosis.collect(offline=offline, log=log)
    parts["KOSIS"] = _blank_to_none(to_rows(rec))
    unmapped += unm
    meta.append({"소스": "KOSIS 인구동향조사 (API)", "행": len(rec),
                 "초": round((datetime.now() - t0).total_seconds(), 2)})

    log("[4/4] 내부자료 · 엑셀")
    t0 = datetime.now()
    rec, _conv, _skip = convert_xlsx.convert(log=log)
    internal = _blank_to_none(to_rows(rec))
    # 소스 우선순위 — 국내 지표는 KOSIS (CLAUDE.md). 2026-09-16 사용자 확정.
    # 내부 정리표의 출생아수는 10개 지역·반올림 값이고 KOSIS 가 17개 시도를 전부
    # 덮으므로 제외합니다. 겹치는 30건을 그대로 두면 중복 키로 중단됩니다.
    dropped = [r for r in internal if r["indicator_code"] == "BIRTHS"]
    internal = [r for r in internal if r["indicator_code"] != "BIRTHS"]
    if dropped:
        log(f"  소스 우선순위로 제외: INTERNAL BIRTHS {len(dropped)}행 (KOSIS 우선)")
    parts["내부자료"] = internal
    meta.append({"소스": "내부 정리표 (엑셀)", "행": len(rec),
                 "초": round((datetime.now() - t0).total_seconds(), 2)})

    return parts, unmapped, pending, meta


def check_axes(rows):
    """맞추기로 한 축이 실제로 맞았는지 확인합니다. 통과를 가정하지 않습니다."""
    import re
    problems = []
    kr = re.compile(r"^KR-\d{2}$")
    iso3 = re.compile(r"^[A-Z]{3}$")

    for r in rows:
        if not (kr.match(r["region_code"]) or iso3.match(r["region_code"])):
            problems.append(f"region_code 형식 위반: {r['region_code']!r}")
        if not isinstance(r["period"], int):
            problems.append(f"period 가 정수가 아님: {r['period']!r}")
        expect = UNIT_POLICY if r["indicator_code"] == "POLICY_COUNT" else UNIT_TARGET
        if r["unit"] != expect:
            problems.append(f"unit 불일치: {r['indicator_code']} {r['region_code']} "
                            f"{r['period']} → {r['unit']!r} (기대 {expect!r})")
    return sorted(set(problems))


def write_codebook_s4(path, pending, unmapped, meta, rows, run_date):
    """report.py 의 write_codebook 4절 형식을 그대로 씁니다."""
    out = []
    A = out.append
    A(f"# 코드북 — 4절 발췌 ({run_date})\n")
    A("3교시 세 소스를 합친 패널에 대한 것입니다. "
      "`scripts/report.py` 의 `write_codebook` 4절과 같은 형식입니다.\n")

    A("---\n\n## 4. 판정이 필요했던 항목\n")
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

    A("---\n\n## 참고 · 이번 패널의 구성\n")
    A("| 소스 | 행 | 소요 |")
    A("|---|---|---|")
    for m in meta:
        A(f"| {m['소스']} | {m['행']} | {m['초']}초 |")
    A(f"\n합계 {len(rows)}행\n")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))


def main(argv=None):
    ap = argparse.ArgumentParser(description="세 소스 → 단일 패널")
    ap.add_argument("--missing-reason", default="NA_NOTSURVEYED",
                    choices=["NA_NOTSURVEYED", "NA_NOTAPPLICABLE", "NA_CONFIDENTIAL"],
                    help="World Bank 결측 사유. 응답이 주지 않으므로 지정해야 합니다")
    ap.add_argument("--offline", action="store_true", help="KOSIS 는 스냅샷 사용")
    ap.add_argument("--out", default=os.path.join(BASE, "exercises", "04", "panel.csv"))
    ap.add_argument("--codebook", default=os.path.join(BASE, "exercises", "04", "codebook_s4.md"))
    a = ap.parse_args(argv)

    try:
        parts, unmapped, pending, meta = gather(a.missing_reason, offline=a.offline)
    except Exception as e:  # noqa: BLE001
        print(f"중단: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    rows = [r for part in parts.values() for r in part]

    # 중복 키 — references/schema.md 중단 조건
    seen = {}
    dups = []
    for i, r in enumerate(rows):
        k = (r["indicator_code"], r["region_code"], r["period"])
        if k in seen:
            dups.append(f"{k} (행 {seen[k]}, {i})")
        seen[k] = i
    if dups:
        print(f"중단: 중복 키 {len(dups)}건\n  " + "\n  ".join(dups[:10]), file=sys.stderr)
        return 1

    problems = check_axes(rows)
    if problems:
        print(f"중단: 축 정렬 위반 {len(problems)}건\n  " + "\n  ".join(problems[:10]), file=sys.stderr)
        return 1

    run_date = datetime.now(KST).strftime("%Y-%m-%d")
    write_codebook_s4(a.codebook, pending, unmapped, meta, rows, run_date)

    # 산출물 단계가 다시 수집하지 않도록 판정 대기 목록을 함께 남깁니다.
    import json
    side = os.path.join(os.path.dirname(a.out), "pending.json")
    with open(side, "w", encoding="utf-8") as f:
        json.dump({"pending": pending, "unmapped": unmapped, "meta": meta,
                   "run_date": run_date}, f, ensure_ascii=False, indent=2)

    import pandas as pd
    df = pd.DataFrame(rows, columns=COLUMNS)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")

    print(f"\n패널 {len(df)}행\n")
    print("소스 × 지표:")
    print(df.groupby(["source", "indicator_code"]).size().to_string())
    print("\n지역 체계:")
    print(df["region_code"].str.slice(0, 3).replace({"KR-": "국내 KR-NN"})
          .where(df["region_code"].str.startswith("KR-"), "국외 ISO3")
          .value_counts().to_string())
    print(f"\n기간: {df['period'].min()}~{df['period'].max()} (연 단위 정수)")
    print(f"\n단위:\n{df.groupby(['indicator_code', 'unit']).size().to_string()}")
    miss = df[df["value"].isna()]
    print(f"\n결측 {len(miss)}행:")
    print(miss.groupby(["source", "missing_reason"]).size().to_string())
    print(f"\n미확인 (코드북 4절): 판정 필요 {len(pending)}건 · 매핑 실패 {len(unmapped)}건")
    print(f"\n저장: {a.out}\n      {a.codebook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
