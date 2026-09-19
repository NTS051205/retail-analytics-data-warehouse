# Online Retail II Data Profiling

This report records Phase 1 observations from the unchanged source workbook. It does not define cleaning rules or remove records.

## Dataset Overview

**OBSERVED FACT**

- Workbook: `data/raw/online_retail_II.xlsx` (45,622,278 bytes).
- SHA-256 before and after analysis: `BCBE73B35F5B7BABF197FB0CB983A11F5D9FF929078D4AA53D171B1F2DF2E980`.
- Worksheets read exactly once: 2 (`Year 2009-2010, Year 2010-2011`).
- Combined source rows: 1,067,371.
- Sum of worksheet rows equals the combined row count: `True`.
- `source_row_number` is the physical Excel row number; row 1 is the header.
- The combined dataframe exists only in memory. No production row identifier or transformed dataset is created.

**POSSIBLE INTERPRETATION**

- The workbook is suitable for evidence collection, but profiling findings alone do not authorize cleaning or exclusion rules.

## Sheet Structure

**OBSERVED FACT**

| Sheet | Rows | Columns | Minimum InvoiceDate | Maximum InvoiceDate | Schema matches |
| --- | --- | --- | --- | --- | --- |
| Year 2009-2010 | 525,461 | 8 | 2009-12-01 07:45:00 | 2010-12-09 20:01:00 | True |
| Year 2010-2011 | 541,910 | 8 | 2010-12-01 08:26:00 | 2011-12-09 12:50:00 | True |
- All worksheets use the same ordered schema: `True`.

**POSSIBLE INTERPRETATION**

- Matching schemas allow combined profiling without renaming source columns.

## Schema

**OBSERVED FACT**

- Actual ordered columns: `["Invoice", "StockCode", "Description", "Quantity", "InvoiceDate", "Price", "Customer ID", "Country"]`.
- Missing expected columns: `[]`.
- Extra columns: `[]`.
- Headers with outer whitespace: `[]`.
| Scope | Column | Pandas dtype |
| --- | --- | --- |
| Year 2009-2010 | Invoice | object |
| Year 2009-2010 | StockCode | object |
| Year 2009-2010 | Description | object |
| Year 2009-2010 | Quantity | int64 |
| Year 2009-2010 | InvoiceDate | datetime64[us] |
| Year 2009-2010 | Price | float64 |
| Year 2009-2010 | Customer ID | float64 |
| Year 2009-2010 | Country | str |
| Year 2010-2011 | Invoice | object |
| Year 2010-2011 | StockCode | object |
| Year 2010-2011 | Description | object |
| Year 2010-2011 | Quantity | int64 |
| Year 2010-2011 | InvoiceDate | datetime64[us] |
| Year 2010-2011 | Price | float64 |
| Year 2010-2011 | Customer ID | float64 |
| Year 2010-2011 | Country | str |
| Combined | Invoice | object |
| Combined | StockCode | object |
| Combined | Description | object |
| Combined | Quantity | int64 |
| Combined | InvoiceDate | datetime64[us] |
| Combined | Price | float64 |
| Combined | Customer ID | float64 |
| Combined | Country | str |

**POSSIBLE INTERPRETATION**

- Pandas dtype inference is evidence about source representation, not a final warehouse type decision.

## Date Range

**OBSERVED FACT**

