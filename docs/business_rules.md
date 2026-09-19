# Phase 2 Business Rules and Data Contract

## Status and scope

This document is the implementation-ready Phase 2 contract for the Online Retail II project. It is based on the actual Phase 1 outputs and the official UCI dataset documentation. It defines decisions for Phase 3, but contains no transformation code.

Status: **frozen candidate awaiting human approval**. Phase 3 must not start until this document is approved.

This contract applies to the fixed source workbook whose Phase 1 SHA-256 is:

```text
BCBE73B35F5B7BABF197FB0CB983A11F5D9FF929078D4AA53D171B1F2DF2E980
```

Authoritative evidence:

- [`docs/data_profiling.md`](data_profiling.md)
- `data/profiling/*.csv`
- [UCI Online Retail II dataset documentation](https://archive.ics.uci.edu/dataset/502/online%2Bretail%2Bii)
- [UCI dataset DOI](https://doi.org/10.24432/C5CG6D)

UCI states that an invoice code beginning with `c` indicates a cancellation. UCI does not define negative non-C quantities as returns, does not define zero or negative price treatment, and does not define the project KPIs. Those decisions are frozen below from profiling evidence and explicit project policy.

## Profiling evidence used

| Area | Actual Phase 1 evidence |
| --- | --- |
| Volume | 1,067,371 source rows across 2 worksheets; worksheet counts reconcile exactly. |
| Schema | Both sheets have the same ordered 8-column schema. |
| Invoice | 53,628 distinct invoices; 0 null/blank; 6 nonstandard `A...` invoice values. |
| Cancellation pattern | 19,493 C-prefixed negative-quantity rows, 0 C-prefixed zero-quantity rows, and 1 C-prefixed positive-quantity row. |
| Non-C quantity pattern | 3,457 negative, 0 zero, and 1,044,420 positive rows. |
| Quantity | 0 null/invalid; range -80,995 to 80,995. |
| Price | 0 null/invalid; 5 negative, 6,202 zero, and 1,061,164 positive rows. |
| Negative price | All 5 rows are non-C, positive quantity, StockCode `B`, Description `Adjust bad debt`, missing Customer ID, and nonstandard `A...` invoices. |
| Customer ID | 243,007 null rows; 5,942 known IDs; no non-zero fractional IDs. |
| Description | 4,382 null rows; all are zero-price, non-C, and missing-customer rows. |
| Product description consistency | 3,718 StockCodes have one non-null description, 1,232 have multiple, and 355 have no non-null description. |
| StockCode | 5,305 distinct; 0 null/blank; numeric, alphabetic, mixed, and special codes all occur. |
| Country | 43 source values; 0 null/blank/outer-whitespace rows; 0 trim/case-equivalent variant groups. |
| Duplicate-looking rows | 67,242 rows participate in 32,907 exact-looking groups; the largest group contains 20 rows. |
| Date | 0 null/blank/invalid values; source range 2009-12-01 07:45:00 through 2011-12-09 12:50:00. |

The two sheet date ranges overlap. Some sampled transactions also occur in both sheets at different physical source locations. There is no trustworthy source invoice-line identifier with which to distinguish extraction duplication from legitimate repeated occurrences. The contract therefore preserves every physical source occurrence.

## Classification model

Every source row receives exactly one top-level classification after lossless parsing:

| Classification | Meaning | Pipeline behavior |
| --- | --- | --- |
| `ACCEPT` | Structurally usable and no approved quality flag applies. | Load to cleaned data, staging, and fact. |
| `ACCEPT WITH QUALITY FLAG` | Structurally usable but one or more documented suspicious conditions apply. | Retain, load, and expose the approved flags. KPI eligibility follows the predicates in this document. |
| `REJECT` | A required field cannot be represented safely, or the row is an observed non-merchandise/accounting adjustment outside the retail-sales fact scope. | Write to the rejected-record output with reason and lineage. Do not load to staging or fact. |

Precedence is `REJECT` over `ACCEPT WITH QUALITY FLAG` over `ACCEPT`. A row may have multiple quality flags, but it has only one top-level classification. Rejected rows are never silently discarded.

## Approved quality flags

These are the only row-level quality flags approved for Phase 3.

| Flag | Condition | Phase 1 evidence | KPI effect |
| --- | --- | --- | --- |
| `MISSING_CUSTOMER` | Source Customer ID is null/blank. | 243,007 rows. | Does not remove an otherwise qualifying line from sales/product/country KPIs. Excludes the row from known-customer KPIs. |
| `INVALID_CUSTOMER_ID` | Non-null Customer ID cannot be represented as an integral identifier. | 0 observed; all known IDs were integral-valued. | Map to UNKNOWN customer and exclude from known-customer KPIs. |
| `MISSING_DESCRIPTION` | Description is null/blank after trimming. | 4,382 rows. | Does not independently exclude a line from KPIs. |
| `ZERO_PRICE` | `unit_price = 0`. | 6,202 rows. | Exclude from the core commercial sale and core cancellation scopes so zero-price rows cannot inflate Orders, Units Sold, AOV, or customer counts. |
| `QUANTITY_SIGN_MISMATCH` | C-prefixed invoice with quantity `>= 0`, or non-C invoice with quantity `<= 0`. | C positive: 1; C zero: 0; non-C negative: 3,457; non-C zero: 0. | Exclude from the core commercial sale and core cancellation scopes. Do not label non-C negatives as returns. |
| `NONSTANDARD_INVOICE` | Normalized invoice is neither digits-only nor `C` followed by digits. | 6 `A...` rows. | Exclude from commercial KPIs, but retain if no reject rule also applies. |
| `MISSING_COUNTRY` | Country is null/blank after trimming. | 0 observed. | Map to UNKNOWN country; otherwise qualifying sales remain in overall KPIs. |

`NEGATIVE_PRICE_ADJUSTMENT` is a reject reason, not a quality flag. Special or alphabetic StockCodes do not receive a flag solely because of their syntax.

## Field contracts

### `invoice_no`

| Property | Contract |
| --- | --- |
| Business meaning | Source transaction/invoice identifier. A leading `C` denotes a cancellation according to UCI. |
| Expected source type | Pandas `object`; observed values may be numeric-looking or text. |
| Normalized target type | Non-null canonical text; SQL target `nvarchar(32)`. |
| Null policy | Null/blank is not usable. |
| Reject conditions | Null/blank after conversion and trimming: `MISSING_INVOICE`. |
| Retain conditions | Any nonblank value is retained unless another field causes rejection. |
| Quality-flag conditions | Values not matching `^[0-9]+$` or `^C[0-9]+$`: `NONSTANDARD_INVOICE`. |
| Transformation | If the source cell is numeric and integral, emit base-10 digits without a `.0` suffix. Otherwise convert to text, trim outer whitespace, and uppercase. Do not remove the `C` prefix. |
| Warehouse behavior | Store on the fact as a degenerate transaction identifier. Use the normalized value for distinct-order logic. Derive `is_cancelled` separately. |

The UCI description calls invoice numbers six-digit identifiers, but Phase 1 did not profile length as a validity rule. Phase 3 must therefore not reject an invoice solely because its digit count differs.

### `stock_code`

| Property | Contract |
| --- | --- |
| Business meaning | Product/item identifier supplied by the source. |
| Expected source type | Pandas `object`; numeric-looking, alphabetic, alphanumeric, and special values occur. |
| Normalized target type | Non-null canonical text; SQL target `nvarchar(64)`. |
| Null policy | Null/blank is not usable because the fact cannot resolve a product. |
| Reject conditions | Null/blank after conversion and trimming: `MISSING_STOCK_CODE`. |
| Retain conditions | Every nonblank code, including `POST`, `DOT`, `M`, `AMAZONFEE`, `BANK CHARGES`, gift codes, and other special values. |
| Quality-flag conditions | None based only on numeric/non-numeric syntax. |
| Transformation | If the source cell is numeric and integral, emit base-10 digits without `.0`. Otherwise convert to text and trim surrounding whitespace. Preserve source casing and leading zeroes already present in textual values. Do not uppercase automatically. |
| Warehouse behavior | One `dim_product` member per normalized StockCode. Do not discard or merge codes based on a manually invented product/non-product list. Use case-sensitive StockCode comparison/keying so SQL Server's default collation does not merge source-distinct text values solely by case. |

Phase 1 observed case variants but did not prove that they are semantically equivalent. StockCode therefore remains text with source casing preserved. Special and alphabetic values remain valid identifiers and are not rejected merely because they are nonnumeric.

### `description`

| Property | Contract |
| --- | --- |
| Business meaning | Source product/item description or source operational text. |
| Expected source type | Nullable Pandas `object`; some non-null values may be numeric-looking. |
| Normalized target type | Nullable text; SQL target `nvarchar(512)`. |
| Null policy | Null/blank is permitted. |
| Reject conditions | None based only on Description. |
| Retain conditions | Retain all non-null text and retain rows with missing Description. |
| Quality-flag conditions | Null/blank after trimming: `MISSING_DESCRIPTION`. |
| Transformation | Convert non-null values to text and trim outer whitespace. Preserve case and internal wording. Convert an empty result to null. |
| Warehouse behavior | Keep the line-level normalized description in staging. `dim_product` uses the deterministic canonical-description rule below. |

Canonical `dim_product.description` selection for each normalized, case-preserving StockCode:

1. Use non-null descriptions from core commercial sale lines first.
2. If no core commercial sale line has a description for that StockCode, use non-null descriptions from all accepted rows.
3. Group by the exact trimmed description and choose the value with the greatest source-occurrence count.
4. If counts tie, choose the value whose latest `invoice_datetime` is greatest.
5. If still tied, choose the lexically smallest trimmed description using ordinal Unicode code-point ordering.
6. If no non-null candidate exists, use the display value `UNKNOWN DESCRIPTION`.

The simple “latest non-null description” candidate is not approved. Phase 1 found 1,232 multi-description StockCodes, and examples show that rare recent values can be adjustment notes rather than product names. The frequency-first rule is supported by dominant observed descriptions and has deterministic tie behavior.

### `quantity`

| Property | Contract |
| --- | --- |
| Business meaning | Signed quantity recorded for an item occurrence. UCI does not define negative non-C quantities as returns. |
| Expected source type | Numeric integer; Phase 1 read `int64`. |
| Normalized target type | Signed integer; SQL target `int`. |
| Null policy | Null is not usable. |
| Reject conditions | Null, nonnumeric, non-finite, non-integral, or not losslessly representable as SQL `int`: `MISSING_QUANTITY` or `INVALID_QUANTITY`. |
| Retain conditions | Retain negative, zero, and positive integral values when structurally valid. |
| Quality-flag conditions | C with quantity `>= 0`, or non-C with quantity `<= 0`: `QUANTITY_SIGN_MISMATCH`. |
| Transformation | Parse losslessly to a signed integer. Never apply `ABS`, change the sign, or infer “return” from quantity alone. |
| Warehouse behavior | Store the signed value on the fact. KPI treatment is determined by the core predicates below. |

### `invoice_datetime`

| Property | Contract |
| --- | --- |
| Business meaning | Date and time at which the source transaction was generated. |
| Expected source type | Excel/Pandas datetime; Phase 1 read `datetime64[us]`. |
| Normalized target type | Timezone-naive timestamp; SQL target `datetime2(0)`. |
| Null policy | Null is not usable. |
| Reject conditions | Missing or unparseable value: `INVALID_INVOICE_DATE`. |
| Retain conditions | Every successfully parsed source timestamp. |
| Quality-flag conditions | None for the observed source. |
| Transformation | Parse without timezone conversion and preserve source wall-clock date and time to seconds. |
| Warehouse behavior | Drives `date_key` and `batch_month`; fact keeps the full timestamp. |

### `unit_price`

| Property | Contract |
| --- | --- |
| Business meaning | Product price per unit in sterling according to UCI. |
| Expected source type | Numeric; Phase 1 read `float64`. |
| Normalized target type | Exact decimal; SQL target `decimal(19,4)`. |
| Null policy | Null is not usable. |
| Reject conditions | Missing, nonnumeric, non-finite, decimal overflow: `MISSING_PRICE` or `INVALID_PRICE`. An observed value `< 0`: `NEGATIVE_PRICE_ADJUSTMENT`. |
| Retain conditions | Values equal to or greater than zero. |
| Quality-flag conditions | Value equal to zero: `ZERO_PRICE`. |
| Transformation | Convert through a decimal string representation, not binary-float arithmetic. Conversion to scale 4 must be lossless; a value with non-zero precision beyond scale 4 is `INVALID_PRICE`, not silently rounded. Do not apply `ABS`. |
| Warehouse behavior | Store exact decimal on the fact. Zero-price rows remain auditable but are excluded from core commercial KPIs. Negative-price accounting-adjustment rows go to rejected output and do not enter `fact_sales`. |

The 5 observed negative-price rows are all `Adjust bad debt` entries on nonstandard `A...` invoices. They are classified as non-merchandise/accounting adjustments and excluded because `fact_sales` and its KPIs model retail product sales. This rule does **not** claim that every negative numeric value is inherently corrupted or universally invalid.

`NEGATIVE_PRICE_ADJUSTMENT` is approved for this profiled source evidence. A future negative-price pattern with different evidence requires review rather than automatic reuse of the accounting-adjustment interpretation.

### `customer_id`

| Property | Contract |
| --- | --- |
| Business meaning | Source customer identifier. It is an identifier, not a measure. |
| Expected source type | Nullable `float64`; every observed non-null value is integral-valued. |
| Normalized target type | Nullable canonical text; SQL target `nvarchar(32)`. |
| Null policy | Null/blank does not invalidate an otherwise usable transaction. |
| Reject conditions | None based only on Customer ID. |
| Retain conditions | Retain rows with known, missing, or unusable Customer ID. |
| Quality-flag conditions | Null/blank: `MISSING_CUSTOMER`. Non-null value that cannot be represented as an integer identifier: `INVALID_CUSTOMER_ID`. |
| Transformation | Parse numeric cells and numeric text exactly. If integral, emit base-10 digits without `.0` or scientific notation. After trimming, any nonempty value that cannot be parsed as an integral identifier becomes null and receives `INVALID_CUSTOMER_ID`. |
| Warehouse behavior | Known values resolve to `dim_customer`; null/invalid values resolve to the single UNKNOWN customer member. UNKNOWN does not contribute to known-customer, repeat-customer, repeat-rate, or top-customer counts. The transaction can still contribute to overall, product, and country metrics. |

### `country`

| Property | Contract |
| --- | --- |
| Business meaning | Source country label associated with the customer/transaction. |
| Expected source type | Text/string. |
| Normalized target type | Text; SQL target `nvarchar(128)`. |
| Null policy | Null/blank is permitted and maps to UNKNOWN. |
| Reject conditions | None based only on Country. |
| Retain conditions | Retain every nonblank label, including historical/source labels such as `EIRE`, `RSA`, `European Community`, and `Unspecified`. |
| Quality-flag conditions | Null/blank after trimming: `MISSING_COUNTRY`. |
| Transformation | Trim outer whitespace only. Do not change case, expand abbreviations, or apply a manual mapping in Phase 3. |
| Warehouse behavior | One `dim_country` member per normalized source label, plus UNKNOWN. The literal source value `Unspecified` remains distinct from warehouse UNKNOWN. |

Phase 1 found no country case/trim variants, so casing normalization and a manual country map are not approved.

## Invoice and cancellation rules

### Exact `is_cancelled` definition

```text
is_cancelled = normalized invoice_no starts with "C"
```

The check is case-insensitive before invoice normalization and becomes a simple uppercase-prefix check afterward. It depends only on the documented invoice prefix, not on quantity sign, price, or a description keyword.

Cancellation rows are retained unless an independent reject rule applies. `is_cancelled = true` even for the single observed C-prefixed positive-quantity anomaly.

### Quantity-sign treatment

| Condition | Observed rows | Classification | `is_cancelled` | Core KPI treatment |
| --- | ---: | --- | --- | --- |
| C invoice + negative quantity | 19,493 | `ACCEPT`, unless another quality/reject condition applies | `true` | Core cancellation when invoice format is standard and price is positive. Signed `line_amount` reduces Net Sales. |
| C invoice + zero quantity | 0 | `ACCEPT WITH QUALITY FLAG` (`QUANTITY_SIGN_MISMATCH`) | `true` | Excluded from core commercial sale/core cancellation value and unit metrics; still counts as cancellation activity at invoice level. |
| C invoice + positive quantity | 1 | `ACCEPT WITH QUALITY FLAG` (`QUANTITY_SIGN_MISMATCH`) | `true` | Excluded from core commercial sale/core cancellation value and unit metrics; still counts as cancellation activity at invoice level. |
| Non-C invoice + negative quantity | 3,457 | `ACCEPT WITH QUALITY FLAG` (`QUANTITY_SIGN_MISMATCH`) | `false` | Retained for audit but excluded from core commercial KPIs. Do not call it a return. |
| Non-C invoice + zero quantity | 0 | `ACCEPT WITH QUALITY FLAG` (`QUANTITY_SIGN_MISMATCH`) | `false` | Retained for audit but excluded from core commercial KPIs. |
| Non-C invoice + positive quantity | 1,044,420 | `ACCEPT` unless another condition applies | `false` | Core commercial sale only when the reusable predicate below is satisfied. |

Retaining non-C negative quantities preserves source fidelity and auditability. Flagging and excluding them from standard commercial KPIs avoids inventing an unsupported “return” interpretation.

### Cancellation effect on analytics

| Metric family | Rule |
| --- | --- |
| Net Sales | Include core cancellation lines with their signed negative `line_amount`; never convert to an absolute value. |
| Orders | C-prefixed invoices do not count as Orders. |
| Units Sold | Cancellation quantities do not reduce or increase Units Sold. Report cancellation units separately if needed. |
| Cancellation Rate | Count distinct accepted C-prefixed invoices at invoice level as defined in the KPI section. |
| Product metrics | Core cancellation line amount reduces product Net Sales. Cancellation rows do not increase product Orders or Units Sold. Keep separate cancellation-invoice/unit measures. |
| Customer metrics | For known customers, core cancellation line amount reduces customer Net Sales/total spend. It does not create an Order. UNKNOWN customer remains excluded from customer counts and rankings. |

## Price treatment

| Price condition | Observed rows | Classification | Treatment |
| --- | ---: | --- | --- |
| `unit_price < 0` | 5 | `REJECT` (`NEGATIVE_PRICE_ADJUSTMENT`) | Preserve as non-merchandise/accounting adjustments in rejected output with lineage. Exclude from `fact_sales` and retail KPIs because those outputs model retail product sales. |
| `unit_price = 0` | 6,202 | `ACCEPT WITH QUALITY FLAG` (`ZERO_PRICE`) | Retain in staging/fact for audit. `line_amount = 0`. Exclude from core commercial sale and core cancellation scopes. |
| `unit_price > 0` | 1,061,164 | `ACCEPT` unless another condition applies | Eligible for core scopes only when invoice-format and quantity-sign predicates also pass. |

A positive price alone does not prove a retail sale. The observed high-price samples include manual charges and fees, so the core predicates must be applied together.

## Duplicate-looking rows, identity, and grain

Exact-looking business rows are not automatically removed.

Physical source identity is the pair:

```text
source_sheet
source_row_number
```

`source_row_number` is the physical Excel row number. Row 1 is the header and the first data row is row 2.

Production identity is:

```text
source_row_id = lowercase hexadecimal SHA-256(
    UTF-8(source_sheet + "|" + base-10 source_row_number without leading zeroes)
)
```

The fixed source sheet names contain no `|`, so the encoding is unambiguous for this project. Phase 3 must test determinism and uniqueness. A source-row-ID collision is a batch failure; it is not resolved by dropping a row.

Frozen grains:

- Staging grain: **one accepted source invoice-line occurrence**.
- Fact grain: **one accepted source invoice-line occurrence**.

Rows that are identical across all eight source business fields remain separate when their physical identities differ. This also preserves overlapping-sheet occurrences. Monetary and unit aggregates therefore operate on accepted source occurrences, while Orders use distinct normalized invoice numbers. The source does not provide enough evidence for a safer automatic deduplication rule; this remains a documented dataset limitation.

## Derived columns

Only the following Phase 3 derived columns are approved:

| Column | Definition |
| --- | --- |
| `source_sheet` | Exact workbook worksheet name. |
| `source_row_number` | Physical Excel row number, including the header offset. |
| `source_row_id` | Deterministic SHA-256 value defined above. |
| `line_amount` | Exact decimal `quantity * unit_price`; SQL target `decimal(30,4)`; never use absolute values. |
| `is_cancelled` | Boolean prefix rule defined above. |
| `batch_month` | Calendar month from `invoice_datetime`, stored as the first date of that month (`YYYY-MM-01`). |
| `loaded_at` | UTC timestamp supplied by the load execution; one consistent execution timestamp per batch is preferred. |
| Approved quality flags | Boolean columns for the flags listed in this document. |

Do not add inferred return flags, product-category mappings, deduplication flags, or additional business semantics in Phase 3 without a reviewed amendment.

## Rejection rules

| Condition | Classification | Reason code | Explanation | Phase 1 matching evidence |
| --- | --- | --- | --- | ---: |
| Invoice null/blank after normalization | `REJECT` | `MISSING_INVOICE` | Cannot identify transaction or cancellation status safely. | 0 |
| StockCode null/blank after normalization | `REJECT` | `MISSING_STOCK_CODE` | Cannot resolve fact to product grain. | 0 |
| InvoiceDate missing or unparseable | `REJECT` | `INVALID_INVOICE_DATE` | Cannot assign event time, date key, or batch month. | 0 |
| Quantity missing | `REJECT` | `MISSING_QUANTITY` | Cannot calculate signed units or line amount. | 0 |
| Quantity nonnumeric, non-finite, non-integral, or integer overflow | `REJECT` | `INVALID_QUANTITY` | Cannot represent the required signed integer losslessly. | 0 |
| Price missing | `REJECT` | `MISSING_PRICE` | Cannot calculate line amount. | 0 |
| Price nonnumeric, non-finite, or decimal overflow | `REJECT` | `INVALID_PRICE` | Cannot represent a reliable exact unit price. | 0 |
| Price below zero | `REJECT` | `NEGATIVE_PRICE_ADJUSTMENT` | Observed records are non-merchandise bad-debt/accounting adjustments outside the retail product-sales fact scope. | 5 |

The evidence column reports Phase 1 source matches, not Phase 3 execution results. Phase 3 must calculate and reconcile actual accepted, flagged, and rejected counts rather than copying these numbers.

Rejected records must retain, at minimum:

```text
source_row_id
source_sheet
source_row_number
all eight raw source fields
reject_reason
rejected_at
```

If more than one reject condition applies, emit one rejected row with a deterministic delimiter-separated list of reason codes in the table order above.

## Reusable analytical scopes and core commercial predicate

All SQL marts and Power BI measures must reuse these named logical predicates from one governed implementation. They must not restate slightly different versions in separate marts or measures.

### Core commercial sale-line predicate

The reusable predicate is:

```text
core_commercial_sale_line =
    row is ACCEPT or ACCEPT WITH QUALITY FLAG
AND NONSTANDARD_INVOICE = false
AND is_cancelled = false
AND quantity > 0
AND unit_price > 0
```

The three commercial tests are therefore exactly:

```text
is_cancelled = 0
quantity > 0
unit_price > 0
```

The accepted-row and standard-invoice gates are data-contract prerequisites, not alternative commercial definitions. The `NONSTANDARD_INVOICE` gate is required by profiling because one positive-price `A...` row is also an `Adjust bad debt` accounting entry and must not become a retail sale.

### Core cancellation line

An accepted row is a core cancellation line only when all conditions hold:

```text
invoice_no matches ^C[0-9]+$
is_cancelled = true
quantity < 0
unit_price > 0
```

`MISSING_CUSTOMER`, `MISSING_DESCRIPTION`, or `MISSING_COUNTRY` alone does not remove an otherwise qualifying line from these scopes. `NONSTANDARD_INVOICE`, `ZERO_PRICE`, or `QUANTITY_SIGN_MISMATCH` prevents a row from entering either core scope.

### Qualifying order and cancellation invoice

- A qualifying Order is a distinct normalized non-C `invoice_no` containing at least one core commercial sale line.
- A qualifying cancelled invoice is a distinct normalized `invoice_no` containing at least one accepted row with `is_cancelled = true`.
- The cancellation-activity definition follows UCI's prefix rule even when a C invoice has a quantity-sign quality flag. Its line value remains excluded unless it passes the core cancellation predicate.

### Zero-price KPI behavior

Zero-price rows remain accepted, stored, and flagged, but `unit_price > 0` is required by the core commercial predicate. Therefore:

| KPI | Zero-price treatment |
| --- | --- |
| Net Sales | Excluded from the commercial aggregation. Their stored `line_amount` is zero. |
| Orders | A zero-price row cannot create an Order. Its invoice counts only if another row on that invoice satisfies `core_commercial_sale_line`. |
| Units Sold | Quantity from a zero-price row is excluded. |
| Average Order Value | Excluded from the numerator and cannot create an invoice in the denominator. |
| Known Customers | A customer appearing only on zero-price rows is not counted. The customer counts only if at least one row satisfies `core_commercial_sale_line`. |

## KPI definitions

All formulas apply the current report filter context to `invoice_datetime`, country, product, and other selected dimensions before aggregation. Divide-by-zero results are `NULL`, not zero.

### Net Sales

```text
Net Sales = SUM(line_amount)
            over (core commercial sale lines UNION core cancellation lines)
```

Core commercial sale-line amounts are positive. Core cancellation line amounts retain their negative sign and reduce Net Sales. Zero-price rows, rejected negative-price adjustments, nonstandard invoices, and quantity-sign mismatches do not contribute.

### Orders

```text
Orders = COUNT(DISTINCT invoice_no) over core_commercial_sale_line
```

C-prefixed invoices and invoices having no `core_commercial_sale_line` are excluded.

### Known Customers

```text
Known Customers = COUNT(DISTINCT customer_id)
                  over core_commercial_sale_line
                  where customer_id is not null/UNKNOWN
```

UNKNOWN never inflates customer counts. Missing-customer lines can still contribute to overall Net Sales, Orders, Units Sold, product metrics, and country metrics.

### Units Sold

```text
Units Sold = SUM(quantity) over core_commercial_sale_line
```

This is gross positive sold quantity. Cancellation and non-C negative quantities do not reduce Units Sold. If cancellation units are reported, use `SUM(ABS(quantity))` over core cancellation lines as a separately named metric.

### Average Order Value

```text
Average Order Value = SUM(line_amount over core_commercial_sale_line) / Orders
```

The numerator excludes cancellations. Do not calculate AOV as Net Sales divided by Orders, because Net Sales includes signed cancellation effects.

### Repeat Customer

Within the same report filter context, a Repeat Customer is a known customer with at least two distinct qualifying Orders:

```text
COUNT(DISTINCT qualifying order invoice_no) >= 2
```

UNKNOWN is never a Repeat Customer.

### Repeat Rate

```text
Repeat Rate = Repeat Customers
              / Known Customers with at least one qualifying Order
```

Numerator and denominator must use the same filter context and `core_commercial_sale_line` predicate.

### Cancellation Rate

```text
Cancellation Rate = COUNT(DISTINCT qualifying cancelled invoice_no)
                    / COUNT(DISTINCT invoice_no in
                        qualifying Orders UNION qualifying cancelled invoices)
```

This is an invoice-level rate, not cancelled rows divided by total rows. The single observed C-prefixed positive-quantity invoice remains cancellation activity by the UCI prefix rule, but its line amount and units are excluded from core value/unit metrics.

## Product, customer, and country metric behavior

| Area | Required behavior |
| --- | --- |
| Product | Group by normalized case-preserving StockCode. Product Net Sales uses core commercial sale plus core cancellation line amounts. Product Units Sold and Order Count use `core_commercial_sale_line` only. Cancellation activity is reported separately. Special StockCodes are not excluded merely by syntax. |
| Customer | Use known customers only for customer counts, repeat metrics, purchase frequency, rankings, and top-customer outputs. Customer Net Sales/total spend may include signed core cancellations for that known customer. UNKNOWN transactions remain in non-customer totals. |
| Country | Use the normalized source country member or UNKNOWN. Overall formulas are unchanged. Preserve `Unspecified` as a real source label rather than converting it to UNKNOWN. |

## Reconciliation requirements for Phase 3

Phase 3 must prove all of the following with actual execution results:

1. `source rows = accepted rows + rejected rows`.
2. `accepted rows = ACCEPT rows + ACCEPT WITH QUALITY FLAG rows`.
3. Every accepted row has exactly one unique `source_row_id`.
4. Duplicate-looking rows with different physical identities are preserved.
5. Every rejected row has at least one approved reason code and complete lineage.
6. `line_amount` uses exact decimal arithmetic and preserves sign.
7. The centralized `core_commercial_sale_line` and core cancellation predicates reproduce the contract exactly.
8. UNKNOWN customer and country mappings do not change overall line-level monetary totals.

Do not copy Phase 1 counts into a Phase 3 result report. Phase 3 must calculate its own counts from the implemented transformation.

## Open Decisions

There are no unresolved decisions that block Phase 3 after human approval of this contract.

The following source semantics remain unknown, but their transformation behavior is frozen and therefore does not block implementation:

- The business meaning of 3,457 non-C negative-quantity rows is unknown. They are retained, flagged, excluded from core commercial KPIs, and are not called returns.
- The single C-prefixed positive-quantity row is still a cancellation by prefix, but its value/unit contribution is excluded because of the sign mismatch.
- Exact-looking and cross-sheet repeated occurrences cannot be classified reliably as accidental duplicates. They are preserved by physical identity.
- Special StockCodes may represent products, postage, fees, or operational entries. No manual exclusion mapping is approved; KPI eligibility comes from the shared invoice, quantity, and price predicates.

Any proposal to change these treatments requires a reviewed amendment to this document before transformation code changes.
