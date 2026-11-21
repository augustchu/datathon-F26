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

The density data has yet to be supplied. A small MAE gain over this unstable MLP alone would be weak evidence. Check the training median, RMSE and tail errors on the matched holdout when the two density results are available.
