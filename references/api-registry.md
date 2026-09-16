# 소스 레지스트리

2교시에서 만든 요청 명세서가 여기에 쌓입니다. **다른 기관으로 옮길 때 고치는 파일이 이것입니다.**

각 항목은 실제 호출 1건으로 검증한 뒤에 등록합니다. 문서와 응답이 다르면 응답이 맞습니다.

---

## World Bank Indicators

- 엔드포인트: `https://api.worldbank.org/v2/country/{country}/indicator/{indicator}`
- 인증: 불필요. 인증 헤더·키 없이 200 수신 확인
- 필수 파라미터: `country` (ISO3, 세미콜론으로 복수 지정), `indicator`
- 선택 파라미터: `format=json` (기본값은 xml), `date=2015:2025`, `per_page` (기본값 50, 응답으로 확인), `page` (1부터)
- 응답 최상위: **원소 2개짜리 배열**. `[0]`은 페이징 정보, `[1]`은 관측치 배열
- `data_path`: `[1]`
- 응답 키 — 메타 `[0]` 5개: `page` `pages` `per_page` `total` `lastupdated`
- 응답 키 — 관측치 `[1][]` 8개: `indicator` `country` `countryiso3code` `date` `value` `unit` `obs_status` `decimal`. 문서에 없는 추가 키는 나오지 않음
- 페이지 처리: **받은 행 수와 `[0].total`을 비교.** 받은 행이 적으면 뒷장이 남아 있음. `total`과 `per_page` 비교는 보조 지표일 뿐입니다 — `per_page`는 요청한 상한이고 서버가 깎아서 줄 수 있어 둘이 갈릴 수 있습니다. `collect.py`의 판정 기준도 행 수 비교입니다
- 결측: `value`가 `null`. **사유는 알 수 없습니다** — `obs_status` 필드는 있으나 값이 있는 행·없는 행 모두 빈 문자열이고, 메타에도 각주 키가 없습니다. `missing_reason`은 수집 시점에 따로 정해야 합니다
- 에러 응답: **미확인.** 문서에 기술 없음. 정상 호출만으로는 확인 불가
- 호출 제한: **문서에 없음. 미확인**
- 출처 표기 문구: 문서에 없음
- 최종 확인: **2026-09-16 실호출 1건으로 7개 검사 전량 통과** (`GET /v2/country/KOR/indicator/SP.DYN.TFRT.IN?format=json&date=2015:2025` → 200, `total=11 per_page=50 pages=1`, 11행 수신)
  - 검증 스크립트: `exercises/02/verify_spec.py` · 명세서: `exercises/02/spec_worldbank.json` (`verified_at` / `verified_result`)
  - 스크립트는 기대값을 명세서에서 읽어 대조하므로, 명세서를 고치면 그대로 재검증됩니다. 실패 시 exit 1

**주의** — `format`을 빼면 XML이 옵니다. 문서를 대충 읽으면 반드시 걸리는 함정입니다.

---

## KOSIS 공유서비스

- 엔드포인트: `https://kosis.kr/openapi/Param/statisticsParameterData.do`
- 인증: 필요. `apiKey` 파라미터. 환경변수 `KOSIS_API_KEY`
- 필수 파라미터: `method=getList`, `apiKey`, `orgId`, `tblId`, `itmId`, `objL1`, `prdSe`(Y/Q/M), `startPrdDe`, `endPrdDe`, `format=json`, `jsonVD=Y`
- **`method` · `itmId` · `objL1` · `format` · `jsonVD` 중 하나라도 빠지면** 200과 함께 에러 객체가 옵니다. 표준 JSON이 아니라 파싱에서 터집니다
- 응답: 평평한 객체 배열. 지역은 `C1_NM`, 기간은 `PRD_DE`, 값은 `DT`(문자열)
- 결측: `DT`가 빈 문자열
- 호출 제한: 발급 등급에 따라 다름. 발급 화면에서 확인 후 여기에 기록할 것
- 통계표: `DT_1B81A21` 시도/합계출산율. 항목 `itmId=T1`. 분류 `objL1=ALL` 이면 전국+17개 시도
- **`DT_1B81A17` 은 시군구 표입니다.** 시도 단위로 착각해 쓰면 `err=21` 이 납니다
- 표 찾기: `https://kosis.kr/openapi/statisticsSearch.do?method=getList&apiKey=...&searchNm=합계출산율&format=json&jsonVD=Y`
- 주의: `UNIT_NM` 이 표의 8개 항목 단위를 합친 문자열로 옵니다
- 최종 확인: 2026-09-16 실시간 호출 198행 검증 완료

