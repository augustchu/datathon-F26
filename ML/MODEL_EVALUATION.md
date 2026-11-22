# Missing bed-count model evaluation

**Decision:** take the facilities-only neural network forward for validation. It had lower mean absolute error than ridge, random forest and the NFHS-enriched neural network in each of three refits on the same facility split. The existing results support choosing the next model to validate. Estimates for genuinely missing bed counts still need verification.

## Shared test

We used the November 21, 2026 Virtue Foundation snapshot and the saved capacity audit for [notebook 09](../non-ml/notebooks/09_count_model_comparison.ipynb). The split contained 1,498 training facilities, 489 calibration facilities and 522 test facilities with reported bed counts. The bed count was withheld from each model at prediction time. Every test score uses those same 522 audited counts. For the neural networks, a branch with multiple equivalent reported labels uses its first matching saved FDR row. This keeps each facility at one vote.

Predictions were put on the same scale as the ridge and random forest outputs by rounding to whole nonnegative counts. There were 54 test facilities with 500 beds or more.

## Metrics and model choice

The neural networks train with Huber loss on `log1p(beds)`. The logarithm compresses the long tail of hospital sizes. Huber loss reduces the influence of large training residuals. Validation loss selects the saved training checkpoint. After converting the predictions back to beds, we use these common regression metrics across every model on the same test rows.

| Metric | How to read it |
| --- | --- |
| Mean absolute error (MAE) | The average absolute miss in beds across the entire test set. This is the main comparison metric. An MAE of 122 means predictions missed by about 122 beds on average. |
| Median and 90th-percentile absolute error | The middle error and the error at the 90th percentile. These help distinguish a typical miss from the larger misses that drive up the mean. |
| Root mean squared error (RMSE) | A measure in beds that gives large misses more weight. Use it to see whether improving average error came with worse outliers. |
| R² | A unitless score against a constant prediction equal to the average test label. One is perfect, zero has the same squared error as that constant, and negative scores have more squared error. |
| MAE and signed error for hospitals with 500+ beds | MAE tells us the size of large-hospital misses. Average predicted minus reported beds shows whether those misses tend to be underestimates or overestimates. |

Compare MAE with the simple **training median** and the other models. An improvement on held-out reported counts is evidence that the model learns useful patterns. An error that is large relative to a facility's size limits its usefulness as an individual estimate. The aggregate scores do not measure accuracy on facilities whose bed counts were missing in the source.

For the density experiments, compare the **all** cohort from facilities only, facilities plus density, and facilities plus density and NFHS. Match both printed fingerprints first. Lower MAE after adding density is evidence it helped on that cohort. RMSE and density coverage show whether gains come with larger misses or depend on where the density data matches. These notebooks use their own shared group split. The 522-facility results below describe the earlier comparison.

## Saved GitHub models

