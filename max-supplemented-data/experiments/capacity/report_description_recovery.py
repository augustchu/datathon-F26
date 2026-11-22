"""Render and log the held-out description-recovery comparison."""

from argparse import ArgumentParser
from pathlib import Path
import json
import os

NAMES = {'ranked': 'Publisher ranked', 'newest': 'Newest', 'same_year_mean': 'Same-year mean',
         'two_year_mean': 'Two-year mean'}
PLOTS = {'rule-scores.png': 'Validation and test MAE by source rule',
         'validation-learning-curves.png': 'Boosting curves and selected rounds',
         'error-by-facility-slice.png': 'Performance on the text and large-hospital slices'}


def chosen_trial(run):
    return next(t for t in run['trials'] if all(t[k] == v for k, v in run['fit'].items()))


def plot(data, directory):
    import matplotlib
    matplotlib.use('Agg')
    from matplotlib import pyplot as plt
    import numpy as np

    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False,
                         'axes.edgecolor': '#8793a0', 'axes.titlesize': 12, 'text.color': '#192c3b'})
    runs = data['runs']
    directory.mkdir(parents=True, exist_ok=False)
    palette = ('#126985', '#a8462c', '#6a4c88', '#497654')
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.6), sharex=True, layout='constrained')
    for ax, split in zip(axes, ('validation', 'test'), strict=True):
        values = [row[split]['mae'] for row in runs.values()]
        ax.scatter(range(len(runs)), values, c=palette, s=68, zorder=3)
        for i, value in enumerate(values):
            ax.annotate(f'{value:.2f}', (i, value), xytext=(0, -14), textcoords='offset points', ha='center')
        ax.set_xticks(range(len(runs)), [label.replace(' ', '\n', 1) for label in NAMES.values()], fontsize=9)
        ax.margins(x=.19, y=.16)
        ax.set_title(f'{split.capitalize()} · {next(iter(runs.values()))["counts"][split]} facilities')
        ax.grid(axis='y', color='#e4e9ed')
    axes[0].set_ylabel('Mean absolute error (beds) · Lower is better')
    axes[1].axhline(data['previous_release']['mae'], ls='--', color='#64748b', label='Earlier release model')
    axes[1].legend(frameon=False, loc='upper center', fontsize=9)
    fig.suptitle('Full-width XGBoost after description recovery', fontsize=15, weight='semibold')
    fig.savefig(directory/'rule-scores.png', dpi=165)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(9.4, 4.8), layout='constrained')
    distinct = set()
    for (key, run), color in zip(runs.items(), palette, strict=True):
        curve = chosen_trial(run)['history']['validation']['mae_beds']
        trajectory = tuple(curve)
        if trajectory in distinct:
            continue
        distinct.add(trajectory)
        stop = run['fit']['best_round']
        label = NAMES[key]
        if key == 'newest' and data['date_rule_predictions_equal']:
            label = 'Newest and both averaging rules'
        ax.plot(range(1, len(curve) + 1), curve, label=label, color=color)
        ax.scatter(stop, curve[stop - 1], color=color, s=42, zorder=4)
    ax.grid(axis='y', color='#e4e9ed')
    ax.set(xlabel='Boosting round', ylabel='Validation MAE (beds)',
           title='Validation error and the selected stopping rounds')
    ax.legend(frameon=False)
    fig.savefig(directory/'validation-learning-curves.png', dpi=165)
    plt.close(fig)

    chosen = runs[data['test_mae_leader']]
    ranked = runs['ranked']
    slice_names = list(ranked['slices'])
    x = np.arange(len(slice_names))
    width = .23
    fig, ax = plt.subplots(figsize=(10, 4.6), layout='constrained')
    values = [('Earlier release', [ranked['slices'][s]['benchmark_mae'] for s in slice_names], '#64748b'),
              ('Publisher ranked · wide', [ranked['slices'][s]['mae'] for s in slice_names], palette[0]),
              (f'{NAMES[data["test_mae_leader"]]} · wide', [chosen['slices'][s]['mae'] for s in slice_names],
               palette[1])]
    if data['test_mae_leader'] == 'ranked':
        values.pop()
    for i, (name, scores, color) in enumerate(values):
        ax.bar(x + (i - (len(values) - 1) / 2) * width, scores, width=width, color=color, label=name)
    ax.set_xticks(x, [f'{s.capitalize()}\n(n={ranked["slices"][s]["n"]})' for s in slice_names])
    ax.set(ylabel='Test MAE (beds)', title='Error by bed wording and facility size')
    ax.legend(frameon=False, loc='upper left')
    ax.grid(axis='y', color='#e4e9ed')
    ax.set_axisbelow(True)
    fig.savefig(directory/'error-by-facility-slice.png', dpi=165)
    plt.close(fig)


