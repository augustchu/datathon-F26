# Challenge data and public-source comparison

Snapshot date for the collected public sources is 21 November 2026. Counts in this document refer to the named dataset or extract. Several rows can describe one branch, and one listing can cover multiple services or an entire programme.

## What the datasets measure

| Dataset | Unit of observation | Fields relevant to the lookup | How the project uses it |
| --- | --- | --- | --- |
| Virtue Foundation Foundational Data Refresh (FDR) | Intended as one consolidated facility row. Some records can mix branches or contain contradictory claims. | Facility name and identifiers, addresses, PIN and coordinates, contacts where published, descriptions, capabilities, procedures, specialties, equipment, establishment year, doctors, bed capacity and source URLs. | The primary facility inventory, source text for service candidates, and observed-count targets for the EDA. |
| Public government programme extracts | One listed facility, branch, service scope or participating unit per source row. | Facility name, address/location, explicit service or specialty wording, published phones and email, validity text and official links. | Narrower service corroboration and contacts, with scheme and date qualifications retained. [Government extracts and official source links](../data/government_sources/README.md) |
| Public accreditation, certification and blood-service directories | One directory or certification entry. | Facility identity, status or exact blood-facility type, address and coordinates when published, selected validity dates, and original record references. | Identity, directory type and certification evidence. Detailed accredited scope can be reviewed separately when linked. [Supplemental schema and extraction limits](../data/supplemental/schema.json) |
| India Post PIN directory and NFHS-5 | A post office or a surveyed district, respectively. | Published PIN–state–district mappings, postal coordinates, and 2019–21 area-level health/household indicators with sample flags. | Check geography, generate approximate area origins and provide district context. Keep postal and facility points distinct. [India Post directory](https://www.data.gov.in/resource/all-india-pincode-directory-till-last-month) · [NFHS-5 district factsheets](https://www.data.gov.in/catalog/national-family-health-survey-5-nfhs-5-india-districts-factsheet-data-provisional) |

FDR metadata describes a public-web ingestion and consolidation pipeline with generative extraction and entity resolution. Its intended breadth helps surface service leads and compare facilities. Government PDFs and programme lists usually have a narrower mandate and can offer more precise evidence about a named branch, a covered procedure or a validity period. A source URL in the FDR row can be a candidate link where the field-level origin is ambiguous. Keep the exact excerpt, source and scope together before using it in a recommendation. [Marketplace listing](https://dbc-4f3d9a42-7d6a.cloud.databricks.com/marketplace/consumer/listings/19326b3d-db63-4627-abc0-cf4e8131a305) · [Collection action log](../ACTION_LOG.md)

## Original FDR coverage

The challenge brief describes about **10,000 facilities** and **51 attributes**. Its figures below are published coverage percentages. They measure general field availability in the brief and should be kept separate from counts of null entries, populated text or independently accepted values in a specific snapshot.

| Field | Coverage in the challenge brief |
| --- | ---: |
| Description | 100% |
| Capability | 99.7% |
| Procedure | 92.5% |
| Equipment | 77.0% |
| Year established | 47.8% |
| Number of doctors | 36.4% |
| Capacity | 25.2% |

### Archived Unity Catalog counts and FDR content checks

Verified archived Unity Catalog metadata for the FDR facilities table reports **10,088 rows and 51 fields**. The counts below are catalog non-NULL counts for individual columns. The denominator in the last column is 10,088. The archive contains schema and statistics. The observed content checks require access to facility rows.

| FDR column | Non-NULL rows in catalog metadata | Share of FDR rows |
| --- | ---: | ---: |
| `description` | 10,008 | 99.2% |
| `capacity` | 9,974 | 98.9% |
| `numberDoctors` | 9,975 | 98.9% |
| `phone_numbers` | 10,031 | 99.4% |
| `officialPhone` | 10,031 | 99.4% |
| `latitude` | 9,970 | 98.8% |
| `longitude` | 9,970 | 98.8% |
| `address_zipOrPostcode` | 10,030 | 99.4% |
| `source_urls` | 9,970 | 98.8% |

The cited **99.4%** phone figure represents non-NULL entries in each phone column separately. A content audit must check for blanks, placeholders, empty collections, valid formats, duplicates across fields and numbers published for the correct branch. The separate coordinate counts leave paired coverage, decimal ranges and location validity open. `source_urls` needs link parsing and claim-level attribution checks. The gap between the brief's count coverage and the archived non-NULL rates cannot be interpreted until the team examines actual bed and doctor values for exact counts, ranges, placeholders and conflicting labels. Use [source coverage](notebooks/01_source_eda.ipynb), [geography](notebooks/02_facility_and_geography.ipynb) and [count EDA](notebooks/03_count_field_eda.ipynb) for those audits.

## Measured public extract coverage

The local pipeline loaded **31,507 source rows** across the saved supplemental and government extracts. Published links accompany all loaded rows. About 70% are NQAS certification listings with state-level geography and no service descriptions in the extract. The programme extracts cover specific populations, including BIS cashless arrangements in Delhi/NCR, CMCHIS participants in Tamil Nadu, and a Kerala hearing-care programme. NABH contributes 500 rows from 20 evenly spaced pages in its listing. RGHS contributes zero rows because the public source was inaccessible. [Source inventory and collection decisions](../ACTION_LOG.md)

| Saved government extract | Rows | With service or specialty text | With a listed phone | With an email | With validity or status text |
| --- | ---: | ---: | ---: | ---: | ---: |
| BIS CGHS | 294 | 294 | 287 | 245 | 294 |
| ESIC | 6 | 6 | 6 | 0 | 6 |
| Tamil Nadu CMCHIS | 2,157 | 1,960 | 0 | 0 | 0 |
| Kerala SHA Sruthitharangam | 11 | 1 | 11 | 11 | 0 |
| Rajasthan RGHS | 0 | 0 | 0 | 0 | 0 |

These are counts of populated fields in the saved seven-field exports. The validity field can include statuses such as suspended, exited or non-renewing. A listed phone is useful for follow-up. The format checks yielded plausible numbers in 286 BIS rows, six ESIC rows and 11 Kerala rows. A call is needed to check the branch, service and current availability. The three older supplemental extracts do not include contacts in their saved schema. [Government export schema and source links](../data/government_sources/README.md) · [Supplemental schema](../data/supplemental/schema.json)

| Saved directory extract | Rows | Published geography in extract | Evidence it contributes |
| --- | ---: | --- | --- |
| NHSRC NQAS | 22,015 | State labels, with no address or coordinates in the extract. | Displayed certification and a validity date. Dates for 8,274 entries preceded the snapshot date. [Official certification listing](https://qps.nhsrcindia.org/quality-certification/national-level-certificate) |
| NABH | 500 | 500 addresses, 449 source coordinate pairs, no standalone state field. Of these pairs, 448 passed the broad India coordinate check. | Directory status and accreditation identifier. A scope certificate can separately list procedures when available. [Official directory](https://nabh.co/find-a-healthcare-organisation/) · [Example scope PDF](https://portal.nabh.co/Documents/AccreditedList/Hospitals/H-2025-1498_5th%20Edition.pdf) |
| eRaktKosh | 6,524 | Collection queried all 36 state/UT lookup options. State and address on all rows, with 5,267 source coordinate pairs after removing 1,257 zero-coordinate placeholders. Of those pairs, 5,263 passed the broad India check. | Exact directory types for 4,754 blood banks and 1,770 blood storage units. Live inventory and slots need separate evidence. [Official blood-centre directory](https://www.eraktkosh.mohfw.gov.in/eraktkoshPortal/#/publicPages/bloodBankDirectory) |

The public audit found a six-digit PIN candidate in 211 BIS, 562 CMCHIS, five ESIC, 11 Kerala, 397 NABH and 168 eRaktKosh rows. The government extracts above supplied no facility coordinate pairs. Some address text is contradictory. The Kerala Ascent listing gives Malappuram as the district and a Kozhikode address. Numeric coordinate checks on the public directories establish plausibility only. Link records conservatively, preserve reported and postal-derived geography separately, and review flagged rows before routing. [Government data limitations](../data/government_sources/README.md) · [Facility and geography audit](notebooks/02_facility_and_geography.ipynb)

## Combining the datasets

- **Recommend with service-specific scope.** A dialysis label in a named scheme listing can corroborate a matching FDR branch. A nephrology specialty or dialysis machine mention can support follow-up. A documented procedure is stronger than either alone. Retain care-level qualifiers such as NICU Level III and distinctions such as blood storage units versus blood banks. Scheme eligibility and a service listing should be shown with their scope and date. [Delhi DGEHS service list](https://dgehs.delhi.gov.in/sites/default/files/DGHS/universal/om_40.pdf) · [Nellore district facility services](https://spsnellore.ap.gov.in/district-coordinator-of-hospital-services/)
- **Verify the claim and how it was copied separately.** Preliminary browser spot checks found six matching NABH/NQAS rows and three unverified eRaktKosh rows. The larger 95% confidence audit was paused. A page match checks extraction fidelity. Independent evidence for an operating service, precise location or current availability has to answer a different question. [Saved browser audit results](../data/external/verification/report.json) · [Action log](../ACTION_LOG.md)
- **Model missing FDR fields with FDR labels.** The saved public extracts do not provide consistent target counts for beds and doctors. Use the original FDR schema to select eligible predictors strictly after `countries`, then screen for target mentions and other leakage. Keep geography and source identifiers for linkage, group-based validation and coverage analysis. Audit exact usable target counts after branch linkage before splitting. Public contacts and curated service scope strengthen the evidence displayed to users. They require an explicitly evaluated design before being used as predictors or as service labels. [Count EDA](notebooks/03_count_field_eda.ipynb) · [Previous capacity random forest](../ML/capacity_EDA/README.md)
- **Keep district context and load evidence at their proper grain.** The PIN source covers post offices and a PIN can map to multiple districts. NFHS indicators describe districts, with suppression and small-sample flags. Facility total beds and doctor counts describe infrastructure. The optional load selector needs fresh comparable service-level availability or clearly labeled demand and capacity scenarios. [District context notebook](notebooks/06_district_context.ipynb) · [Load policy scenarios](notebooks/08_load_policy_scenarios.ipynb)
