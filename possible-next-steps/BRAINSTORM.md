# Brainstorm: where the project should go next

**The three most promising directions are (1) source-grounded recovery of missing fields, (2) missing-field models validated for the population that needs them, and (3) decision-focused verification.** They build on the work already completed and address different weaknesses in the current evidence. The objective is an explainable service-and-location recommendation, with useful estimates where justified and visible uncertainty everywhere else.

Review date: **21 November 2026, America/New_York**. Read the [context and history audit](CONTEXT_AND_HISTORY.md) for the original prompt, the 24 reviewed commits, and the distinction between committed results and ongoing local experiments. The three proposal folders contain the associated literature and detailed experiments.

## How the choices were made

The competition requires a clear explanation of why a facility was chosen and how the system decided, using the provided dataset, cited underlying facility text, uncertainty communication, a nontechnical workflow and Databricks Free Edition. Your later instructions put missing-column prediction at the center of your contribution, keep interpretable and flexible models comparable, and assign load balancing to a teammate.

I evaluated ideas against five considerations: direct contribution to those requirements; fit with your current scope; reuse of existing code and evidence; a feasible, measurable pilot; and whether the necessary data exist. These are editorial judgments, **not an organizer scoring rubric or quantitative forecasts of success**. A technically novel idea that needs unavailable data is less attractive now than a modest extension that can be evaluated honestly.

## Candidate directions

| # | Candidate and hypothesis | Why it could help | Main uncertainty or cost | Decision |
| --- | --- | --- | --- | --- |
| 1 | **Recover missing facts from descriptions, pages and scope documents.** Exact branch-specific extraction can fill more useful fields than the present capacity-only pass. | Adds source-supported values and directly citable service evidence; extends your recent description-recovery request. | The existing strict capacity pass yielded only 12 blank counts; broader recovery yield and review cost are unknown. | **Selected #1.** Test counts, equipment and explicit services, with attribution and abstention. |
| 2 | **Validate per-target imputation under real missingness and transfer.** A wide-field model may retain useful signal on originally missing facilities. | Turns the existing model comparisons into evidence that could justify estimates; preserves interpretable alternatives. | Observed-count tests are repeatedly used, source text can reveal labels, and independently recovered missing labels are scarce. | **Selected #2.** Fresh splits, source/geography tests, independent missing-label cohort and intervals. |
| 3 | **Prioritize the next verification question by its effect on recommendations.** Targeted work may resolve more consequential uncertainty per hour. | Improves exact service/location evidence and creates new development or evaluation labels. | Verification can fail; actively selected successes are not a representative accuracy audit. | **Selected #3.** Compare transparent policies, preserve a separate probability-sampled audit. |
| 4 | **Improve branch/entity matching.** Match a larger fraction of useful public records to exact VF branches. | Makes existing NQAS/NABH and government data usable without acquiring more rows. | Same-name hospitals, shared switchboards and merged source records create dangerous false matches. | A major component of #1, with its own match/nonmatch benchmark. |
| 5 | **Complete population-density and NFHS ablations.** Context may improve estimates where facility fields are sparse. | Notebook scaffolding and a teammate handoff already exist. | Density data are still needed; historical district context is not a facility fact, and NFHS has not consistently helped. | A bounded experiment within #2 after joins and data are ready. |
| 6 | **Add interpretable nonlinear models.** A GAM or explainable boosting model may improve fit while exposing feature effects. | Could bridge ridge and forests, matching the request for understandable models. | Another model family cannot fix selection bias or unreliable target labels. | Optional benchmark extension within #2 after the evaluation design is fixed. |
| 7 | **Build equity/access maps for verified services.** Distinguish poorly served places from places with poor data. | Fits the district-planner audience and NFHS/geography supplement; naturally visual. | Requires validated locations, suitable population denominators and honest service coverage. | Strong future extension; coordinate with the teammate's access/load work. |
| 8 | **Measure real service readiness and operational slots.** Service-specific staffed resources may support better availability decisions. | Far closer to congestion than total bed counts. | No comparable fresh service-slot feed exists; acquisition is substantial. | Defer operational claims until data arrive; #3 can test how to acquire them. |
| 9 | **Expand patient-experience ranking.** Comparable branch-level feedback may capture aspects distance misses. | Follows your interest in “best,” ratings and reviews. | Current pilot found incompatible scopes/periods and no comparable approved aggregate set. | Defer until suitable data exist; retain branch-specific review links. |
| 10 | **Call every facility before launch.** A mass campaign could refresh many service claims. | The Hindi IVR prototype and runbook provide a foundation. | Contactability, response bias, staffing, review effort and provider integration remain unmeasured. | Test targeted verification first; do not assume every call yields a verified fact. |
| 11 | **Add more public registries and scrape nationwide.** More sources might increase coverage. | Some regions and services are thinly represented. | More unlinked or duplicate records may add little decision value. | Acquire sources to resolve measured gaps from #1/#3, not to maximize row count. |
| 12 | **Train larger neural, multi-target or generative imputation models.** Shared patterns may improve sparse-field prediction. | Potential research upside once enough trustworthy labels exist. | Compute, interpretability, error propagation and weak evaluation can dominate any gain. | Low immediate priority; avoid cascaded estimated predictors by default. |