def track(args, data):
    import wandb

    args.run_dir.mkdir(parents=True, exist_ok=True)
    for variable, directory in [('WANDB_CACHE_DIR', 'cache'), ('WANDB_DATA_DIR', 'staging'),
                                ('WANDB_CONFIG_DIR', 'config')]:
        os.environ.setdefault(variable, str(args.run_dir/directory))
    runs = data['runs']
    summary = [[NAMES[name], run['validation']['mae'], run['test']['mae'], run['test']['rmse'],
                run['paired_mae_vs_ranked']['lower_95'], run['paired_mae_vs_ranked']['upper_95'],
                run['new_training_bed_labels']] for name, run in runs.items()]
    recoveries = [[NAMES[name], field, run['description_recoveries'].get(field, 0)]
                  for name, run in runs.items() for field in ('capacity', 'numberDoctors', 'service', 'equipment', 'yearEstablished')]
    settings = wandb.Settings(console='off', disable_git=True, disable_code=True, disable_job_creation=True,
                              x_disable_meta=True, x_disable_machine_info=True, x_disable_stats=True)
    with wandb.init(id=args.run_id, resume='allow', mode='online', project=args.project, entity=args.entity,
                    dir=str(args.run_dir), name='description-recovery-wide-xgboost-comparison',
                    job_type='matched-evaluation', settings=settings, config={
                        'exclusions': data['excluded_source_fields'],
                        'training_fits': 4 * len(runs),
                        'test_facilities': next(iter(runs.values()))['counts']['test'],
                        'description_rows_scanned': data['description_rows_scanned'],
                    }) as run:
        metric_table = wandb.Table(columns=['Rule', 'Validation MAE', 'Test MAE', 'Test RMSE', 'Paired lower',
                                            'Paired upper', 'New training beds'], data=summary)
        panels = {'comparison/metrics': metric_table,
                  'comparison/recoveries': wandb.Table(columns=['Rule', 'Field', 'Newly filled branches'],
                                                        data=recoveries),
                  'comparison/test_mae': wandb.plot.bar(metric_table, 'Rule', 'Test MAE',
                                                        title='Same 455 test facilities · MAE in beds')}
        for name, caption in PLOTS.items():
            panels[f'figures/{Path(name).stem}'] = wandb.Image(str(args.plots / name), caption=caption)
        run.define_metric('boosting_round')
        run.define_metric('validation/*', step_metric='boosting_round')
        curves = {name: chosen_trial(m)['history']['validation']['mae_beds'] for name, m in runs.items()}
        for i in range(max(map(len, curves.values()))):
            run.log({'boosting_round': i + 1, **{f'validation/{name}_mae_beds': series[i]
                                                for name, series in curves.items() if i < len(series)}})
        run.log(panels)
        run.summary.update({'validation_leader': data['validation_leader'], 'test_mae_leader': data['test_mae_leader'],
                            'date_rule_predictions_equal': data['date_rule_predictions_equal']})
        art = wandb.Artifact('description-recovery-wide-capacity-comparison', type='evaluation',
                             description='Aggregate rule metrics, recovery counts, training curves, and written evaluation.')
        for file in (args.results, args.report, *(args.plots / name for name in PLOTS)):
            art.add_file(str(file), name=file.name)
        expected = run.log_artifact(art).wait(timeout=90).name
        url = run.url

    remote = wandb.Api(timeout=15).run(f'{args.entity}/{args.project}/{args.run_id}')
    artifact = next(a for a in remote.logged_artifacts() if a.name == expected)
    media = sorted(f.name for f in remote.files() if f.name.startswith('media/'))
    if (remote.state != 'finished' or not set(PLOTS) <= set(artifact.manifest.entries)
            or len([f for f in media if f.startswith('media/images/')]) < len(PLOTS)):
        raise RuntimeError('The charts could not be verified on the remote run.')
    (args.run_dir/'receipt.json').write_text(json.dumps({'url': url, 'artifact': expected, 'media': media},
                                                       indent=2)+'\n')
    print(url, expected, flush=True)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--plots', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--run-dir', type=Path)
    parser.add_argument('--project')
    parser.add_argument('--entity')
    parser.add_argument('--run-id')
    args = parser.parse_args()
    data = json.loads(args.results.read_text())
    if args.run_dir:
        if not all([args.report, args.project, args.entity, args.run_id]):
            parser.error('A report, project, entity and run ID are required to log.')
        track(args, data)
    else:
        plot(data, args.plots)


if __name__ == '__main__':
    main()
