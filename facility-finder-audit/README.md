# Run the Facility Finder audit

Open **Facility Finder Datathon Audit.ipynb** in a Databricks Git folder on the `restore-care-finder-map` branch of `augustchu/datathon-F26`. The sibling ZIP contains the companion inputs, references and model artifacts. The preflight looks for the ZIP next to the notebook. If the Databricks runtime starts elsewhere, set the `assets_zip` widget to the absolute workspace path of **Facility Finder Audit Assets.zip**.

## Access and inputs

- Attach compatible Databricks Python compute. Use a Git folder with Git CLI access and give `main_git_checkout` the Git folder's absolute workspace path. The notebook can reuse that checkout for the restored branch. If the compute cannot use the Git folder through the CLI, configure a workspace secret with GitHub read access and set `github_secret_scope` and `github_secret_key` to its scope and key names.
- Check that compute can read the organizer's `facilities`, `india_post_pincode_directory`, and `nfhs_5_district_health_indicators` tables in `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`. If your team uses workspace exports instead, set `facilities_path`, `pin_path`, and `nfhs_path` to the approved files. The organizer tables and exports stay in Databricks.
- Run the install and restart cells first. Set `replay_level` to `standard`, check the path and revision receipts, then run the rest of the notebook. Use `full` for additional training and the WorldPop rebuild.

## What to verify

The notebook pins the restored app to `d682872` and the research code to `af89658`. The load experiments require the original capacity release from `ff7dd5e`. The exact forecast replay produces 181,386 rows. Its app handoff produces a 10,088-row facility snapshot and 38 eligible load projections, then opens the restored Streamlit app for an offline MRI search. The notebook writes a handoff receipt and map to the run directory. A deployed app and live TravelTime routes require their Databricks resources and credentials.

[Databricks Git folders](https://docs.databricks.com/aws/en/repos/git-operations-with-repos) · [Genie Code in notebooks](https://docs.databricks.com/aws/en/notebooks/ds-agent)