- Minimum valid InvoiceDate: `2009-12-01 07:45:00`.
- Maximum valid InvoiceDate: `2011-12-09 12:50:00`.
- True-null rows: 0; blank-string rows: 0; unparseable nonblank rows: 0.
- Common overlap across worksheet ranges: `2010-12-01 08:26:00` to `2010-12-09 20:01:00`.
- Missing or unparseable dates are excluded from the period tables.
| Year | Rows |
| --- | --- |
| 2009 | 45,228 |
| 2010 | 522,714 |
| 2011 | 499,429 |
| Year-month | Rows |
| --- | --- |
| 2009-12 | 45,228 |
| 2010-01 | 31,555 |
| 2010-02 | 29,388 |
| 2010-03 | 41,511 |
| 2010-04 | 34,057 |
| 2010-05 | 35,323 |
| 2010-06 | 39,983 |
| 2010-07 | 33,383 |
| 2010-08 | 33,306 |
| 2010-09 | 42,091 |
| 2010-10 | 59,098 |
| 2010-11 | 78,015 |
| 2010-12 | 65,004 |
| 2011-01 | 35,147 |
| 2011-02 | 27,707 |
| 2011-03 | 36,748 |
| 2011-04 | 29,916 |
| 2011-05 | 37,030 |
| 2011-06 | 36,874 |
| 2011-07 | 39,518 |
| 2011-08 | 35,284 |
| 2011-09 | 50,226 |
| 2011-10 | 60,742 |
| 2011-11 | 84,711 |
| 2011-12 | 25,526 |

**POSSIBLE INTERPRETATION**

- The observed period coverage can support monthly replay after Phase 2 establishes treatment rules.

## Null Patterns

**OBSERVED FACT**

| Column | Null rows | Null % | Blank-string rows | Blank % |
| --- | --- | --- | --- | --- |
| Invoice | 0 | 0.0000% | 0 | 0.0000% |
| StockCode | 0 | 0.0000% | 0 | 0.0000% |
| Description | 4,382 | 0.4105% | 0 | 0.0000% |
| Quantity | 0 | 0.0000% | 0 | 0.0000% |
| InvoiceDate | 0 | 0.0000% | 0 | 0.0000% |
| Price | 0 | 0.0000% | 0 | 0.0000% |
| Customer ID | 243,007 | 22.7669% | 0 | 0.0000% |
| Country | 0 | 0.0000% | 0 | 0.0000% |

**POSSIBLE INTERPRETATION**

- Nulls and blank strings remain separate observations because their source meanings may differ.

## Invoice / Cancellation Patterns

**OBSERVED FACT**

- Distinct non-null invoices: 53,628.
- C-prefixed rows: 19,494; distinct invoices: 8,292.
- Non-C nonblank rows: 1,047,877; distinct invoices: 45,336.
- Null invoice rows: 0; blank-string rows: 0.
- Unexpected nonblank invoice formats: 6; frequent examples: `[{"value": "A506401", "row_count": 1}, {"value": "A516228", "row_count": 1}, {"value": "A528059", "row_count": 1}, {"value": "A563185", "row_count": 1}, {"value": "A563186", "row_count": 1}, {"value": "A563187", "row_count": 1}]`.
| Invoice class | Quantity class | Rows | Distinct invoices |
| --- | --- | --- | --- |
| c_prefixed | negative | 19,493 | 8,291 |
| c_prefixed | zero | 0 | 0 |
| c_prefixed | positive | 1 | 1 |
| non_c | negative | 3,457 | 3,393 |
| non_c | zero | 0 | 0 |
| non_c | positive | 1,044,420 | 41,943 |

**POSSIBLE INTERPRETATION**

- C-prefixed positive quantities and non-C negative quantities require review before treatment rules are defined.

## Quantity Patterns

**OBSERVED FACT**

| Metric | Quantity | Price |
| --- | --- | --- |
| count | 1,067,371.0000 | 1,067,371.0000 |
| null_count | 0.0000 | 0.0000 |
| negative_count | 22,950.0000 | 5.0000 |
| zero_count | 0.0000 | 6,202.0000 |
| positive_count | 1,044,421.0000 | 1,061,164.0000 |
| mean | 9.9389 | 4.6494 |
| median | 3.0000 | 2.1000 |
| std | 172.7058 | 123.5531 |
| min | -80,995.0000 | -53,594.3600 |
| p01 | -3.0000 | 0.2100 |
| p05 | 1.0000 | 0.4200 |
| p25 | 1.0000 | 1.2500 |
| p50 | 3.0000 | 2.1000 |
| p75 | 10.0000 | 4.1500 |
| p95 | 30.0000 | 9.9500 |
| p99 | 100.0000 | 18.0000 |
| max | 80,995.0000 | 38,970.0000 |
- Percentiles use Pandas linear interpolation; standard deviation uses `ddof=1`.
- `extreme_quantity_samples.csv` contains deterministic small samples, not invalid-row classifications.

