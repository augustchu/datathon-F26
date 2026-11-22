# Count scraper fidelity audit

The **48 random checks** found a match in every sampled cell. A **49th, targeted check** found a real extraction omission. IFFCO lists `04` doctors for Suraksha Hospital in Belagavi, and the scraper emitted a blank because its integer rule rejects a leading zero. This branch was outside the random sample. The targeted check explains the difference between the 6,459 positive source cells and 6,458 positive scraped doctor counts in the IFFCO archive. [Source row by Rohini ID](https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters#:~:text=8900080583832) · [Finding and record ID](targeted-finding.json) · [Integer rule](../../../../scripts/extract_iffco_capacity.py)

| Archived publisher and field | Precision matches | Recall matches | Source-positive cells | Emitted positive cells |
| --- | ---: | ---: | ---: | ---: |
| IFFCO, bed count | 6/6 | 6/6 | 7,380 | 7,380 |
| IFFCO, doctor count | 6/6 | 6/6 | 6,459 | 6,458 |
| SBI 2022, bed count | 6/6 | 6/6 | 14,653 | 14,653 |
| SBI 2022, doctor count | 6/6 | 6/6 | 10,547 | 10,547 |

Each precision and recall estimate has a two-sided exact binomial 95% interval of **54.1% to 100%**. The random sample had zero observed false positives, zero observed false negatives and zero inconclusive cases. The targeted omission is reported separately. These small, equally allocated samples give four field-specific pairs of measurements. The ledger preserves each sampled number, source URL, printed identifier and decision. [Results and evidence ledger](results.json) · [Frozen sample and input hashes](sample.json)

## What was measured

The unit is a positive count for one printed publication row and field. **Precision** asks what share of emitted positive values match the original row, column and count. A wrong row or count, or a positive output with no corresponding source claim, counts as a false positive. **Recall** starts from the source and asks what share of eligible positive source values appear correctly in the output. A missing or incorrect output counts as a false negative. The audit uses whole-hospital beds and total doctors. Blank, zero and `NA` cells are outside the positive-count recall frame. An ambiguous source cell in the sampled measurements would remain inconclusive.

- **Precision sample.** Draw six positive outputs per publisher and field without replacement, then check the original row.
- **Recall sample.** Independently enumerate positive cells from the source snapshots and draw six per publisher and field without replacement, then look up the resulting source record identifier in the output. IFFCO's original table was read with Python's HTML parser by table and column. SBI's printed serials and count columns were enumerated from the archived PDF text layout. This produces a source-first frame independently of the production parser's PDF row boundaries. The lookup uses the same publication row key to compare the counts.
- **Review.** Read the four sampled IFFCO groups from the original HTML table, using the Rohini ID and table row. Validate the SBI groups against the independently extracted layout and the visible columns of the full source PDF pages, using PDF page and printed serial. For example, the Excelscare Hospital row shows **105** total doctors and **5** in the adjacent full-time column. [IFFCO disclosure](https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters) · [SBI example, page 75](https://content.sbigeneral.in/uploads/f22596609234455e8b24df7859027ce6.pdf#page=75)

The result measures archived publication-to-CSV fidelity for these two structured count extracts. Accuracy of the source's own statement, matching that statement to a Virtue Foundation branch, and comparable definitions or dates across insurers require their own evidence. The sampled numbers were readable. The independent user review queue is still pending.

## Replay

Use Python with SciPy and Poppler's `pdftotext`. The archive folder needs the four input files named in the [sample manifest](sample.json). Keep the archived HTML and PDF versions used by the extractor so the source identifiers and geometries align. The command reproduces the frozen draws and scores them using the saved source readings.

```bash
audit=max-supplemented-data/experiments/verification/scraper-audit
python3 "$audit/sample_audit.py" /path/to/count-archive /tmp/count-audit-sample.json \
  --observed-values "$audit/source-observations.json" \
  --audit-report /tmp/count-audit-results.json
```