## Why these three are distinct and complementary

**Direction 1 asks whether the answer is already stated somewhere.** A hospital passage may explicitly state a whole-branch bed count or a service subtype. Recovery should preserve the passage, date, unit, scope and linkage decision. This improves factual coverage even if training error never changes. It addresses the prompt's citation requirement most directly. [Full proposal and literature](01-source-grounded-field-recovery/README.md)

**Direction 2 asks what can be estimated when reliable evidence is absent.** The latest exploratory masked, phone-excluded forest has MAE 112.25 on 467 observed-count facilities, compared with 156.29 for the median on that cohort. That is promising reconstruction evidence, but it says little by itself about the facilities with originally blank targets. The extension is a credible evaluation and uncertainty layer, followed by expansion to other fields when supported. [Full proposal and literature](02-validated-missing-field-models/README.md)

**Direction 3 asks which unresolved fact deserves new effort.** It can prioritize a branch identity, a service claim, an outdated resource count or another decisive gap. Its outcome is newly acquired evidence and measured review yield. Some labels are reserved for Direction 2's independent audit, while others can improve training or source coverage. It should never recycle the audit labels into tuning before evaluation ends. [Full proposal and literature](03-targeted-verification/README.md)

The combination is stronger than three unrelated features: recover available evidence, estimate where justified, then acquire missing information where it has the greatest measured value. The shared data contract retains **reported, extracted, reviewed, corroborated, estimated, conflicting and unknown** statuses. A reviewed quotation is still a source claim, and a staff answer is still a dated self-report.

## What must accompany any choice

The [latest dashboard](../dashboard/dashboard.py) now accepts real facility exports, loads saved capacity models and can export an estimate overlay. However, it still uses its own service matching and ranking logic rather than the prepared evidence/location gates in `non-ml`. Integrating those paths is essential delivery work, not a fourth competing research direction. Otherwise, better evidence and model handling may never reach the visible answer.

Likewise, run the final workflow inside the required Free Edition environment and retain run outputs, versions and dependencies. The project's saved local analyses do not establish an end-to-end Free Edition demonstration. Make query-time processing small by preserving the existing preparation checkpoints. Do not make an unverified GPU allocation or a new paid service a prerequisite for the initial pilot.

Keep a shared service-evidence gate across confirmed, ML-assisted and interpretable modes. Capacity estimates may change resource context; they must not establish dialysis availability, erase a location hold, or turn a district health indicator into a patient-specific diagnosis. Opt-in load balancing remains the teammate's work and needs compatible service-specific measurements before claiming reduced crowding.

## Suggested order of work

1. **Freeze the research contract.** Agree on field definitions, branch groups, evidence statuses, a small query panel and success measures. Record the current source and code versions. Reserve new evaluation labels before further tuning.
2. **Run Direction 1's diagnostic extraction pilot.** Measure new correct facts, branch errors and reviewer time across a few fields. Preserve rule-based recovery as the baseline.
3. **Start Direction 3's acquisition pilot in parallel.** Build the worklist and acquire missing evidence. Keep planned acquisition separate from the newest-first incoming-report moderation stack. Begin with documents; future outreach follows its existing runbook.
4. **Run Direction 2 on the improved evaluation design.** Compare a compact model set, missingness patterns and transfer tests. Only then test optional density/NFHS or new model families. Freeze predictions before opening independently recovered test labels.
5. **Demonstrate one coherent user journey.** Use the shared backend in Free Edition: request service and place, inspect cited candidates, compare modes and uncertainty, show a documented exclusion or abstention, and demonstrate how reviewed evidence changes an answer.

These are stages with decision gates, not a promise that all pilots fit into one remaining competition session. If time is very short, choose a small source-recovery demonstration plus a clear limitations report and a verified end-to-end run. If a model does not improve independently evaluated performance, keep the confirmed recommendation and report the negative result.

## What would change this ranking?

- A reliable service-specific operational feed would raise readiness and load-balancing research substantially.
- An authorized, comparable branch-level patient-experience export would make rating-based ranking testable.
- Near-zero audited extraction yield would lower Direction 1's priority and favor independent verification.
- Poor independent missing-label accuracy would limit Direction 2 to broad exploratory context or verification leads.
- A successful, repeatable Free Edition run using one backend would remove the largest cross-cutting delivery uncertainty.

The ambition is not a fully populated spreadsheet at any cost. It is a better-supported answer whose improvements can be traced to real evidence and measured experiments.
