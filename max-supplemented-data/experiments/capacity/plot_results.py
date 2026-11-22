"""Render aggregate capacity evaluation figures from a frozen training receipt."""

from argparse import ArgumentParser
import json
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt


FEATURE_LABELS = {'facility_only': 'Facilities', 'socioeconomic': '+ socioeconomic',
                  'socioeconomic_population': '+ socioeconomic and population'}


def plot(report, output):
    winner_id = report['winner']['release_run_id']
    winner = next(run for run in report['runs'] if run['run_id'] == winner_id)
    figure, (all_arms, by_size) = plt.subplots(1, 2, figsize=(11.6, 4.9), layout='constrained',
                                              gridspec_kw={'width_ratios': [1.32, 1]})
    colors = ['#007b78' if run['run_id'] == winner_id else '#7591a8' for run in report['runs']]
    labels = [f'{run["iteration"][-2:]}  {FEATURE_LABELS[run["feature_set"]]}' for run in report['runs']]
    bars = all_arms.barh(labels, [run['test']['mae'] for run in report['runs']], color=colors)
    all_arms.bar_label(bars, fmt='%.1f', padding=3, fontsize=9)
    all_arms.axvline(winner['baseline']['test']['mae'], color='#324059', linestyle='--', label='Training median')
    upper = max(winner['baseline']['test']['mae'], *(run['test']['mae'] for run in report['runs'])) * 1.15
    all_arms.set(xlim=(0, upper), title='Six models on the same facilities', xlabel='Mean absolute error (beds)')
    all_arms.invert_yaxis()
    all_arms.legend(frameon=False, loc='lower left', bbox_to_anchor=(0, 1.10))

    groups = winner['subgroups']['metrics']['actual_bed_band']
    bands = [band for band in ('0–49', '50–200', '201+') if band in groups]
    bars = by_size.bar([f'{band}\n(n={groups[band]["n"]})' for band in bands],
                       [groups[band]['mae'] for band in bands], color='#007b78', width=.58)
    by_size.bar_label(bars, fmt='%.1f', padding=3, fontsize=9)
    by_size.margins(y=.18)
    by_size.set(title='Selected model by facility size', xlabel='Reported bed count', ylabel='Mean absolute error (beds)')
    for axis in (all_arms, by_size):
        axis.spines[['top', 'right']].set_visible(False)
        axis.set_axisbelow(True)
        axis.grid(axis='x' if axis is all_arms else 'y', color='#e5ebef', linewidth=.8)
    figure.suptitle(f'Bed capacity evaluation (n={report["holdout"]["test"]["n"]})', fontsize=15, weight='bold')
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, dpi=165, facecolor='white')
    plt.close(figure)


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--metrics', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(json.loads(args.metrics.read_text(encoding='utf8')), args.output)
