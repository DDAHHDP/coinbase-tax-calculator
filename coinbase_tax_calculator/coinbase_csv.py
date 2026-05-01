from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from .amounts import parse_decimal
from .models import CoinbaseRow


HEADER_PREFIX = "ID,Timestamp,Transaction Type,"


def load_coinbase_rows(path: Path) -> list[CoinbaseRow]:
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        lines = csv_file.readlines()

    header_index = _find_header_index(lines, path)
    reader = csv.DictReader(lines[header_index:])
    rows: list[CoinbaseRow] = []

    for row_number, row in enumerate(reader, start=1):
        rows.append(
            CoinbaseRow(
                source_file=path,
                source_row_number=row_number,
                transaction_id=row["ID"].strip(),
                timestamp=datetime.strptime(
                    row["Timestamp"].strip(), "%Y-%m-%d %H:%M:%S UTC"
                ).replace(tzinfo=timezone.utc),
                transaction_type=row["Transaction Type"].strip(),
                asset=row["Asset"].strip(),
                quantity_transacted=parse_decimal(row["Quantity Transacted"]),
                price_currency=row["Price Currency"].strip(),
                price_at_transaction=parse_decimal(row["Price at Transaction"]),
                subtotal_amount=parse_decimal(row["Subtotal"]),
                total_amount=parse_decimal(
                    row["Total (inclusive of fees and/or spread)"]
                ),
                fees_amount=parse_decimal(row["Fees and/or Spread"]),
                notes=row["Notes"].strip(),
            )
        )

    return rows


def _find_header_index(lines: list[str], path: Path) -> int:
    for index, line in enumerate(lines):
        if line.startswith(HEADER_PREFIX):
            return index
    raise ValueError(f"Could not locate Coinbase CSV header in {path}")
