# Map restore and import review

The restored UI comes from `a141a75` on November 21, 2026 at 11:06 p.m. ET. It is the last map-backed Facility Finder revision before `93d2028` introduced the Databricks prototype five minutes later. The earlier `f2a1e0e` map layout included a model estimator. The selected revision removed that estimator, retained source-reported beds and fit weights, boxed the search panels, and added sorting by distance, fit, beds, or occupancy.

The Databricks prototype's `app.yaml` launched a separate page titled “Find the right care,” with sidebar controls and result expanders. That entry point never rendered the map. This restore switches the entry point to `classic_dashboard.py`, using the familiar Service, Location, and Options panels, result cards, numbered pins, and contact popups. The map sits alongside the results and now shows an India overview before a search. An empty search result can still show the origin pin.

The dashboard defaults to the prepared Databricks snapshot when a warehouse is configured. The local preview defaults to the clearly labeled synthetic demo. The original facilities export and dashboard-schema CSV options are still available.

## Import decisions

The 23 selected changes are implemented. Recommended is the default and combines travel, service evidence, occupancy, regional demand, and data confidence when those inputs are available. Emergency uses travel and data confidence. Recommended uses TravelTime by default with the prepared snapshot and falls back to straight-line scoring when routing is unavailable. The existing distance, fit, beds, and occupancy sorts keep their original definitions. Equity runs after the selected sort and uses reviewed, comparable evidence. The shipped evidence file has no real facility records, and the maternity demo uses clearly labeled fictional evidence.

| # | Change | Where it changed | Decision |
| --- | --- | --- | --- |
| 1 | Add an urgency selector for Routine, Urgent, or Emergency. | `d57c86a`, `93d2028` | Included |
| 2 | Add the alternate Recommended sort with the care engine's service evidence, straight-line travel proxy, occupancy penalty, and score breakdown. | `d57c86a` | Included |
| 3 | Make Recommended the default sort. Requires item 2. | `d57c86a` | Included |
| 4 | Add Advanced options for selecting and ordering personal priorities, plus a Ranked criteria sort and breakdown. | `d57c86a` | Included |
| 5 | Replace adjustable fit weights with fixed weights. | `d57c86a` | Unselected |
| 6 | Remove Beds from the sort menu. | `d57c86a` | Unselected |
| 7 | Hide source-reported beds in result cards, explanations, and map popups. | `d57c86a` | Unselected |
| 8 | Add the equity toggle and ranking adjustment for comparable, non-time-critical results, with its explanatory text. | Uncommitted map-dashboard change | Included |
| 9 | Show equity scores, previous ranks, source measures, and uncertainty. Requires item 8. | Uncommitted map-dashboard change | Included |
| 10 | Add the fictional maternity equity demo, including its revised facility coordinates and example search. | Uncommitted map-dashboard change | Included |
| 11 | Connect the prepared Databricks facility snapshot as a data source, with Delta/SQL loading. | `93d2028` | Included |
| 12 | Display the snapshot preparation date and facility count. Requires item 11. | `93d2028` | Included |
| 13 | Use the cleaned newest-source release for facility names, locations, coordinates, and source service text. | `ff7dd5e`, `7c694a5`, `b649ce3` | Included |
| 14 | Switch to the prototype's shorter service menu, including General care. | `93d2028`, `ff7dd5e` | Unselected |
| 15 | Use one city/district/PIN field prefilled with Jaipur and a separate coordinate toggle. | `93d2028` | Unselected |
| 16 | Re-rank immediately when controls change after an initial search. | `93d2028` | Included |
| 17 | Apply the prototype's source-link, coordinate, and negation checks for eligibility and show its no-match message when no record qualifies. | `93d2028` | Unselected |
| 18 | Show matched service terms, excerpts, and linked facility sources in results. | `93d2028` | Included |
| 19 | Show region-match flags and the named district/NFHS basis where available. | `93d2028`, `01b2a3e` | Unselected |
| 20 | Add a district-population scenario with a 0–2% slider, equal case allocation, and a routine/urgent load penalty. It replaced a concurrent-case simulation in the prototype. | `01b2a3e`, `ff7dd5e` | Included |
| 21 | Show the scenario's modeled district population, case totals, cases allocated to each facility, bed proxy, and load ratio. Requires item 20. | `01b2a3e`, `ff7dd5e` | Included |
| 22 | Show capacity from the release, including estimate labels, intervals, source, withheld values, and discrepancies with the Marketplace count. | `ff7dd5e`, `b649ce3` | Included |
| 23 | Use released capacity for the load calculation and a disclosed 50-bed fallback when capacity is unavailable. Requires item 20. | `93d2028`, `ff7dd5e` | Included |
| 24 | Show the data-quality confidence score and tier on cards. | `ff7dd5e` | Included |
| 25 | Apply the prototype's confidence penalty to rankings, including emergency results. | `ff7dd5e` | Included |
| 26 | Prefer the release's phone number when available and add a phone-source link. The restored cards already have a phone field. | `ff7dd5e` | Included |
| 27 | Add facility website links. | `ff7dd5e` | Included |
| 28 | Offer TravelTime driving estimates with straight-line fallback, excluding unreachable destinations when routing works. | `ff7dd5e` | Included |
| 29 | Enable TravelTime by default. Requires item 28. | `ff7dd5e` | Included |
| 30 | Use expandable result cards, with the first card expanded. | `93d2028` | Unselected |
| 31 | Use the prototype's “Find the right care” title and “Find facility options” button copy. | `93d2028` | Unselected |
| 32 | Add the prototype's availability warning and its “How this prototype decides” explanation, updated for whichever rules are selected. | `93d2028`, `01b2a3e`, `ff7dd5e` | Included |

The release also changed its CSV formatting, corrected catalog and file lookup paths, and revised snapshot preparation, matching, and validation. Those changes remain in the prototype's source. Their relevant effects on displayed fields and scoring are covered above.

## Run or deploy the restore

For a local preview, install `requirements.txt` and run `streamlit run classic_dashboard.py` from `first-prototype/`. Select the synthetic demo, enter PIN `411001`, and click **Find facilities**. The map should show a red origin marker and numbered facility markers in the same order as the result cards.

Deploy the `first-prototype/` directory to the existing app. Its manifest starts the classic page. For a comparison, the newer prototype can still be run with `streamlit run dashboard.py` in a configured Databricks environment.
