# Direction 2: Missing-field models that earn the right to fill a value

**Why this is promising.** The project has already found predictive signal in incomplete facility records. The next valuable advance is establishing which estimates remain useful for the facilities whose values are actually missing, then expanding that validated approach to other fields. A fresh, leakage-resistant evaluation, interpretable baselines, and honest prediction intervals would turn exploratory model scores into a defensible data product. This directly supports the requested confirmed/ML-assisted/regression-assisted comparison and the competition's requirement to explain uncertainty. It also gives the load-balancing teammate a clearly labeled resource estimate with known limits, rather than an unexplained number.

**Recommendation:** prioritize capacity and doctor counts; validate one target at a time using originally observed predictors. Publish estimates only where the evaluation supports them. Keep uncertain service predictions as verification leads. Estimated beds never establish treatment availability.

Review date: **21 November 2026, America/New_York**. This is a proposed research direction, not a report of newly completed experiments. Links to project results refer to the reviewed working-tree snapshot; some newer experiments were uncommitted during this review.

## Fit with the prompt and the user's direction

The datathon asks where someone should obtain a named service from a given location, why the facility can be trusted, and how it was selected. It requires the supplied facility data, cited underlying text, uncertainty communication, a nontechnical workflow, and execution on Databricks Free Edition. Filling every blank is therefore an intermediate objective: the end product must improve the usefulness of an explainable recommendation.

The user's later instructions specifically prioritize modeling missing columns, using more available information, comparing interpretable and flexible models, excluding flagged facilities, avoiding cascaded estimation errors, and tracking experiments in W&B. Load balancing is assigned to a teammate. This direction follows that division of work while preserving the three-mode design described in the [project summary](../../non-ml/PROJECT_SUMMARY.md). Regression is itself a learned statistical model; the useful distinction is between transparent statistical baselines and more flexible ML methods, not between learning and no learning.

## What the existing work establishes—and leaves open

The [model evaluation](../../ML/MODEL_EVALUATION.md) reports these useful findings:

| Evidence reviewed | Implication for the next experiment |
| --- | --- |
| On 467 audited observed-count test branches, the core boosted forest without extra numeric flags has MAE 123.14 beds; the masked, phone-excluded 48-field version has MAE 112.25; the median baseline has MAE 156.29. | Wider inputs contain useful signal in this cohort. Preserve that candidate, but do not treat this as measured missing-count accuracy. |
| Raw text contains a number matching recorded capacity for 332 of those 467 test facilities; some source content IDs cross split boundaries. | Separate direct fact recovery from inference, inspect duplicated source material, and evaluate branches with no explicit target mention. Masking detected phrases is a useful stress test, not proof that all leakage is removed. |
| Extra numeric missingness flags show no consistent improvement across reruns. | Keep a paired ablation; there is no reason to expand flag complexity solely because the technique is common. |
| A neural network once predicted about 1.47 million beds; bounded preprocessing removed that particular extrapolation on a retrospective rerun. | Audit extreme inputs and output behavior before release, and test the revised design on unseen facilities. |
| Twelve missing counts were recovered from descriptions, nine usable for the existing training partition; retraining gains were inconsistent. | More independently checked labels are currently more informative than repeatedly tuning on the same test set. Same-record text agreement is not independent validation. |

The earlier 522-facility comparison, the 504-row density-control comparison, and the 467-branch forest comparison answer different cohort-specific questions. Their MAEs cannot establish a cross-cohort winner. Density benefits remain unmeasured without the data; NFHS augmentation has not demonstrated a consistent benefit. The current calibration set also served model selection in some experiments, so the next interval experiment needs a genuinely separate calibration allocation.

## Academic literature reviewed

This targeted review searched primary research for missing-value prediction, missingness shift, leakage, spatial validation, and conformal prediction. It emphasizes 2023–2026 publications and one relevant theoretical foundation. It is not an exhaustive systematic review, and none of these papers validates the Virtue Foundation dataset itself.

### 1. Predicting with incomplete inputs is different from reconstructing every input

Le Morvan, Josse, Scornet and Varoquaux, **“What's a good imputation to predict with missing values?”**, NeurIPS 2021, show under their assumptions that flexible impute-then-regress procedures can be asymptotically optimal without perfect reconstruction of missing predictors. The result concerns prediction from incomplete covariates, not a guarantee that an imputed facility fact is correct. **Design consequence:** retain simple predictor handling and native missing-value trees as serious baselines; do not require a complete synthetic facility record before estimating one target. Finite, noisy datasets still need empirical testing. [Primary paper](https://proceedings.neurips.cc/paper/2021/file/5fe8fdc79ce292c39c5f209d734b7206-Paper.pdf)

