# Iteration 02 · 48-case extension

This run adds cases R37–R48 to the exact first 36 without replacing any original case. It adds six bed and six doctor comparisons, two per insurer-availability stratum in each field. Its outputs live under `local/iteration-02/`. The frozen sample SHA-256 is `0bbc85ff3c5b3f927c6314d0292abec58584aa05cd38faa9f35601dca60dd88d`. [Initial reviews](../review/initial/) · [New reviews](../review/extension/)

## What the review could establish

| Field | Sampled | Supported exact value | At least one existing candidate matched |
| --- | ---: | ---: | ---: |
| Whole-facility bed capacity | 22 | 7 | 5 |
| Total doctors | 20 | 0 | 0 |
| Hospital website | 5 | 5 | 5 |
| Establishment year | 1 | 0 | 0 |

The 20 doctor reviews surfaced clinician directories, rounded counts, staffing subsets and address mismatches. These findings explain the absence of exact training labels. Candidate counts from estimated or rounded records received no training target. The date and scope requirements were consequential for beds as well. For example, an accreditation certificate gave a sanctioned bed count and the provider website gave a group-wide count. A known operating total needs evidence of its own scope. [Doctor reviews](../review/initial/doctors-r17-r30.json) · [Extended doctor reviews](../review/extension/doctors-r43-r48.json) · [Ashwini example](../review/initial/bed-r01-r08.json)

## IFFCO Tokio versus SBI General

| Scope of comparison | IFFCO Tokio | SBI General |
| --- | ---: | ---: |
| Agreement where the insurer appeared and a reviewed bed value was established | 1 of 4 | 1 of 5 |
| Agreement on the two supported bed cases with both insurers present | 1 of 2 | 1 of 2 |
| Supported cases where the insurers gave different bed totals | 0 | 0 |
| Supported cases where the insurers gave different doctor totals | 0 | 0 |

Six sampled bed cases and six sampled doctor cases had both insurers. The insurers gave differing values in three of those bed cases and four of those doctor cases. All seven differing-insurer cases remained unresolved. The sole paired win for each insurer comes from the same case, Shifa Hospitals, where both insurers give 130 and two public institutions also report 130. In the other resolved paired case the insurers both give 56 and the hospital publishes 55. The independent label evidence supports no insurer-wide accuracy winner. Publisher and recency are also confounded in this run because the main releases are 2025 for IFFCO and 2022 for SBI. [Malnad review](../review/initial/bed-r09-r16.json) · [Shifa review](../review/extension/bed-r40-r42.json) · [IFFCO disclosure](https://www.iffcotokio.co.in/health-insurance/network-hospital-quality-parameters) · [SBI disclosure](https://content.sbigeneral.in/uploads/f22596609234455e8b24df7859027ce6.pdf)

## Source selector and export decision

| Held-out method on the 7 supported bed cases | Exact top choice | Exact top choice when a matching candidate was available | Mean candidate Brier score |
| --- | ---: | ---: | ---: |
| XGBoost selector | 4 of 7 | 4 of 5 | 0.237 |
| Publisher fallback | 4 of 7 | 4 of 5 | 0.263 |
| Newest first | 1 of 7 | 1 of 5 | — |

The two cases with a supported third value count as misses for every method that can only choose an existing candidate. The bed model's Brier score improved relative to the publisher fallback but it tied on exact top choice and fell below the minimum of eight supported cases. Its model and experimental choices remain in iteration 02. A doctor model requires exact labeled examples. This run used the pooled fallback for both fields.

The observed bed agreement was **4 of 7** for FDR, **1 of 4** for IFFCO, and **1 of 5** for SBI. The pooled, smoothed order is FDR, IFFCO, then SBI. Its different denominators and small cohort mean the ordering is a practical fallback for these records. The dataset assigns the selected published values and dates, supplies direct review corrections where available, and leaves known unresolved reviews missing. It keeps the original counts alongside those decisions.

## Coverage

| Count | Original reported on a routable branch | Selected value | Increase |
| --- | ---: | ---: | ---: |
| Beds | 2,375 | 2,717 | 342 |
| Doctors | 3,457 | 3,707 | 250 |

The export preserves all 10,088 original rows, associated with 10,073 linked branch identities. It holds 499 branches from automatic routing and selected counts. The 347 selected external bed totals and 270 selected external doctor totals are historical observations. The bed release also uses eight supported manual cases including one earlier check, ten earlier description-based figures, and the externally reviewed Faridabad count. The 16 unresolved bed cases include the earlier Raigarh review. All 20 unresolved doctor reviews have blank selected counts. Existing estimates remain available in dedicated fields.

This is a selected-disagreement pilot. Counts of resolved and unresolved cases, branch holds, scope exclusions, and the paired comparison matter as much as the observed ranking. A later insurer comparison needs independently resolved cases with comparable, differing reports from the two insurers on the same branch. A contemporaneous regulator filing or a provider's dated whole-facility report is a stronger adjudication source when the document defines the count. [WHO desk review guidance](https://www.who.int/publications/i/item/9789240047389)
