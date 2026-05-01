from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class CoinbaseRow:
    source_file: Path
    source_row_number: int
    transaction_id: str
    timestamp: datetime
    transaction_type: str
    asset: str
    quantity_transacted: Decimal | None
    price_currency: str
    price_at_transaction: Decimal | None
    subtotal_amount: Decimal | None
    total_amount: Decimal | None
    fees_amount: Decimal | None
    notes: str
