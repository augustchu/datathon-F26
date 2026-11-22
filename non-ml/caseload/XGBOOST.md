# XGBoost estimates for missing facility caseload

The experiment uses two sets of reported annual facility activity. It estimates a typical month's **delivered workload** by dividing each reported period total by its number of calendar months. The prediction has the same activity unit as the source. The model compares facilities within each programme. The two programmes have different definitions, features, and populations.

## Results

| Service and test | Scored sites | XGBoost MAE per month | Lowest comparator MAE |
| --- | ---: | ---: | ---: |
| Himachal Pradesh dialysis, held-out districts across both fiscal years | 24, with 47 site-years | 60.21 sessions | 51.75 sessions, machine-scaled median |
| Himachal Pradesh dialysis, later fiscal year and held-out districts | 24 | 61.32 sessions | 52.83 sessions, machine-scaled median |
| Himachal Pradesh dialysis, later fiscal year at previously observed sites | 23 | 59.95 sessions | 45.85 sessions, last observed fiscal-year average |
| Tamil Nadu maternity, held-out districts | 94 | 39.76 deliveries | 36.86 deliveries, bed-scaled median within facility type |

The simpler comparators had lower MAE in every test. The results file retains the XGBoost estimates because this is an offline model comparison. Each application identifies the method with the lower test MAE and includes its estimate. Neither historical dialysis sessions nor the machine count establish current appointment capacity.

The [baseline coverage audit](BASELINES.md) carries the lower-error prior-year estimates into a separate file for the five exact facility IDs in the final release.

## Training data

