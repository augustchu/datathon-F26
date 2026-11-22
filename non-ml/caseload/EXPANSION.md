# Facility caseload expansion

Coverage expanded from **5 to 26 distinct facilities**. The expansion adds **21** facilities. Published counts cover **16** and baseline estimates cover **9**, with **4** sites represented in both. The five earlier dialysis estimates bring the total number of facilities with modeled caseloads to **14**. [Expansion results](expanded_coverage.json) · [Earlier dialysis fills](baseline_coverage.json)

There are **35 directly reported**, **20 estimated** and **5 withheld** facility-service-period targets in the expansion. Eight estimates fill historical months whose reports are listed but could not be read. The other 12 apply to closed periods for which the reviewed sources supplied no matching total. Counting only FY2025–26 gives **17 distinct facilities**, including **10 with estimates**. This includes 7 sites with 12 newly recovered reported counts, plus 5 sites with 8 newly estimated gaps and the five prior dialysis sites.

## Coverage by Care needed

Facility counts in each column are distinct within a care option. A facility can have multiple services or both reported and estimated periods. A zero indicates that this audit supplied no qualifying fill for the corresponding option or status.

| Care needed | Prior dialysis estimates | New directly reported facilities | New baseline-estimated facilities | Withheld periods |
| --- | ---: | ---: | ---: | ---: |
| Dialysis | 5 | 0 | 0 | 0 |
| Emergency C-section | 0 | 0 | 0 | 0 |
| Maternity / delivery | 0 | 8 | 3 | 1 |
| NICU | 0 | 1 | 0 | 1 |
| Pediatric care | 0 | 2 | 1 | 0 |
| Cardiac surgery | 0 | 2 | 1 | 0 |
| Cardiology consultation | 0 | 2 | 0 | 2 |
| MRI | 0 | 4 | 3 | 0 |
| CT scan | 0 | 4 | 2 | 0 |
| Burn care | 0 | 0 | 0 | 0 |
| Orthopedic surgery | 0 | 1 | 1 | 0 |
| Fracture care | 0 | 0 | 0 | 0 |
| Dental care | 0 | 2 | 0 | 0 |
| Oral surgery | 0 | 2 | 0 | 0 |
| Cataract surgery | 0 | 0 | 0 | 0 |
| Stroke care | 0 | 0 | 1 | 0 |
| Chemotherapy | 0 | 0 | 1 | 0 |
| Trauma / emergency | 0 | 7 | 2 | 1 |

## Facility and period selection

This audit uses the November 22, 2026 final capacity release. It has 10,088 rows and 10,073 distinct nonblank facility IDs, including 9,574 with a routing-eligible row. The SHA-256 of `facilities_newest_capacity.csv` is `0aecdb730ed177ac05f8787c9c024d3f88dd8d7c97f702b8fdc3d0b1e4d1af94`. The CSV has no service-by-period caseload field. A fill attaches a reported or estimated value to a facility ID, care option, reporting scope, and closed time period in this audit. The denominator for each care option remains unknown because the release's service inventory is incomplete. [Facility release and evidence grades](../../max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md) · [Earlier coverage audit](BASELINES.md)

Each input carries the reporting institution, project ID, release state and PIN, official campus evidence, source URL and table locator, related dashboard option, and exact activity scope. The script checks the release ID, state, PIN, and routing eligibility. The branch links were reviewed by name and location, with further corroboration such as a shared hospital contact where PINs disagree. The manifests preserve each discrepancy, including Whitefield, AIIMS Bhubaneswar, and Himalayan Hospital. Counts from a clinic, department, programme or campus stay at that scope. [Source manifests](expansion/)

The audit includes complete fiscal years, calendar years, and individual months through October 2026. A target is a recent complete observation recovered for an exact site, or a closed period where the relevant count could not be read or located. The target list includes a few earlier gaps because the same facility has published usable nearby months. Its periods therefore differ by source. Each reported annual total is divided by 12 to express average activity per month. This conversion leaves monthly variation unknown. A monthly target uses its own count. The units are events such as attendances, operations, scans, or administrations and can include repeat visits or treatments for an individual.

