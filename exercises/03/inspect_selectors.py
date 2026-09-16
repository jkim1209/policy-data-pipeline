"""셀렉터 조사. 어느 태그·클래스에 무엇이 들어 있는지 코드로 셉니다.

HTML 을 눈으로 훑어 셀렉터를 정하면, 페이지가 길 때 놓치고 개편되면 못 알아챕니다.
여기서는 DOM 을 전수로 세서 '반복되는 구조'와 '값이 실린 자리'를 찾습니다.

출력 세 가지
  1) 클래스별 출현 횟수와 대표 텍스트
  2) data-* 속성 목록 (클래스보다 안정적인 경우가 많습니다)
  3) 반복 블록 후보 — 같은 클래스가 여러 번 나오고 내부 구조가 같은 것
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter, defaultdict

from bs4 import BeautifulSoup

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load(path):
    with open(path, encoding="utf-8") as f:
        return BeautifulSoup(f.read(), "html.parser")


def survey(soup):
    by_class = defaultdict(list)       # (tag, class) -> [텍스트]
    attrs = Counter()                  # data-* 속성
    for el in soup.find_all(True):
        classes = el.get("class") or []
        text = el.get_text(" ", strip=True)
        for c in classes:
            by_class[(el.name, c)].append(text)
        if not classes:
            by_class[(el.name, "")].append(text)
        for k in el.attrs:
            if k.startswith("data-"):
                attrs[f"{el.name}[{k}]"] += 1
    return by_class, attrs


def main(argv=None):
    ap = argparse.ArgumentParser(description="HTML 셀렉터 조사")
    ap.add_argument("path", nargs="?",
                    default=os.path.join(BASE, "exercises", "03", "policy_page.html"))
    ap.add_argument("--min", type=int, default=1, help="이 횟수 이상만 표시")
    a = ap.parse_args(argv)

    soup = load(a.path)
    by_class, attrs = survey(soup)
    print(f"파일: {os.path.relpath(a.path, BASE)}")
    print(f"제목: {soup.title.get_text(strip=True) if soup.title else '(없음)'}")
    print(f"전체 엘리먼트 {len(soup.find_all(True))}개\n")

    print("### 1. 태그·클래스별 출현")
    print("| 셀렉터 | 횟수 | 대표 텍스트 | 고유값 |")
    print("|:--|---:|:--|---:|")
    rows = sorted(by_class.items(), key=lambda kv: -len(kv[1]))
    for (tag, cls), texts in rows:
        if len(texts) < a.min:
            continue
        sel = f"{tag}.{cls}" if cls else tag
        sample = next((t for t in texts if t), "")
        sample = (sample[:34] + "…") if len(sample) > 34 else sample
        print(f"| `{sel}` | {len(texts)} | {sample or '(빈 텍스트)'} | {len(set(texts))} |")

    print("\n### 2. data-* 속성")
    if attrs:
        for k, n in attrs.most_common():
            print(f"- `{k}` — {n}회")
    else:
        print("- 없음")

    print("\n### 3. 반복 블록 후보")
    for (tag, cls), texts in rows:
        if not cls or len(texts) < 3:
            continue
        sel = f"{tag}.{cls}"
        els = soup.select(sel)
        # 내부 구조가 같은지 — 자식 클래스 조합을 비교
        shapes = Counter(
            tuple(sorted({c for ch in el.find_all(True) for c in (ch.get("class") or [])}))
            for el in els
        )
        same = len(shapes) == 1
        print(f"- `{sel}` × {len(els)} — 내부 구조 {'동일' if same else f'{len(shapes)}가지'}"
              + (f" · 자식 클래스 {list(shapes)[0]}" if same and list(shapes)[0] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
