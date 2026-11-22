# In The Loop

In The Loop helps reviewers check scraped data against the pages it came from. Import a spreadsheet, open the cited webpage passage or PDF page, and mark each claim **Correct**, **Incorrect**, or **Inconclusive**. Reviewers can also attach full-page screenshots, compare conflicting claims from several publishers, and export their decisions with notes. The app runs in the browser and the review workflow makes zero AI model calls. [Instructions and example files](README.md)

## Why it matters

An apparent mismatch can come from a scraper error, a different reporting date, or a publisher counting something different. The tool lets reviewers handle those cases separately. During our bed and doctor count audit, all 48 random checks matched the original disclosures. A separate check found that IFFCO published `04` doctors for one hospital and the scraper dropped the value because it rejected the leading zero. Each random result came from just six checks per source, field, and metric, so the confidence intervals were wide. [Scraper audit and cited evidence](../max-supplemented-data/experiments/verification/scraper-audit/README.md)

The accuracy test can reduce how much a person needs to review. For a sheet of 161 claims, the defaults call for 39 randomly selected rows, 122 fewer than reviewing every row. Those settings use a 90% accuracy threshold, 98% planning accuracy, 5% significance level, and at least 80% power. The sample stays fixed after the draw. Unclear rows stay in the test and may make the result inconclusive. [How the sample is calculated](README.md#test-scraper-accuracy-with-a-random-sample)

## Compute comparison

We benchmarked the browser work associated with two workflows on the same 20 synthetic disclosure pages. The direct-link workflow opened each page at a cited row. The agent evidence workflow made the same visits and used browser control to produce a full-page PNG for each page. Both used the same warmed Chrome session on one computer. We alternated their order across six paired trials and measured Chrome CPU time across its processes. [Benchmark code](benchmarks/browser_workload.py) · [Chrome process CPU counter](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/SystemInfo.pdl)

| Browser workload | Median Chrome CPU per 20 pages | Median elapsed time | PNG data generated |
| --- | ---: | ---: | ---: |
| Open the direct source links | 0.66 seconds | 0.26 seconds | 0 MB |
| Open the links and capture full-page screenshots | 5.12 seconds | 5.87 seconds | 14.5 MB |

Full-page capture used **about 7.8× the Chrome CPU time** in this benchmark. Each page was 1,360 × 4,471 pixels. The direct-link workflow skips that capture step and lets the person read the original page. The benchmark covers page loads and capture on one machine, with some CPU use potentially missed when a browser process exited between readings. [Raw trial measurements](benchmarks/browser-workload.json)

The human workflow uses zero AI inference. The agent workflow also uses model compute to read and judge the evidence. The earlier Codex review exports record decisions and timestamps for entering them into the app. Agent inference charges and human review minutes still need to be measured on the same cases before comparing total cost or speed. [Earlier desk-review record](../max-supplemented-data/experiments/verification/README.md#earlier-desk-review-pilot)

## Planned experiments

| Experiment | What to measure |
| --- | --- |
| **Human vs. agent decisions** | Give both the same frozen mix of straightforward claims, clear extraction errors, and ambiguous sources. Use an independent reviewed reference. Compare correct decisions, missed errors, and inconclusive calls. |
| **Cost of a resolved check** | Compare agent-only, human link-only, and agent-prepared evidence with human review. Time the full workflow. Record agent model usage and charges, browser CPU time, and human active minutes. Include uncertain cases and report cost per correctly resolved claim. |
| **Screenshots vs. direct links** | Randomize which evidence format reviewers see first, using similarly difficult cases. Compare time, correct decisions, and how often reviewers need another source. Include screenshot preparation in the end-to-end comparison. |
| **Random sample vs. full review** | Use sheets whose row accuracy has already been checked. Test whether the planned sample reaches its expected false-pass rate and power. Track how much reviewer work was saved and how often unclear evidence prevents a conclusion. |
