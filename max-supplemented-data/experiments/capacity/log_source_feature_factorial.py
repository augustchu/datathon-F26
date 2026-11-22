"""Publish aggregate model comparisons and charts to Weights & Biases."""

from argparse import ArgumentParser
from pathlib import Path
import json
import os

import wandb


PLOT_CAPTIONS = {
    'factorial-test-mae.png': 'Test MAE across source rules and district feature blocks',
    'paired-changes.png': 'Paired MAE changes with 95% district-bootstrap intervals',
    'errors-by-facility-size.png': 'Absolute error and signed bias by reported bed count',
}


def table(rows):
    return wandb.Table(columns=list(rows[0]), data=[list(row.values()) for row in rows])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True, type=Path)
    parser.add_argument('--plots', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--run-dir', required=True, type=Path)
    parser.add_argument('--project', required=True)
    parser.add_argument('--entity', required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()

    result = json.loads(args.results.read_text())
    audit = result['audit']
    groups = list(result['results'].values())
    primary, strict = groups
    args.run_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('WANDB_CACHE_DIR', str(args.run_dir / 'cache'))
    os.environ.setdefault('WANDB_DATA_DIR', str(args.run_dir / 'staging'))
    os.environ.setdefault('WANDB_CONFIG_DIR', str(args.run_dir / 'config'))
    settings = wandb.Settings(console='off', disable_git=True, disable_code=True, disable_job_creation=True,
                              x_disable_meta=True, x_disable_machine_info=True, x_disable_stats=True)
    expected = None
    with wandb.init(id=args.run_id, resume='allow', mode='online', project=args.project, entity=args.entity,
                    dir=str(args.run_dir), name='fresh-xgboost-source-feature-factorial',
                    job_type='matched-evaluation', settings=settings, config={
                        'test_facilities': audit['holdout']['test']['n'],
                        'test_fingerprint': audit['holdout']['test']['sha256_facility_and_observed_count'],
                        'disputed_training_flags_removed': audit['strict_training_flags_changed'],
                        'xgboost_version': audit['xgboost_version'],
                        'fresh_fit_count': 34, 'prior_test_reused': audit['prior_test_reused'],
                    }) as run:
        arms = [row for group in groups for row in group['arms']]
        contrasts = [row for group in groups for row in group['contrasts']]
        slices = [row for group in groups for row in group['slices']]
        ranked_switches = [row for row in primary['contrasts'] if row['factor'] == 'Source rule only']
        panels = {
            'comparisons/arm_scores': table(arms),
            'comparisons/paired_effect_estimates': table(contrasts),
            'comparisons/size_and_feature_availability': table(slices),
            'plots/source_switch_only': wandb.plot.bar(
                table(ranked_switches), 'contrast', 'delta_mae',
                title='Switching to the newest source at fixed features · Test MAE change (beds)'),
            'plots/validation_versus_test': wandb.plot.scatter(
                table(arms), 'validation_mae', 'test_mae', title='Validation vs held-out test MAE (beds)'),
        }
        for name, caption in PLOT_CAPTIONS.items():
            panels[f'plots/{Path(name).stem}'] = wandb.Image(str(args.plots / name), caption=caption)
        run.log(panels)
        run.summary['analysis'] = {'original': primary['winner'], 'strict': strict['winner'],
                                   'test_predictor_coverage': audit['test_predictor_coverage'],
                                   'original_predictions_match_archive': audit['same_test_predictions_as_archive']}
        payload = wandb.Artifact('capacity-source-feature-factorial-results', type='evaluation',
                                 description='Aggregate results, confidence intervals, report and three model comparison charts.')
        for source in (args.results, args.report, *(args.plots / name for name in PLOT_CAPTIONS)):
            payload.add_file(str(source), name=source.name)
        expected = run.log_artifact(payload).wait(timeout=90).name
        result_url = run.url

    remote = wandb.Api(timeout=15).run(f'{args.entity}/{args.project}/{args.run_id}')
    artifact = next(a for a in remote.logged_artifacts() if a.name == expected)
    files = set(artifact.manifest.entries)
    if remote.state != 'finished' or not set(PLOT_CAPTIONS) <= files:
        raise RuntimeError('The remote run or comparison images could not be verified.')
    media = sorted(f.name for f in remote.files() if f.name.startswith('media/'))
    if sum(f.startswith('media/images/') for f in media) < len(PLOT_CAPTIONS):
        raise RuntimeError('Expected three image panels on the finished run.')
    receipt = {'url': result_url, 'run_id': args.run_id, 'artifact': expected,
               'artifact_files': sorted(files), 'run_media': media}
    (args.run_dir / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(result_url, expected, flush=True)


if __name__ == '__main__':
    main()
