"""samples/ 의 JSON · XML · CSV 를 표준 스키마 하나로 합칩니다.

schema.Record 를 통과하지 못하는 행은 만들어지지 않습니다. 특히 값이 비면
missing_reason 없이는 생성 자체가 실패하므로, 형식마다 다른 결측 표기를
전부 사유 코드로 옮겨야 합니다.

    JSON  "value": null
    XML   <value></value>  또는  <value/>   (빈 태그. 조용히 사라지기 쉬움)
    CSV   '-'  또는  공란                    (둘의 뜻이 다를 수 있음)

MISSING_POLICY 는 비워둔 채로 시작합니다. 응답만 봐서는 사유를 알 수 없고,
추측해서 채우면 그게 확인된 사실인지 기본값인지 구분할 수 없게 됩니다.
--inventory 로 결측 위치를 먼저 확인하고, 사람이 정한 뒤에 채웁니다.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import COLUMNS, KST, MISSING_REASONS, Record, SchemaError, to_rows  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SAMPLES = os.path.join(BASE, "exercises", "03", "samples")

# 결측 표기 → 사유 코드. 사람이 정한 뒤에만 채웁니다. None 이면 변환이 중단됩니다.
#
# 2026-09-16 사용자 확정. 네 표기 모두 미조사로 판정했습니다.
# CSV 의 '-' 와 공란은 뜻이 다를 수 있다는 단서가 있었으나(samples/README.md),
# 확인 결과 둘 다 미조사로 통일. 응답만으로는 구분할 근거가 없었습니다.
MISSING_POLICY = {
    "json.null": "NA_NOTSURVEYED",       # 2025년 미공표. lastupdated 기준 공표 전
    "xml.empty_tag": "NA_NOTSURVEYED",   # resultCode=00 정상 응답, 값만 빔
    "csv.dash": "NA_NOTSURVEYED",
    "csv.blank": "NA_NOTSURVEYED",
}


class MissingPolicyError(RuntimeError):
    pass


def region_map():
    path = os.path.join(BASE, "references", "region-codes.csv")
    with open(path, encoding="utf-8") as f:
        return {r["region_name"]: r["region_code"] for r in csv.DictReader(f)}


def _reason(kind):
    code = MISSING_POLICY.get(kind)
    if code is None:
        raise MissingPolicyError(
            f"결측 표기 '{kind}' 의 사유 코드가 정해지지 않았습니다. "
            f"{sorted(MISSING_REASONS)} 중 하나를 MISSING_POLICY 에 지정하세요."
        )
    return code


def _to_code(name, rmap, unmapped, source):
    """지역명 → 코드. 사전에 없으면 임의로 매핑하지 않고 멈춥니다."""
    code = rmap.get(name)
    if code is None:
        unmapped.append({"소스": source, "지역명": name})
        raise SchemaError(
            f"사전에 없는 지역명: {name!r} ({source}). "
            "references/region-codes.csv 에 등록한 뒤 다시 실행하세요."
        )
    return code


# ── JSON (World Bank 식 · 원소 2개 배열)
def from_json(path, ts, inventory):
    payload = json.load(open(path, encoding="utf-8"))
    if not (isinstance(payload, list) and len(payload) == 2):
        raise SchemaError("JSON 최상위가 원소 2개짜리 배열이 아닙니다")
    url = f"file://{path}"
    out = []
    for x in payload[1]:
        v = x["value"]
        missing = v is None
        if missing:
            inventory.append({"소스": "JSON", "표기": "null", "지역": x["countryiso3code"],
                              "연도": int(x["date"]), "정책키": "json.null"})
        out.append(Record(
            source="WORLDBANK", indicator_code=x["indicator"]["id"],
            region_code=x["countryiso3code"], period=int(x["date"]),
            value=None if missing else float(v),
            unit="명",                      # 응답 unit 은 빈 문자열
            vintage="연간", source_url=url, retrieved_at=ts,
            missing_reason=_reason("json.null") if missing else None,
        ))
    return out


# ── XML (공공데이터포털 식 · <items><item>)
def from_xml(path, ts, rmap, unmapped, inventory):
    root = ET.parse(path).getroot()
    code = root.findtext("./header/resultCode")
    if code not in (None, "00"):
        raise SchemaError(f"XML 응답 헤더가 정상이 아닙니다: resultCode={code} "
                          f"msg={root.findtext('./header/resultMsg')!r}")
    url = f"file://{path}"
    out = []
    for item in root.findall(".//items/item"):
        el = item.find("value")
        # 빈 태그 두 형태: <value></value> 와 <value/> 는 파싱 결과가 모두 text=None
        # 또는 ''. 여기서 분기를 빠뜨리면 값이 조용히 사라집니다.
        raw = "" if el is None or el.text is None else el.text.strip()
        missing = raw == ""
        name = item.findtext("regionName", "").strip()
        region = _to_code(name, rmap, unmapped, "XML")
        year = int(item.findtext("year"))
        if missing:
            # <value></value> 와 <value/> 는 ElementTree 파싱 결과가 같습니다(text=None).
            # 원문에서 둘을 구분하려면 별도 파서가 필요합니다. 없는 구분을 적지 않습니다.
            inventory.append({"소스": "XML", "표기": "빈 태그", "지역": f"{name}({region})",
                              "연도": year, "정책키": "xml.empty_tag"})
        out.append(Record(
            source="PUBLICDATA", indicator_code=item.findtext("itemCode", "").strip(),
            region_code=region, period=year,
            value=None if missing else float(raw),
            unit=item.findtext("unit", "명").strip() or "명",
            vintage="확정", source_url=url, retrieved_at=ts,
            missing_reason=_reason("xml.empty_tag") if missing else None,
        ))
    return out


# ── CSV (KOSIS 내려받기 식)
def from_csv(path, ts, rmap, unmapped, inventory):
    url = f"file://{path}"
    out = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw = (row["값"] or "").strip()
            name = row["행정구역별"].strip()
            region = _to_code(name, rmap, unmapped, "CSV")
            year = int(row["시점"])
            kind = None
            if raw == "-":
                kind = "csv.dash"
            elif raw == "":
                kind = "csv.blank"
            if kind:
                inventory.append({"소스": "CSV", "표기": "'-'" if kind == "csv.dash" else "(공란)",
                                  "지역": f"{name}({region})", "연도": year, "정책키": kind})
            out.append(Record(
                source="KOSIS", indicator_code="TFR", region_code=region, period=year,
                value=None if kind else float(raw),
                unit=row["단위"].strip() or "명",
                vintage="확정", source_url=url, retrieved_at=ts,
                missing_reason=_reason(kind) if kind else None,
            ))
    return out


def build(inventory_only=False):
    ts = datetime.now(KST).isoformat(timespec="seconds")
    rmap, unmapped, inventory = region_map(), [], []

    if inventory_only:
        # 사유 코드가 없어도 결측 위치는 셀 수 있게 임시로 통과시킵니다.
        for k in MISSING_POLICY:
            MISSING_POLICY[k] = MISSING_POLICY[k] or "NA_NOTSURVEYED"

    records = []
    records += from_json(os.path.join(SAMPLES, "response.json"), ts, inventory)
    records += from_xml(os.path.join(SAMPLES, "response.xml"), ts, rmap, unmapped, inventory)
    records += from_csv(os.path.join(SAMPLES, "response.csv"), ts, rmap, unmapped, inventory)
    return records, inventory, unmapped


def main(argv=None):
    ap = argparse.ArgumentParser(description="samples/ 세 형식 → 표준 스키마 통합")
    ap.add_argument("--inventory", action="store_true", help="결측 위치만 확인하고 끝냅니다")
    ap.add_argument("--out", help="CSV 저장 경로")
    a = ap.parse_args(argv)

    try:
        records, inventory, unmapped = build(inventory_only=a.inventory)
    except MissingPolicyError as e:
        print(f"중단: {e}", file=sys.stderr)
        print("    --inventory 로 결측 위치를 먼저 확인하세요.", file=sys.stderr)
        return 2
    except SchemaError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    if a.inventory:
        print(f"결측 {len(inventory)}건\n")
        print("| 소스 | 결측 표기 | 지역 | 연도 | 정책 키 |")
        print("|:--|:--|:--|---:|:--|")
        for x in inventory:
            print(f"| {x['소스']} | {x['표기']} | {x['지역']} | {x['연도']} | `{x['정책키']}` |")
        print(f"\n전체 {len(records)}행 · 지역 사전 미등록 {len(unmapped)}건")
        return 0

    rows = to_rows(records)          # 중복 키 검사
    print(f"통합 {len(rows)}행 (JSON+XML+CSV)")
    try:
        import pandas as pd
        df = pd.DataFrame(rows, columns=COLUMNS)
        print(f"\n소스별:\n{df.groupby('source').size().to_string()}")
        print(f"\n결측 사유별:\n{df[df['value'] == ''].groupby('missing_reason').size().to_string()}")
        print(f"\n{df.to_string(max_colwidth=28)}")
        if a.out:
            df.to_csv(a.out, index=False, encoding="utf-8-sig")
            print(f"\n저장: {a.out}")
    except ImportError:
        for r in rows:
            print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