**Himachal Pradesh dialysis.** The Pradhan Mantri National Dialysis Programme (PMNDP) sources give **23 site rows in FY2023–24** and **24 in FY2024–25**. They reconcile to 53,736 and 61,224 annual programme haemodialysis sessions. The input is the site's functional machine count from March at the end of each reported fiscal year. March session counts are retained in the manifest for source checks. They are excluded as predictors because they are part of the annual outcomes. The yearly average can include months before a centre opened. Civil Hospital Manali appears only in FY2024–25, when the 865 fiscal-year sessions equal its cumulative count since start. The estimates are therefore programme fiscal-year averages rather than estimates of a new unit's steady operating pace. [FY2023–24 NHM report](https://nhm.hp.gov.in/storage/app/media/uploaded-files/PMNDP%202023-24.pdf) · [FY2024–25 State Health Society report, pp. 32–33](https://himachal.nic.in/ControlsAsync/ViewFTPFile?qs=KI3gZ53zz1zG%2FM2MNgtMD88%2B%2FdIiV4g0qH%2F6C%2BTu6A%2BCGENB6PC4Q3ptiX8VVWg9n4fbej7hbzccABnn7JOg6hIRA2eJF63H)

**Tamil Nadu maternity.** The cohort contains **94 Directorate of Medical and Rural Health Services (DMS) comprehensive emergency obstetric and newborn care centres in 37 districts**. Table 1 of the NHM Executive Committee agenda reports 137,712 deliveries and 6,173 CEmONC beds in this cohort. The model uses CEmONC beds and the source's facility type. It leaves out occupancy, staffing proposals, priority status, facility name, and district name. The published table covers all 94 facilities for FY2025–26. The agenda gives a separate cohort for medical college hospitals. Public CHCs elsewhere in India and private maternity sites require their own evidence and model validation. [Tamil Nadu NHM, Agenda 24 Annexure 1](https://nhm.tn.gov.in/wp-content/uploads/2026/51st_EC-Part-A-Final.pdf)

### Population comparison

I tested the same XGBoost specification and held-out districts with and without district population on the same rows. For HP dialysis, 2011 district population increased MAE from **60.21 to 66.09** sessions/month. For TN maternity, mid-2022 district estimates increased MAE from **39.76 to 40.19** deliveries/month. The input manifests record the vintages and name matching. These comparisons test district population totals. Catchment population, age-specific population, and births were outside this experiment. [HP Economic Survey, appendix Table 8](https://himachalservices.nic.in/economics/pdf/en-economic_survey_2025-26.pdf) · [Tamil Nadu Statistical Handbook, Table 2.1](https://www.des.tn.gov.in/sites/default/files/2025-07/2.VITAL%20STATISTICS.pdf)

## Evaluation design

- The base model uses XGBoost 3.4.1, a Poisson count objective, depth 2, a 0.05 learning rate, L2 regularization of 10, and 100 boosting rounds. One-hot encoding for facility type is fitted within each training fold. A single fixed configuration was used throughout. [XGBoost parameters](https://xgboost.readthedocs.io/en/stable/parameter.html)
- District holdouts exclude all sites and years in the held district from training. The first two HP district tests use period-end machine inventories. The later-year district test trains on the earlier fiscal year and different districts, with the held-out sites' available period-end machinery. It measures transfer to a new site and period with machinery known.
- The known-site forward test uses earlier labels and, for each test site, the previous March's machinery. It scores only sites seen before the target year. Manali has no preceding row and is excluded from that test. This is the closest of the tests to the FY2025–26 application, which carries forward the last published site-level machine counts.
- Each comparator is fitted on the same training fold. The pooled median ignores facility features. The peer-type median uses the same category where at least two rows are available. Capacity scaling multiplies the site's input by the fold median of deliveries per CEmONC bed or sessions per machine, within type where applicable. The known-site test also uses the same centre's last observed average.
- MAE is the mean of absolute errors on the identical test rows. The output also saves the median absolute error, the 90th percentile absolute error, the signed error, and each holdout prediction and outcome. The uncertainty band on each XGBoost estimate is the point plus or minus the empirical 90th percentile absolute error of the selected validation test, truncated at zero. The small held-out sample and possible capacity changes limit the interpretation of these bands.

## Application to missing caseload

The [results JSON](xgboost_results.json) contains XGBoost estimates for **24 HP PMNDP sites for FY2025–26** with no validated site-level FY2025–26 programme total in the input. The application uses March 2025 functional machine counts as the scenario for that year. Each row has earlier activity at the same centre. An output row is a modeled figure for a missing reporting period. The five rows with a reviewed FDR identity link can be joined by their `facility_link.fdr_id` field. The other rows use their named public programme identity.

| Site | March 2025 machines | XGBoost sessions/month | Last observed sessions/month |
| --- | ---: | ---: | ---: |
| Regional Hospital Bilaspur | 6 | 202 | 257 |
| Regional Hospital Kullu | 8 | 307 | 276 |
| Zonal Hospital Dharamshala | 9 | 369 | 464 |

The 24 XGBoost values sum to roughly **4,914 sessions per month**. The previous-fiscal-year average sums to **5,102**. The state later reported **48,048 sessions** across 24 PMNDP units through November 2025, or **6,006 per month over those eight months**. The annual and eight-month periods differ, and no site shares accompany that aggregate. The comparison suggests caution about underestimating the more recent programme scale and cannot score individual facility predictions. [Himachal Pradesh Economic Survey 2025–26, p. 310](https://himachalservices.nic.in/economics/pdf/en-economic_survey_2025-26.pdf) · [MoHFW programme count](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2297506&lang=2&reg=48)

The maternity reports supply observed totals for every centre in the tested DMS cohort. The previous review cases at Faridabad Medical Center, Arti Hospital Budge Budge, and CHC Dudhi fall outside the tested cohorts. The application figures are stored in a separate offline results file. A service-pressure calculation needs comparable staffed capacity at the site for the same period.

## Reproduce

From the repository root, use Python 3.10 or newer, NumPy, and XGBoost 3.4.1.

```sh
python3 -m pip install "xgboost==3.4.1" numpy
python3 non-ml/caseload/xgboost_caseload.py --output non-ml/caseload/xgboost_results.json
python3 -m unittest discover -s non-ml/caseload -p 'test_*.py'
```

The [dialysis](hp_dialysis_model_data.json) and [maternity](tn_maternity_model_data.json) manifests record source links and locators, column definitions, the unit, exact observed counts, and candidate rows. A candidate has a missing `total` and a unique facility-period key. Record the source date and scope of any carried-forward feature. The builder checks features against observed ranges and district coverage for categories before emitting an estimate. The JSON stores the source and the model's comparable test for each cohort. It can be regenerated without reading any private dataset.
