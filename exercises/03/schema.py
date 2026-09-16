"""표준 스키마를 강제하는 데이터클래스.

근거: references/schema.md

이 파일의 목적은 '잘못된 행을 만들 수 없게' 하는 것입니다. 검사 함수를 따로
두고 나중에 부르는 방식은, 부르는 걸 잊으면 그대로 통과합니다. 생성자에서
막으면 잊을 수가 없습니다.

가장 중요한 규칙 하나:

    value 가 비어 있으면 missing_reason 이 반드시 있어야 한다.

결측 사유는 수집 시점에만 알 수 있습니다(references/schema.md). 그때 안 적으면
이후에 복원할 방법이 없고, 미조사·해당없음·비공개는 분석에서 취급이 서로
다릅니다. 빈칸 하나로 뭉개지지 않게 생성 자체를 실패시킵니다.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# references/schema.md · 컬럼 순서. 산출물의 열 순서가 이것으로 고정됩니다.
COLUMNS = ["source", "indicator_code", "region_code", "period", "value", "unit",
           "vintage", "retrieved_at", "source_url", "missing_reason"]

# references/schema.md · 결측을 셋으로 나누는 이유
MISSING_REASONS = {
    "NA_NOTSURVEYED": "미조사",       # 보간 검토 가능
    "NA_NOTAPPLICABLE": "해당없음",   # 보간 금지
    "NA_CONFIDENTIAL": "비공개",      # 값은 존재. 다른 경로 검토
}

# references/schema.md · vintage 는 넷 중 하나
VINTAGES = {"확정", "잠정", "추계", "연간"}

# references/schema.md · region_code
#   국내: KR- + 행정구역코드 2자리 / 국외: ISO 3166-1 alpha-3
RE_REGION_KR = re.compile(r"^KR-\d{2}$")
RE_REGION_ISO3 = re.compile(r"^[A-Z]{3}$")

PERIOD_MIN, PERIOD_MAX = 1900, 2100


class SchemaError(ValueError):
    """표준 스키마 위반. 행을 만들지 않습니다."""


def _require_text(name, v):
    if not isinstance(v, str) or not v.strip():
        raise SchemaError(f"{name}: 비어 있을 수 없습니다 (받은 값 {v!r})")
    return v.strip()


@dataclass(frozen=True, slots=True)
class Record:
    """표준 스키마 한 행. 규칙을 어기면 생성되지 않습니다.

    source | indicator_code | region_code | period | value | unit |
    vintage | retrieved_at | source_url | missing_reason
    """

    source: str
    indicator_code: str
    region_code: str
    period: int
    value: float | None
    unit: str
    vintage: str
    source_url: str
    missing_reason: str | None = None
    retrieved_at: str = field(default_factory=lambda: datetime.now(KST).isoformat(timespec="seconds"))

    def __post_init__(self):
        set_ = object.__setattr__      # frozen 이라 정규화는 이렇게 씁니다

        set_(self, "source", _require_text("source", self.source))
        set_(self, "indicator_code", _require_text("indicator_code", self.indicator_code))
        set_(self, "unit", _require_text("unit", self.unit))
        set_(self, "source_url", _require_text("source_url", self.source_url))

        # region_code — 국내 KR-NN / 국외 ISO3
        region = _require_text("region_code", self.region_code)
        if not (RE_REGION_KR.match(region) or RE_REGION_ISO3.match(region)):
            raise SchemaError(
                f"region_code: 국내는 'KR-'+2자리, 국외는 ISO 3166-1 alpha-3 여야 합니다 "
                f"(받은 값 {region!r}). 사전에 없는 지역명을 임의로 매핑하지 마세요."
            )
        set_(self, "region_code", region)

        # period — 연도 정수
        try:
            period = int(self.period)
        except (TypeError, ValueError):
            raise SchemaError(f"period: 연도 정수여야 합니다 (받은 값 {self.period!r})") from None
        if not PERIOD_MIN <= period <= PERIOD_MAX:
            raise SchemaError(f"period: {PERIOD_MIN}~{PERIOD_MAX} 범위여야 합니다 (받은 값 {period})")
        set_(self, "period", period)

        # vintage
        if self.vintage not in VINTAGES:
            raise SchemaError(
                f"vintage: {sorted(VINTAGES)} 중 하나여야 합니다 (받은 값 {self.vintage!r})"
            )

        # value — 빈 문자열·공백 문자열도 결측으로 봅니다
        value = self.value
        if isinstance(value, str):
            value = value.strip()
            value = None if value == "" else value
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise SchemaError(f"value: 실수 또는 공백이어야 합니다 (받은 값 {self.value!r})") from None
            if value != value:                       # NaN 은 결측을 뭉갠 흔적입니다
                raise SchemaError(
                    "value: NaN 은 허용하지 않습니다. 결측이면 value 를 비우고 "
                    "missing_reason 을 적으세요."
                )
        set_(self, "value", value)

        # missing_reason — 이 파일의 핵심 규칙
        reason = self.missing_reason
        if isinstance(reason, str):
            reason = reason.strip() or None
        set_(self, "missing_reason", reason)

        if value is None and reason is None:
            raise SchemaError(
                f"결측 사유 누락: {self.source}/{region}/{period}. "
                f"value 가 비면 missing_reason 이 반드시 있어야 합니다. "
                f"{sorted(MISSING_REASONS)} 중 하나를 수집 시점에 정하세요. "
                "지금 적지 않으면 이후에 복원할 방법이 없습니다."
            )
        if reason is not None and reason not in MISSING_REASONS:
            raise SchemaError(
                f"missing_reason: {sorted(MISSING_REASONS)} 중 하나여야 합니다 (받은 값 {reason!r})"
            )
        if value is not None and reason is not None:
            raise SchemaError(
                f"값과 결측 사유가 동시에 있습니다: value={value}, missing_reason={reason!r}. "
                "둘 중 하나가 틀렸습니다."
            )

        # retrieved_at — ISO 8601, KST
        ts = _require_text("retrieved_at", self.retrieved_at)
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            raise SchemaError(f"retrieved_at: ISO 8601 이어야 합니다 (받은 값 {ts!r})") from None
        if parsed.tzinfo is None:
            raise SchemaError(f"retrieved_at: 시간대가 없습니다. KST 오프셋이 필요합니다 ({ts!r})")
        if parsed.utcoffset() != KST.utcoffset(None):
            raise SchemaError(
                f"retrieved_at: KST(+09:00)여야 합니다 (받은 값 {ts!r}, "
                f"오프셋 {parsed.utcoffset()})"
            )
        set_(self, "retrieved_at", ts)

    @property
    def key(self):
        """references/schema.md 의 중복 판정 키."""
        return (self.indicator_code, self.region_code, self.period)

    @property
    def is_missing(self):
        return self.value is None

    def as_row(self):
        """표준 스키마 컬럼 순서의 dict. 빈 값은 빈 문자열로."""
        d = asdict(self)
        return {c: ("" if d[c] is None else d[c]) for c in COLUMNS}


def to_rows(records):
    """Record 목록 → 표준 스키마 dict 목록. 중복 키를 여기서 막습니다.

    중복 키(indicator_code + region_code + period)는 references/schema.md 의
    중단 조건입니다. 행 하나로는 알 수 없어 모음 단계에서 검사합니다.
    """
    seen = {}
    for i, r in enumerate(records):
        if not isinstance(r, Record):
            raise SchemaError(f"{i}번이 Record 가 아닙니다: {type(r).__name__}")
        if r.key in seen:
            raise SchemaError(
                f"중복 키: {r.key} (행 {seen[r.key]}번과 {i}번). "
                "indicator_code + region_code + period 는 유일해야 합니다."
            )
        seen[r.key] = i
    return [r.as_row() for r in records]


def to_dataframe(records):
    """pandas 가 있으면 DataFrame 으로. 컬럼 순서를 표준 스키마로 고정합니다."""
    import pandas as pd
    return pd.DataFrame(to_rows(records), columns=COLUMNS)

