"""Emit the modular, unexecuted analysis notebooks as standard Jupyter JSON."""

import json
from pathlib import Path
from textwrap import dedent


BOOTSTRAP = '''
    from pathlib import Path
    import os
    import sys

    import pandas as pd

    # Fill this in if the notebook's working directory is outside the repository.
    REPO_ROOT = os.environ.get("DATATHON_REPO_ROOT") or None
    cwd = Path.cwd().resolve()
    ROOT = Path(REPO_ROOT).expanduser().resolve() if REPO_ROOT else next(
        (folder for folder in (cwd, *cwd.parents)
         if (folder / "non-ml" / "config" / "project.json").is_file()),
        None,
    )
    if ROOT is None or not (ROOT / "non-ml" / "config" / "project.json").is_file():
        raise RuntimeError("Set REPO_ROOT to the directory that contains non-ml/config/project.json.")
    sys.path.insert(0, str(ROOT / "non-ml" / "src"))

    from facility_tool.workspace import Store, read_config, show

    config = read_config(ROOT)
    MODE = os.environ.get("DATATHON_MODE", config["input_mode"])
    STORE = Store(ROOT, MODE, config["as_of"])
    print(f"Input mode: {MODE} | Evidence date: {config['as_of']}")
'''


def markdown(text):
    return {"cell_type": "markdown", "metadata": {}, "source": dedent(text).strip().splitlines(keepends=True)}


def code(text):
    cell = markdown(text)
    cell.update(cell_type="code", execution_count=None, outputs=[])
    return cell