**POSSIBLE INTERPRETATION**

- Extreme quantities may represent bulk activity, cancellations, adjustments, or source issues.

## Price Patterns

**OBSERVED FACT**

- Negative-price rows: 5; zero-price rows: 6,202.
- Zero-price invoice relationship: C-prefixed 0, non-C 6,202, null invoice 0.
- Zero-price rows with missing Customer ID: 6,131.
- Frequent zero-price descriptions: `[{"value": "check", "row_count": 162}, {"value": "?", "row_count": 92}, {"value": "damages", "row_count": 84}, {"value": "damaged", "row_count": 81}, {"value": "found", "row_count": 28}, {"value": "missing", "row_count": 27}, {"value": "sold as set on dotcom", "row_count": 20}, {"value": "Damaged", "row_count": 17}, {"value": "adjustment", "row_count": 16}, {"value": "OWL DOORSTOP", "row_count": 15}]`.
- Frequent zero-price StockCodes: `[{"value": "46000M", "row_count": 18}, {"value": 22501, "row_count": 18}, {"value": 79321, "row_count": 17}, {"value": 21116, "row_count": 16}, {"value": 22423, "row_count": 16}, {"value": 23084, "row_count": 16}, {"value": "46000S", "row_count": 15}, {"value": 22734, "row_count": 15}, {"value": 22139, "row_count": 14}, {"value": 35965, "row_count": 14}]`.
- Frequent negative-price descriptions: `[{"value": "Adjust bad debt", "row_count": 5}]`.
- Negative-price invoice relationship: C-prefixed 0, non-C 5, null invoice 0.
- Negative-price quantity relationship: negative 0, zero 0, positive 5, null/blank/unparseable 0.
- `price_anomaly_samples.csv` contains samples for negative, zero, and largest positive prices.

**POSSIBLE INTERPRETATION**

- Zero and negative prices may represent different source situations; no final interpretation is assigned here.

## Customer ID Findings

**OBSERVED FACT**

- Combined Pandas dtype: `float64`.
- Values rendered with a `.0` suffix: 824,364; values with a non-zero fractional component: 0.
- Missing Customer ID rows: 243,007 (22.7669%).
- Distinct invoices among missing-customer rows: 8,752.
- Missing-customer relationship: C-prefixed 750, non-C 242,257, negative quantity 4,206, zero price 6,131.

**POSSIBLE INTERPRETATION**

- Customer ID representation and missingness need an explicit Phase 2 decision; missing IDs are not rejected here.

## Product / StockCode Findings

**OBSERVED FACT**

