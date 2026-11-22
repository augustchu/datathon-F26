# Facility recommendation benchmark

This panel contains 16 service-and-place searches across India. Eight start in major cities and eight start in smaller district or town catchments that serve rural populations. Four regression queries revisit work the team already used during development. Twelve queries form a separate evidence panel. The settings cover driving, walking, cycling, and public transport, with specific cases for similar branch names, blood banks versus storage units, Level III NICU, emergency C-section, and sparse coverage.

[Search inputs](queries.json) · [Reference judgments and sources](references.json) · [Evaluation log template](EVALUATION_LOG.md)

## Score the finished algorithm

1. Run the same algorithm and settings against every search in `queries.json`. Pass the service, origin, and requested transport mode. Keep optional load and equity settings off for the ordinary comparison and record any separate scenario run. Save what the user actually sees, in display order, with one list per query and scoring mode. Leave unverified leads out of that list.
2. Export the recommendations as JSON using the format below. Record the algorithm version, dataset snapshot, routing policy, and time. Explicitly record abstentions, unsupported inputs, and errors. If the app prompts for a more specific place, keep that first attempt in the run. A retry with a PIN or coordinates can be recorded separately.
3. Starting at the repo root, run the scorer. It only needs Python 3.10+ and its standard library.

```sh
python3 recommendation-benchmark/evaluate.py \
  --predictions /path/to/final-predictions.json \
  --output /path/to/final-evaluation.json
```

Use `--baseline /path/to/baseline-predictions.json` to compare with another run on the same panel, such as keyword matching and nearest-first ranking. Use the same source snapshot, query settings, and travel basis for a like-for-like comparison. Report offline, saved-route, and live results with their own labels. The full JSON includes input SHA-256 hashes, group summaries, and per-query findings. The default is top five. Set `--top-k` to study a different list length.

### Prediction format

```json
{
  "run": {
    "id": "final-confirmed-01",
    "algorithm": "Git commit and settings",
    "snapshot": "Version of prepared data",
    "routing": "TravelTime driving route km and mode-specific routing",
    "recorded_at": "2026-11-22T14:00:00Z"
  },
  "predictions": [
    {
      "query_id": "urban-01",
      "outcome": "recommendations",
      "latency_ms": 215,
      "results": [
        {
          "facility_id": "example-facility-id",
          "name": "Example Facility",
          "pin": "123456",
          "source_url": "https://example.org/service-evidence",
          "evidence_excerpt": "Example of the excerpt the app displayed"
        }
      ]
    }
  ]
}
```

The snippet uses fictional values to show the format. For captured results, include the URL and excerpt actually presented by the app. Order is rank. An exact stable branch ID works by itself alongside the name. Without an ID, use the exact branch name and a six-digit PIN string. For an `abstain`, `unsupported_query`, or `error` outcome, keep the list empty and include a `reason`. A missing query row is counted as missing output. Metadata, reason, and latency are optional in the input and useful for reproducing a run.

For travel diagnostics, set `travel_basis` to `route_distance` or `straight_line` with `travel_value` in km, or `route_time` with the value in minutes. Use `unavailable` if a route estimate is missing. Include the actual `travel_mode` for a routed result. If the product displays a car-only label, use `car_only: true` and include the requested mode's `requested_mode_status`. The scorer checks that it was a driving route and that the requested mode was explicitly `not_routed_within_limit`. The optional top-result travel gap compares only like-for-like values among returned facilities.

### Final Databricks app replay

Once the app version is ready to evaluate, `run_first_prototype.py` can run its ranking functions on an authorized frozen Marketplace checkpoint. It uses the published capacity release, reviewed service evidence, and location holds from the checked-out version. It records unsupported menu items and transport modes as coverage gaps. The benchmark uses five results, a 0% population scenario, and emergency severity for the emergency C-section query.

```sh
python3 recommendation-benchmark/run_first_prototype.py \
  --raw-facilities /path/to/raw_facilities.jsonl \
  --as-of 2026-11-22 \
  --run-id final-app-replay \
  --output /path/to/final-predictions.json
```

The replay defaults to straight-line ranking. To use live driving estimates from this runtime, supply the existing TravelTime credentials in the environment and pass `--use-traveltime`. The receipt records the actual route basis and any fallback. A local replay exercises the app code on the recorded snapshot.

### Existing notebook adapter

`run_lookup.py` reads existing prepared lookup checkpoints and exports the selected evidence mode. It reuses the current notebook code, runs offline, and keeps its raw output in the location you specify. The checkpoint root is the directory that contains the input-mode folder, such as `export/2026-11-21`. If the checkpoints are missing, run [notebook 06b](../non-ml/notebooks/06b_prepare_lookup.ipynb) with your authorized inputs first.

```sh
python3 recommendation-benchmark/run_lookup.py \
  --checkpoint-root /path/to/checkpoint-root \
  --snapshot-date 2026-11-21 \
  --run-id notebook-offline \
  --output /path/to/notebook-predictions.json

python3 recommendation-benchmark/evaluate.py \
  --predictions /path/to/notebook-predictions.json
```

For a different implementation, export its visible results directly instead of using the notebook adapter.

## Interpret and extend the references

- **Supported** means the reviewed source documents the named service at that branch. It supplies a reference example, called an anchor. This check covers documentary support. Assess the actual point and route, source date or scope, and current availability separately.
- **Unsupported** means the specific returned record has a documented problem for this query. For example, the held Faridkot source row conflicts with its published location, and the Hebbal entry is for a blood storage unit in a blood-bank search. These judgments apply to the cited records. A new branch correction or qualifying claim calls for a revised reference version.
- **Unresolved** means a reviewer examined an ambiguity. **Unreviewed** means the returned branch has no matching judgment. Identity matches by known branch ID or by normalized exact name and PIN. Review legitimate unmatched aliases and add them with their evidence. If the app returns a PIN that conflicts with a positive reference, the scorer withholds positive credit.
- **Anchor hit at five** means at least one documented example appeared in the first five. The anchor check scores completed recommendation or abstention responses where a reference anchor exists. It excludes errors, unsupported inputs, and missing outputs, all of which remain visible. An abstention with an anchor is a retrieval miss and can still be justified by travel or scope. The file is a partial inventory, so these counts cannot establish overall recall, correctness of unknown results, or appropriateness of every abstention.
- **Citations present** counts results that contain a URL and some excerpt. Reviewers still need to verify that the wording at that link supports the requested service for the exact branch. The documentary anchor panels and the act of showing a link are different checks.

For the final comparison, freeze the inputs and keep the reference file out of the ranker's inputs. Have a reviewer who did not implement the ranking check the combined, shuffled results from the final system and baseline. For each unreviewed or disputed candidate, verify branch identity, exact service and scope, evidence date, and whether the displayed excerpt supports it. Record the judgment, precise source, short excerpt, reviewer, and review date in a new copy of `references.json`. Preserve disagreements and name/PIN conflicts for adjudication. Then score both saved outputs against the same reviewed file and keep both evaluation receipts. Save screenshots for a supported search, a stopped or corrected search, and a routing or evidence gap if those cases occur. Use a separate frozen panel for future tuning.

Run the evaluator tests with:

```sh
python3 -m unittest discover -s recommendation-benchmark
```
