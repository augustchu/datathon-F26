"""Render the model scores, paired effects, and size-specific errors."""

from argparse import ArgumentParser
from pathlib import Path
import json

import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
import numpy as np


FEATURE_SETS = ('facility_only', 'population', 'socioeconomic', 'socioeconomic_population')
FEATURE_LABELS = ('Facility only', '+ Population', '+ NFHS', '+ Both')
RULE_LABELS = {'ranked': 'Publisher ranked', 'newest': 'Newest report', 'raw_fdr': 'Original FDR'}
POLICY_LABELS = {'original_eligibility': 'Original training eligibility',
                 'exclude_disputed_targets': 'Disputed bed labels excluded'}
RULE_COLORS = {'ranked': '#126985', 'newest': '#a8462c', 'raw_fdr': '#64748b'}


def score_profiles(results, output):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6), sharey=True, layout='constrained')
    for ax, (policy, result) in zip(axes, results.items(), strict=True):
        arms = {(row['rule'], row['features']): row for row in result['arms']}
        for rule in ('ranked', 'newest'):
            values = [arms[rule, spec]['test_mae'] for spec in FEATURE_SETS]
            ax.plot(range(4), values, marker='o', lw=2, color=RULE_COLORS[rule], label=RULE_LABELS[rule])
        control = arms['raw_fdr', 'facility_only']['test_mae']
        ax.axhline(control, ls='--', lw=1.3, color=RULE_COLORS['raw_fdr'], label='Original FDR control')
        ax.set_xticks(range(4), FEATURE_LABELS)
        ax.set_title(POLICY_LABELS[policy])
        ax.grid(axis='y', color='#e6eaee')
    axes[0].set_ylabel('Test mean absolute error (beds)')
    axes[1].legend(loc='best', frameon=False, fontsize=9)
    fig.suptitle('Population and NFHS effects depend on the source rule', fontsize=15, weight='semibold')
    fig.supxlabel('Lower is better. Both panels score the same 455 facilities.', fontsize=9)
    fig.savefig(output / 'factorial-test-mae.png', dpi=165)
    plt.close(fig)


def paired_effects(results, output):
    baseline, stricter = results.values()
    strict_by_name = {row['contrast']: row for row in stricter['contrasts']}
    primary = [row for row in baseline['contrasts'] if row['factor'] in
               {'Source rule only', 'Population', 'NFHS'} or row['contrast'] == 'Ranked + population → newest + NFHS']
    fig, ax = plt.subplots(figsize=(10.4, 7.8), layout='constrained')
    for records, offset, color, label in [
        (primary, .14, '#126985', 'Original eligibility'),
        ([strict_by_name[row['contrast']] for row in primary], -.14, '#a8462c', 'Disputed bed labels excluded'),
    ]:
        y = np.arange(len(primary))[::-1] + offset
        point = np.asarray([r['delta_mae'] for r in records])
        ax.errorbar(point, y, xerr=[point - [r['lower_95'] for r in records],
                                   [r['upper_95'] for r in records] - point],
                    fmt='o', ls='none', color=color, capsize=2, label=label)
    ax.set_yticks(np.arange(len(primary))[::-1], [r['contrast'] for r in primary], fontsize=9)
    ax.axvline(0, color='#64748b', ls='--', lw=1)
    ax.grid(axis='x', color='#e6eaee')
    ax.set_xlabel('Change in test MAE (beds) · Negative favors the change')
    ax.set_title('What happens when one input changes', loc='left', fontsize=15, weight='semibold', pad=12)
    ax.legend(loc='lower right', frameon=True)
    fig.savefig(output / 'paired-changes.png', dpi=165)
    plt.close(fig)


def size_errors(results, output):
    result = results['original_eligibility']
    size_groups = ('Under 50 beds', '50–200 beds', 'Over 200 beds')
    slices = {(row['slice'], row['rule']): row for row in result['slices']}
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.6), layout='constrained')
    x = np.arange(len(size_groups))
    width = .23
    label = {'ranked': 'Ranked + population', 'newest': 'Newest + NFHS', 'raw_fdr': 'Original FDR'}
    for i, rule in enumerate(label):
        rows = [slices[name, rule] for name in size_groups]
        for ax, key in zip(axes, ('mae', 'mean_bias'), strict=True):
            ax.bar(x + (i - 1) * width, [r[key] for r in rows], width=width,
                   color=RULE_COLORS[rule], label=label[rule])
    for ax in axes:
        ax.set_xticks(x, [f'{name}\n(n={slices[name, "ranked"]["n"]})' for name in size_groups])
        ax.axhline(0, color='#64748b', lw=.8)
        ax.grid(axis='y', color='#e6eaee')
        ax.set_axisbelow(True)
    axes[0].set_ylabel('Mean absolute error (beds)')
    axes[1].set_ylabel('Mean prediction minus label (beds)')
    axes[0].legend(loc='upper left', frameon=False, fontsize=9)
    fig.suptitle('The average error hides large-hospital underprediction', fontsize=15, weight='semibold')
    fig.savefig(output / 'errors-by-facility-size.png', dpi=165)
    plt.close(fig)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--results', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    rc = {'font.family': 'DejaVu Sans', 'axes.spines.top': False, 'axes.spines.right': False,
          'text.color': '#182532', 'axes.labelcolor': '#182532', 'axes.edgecolor': '#8793a0'}
    plt.rcParams.update(rc)
    results = json.loads(args.results.read_text())['results']
    args.output.mkdir(parents=True, exist_ok=False)
    score_profiles(results, args.output)
    paired_effects(results, args.output)
    size_errors(results, args.output)


if __name__ == '__main__':
    main()
