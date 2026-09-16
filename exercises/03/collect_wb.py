"""World Bank Indicators 수집 → 표준 스키마.

명세서: references/api-registry.md · "World Bank Indicators" (최종 확인 2026-09-16)
출력 스키마: references/schema.md

────────────────────────────────────────────────────────────────────────
이 호출에서 일어날 수 있는 실패 다섯 가지와 대응
────────────────────────────────────────────────────────────────────────
1) 일시 장애·타임아웃 → 무한 재시도
   증상: 연결 끊김, 5xx. 잘못 짜면 while True 로 영원히 두드립니다.
   대응: 재시도 횟수 상한(RETRY) + 지수 백오프. 상한을 넘으면 예외로 중단.

2) 호출 제한(429)
   증상: 200 대신 429. 같은 속도로 재시도하면 계속 429.
   대응: 429 는 별도 분기로 간격을 늘려 재시도, 상한 초과 시 중단.
   주의: 명세서상 호출 제한 수치는 "문서에 없음. 미확인" 입니다. 추측하지 않고
         보수적으로 간격을 둡니다.

3) 비JSON 응답 (XML / HTML 오류 페이지)
   증상: 200 인데 본문이 XML 또는 HTML. 명세서 주의 — format 을 빼면 XML.
   대응: json() 실패 시 재시도하지 않고 즉시 중단. 일시 장애가 아니라 요청이
         틀린 것이라 재시도는 낭비입니다. 본문 앞 300자를 함께 보여줍니다.

4) 조용한 실패 — 0건 수집 / 부분 수집
   증상 a: total=0, 관측치가 빈 배열. 에러 없이 "성공"으로 끝납니다.
   증상 b: 첫 쪽만 받고 끝냄. 11년 중 4년만 들고 추세를 말하게 됩니다.
   대응: 명세서의 페이지 처리 기준대로 "받은 행 수 vs [0].total" 로 판정하고
         pages 만큼 순회해 전량을 모읍니다. 모자라면 중단. 0건도 중단.

5) 스키마 드리프트 — 응답 키 변경
   증상: countryiso3code 가 iso3 등으로 바뀜. get() 로 읽으면 None 이 들어가
         지역 정보가 통째로 사라진 채 행 수만 맞습니다. 가장 위험한 실패입니다.
   대응: 명세서에 등록된 관측치 키 8개를 전 행에 대해 대조. 누락 시 중단.
         region_code 가 비면 행을 만들지 않고 중단합니다.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

KST = timezone(timedelta(hours=9))

# ── 명세서(references/api-registry.md)에서 옮긴 값. 코드가 아니라 명세가 근거입니다.
ENDPOINT = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
META_KEYS = ["page", "pages", "per_page", "total", "lastupdated"]
OBS_KEYS = ["indicator", "country", "countryiso3code", "date", "value", "unit",
            "obs_status", "decimal"]
# 표준 스키마로 옮길 때 실제로 읽는 키. 없으면 값·지역이 소실되므로 중단합니다.
USED_KEYS = ["indicator", "countryiso3code", "date", "value"]
DATA_PATH = 1               # 명세서 data_path: [1]
DEFAULT_PER_PAGE = 500      # 명세서 기본값은 50. 페이지 수를 줄이려 명시 지정
SOURCE = "WORLDBANK"        # scripts/normalize.py · from_worldbank 과 동일한 표기
UNIT = "명"                 # 응답 unit 필드는 빈 문자열이라 여기서 부여
VINTAGE = "연간"            # config/sources.yml · wb_tfr

COLUMNS = ["source", "indicator_code", "region_code", "period", "value", "unit",
           "vintage", "retrieved_at", "source_url", "missing_reason"]
MISSING_CODES = {"NA_NOTSURVEYED", "NA_NOTAPPLICABLE", "NA_CONFIDENTIAL"}

RETRY = 3
BACKOFF = 1.5
RATE_LIMIT_WAIT = 5.0


class CollectError(RuntimeError):
    """수집 중단. 산출물을 만들지 않습니다."""


def _request(session, url, params, timeout=30):
    """실패 1·2·3 을 처리하는 요청 한 건."""
    last = None
    for attempt in range(RETRY):
        try:
            r = session.get(url, params=params, timeout=timeout)

            # 실패 2 — 호출 제한
            if r.status_code == 429:
                if attempt < RETRY - 1:
                    time.sleep(RATE_LIMIT_WAIT * (attempt + 1))
                    last = CollectError("호출 제한(429)")
                    continue
                raise CollectError(
                    f"호출 제한(429)이 {RETRY}회 계속됐습니다. 수집 간격을 늘리세요. "
                    "명세서상 호출 제한 수치는 미확인 상태입니다."
                )

            r.raise_for_status()

            # 실패 3 — 비JSON 응답. 재시도하지 않습니다.
            try:
                return r.json()
            except ValueError:
                body = (getattr(r, "text", "") or "").strip().replace("\n", " ")[:300]
                raise CollectError(
                    "응답 본문이 JSON이 아닙니다. format=json 과 요청 주소를 확인하세요. "
                    "(명세서 주의: format 을 빼면 XML 이 옵니다)\n"
                    f"    본문: {body or '(본문 없음)'}"
                ) from None

        except CollectError:
            raise
        except Exception as e:  # noqa: BLE001 — 실패 1: 타임아웃·5xx 등 일시 장애
            last = e
            if attempt < RETRY - 1:
                time.sleep(BACKOFF ** attempt)

    # 실패 1 — 상한을 넘기면 멈춥니다. 무한 재시도하지 않습니다.
    raise CollectError(f"{RETRY}회 재시도 후 실패: {last}")


def _check_shape(payload):
    """명세서의 응답 최상위 구조를 강제합니다."""
    if not isinstance(payload, list) or len(payload) != 2:
        got = type(payload).__name__
        if hasattr(payload, "__len__"):
            got += f" len={len(payload)}"
        head = json.dumps(payload, ensure_ascii=False)[:300] if payload is not None else "None"
        raise CollectError(
            "응답 최상위가 원소 2개짜리 배열이 아닙니다. 대개 에러 응답입니다.\n"
            f"    실제: {got}\n    본문: {head}"
        )
    meta, rows = payload[0], payload[DATA_PATH]
    if not isinstance(meta, dict) or not isinstance(rows, list):
        raise CollectError(
            f"[0]=dict, [{DATA_PATH}]=list 여야 합니다. "
            f"실제 [0]={type(meta).__name__}, [{DATA_PATH}]={type(rows).__name__}"
        )
    # lastupdated 는 빠지는 사례가 있어 페이징 키만 필수로 봅니다.
    hard = [k for k in META_KEYS if k not in meta and k != "lastupdated"]
    if hard:
        raise CollectError(f"메타에 페이징 키가 없습니다: 누락 {hard} / 실제 키 {sorted(meta)}")
    return meta, rows


def _check_keys(rows, log=print):
    """실패 5 — 스키마 드리프트. 명세서 키 8개를 전 행에 대해 대조.

    USED_KEYS 는 표준 스키마로 옮길 때 실제로 읽는 키입니다. 하나라도 없으면
    값이나 지역이 소실되므로 중단합니다. 나머지 명세서 키는 읽지 않으므로
    없어도 산출물은 정확합니다. 다만 명세서와 응답이 벌어졌다는 신호이므로
    경고로 남깁니다. 중단과 경고를 구분하지 않으면 둘 중 하나를 포기하게 됩니다.
    """
    missing = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CollectError(f"관측치 {i}번이 객체가 아닙니다: {type(row).__name__}")
        for k in OBS_KEYS:
            if k not in row:
                missing.setdefault(k, []).append(i)

    seen = sorted({k for r in rows for k in r})
    hard = {k: v for k, v in missing.items() if k in USED_KEYS}
    if hard:
        raise CollectError(
            "응답 키가 명세서와 다릅니다. 스키마 드리프트입니다.\n"
            f"    누락(수집에 필요): { {k: f'{len(v)}행' for k, v in hard.items()} }\n"
            f"    실제 키: {seen}\n"
            f"    명세서 키: {OBS_KEYS}\n"
            "    → 코드에서 키를 보정하기 전에 references/api-registry.md 를 먼저 갱신하세요."
        )
    soft = {k: len(v) for k, v in missing.items() if k not in USED_KEYS}
    if soft:
        log(f"  경고: 명세서에 있으나 응답에 없는 키 {soft} · 실제 키 {seen}")
    extra = [k for k in seen if k not in OBS_KEYS]
    if extra:
        log(f"  경고: 명세서에 없는 키가 응답에 있습니다 {extra}")
    return {"누락_경고": soft, "추가_키": extra}


def _collect_pages(session, url, base_params):
    """실패 4 — 조용한 실패 · 부분 수집."""
    meta, rows = _check_shape(_request(session, url, dict(base_params, page=1)))
    total = int(meta.get("total", 0))
    pages = int(meta.get("pages", 1))

    if total == 0 or not rows:
        raise CollectError(
            f"수집 0건입니다. 요청 조건을 확인하세요 (total={total}, 받은행={len(rows)}). "
            "0건을 정상 종료로 처리하지 않습니다."
        )

    for p in range(2, pages + 1):
        _m, chunk = _check_shape(_request(session, url, dict(base_params, page=p)))
        if not chunk:
            raise CollectError(f"{p}쪽이 비어 있습니다. pages={pages} 와 맞지 않습니다.")
        rows.extend(chunk)

    if len(rows) != total:
        raise CollectError(
            f"부분 수집 {len(rows)}/{total}건. 페이지 처리를 확인하세요. "
            f"(pages={pages}, per_page={meta.get('per_page')})"
        )
    return meta, rows


def to_schema(rows, source_url, missing_reason, retrieved_at=None):
    """표준 스키마 10컬럼으로 변환. references/schema.md"""
    if missing_reason not in MISSING_CODES:
        raise CollectError(
            f"결측 사유 코드가 필요합니다. {sorted(MISSING_CODES)} 중 하나.\n"
            "    World Bank 응답은 결측 사유를 담지 않습니다(obs_status 가 빈 문자열).\n"
            "    수집 시점에 정하지 않으면 이후 복원할 방법이 없습니다."
        )
    ts = retrieved_at or datetime.now(KST).isoformat(timespec="seconds")
    out = []
    for x in rows:
        region = x.get("countryiso3code")
        if not region:                       # 실패 5 의 마지막 방어선
            raise CollectError(
                f"region_code 가 비었습니다. 지역 소실: {json.dumps(x, ensure_ascii=False)[:200]}"
            )
        period = int(x["date"])
        if not 1900 <= period <= 2100:       # schema.md · period 1900~2100
            raise CollectError(f"period 범위 이탈: {period}")
        v = x["value"]
        value = None if v is None else float(v)
        out.append(dict(zip(COLUMNS, [
            SOURCE, x["indicator"]["id"], region, period, value, UNIT, VINTAGE,
            ts, source_url, None if value is not None else missing_reason,
        ])))
    return out


def collect(country, indicator, start, end, missing_reason, session=None,
            per_page=DEFAULT_PER_PAGE, log=print):
    """명세서대로 수집해 표준 스키마 행 목록을 돌려줍니다."""
    if session is None:
        import requests
        session = requests
    url = ENDPOINT.format(country=country, indicator=indicator)
    params = {"format": "json", "date": f"{start}:{end}", "per_page": per_page}
    log(f"  GET {url}?{urlencode(params)}")

    meta, rows = _collect_pages(session, url, params)
    _check_keys(rows, log=log)
    log(f"  수집 {len(rows)}건 (total={meta.get('total')}, pages={meta.get('pages')}, "
        f"lastupdated={meta.get('lastupdated', '없음')})")

    out = to_schema(rows, f"{url}?{urlencode(params)}", missing_reason)
    miss = sum(1 for r in out if r["value"] is None)
    log(f"  표준 스키마 {len(out)}행 · 결측 {miss}행 → {missing_reason}")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="World Bank 수집 → 표준 스키마")
    ap.add_argument("--country", default="KOR;JPN;FRA;DEU;ITA;ESP;USA")
    ap.add_argument("--indicator", default="SP.DYN.TFRT.IN")
    ap.add_argument("--start", type=int, default=2015)
    ap.add_argument("--end", type=int, default=2025)
    ap.add_argument("--per-page", type=int, default=DEFAULT_PER_PAGE)
    ap.add_argument("--missing-reason", choices=sorted(MISSING_CODES), required=True,
                    help="응답이 사유를 주지 않으므로 수집 시점에 지정해야 합니다")
    ap.add_argument("--out", help="CSV 저장 경로 (생략 시 미저장)")
    a = ap.parse_args(argv)

    try:
        rows = collect(a.country, a.indicator, a.start, a.end, a.missing_reason,
                       per_page=a.per_page)
    except CollectError as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    print("\n| " + " | ".join(COLUMNS) + " |")
    print("|" + "---|" * len(COLUMNS))
    for r in rows[:5]:
        print("| " + " | ".join("" if r[c] is None else str(r[c]) for c in COLUMNS) + " |")
    if len(rows) > 5:
        print(f"... 총 {len(rows)}행")

    if a.out:
        import csv
        with open(a.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        print(f"\n저장: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
