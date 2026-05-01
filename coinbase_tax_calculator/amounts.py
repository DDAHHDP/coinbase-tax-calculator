from __future__ import annotations

from decimal import Decimal


ZERO = Decimal("0")


def parse_decimal(value: str) -> Decimal | None:
    cleaned = value.strip().strip('"')
    if cleaned in {"", "N/A"}:
        return None

    is_negative = cleaned.startswith("(") and cleaned.endswith(")")
    if is_negative:
        cleaned = cleaned[1:-1]

    cleaned = cleaned.replace("$", "").replace(",", "").strip()
    number = Decimal(cleaned)
    return -number if is_negative else number


def decimal_to_str(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")
