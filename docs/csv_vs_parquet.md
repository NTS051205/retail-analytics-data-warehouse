# CSV vs Parquet Benchmark

## Scope

This is a small local educational benchmark. It is not a universal performance claim.

- Measured at (UTC): `2026-09-23T00:48:47.338355+00:00`
- Accepted rows available: `1,067,366`
- Rows measured: `100,000`
- Columns in the full-read comparison: `24`
- Sample method: `first accepted rows in preserved source order`
- Timed read repetitions: `3`; the table reports the median.

## Environment

- Platform: `Windows-11-10.0.26200-SP0`
- Python: `3.12.2`
- Pandas: `3.0.5`
- PyArrow: `25.0.1`

## Measured results

| Measurement | CSV | Parquet |
| --- | ---: | ---: |
| File size (bytes) | 29,203,074 | 8,112,291 |
| File size (MiB) | 27.850 | 7.736 |
| Full read, median seconds | 0.532471 | 0.128619 |
| Selected-column read (source_row_id, invoice_no, quantity, line_amount), median seconds | 0.277681 | 0.055877 |

Parquet size divided by CSV size for this sample: `0.2778`.

Raw timing observations in seconds:

- CSV full read: `[0.5097460999968462, 0.5324705000093672, 0.5461149000038859]`
- Parquet full read: `[0.16687559999991208, 0.12861910002538934, 0.1255337999900803]`
- CSV selected-column read: `[0.3250419999822043, 0.2776810000068508, 0.2608030999836046]`
- Parquet selected-column read: `[0.0683335000067018, 0.05587730000843294, 0.05256010001176037]`

## Limitations

- The benchmark uses the first accepted source occurrences as a deterministic sample; it is not a randomized workload.
- Operating-system file caching, other local processes, storage hardware, and library versions can affect timings.
- CSV type inference and Parquet schema restoration do different work, so the formats are not semantically identical read paths.
- The benchmark measures local single-process Pandas reads and does not predict every SQL Server or BI workload.

## Project decision

The project continues with Parquet for cleaned local analytical data because it preserves a stable typed schema, supports column projection, provides compression, and works with the required year/month partition layout. The measurements above are local evidence, not a guarantee that Parquet is faster in every environment.
