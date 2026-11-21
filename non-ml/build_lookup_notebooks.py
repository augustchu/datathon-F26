"""Generate the lookup and policy evaluation notebooks using the shared bootstrap."""

from build_analysis_notebooks import code, markdown, notebook


def build():
    notebook('06b_prepare_lookup', 'Prepare lookup snapshots', '''
        Run this after the branch audit, service evidence, and field updates. Search uses
        prepared place and service checkpoints. It reads the existing routing flags and
        recommendations use the service evidence available on the configured snapshot date.
    ''', [
        markdown('''
            ## Materialize the stable decisions

            The data preparation resolves eligible place centers and known ambiguity,
            attaches citable contacts and service corroboration from 04b, and organizes each
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
        This notebook exercises the tool logic in Python. Its default example is fictional.
        Persistent moderation can be enabled with a private database path.
    ''', [
        markdown('''
            ## One search, three modes
            The same documented service rules feed all three modes. `confirmed` uses documented
            fields, `ml` reads authorized ML model outputs if available, and `regression` reads
            authorized classical model outputs if available. Model-only service scores appear as
            leads to verify. The demo estimates were written by hand to exercise the distinction.
        '''),
        code('''
            from facility_tool.lookup import lookup, side_by_side
            from facility_tool.response import LOAD_OPT_IN_LABEL, new_alternatives, record_call, report_unconfirmed_call

            CONCERN = config['lookup']['service']  # Example: 'I need dialysis'
            LOCATION = config['lookup']['location']  # City, district, PIN or latitude,longitude
            # Future checkbox label; the logic stays off unless explicitly enabled.
            print(LOAD_OPT_IN_LABEL)
            SPREAD_DEMAND = False
            USE_TRAVELTIME = config['traveltime']['enabled']  # Uses credentials if configured; sends coordinates.
            TRANSPORT_MODE = config['traveltime']['mode']  # driving, public_transport, walking, cycling
            ROUTE_SORT = None  # Default distance, or time for public transport. Can also choose 'time'.
            TRAVEL_WEIGHT = config['lookup']['travel_weight']
            MAX_EXTRA_KM = config['lookup']['max_extra_km']
            MAX_EXTRA_MINUTES = config['lookup']['max_extra_minutes']
            # Optional CSV of comparable service-specific demand and capacity for this snapshot.
            PRESSURE_CSV = None

            settings = dict(concern=CONCERN, location=LOCATION, spread_demand=SPREAD_DEMAND,
                            traveltime_enabled=USE_TRAVELTIME, travel_mode=TRANSPORT_MODE, route_sort=ROUTE_SORT,
                            travel_weight=TRAVEL_WEIGHT,
                            max_extra_km=MAX_EXTRA_KM, max_extra_minutes=MAX_EXTRA_MINUTES,
                            pressure_inputs=pd.read_csv(PRESSURE_CSV) if PRESSURE_CSV else None)
            result = lookup(STORE, config, **settings)
            print(f"{result['service']} | {result['origin'].label} | {result['origin'].basis}")
            print(f"Transport: {result['transport_mode']} | Requested sort: {result['route_sort']}")
            if MODE == 'demo':
                print('Fictional facilities, values, and contacts. Do not use them for care decisions.')
            show(result['summary'])
            show(side_by_side(result['rows']))
        '''),
        markdown('''
            ## Reasons, contacts and travel
            Driving uses estimated route distance by default. Select walking, cycling or public
            transport to compare the same search. Public transport uses estimated travel time.
            Straight-line kilometers remain visible as a separate fallback. Check the recorded
            ranking metric and reason when routes are missing. The fit score uses
            the accepted service citation, validity, precision of the origin, and proximity.
            A listed phone can be outdated. Call the branch to verify the requested service
            and availability before traveling. Inspect the complete `trace` for excluded candidates.
        '''),
        code('''
            summary_columns = ['mode', 'kind', 'rank', 'name', 'distance_km', 'route_distance_km', 'travel_minutes',
                               'travel_mode', 'travel_source_url', 'ranking_travel_metric', 'ranking_travel_reason',
                               'location_source_urls', 'location_review_flags',
                               'fit_score', 'fit_score_basis', 'phone_number',
                               'phone_source_urls', 'call_to_verify', 'call_response', 'excerpt',
                               'verification_status', 'supporting_publishers', 'supporting_urls',
                               'source_url', 'scope', 'validity', 'reason', 'weighted_score']
            show(result['rows'][summary_columns], limit=25)
            route_summary = result['route_status']
            if not route_summary.empty:
                show(route_summary.groupby('travel_status', dropna=False).size().rename('candidates').reset_index())
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
                if not CALL_CONFIRMED and added.empty:
                    print('No additional supported facilities within the current search settings.')
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
        Keep this analysis separate from evidence and model fitting. Use the built-in fictional
        example or explicitly supply compatible service demand and capacity observations or
        assumptions. This notebook makes no live calls or assignments.
    ''', [
        markdown('''
            ## Run matched comparisons
            Keep the service, facility evidence, and location fixed. The opt-in selector considers
            only adequately supported options of comparable scope within the extra travel limit.
            Its final score can trade extra travel against a condition-specific demand/capacity
            ratio. Compare only compatible unit, period, and source basis. Scenario or historical
            pressure is labeled as such. Total beds alone won't stand in for dialysis slots.
            For context, [Moore and Huang](https://pmc.ncbi.nlm.nih.gov/articles/PMC12107644/)
            studied redistribution with known appointment availability and a travel penalty.
        '''),
        code('''
            from facility_tool.lookup import lookup
            from facility_tool.recommend import simulate_spread

            CONCERN = config['lookup']['service']
            LOCATION = config['lookup']['location']
            SCENARIO_CSV = None  # Optional fixture with facility_id, service, demand, capacity, unit, period, basis, source_url.
            REQUESTS = 100  # Fictional suggestions in the optional replay. No patient records.
            pressure = pd.read_csv(SCENARIO_CSV) if SCENARIO_CSV else STORE.read('pressure_scenarios', optional=True)
            common = dict(concern=CONCERN, location=LOCATION, traveltime_enabled=False, pressure_inputs=pressure)
            baseline = lookup(STORE, config, **common, spread_demand=False)
            opt_in = lookup(STORE, config, **common, spread_demand=True)
            columns = ['mode', 'name', 'rank', 'distance_km', 'extra_km_vs_baseline', 'pressure_ratio',
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
    ])


if __name__ == '__main__':
    build()
