"""KOSIS 수집 → 표준 스키마.

명세서: references/api-registry.md · "KOSIS 공유서비스 › 시도/출생아수 DT_1B8000H"
설정:   config/sources.yml · kosis_births
출력:   references/schema.md

이 소스에서 걸리는 것 네 가지
  1) 응답이 배열이 아니라 객체면 에러입니다. 200 과 함께 옵니다
     (err=21 잘못된 요청 변수 / err=20 필수요청변수 누락)
  2) 결측 표기가 두 가지입니다 — 빈 문자열과 '-'. float() 전에 둘 다 걸러야 합니다
  3) UNIT_NM 이 없습니다(None). 단위는 config 에서 부여합니다
  4) objL1=ALL 이면 '국외' 분류가 함께 옵니다. region-codes.csv 에 없으므로
     임의로 매핑하지 않고 미확인으로 남깁니다(CLAUDE.md)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import COLUMNS, KST, Record, SchemaError, to_rows  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 결측 표기 → 사유 코드. 응답은 사유를 알려주지 않으므로 수집 시점에 정합니다.
MISSING_MARKS = {"", "-"}

RETRY = 3
BACKOFF = 1.5


class CollectError(RuntimeError):
    pass


def load_env(path=None):
    """.env 를 환경변수로 읽습니다. 이미 셸에 있는 값은 덮지 않습니다.

    run.py 와 같은 규칙입니다. run.py 를 거치지 않고 이 모듈만 쓰는 경로가
    있어서 여기서도 한 번 읽습니다.
    """
    path = path or os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, val = line.partition("=")
            k, val = k.strip(), val.strip().strip('"').strip("'")
            if k and val and k not in os.environ:
                os.environ[k] = val


def load_source(sid="kosis_births"):
    import yaml
    with open(os.path.join(BASE, "config", "sources.yml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for s in cfg["sources"]:
        if s["id"] == sid:
            return s
    raise CollectError(f"config/sources.yml 에 {sid} 가 없습니다")


def region_map():
    path = os.path.join(BASE, "references", "region-codes.csv")
    with open(path, encoding="utf-8") as f:
        return {r["region_name"]: r["region_code"] for r in csv.DictReader(f)}


def _request(url, params):
    import requests
    last = None
    for attempt in range(RETRY):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                raise CollectError("호출 제한(429). 발급 등급별 제한을 확인하세요")
            r.raise_for_status()
            try:
                return r.json()
            except ValueError:
                body = (r.text or "").strip().replace("\n", " ")[:300]
                raise CollectError(
                    "응답이 JSON 이 아닙니다. format=json · jsonVD=Y 를 확인하세요.\n"
                    f"    본문: {body}"
                ) from None
        except CollectError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < RETRY - 1:
                time.sleep(BACKOFF ** attempt)
    raise CollectError(f"{RETRY}회 재시도 후 실패: {last}")


def _check_payload(payload):
    """KOSIS 는 잘못된 요청에도 200 을 줍니다. 배열이 아니면 에러 응답입니다."""
    if isinstance(payload, dict):
        raise CollectError(
            "배열이 와야 하는데 객체가 왔습니다. 에러 응답입니다.\n"
            f"    err={payload.get('err')} · {payload.get('errMsg')}\n"
            "    method · itmId · objL1 · format · jsonVD 중 빠진 것이 없는지 확인하세요."
        )
    if not isinstance(payload, list) or not payload:
        raise CollectError(f"수집 0건입니다. 요청 조건을 확인하세요 (받은 것: {type(payload).__name__})")
    return payload


def fetch(src, offline=False, log=print):
    if offline:
        path = os.path.join(BASE, src["sample"])
        if not os.path.exists(path):
            raise CollectError(f"스냅샷이 없습니다: {path}")
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        log(f"  스냅샷 사용 {os.path.basename(path)}")
        url = f"file://{path}"
    else:
        params = dict(src["params"])
        auth = src.get("auth", {})
        load_env()
        key = os.environ.get(auth.get("env", ""), "")
        if not key:
            raise CollectError(
                f"인증키가 없습니다. 환경변수 {auth.get('env')} 를 설정하거나 --offline 으로 실행하세요."
            )
        params[auth["param"]] = key
        payload = _request(src["endpoint"], params)
        # 요청 주소에서 인증키는 제외합니다. 산출물에 키가 남으면 안 됩니다.
        safe = {k: v for k, v in params.items() if k != auth["param"]}
        url = f"{src['endpoint']}?{urlencode(safe, encoding='utf-8')}"
        log(f"  호출 완료 (인증키는 source_url 에 남기지 않습니다)")
    return _check_payload(payload), url


def to_schema(payload, src, url, missing_reason, log=print):
    rmap = region_map()
    ts = datetime.now(KST).isoformat(timespec="seconds")
    records, unmapped, marks = [], [], {}

    for x in payload:
        name = (x.get("C1_NM") or "").strip()
        code = rmap.get(name)
        if code is None:
            # 사전에 없는 지역명은 임의로 매핑하지 않습니다(CLAUDE.md).
            if not any(u["항목"] == name for u in unmapped):
                unmapped.append({"항목": name, "종류": "지역명", "소스": src["id"]})
            continue

        raw = str(x.get("DT", "")).strip()
        if raw in MISSING_MARKS:
            marks.setdefault(raw or "(빈 문자열)", 0)
            marks[raw or "(빈 문자열)"] += 1
            value, reason = None, missing_reason
        else:
            try:
                value, reason = float(raw.replace(",", "")), None
            except ValueError:
                raise CollectError(
                    f"DT 를 해석할 수 없습니다: {raw!r} ({name}/{x.get('PRD_DE')}). "
                    f"알려진 결측 표기는 {sorted(MISSING_MARKS)} 입니다. "
                    "새 표기라면 references/api-registry.md 를 먼저 갱신하세요."
                ) from None

        try:
            records.append(Record(
                source="KOSIS", indicator_code=src["indicator_code"], region_code=code,
                period=int(x["PRD_DE"]), value=value,
                unit=(x.get("UNIT_NM") or src["unit"]).strip(),
                vintage=src["vintage"], source_url=url, retrieved_at=ts,
                missing_reason=reason,
            ))
        except SchemaError as e:
            raise CollectError(f"{name}/{x.get('PRD_DE')}: {e}") from None

    log(f"  표준 스키마 {len(records)}행 · 결측 {sum(1 for r in records if r.is_missing)}행")
    for k, n in marks.items():
        log(f"    결측 표기 {k!r} {n}건 → {missing_reason}")
    if unmapped:
        log(f"  사전에 없는 지역명 {len(unmapped)}건 — 행을 만들지 않았습니다: "
            f"{[u['항목'] for u in unmapped]}")
    return records, unmapped


def collect(sid="kosis_births", offline=False, missing_reason="NA_NOTSURVEYED", log=print):
    src = load_source(sid)
    payload, url = fetch(src, offline=offline, log=log)
    log(f"  응답 {len(payload)}행")
    return to_schema(payload, src, url, missing_reason, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="KOSIS 수집 → 표준 스키마")
    ap.add_argument("--source", default="kosis_births")
    ap.add_argument("--offline", action="store_true", help="raw/sample 스냅샷 사용")
    ap.add_argument("--missing-reason", default="NA_NOTSURVEYED",
                    choices=["NA_NOTSURVEYED", "NA_NOTAPPLICABLE", "NA_CONFIDENTIAL"])
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    try:
        records, unmapped = collect(a.source, offline=a.offline,
                                    missing_reason=a.missing_reason)
    except (CollectError, SchemaError) as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    rows = to_rows(records)
    print(f"\n{len(rows)}행")
    if a.out:
        import pandas as pd
        pd.DataFrame(rows, columns=COLUMNS).to_csv(a.out, index=False, encoding="utf-8-sig")
        print(f"저장: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
