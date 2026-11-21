# Government facility extracts

Collected on 21 November 2026. The deliverable files are `government_facilities.csv` and `government_facilities.jsonl`. They contain only these seven fields:

| Field | Meaning |
| --- | --- |
| `facility_name` | Published facility, branch, or participating-unit name. |
| `phone_numbers` | Published facility contacts, kept as strings with original formatting. |
| `email_addresses` | Published facility contacts. Programme-office footer contacts are excluded. |
| `location` | Published address and district/location labels. |
| `services` | Explicit service, procedure, or specialty wording. |
| `validity_dates` | Published validity periods and associated qualifications, preserved as text. |
| `source_links` | Official source URLs, with PDF page anchors or public record-search links when available. |

JSONL preserves multivalue fields as arrays. In CSV those same fields are JSON-encoded arrays inside cells. An empty array means the field was not available in this extract; an empty service list does not establish that a facility has no services. `schema.json` defines the exact record structure.

## Coverage

| Source | Records | Collection and limits |
| --- | ---: | --- |
| BIS-published CGHS list | 294 | Direct download of the 68-page PDF dated 10 November 2026: 227 main entries and 67 separately listed branch entries. Covers BIS cashless arrangements in Delhi/NCR. |
| ESIC empanelment circular | 6 | Complete four-page PDF text retrieved through the web reader; direct host downloads timed out. Image retrieval also failed. Two Action Cancer Hospital entries have different scopes and remain separate. |
| Tamil Nadu CMCHIS | 2,157 | Complete public export, matching the displayed directory count. It includes hospitals and other participating units such as welfare offices. |
| Rajasthan RGHS | 0 | Homepage, current/legacy PDF downloads, and browser access timed out. The empty extract is an access failure, not evidence that no hospitals are listed. |
| Kerala SHA Sruthitharangam | 11 | Complete rendered public browser table. Direct HTTP returned 403. This is a small programme list, not a statewide census. |
| **Total source records** | **2,468** | Records are concatenated without cross-source matching or entity deduplication. This is not a unique-facility count. |

Per-source CSV/JSONL files are included alongside the combined files. Retrieval methods, source hashes, row fragments and coverage checks are under `audit/`; source snapshots are under `raw/`. Only the CSV/JSONL deliverables have the restricted seven-field schema.

## Data limitations

- CMCHIS has explicit service flags for 1,960 records; 197 have none. Its directory/profile does not publish facility phone numbers, emails or validity periods. Those fields remain empty. Committee meeting dates and administrative years were not treated as validity dates.
- Kerala publishes contacts for all 11 entries, but only NISH has an explicit facility-specific service label: Audio Verbal Therapy. The other ten service arrays are empty rather than inferred from the programme's general scope. No validity periods are published.
- BIS retains exited/suspended/non-renewing entries. Its 19-entry introductory warning and row-specific validity qualifications are retained in `validity_dates`; dates alone must not be interpreted as active status. Expired entries are not silently discarded.
- PDF service scopes remain paragraphs when splitting them could detach a qualification from its procedure. Broad specialties such as nephrology are not expanded into a dialysis claim.
- Published contact spelling and formatting are preserved. Kerala's Baby Memorial row contains `O495-2777777` with a letter O. No guessed correction was made. Some PDF phone numbers are local numbers or ranges, not complete internationally formatted numbers.
- One BIS email cell contained an ambiguous repeated trailing fragment. The fragment was omitted while its two complete addresses and original source text were retained; see the central audit warning.
- Kerala's Ascent Hospital row says district Malappuram but gives a Kozhikode address. Both source claims remain visible in `location`.
- These are source claims and scheme scopes, not independently corroborated operational services or real-time availability. No statistical manual-validation audit was resumed.

## Reproduce

From the repository root, install `scripts/requirements_government_sources.txt` into an isolated Python environment. The saved snapshots allow these commands to regenerate the successful extracts without downloading again:

```bash
python scripts/scrape_government_central.py
python scripts/scrape_government_cmchis.py --offline
python scripts/scrape_government_kerala.py
python scripts/build_government_sources.py
```

Kerala's default input is the saved browser-DOM table snapshot; `--refresh` attempts a new HTTP/BeautifulSoup fetch and fails explicitly if blocked. CMCHIS supports `--refresh` to fetch the current export. The central script's ESIC extraction uses the saved full-document web-reader text; it is not an automatic refresh of ESIC. Its `--download` option retries public PDF downloads.

`scripts/scrape_government_rghs.py` can retry RGHS or accept an official PDF via `--pdf`; its PDF parser could not be validated while the source was inaccessible. Do not claim a successful RGHS refresh based only on that script's presence.

`build_government_sources.py` verifies seven-field schemas, value types, nonempty names/provenance, and exact agreement between each source's CSV and JSONL before combining them. It does not resolve duplicates or establish real-world correctness.

## Official sources

- [BIS CGHS list](https://www.bis.gov.in/wp-content/uploads/2026/11/Circular-and-227-cghs-cashless-consolidated-list-10-Nov-2026.pdf)
- [ESIC circular](https://dmn.esic.gov.in/attachments/sstfile/Empanelment_of_CGHS_Empanelled_HCOs_1772002059.pdf)
- [Tamil Nadu CMCHIS directory](https://www.cmchistn.com/empanelment/hospital-list)
- [Rajasthan RGHS](https://rghs.rajasthan.gov.in/RGHS/home/)
- [Kerala SHA programme list](https://sha.kerala.gov.in/?page_id=5749&lang=en)
