"""Generate the lookup and policy evaluation notebooks using the shared bootstrap."""

from build_analysis_notebooks import code, markdown, notebook


def build():
    notebook('06b_prepare_lookup', 'Prepare lookup snapshots', '''
        Run this after the branch audit, service and assessment evidence, and field updates. Search uses
        prepared place and service checkpoints. It reads the existing routing flags and
        recommendations use the service evidence available on the configured snapshot date.
    ''', [
        markdown('''
            ## Materialize the stable decisions

            The data preparation resolves eligible place centers and known ambiguity,
            attaches cited contacts, service corroboration from 04b and assessment evidence
            from 04c, and organizes each
            service's quotes, field estimates and load inputs. Review the candidate totals
            before running a search. Refresh these checkpoints when upstream data or rules change.
        '''),
        code('''
            from facility_tool.query_assets import prepare_lookup_assets

            profile = prepare_lookup_assets(STORE, config)
            show(profile)
        '''),
    ])
    notebook('07_compare_and_feedback', 'Compare the three recommendation modes', '''
        Run notebook 06b to prepare the place and service snapshots before searching.
        Enter a service and a starting location. Each mode shows up to five ranked recommendations.
        The default example uses fictional facilities and straight-line distance.
        Persistent moderation can be enabled with a private database path.
    ''', [
        markdown('''
            ## One search, three modes
            The same documented service rules feed all three modes. `confirmed` uses documented
            fields, `ml` reads authorized ML model outputs if available, and `regression` reads
            authorized classical model outputs if available. Model-only service scores appear as
            leads to verify. The demo estimates were written by hand to exercise the distinction.

            Set `CONSIDER_EQUITY_SCORE` to `True` to use equity scores in balanced sorting.

            <details>
            <summary>What's that?</summary>
            Women and people from marginalized groups can receive worse care. The equity score
            combines patient-reported respect and clinical outcomes to help choose between similar hospitals.
            </details>
        '''),
        code('''
            from datetime import date

            from facility_tool.equity import load_paired_equity_evidence, synthetic_equity_evidence
            from facility_tool.lookup import lookup, side_by_side
            from facility_tool.response import LOAD_OPT_IN_LABEL, new_alternatives, record_call, report_unconfirmed_call

            CONCERN = config['lookup']['service']  # Example: 'I need dialysis'
            LOCATION = config['lookup']['location']  # City, district, PIN or latitude,longitude
            # Future checkbox label; the logic stays off unless explicitly enabled.
            print(LOAD_OPT_IN_LABEL)
            SPREAD_DEMAND = False
            USE_TRAVELTIME = config['traveltime']['enabled']  # Set False for an offline distance-only comparison.
            TRANSPORT_MODE = config['traveltime']['mode']  # driving, public_transport, walking, cycling
            ROUTE_SORT = None  # Default distance, or time for public transport. Can also choose 'time'.
            FACILITY_SORT = config['lookup']['facility_sort']  # balanced or closest
            TRAVEL_WEIGHT = config['lookup']['travel_weight']
            MAX_EXTRA_KM = config['lookup']['max_extra_km']
            MAX_EXTRA_MINUTES = config['lookup']['max_extra_minutes']
            # Optional provider CSV of fresh same-service availability for a common window.
            MEASURED_LOAD_CSV = os.environ.get('DATATHON_MEASURED_LOAD_CSV')
            CONSIDER_EQUITY_SCORE = False
            EQUITY_EVIDENCE_CSV = None  # Optional reviewed clinical outcome and respect aggregates for real data.
            equity_evidence = None
            if CONSIDER_EQUITY_SCORE and MODE == 'demo':
                equity_evidence = synthetic_equity_evidence(
                    'dialysis', 'fdr:demo-12', 'fdr:demo-24', as_of=date.fromisoformat(config['as_of']),
                )
            elif CONSIDER_EQUITY_SCORE and EQUITY_EVIDENCE_CSV:
                equity_evidence = load_paired_equity_evidence(EQUITY_EVIDENCE_CSV)

            settings = dict(concern=CONCERN, location=LOCATION, spread_demand=SPREAD_DEMAND,
                            traveltime_enabled=USE_TRAVELTIME, travel_mode=TRANSPORT_MODE, route_sort=ROUTE_SORT,
                            facility_sort=FACILITY_SORT, travel_weight=TRAVEL_WEIGHT,
                            max_extra_km=MAX_EXTRA_KM, max_extra_minutes=MAX_EXTRA_MINUTES,
                            measured_load=pd.read_csv(MEASURED_LOAD_CSV) if SPREAD_DEMAND and MEASURED_LOAD_CSV else None,
                            consider_equity=CONSIDER_EQUITY_SCORE, equity_evidence=equity_evidence)
            result = lookup(STORE, config, **settings)
            print(f"{result['service']} | {result['origin'].label} | {result['origin'].basis}")
            print(f"Transport: {result['transport_mode']} | Requested sort: {result['route_sort']}")
            print(f"Facility preference: {result['facility_sort']}")
            print(result['routing']['message'])
            if CONSIDER_EQUITY_SCORE:
                print(result['summary'].iloc[0]['equity_ranking_reason'])
            if MODE == 'demo':
                print('Fictional facilities, values, and contacts. Do not use them for care decisions.')
            show(result['summary'])
            show(side_by_side(result['rows']))
        '''),
        markdown('''
            ## Reasons, contacts and travel
            TravelTime checks every branch that passes the service and location rules. The starting
            location is the origin for route estimates. The standard API considers trips of up to
            four hours. The routing summary shows whether estimates cover the full comparison.
            Driving sorts by route distance by default. Select walking, cycling, or public transport
            to compare the same search. Public transport sorts by estimated travel time.
            If the top driving option lacks a route for the requested transport, one of the five
            slots can show that branch with its driving route and a note. If no requested-mode
            routes are available, the list can show up to five labeled driving alternatives.
            `travel_mode` shows how to reach each branch in the list. Offline comparisons and
            fictional demos use straight-line distance. Within the same service evidence tier,
            balanced ranking gives limited travel credit for independent service corroboration and
            a current external assessment. Compare `travel_only_rank`
            with `baseline_rank` before considering opt-in load balancing. These credits are
            ranking preferences in travel units. The fit score uses
            the accepted service citation, validity, precision of the origin, and proximity.
            Equity scores use paired clinical outcomes and patient-reported respect from comparable
            service cohorts. When the evidence can distinguish nearby branches with comparable
            documented services, scores can adjust their order. Compare `pre_equity_rank` with
            `rank` and use `equity_evidence` to see the paired measures and source. For separately
            shown driving options, compare `pre_equity_rank` with `driving_rank`. The sample
            equity scores in demo mode are fictional. Without comparable evidence the ordinary
            order applies, as it does in time-critical searches and closest sorting.
            A listed phone can be outdated. Call the branch to verify the requested service
            and availability before traveling. Inspect the complete `trace` for excluded candidates.
        '''),
        code('''
            summary_columns = ['mode', 'kind', 'rank', 'baseline_rank', 'travel_only_rank', 'name',
                               'pre_equity_rank', 'equity_score', 'equity_score_lower',
                               'equity_score_upper', 'equity_evidence',
                               'distance_km', 'route_distance_km', 'travel_minutes',
                               'travel_mode', 'route_note', 'requested_mode_route_status', 'driving_rank',
                               'travel_source_url', 'ranking_travel_metric', 'ranking_travel_reason',
                               'ranking_policy_reason', 'corroboration_credit', 'assessment_credit',
                               'quality_credit_unit', 'balanced_travel_score', 'quality_assessment_status',
                               'quality_evidence',
                               'patient_rating', 'patient_rating_min', 'patient_rating_max', 'patient_rating_response_count',
                               'patient_rating_source_url', 'patient_reviews_search_url',
                               'patient_ranking_reason', 'patient_experience_credit',
                               'location_source_urls', 'location_review_flags',
                               'fit_score', 'fit_score_basis', 'phone_number',
                               'phone_source_urls', 'call_to_verify', 'call_response', 'excerpt',
                               'verification_status', 'supporting_publishers', 'supporting_urls',
                               'source_url', 'scope', 'validity', 'reason', 'load_status', 'load_available_units',
                               'load_unit', 'load_as_of', 'load_window_start', 'load_window_end', 'load_source_url']
            show(result['rows'][summary_columns], limit=25)
            route_summary = result['route_status']
            if not route_summary.empty:
                counts = route_summary.groupby(['travel_mode', 'travel_status'], dropna=False).size()
                show(counts.rename('candidates').reset_index())
            show(result['trace'], limit=15)
        '''),
        markdown('''
            ## Follow up after a call
            A future yes/no button should call `record_call` and rerun the same query with that
            feedback. A No removes this branch for the selected service across all three modes
            and replenishes the list from the next supported options. A Yes marks the user's
            response for this search. A No can also create a report in a private moderation stack,
            where moderators see the newest open reports first and record a disposition. Report
            creation and review leave the original evidence and training data intact.
            In real mode, set a facility ID and response after you call. The demo simulates a No.
        '''),
        code('''
            feedback = ()
            CALL_FACILITY_ID = None
            CALL_CONFIRMED = None
            MODERATION_DB = None  # An optional private SQLite path, outside Git
            REPORT_REASON = 'service_not_confirmed'  # Or 'poor_fit'
            REPORT_SUBMISSION_ID = None  # Supply the same token on retrying one report
            eligible = result['rows'].loc[result['rows']['mode'].eq('confirmed') &
                                          result['rows']['kind'].eq('recommendation')]
            if MODE == 'demo' and not eligible.empty:
                CALL_FACILITY_ID = eligible.iloc[0]['facility_id']
                CALL_CONFIRMED = False
                print('Simulating a No on the first fictional facility.')
            if CALL_FACILITY_ID:
                if CALL_FACILITY_ID not in set(result['rows']['facility_id']):
                    raise ValueError('Choose a facility ID shown in this search.')
                feedback = record_call(feedback, result['service'], CALL_FACILITY_ID, confirmed=CALL_CONFIRMED)
                revised = lookup(STORE, config, **settings, feedback=feedback)
                added = new_alternatives(result['rows'], revised['rows'])
                print('Excluded for this caller and service:', revised['excluded_by_caller'])
                print(revised['routing']['message'])
                if not CALL_CONFIRMED and added.empty:
                    print('The search returned no additional recommendation.')
                show(added[summary_columns] if not added.empty else revised['rows'][summary_columns])
        '''),
        code('''
            # Keep persistence separate so alternatives are already available if reporting fails.
            if MODERATION_DB and CALL_FACILITY_ID and CALL_CONFIRMED is False:
                from facility_tool.moderation import ModerationStack
                stack = ModerationStack(MODERATION_DB, allowed_services=config['service_aliases'])
                report = report_unconfirmed_call(feedback, result['service'], CALL_FACILITY_ID,
                                                 moderation_stack=stack, reason=REPORT_REASON,
                                                 submission_id=REPORT_SUBMISSION_ID)
                print(f'Moderation report {report.report_id} | {report.status}')
        '''),
    ])
    notebook('08_load_policy_scenarios', 'Test the opt-in load selector', '''
        Compare historical workload scenarios with compatible service throughput. Use the sourced
        March 2025 dialysis scenario, a supplied CSV, or the built-in fictional example. These are
        planning comparisons using straight-line distance.
    ''', [
        markdown('''
            ## Run matched comparisons
            Keep the service, facility evidence, and location fixed. The opt-in selector considers
            only adequately supported options of comparable scope within the extra travel limit.
            Its final score can trade extra travel against a condition-specific demand/capacity
            ratio. Compare only compatible unit, period, and source basis. Scenario or historical
            pressure is labeled as such. Total beds alone won't stand in for dialysis slots.
            The dialysis input uses observed March 2025 haemodialysis sessions and predicted monthly
            throughput. Each predicted capacity is flagged and linked to its source and method.
            See [the implementation report](../LOAD_BALANCING.md) for the source and evaluation.
        '''),
        code('''
            import json

            from facility_tool.lookup import lookup, resolve_service
            from facility_tool.recommend import simulate_spread

            CONCERN = config['lookup']['service']
            LOCATION = config['lookup']['location']
            SCENARIO_CSV = None  # Optional table of comparable demand and capacity.
            USE_DIALYSIS_EVIDENCE = MODE != 'demo' and resolve_service(CONCERN, config['service_aliases']) == 'dialysis'
            REQUESTS = 100  # Fictional suggestions in the optional replay. No patient records.
            if SCENARIO_CSV:
                pressure = pd.read_csv(SCENARIO_CSV)
            elif USE_DIALYSIS_EVIDENCE:
                evidence = json.loads((ROOT / 'non-ml/caseload/load_evidence.json').read_text())
                pressure = pd.DataFrame(evidence['pressure_inputs'])
                show(pressure[['facility_id', 'demand', 'capacity', 'capacity_predicted', 'period']])
                replay = json.loads((ROOT / 'non-ml/caseload/load_planning_replay.json').read_text())
                show(pd.DataFrame([replay['summary']]))
            else:
                pressure = STORE.read('pressure_scenarios', optional=True)
            common = dict(concern=CONCERN, location=LOCATION, traveltime_enabled=False,
                          pressure_inputs=pressure, load_context='planning')
            baseline = lookup(STORE, config, **common, spread_demand=False)
            opt_in = lookup(STORE, config, **common, spread_demand=True)
            columns = ['mode', 'name', 'rank', 'distance_km', 'extra_km_vs_baseline', 'pressure_ratio',
                       'pressure_capacity_predicted', 'pressure_capacity_method', 'pressure_capacity_source_url',
                       'pressure_grade', 'travel_penalty', 'pressure_penalty', 'weighted_score', 'weighted_status']
            show(baseline['summary'])
            show(opt_in['summary'])
            show(opt_in['rows'].loc[opt_in['rows']['kind'].eq('recommendation'), columns], limit=25)
        '''),
        markdown('''
            ## Sensitivity and a separate capacity-weighted replay
            Compare first choices across multiple travel weights. The replay demonstrates how
            a fixed batch of hypothetical suggestions might be spread across sites. Its output
            measures recommendations shown, with no claim about visits or actual crowding.
        '''),
        code('''
            if pressure.empty:
                print('No comparable demand/capacity inputs. The ordinary ranking is available.')
            else:
                sensitivity = []
                for weight in (0.25, 0.5, 0.75):
                    scenario = lookup(STORE, config, **common, spread_demand=True, travel_weight=weight)
                    first = scenario['rows'].loc[scenario['rows']['kind'].eq('recommendation') &
                                                 scenario['rows']['rank'].eq(1)]
                    sensitivity.append(first[['mode', 'name', 'extra_km_vs_baseline', 'pressure_ratio',
                                              'weighted_score', 'weighted_status']].assign(travel_weight=weight))
                show(pd.concat(sensitivity, ignore_index=True))
                if MODE == 'demo' or SCENARIO_CSV:
                    base_candidates = baseline['trace'].loc[baseline['trace']['mode'].eq('confirmed')]
                    show(simulate_spread(base_candidates, pressure, baseline['service'], requests=REQUESTS,
                                         max_extra_km=config['lookup']['max_extra_km']))
        '''),
        markdown('''
            ## Historical replay across Care needed options

            The checked replay aligns published reports and backtested caseload predictions by
            branch, activity definition and period. Its coverage distinguishes origins screened
            from cases with enough evidence to compare rankings. See the [replay report](../caseload/CARE_OPTIONS_REPLAY.md)
            for prediction flags, inputs and reproduction commands.
        '''),
        code('''
            care_replay = json.loads((ROOT / 'non-ml/caseload/care_options_replay.json').read_text(encoding='utf-8'))
            show(pd.DataFrame([care_replay['summary']]))
            show(pd.DataFrame(care_replay['coverage']), limit=25)
            changes = [dict(care_needed=group['care_needed'], period=group['period_start'],
                            origin=case['origin']['name'], **move)
                       for group in care_replay['cohorts'] for case in group['cases']
                       for move in (case['rank_changes'] or [])]
            if changes:
                show(pd.DataFrame(changes))
            else:
                print('No supported ranking changes in the expanded historical replay.')
        '''),
        markdown('''
            ## Network-wide caseload scenarios

            All release rows have numerical scenarios. A conditional value assumes a branch could
            provide the specified service and does not verify that it does. This notebook compares
            only branches with both independent lookup eligibility and exact historical scope evidence.
            The [network report](../caseload/NETWORK_CASELOAD.md) gives a separate explicit hypothetical mode.
        '''),
        code('''
            from facility_tool.network_load import read_network_pressure, replay_network_load

            NETWORK_FORECASTS = ROOT / 'non-ml/caseload/network_caseload.csv.gz'
            RUN_NETWORK_PLANNING = False
            if RUN_NETWORK_PLANNING:
                options = dict(concern=CONCERN, location=LOCATION, facility_sort='closest',
                               traveltime_enabled=False, load_context='planning', network_forecasts=NETWORK_FORECASTS)
                network_ranked = lookup(STORE, config, **options, spread_demand=True)
                show(network_ranked['summary'])
                load_status = network_ranked['summary'].get('load_status', pd.Series(dtype=str))
                if load_status.isin(['non_emergency_only', 'no_comparable_load_modality']).any():
                    print('The request does not qualify for this planning scenario.')
                else:
                    network_trace = network_ranked['trace'].query("mode == 'confirmed' and decision == 'eligible'")
                    network_inputs = read_network_pressure(NETWORK_FORECASTS, network_ranked['service'],
                                                           network_trace['facility_id'])
                    network_inputs = network_inputs.loc[network_inputs['service_evidence'].eq('reported_activity_in_scope')]
                    supported = network_trace.loc[network_trace['facility_id'].isin(network_inputs['facility_id'])]
                    show(replay_network_load(supported, network_inputs, requests=REQUESTS), limit=25)
        '''),
    ])


if __name__ == '__main__':
    build()
