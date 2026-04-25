"""Holiday calendars + seasonality helpers for the forecasting layer.

Built-in calendars cover the regions Kairos expects to deploy in:
- US: federal holidays + Black Friday / Cyber Monday (commerce traffic)
- IN: major Indian public holidays
- EU: ECB common holidays + ChristmaWeek
- intl: a small union covering globally-observed dates

Each calendar yields (name, date) tuples Prophet can ingest as a holidays
DataFrame. Callers can union multiple calendars. The names are stable so
plot legends and audit logs stay consistent across versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# ── Static holiday tables ─────────────────────────────────────────────
# Hard-coded for 2025-2027 — refresh annually. We deliberately avoid pulling
# `holidays` PyPI as a runtime dep to keep the wheel small and offline-safe.

_US_HOLIDAYS: list[tuple[str, date]] = [
    ("us_new_year", date(2025, 1, 1)),
    ("us_mlk_day", date(2025, 1, 20)),
    ("us_memorial_day", date(2025, 5, 26)),
    ("us_independence_day", date(2025, 7, 4)),
    ("us_labor_day", date(2025, 9, 1)),
    ("us_thanksgiving", date(2025, 11, 27)),
    ("us_black_friday", date(2025, 11, 28)),
    ("us_cyber_monday", date(2025, 12, 1)),
    ("us_christmas", date(2025, 12, 25)),
    ("us_new_year", date(2026, 1, 1)),
    ("us_mlk_day", date(2026, 1, 19)),
    ("us_memorial_day", date(2026, 5, 25)),
    ("us_independence_day", date(2026, 7, 4)),
    ("us_labor_day", date(2026, 9, 7)),
    ("us_thanksgiving", date(2026, 11, 26)),
    ("us_black_friday", date(2026, 11, 27)),
    ("us_cyber_monday", date(2026, 11, 30)),
    ("us_christmas", date(2026, 12, 25)),
    ("us_new_year", date(2027, 1, 1)),
    ("us_mlk_day", date(2027, 1, 18)),
    ("us_memorial_day", date(2027, 5, 31)),
    ("us_independence_day", date(2027, 7, 4)),
    ("us_labor_day", date(2027, 9, 6)),
    ("us_thanksgiving", date(2027, 11, 25)),
    ("us_black_friday", date(2027, 11, 26)),
    ("us_cyber_monday", date(2027, 11, 29)),
    ("us_christmas", date(2027, 12, 25)),
]

_IN_HOLIDAYS: list[tuple[str, date]] = [
    ("in_republic_day", date(2025, 1, 26)),
    ("in_holi", date(2025, 3, 14)),
    ("in_independence_day", date(2025, 8, 15)),
    ("in_diwali", date(2025, 10, 20)),
    ("in_christmas", date(2025, 12, 25)),
    ("in_republic_day", date(2026, 1, 26)),
    ("in_holi", date(2026, 3, 4)),
    ("in_independence_day", date(2026, 8, 15)),
    ("in_diwali", date(2026, 11, 8)),
    ("in_christmas", date(2026, 12, 25)),
    ("in_republic_day", date(2027, 1, 26)),
    ("in_holi", date(2027, 3, 22)),
    ("in_independence_day", date(2027, 8, 15)),
    ("in_diwali", date(2027, 10, 28)),
    ("in_christmas", date(2027, 12, 25)),
]

_EU_HOLIDAYS: list[tuple[str, date]] = [
    ("eu_new_year", date(2025, 1, 1)),
    ("eu_good_friday", date(2025, 4, 18)),
    ("eu_easter_monday", date(2025, 4, 21)),
    ("eu_labour_day", date(2025, 5, 1)),
    ("eu_christmas", date(2025, 12, 25)),
    ("eu_boxing_day", date(2025, 12, 26)),
    ("eu_new_year", date(2026, 1, 1)),
    ("eu_good_friday", date(2026, 4, 3)),
    ("eu_easter_monday", date(2026, 4, 6)),
    ("eu_labour_day", date(2026, 5, 1)),
    ("eu_christmas", date(2026, 12, 25)),
    ("eu_boxing_day", date(2026, 12, 26)),
    ("eu_new_year", date(2027, 1, 1)),
    ("eu_good_friday", date(2027, 3, 26)),
    ("eu_easter_monday", date(2027, 3, 29)),
    ("eu_labour_day", date(2027, 5, 1)),
    ("eu_christmas", date(2027, 12, 25)),
    ("eu_boxing_day", date(2027, 12, 26)),
]

CALENDARS: dict[str, list[tuple[str, date]]] = {
    "us": _US_HOLIDAYS,
    "in": _IN_HOLIDAYS,
    "eu": _EU_HOLIDAYS,
    "intl": _US_HOLIDAYS + _EU_HOLIDAYS,  # union — for global products
}


@dataclass(frozen=True)
class HolidayWindow:
    """One row in a holidays DataFrame Prophet accepts.

    Prophet's `holidays` DF columns: holiday (str), ds (timestamp),
    lower_window (int days before), upper_window (int days after).
    """

    name: str
    day: date
    lower_window: int = -1  # day-before traffic ramp
    upper_window: int = 1  # day-after recovery


def union_calendars(regions: list[str]) -> list[HolidayWindow]:
    """Merge selected regional calendars; preserves duplicates as separate rows.

    Prophet accepts multiple rows for the same date — useful when a date is
    a holiday in multiple regions (Christmas in US + EU). Each retains its
    region-prefixed name so the audit trail is unambiguous.
    """
    out: list[HolidayWindow] = []
    seen: set[tuple[str, date]] = set()
    for region in regions:
        for name, day in CALENDARS.get(region.lower(), []):
            key = (name, day)
            if key in seen:
                continue
            seen.add(key)
            out.append(HolidayWindow(name=name, day=day))
    return out


__all__ = ["CALENDARS", "HolidayWindow", "union_calendars"]