The original [GitHub commit](https://github.com/augustchu/datathon-F26/commit/6ce3f1a2019f7cf6c6a6f3d2b188a6bbc7495253) contains two fitted preprocessors and saved networks. A [follow-up commit](https://github.com/augustchu/datathon-F26/commit/ae25e603a20e8f7b35a106f3bb3af074d5363933) updated both sets of weights during the audit. We scored both revisions with their published preprocessors. Both preprocessors record 2,013 training rows, consistent with the notebook's 80/10/10 row split. The exported artifacts do not identify the training facilities. If the same 2,517 labeled source rows were used, the 504 non-training rows could not cover all 522 facilities in our test. Training overlap makes these artifact scores descriptive.

| Saved model | Initial MAE, all 522 | Updated MAE, all 522 | Updated MAE, 500+ beds |
| --- | ---: | ---: | ---: |
| Facilities-only neural network | 108.8 | 111.4 | 583.7 |
| NFHS-enriched neural network | 112.9 | 111.5 | 577.6 |

## Facility-held comparison

We refit both networks using their notebook architectures, feature preparation, log-count target and Huber loss. Preprocessing and weights were fitted on the 1,498 training facilities. Early stopping used the 489 calibration facilities. The test rows were reserved for scoring. The NFHS join followed the notebook's majority district/state mapping per PIN and matched 315 of the 522 test facilities. With tied PIN mappings, it retained the first district/state after the count sort.

| Model | Test MAE (beds) | MAE on facilities with 500+ beds |
| --- | ---: | ---: |
| Training median | 150.6 | 853.3 |
| Log-ridge regression | 141.9 | 834.0 |
| Log-random forest | 141.4 | 817.5 |
| Facilities-only neural network, refit | **120.3–125.1** | **575.7–608.7** |
| NFHS-enriched neural network, refit | 128.3–146.2 | 660.6–678.8 |

The ranges show results across seeds 42, 104 and 2026. They are not prediction intervals. The facilities-only network averaged **121.9 beds MAE** across the three refits, about **14% below random forest**. Its MAE also beat the enriched network on the 315 NFHS-matched facilities with each seed. The random forest and ridge differed by about half a bed in MAE on this test.

| Refit seed | Facilities-only MAE | NFHS-enriched MAE |
| --- | ---: | ---: |
| 42 | 125.06 | 146.24 |
| 104 | 120.27 | 128.26 |
| 2026 | 120.33 | 128.63 |

## Why choose the facilities-only model

- It won on overall MAE in all three refits. Adding district indicators increased error on this test, even among branches with an NFHS match.
- The facilities-only model also improved errors for larger hospitals. It still underestimated the 500+ bed group by about **553 to 599 beds on average** across refits. Those point estimates are too uncertain to present as verified bed counts.
- The test hides known reported counts. Facilities missing capacity in the source can have different reporting patterns. The analysis did not measure predictive coverage or calibrated intervals on that population. The legacy network also used some predictors outside the later post-`countries` feature boundary, and its input shortlist was designed using exploratory results from this dataset.

Keep the base network as the candidate for a comparison on newly verified counts from facilities with missing labels. Include rural, small and large branches in that check. Preserve the reported labels and the uncertainty information separately from the model estimates.

## Facilities-only control for the density experiment

The [facilities-only notebook](models%20with%20population%20density/Capacity%20with%20Facility%20Data%20Only.ipynb) uses the same trainer and group assignments as the density variants. It reads the facility snapshot directly. All three notebooks keep the same eligible rows before training. The facilities-only control omits both the density value and its missingness flag.

On the frozen FDR snapshot, one CPU run at seed 42 used 1,510 training, 503 validation and 504 test rows. There were 503 distinct test facility groups. These are the results for this experiment's holdout.

| Model | MAE (beds) | RMSE (beds) | R² |
| --- | ---: | ---: | ---: |
| Training median | 151.11 | 326.38 | −0.08 |
| Facilities-only MLP | 3,042.13 | 65,431.29 | −43,480.24 |

The MLP's median absolute error was 47 beds and its 90th-percentile error was 302.1 beds. One prediction of about 1.47 million beds accounted for 95.8% of its total absolute error. This is a serious failure for automated imputation. The full test score includes that row. The earlier 522-facility comparison showed useful signal in the same architecture on a different split. Together the results show why we track both typical error and extreme misses. A predeclared training-side rule for handling extreme inputs would need evaluation on a fresh holdout.

### Diagnosing and replaying the outlier

The affected row had 109 reported beds. Its source listed a fractional social-media presence count of 79.53, while the training counts ranged from 0 to 9. The original standard scaler turned 79.53 into an input of +42.12. The missing-procedure indicator, seen on only two training rows, separately became −27.46. The network predicted 14.20 on the log scale, which became 1,469,024 beds after the inverse transform and rounding.

The shared trainer now fits median imputation and a [uniform quantile transform](https://scikit-learn.org/1.7/modules/generated/sklearn.preprocessing.QuantileTransformer.html) on the training rows. Numeric inputs are bounded between 0 and 1, and values beyond the fitted range map to an endpoint. Binary presence and missingness flags stay at 0 or 1. On the affected row, the two inputs became 1 and 0. The fitted input transform is part of the saved preprocessor.

The facilities-only network was retrained on CPU with the same seed, 1,510 training rows and 503 validation rows. The test fingerprint and all 504 test labels matched the original run. The affected row earned a new prediction of 11 beds, an error of 98 beds.

| Model on the same 504 test rows | MAE (beds) | Median absolute error (beds) | 90th-percentile absolute error (beds) | RMSE (beds) | R² |
| --- | ---: | ---: | ---: | ---: | ---: |
| Training median | 151.11 | 66.5 | 400.0 | 326.38 | −0.08 |
| MLP, original preprocessing | 3,042.13 | 47.0 | 302.1 | 65,431.29 | −43,480.24 |
| MLP, bounded preprocessing | 126.48 | 45.5 | 279.4 | 284.89 | 0.18 |

This is a retrospective rerun after inspecting a test failure. The result shows that the specific extrapolation was removed and that error improved on this cohort. Generalization still needs an independent holdout. The density data has yet to be supplied. Rerun the density variants with the same shared preprocessing when it is available.

## XGBoost forests with explicit missing predictors

The [XGBoost notebook](models%20with%20population%20density/Capacity%20with%20XGBoost%20Forests.ipynb) uses the November 21, 2026 facility snapshot and the saved facility partitions from notebook 03. It applies the location and count audits before fitting, scoring, or estimating missing capacity. The location audit excluded 895 source rows, including 289 with a reported capacity. Unlinked rows, malformed facility or operator categories, and unusable capacity labels were also excluded. Facilities with missing predictors remained eligible. The retained labeled branches numbered 1,311 for training, 441 for calibration, and 467 for testing. These results use a different evaluation cohort from the earlier MLP comparisons.

The trainer compares a standalone XGBoost random forest with 24 sequential boosting rounds of eight trees each. It uses reported doctor counts, core facility fields, and optional metadata and geographic inputs. Each categorical field has a `missing` level. Each numeric field keeps its missing value and gets a binary missing indicator. Calibration MAE favored the boosted forest with 18 core inputs, including indicators. It scored 118.12 beds, compared with 119.89 for the boosted forest with the broader 36-input facility bundle. Adding 15 NFHS indicators and their missing flags to the broader bundle yielded 120.56 beds on the same calibration facilities. Population density remains configurable and was unavailable for these runs. [XGBoost random-forest training guide](https://xgboost.readthedocs.io/en/stable/tutorials/rf.html)

| Model on the same 467 test facilities | MAE (beds) | Median absolute error (beds) | 90th-percentile absolute error (beds) | RMSE (beds) | R² |
| --- | ---: | ---: | ---: | ---: | ---: |
| Training median | 156.29 | 70 | 400.0 | 353.73 | −0.079 |
| XGBoost random forest | 128.16 | 38 | 353.4 | 295.17 | 0.249 |
| XGBoost boosted forest | 123.00 | 39 | 343.0 | 286.38 | 0.293 |

The boosted forest reduced MAE by 21.3% against the median. It underpredicted by 71.6 beds on average and its largest miss was 3,459 beds. It returned estimates for 6,937 branches with missing capacity. Those branches still need independent verification to measure imputation accuracy where the source did not report a count.

### Do explicit numeric missingness flags help?

Refit the boosted forest twice on the same 1,311 eligible training facilities with seed 42 and the original tree settings. The 441 calibration facilities and the 467 test facilities matched the previous run. The location audit still excluded the same 895 source rows. The test IDs and observed counts matched fingerprint `b14d649516340f1f2d872dd4582f6026cb805a35311cad3f4a8966240ae2c401`.

The second fit dropped the eight numeric missingness flags from the core feature set. Both fits used the same numeric values, including native `NaN`, and the same categorical `missing` levels. Calibration MAE was 118.12 beds with flags and 117.52 without.

| Boosted forest on the same 467 test facilities | MAE (beds) | Median absolute error (beds) | 90th-percentile absolute error (beds) | RMSE (beds) |
| --- | ---: | ---: | ---: | ---: |
| With numeric flags | 123.00 | 39 | 343.0 | 286.38 |
| Without numeric flags | 123.14 | 38 | 328.2 | 287.01 |

The paired test MAE difference was −0.15 beds with flags relative to without them. A 95% percentile interval from 20,000 paired facility resamples was −1.30 to +1.01 beds for these fixed fits. Across ten additional paired runs with seeds 43–52, flags lowered test MAE in three runs and increased it by an average of 0.34 beds. The flags showed no consistent predictive benefit on this cohort. These scores use facilities with observed capacity.

### Full source-width comparison

The source has 51 columns. The exploratory fit used all 50 predictors after setting aside the target `capacity`. The location and count exclusions, the 467-facility test fingerprint, and the forest settings matched the previous runs. Numeric and date fields retained missing values. We one-hot encoded recurring categories and represented free text and other strings with lengths, exact-match frequencies, and TF-IDF tokens. We fitted all vocabularies and frequencies on the 1,311 training facilities. Expanded input columns totaled 996, or 1,033 with 37 extra missingness flags.

| Source inputs | Forest | Extra flags | Calibration MAE | Test MAE | Test RMSE |
| --- | --- | --- | ---: | ---: | ---: |
| Core facility fields | Boosted | No | 117.52 | 123.14 | 287.01 |
| All 50 | Standalone | No | 108.32 | 115.99 | 271.60 |
| All 50 | Standalone | Yes | 108.03 | 116.34 | 271.62 |
| All 50 | Boosted | No | 105.55 | 111.78 | 268.27 |
| All 50 | Boosted | Yes | 105.70 | 112.46 | 268.65 |
| All 50 with detected bed-count phrases masked | Boosted | No | 104.54 | 112.12 | 267.73 |
| All 50 with detected bed-count phrases masked | Boosted | Yes | 105.11 | 112.11 | 266.00 |
| 48 excluding phone fields | Boosted | No | 105.88 | 112.53 | 270.05 |
| 48 excluding phone fields, with detected bed-count phrases masked | Boosted | No | 104.94 | 112.25 | 267.85 |

The full-width boosted forest without flags reduced MAE by 11.37 beds relative to the core model. A paired 95% interval put the reduction at 4.91 to 17.90 beds on this cohort. With detected bed-count phrases masked, the reduction was 11.03 beds. Numeric flags increased MAE by 0.68 beds at seed 42 and lowered it by 0.37 beds on average across ten additional seeds.

The text scan found apparent bed-count wording for 360 test facilities and a number matching the recorded capacity for 332. It found wording for 205 of 6,937 facilities with a missing structured count. Among the 107 test facilities without a detected mention, the core boosted forest had MAE of 148.20 and the masked 48-column boosted forest had MAE of 140.66. The scan can miss phrases or pick up counts for individual units. Some source content identifiers also recur across split boundaries. These comparisons describe the existing holdout of reported counts.

### Using counts recovered from descriptions

The description pass screened for a single exact whole-facility count and rejected unit-level, historical, aggregate and ambiguous claims. Manual review retained 12 previously blank branch counts, with source excerpts and record IDs. Among 183 branches with both a description result and a structured count, 180 agreed. The three discrepancies kept their structured counts. This is agreement between fields in the same dataset. Nine recovered labels belonged to the existing training partition, bringing the training set from 1,311 to 1,320. The other recovered labels are available for source review. The original 441 calibration and 467 test labels stayed fixed.

We compared 13 model configurations with and without the new examples at seed 42. Every comparison kept the label-source description out of its predictors. The wider forests used the 47 remaining source fields after also removing both phone fields, with detected bed-count phrases masked in other text. The workbook records paired MAE, RMSE and 90th-percentile error for all configurations.

| Selected model configurations | Reported-only MAE | Augmented MAE |
| --- | ---: | ---: |
| Log-ridge regression | 140.04 | 140.05 |
| Scikit-learn log-random forest | 142.32 | 142.56 |
| Facilities-only MLP | 131.34 | 131.19 |
| NFHS MLP | 133.70 | 128.57 |
| Original post-countries random forest | 124.37 | 124.31 |
| XGBoost boosted forest with 47 fields and no extra flags | 112.80 | 112.35 |

Most changes in MAE were smaller than one bed. The NFHS MLP improved at seed 42 and worsened at both additional seeds, from 130.55 to 131.41 at seed 104 and from 131.00 to 132.53 at seed 2026. The extraction recovered a small set of usable source claims. This retraining did not establish a consistent accuracy gain.

### Testing whether a facility lists a phone number

The `phone_listed` candidate has two levels, `listed` and `not_listed`. It uses the same listing definition as the source coverage audit across `phone_numbers` and `officialPhone`. Empty arrays and blank markers count as `not_listed`. The model receives only the encoded category. Verifying that someone answers the line requires a separate call.

We appended the indicator to the same 47-field boosted forest with bed-count wording masked and no extra numeric missingness flags. Every pair kept the other inputs, tree settings, audit exclusions, and train, calibration and test facility IDs fixed. Of the 467 test facilities, 455 listed a phone and 12 did not. All nine added description labels came from facilities that listed a phone.

| Training labels | Seed | Test MAE without status | Test MAE with status |
| --- | ---: | ---: | ---: |
| Reported and description-derived | 42 | 112.35 | 112.34 |
| Reported and description-derived | 104 | 111.40 | 112.18 |
| Reported and description-derived | 2026 | 110.90 | 111.33 |
| Reported only | 42 | 112.80 | 111.87 |

At seed 42 with the recovered labels, the paired MAE change with the flag was −0.01 beds, with a 95% paired bootstrap interval of −1.58 to +1.64 beds. Calibration MAE and the 90th-percentile test error improved at that seed, while test RMSE increased. The other augmented runs had higher test MAE with the flag. The reported-only run also had a paired 95% interval that included zero. The results support keeping the category as an audit and experimental field.
