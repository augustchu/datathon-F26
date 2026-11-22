# Direction 3: Verify the facts that would most improve a recommendation

**Why this is promising.** The project already has a service-evidence pipeline, branch/location holds, a Hindi verification prototype, and a moderation stack. The next useful step is to decide **which branch and which question deserve the next verification effort**. A missing dialysis claim at the only nearby candidate can matter more than a more accurate bed estimate at a hospital that cannot enter the shortlist. A small, measured verification pilot could improve recommendations and produce genuinely new training or evaluation labels. It also gives a clear answer to the competition's central question: why should someone trust this recommendation?

**Recommendation:** build an offline verification-priority table and compare it with random selection on a fixed budget. Start with documentary review and use the proposed Hindi call pilot for unresolved, high-impact claims. Keep a separate randomly sampled evaluation set to measure accuracy. [Hindi call pilot and mass verification runbook](../../non-ml/MASS_VERIFICATION.md#proposed-next-step-after-the-browser-review)

Review date: **21 November 2026, America/New_York**. [Brainstorm and selection](../BRAINSTORM.md) · [Project and prompt review](../CONTEXT_AND_HISTORY.md).

## Fit to the prompt and your direction

The original prompt asks for a service and a location, evidence for each important claim, and an explanation of both the chosen facility and the selection process. It explicitly warns about merged hospitals, contradictory locations and weak service claims. Your subsequent instructions added pre-deployment verification, phone numbers, call-confirmation feedback, newest-first moderation, offline preparation, and comparisons between confirmed, ML-assisted and interpretable estimates. This proposal extends those decisions without taking over your teammate's load-balancing work.

The [project summary](../../non-ml/PROJECT_SUMMARY.md) reports only **three independently corroborated branch–service pairs**, against 6,617 single-publisher pairs, 4,563 insufficient pairs and 10 conflicting pairs in the saved combined audit. Those are evidence classifications, not estimates of the true service prevalence. The [location audit](../../non-ml/LOCATION_AUDIT.md) held **839 additional branches**. The [mass-verification runbook](../../non-ml/MASS_VERIFICATION.md) describes a local prototype; a live campaign is not a completed result. The [ratings pilot](../../non-ml/PATIENT_REVIEW_PILOT.md) did not find a comparable rating set that could change the real rankings under the current rules.

These gaps point to an information-acquisition problem. More predictions cannot by themselves resolve a wrong branch, establish that a source is current, or provide the independent evidence needed to evaluate predictions on originally missing fields. Verification can resolve those specific uncertainties, while explicitly recording failures to resolve them.

## What the literature supports

This is a targeted literature review, not a systematic review. Searches covered active feature acquisition, cost-sensitive information acquisition, active testing, label non-response and facility-function verification, with recent work through the review date and a foundational evaluation paper. The sources below are original research, plus WHO's primary assessment guidance. None evaluates this exact Indian facility-directory pipeline; the transfer to our project is a proposed design inference.

### 1. Choose information according to the decision it could change

**Valancius, Lennon and Oliva (2024), “Acquisition Conditioned Oracle for Nongreedy Active Feature Acquisition,” ICML, PMLR 235:48957–48975.** The authors study selecting features under acquisition costs and propose a nonparametric approach that accounts for jointly useful acquisitions. Their benchmark results motivate considering combinations of questions rather than treating each field as independently useful. [Paper and proceedings](https://proceedings.mlr.press/v235/valancius24a.html)

**Application:** confirming both a branch address and its dialysis service may make a candidate usable, whereas confirming either alone may not. Group such questions into one review task. The paper's acquired features and oracle assumptions differ from fallible websites and staff responses, so its benchmark gains do not predict our gains. A hand-built decision-impact baseline should precede an acquisition model.

### 2. The best next question can vary by facility

**Guney et al. (2025), “Active feature acquisition via explainability-driven ranking,” ICML, PMLR 267:20748–20765.** This method uses instance-specific explanations to guide a learned feature-acquisition policy, rather than one global ordering of useful features. It reports improved predictive performance and acquisition efficiency on its evaluated datasets. [Paper and proceedings](https://proceedings.mlr.press/v267/guney25a.html)

**Application:** ask about a disputed location for one branch and a service subtype for another. A large bed-model attribution does not automatically mean that verifying beds improves the recommendation. We should test the actual change in service eligibility or ranking. The paper motivates case-specific acquisition; it does not justify building a decision transformer for our first pilot or interpreting SHAP values as clinical importance.

### 3. Unanswered requests can systematically bias the result

**Robinson, Tax, Mudd and Guy (2024), “Active learning with biased non-response to label requests,” Data Mining and Knowledge Discovery 38:2117–2140.** The authors show that non-response can degrade active learning and propose an expected-utility correction. Their experiments include e-commerce data, and some settings remain vulnerable after correction. [Published article](https://link.springer.com/article/10.1007/s10618-024-01026-x)

**Application:** a facility that answers a switchboard is not necessarily representative of facilities without working numbers or accessible websites. Record every attempted review, not just successful confirmations; report contactability and completion by region, operator and evidence class. Preserve “no answer” and “unknown.” Favoring easy-to-contact hospitals alone could systematically exclude poorly documented areas. Response models would be exploratory until enough local attempts exist; the paper does not eliminate non-response bias for us.

### 4. Acquiring training labels and evaluating a model are different jobs

**Kossen, Farquhar, Gal and Rainforth (2021), “Active Testing: Sample-Efficient Model Evaluation,” ICML, PMLR 139:5753–5763.** Active testing selects labels to estimate model performance efficiently and corrects selection bias; its acquisition objective differs from active learning for training. The demonstrations include image benchmarks rather than healthcare directories. [Paper and proceedings](https://proceedings.mlr.press/v139/kossen21a.html)

**Application:** do not report the unweighted accuracy of a high-priority verification queue as national accuracy. Initially use a separate probability-sampled audit cohort for evaluation, with its sample design and failed retrievals visible. A more advanced active-testing estimator is a later option if selection probabilities and its assumptions can be implemented correctly. Labels reserved for final evaluation must never be used to tune the model or the verification priority rule.

### 5. A facility designation is weaker than demonstrated functionality

**Gausman et al. (2023), “Validating indicators for monitoring availability and geographic distribution of emergency obstetric and newborn care (EmoNC) facilities,” PLOS ONE 18(9):e0287904.** This study triangulates facility records, health-system information and geography in Argentina, Ghana and India. In its Indian sample, three comprehensive-designated facilities, 23.1% of that category, evidenced all nine assessed functions. These are selected study settings, not a national performance rate. [Published study](https://doi.org/10.1371/journal.pone.0287904)

**Application:** ask service-specific questions with a date and branch identity instead of accepting a generic hospital label. The authors also discuss the role of caseload: not performing a function in the observation window does not always establish inability to perform it. Our schema should therefore separate “offers,” “performed recently,” “staff/resources present,” and “available now.” A keypad answer alone cannot establish all four.

**WHO Harmonized Health Facility Assessment (HHFA), guidance introduced in 2022–2023.** HHFA distinguishes service availability, readiness, quality, and management/finance, and uses interviews, observation and record review. [WHO introduction and guidance](https://www.who.int/data/data-collection-tools/harmonized-health-facility-assessment/introduction)

**Application:** preserve those evidence distinctions in the review form. The project can borrow the categories; it cannot call a short phone check an HHFA assessment or infer current slots from total beds.

## The proposed research question

**For a fixed amount of reviewer time, can decision-focused verification resolve more consequential errors and enable more well-supported service-and-location answers than random or uncertainty-only selection?**

“Consequential” needs an operational definition before the pilot. For this project it can mean resolving a mistaken branch, removing an unsupported top recommendation, or adding the first eligible candidate for a previously unanswered query. It does not mean a measured improvement in patient health outcomes.

A useful initial unit is a **branch–claim–question**, with fields for branch ID, requested service or numeric field, disputed value, exact source, review reason, expected effect on the frozen query set, reviewer minutes, outcome and evidence date. One review task may bundle several dependent claims at the same branch.

## A concrete first experiment

1. **Freeze a narrow query panel and the candidate pool.** Select two services and two regions from real covered data, including a region with sparse evidence, and include an explicit numeric-field cohort for whole-branch capacity or doctor count. Publish the service and numeric-field definitions, origin choices and selection rule before reviewing outcomes. Include queries that currently abstain. These examples are a local demonstration, not a representative sample of India.
2. **Construct priorities offline.** First identify branch/location conflicts, insufficient exact-service evidence, old or contradictory claims, and missing model fields. Replay the ranking under a small set of explicitly hypothetical resolutions. Measure whether an answer could change eligibility, remove a bad top result, or create a first supported result. Hypothetical resolutions never enter live recommendations.
3. **Use three transparent strategies.** Compare stratified random review, uncertainty/conflict-only review, and decision-impact review. Give each the same reviewer-minute budget and comparable proportions of numeric and service/location questions. For a manageable first wave, plan about 20 independent review tasks per arm; this is a feasibility budget, not a power calculation. Group shared branches and shared documents so an answer cannot inadvertently leak between arms. Randomly assign comparable candidate blocks, and record unique information acquired and overlap. Judge numeric acquisition by usable new labels per hour as well as prediction improvement; a bed label need not change the current ranking to be useful research evidence.
4. **Start with documents.** Review exact hospital branch pages, dated scope certificates and relevant government listings. For questions that require outreach, keep the result unresolved until an approved future pilot supplies it. The existing runbook governs live calls; this planning work sends none.
5. **Blind the adjudication where practical.** Reviewers should see the source and claim, but not a model prediction they are being asked to confirm. Independently double-review a subset and all conflicting dispositions. Save the original claim and append the decision with its basis.
6. **Hold back the evaluation cohort.** Independently sample an additional cohort of originally missing counts or unresolved service claims. Specify its strata and inclusion probabilities before lookup. Keep these labels out of development and record unresolved retrievals. If the budget cannot support a defensible audit, report only pilot yield and costs.
7. **Rebuild the existing checkpoints.** Feed approved evidence into the service verification and field-update tables, then regenerate lookup assets. Use the same recommendation backend for every comparison. Show a before/after trace with exact passages and remaining unknowns.

The first priority score should be inspectable: a decision-impact tier, followed by evidence gap and estimated review cost. With enough pilot data, a candidate research formula is estimated probability of obtaining a usable answer multiplied by expected reduction in decision error, divided by expected review minutes. Until those quantities have credible estimates, label them assumptions rather than calibrated probabilities. Keep exploration slots across geography and facility types to avoid selecting only easy urban cases.

## Evaluation and adoption criteria

| Measure | What to count | Interpretation |
| --- | --- | --- |
| Review yield | Independently adjudicated, usable branch-specific facts per reviewer hour | Primary efficiency outcome; disclose source/self-report class |
| Recommendation error | Wrong-branch or unsupported-service outputs among reviewed displayed outputs | Do not substitute absence of review for correctness |
| Answer coverage | Frozen queries with at least one evidence-eligible candidate | Report alongside error and abstention, never alone |
| Decision change | Removed unsupported first results; new supported answers; corrected locations | Explain each change with the underlying evidence |
| Contact/retrieval funnel | Selected, attempted, reached, branch-confirmed, answered, adjudicated | Reveals failure and non-response, rather than hiding it |
| Distribution of benefits | Results by service, region, operator and initial evidence tier | Small groups need counts and wide uncertainty, not strong fairness claims |
| New model labels | Originally missing numeric fields independently recovered per reviewer hour, separated into development and audit sets | Supports Direction 2 without contaminating its final test |

Proceed to a larger pilot only if decision-focused review improves useful facts or consequential resolutions per hour against the baselines, without a worse adjudicated recommendation-error rate or systematic loss of coverage in the prespecified strata. Report uncertainty at the branch/task level. If the small pilot cannot distinguish strategies, retain the simple queue and collect more evidence; do not describe an inconclusive difference as a win. If most checks remain unresolved, change the sources or verification method before scaling.

## Why select this over a mass campaign or another model sweep?

It makes the next unit of work purposeful. The existing mass-calling proposal explains how outreach might run; this proposal tests what is worth asking first. It can uncover source errors that every model otherwise learns, and it creates the independent missing-value labels the current evaluations lack. It also produces a compact competition demonstration: one query, a documented uncertainty, a justified verification choice, and the resulting change in the answer.

Keep two structures distinct: the user's newest-first moderation stack for incoming reports, and this offline research queue for planned acquisition. A recent report is reviewed promptly but is not automatically true. Reviewed evidence still carries its original evidence class. Capacity estimates remain resource context; source facts, self-reports, operational availability and patient experience retain separate meanings.

**First deliverable:** a versioned verification worklist, a reviewer form, a fixed query panel and a before/after report. No new neural policy or live calling infrastructure is necessary to test the central idea.