- Distinct non-null StockCodes: 5,305.
- Null StockCode rows: 0; blank-string rows: 0.
| Structural category | Rows | Distinct values | Frequent examples |
| --- | --- | --- | --- |
| null | 0 | 0 | [] |
| blank | 0 | 0 | [] |
| numeric_like | 932,385 | 3,590 | [{"value": 22423, "row_count": 4424}, {"value": 21212, "row_count": 3318}, {"value": 20725, "row_count": 3259}, {"value": 84879, "row_count": 2960}, {"value": 47566, "row_count": 2768}, {"value": 21232, "row_count": 2747}, {"value": 22197, "row_count": 2549}, {"value": 22383, "row_count": 2540}, {"value": 20727, "row_count": 2529}, {"value": 21931, "row_count": 2434}] |
| alphabetic | 5,477 | 16 | [{"value": "POST", "row_count": 2122}, {"value": "DOT", "row_count": 1446}, {"value": "M", "row_count": 1421}, {"value": "D", "row_count": 177}, {"value": "S", "row_count": 104}, {"value": "ADJUST", "row_count": 67}, {"value": "AMAZONFEE", "row_count": 43}, {"value": "DCGSSGIRL", "row_count": 25}, {"value": "DCGSSBOY", "row_count": 23}, {"value": "PADS", "row_count": 19}] |
| mixed_alphanumeric | 129,307 | 1,689 | [{"value": "85123A", "row_count": 5829}, {"value": "85099B", "row_count": 4216}, {"value": "82494L", "row_count": 2108}, {"value": "85099C", "row_count": 1952}, {"value": "85099F", "row_count": 1916}, {"value": "84970S", "row_count": 1477}, {"value": "84029E", "row_count": 1279}, {"value": "47591D", "row_count": 1184}, {"value": "84997D", "row_count": 1146}, {"value": "84029G", "row_count": 1124}] |
| special_or_other | 202 | 10 | [{"value": "BANK CHARGES", "row_count": 102}, {"value": "gift_0001_20", "row_count": 29}, {"value": "gift_0001_30", "row_count": 29}, {"value": "gift_0001_10", "row_count": 16}, {"value": "gift_0001_50", "row_count": 8}, {"value": "gift_0001_40", "row_count": 7}, {"value": "gift_0001_80", "row_count": 4}, {"value": "gift_0001_70", "row_count": 3}, {"value": "gift_0001_60", "row_count": 2}, {"value": "gift_0001_90", "row_count": 2}] |
- Missing Description rows: 4,382 (0.4105%).
- Missing-description overlap: missing Customer ID 4,382; C-prefixed invoice 0.
- Missing-description quantity relationship: negative 2,689, zero 0, positive 1,693, null/blank/unparseable 0.
- Missing-description price relationship: negative 0, zero 4,382, positive 0, null/blank/unparseable 0.
- Frequent StockCodes on missing-description rows: `[{"value": 22139, "row_count": 12}, {"value": 84990, "row_count": 12}, {"value": 79321, "row_count": 11}, {"value": 35965, "row_count": 11}, {"value": 22950, "row_count": 10}, {"value": 23084, "row_count": 10}, {"value": 22087, "row_count": 9}, {"value": 22084, "row_count": 9}, {"value": 71477, "row_count": 8}, {"value": 37461, "row_count": 8}]`.
- StockCodes with one description: 3,718; multiple descriptions: 1,232; maximum for one StockCode: 9.
| StockCode | Distinct non-null descriptions | Rows | Description samples |
| --- | --- | --- | --- |
| 20713 | 9 | 1,385 | [{"value": "JUMBO BAG OWLS", "row_count": 1372}, {"value": "missing", "row_count": 1}, {"value": "wrongly marked. 23343 in box", "row_count": 1}, {"value": "wrongly coded-23343", "row_count": 1}, {"value": "found", "row_count": 1}, {"value": "Found", "row_count": 1}, {"value": "wrongly marked 23343", "row_count": 1}, {"value": "Marked as 23343", "row_count": 1}] |
| 22423 | 7 | 4,424 | [{"value": "REGENCY CAKESTAND 3 TIER", "row_count": 4412}, {"value": "damaged", "row_count": 3}, {"value": "smashed", "row_count": 2}, {"value": "faulty", "row_count": 2}, {"value": "damages", "row_count": 2}, {"value": "broken, uneven bottom", "row_count": 1}, {"value": "wonky bottom/broken", "row_count": 1}] |
| 21181 | 7 | 1,920 | [{"value": "PLEASE ONE PERSON METAL SIGN", "row_count": 1730}, {"value": "PLEASE ONE PERSON  METAL SIGN", "row_count": 181}, {"value": "adjustment", "row_count": 2}, {"value": "missing", "row_count": 1}, {"value": "on cargo order", "row_count": 1}, {"value": "check", "row_count": 1}, {"value": "dotcom", "row_count": 1}] |
| 23084 | 7 | 1,067 | [{"value": "RABBIT NIGHT LIGHT", "row_count": 1051}, {"value": "temp adjustment", "row_count": 1}, {"value": "allocate stock for dotcom orders ta", "row_count": 1}, {"value": "add stock to allocate online orders", "row_count": 1}, {"value": "for online retail orders", "row_count": 1}, {"value": "Amazon", "row_count": 1}, {"value": "website fixed", "row_count": 1}] |
| 22734 | 7 | 807 | [{"value": "SET OF 6 RIBBONS VINTAGE CHRISTMAS", "row_count": 792}, {"value": "amazon", "row_count": 5}, {"value": "Carton qnty was 216 not 144 as stat", "row_count": 1}, {"value": "amazon adjustment", "row_count": 1}, {"value": "amendment", "row_count": 1}, {"value": "amazon sales", "row_count": 1}, {"value": "FOUND", "row_count": 1}] |
| 47566B | 6 | 804 | [{"value": "TEA TIME PARTY BUNTING", "row_count": 795}, {"value": "incorrectly credited C550456 see 47", "row_count": 2}, {"value": "missing", "row_count": 1}, {"value": "correct previous adjustment", "row_count": 1}, {"value": "stock credited from royal yacht inc", "row_count": 1}, {"value": "reverse previous adjustment", "row_count": 1}] |
| 85175 | 6 | 475 | [{"value": "CACTI T-LIGHT CANDLES", "row_count": 469}, {"value": "dotcom sold sets", "row_count": 1}, {"value": "Amazon sold sets", "row_count": 1}, {"value": "wrongly sold sets", "row_count": 1}, {"value": "? sold as sets?", "row_count": 1}, {"value": "check", "row_count": 1}] |
| 21830 | 6 | 290 | [{"value": "ASSORTED CREEPY CRAWLIES", "row_count": 283}, {"value": "MERCHANT CHANDLER CREDIT ERROR, STO", "row_count": 1}, {"value": "sold as 1", "row_count": 1}, {"value": "?", "row_count": 1}, {"value": "damaged", "row_count": 1}, {"value": "OOPS ! adjustment", "row_count": 1}] |
| 22719 | 6 | 265 | [{"value": "GUMBALL MONOCHROME COAT RACK", "row_count": 251}, {"value": "GUMBALL COATHOOK., BLACK & WHITE ", "row_count": 5}, {"value": 22467, "row_count": 1}, {"value": "wrong barcode (22467)", "row_count": 1}, {"value": "sold as 22467", "row_count": 1}, {"value": "wrong code", "row_count": 1}] |
| 85123A | 5 | 5,829 | [{"value": "WHITE HANGING HEART T-LIGHT HOLDER", "row_count": 5817}, {"value": "CREAM HANGING HEART T-LIGHT HOLDER", "row_count": 9}, {"value": "21733 mixed", "row_count": 1}, {"value": "?", "row_count": 1}, {"value": "wrongly marked carton 22804", "row_count": 1}] |