def notebook(name, title, intro, cells):
    document_cells = [markdown(f"# {title}\n\n{dedent(intro).strip()}"), code(BOOTSTRAP), *cells]
    for index, cell in enumerate(document_cells):
        cell["id"] = f"cell-{index:02d}"
    document = {
        "cells": document_cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = Path(__file__).resolve().parent / "notebooks" / f"{name}.ipynb"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main():
    notebook("01_source_eda", "Audit the input sources", '''
        Start here to inspect the original facility records and the separate public snapshots.
        The default `demo` mode uses fictional records. `public` mode reads the existing
        supplemental snapshots in this repository. `databricks` mode reads the configured
        Unity Catalog tables only when selected inside an authorized Databricks workspace.
        Each mode and evidence date has a separate checkpoint directory.
    ''', [
        markdown('''
            ## Read and save each input

            A source row is a published listing. Several rows can describe the same branch,
            and a row can contain contradictory values. The raw facility checkpoint preserves
            the source column order for the later `countries` predictor boundary.
        '''),
        code('''
            from facility_tool.data import load_records
            from facility_tool.profiles import field_profile, phone_coverage, source_profile
            from facility_tool.workspace import load_inputs

            inputs = load_inputs(MODE, config, globals().get("spark"))
            raw_fdr = inputs["facilities"]
            records = load_records(ROOT, raw_fdr if len(raw_fdr.columns) else None, include_public=MODE != "demo")
            for name, frame in {"records": records, "raw_facilities": raw_fdr,
                                "raw_pins": inputs["pins"], "raw_nfhs": inputs["nfhs"]}.items():
                STORE.write(name, frame)
            source_counts = pd.DataFrame(list(records.attrs["source_counts"].items()), columns=["source", "rows_loaded"])
            STORE.write("source_counts", source_counts)
            print(f"Saved input checkpoints to {STORE.path}")
            show(source_counts)
        '''),
        markdown('''
            ## Coverage and formatting

            Nonempty text, plausible coordinates and plausible phone format answer different
            questions. A plausible phone format offers a lead for follow-up. Source and URL
            coverage count the snapshot rows, including duplicates.
        '''),
        code('''
            profiles = {"source_profile": source_profile(records), "phone_coverage": phone_coverage(records),
                        "fdr_field_profile": field_profile(raw_fdr)}
            for name, frame in profiles.items():
                STORE.write(name, frame)
                print(name.replace("_", " ").title())
                show(frame, limit=60 if name == "fdr_field_profile" else 20)
        '''),
    ])

    notebook("02_facility_and_geography", "Identify facility branches and locations", '''
        Read `records`, `raw_facilities` and `raw_pins` from notebook 01. Save the branch
        entities, the link and review decisions, the postal directory by PIN and district,
        and the raw FDR rows with an appended facility ID.
    ''', [
        markdown('''
            ## Branch matching and geographic conflicts

            A merge requires a unique same-name branch match with supporting address or
            coordinate evidence. Review the candidate identifiers in ambiguous rows before
            merging them. A postal-directory point describes an area. Facility coordinates
            retain their own source and precision.
        '''),
        code('''
            from facility_tool.data import link_entities
            from facility_tool.geo_audit import audit_locations
            from facility_tool.geography import add_postal_context, build_postal_areas

            records = STORE.read("records")
            raw_fdr = STORE.read("raw_facilities")
            entities, links = link_entities(records)
            postal_areas = build_postal_areas(STORE.read("raw_pins"))
            entities = add_postal_context(entities, postal_areas)
            location_records, location_entity_review, location_quality_summary = audit_locations(
                records, entities, postal_areas, links=links, postal_offset_km=50,
            )
            # A PIN assigned exclusively to a different state leaves the branch point unresolved.
            state_conflicts = location_entity_review.loc[
                location_entity_review["postal_resolution"].eq("state_conflict"), "facility_id"
            ]
            entities.loc[entities["facility_id"].isin(state_conflicts), "location_quality"] = "conflicting_postal_state"
            snapshots = {"entities": entities, "links": links, "postal_areas": postal_areas,
                         "location_records": location_records, "location_entity_review": location_entity_review,
                         "location_quality_summary": location_quality_summary}
            for name, frame in snapshots.items():
                STORE.write(name, frame)

            decisions = records[["record_id", "source", "name"]].merge(links, on="record_id", validate="1:1")
            show(decisions.groupby(["source", "link_status"]).size().rename("records").reset_index())
            show(decisions.loc[decisions["link_status"].eq("ambiguous")])
            show(entities.groupby(["location_quality", "postal_resolution"]).size().rename("facilities").reset_index())
        '''),
        markdown('''
            ## Inspect locations that need a second source

            Review the published address, PIN, original point and linked branch records.
            The audit flags likely coordinate reversals, zero placeholders, shared points
            and inconsistent or ambiguous PINs. Distance to a postal-office centroid
            is a review signal. Shared points can describe a real campus. An explicit
            conflict with all directory state labels blocks distance ranking for the branch.
        '''),
        code('''
            show(location_quality_summary.loc[location_quality_summary["count"].gt(0)])
            show(location_records.loc[location_records["review_needed"].fillna(False).astype(bool)])
            show(location_entity_review.loc[location_entity_review["review_needed"].fillna(False).astype(bool)])
        '''),
        markdown('''
            ## Map back to the original FDR rows

            Source position ties each raw field to its transformed source row. An ambiguous
            record stays in the raw audit with a blank facility ID. Branch-level statistics
            and recommendations use links with an assigned ID.
        '''),
        code('''
            positions = records.loc[records["source"].eq("fdr"), ["record_id", "input_position"]]
            mapping = positions.merge(links[["record_id", "facility_id"]], on="record_id", how="left", validate="1:1")
            mapping["input_position"] = mapping["input_position"].astype(int)
            if sorted(mapping["input_position"]) != list(range(len(raw_fdr))):
                raise ValueError("FDR records and raw rows differ. Rerun notebook 01 for this snapshot.")
            linked = raw_fdr.copy()
            linked["facility_id"] = mapping.sort_values("input_position")["facility_id"].fillna("").to_numpy()
            STORE.write("linked_fdr", linked)
            print(f"FDR rows: {len(linked):,} | Assigned branch ID: {linked['facility_id'].ne('').sum():,}")
        '''),
    ])

    notebook("03_count_field_eda", "Explore bed and doctor counts before modeling", '''
        Read `linked_fdr` from notebook 02. Audit bed capacity and number of doctors
        independently. Review the observed values, the reporting gaps, and plausible
        inputs before choosing a regression specification. This notebook performs
        descriptive analysis and saves tables for review.
    ''', [
        markdown('''
            ## What the literature changes here

            Examine zero values, dispersion and differences across facility types before
            choosing a count model. The shape of the outcome can affect count-model accuracy.
            A marginal variance-to-mean ratio alone cannot assess a conditional model.
            [Alomair (2024)](https://pmc.ncbi.nlm.nih.gov/articles/PMC11687910/)

            Spatial structure can make random validation optimistic. This audit keeps linked
            facility IDs together. Hold out whole geographies later if the goal is transfer
            to unseen districts and the data support that split.
            [Linnenbrink et al. (2024)](https://gmd.copernicus.org/articles/17/5897/2024/gmd-17-5897-2024.html)

            Assess missing-field reconstruction and downstream recommendations separately.
            An imputation benchmark found that lower reconstruction error did not necessarily
            improve downstream performance. Compare reporting rates before treating labeled
            facilities as representative, then measure what estimates change in the shortlist.
            [Nikitina and Mouriño (2026)](https://link.springer.com/article/10.1007/s41060-026-01295-3)

            For future service classification, use independently reviewed positives and negatives.
            Text silent on a service stays unlabeled.
        '''),
        code('''
            from facility_tool.eda import COUNT_UNITS, capacity_eda

            linked = STORE.read("linked_fdr")
            entities = STORE.read("entities")
            links = STORE.read("links")
            records = STORE.read("records")
            original = [field for field in linked.columns if field != "facility_id"]
            seed = 42
            # Example override per target: ["facilityTypeId", "operatorTypeId"].
            feature_columns = {"capacity": None, "numberDoctors": None}

            # Extra context is for reporting-gap audits. Keep it out of the original schema list.
            sources = records[["record_id", "source"]].merge(links[["record_id", "facility_id"]], on="record_id", validate="1:1")
            sources = sources.loc[sources["facility_id"].fillna("").ne("")].groupby("facility_id")["source"].agg(
                lambda names: ", ".join(sorted(set(names)))
            )
            audits = linked.copy()
            audits["source_types"] = audits["facility_id"].map(sources)
            branches = entities.set_index("facility_id")
            for field in ("state", "district"):
                if field not in original:
                    places = branches[field].replace("", None).fillna(branches[f"postal_{field}"])
                    places = places.mask(branches["postal_resolution"].isin(["state_conflict", "district_conflict"]))
                    audits[field] = audits["facility_id"].map(places)
        '''),
        markdown('''
            ## Label quality and the proposed holdout

            The parser separates exact counts, ranges, unclear units, implausible numeric
            forms and missing values. Review conflicting values from linked rows as a branch
            issue. Facility IDs define the proposed train, calibration and test partitions.
            Whole-snapshot reports below describe coverage. Relationships and plots use
            training rows only. The readiness counts help identify small cohorts before fitting.
        '''),
        code('''
            results = {}
            names = {"capacity": "capacity", "numberDoctors": "number_doctors"}
            for target, prefix in names.items():
                if target not in original:
                    print(f"{target}: no original facility field loaded in {MODE} mode.")
                    continue
                result = capacity_eda(audits, target, original, seed=seed, feature_columns=feature_columns[target],
                                      reference_year=int(config["as_of"][:4]))
                results[target] = result
                for table, frame in result.items():
                    STORE.write(f"{prefix}_{table}", frame)
                print(f"{target} ({COUNT_UNITS[target]})")
                show(result["summary"])
                print("Source-level parsing outcomes")
                show(result["source_target_audit"].groupby("parse_status").size().rename("source_rows").reset_index())
                print("Branch-level conflicts and unclear counts")
                review = result["target_audit"]
                show(review.loc[review["status"].isin(["conflicting", "unparseable"])])
                print("Proposed holdout readiness")
                show(result["split_readiness"])
        '''),
        markdown('''
            ## Reporting gaps, leakage and interactions

            Inspect counts alongside percentages and correlations. A pattern in a group with
            few observed counts can be unstable. A potential predictor must appear after
            `countries` in the source schema. The structured subset limits obvious leakage.
            Text that repeats the target and suspicious count agreement deserve a source check.
        '''),
        code('''
            for target, result in results.items():
                for table in ("missingness", "feature_audit", "group_coverage", "numeric_relationships",
                              "categorical_relationships", "interaction_summary"):
                    print(f"{target} | {table.replace('_', ' ').title()}")
                    frame = result[table]
                    if table == "missingness":
                        frame = frame.sort_values("raw_missing_fraction", ascending=False)
                    show(frame, limit=60 if table in ("missingness", "feature_audit") else 20)
        '''),
        markdown('''
            ## Training label distributions

            These charts use exact training counts. The histogram and quantiles show tail
            behavior and sparse or zero counts. The scatter plot uses only paired observations.
        '''),
        code('''
            try:
                import matplotlib.pyplot as plt
            except ImportError:
                plt = None

            for target, result in results.items():
                training = result["analysis_rows"]
                observed = pd.to_numeric(training["value"], errors="coerce").dropna()
                if observed.empty:
                    continue
                print(f"{target} | Training summary")
                show(observed.describe(percentiles=[.1, .25, .5, .75, .9, .99]).rename(COUNT_UNITS[target]))
                print(f"Explicit zeros: {observed.eq(0).sum():,}")
                if len(observed) > 1 and observed.mean() > 0:
                    print(f"Marginal variance / mean: {observed.var() / observed.mean():.2f}")
                if plt is None:
                    continue
                other = next(field for field in COUNT_UNITS if field != target)
                plot_pair = other in training and training[["value", other]].notna().all(axis=1).any()
                figure, axes = plt.subplots(1, 1 + int(plot_pair), figsize=(10 if plot_pair else 5, 3.5), squeeze=False)
                axes[0, 0].hist(observed, bins="auto", color="#24576d")
                axes[0, 0].set(xlabel=f"Reported {COUNT_UNITS[target]}", ylabel="Training facilities")
                if plot_pair:
                    paired = training[["value", other]].dropna()
                    axes[0, 1].scatter(paired[other], paired["value"], alpha=.5, color="#24576d")
                    axes[0, 1].set(xlabel=f"Reported {COUNT_UNITS[other]}", ylabel=f"Reported {COUNT_UNITS[target]}")
                figure.tight_layout()
                plt.show()
                plt.close(figure)
        '''),
    ])

    notebook("04_service_evidence", "Trace each service claim to its source", '''
        Read `records` and `links` from notebook 02. Save a long-form `claims` table with
        the facility branch, exact source excerpt, service, stance, source scope, citation
        quality and validity. Edit `service_aliases` in `non-ml/config/project.json` to add
        equivalent names for the same service.
    ''', [
        markdown('''
            ## Build the claims table

            Look for explicit statements, exclusions and qualified mentions. A specialty,
            single piece of equipment, broad directory category, or generic empanelment
            can help identify a lead for review. The evidence field shows which text was
            matched. Empty text leaves the service unknown.
        '''),
        code('''
            from facility_tool.data import extract_claims

            records = STORE.read("records")
            links = STORE.read("links")
            claims = extract_claims(records, links, config["service_aliases"], config["as_of"])
            STORE.write("claims", claims)
            overview = claims.groupby(["source", "stance", "validity", "citation_quality"]).size()
            show(overview.rename("claim_rows").reset_index(), limit=60)
            print(f"Facilities with at least one extracted claim: {claims['facility_id'].nunique():,}")
        '''),
        markdown('''
            ## Inspect the excerpts and disagreements

            Multiple claims per facility can repeat the same extracted text. Agreement
            across independent sources requires inspecting where the text came from.
            An applicable exclusion or expired listing needs review before using the claim.
        '''),
        code('''
            service = config["lookup"]["service"]
            selected = claims.loc[claims["service"].eq(service)].copy()
            review_columns = ["facility_id", "stance", "scope", "excerpt", "evidence_field", "validity",
                              "validity_text", "citation_quality", "source_url", "source_urls", "match_reason"]
            show(selected[review_columns], limit=30)
            stance_sets = selected.groupby("facility_id")["stance"].agg(set)
            conflicts = stance_sets[stance_sets.map(lambda stances: {"supports", "contradicts"}.issubset(stances))]
            print(f"{service} | Branches with both support and contradiction: {len(conflicts):,}")
            show(selected.loc[selected["facility_id"].isin(conflicts.index), review_columns])
        '''),
    ])

    notebook("05_field_updates", "Compare reported fields and model estimates", '''
        Read the original facility fields, linked contacts and branch IDs. Save a sidecar
        `field_updates` table with one provenance-tagged value per field, facility and method.
        Later notebooks display model estimates beside the evidence while keeping the
        original data and the service claims available for inspection.
    ''', [
        markdown('''
            ## Observed counts, contacts and optional imported estimates

            An unambiguous exact count is copied from the original FDR field with the
            quoted value and source record ID. Ranges and contradictory counts remain in
            the audit from notebook 03. Linked phone and email evidence stays traceable
            to the source listing. Multiple published contacts can coexist.

            Set `estimate_file` to an already-reviewed CSV or JSONL with `facility_id`,
            `field`, `method`, `value`, `unit`, `lower`, `upper`, `provenance` and `detail`.
            Method must be `ml` or `regression`. Supply the model name, version, evaluation
            cohort and interval definition in the provenance and detail where available.
            The demo uses hand-written overlay values so each mode can be compared locally.
        '''),
        code('''
            from facility_tool.eda import COUNT_UNITS, reported_count_updates
            from facility_tool.examples import demo_overlays, demo_pressure
            from facility_tool.updates import contact_evidence, empty_updates, load_estimates

            entities = STORE.read("entities")
            records = STORE.read("records")
            links = STORE.read("links")
            linked = STORE.read("linked_fdr")
            estimate_file = None  # Optional path to existing results for this snapshot.
            observed = [reported_count_updates(linked, field) for field in COUNT_UNITS if field in linked.columns]
            imported = load_estimates(estimate_file, entities["facility_id"], services=config["service_aliases"])
            contacts = contact_evidence(records, links)
            STORE.write("contact_evidence", contacts)
        '''),
        markdown('''
            ## Keep service load data separate

            Bed counts and doctor counts are resource indicators. The separate load
            tables require the relevant service and unit. Demo availability is dated
            fiction. Demo pressure is an illustrative demand/capacity scenario. Real
            snapshots start with empty load tables until eligible observations are supplied.
        '''),
        code('''
            measured_load = pd.DataFrame(columns=["facility_id", "service", "unit", "available_units", "as_of", "source_url"])
            pressure_scenarios = pd.DataFrame(columns=["facility_id", "service", "unit", "period", "basis", "demand", "capacity", "source_url"])
            if MODE == "demo":
                fixture_estimates, measured_load = demo_overlays(entities, config["as_of"])
                pressure_scenarios = demo_pressure(entities)
                if not estimate_file:
                    imported = fixture_estimates

            frames = [frame for frame in [*observed, imported] if not frame.empty]
            updates = pd.concat(frames, ignore_index=True) if frames else empty_updates()
            STORE.write("field_updates", updates)
            STORE.write("measured_load", measured_load)
            STORE.write("pressure_scenarios", pressure_scenarios)
            show(updates.groupby(["method", "field"]).size().rename("facility_values").reset_index())
            show(updates.loc[updates["method"].isin(["ml", "regression"])], limit=12)
            print(f"Cited contact rows: {len(contacts):,} | Dated load rows: {len(measured_load):,} | Pressure scenarios: {len(pressure_scenarios):,}")
        '''),
    ])

    notebook("06_district_context", "Review optional district survey context", '''
        Read `entities` and `raw_nfhs`. This notebook can run after notebook 02 independently
        of the counts and service evidence. NFHS-5 district survey indicators describe an area
        in 2019–21. The survey is for context and use in later geographic coverage audits.
    ''', [
        markdown('''
            ## Inspect available survey fields

            The input is optional outside demo mode. In an authorized Databricks session,
            enabling `include_nfhs` in the project config makes notebook 01 load the
            configured table. Select the exact indicator names from that input. Parentheses
            indicate small samples and asterisks flag suppressed values in the source.
        '''),
        code('''
            from facility_tool.profiles import district_context, field_profile

            entities = STORE.read("entities")
            nfhs = STORE.read("raw_nfhs")
            show(field_profile(nfhs), limit=30)
            # Edit these names after inspecting available columns above.
            indicators = [field for field in ("hh_improved_water_pct", "hh_electricity_pct") if field in nfhs]
        '''),
        markdown('''
            ## Join on state and district

            Use a unique normalized state/district pair. Postal context can fill in an
            otherwise unknown district when the available evidence agrees. Conflicting
            geographies and duplicate survey keys remain visible for review. Survey
            indicators stay at district scope and outside facility-level service evidence.
        '''),
        code('''
            if not nfhs.empty and not indicators:
                print("Choose indicator columns above to attach survey values.")
            context, ambiguous = district_context(entities, nfhs, indicators)
            STORE.write("district_context", context)
            STORE.write("ambiguous_nfhs_districts", ambiguous)
            if context.empty:
                print("No district survey rows available in this snapshot.")
            else:
                show(context["nfhs_join"].value_counts().rename("facilities").reset_index())
                indicator_columns = [column for column in context.columns if any(column.startswith(name) for name in indicators)]
                if indicator_columns:
                    show(context[["state_ut", "district_name", *indicator_columns]].drop_duplicates())
            show(ambiguous)
        '''),
    ])


if __name__ == "__main__":
    main()
