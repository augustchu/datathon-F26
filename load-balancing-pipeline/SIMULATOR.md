# Care finder and load simulator

This is a prototype, not a clinical triage or live bed-availability service. Start it from this folder after installing `requirements.txt`:

```powershell
py -m pip install -r requirements.txt
py -m streamlit run app.py
```

Upload the facility CSV in the sidebar and map its name, coordinates, capability/procedure/specialty/equipment/description fields, optional facility type, capacity, and source URL. Facility coordinates are spatially joined to the ADM2 polygons in `outputs/geoBoundaries-IND-ADM2.geojson`, but only polygons in `outputs/resolved_polygon_crosswalk.csv` are eligible. A point that falls outside, on a boundary, or in overlapping polygons is marked unmapped/ambiguous rather than silently assigned.

The future frontend's dropdown is scaffolded in `issue_catalog.py`. Each stable issue ID maps to a label, a service key that must be supported by facility evidence, a provider scope, and an NFHS proxy profile. The current entries are illustrative drafts; replace them with the team's issue list and reviewed mappings. `general_care` is a broad fallback that requires textual evidence of a general/multispecialty hospital or clinic and excludes records that appear specialist-only (for example dental-only providers). A facility-type column improves that check. General classification is still heuristic and should be reviewed against the facility source data.

## Recommendation logic

- Service matching is a transparent term lookup over the mapped text. A term in a structured service field receives a higher rules-based evidence strength than a match in unstructured text; multiple matched terms raise it modestly. This number is a heuristic, not a calibrated probability that the service is available.
- The matched terms, original text snippet, facility source URL, region assignment, travel estimate, score, capacity, load, and load penalty appear with each recommendation.
- For emergency requests, candidates below the service-evidence threshold are omitted and the closest remaining candidate by travel time is ranked first. Simulated load does not affect emergency ranking.
- Urgent and routine requests use a lower-is-better score: travel minutes (or a clearly labeled distance proxy), plus an evidence-strength penalty, plus a quadratic soft load penalty when capacity is provided. Load never makes a facility clinically ineligible. The weights are visible constants in `care_engine.py` and are demo parameters, not medically validated.
- The patient lookup first filters by the selected issue's facility-text evidence, then sends every matching facility with valid coordinates in one TravelTime Fast one-to-many request. The endpoint supports up to 100,000 destinations per search, so the current dataset fits in one call. The search limit is 10,800 seconds (3 hours); facilities beyond that are not ranked. The request uses the documented `driving+ferry` Fast mode for India and requests only travel time. The result is a typical weekday-morning estimate, not live traffic. Without credentials, the recommendation UI stops with a configuration message instead of silently substituting the straight-line proxy.

## Capacity and simulation assumptions

`capacity_units` is treated as a generic count of concurrent patient/resource slots. Map a capacity column only if its meaning is reasonably consistent across facilities. If capacity is blank, the app still ranks by evidence and travel but applies no capacity penalty. The simulation adds 2 units for emergency, 1.5 for urgent, and 1 for routine cases; these are illustrative resource weights.

The simulator samples synthetic district locations in proportion to modeled 2020 population times a relative NFHS-5 indicator proxy. Pediatric illness uses child ARI, diarrhea, and stunting indicators; maternal care uses institutional birth and C-section indicators; other mapped services use the nearest available proxy listed in `care_engine.py`. The proxy rescales district survey indicators relative to their national mean and clips the multiplier to 0.5–2.0. It does not estimate disease incidence, patient counts, or future demand. Districts without a resolved population mapping or usable geometry are omitted. This makes a reproducible scenario demonstration possible while preserving unknown areas as missing.

The Streamlit intensity slider is a direct case count from 0 to 300. The backend entry point `run_issue_scenario(..., issue_id, intensity, ...)` exposes the same control for a later frontend/API integration. It generates that many cases for the selected issue, then routes them sequentially.

Assignments are sequential: each synthetic case sees the load accumulated by earlier cases. This demonstrates one possible routing policy, not an equilibrium forecast. Random seed is exposed in the app.

## TravelTime API

The adapter calls TravelTime's Fast Time Filter endpoint for driving-time estimates. One-to-many matches the task of routing from one user's coordinates to many facility coordinates; the documented Fast endpoint supports up to 100,000 destinations and a 3-hour ceiling. India has full Fast driving coverage. Read the provider's current API and country coverage documentation: [Fast Time Filter](https://docs.traveltime.com/api/reference/time-filter-fast), [matrix quickstart](https://docs.traveltime.com/api/start/travel-time-distance-matrix), and [supported countries](https://docs.traveltime.com/api/overview/supported-countries). The code makes no API call until a user runs a search or chooses TravelTime for a simulation.

Both credentials are required. Set them in PowerShell before launching Streamlit (or add both values to a local `.streamlit/secrets.toml`, which must remain private):

```powershell
$env:TRAVELTIME_APP_ID = "your-application-id"
$env:TRAVELTIME_API_KEY = "your-api-key"
py -m streamlit run app.py
```

## Current limits / next data needed

`facilities dataset.csv` is now bundled and auto-loaded; an optional upload can override it. Its 10,088 rows include duplicate source IDs, some missing coordinates/capacity, and records whose address/state/name may conflict with the coordinates or service text. The app preserves rows with invalid coordinates as unmapped and surfaces address-state conflicts and repeated IDs for review. The `capacity` field is optional and missing values remain unknown; there is not yet a completed capacity prediction file. In particular, text evidence does not prove that a service is operational now, and the current rules do not resolve merged facility identities or prove that each source URL supports each individual claim. Do not use this prototype for actual emergency referral decisions.