**POSSIBLE INTERPRETATION**

- Non-standard StockCodes and multiple descriptions can be legitimate; syntax alone does not define a cleaning rule.

## Country Findings

**OBSERVED FACT**

- Distinct non-null country values: 43.
- Null rows: 0; blank-string rows: 0.
- Rows with outer whitespace: 0.
- Comparison-key groups with multiple raw spellings/cases: 0; candidates: `[]`.
| Country | Rows | Row % | Distinct invoices | Known customers | Missing-customer rows |
| --- | --- | --- | --- | --- | --- |
| United Kingdom | 981,330 | 91.9390% | 49,108 | 5,410 | 240,029 |
| EIRE | 17,866 | 1.6738% | 806 | 5 | 1,671 |
| Germany | 17,624 | 1.6512% | 1,095 | 107 | 0 |
| France | 14,330 | 1.3426% | 746 | 95 | 128 |
| Netherlands | 5,140 | 0.4816% | 250 | 23 | 0 |
| Spain | 3,811 | 0.3570% | 188 | 41 | 0 |
| Switzerland | 3,189 | 0.2988% | 123 | 22 | 125 |
| Belgium | 3,123 | 0.2926% | 183 | 29 | 0 |
| Portugal | 2,620 | 0.2455% | 124 | 24 | 116 |
| Australia | 1,913 | 0.1792% | 117 | 15 | 0 |
| Channel Islands | 1,664 | 0.1559% | 79 | 14 | 0 |
| Italy | 1,534 | 0.1437% | 92 | 17 | 0 |
| Norway | 1,455 | 0.1363% | 53 | 13 | 0 |
| Sweden | 1,364 | 0.1278% | 129 | 19 | 19 |
| Cyprus | 1,176 | 0.1102% | 45 | 11 | 0 |

