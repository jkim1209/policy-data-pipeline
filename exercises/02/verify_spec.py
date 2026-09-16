"""spec_worldbank.json 실호출 검증. 요청 1건으로 6개 항목을 확인합니다.

기대값은 전부 명세서에서 읽어옵니다. 스크립트에 상수로 박지 않습니다.
그래야 '명세서가 맞는지'를 검사하는 것이 됩니다.
"""
from __future__ import annotations
import json, os, re, sys
from datetime import datetime

import requests

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SPEC = os.path.join(BASE, "exercises", "02", "spec_worldbank.json")


def keys_in(text, label):
    """명세서 산문에서 '라벨(a, b, c)' 형태의 키 목록을 뽑습니다."""
    m = re.search(re.escape(label) + r"\(([^)]*)\)", text)
    return [k.strip() for k in m.group(1).split(",")] if m else []


def main():
    spec = json.load(open(SPEC, encoding="utf-8"))
    shape = spec["응답_최상위_구조"]
    exp_meta_keys = keys_in(shape, "페이징 정보 객체")
    exp_obs_keys = keys_in(shape, "관측치 객체 배열")
    data_path = spec["data_path"]

    # 명세서의 필수 파라미터(경로)와 선택 파라미터로 요청을 조립
    url = spec["엔드포인트"].format(country="KOR", indicator="SP.DYN.TFRT.IN")
    params = {"format": "json", "date": "2015:2025"}   # per_page 생략 → 명세서상 기본값 50
    print(f"요청: GET {url}")
    print(f"      params={params}  (인증 헤더/키 없음 — 명세서 '인증: 불필요' 확인용)\n")

    r = requests.get(url, params=params, timeout=30)
    rows_checked = []

    def check(name, expected, actual, ok):
        rows_checked.append({"검사": name, "기대(명세서)": str(expected),
                             "실제(응답)": str(actual), "판정": "통과" if ok else "실패"})
        return ok

    # 1. HTTP 200
    check("HTTP 200", "200", r.status_code, r.status_code == 200)

    # 2. JSON 파싱
    try:
        payload = r.json()
        parsed, detail = True, f"{type(payload).__name__} 파싱 성공"
    except ValueError as e:
        payload, parsed, detail = None, False, f"파싱 실패: {e}"
    check("JSON 파싱", "format=json 이면 JSON", detail, parsed)
    if not parsed:
        return report(rows_checked, spec, url, params, r)

    # 3. 응답 최상위 구조
    ok3 = (isinstance(payload, list) and len(payload) == 2
           and isinstance(payload[0], dict) and isinstance(payload[1], list))
    actual3 = (f"{type(payload).__name__} len={len(payload)}"
               + (f", [0]={type(payload[0]).__name__}, [1]={type(payload[1]).__name__}"
                  if isinstance(payload, list) and len(payload) == 2 else ""))
    check("응답 최상위 구조", "원소 2개 배열, [0]=dict, [1]=list", actual3, ok3)

    # 4. data_path 로 관측치 도달
    try:
        obs = payload[int(re.fullmatch(r"\[(\d+)\]", data_path).group(1))]
        ok4 = isinstance(obs, list) and len(obs) > 0 and isinstance(obs[0], dict)
        actual4 = f"{data_path} → list, {len(obs)}건, 원소는 dict"
    except Exception as e:  # noqa: BLE001
        obs, ok4, actual4 = [], False, f"도달 실패: {e}"
    check("data_path 로 관측치 도달", f"{data_path} 가 관측치 배열", actual4, ok4)

    # 5. 필수 키 존재 (메타 · 관측치 전 행)
    meta = payload[0]
    miss_meta = [k for k in exp_meta_keys if k not in meta]
    check("필수 키 존재 · 메타", f"{len(exp_meta_keys)}개 {exp_meta_keys}",
          "누락 없음" if not miss_meta else f"누락 {miss_meta}", not miss_meta)

    miss_obs = sorted({k for k in exp_obs_keys for row in obs if k not in row})
    extra_obs = sorted({k for row in obs for k in row} - set(exp_obs_keys))
    check("필수 키 존재 · 관측치 전 행", f"{len(exp_obs_keys)}개 {exp_obs_keys}",
          (f"{len(obs)}행 모두 보유" if not miss_obs else f"누락 {miss_obs}")
          + (f" / 명세서에 없는 키 {extra_obs}" if extra_obs else ""),
          not miss_obs)

    # 6. 페이지 여유
    total, per_page, page, pages, got = (int(meta.get("total", -1)), int(meta.get("per_page", -1)),
                                         int(meta.get("page", -1)), int(meta.get("pages", -1)), len(obs))
    remain = got < total
    check("페이지 여유", "받은 행 == total 이면 전량",
          f"total={total} per_page={per_page} page={page} pages={pages} 받은행={got}"
          f" → 뒷장 {'남음' if remain else '없음'}", not remain)

    return report(rows_checked, spec, url, params, r, meta_extra={
        "total": total, "per_page": per_page, "rows": got})


def report(rows, spec, url, params, r, meta_extra=None):
    w = [max(len(x["검사"]) for x in rows), 34, 62, 4]
    hdr = ["검사", "기대(명세서)", "실제(응답)", "판정"]
    print("| " + " | ".join(h.ljust(x) for h, x in zip(hdr, w)) + " |")
    print("|" + "|".join("-" * (x + 2) for x in w) + "|")
    for x in rows:
        print("| " + " | ".join(str(x[h])[:c].ljust(c) for h, c in zip(hdr, w)) + " |")

    failed = [x["검사"] for x in rows if x["판정"] == "실패"]
    verdict = "일치" if not failed else "불일치"
    print(f"\n{len(rows)}개 검사 중 통과 {len(rows) - len(failed)} / 실패 {len(failed)}")
    print(f"판정: 명세서와 실제 응답 {verdict}" + (f" — 실패 {failed}" if failed else ""))

    now = datetime.now().astimezone().isoformat(timespec="seconds")
    spec["verified_at"] = now
    spec["verified_result"] = {
        "판정": verdict,
        "요청": {"url": url, "params": params, "인증": "없음", "http_status": r.status_code},
        "검사": [{k: x[k] for k in ("검사", "기대(명세서)", "실제(응답)", "판정")} for x in rows],
        "통과": len(rows) - len(failed), "실패": len(failed),
        "검증_스크립트": "exercises/02/verify_spec.py",
    }
    if meta_extra:
        spec["verified_result"]["응답_요약"] = meta_extra
    with open(SPEC, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"\nspec_worldbank.json 갱신: verified_at={now}, verified_result 추가")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
