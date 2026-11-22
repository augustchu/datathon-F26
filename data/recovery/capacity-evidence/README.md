# External bed and doctor counts

The earlier field-recovery pilot approved one missing bed count, 35 beds at Faridabad Medical Center. The saved empanelment extracts lack a bed-count field. This 21 November 2026 replay uses four different public tables that report counts at the facility level. [Earlier pilot](../SAMPLING.md) · [Why the first extracts were shelved](../../../non-ml/DATA_COMPARISON.md#dead-end-for-capacity-verification-using-the-saved-government-extracts)

## Coverage on the saved Virtue Foundation audit

| Source | Scope and period | Imported rows | Exact branch matches | Branches with a missing FDR bed count and an outside total |
| --- | --- | ---: | ---: | ---: |
| IFFCO Tokio | Reported total beds and doctors, 31 March 2025 | 7,380 | 211 | 113 |
| SBI General | Reported total beds and doctors, 31 March 2022 for all positive bed counts | 17,529 | 556 | 290 |
| Delhi DGHS | Approved bed strength, list published 18 July 2025 with registration expiry retained | 1,184 | 45 | 0 |
| Kerala DHS | Sanctioned inpatient beds, July 2026 | 1,289 | 0 | 0 |
| **Distinct linked FDR branches** | **10,059 branch records available for 10,073 audit IDs** | **27,382 source rows** | **696** | **353** |

The workbook marks **65 claims across 37 branches** where FDR already cites the same publisher domain. After excluding those claims, sources new to the matched branch supplied total-bed candidates for **347** branches missing a bed count and doctor-count candidates for **270** branches missing a doctor count. The workbook places **93 bed counts** and **71 doctor counts** dated 2025 onward beside the original fields. Each is a positive whole-facility count with strong branch linkage and a single value among the qualifying 2025-onward reports. These are historical source-reported candidates. Older counts and different scopes stay in the evidence table with their dates. Delhi's approved beds describe registration scope. Kerala's sanctioned beds describe administratively allocated inpatient capacity. [IFFCO Tokio HTML table](https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters) · [SBI General disclosure](https://content.sbigeneral.in/uploads/f22596609234455e8b24df7859027ce6.pdf) · [Delhi DGHS list](https://dgehs.delhi.gov.in/sites/default/files/inline-files/msnh_27.pdf) · [Kerala DHS list](https://dhs.kerala.gov.in/wp-content/uploads/2026/08/Sanctioned_IP_Beds_DHS_Kerala_July_2026-final.pdf)

The accepted joins in this replay used a unique normalized facility name and matching PIN. The matcher also supports exact full-address matching when the name and address are unique. Branches with a conflicting address, ambiguous identity or an existing location hold go to review. A similar name or an alias in the same PIN is a lead for review. Kerala supplied one such lead. Distinct facilities can share a PIN, and exact matching trades coverage for fewer mistaken branch links. Fourteen audit IDs lacked a corresponding branch record and were excluded from the join and workbook.

The insurer reports produced **121 equal** and **243 different** numeric comparisons with already populated FDR bed counts. These are source-claim comparisons, so a branch with multiple reports can appear more than once. FDR observation dates are unknown for this comparison. Different values can reflect a real change over time, and equal values alone leave the FDR period unconfirmed. The two insurers may obtain counts from the same hospital, so different publisher domains leave the underlying independence unestablished. The original FDR labels and model inputs were preserved.

## Browser adjudications

- **Holy Family Hospital, Bandra, PIN 400050.** FDR lists 268 and IFFCO Tokio reports 232 as of March 2025. The hospital page supports the FDR value of 268 as a hospital-published total. The page gives no observation date. [About the hospital](https://holyfamilyhospital.in/about-us) · [Browser capture](holy-family-bandra-beds.jpg)
- **Shri Balaji Metro Hospital, Raigarh, PIN 496001.** FDR lists 450 and IFFCO Tokio reports 106 as of March 2025. The state board's 2025 permission describes an expansion from 106 to 450. The branch website describes both 450 and 400 beds. The reviewed value is missing because the operating total remains unresolved. [Hospital page](https://shribalajimetrohospital.in/about) · [Expansion permission](https://ocmms.nic.in/OCMMS_NEW/download.action?applicationId=19540650&industryFor=both&industryState=Chhattisgarh&industryType=CTE) · [Browser capture](shri-balaji-metro-raigarh-beds.jpg)

These two reviews illustrate the adjudication rules. Their outcomes apply to the named branches. The remaining queue includes count differences, scope differences, potential identity matches and location holds. [Review records](reviews.json)

## Replay

Download the four linked documents before running the commands. Use the raw HTML response for IFFCO Tokio. The parsers require Beautiful Soup, Poppler's `pdftotext` and `pdftocairo`, and `pdfplumber`. The FDR inputs are JSONL checkpoints from the prepared location and count audits. Keep working files and FDR-derived row exports in the team's approved local or workspace storage.

```bash
python3 scripts/extract_iffco_capacity.py iffco.html iffco.csv
python3 scripts/extract_sbi_capacity.py sbi-2022.pdf --edition 2022 --output sbi-2022.csv
python3 scripts/scrape_capacity_kerala.py --pdf kerala.pdf --output kerala.csv
python3 scripts/extract_delhi_capacity.py --pdf delhi.pdf --output delhi.csv
python3 scripts/match_external_capacity.py --entities entities.jsonl \
  --targets capacity_target_audit.jsonl --doctor-targets number_doctors_target_audit.jsonl \
  --location-records location_records.jsonl \
  --source iffco.csv --source sbi-2022.csv --source kerala.csv --source delhi.csv \
  --output-dir capacity_match
```

The matcher writes `matched_claims.csv`, `review_queue.csv` and `summary.json`. Source rows preserve counts, scope, reporting date or period, source record ID and linked page. The Delhi parser also preserves registration expiry. The location checkpoint provides the list of source URLs from each FDR branch. The SBI source's parsed PIN, bed and doctor values agreed with a second text extraction for all 17,529 rows. Kerala's extracted sanctioned counts sum to the published statewide total of 38,525. These are extraction checks.

The National Hospital Directory listing returned HTTP 503. Go Digit's March 2025 PDF returned HTTP 403 over direct download and in the browser. Star Health's March 2024 disclosure is accessible and has 13,335 rows with ROHINI identifiers, facility totals and city/state. Its lack of street addresses and PINs limits direct linkage. A join using the same ROHINI identifier in an address-bearing source would provide stronger linkage. NBEMS publishes some whole-hospital totals on individual profiles, with the observation date described as the last update. [National directory](https://www.data.gov.in/resource/national-hospital-directory-geo-code-and-additional-parameters-updated-till-last-month) · [Go Digit disclosure](https://www.godigit.com/content/dam/godigit/general/financials/public-disclosure/disclosure-of-quality-parameter-of-network-hospitals-march-25.pdf) · [Star Health disclosures](https://www.starhealth.in/media-center/) · [Example NBEMS profile](https://accr.natboard.edu.in/online_user/hospital_profile.php?token=yhYmWGDnk%29Tw0qb9jxFCI%282Xrd0sB51ZEc23p20%40NJ0vPt7UaeR6flLgmfdw9WNaypzCtl7UP4Ls)