**POSSIBLE INTERPRETATION**

- Comparison keys are profiling aids only and do not establish a normalization rule.

## Duplicate-Looking Rows

**OBSERVED FACT**

- Participating rows: 67,242; groups: 32,907; largest group: 20 rows.
| Rank | Group size | Invoice | StockCode | Description | Quantity | InvoiceDate | Price | Customer ID | Country |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 20 | 555524 | 22698 | PINK REGENCY TEACUP AND SAUCER | 1 | 2011-06-05 11:37:00 | 2.95 | 16923.0 | United Kingdom |
| 2 | 12 | 555524 | 22697 | GREEN REGENCY TEACUP AND SAUCER | 1 | 2011-06-05 11:37:00 | 2.95 | 16923.0 | United Kingdom |
| 3 | 10 | 537224 | 70007 | HI TEC ALPINE HAND WARMER | 1 | 2010-12-05 16:24:00 | 1.65 | 13174.0 | United Kingdom |
| 4 | 8 | 572861 | 22775 | PURPLE DRAWERKNOB ACRYLIC EDWARDIAN | 12 | 2011-10-26 12:46:00 | 1.25 | 14102.0 | United Kingdom |
| 5 | 6 | 496431 | 84826 | ASSTD DESIGN 3D PAPER STICKERS | 1 | 2010-02-01 12:30:00 | 0.85 | 16415.0 | United Kingdom |
| 6 | 6 | 502660 | 17021 | NAMASTE SWAGAT INCENSE | 6 | 2010-03-25 17:18:00 | 0.3 | 13187.0 | United Kingdom |
| 7 | 6 | 525065 | 20894 | HANGING BAUBLE T-LIGHT HOLDER LARGE | 1 | 2010-10-03 14:28:00 | 2.95 | 16799.0 | United Kingdom |
| 8 | 6 | 534219 | 35953 | FOLKART STAR CHRISTMAS DECORATIONS | 1 | 2010-11-21 16:13:00 | 1.25 | 13230.0 | United Kingdom |
| 9 | 6 | 536412 | 21448 | 12 DAISY PEGS IN WOOD BOX | 2 | 2010-12-01 11:49:00 | 1.65 | 17920.0 | United Kingdom |
| 10 | 6 | 536464 | 22866 | HAND WARMER SCOTTY DOG DESIGN | 1 | 2010-12-01 12:23:00 | 2.1 | 17968.0 | United Kingdom |
> Duplicate-looking rows are measured only and are not assumed to be accidental duplicates because the source does not provide a trustworthy invoice-line identifier.

**POSSIBLE INTERPRETATION**

- Repeated business-field values can be separate source-line occurrences, so physical source-row metadata must remain available.

## Important Anomalies

**OBSERVED FACT**

- C-prefixed rows with positive quantity: 1.
- Non-C rows with negative quantity: 3,457.
- Negative-price rows: 5; zero-price rows: 6,202.
- Unexpected invoice-format rows: 6.
- Missing-customer rows: 243,007; missing-description rows: 4,382.

**POSSIBLE INTERPRETATION**

- Each anomaly category may contain several business situations; Phase 2 should review evidence before assigning treatment.

## Open Questions for Phase 2

- How should C-prefixed invoices with non-negative quantities be treated?
- How should negative quantities on non-C invoices be classified?
- Which zero-price or negative-price patterns are valid adjustments or unusable records?
- Which critical nulls require rejection, and which should remain with an UNKNOWN member or quality flag?
- How should non-standard StockCodes be represented without assuming they are invalid products?
- Which evidence-based rule should select a description when one StockCode has multiple descriptions?
- Do any country labels need normalization after source semantics are reviewed?
- Which suspicious but usable rows need a small documented quality-flag set?