### 시도/출생아수 — `DT_1B8000H`

- 통계표: `DT_1B8000H` **시도/인구동태건수 및 동태율(출생·사망·혼인·이혼)**
- 항목: `itmId=T10` **출생건수 (명)**. 같은 표에 T11 조출생률, T12 합계출산율, T20 사망건수 등 11개 항목
- 분류: `objL1=ALL` 이면 **전국 + 17개 시도 + `국외`** = 19개
- **`국외` 는 `references/region-codes.csv` 에 없습니다.** 2015~2025 전 구간이 결측이라 값은 없지만, 분류값으로는 옵니다
- **결측 표기가 두 가지입니다: 빈 문자열과 `-`.** 이 표에서는 `-` 로 왔습니다. `float()` 전에 둘 다 걸러야 합니다
- **`UNIT_NM` 이 없습니다(`None`).** 단위는 `config/sources.yml` 에서 부여해야 합니다. 항목명 `출생건수 (명)` 안에 단위가 들어 있습니다
- 응답 키: `C1` `C1_NM` `C1_NM_ENG` `C1_OBJ_NM` `C1_OBJ_NM_ENG` `DT` `ITM_ID` `ITM_NM` `ITM_NM_ENG` `LST_CHN_DE` `ORG_ID` `PRD_DE` `PRD_SE` `TBL_ID` `TBL_NM` (합계출산율 표보다 키가 많습니다)
- 값 범위(2015~2025 실측): 전국 230,028~438,420 · 시도별 2,708~113,495
- 최종 확인: **2026-09-16 실시간 호출 203행 검증 완료** (값 198 · 결측 5)

**막다른 길로 확인된 것** — 같은 지표를 찾다가 확인했습니다. 다시 시도하지 마세요.

- `DT_1B81A21` (시도/합계출산율): 항목 8개가 전부 출산율 계열(`T1` 합계출산율, `T2`~`T8` 모의 연령별 출산율). **출생아수 없음**
- `DT_1B81A01` + `itmId=T20` + `objL1=ALL`: `err=21` (잘못된 요청 변수). `raw/sample/kosis_births.json` 스냅샷이 이 조합으로 되어 있으나 **실제 API 와 다릅니다.** 수업용 픽스처입니다
- `INH_1B81A01` (출생아수): 분류가 **시군구 258개**입니다. 시도 단위가 아닙니다
- 표 검색: `https://kosis.kr/openapi/statisticsSearch.do?method=getList&apiKey=...&searchNm=출생아수&format=json&jsonVD=Y` — 정상 동작
- **MCP `korean-stat` 서버의 `search_statistics` 는 어떤 키워드에도 0건을 돌려줍니다.** 표 탐색에 쓸 수 없습니다 (2026-09-16 확인)

**주의** — `DT`가 문자열로 옵니다. `float()` 변환 전에 빈 문자열을 걸러야 합니다.

---

## OECD Data Explorer

- 엔드포인트 계열: `https://sdmx.oecd.org/public/rest/data/...`
- SDMX 구조. dataflow / dimension / codelist 개념을 먼저 이해해야 합니다
- **접근 경로가 개편된 이력이 있습니다.** 강의 전 반드시 실제 호출로 재확인하고 확인일자를 갱신할 것
- 최종 확인: (미검증)

---

## Eurostat

- 데이터브라우저에서 무료 내려받기 가능. SDMX API 제공
- 기업 AI 활용률 등 디지털 지표는 `isoc_` 계열
- 최종 확인: (미검증)

---

## 등록 양식

새 소스를 추가할 때 아래를 채우고, 실제 호출로 검증한 뒤 확인일자를 남기세요.

```
- 엔드포인트:
- 인증:
- 필수 파라미터:
- 선택 파라미터:
- 응답 최상위 구조:
- data_path:
- 페이지 처리:
- 결측 표기:
- 호출 제한:
- 출처 표기 문구:
- 최종 확인:
```