The earlier I.T.S. dental observations have been corrected to **I.T.S. Centre for Dental Studies & Research, Murad Nagar, Ghaziabad**, PIN 201206. The publishing campus, contact page, and release row support that branch. The published registration and oral surgery counts are unchanged. [Contact](https://itsdentalcollege.com/contact) · [OPD and procedure statistics](https://itsdentalcollege.com/opd-patients)

## Source families and coverage limits

| Source family | What it supplies for the audit |
| --- | --- |
| Government and public hospital service reports | Named maternity and casualty totals at Rao Tula Ram Memorial, four Tamil Nadu facilities with delivery totals, King Edward Memorial and Sanjay Gandhi Memorial. Delhi's Lok Nayak Hospital publishes monthly emergency attendance and calendar 2025 OPD totals for general pediatric medicine and its pediatric cardiology clinic. AIIMS Nagpur supplies a monthly Trauma & Emergency OPD series. AIIMS Bhubaneswar and SCTIMST supply annual specialist activity. [Tamil Nadu NHM, tables 1 and 3](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf) · [Rao Tula Ram performance](https://rtrmh.delhi.gov.in/rtrmh/annual-report-2012-13) · [Lok Nayak report](https://lnjp.delhi.gov.in/lnjp/annual-report-opd-jan-2025-dec-2025) · [AIIMS Nagpur August 2026 census](https://aiimsnagpur.edu.in/upload/monthly_newsletter/fd39620399cabc9428ee57a1bf089d8e.pdf) · [AIIMS Bhubaneswar archive](https://aiimsbhubaneswar.nic.in/annual-report/) · [SCTIMST archive](https://www.sctimst.ac.in/About%20SCTIMST/Annual%20Report/) |
| Medical and dental college activity returns | Named monthly activity in pediatrics, emergency, delivery, orthopedics, scans, dental registrations and oral surgery. Age, treatment setting, and major or minor procedure scope follow the relevant table. [Himalayan Hospital](https://hims.srhu.edu.in/mci-information/) · [SGRDI](https://www.sgrdimsr.in/page-clinical_material.html) · [Zydus Dahod, October 2026](https://www.zmchdahod.in/pdf/Clinical_Material_Month_of_October_2026.pdf) · [I.T.S.](https://itsdentalcollege.com/opd-patients) · [Vishnu Dental College](https://vdc.edu.in/department-wise-number-of-patients/) |
| Specialty hospital and charitable network reports | Whitefield reports hospital-wide activity by service, and U. N. Mehta reports monthly imaging and a separate Ayushman Bharat Yojana cardiac surgery series. Emmanuel Hospital Association lists each member hospital's delivery activity by fiscal year. The FY2021–22 network-level summary repeats earlier counts for the three linked sites, so the manifest uses the individual hospital tables in that report edition. [Whitefield FY2025–26 report](https://whitefield.sssihms.org/about-hospital/annual-report-2025-26/) · [U. N. Mehta statistics](https://www.unmicrc.org/Statistics) · [EHA annual reports](https://eha-health.org/downloads/annual-reports) |

Source review also identified narrower data gaps. Emergency C-section requires a count explicitly marked emergency. All-cesarean tables give a different denominator. Emergency and casualty attendance includes visits unrelated to trauma unless the table provides a trauma category. Major orthopedic surgery includes elective operations, so it cannot identify fracture care. A neurointerventional thrombectomy count covers a small subgroup of stroke care. A burn or plastic-surgery department total requires a burn-specific breakdown. National and network aggregates stay at their published geography or programme scope. [Saifai fracture admission cohort](https://pmc.ncbi.nlm.nih.gov/articles/PMC13271864/) · [King Edward Memorial report](https://www.kem.edu/wp-content/uploads/2026/02/Annual-Report-2025-2nd-Feb-2026.pdf) · [Safdarjung 2025 combined specialty reporting](https://www.vmmc-sjh.mohfw.gov.in/sites/default/files/Mera%20Aspataal%20Report%20-%20Year%202025%20%28January%20to%20December%202025%29.pdf)

Aravind reports branch-level cataract surgery for its included hospitals. The affiliated Kolkata hospital in the release has documented cataract services, but an exact contemporary volume was unavailable for that branch. Sankara's Gift of Vision branch tables give outreach surgeries under a single total. The count has no procedure-level breakdown in the table. The audit leaves these leads out of the cataract fills. [Aravind FY2025–26 activity report](https://aravind.org/wp-content/uploads/2026/09/Activity-Report-2025-2026.pdf) · [Kolkata accreditation scope](https://portal.nabh.co/Documents/AccreditedList/Eye%20Care%20Organisation/ECO-2024-0516.pdf) · [Sankara outreach performance](https://sankaraeye.com/wp-content/uploads/2026/04/GOV-Performance-Report-as-on-31st-March-2026-2.pdf)

## Baseline evaluation

The two candidates predict each target with its own facility, service scope, and reporting grain. **Last observed** carries forward the most recent prior actual count. **Recent median** takes a median of comparable observed counts in the 12 months or three fiscal or calendar years up to that most recent period. Both methods require the latest observation at the anchor and at least three months or two annual observations in the same recent window. Fiscal and calendar year series are evaluated separately. Missing counted periods stay blank and observed zeros count as actuals.

For each earlier observed period, the test uses the same number of months or years from the anchor to the prediction period as the intended fill. It requires an exact observed anchor and limits the training data to the anchor or earlier. The candidates are compared on the same held-out periods, with at least four monthly or two annual periods required for an estimate. Selection minimizes mean absolute error (MAE), in the reported unit per month. Ties go to last observed. The JSON contains both predictions and the actual for each scored period. A missing target with too few tests stays withheld. For observed targets, the published count takes precedence regardless of the test results.

This is a rolling test of same-site estimates using event dates. Publication delays are outside the test. The error on the trials used to select the method is descriptive and can favor the selected method. Annual rows with two tests provide particularly limited evidence. The tests apply to facilities with observed history at the source's reporting grain. MAEs for different scopes and units are reported individually. The prior five dialysis results use the separate programme baseline test. [Dialysis programme results](BASELINES.md#apply-the-prior-year-dialysis-average)

## Model results for missing periods

Recent median had the lower MAE for 11 filled periods. Last observed was selected for 9. The table gives the chosen point estimate, the number of same-horizon tests and both candidate MAEs. Estimates and errors use the series activity unit per month. Full source counts and definitions are in the JSON.

| Facility | Care needed and unit | Target | Estimate/month | Test periods | Last observed MAE | Median MAE | Chosen |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| AIIMS Bhubaneswar | CT scan, scans | FY2025–26 | 3,825.33 | 5 | 455.68 | 620.88 | Last observed |
| AIIMS Bhubaneswar | Chemotherapy, administrations | FY2025–26 | 4,359.42 | 4 | 791.90 | 1,413.07 | Last observed |
| AIIMS Bhubaneswar | MRI, scans | FY2025–26 | 1,245.00 | 5 | 201.90 | 298.65 | Last observed |
| AIIMS Nagpur | Trauma / emergency, visits | 2026-09 | 2,390.50 | 15 | 335.67 | 330.63 | Recent median |
| AIIMS Nagpur | Trauma / emergency, visits | 2026-10 | 3,011.00 | 14 | 327.86 | 393.11 | Last observed |
| Christian Hospital Chhatarpur | Maternity / delivery, deliveries | FY2025–26 | 166.33 | 4 | 25.00 | 38.75 | Last observed |
| Herbertpur Christian Hospital | Maternity / delivery, deliveries | FY2025–26 | 20.75 | 4 | 23.21 | 36.73 | Last observed |
| Himalayan Hospital | CT scan, scans | 2025-09 † | 1,338.00 | 13 | 367.54 | 225.50 | Recent median |
| Himalayan Hospital | CT scan, scans | 2026-02 † | 1,316.00 | 16 | 321.50 | 197.38 | Recent median |
| Himalayan Hospital | MRI, scans | 2025-09 † | 660.00 | 13 | 160.00 | 114.50 | Recent median |
| Himalayan Hospital | MRI, scans | 2026-02 † | 653.50 | 16 | 144.25 | 103.00 | Recent median |
| Himalayan Hospital | Orthopedic surgery, operations | 2025-09 † | 171.00 | 13 | 48.54 | 34.31 | Recent median |
| Himalayan Hospital | Orthopedic surgery, operations | 2026-02 † | 145.50 | 16 | 42.38 | 33.31 | Recent median |
| Himalayan Hospital | Pediatric care, visits | 2025-09 † | 5,087.00 | 13 | 383.92 | 322.54 | Recent median |
| Himalayan Hospital | Pediatric care, visits | 2026-02 † | 5,158.00 | 16 | 356.25 | 344.62 | Recent median |
| Kachhwa Christian Hospital | Maternity / delivery, deliveries | FY2025–26 | 4.83 | 4 | 1.08 | 1.58 | Last observed |
| LNJP Hospital | Trauma / emergency, visits | 2026-01 | 17,995.00 | 9 | 2,171.33 | 2,575.33 | Last observed |
| SCTIMST, Thiruvananthapuram | Cardiac surgery, operations | FY2025–26 | 161.92 | 2 | 18.12 | 14.96 | Recent median |
| SCTIMST, Thiruvananthapuram | Stroke care, mechanical thrombectomy procedures | FY2025–26 | 2.67 | 2 | 0.25 | 0.27 | Last observed |
| U. N. Mehta, Ahmedabad | MRI, scans | 2025-12 | 135.00 | 8 | 29.50 | 21.81 | Recent median |

† Himalayan Hospital's September 2025 and February 2026 PDFs are listed in its clinical material archive. Attempts to retrieve their contents failed. These are provisional backfills for retrieval gaps. The script withholds both corresponding cardiology targets because prior tables often combine medicine and cardiology and the usable rows yield too few matched tests. [Himalayan Hospital clinical material](https://hims.srhu.edu.in/mci-information/)

AIIMS Nagpur's October 2026 target uses a two-month horizon from the August observation. Its September estimate never enters that October prediction. Other filled targets use one period of the source's reporting grain. At AIIMS Bhubaneswar, the NICU narrative specifies calendar years for the last two recoverable admissions totals. Earlier fiscal-year counts would shift the reporting window. The audit attaches **424 calendar-2024 admissions** as reported and withholds the calendar-2025 estimate. Its other withheld targets are FY2025–26 maternity and emergency because each had only one matched test. [Nagpur monthly services census](https://aiimsnagpur.edu.in/upload/monthly_newsletter/fd39620399cabc9428ee57a1bf089d8e.pdf) · [AIIMS Bhubaneswar annual reports](https://aiimsbhubaneswar.nic.in/annual-report/)

## Use in load distribution

The result can supply a historically grounded volume assumption where the load feature can resolve the exact facility, care option, scope and date. For example, emergency OPD attendance describes historical encounter frequency and the chemotherapy series measures administrations that can recur for the same patient. Point estimates are preliminary where activity shifted, including the marked drop in Herbertpur delivery totals, or the actual report is listed but unreadable, as at Himalayan Hospital. Compare any future recovered report against the baseline and give the reported count precedence.

These historic counts support evidence that a facility recorded the activity in a defined period. Availability for routing requires current service and staffing information. Load pressure also requires service-specific staffed capacity and a time window aligned with the numerator. District or catchment population can inform demand separately. This run applies the baselines to an offline coverage file and leaves those operating inputs outside the model.

## Reproduce

Run from the repository root using Python 3.10 or later. Use the exact final release CSV. The script verifies the input SHA-256 against [baseline_coverage.json](baseline_coverage.json) before resolving the facility IDs.

```sh
python3 non-ml/caseload/expand_coverage.py \
  --facilities "/path/to/facilities_newest_capacity.csv" \
  --output non-ml/caseload/expanded_coverage.json
python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```

A manifest under `expansion/` has `sources`, `sites` and `series`. Each series specifies `scope`, `unit`, one reporting `grain`, source-tagged observation batches, and explicitly selected targets. Annual batch dates use April for a fiscal year or January for a calendar year. `null` skips an observation. Each output target gives its inclusive period, target source review, provenance and `status`. `reported` carries the source count. `baseline_estimate` carries the chosen method, source periods, comparable historical trials, and forecast horizon. `gap_kind` distinguishes a listed but unreadable report from a target with no matching total. `gap_source` links to the unreadable report. `withheld` carries a reason. Aggregate coverage counts distinct facility IDs and the targets table keeps multiple time periods separately. Follow the release identifier when joining. [Machine-readable results](expanded_coverage.json) · [Expansion script](expand_coverage.py)
