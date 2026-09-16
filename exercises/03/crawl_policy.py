"""정책 페이지 크롤링 → 표준 스키마.

셀렉터는 inspect_selectors.py 로 조사한 결과입니다. 눈으로 훑어 정하지 않았습니다.

    section.region-card        반복 블록 (17개, 내부 구조 동일)
      └ [data-region]          지역명 (속성. 클래스보다 안정적)
      └ h2.region-name         지역명 (텍스트. 속성과 교차 검증용)
      └ ul.policy-list
          └ li.policy-item
              ├ span.policy-name   정책명
              └ span.policy-type   정책 구분

가져오기 전에 robots.txt 를 확인합니다. 로컬 파일이어도 같은 루틴을 태웁니다.
실제 사이트로 바꿀 때 이 단계를 빼먹지 않게 하려는 것입니다.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from schema import COLUMNS, KST, Record, SchemaError, to_rows  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAGE = os.path.join(BASE, "exercises", "03", "policy_page.html")

SOURCE = "POLICYBRIEF"
INDICATOR = "POLICY_COUNT"
UNIT = "건"                 # 정책 '건수'입니다. 명으로 환산할 수 있는 양이 아닙니다
VINTAGE = "확정"
USER_AGENT = "policy-data-pipeline/1.0 (research; contact via repo)"

SEL_CARD = "section.region-card"
SEL_NAME = "h2.region-name"
SEL_ITEM = "li.policy-item"
SEL_POLICY = "span.policy-name"
SEL_TYPE = "span.policy-type"

RE_UPDATED = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

# scripts/normalize.py 의 POLICY_TAXONOMY 와 같은 사전입니다.
# 여기 없는 정책명은 판단 대상이며, 임의로 분류하지 않습니다.
POLICY_TAXONOMY = {
    "출산장려금": "현금성 지원",
    "난임시술비 지원": "의료비 지원",
    "아이돌봄 서비스": "서비스 지원",
    "다자녀 우대카드": "할인·감면",
}


class CrawlError(RuntimeError):
    pass


# ── robots.txt 확인 루틴
def check_robots(target, user_agent=USER_AGENT, log=print):
    """가져오기 전에 robots.txt 를 확인합니다.

    로컬 파일이면 같은 폴더의 robots.txt 를, http(s) 면 같은 호스트의 것을 읽습니다.
    robots.txt 가 없으면 '허용'으로 간주하되, 없다는 사실을 로그에 남깁니다.
    Crawl-delay 가 있으면 그대로 지킵니다.
    """
    parsed = urlparse(target)
    rp = RobotFileParser()

    if parsed.scheme in ("http", "https"):
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        path = parsed.path or "/"
        log(f"  robots.txt 조회: {robots_url}")
        rp.set_url(robots_url)
        try:
            rp.read()
        except Exception as e:  # noqa: BLE001
            raise CrawlError(f"robots.txt 를 읽지 못했습니다: {e}. 확인 전에는 가져오지 않습니다.") from None
    else:
        robots_path = os.path.join(os.path.dirname(target), "robots.txt")
        path = "/" + os.path.basename(target)
        log(f"  robots.txt 조회: {os.path.relpath(robots_path, BASE)}")
        if not os.path.exists(robots_path):
            log("  robots.txt 없음 → 허용으로 간주 (근거 없음을 기록)")
            return {"허용": True, "crawl_delay": None, "robots": "없음"}
        with open(robots_path, encoding="utf-8") as f:
            rp.parse(f.read().splitlines())

    allowed = rp.can_fetch(user_agent, path)
    delay = rp.crawl_delay(user_agent)
    log(f"  User-agent: {user_agent}")
    log(f"  {path} 접근 {'허용' if allowed else '거부'}"
        + (f" · Crawl-delay {delay}초" if delay else " · Crawl-delay 없음"))

    if not allowed:
        raise CrawlError(
            f"robots.txt 가 {path} 수집을 허용하지 않습니다. 가져오지 않습니다.\n"
            "    규칙을 우회하지 마세요. 필요하면 기관에 이용 문의를 하십시오."
        )
    return {"허용": allowed, "crawl_delay": delay, "robots": "확인"}


def fetch(target, robots, log=print):
    """robots.txt 확인 뒤에만 부릅니다."""
    if not robots.get("허용"):
        raise CrawlError("robots.txt 확인을 통과하지 않았습니다")
    delay = robots.get("crawl_delay")
    if delay:
        log(f"  Crawl-delay {delay}초 대기")
        time.sleep(float(delay))

    parsed = urlparse(target)
    if parsed.scheme in ("http", "https"):
        import requests
        r = requests.get(target, headers={"User-Agent": USER_AGENT}, timeout=30)
        r.raise_for_status()
        return r.text
    with open(target, encoding="utf-8") as f:
        return f.read()


def region_map():
    path = os.path.join(BASE, "references", "region-codes.csv")
    with open(path, encoding="utf-8") as f:
        return {r["region_name"]: r["region_code"] for r in csv.DictReader(f)}


def parse(html, source_url, log=print):
    soup = BeautifulSoup(html, "html.parser")
    rmap = region_map()

    # 기간 — 페이지에 적힌 수집일에서 뽑습니다. 상수로 박지 않습니다.
    updated = soup.select_one("p.updated")
    m = RE_UPDATED.search(updated.get_text() if updated else "")
    if not m:
        raise CrawlError("페이지에서 수집일을 찾지 못했습니다. p.updated 셀렉터를 확인하세요.")
    period = int(m.group(1))
    log(f"  수집일 {m.group(0)} → period {period}")

    cards = soup.select(SEL_CARD)
    if not cards:
        raise CrawlError(f"반복 블록을 찾지 못했습니다: {SEL_CARD!r}. 페이지 구조가 바뀌었는지 확인하세요.")

    records, unmapped, pending, detail = [], [], [], []
    ts = datetime.now(KST).isoformat(timespec="seconds")

    for card in cards:
        attr = (card.get("data-region") or "").strip()
        text = card.select_one(SEL_NAME)
        text = text.get_text(strip=True) if text else ""
        # 속성과 텍스트가 어긋나면 구조가 바뀐 것입니다. 한쪽을 임의로 택하지 않습니다.
        if attr and text and attr != text:
            raise CrawlError(f"지역명 불일치: data-region={attr!r} vs {SEL_NAME}={text!r}")
        name = attr or text
        if not name:
            raise CrawlError("지역명을 찾지 못한 블록이 있습니다")

        items = card.select(SEL_ITEM)
        if not items:
            raise CrawlError(f"{name}: 정책 항목이 0건입니다 ({SEL_ITEM!r})")

        names = []
        for li in items:
            el = li.select_one(SEL_POLICY)
            if el is None:
                raise CrawlError(f"{name}: {SEL_POLICY!r} 가 없는 항목이 있습니다")
            pname = el.get_text(strip=True)
            ptype = li.select_one(SEL_TYPE)
            names.append(pname)
            detail.append({"지역명": name, "정책명": pname,
                           "구분": ptype.get_text(strip=True) if ptype else ""})

            # 규칙으로 분류되지 않는 정책명은 판단 대상으로 남깁니다.
            if pname not in POLICY_TAXONOMY and not any(p["원문"] == pname for p in pending):
                pending.append({
                    "원문": pname, "제안": "(미정)",
                    "근거": f"{SOURCE} · POLICY_TAXONOMY 에 없음",
                    "확인": "미확인",
                })

        code = rmap.get(name)
        if code is None:
            # 사전에 없는 지역명은 임의로 매핑하지 않습니다. 행을 만들지 않고 남깁니다.
            unmapped.append({"항목": name, "종류": "지역명", "소스": SOURCE})
            continue

        try:
            records.append(Record(
                source=SOURCE, indicator_code=INDICATOR, region_code=code,
                period=period, value=float(len(names)), unit=UNIT,
                vintage=VINTAGE, source_url=source_url, retrieved_at=ts,
            ))
        except SchemaError as e:
            raise CrawlError(f"{name}: {e}") from None

    log(f"  지역 블록 {len(cards)}개 · 정책 항목 {len(detail)}건 · 표준 스키마 {len(records)}행")
    if unmapped:
        log(f"  사전에 없는 지역명 {len(unmapped)}건 — 행을 만들지 않았습니다")
    if pending:
        log(f"  규칙으로 분류 못 한 정책명 {len(pending)}건 — 판단 대상")
    return records, unmapped, pending, detail


def crawl(target=PAGE, log=print):
    robots = check_robots(target, log=log)
    html = fetch(target, robots, log=log)
    url = target if urlparse(target).scheme in ("http", "https") else f"file://{target}"
    return parse(html, url, log=log)


def main(argv=None):
    ap = argparse.ArgumentParser(description="정책 페이지 크롤링 → 표준 스키마")
    ap.add_argument("target", nargs="?", default=PAGE)
    ap.add_argument("--out", help="CSV 저장 경로")
    a = ap.parse_args(argv)

    try:
        records, unmapped, pending, detail = crawl(a.target)
    except (CrawlError, SchemaError) as e:
        print(f"중단: {e}", file=sys.stderr)
        return 1

    rows = to_rows(records)
    print(f"\n표준 스키마 {len(rows)}행")
    for r in rows:
        print(f"  {r['region_code']} {r['period']} {r['value']}{r['unit']}")

    if pending:
        print(f"\n판정이 필요한 정책명 {len(pending)}건 (코드북 4절)")
        for p in pending:
            print(f"  - {p['원문']} · {p['근거']}")
    if unmapped:
        print(f"\n사전에 없는 지역명 {len(unmapped)}건")
        for u in unmapped:
            print(f"  - {u['항목']}")

    if a.out:
        import pandas as pd
        pd.DataFrame(rows, columns=COLUMNS).to_csv(a.out, index=False, encoding="utf-8-sig")
        print(f"\n저장: {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
