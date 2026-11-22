"""Check the production lookup with live TravelTime responses and saved evidence."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
import sys
from tempfile import NamedTemporaryFile
from unittest.mock import patch

import pandas as pd

def repository_root():
    configured = os.environ.get('DATATHON_REPO_ROOT')
    script = globals().get('__file__')
    if configured:
        start = Path(configured).expanduser()
    elif script:
        start = Path(script).parent
    else:
        start = Path.cwd()
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / 'non-ml' / 'config' / 'project.json').is_file():
            return directory
    raise SystemExit('Set DATATHON_REPO_ROOT to the datathon-F26 Git checkout or run from that checkout.')


ROOT = repository_root()
sys.path.insert(0, str(ROOT / 'non-ml' / 'src'))

from facility_tool import travel
from facility_tool.lookup import lookup
from facility_tool.recommend import haversine_km
from facility_tool.workspace import Store, read_config


CASES = {
    'jaipur-driving': ('Jaipur, Rajasthan', 'driving'),
    'gadchiroli-driving': ('Gadchiroli, Maharashtra', 'driving'),
    'gadchiroli-walking': ('Gadchiroli, Maharashtra', 'walking'),
}
OLD_RADIUS_KM = 50
LIMIT = 5
SERVICE = 'blood bank'
STOP_STATUSES = {'credential_or_plan_rejected', 'rate_limited'}


def coordinates(rows):
    return Counter((float(row.site_lat), float(row.site_lon)) for row in rows.itertuples(index=False))


def rank_routes(documented, estimates, mode):
    """Calculate closest-mode order independently from the returned matrix."""
    if estimates.empty:
        return [], len(documented)
    joined = documented[['facility_id', 'evidence_tier']].merge(estimates, on='facility_id', validate='1:1')
    distance = pd.to_numeric(joined['route_distance_km'], errors='coerce')
    usable = joined['travel_status'].eq('estimated') & joined['travel_mode'].eq(mode) & distance.map(isfinite)
    ranked = joined.loc[usable & distance.ge(0)].sort_values(
        ['evidence_tier', 'route_distance_km', 'travel_minutes', 'facility_id'], na_position='last',
    )
    unreachable = (joined['travel_status'].eq('not_routed_within_limit') & joined['travel_mode'].eq(mode)).sum()
    return ranked['facility_id'].tolist(), len(documented) - len(ranked) - int(unreachable)


def verify_ranks(result, expected, fallback, requested_mode):
    errors = []
    source_status = result['route_status'].set_index('facility_id')['travel_status']
    fallback_ranks = {facility_id: index for index, facility_id in enumerate(fallback, start=1)}
    expected_modes = [requested_mode] * (len(expected) - len(fallback)) + ['driving'] * len(fallback)
    rows = result['rows']
    recommendations = rows.loc[rows['kind'].eq('recommendation')]
    for mode in ('confirmed', 'regression', 'ml'):
        recs = recommendations.loc[recommendations['mode'].eq(mode)].sort_values('rank')
        if recs['facility_id'].tolist() != expected or recs['rank'].tolist() != list(range(1, len(expected) + 1)):
            errors.append(f'The {mode} recommendation order differs from the complete route matrix.')
        if recs['travel_mode'].tolist() != expected_modes:
            errors.append(f'The {mode} recommendations show an incorrect transport mode.')
        for row in recs.itertuples(index=False):
            car_only = row.route_note.startswith('Car only for this search.')
            if row.facility_id not in fallback_ranks:
                if car_only:
                    errors.append(f'A requested-mode result received a car-only label in {mode}.')
                continue
            status = source_status.get(row.facility_id)
            if pd.isna(row.driving_rank) or row.driving_rank != fallback_ranks[row.facility_id]:
                errors.append(f'A driving alternative shows the wrong driving rank in {mode}.')
            expected_status = 'missing_requested_metric' if status == 'estimated' else status or 'missing_result'
            if pd.isna(row.requested_mode_route_status) or row.requested_mode_route_status != expected_status:
                errors.append(f'A driving alternative shows the wrong requested-mode status in {mode}.')
            if car_only != (status == 'not_routed_within_limit'):
                errors.append(f'A driving label disagrees with the provider status in {mode}.')
            if status != 'not_routed_within_limit' and 'estimate was unavailable' not in row.route_note:
                errors.append(f'A driving alternative omits the unavailable-estimate label in {mode}.')
    return errors


def check_case(store, config, case):
    location, mode = CASES[case]
    candidates = store.read('lookup_candidates_blood_bank')
    documented = candidates.loc[candidates['location_routing_eligible'].eq(True)
                                & candidates['evidence_hold_reason'].eq('')]
    routable = candidates.loc[candidates['location_routing_eligible'].eq(True)
                              & (candidates['evidence_hold_reason'].eq('') | candidates['model_leads'].map(bool))]
    expected_points = coordinates(routable)
    submitted = defaultdict(Counter)
    batches = defaultdict(list)
    post = travel._post_json

    def record_request(payload, **kwargs):
        search = payload['departure_searches'][0]
        transport = search['transportation']['type']
        locations = {row['id']: row['coords'] for row in payload['locations']}
        points = [locations[ident] for ident in search['arrival_location_ids']]
        submitted[transport].update((point['lat'], point['lng']) for point in points)
        batches[transport].append(len(points))
        return post(payload, **kwargs)

    with patch.object(travel, '_post_json', record_request):
        result = lookup(store, config, concern=SERVICE, location=location, travel_mode=mode,
                        facility_sort='closest', traveltime_enabled=True)

    preferred, missing = rank_routes(documented, result['route_status'], mode)
    driving, missing_driving = rank_routes(documented, result['driving_route_status'], 'driving')
    needs_driving = mode != 'driving' and len(preferred) < len(documented)
    fallback = []
    if needs_driving and driving:
        if not preferred:
            fallback = driving[:LIMIT]
        elif driving[0] not in preferred:
            fallback = driving[:1]
    expected = preferred[:LIMIT - len(fallback)] + fallback
    failures = verify_ranks(result, expected, fallback, mode)
    blockers = []
    route_tables = {mode: result['route_status']}
    if needs_driving:
        route_tables['driving'] = result['driving_route_status']
    statuses_by_mode = {
        transport: {str(key): int(value) for key, value in table['travel_status'].value_counts().items()}
        for transport, table in route_tables.items() if not table.empty
    }
    status_counts = Counter()
    for status in statuses_by_mode.values():
        status_counts.update(status)
    provider_incomplete = missing or (needs_driving and (missing_driving or result['driving_route_status'].empty))
    if provider_incomplete:
        blockers.append('Some eligible destinations had missing or incomparable provider estimates.')
    transports = {mode, 'driving'} if needs_driving else {mode}
    coverage_complete = all(submitted[transport] == expected_points for transport in transports)
    if not coverage_complete and STOP_STATUSES & status_counts.keys():
        blockers.append('The provider comparison stopped before the full destination cohort could be checked.')
    elif not coverage_complete:
        failures.append('The submitted destinations differ from the full eligible cohort.')

    origin = result['origin']
    outside_old_radius = sum(count for (lat, lon), count in expected_points.items()
                             if haversine_km(origin.lat, origin.lon, lat, lon) > OLD_RADIUS_KM)
    confirmed = result['rows'].loc[result['rows']['mode'].eq('confirmed')
                                   & result['rows']['kind'].eq('recommendation')].sort_values('rank')
    outside_ranked = int(confirmed['distance_km'].gt(OLD_RADIUS_KM).sum())
    car_only_count = int(confirmed['route_note'].str.startswith('Car only for this search.').sum())
    if len(expected) < LIMIT:
        blockers.append('Fewer than five comparable options were returned for this search.')
    if case == 'gadchiroli-driving' and not outside_ranked:
        blockers.append('This provider response did not exercise a top-five recommendation beyond the old 50 km cutoff.')
    if case == 'gadchiroli-walking' and not car_only_count:
        blockers.append('This provider response did not exercise the explicit car-only label.')
    columns = ['rank', 'name', 'travel_mode', 'distance_km', 'route_distance_km', 'travel_minutes',
               'route_note', 'requested_mode_route_status']
    return {
        'case': case, 'origin': location, 'mode': mode,
        'outcome': 'fail' if failures else 'inconclusive' if blockers else 'pass',
        'failures': failures, 'blockers': blockers, 'documented_branches': len(documented),
        'expected_destinations_per_mode': sum(expected_points.values()),
        'full_destination_cohort_submitted': coverage_complete,
        'request_batch_sizes': dict(sorted(batches.items())), 'provider_status_counts': dict(status_counts),
        'provider_status_counts_by_mode': statuses_by_mode,
        'destinations_beyond_old_50_km_radius': outside_old_radius,
        'recommendations_beyond_old_50_km_radius': outside_ranked,
        'car_only_labels': car_only_count,
        'recommendations': json.loads(confirmed[columns].to_json(orient='records')),
    }


def main(argv=None):
    config = read_config(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint-root', type=Path, help='Prepared DATATHON_OUTPUT_DIR containing <mode>/<as-of>.')
    parser.add_argument('--mode', choices=('export', 'public', 'databricks'), default='export')
    parser.add_argument('--as-of', default=config['as_of'], help='Date used when preparing the lookup checkpoint.')
    parser.add_argument('--case', action='append', choices=CASES, help='Repeat to select cases. The default runs all three.')
    parser.add_argument('--output', type=Path, default=Path('traveltime-live-receipt.json'))
    args = parser.parse_args(argv)
    credentials = ('TRAVELTIME_APP_ID', 'TRAVELTIME_API_KEY')
    if any(not os.environ.get(name, '').strip() for name in credentials):
        parser.error('Set TRAVELTIME_APP_ID and TRAVELTIME_API_KEY before running the live test.')
    try:
        store = Store(ROOT, args.mode, args.as_of, args.checkpoint_root)
        store.read('origin_index')
        store.read('lookup_candidates_blood_bank')
        if args.output.is_dir():
            raise ValueError('Choose a file path for the JSON receipt.')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=args.output.parent):
            pass
    except (ValueError, OSError) as error:
        parser.error(str(error))
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'checkpoint_date': args.as_of,
              'checkpoint_mode': args.mode, 'facility_sort': 'closest', 'cases': []}
    cases = list(dict.fromkeys(args.case or CASES))
    for index, case in enumerate(cases):
        try:
            record = check_case(store, config, case)
        except (OSError, ValueError) as error:
            record = {'case': case, 'outcome': 'inconclusive', 'blockers': [str(error)]}
        report['cases'].append(record)
        print(f"{case} — {record['outcome']}")
        for note in record.get('failures', []) + record.get('blockers', []):
            print(f'  {note}')
        if STOP_STATUSES & record.get('provider_status_counts', {}).keys():
            report['skipped_cases'] = cases[index + 1:]
            break
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Receipt written to {args.output}')
    return 1 if any(row['outcome'] != 'pass' for row in report['cases']) else 0


if __name__ == '__main__':
    if globals().get('__file__'):
        sys.exit(main())
    print('TravelTime did not run. Open non-ml/notebooks/10_check_traveltime_live.ipynb to run it in Databricks.')
