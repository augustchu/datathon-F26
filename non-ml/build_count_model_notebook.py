"""Generate the count-model comparison notebook."""

from build_analysis_notebooks import code, markdown, notebook


def build():
    notebook('09_count_model_comparison', 'Compare estimates for missing resource counts', '''
        Run notebook 03 first for the same snapshot. This optional comparison reads its
        saved facility-held partitions and executes on CPU with scikit-learn 1.2 or later.
        It fits resource-count models and writes no changes to source data. Facility-level
        estimates that pass the release checks are saved to a private checkpoint for notebook 05
        and a CSV with the same fields for review or import. Fitted models and aggregate scores
        are saved as offline Weights & Biases artifacts by default.
    ''', [
        markdown('''
            ## Check the inputs and the labels

            Both targets use the original FDR column order. The predictor shortlist is
            facility type, operator type, and the other unambiguous observed count, with
            a companion availability flag as part of that numeric block. Each listed field
            must also fall strictly after `countries`. Phones and phone-derived fields
            stay outside the models. The lookup still displays cited published contacts.
            The actual FDR schema places establishment year before `countries`.

            URLs, coordinates and unknown strings in the category columns do not become
            one-hot levels. Recognized category names are normalized with explicit aliases
            for `farmacy` and `government`. See the prior audit for raw values and conflicts.
            Keep every exact label in the primary fit, including large observations. A
            separate sensitivity fit omits training values above the training 99th percentile.
            Every exact held-out value remains in both evaluations.
        '''),
        code('''
            from facility_tool.count_models import CATEGORY_NAMES, fit_count_models
            from facility_tool.eda import COUNT_UNITS
            from facility_tool.updates import empty_updates

            SEED = 42
            LOG_MODEL_TO_WANDB = os.environ.get('DATATHON_LOG_COUNT_MODELS', '1') != '0'
            original = STORE.read('raw_facilities').columns.tolist()
            fields = {'capacity': 'capacity', 'numberDoctors': 'number_doctors'}
            audits = {field: STORE.read(f'{prefix}_target_audit', optional=True) for field, prefix in fields.items()}
            write_private_estimates = MODE in ('export', 'databricks')  # Local checkpoint outside version control.
            for field, audit in audits.items():
                if audit.empty:
                    print(f'{field}: run notebook 03 on a snapshot that includes the original FDR rows.')
                else:
                    show(audit.groupby(['split', 'status']).size().rename('facilities').reset_index())
        '''),
        markdown('''
            ## Earn the predictor set on training facilities only

            Check that a category has at least two sufficiently observed levels with
            different outcome medians. A numeric candidate needs at least 30 paired labels,
            coverage of at least 5%, absolute Spearman correlation of at least 0.10 and exact
            count agreement below 95%. The category screen also requires 5% labeled coverage.
            Next, compare candidate blocks in five deterministic training-only folds of
            unique linked facilities. Both methods must improve mean fold MAE at least 1%
            and improve at least four folds over the simpler feature set. At the first
            step, the simpler comparator is the fold-training median. At later steps it
            is the previously selected model. The shared feature set adds the eligible
            candidate with the greatest average relative gain across the two methods.

            Ridge and the random forest fit `log(1 + count)` and convert predictions back to
            whole nonnegative counts. Log scale limits the influence of extreme count values.
            In ridge, a change of one unit in an encoded predictor multiplies the model's
            back-transformed `1 + count` by `exp(coefficient)`, holding its
            other inputs fixed. Two category levels are compared using the difference of
            their coefficients. The back-transformed estimate reflects the fitted log scale
            and does not estimate the arithmetic mean of the raw counts.
        '''),
        code('''
            results = {}
            for field, audit in audits.items():
                if audit.empty:
                    continue
                support = audit.loc[audit['status'].eq('reported'), 'split'].value_counts()
                if any(support.get(part, 0) < limit for part, limit in [('train', 10), ('calibration', 9), ('test', 2)]):
                    print(f'{field}: too few labeled facilities in at least one partition, as expected on the small demo.')
                    continue
                companion = next(other for other in fields if other != field)
                comparison = fit_count_models(field, audit, audits[companion], original, as_of=config['as_of'], seed=SEED)
                results[field] = comparison
                for name in ('label_quality', 'category_quality', 'candidate_evidence', 'ablation', 'coefficients'):
                    table = comparison[name]
                    STORE.write(f'count_models_{field.lower()}_{name}', table)
                    print(f'{field} | {name.replace("_", " ")}')
                    show(table, limit=40)
                STORE.write(f'count_models_{field.lower()}_fold_scores', comparison['fold_scores'])
        '''),
        markdown('''
            ## Report test performance and limits of the overlay

            Prediction intervals use absolute log-count residuals from the separate
            calibration partition, with the finite-sample 90% split-conformal rank.
            Held-out metrics compare the observed counts, including the tail above the
            training 90th percentile. Test coverage checks these intervals on branches
            with reported labels. Reporting patterns can differ where counts are missing.
            Earlier exploratory work examined an all-feature model on this same FDR test
            split. These metrics are internal evaluation after training-only selection.
            Independent evaluation remains open.
            The groups below use facility type and, if selected, companion-count availability.
            A group is eligible only with at least 40 training, 30 calibration and 30 test
            labels, a model that beats the overall median baseline, group MAE below both
            relevant median comparisons, and observed interval coverage of at least 80%.
            An estimate also needs a genuinely missing count, a recognizable facility type,
            acceptable categories and predictor support. Extreme observed labels and unusable
            source values are preserved in notebook 03's audit.

            These estimates describe bed or doctor counts. They supply neither operating
            service evidence nor date-specific capacity or availability. Logistic service
            classification requires independently reviewed yes and no labels.
        '''),
        code('''
            estimates = empty_updates()
            if results:
                estimates = pd.concat([result['updates'] for result in results.values()], ignore_index=True)
            for field, comparison in results.items():
                for name in ('metrics', 'group_metrics'):
                    table = comparison[name]
                    STORE.write(f'count_models_{field.lower()}_{name}', table)
                    print(f'{field} | {name.replace("_", " ")}')
                    show(table, limit=40)
            print(f'Overlay rows that passed the release checks: {len(estimates):,}')
            if not estimates.empty:
                show(estimates.groupby(['field', 'method']).size().rename('rows').reset_index())
            if write_private_estimates:
                STORE.write('count_model_estimates', estimates)
                csv_path = STORE.write_csv('count_model_estimates', estimates)
                print(f'Portable estimate table: {csv_path}')
        '''),
        markdown('''
            ## Save fitted count models to Weights & Biases

            This cell saves the primary fitted ridge and random forest for each target that
            yielded a predictor set. Each artifact includes the fitted preprocessing, model,
            held-out scores, training-median baseline, sensitivity scores, and aggregate
            feature-selection results from the training folds. Facility records and individual
            estimates stay out of the artifacts. The sensitivity scores use the same test set.

            If the tracking packages are missing, run `%pip install "wandb==0.31.2" "skops==0.16.0"`
            in a separate notebook cell. Set `DATATHON_LOG_COUNT_MODELS=0` before running the
            notebook to skip tracking. See the [ML tracking setup](../../ML/README.md#experiment-tracking)
            for W&B project settings and offline storage.

            The saved pipeline predicts on the log scale. Convert its point output with
            `np.maximum(0, np.rint(np.expm1(predicted)))` to recover counts in beds or doctors.
            See the artifact metadata for how to prepare the selected input columns.
        '''),
        code('''
            if LOG_MODEL_TO_WANDB and any(result['fitted_pipelines'] for result in results.values()):
                import json

                sys.path.insert(0, str(ROOT / 'ML' / 'notebooks'))
                from wandb_models import log_sklearn_capacity

                def records(frame):
                    return json.loads(frame.to_json(orient='records', double_precision=15))

                model_names = {'regression': 'ridge', 'ml': 'random-forest'}
                for field, result in results.items():
                    metrics = result['metrics']
                    numeric = metrics.select_dtypes(include='number').columns
                    baseline = metrics.loc[metrics['method'].eq('median'), numeric].iloc[0].to_dict()
                    for method, pipeline in result['fitted_pipelines'].items():
                        method_metrics = metrics.loc[metrics['method'].eq(method)]
                        primary = method_metrics.loc[method_metrics['run'].eq('all_exact_labels'), numeric].iloc[0].to_dict()
                        feature_names = list(pipeline.feature_names_in_)
                        categorical = [name for name in feature_names if name in CATEGORY_NAMES]
                        tracking = log_sklearn_capacity(
                            name=f'{fields[field].replace("_", "-")}-{model_names[method]}',
                            model=pipeline,
                            metrics=primary,
                            baseline_metrics=baseline,
                            evaluation_tables={
                                'test_metrics': records(method_metrics),
                                'group_metrics': records(result['group_metrics'].query('method == @method')),
                                'label_quality': records(result['label_quality']),
                                'candidate_evidence': records(result['candidate_evidence']),
                                'feature_selection': records(result['ablation']),
                                'fold_scores': records(result['fold_scores'].query('method == @method')),
                            },
                            config={
                                'target_field': field,
                                'target_unit': COUNT_UNITS[field],
                                'snapshot_as_of': config['as_of'],
                                'input_mode': MODE,
                                'model_seed': SEED,
                                'selected_predictors': feature_names,
                                'categorical_features': categorical,
                                'numeric_features': [name for name in feature_names if name not in categorical],
                                'feature_stage': (
                                    'Use the selected facility/operator categories normalized with CATEGORY_NAMES, '
                                    'using (unknown) for missing categories. If the companion count is selected, '
                                    'provide log1p(raw reported count) and other_count_reported=True, or NaN and False.'
                                ),
                                'prediction_postprocess': (
                                    'The fitted pipeline returns log1p(count). Apply expm1 once, round '
                                    'to the nearest whole count and clamp at zero.'
                                ),
                            },
                            target_transform='log1p before fitting; expm1 after predict',
                        )
                        print(tracking)
        '''),
    ])


if __name__ == '__main__':
    build()
