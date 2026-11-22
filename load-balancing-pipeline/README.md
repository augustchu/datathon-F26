# India population enrichment pipeline

This workspace contains the NFHS-5 / WorldPop district population pipeline and a care finder/load simulation prototype. It uses the supplied NFHS-5 district indicators, Census 2011 district table, geoBoundaries ADM2 ZIP, WorldPop 2020 raster, PIN directory, 2020 state population projections, and `facilities dataset.csv`. The facility dataset is available, but recommendation behavior still needs review against its noisy source records; facility capacity predictions are not present yet.

## Install

    py -m venv .venv
    .\.venv\Scripts\Activate.ps1
    py -m pip install -r requirements.txt

## Prepare and review district matches

    py population_pipeline.py prepare

The script extracts the ADM2 GeoJSON, spatially assigns states using unique geocoded PIN/district combinations, and writes:

- outputs/polygon_state_votes.csv: PIN vote count, state vote share, and district mode for each polygon.
- outputs/district_crosswalk.csv: within-state district match proposals using the polygon name and PIN-directory district mode.

PIN directory rows are office-level, so the script uses one median point per PIN/state/district combination and counts a PIN at most once per polygon. The generated state vote table flags fewer than 3 PINs and state vote shares below 80%. After review, the crosswalk status can be approved or can describe a single conflicting source. Finalization includes approved rows and explicit single-source conflicts only when the other available district labels agree. It also supports a documented subdistrict-to-parent NFHS assignment and unique exact polygon+PIN name corrections. Rows still marked review, reject/rejected, or all disagree are excluded.

## Supply state projections and finalize

The supplied india_state_population_2020.csv contains the 1 March 2020 state totals in persons. The script ignores the national row and avoids double-counting separate pre-merger DNH/DD rows alongside the combined UT row.

    py population_pipeline.py finalize

Outputs:

- outputs/nfhs_district_population_2020.csv: raw raster sum, state-scaled modeled population, under-15 estimate, confidence flags, and crosswalk metadata.
- outputs/state_scaling_diagnostics.csv: state totals and scale factors. If any polygon still has no defensible state, estimates retain a partial-coverage flag; interpret them as approximate.
- outputs/census_2011_validation_candidates.csv: exact name/state Census comparisons for manual boundary-vintage review; do not interpret post-split matches as district-level validation.
- outputs/resolved_polygon_crosswalk.csv: the mappings actually included.
- outputs/excluded_crosswalk_rows.csv and outputs/excluded_unassigned_state_polygons.csv: unresolved rows retained for audit.

NFHS suppressed * values stay null and parenthesized values are marked low-confidence. PIN-radius demand points are a separate follow-up; the current PIN data is used only to assign administrative states and suggest district-name matches.

## Run the care finder prototype

After installing requirements and producing the population outputs, run:

    py -m streamlit run app.py

Upload the facility CSV and map columns in the sidebar. The app assigns facilities to resolved NFHS districts, ranks by service-text evidence and travel, and can run sequential synthetic load scenarios. See [SIMULATOR.md](SIMULATOR.md) for the scoring rules, assumptions, TravelTime setup, and limitations.

Medical issue dropdown IDs and their service/NFHS mappings are scaffolded in `issue_catalog.py`; replace the draft examples with the final user-facing issue list. The numeric intensity slider controls the exact number of synthetic cases, and `simulation.run_issue_scenario` exposes the same behavior as a Python backend entry point for later Streamlit integration.
