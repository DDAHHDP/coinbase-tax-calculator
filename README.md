# Coinbase Tax Calculator
This project generates auditable Coinbase tax CSVs for perpetual futures and BTC Intx spot disposals. The active workflow is intentionally narrow: private inputs live under `transaction_data/`, generated reports live under `reports/`, and the default CLI mode produces the final unified report bundle.

## Original Project
This repository is derived from the original
[`coinbase-tax-calculator`](https://github.com/tjvick/coinbase-tax-calculator)
created by Tyler Vick. Many thanks to Tyler for the original foundation and
public starting point for this work.

## Disclaimer
This software is provided for entertainment and educational purposes only. It is
not tax, legal, or accounting advice, and it should not be used to generate,
sign, submit, or file official tax documents. Review all outputs manually and
consult a qualified professional before relying on them for any real-world
decision.

## License Status
The original upstream repository history available in this local clone does not
include an explicit open-source license grant. Because of that, this branch does
not add an MIT license or any broader relicensing claim over the original code.
See [NOTICE.md](NOTICE.md) for provenance and licensing context.

## Supported Workflow
- Multi-year Coinbase CSV exports: `transaction_data/coinbase_pro_transactions_2024.csv`, `transaction_data/coinbase_pro_transactions_2025.csv`, etc.
- Coinbase filled-orders markdown for perpetual realized PnL: `transaction_data/filled_orders/AllFilledOrders.md`.
- Manual delisting or suspended-product close adjustments: `transaction_data/manual_trade_list_adjustments.yaml`.
- External BTC cost basis: `transaction_data/btc_cost_basis.csv`.
- Perpetual futures tax report: filled-order realized PnL plus funding income, less funding costs and trading fees.
- BTC Intx spot report: HIFO allocation over eligible BTC lots, with incomplete basis excluded from known taxable totals.
- Unified final report: yearly and `ALL` totals combining perpetual futures and known BTC Intx spot taxable PnL.

## Install
```sh
poetry install
```

For notebook work:
```sh
poetry install --with dev
```

## Input Layout
Place private transaction data here:
```text
transaction_data/
  coinbase_pro_transactions_2024.csv
  coinbase_pro_transactions_2025.csv
  filled_orders/
    AllFilledOrders.md
  btc_cost_basis.csv
  manual_trade_list_adjustments.yaml
  raw_btc_buys/
```

The `transaction_data/` contents are ignored by git.

`coinbase_pro_transactions_20XX.csv`

These yearly CSV files should be downloaded directly from Coinbase Statements at:
[accounts.coinbase.com/statements](https://accounts.coinbase.com/statements)

Use Coinbase's CSV export. Keep the original yearly naming pattern:
- `coinbase_pro_transactions_2024.csv`
- `coinbase_pro_transactions_2025.csv`

`filled_orders/AllFilledOrders.md`

This file should be generated from the Coinbase Orders page at:
[coinbase.com/orders](https://www.coinbase.com/orders)

The current workflow is:
1. Open the filled orders list on Coinbase.
2. Use the MarkDownload Chrome extension:
   [Modern MarkDownload - Markdown Web Clipper](https://chromewebstore.google.com/detail/modern-markdownload-markd/lfihmpgmbpingelkgmodjgghdcjeiikk)
3. Export the page as markdown.
4. Save the result as `transaction_data/filled_orders/AllFilledOrders.md`.

The parser expects the markdown export to contain one filled-order record as 11 non-empty lines in this exact order:
```text
MM/DD/YY HH:MM:SS
Perpetuals
BTC PERP
Limit
Buy
104321.25 USDC
0.0105
100%
\-- / --
1095.37 USDC
Filled
```

Important details:
- Blank lines are ignored.
- Only perpetual products are read from this file.
- The product line should look like `BTC PERP`, `ETH PERP`, etc.
- The side line should be `Buy` or `Sell`.
- Price and notional may include `USDC` and commas.
- The final line must be `Filled` for the order to be included.

`btc_cost_basis.csv`

This file is a manually maintained BTC inventory input for BTC transferred into Coinbase from outside sources. It must contain a header row with these columns:
```csv
Product,Size,Cost,Date,Notes
BTC,1,"40000",2023-01-01,Example external BTC lot
BTC,0.25,"12500",2023 06 01,Another external lot
```

Field requirements:
- `Product`: must be `BTC`
- `Size`: BTC quantity for the lot
- `Cost`: BTC unit price in USD, not total lot cost
- `Date`: acquisition timestamp or date
- `Notes`: optional free text for audit context

Accepted `Date` formats:
- `YYYY-MM-DD HH:MM:SS`
- `YYYY-MM-DD HH:MM`
- `YYYY-MM-DD`
- `YYYY MM DD`
- `YYYY/MM/DD`

Important details:
- For the dedicated BTC spot report, `Cost` is treated as BTC unit price.
- The report writes both `unit_price_usd` and `total_cost_usd` for audit review.
- If a row has invalid BTC size, invalid unit cost, or unsupported date format, the loader raises instead of guessing.

`manual_trade_list_adjustments.yaml`

This file is constructed manually from Coinbase support or compliance communications. In the current workflow, the list of delisted perpetual products, along with the relevant close time and price context, was provided by Coinbase via email and then encoded manually into `manual_trade_list_adjustments.yaml`.

The active parser uses entries with IDs like:
```text
suspended_PROMPT-PERP_2026-02-20-13:00
```

and reads them as synthetic forced-close events for delisted products.

## Run Reports
Generate the final unified report bundle:
```sh
poetry run python calculate_taxes.py
```

The default command writes to:
```text
reports/final/
```

Generate only the futures tax report:
```sh
poetry run python calculate_taxes.py --report-mode futures-tax
```

Generate only the BTC spot report:
```sh
poetry run python calculate_taxes.py --report-mode spot-btc
```

Generate filled-order perpetual PnL for one year:
```sh
poetry run python calculate_taxes.py --year 2025 --report-mode filled-orders
```

All defaults can be overridden:
```sh
poetry run python calculate_taxes.py --report-mode unified --input-glob "transaction_data/coinbase_pro_transactions_*.csv" --filled-orders transaction_data/filled_orders/AllFilledOrders.md --cost-basis transaction_data/btc_cost_basis.csv --manual-adjustments transaction_data/manual_trade_list_adjustments.yaml --output-dir reports/custom
```

## Output Layout
Default report directories:
```text
reports/
  final/
  futures_tax/
  spot_btc/
  filled_orders/
```

The final unified bundle contains:
- `reports/final/unified_tax_summary_by_year.csv`
- `reports/final/futures_tax_summary_by_year.csv`
- `reports/final/futures_tax_by_product_year.csv`
- `reports/final/Spot_BTC_buys.csv`
- `reports/final/Spot_BTC_sells.csv`
- `reports/final/Spot_BTC_tax_by_sell.csv`
- `reports/final/Spot_BTC_tax_lots.csv`
- `reports/final/Spot_BTC_tax_summary_by_year.csv`

`unified_tax_summary_by_year.csv` contains one row per detected year plus an `ALL` row:
```text
perpetual_taxable_or_deductible_amount_usd + intx_btc_taxable_gain_loss_known_usd
```

`futures_tax_summary_by_year.csv` uses:
```text
realized_pnl_usd + funding_income_received_usd - funding_costs_paid_usd - trading_fees_paid_usd
```

`Spot_BTC_tax_summary_by_year.csv` uses:
```text
net_proceeds_usd - HIFO cost_basis_usd
```

## Delisted Perpetual Products
Entries in `manual_trade_list_adjustments.yaml` with IDs like `suspended_PROMPT-PERP_2026-02-20-13:00` and `order_function: CLOSE` are applied as synthetic closes in filled-order and futures-tax modes. Because the YAML has no settlement price, the synthetic close price is recorded as `0 USDC`, and position rows include `close_reason` and `close_source` for audit review.

## Safety Checks
The engine raises instead of guessing when:
- filled-orders markdown is malformed
- forced-close side conflicts with the active position
- BTC cost-basis rows have invalid sizes, costs, or dates

## Important Limitations
The reports are accounting summaries, not legal advice. Filing may require EUR conversion, tax category separation, exemptions, and non-negative category treatment. Review `unified_tax_summary_by_year.csv` together with the supporting futures and BTC audit CSVs before filing.

The filled-orders report computes realized PnL from fills only. It does not include funding fees, trading fees, settlements, deposits, withdrawals, or collateral spot disposals because those fields are not present in `AllFilledOrders.md`; the futures tax report adds funding and trading fees from the Coinbase CSV files.
