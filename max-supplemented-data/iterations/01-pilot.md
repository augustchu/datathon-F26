# Iteration 01 · 36-case pilot

This is the replay of the initial frozen sample. It uses only reviews R01–R36, and its model and dataset are stored under `local/iteration-01/`. The SHA-256 of the exact sample is `a5eaffdc73364065e2d6e87a1146eae2620925917e54f14c22428cbad1b791cc`. [Initial review records](../review/initial/)

| Field | Sampled | Supported exact value | At least one existing candidate matched |
| --- | ---: | ---: | ---: |
| Whole-facility bed capacity | 16 | 5 | 4 |
| Total doctors | 14 | 0 | 0 |
| Hospital website | 5 | 5 | 5 |
| Establishment year | 1 | 0 | 0 |

On the five supported bed cases, leave-one-facility-out evaluation selected the supported value in **4 of 5** cases for XGBoost, **4 of 5** for the publisher baseline and **0 of 5** for newest first. XGBoost's mean candidate Brier score was **0.246**, versus **0.144** for the publisher baseline. Brier score is the mean squared error of the candidate scores against the 0/1 labels, averaged within case first. Lower is better. The count of resolved cases and the tied top-choice result kept the publisher fallback in use.

The two insurers both appeared in four bed cases. One of those had a supported reference value and both insurers gave the same different number. Two of the four paired beds cases had different insurer reports and both remained unresolved. None of the four paired doctor cases yielded a supported count. The sample could show whether an insurer number agreed with a public source on some branches. Its paired subset supplied no labels for learning to choose between differing insurer reports.

The pilot release had **2,721** nonmissing bed counts and **3,713** nonmissing doctor counts across the 10,073 linked branch identities. All **10,088** source rows are preserved in the export. These figures are from the same inputs and data-release rules as the extension. The larger review sample changed which unresolved values remained blank.