### 2. More elaborate imputation may buy little downstream improvement

Le Morvan and Varoquaux, **“Imputation for prediction: beware of diminishing returns,”** ICLR 2025, compare imputation and downstream prediction across 19 datasets. Better predictor reconstruction often produces limited gains with expressive models; missingness indicators can help in their experiments. **Design consequence:** measure both target-value error and downstream recommendation behavior, with an explicit compute budget. Their result does not mean our bed-count errors are unimportant: bed count is the target here, while their central question concerns imputing inputs to another predictor. Nor does their average indicator benefit override this project's negative ablation. [Primary paper](https://proceedings.iclr.cc/paper_files/paper/2025/file/65e837e76a5308df3d5544aab6196e21-Paper-Conference.pdf)

### 3. Reporting patterns can change between training and use

Rockenschaub and colleagues, **“Robust prediction under missingness shifts,”** 2024 arXiv preprint, distinguish ignorable from non-ignorable changes in missingness. A predictor can perform well under one reporting process and fail when that process changes; robustness differs by method and shift. **Design consequence:** compare source groups and missingness profiles, and stress-test the loss of predictor blocks. A missing hospital field might reflect website quality or collection practices rather than hospital size. These are hypotheses to investigate, not proof of a particular missingness mechanism. The paper addresses missing covariates; selection into the observed-target cohort is an additional problem in our project. [Primary manuscript](https://arxiv.org/html/2406.16484v1)

### 4. A holdout only helps if it represents the claim being made

Kapoor and Narayanan, **“Leakage and the reproducibility crisis in machine-learning-based science,”** *Patterns* 2023, document leakage across scientific applications and propose model information sheets. Their framework asks about clean separation, legitimate features, and whether evaluation represents the target population. **Design consequence:** version branch/source groups, text-masking rules, target provenance, and preprocessing fits. A masked observed-count test is evidence about reconstruction among reporting facilities; a claim about filling originally blank fields needs a separately recovered-label cohort. This is a methodological warning, not evidence that every existing gain is spurious. [Primary article](https://pmc.ncbi.nlm.nih.gov/articles/PMC10499856/)

### 5. Geographic validation should match the deployment question

Linnenbrink, Milà, Ludwig and Meyer, **“kNNDM CV: k-fold nearest-neighbour distance matching cross-validation for map accuracy estimation,”** *Geoscientific Model Development* 2024, match validation-to-training distances to prediction-to-training distances. Their simulations show that random splits can be optimistic with clustered samples, while spatial splits can also be overly pessimistic. **Design consequence:** report within-coverage imputation and transfer to held-out regions separately; inspect how far originally missing facilities lie from training support. This study concerns geographic mapping, so its lesson informs our evaluation design rather than mandating its algorithm for facility counts. [Primary article](https://gmd.copernicus.org/articles/17/5897/2024/gmd-17-5897-2024.html)

### 6. Average interval coverage can hide failure in sparse records

Zaffran, Dieuleveut, Josse and Romano, **“Conformal Prediction with Missing Values,”** ICML 2023, show why marginal coverage on imputed data can coexist with undercoverage for particular missingness patterns. They develop a missing-data augmentation approach. **Design consequence:** show coverage and interval width by common missingness patterns, geography, and facility size, as sample sizes permit. Ordinary split conformal is a useful starting point under exchangeability, but a 90% marginal target is not a 90% probability statement for each individual hospital or a guarantee under arbitrary dataset shift. [Primary paper](https://proceedings.mlr.press/v202/zaffran23a.html)

### 7. Recent work offers stronger mask-aware methods, with conditions

Fan, Park, Vo and Brunel, **“Mask-Conditional Conformal Prediction: Valid Uncertainty For All Missing Data Mechanisms,”** AISTATS 2026, propose distributional imputation followed by mask-specific reweighting and correction. Their formal guarantees depend on the specified support and weighting conditions; estimated weights and scarce patterns require scrutiny. **Design consequence:** consider this only after a simple interval baseline exposes substantial pattern-specific undercoverage. The paper is not a license to claim automatic coverage for our selected, noisy missing-target population. Its added complexity must earn a measurable benefit. [Proceedings and paper](https://proceedings.mlr.press/v300/fan26a.html)

## Proposed experiment

### Establish the target and label source

Start with **whole-branch reported bed capacity**, then doctor count with a clearly stated definition. Separate licensed beds, operational beds, staffed beds, and unit-specific beds rather than combining them as interchangeable labels. Freeze a source date and retain conflicting or historical claims in the audit. Source-recovered counts should carry their own provenance; predictions should not replace reported values.

Create one target table per field. Train on accepted observed or independently reviewed labels. Predict from originally observed fields only; preserve unknown values and their provenance. In particular, do not estimate doctors and then feed those estimates into the bed model by default. Median-filled inputs used internally by a regression are preprocessing artifacts, not newly confirmed database facts. Binary service or equipment models need reviewed positives and negatives; absence of a statement is not a negative label.

### Build three complementary evaluations

1. **Fresh grouped development/evaluation design.** Group duplicate branch records and shared source documents before partitioning. Inspect unusually large connected groups. Use training folds for feature/model selection, a separate calibration partition for intervals, and a locked test. Fit vocabularies, imputers, scalers, and frequency encodings on training data alone. Previously inspected tests become development evidence. Reshuffling already explored rows does not make them independent confirmatory data; call this a revised internal evaluation. Reserve stronger confirmation for newly acquired, independently reviewed labels whose predictions and protocol were frozen in advance.
2. **Geographic and source transfer.** Hold out selected districts or states and relevant source families as distinct stress tests. Measure support and missingness differences. Do not interpret source-held or geographically distant tests as the expected error for every deployment facility.
3. **Originally missing-label cohort.** Freeze predictions first, then independently recover counts for a stratified sample of branches whose target was blank. Attempt approximately 100–150 branch verifications initially, spanning geography, facility type, predicted size, and sparse/dense records. This is a feasibility pilot, not a nationally representative accuracy guarantee. Log every attempted case, verification failure, date, and source. Keep these labels out of training and calibration until evaluation is complete. Report verification success and selection bias alongside model error.

### Compare a small, informative model set

Use the same eligible branches and target definitions for the training median, log-ridge, an interpretable count model if the count assumptions fit, random forest, the existing boosted forest, and the bounded-input MLP. Include a phone-excluded wide-field model and a target-text-masked version. Keep direct extraction as its own method. Test density and NFHS only after validated joins, with matched-cohort ablations. Narrow scope to capacity first if time is short.

Log data/split fingerprints, label provenance, exclusions, seeds, preprocessing, feature definitions, predictions, and metrics in the existing W&B artifact workflow. Produce saved tables before queries run; inference and provenance lookup should not trigger training. Verify the complete pilot on Free Edition with recorded runtime and dependencies, since local success alone does not meet the submission requirement.

Include a concrete interpretation report: ridge coefficient directions and magnitudes on the declared transformed scale, their stability across training folds, grouped permutation importance for the forests, and each example's observed inputs, missing inputs and applicability limits. If a nonlinear additive model is tested, show its learned feature-effect curves. These describe model behavior, not causal effects or proof of a facility's capability. Present one inspectable prediction trace from each comparison mode; keep source passages beside factual claims.

## Evaluation and release decision

Report MAE, median error, 90th-percentile error, RMSE, signed error, and tail errors in original units. Add paired uncertainty estimates using the independent sampling/grouping unit. Report subgroup sample sizes rather than confident conclusions for tiny groups. For intervals, report nominal and empirical coverage, interval width, and the fraction of facilities receiving an estimate. For abstention, plot error against retained coverage; selecting only easy facilities can improve error while making the system less useful.

The following are **proposed engineering gates**, not published clinical standards. Freeze them before the new evaluation:

- A complex model must beat the training-median baseline and show a practically useful advantage over the interpretable candidate on the target deployment cohort. A 10% relative MAE reduction against the median is a reasonable starting proposal; set acceptable tail error separately before results are opened.
- A nominal 90% interval requires coverage estimates and confidence intervals on held-out data, including the recovered-label cohort. Material undercoverage, unsupported regions, or excessively broad intervals should trigger an unknown/needs-verification outcome, not a precise displayed value.
- A model-enabled recommendation replay must preserve the service-evidence and location gates. Measure ranking changes and sensitivity across the predicted count interval. If a decision flips across plausible values, expose that instability or retain the confirmed ranking.
- If independent label recovery is insufficient, release an exploratory model comparison with explicit limits. Do not claim validated imputation accuracy for all missing facilities.

The first deliverable should be a reproducible benchmark notebook, a locked evaluation manifest, and an estimate table containing value, interval, provenance, model version, applicability flag, and abstention reason. A successful result would justify expanding to another field. A negative result would still improve the submission by identifying where documentary recovery or verification is more useful than prediction.
