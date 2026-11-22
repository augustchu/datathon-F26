# Run the Facility Finder audit

Open **Facility Finder Datathon Audit.ipynb** in a Databricks Git folder on the `restore-care-finder-map` branch of `augustchu/datathon-F26`. The sibling ZIP contains the companion inputs, references and model artifacts. The preflight looks for the ZIP next to the notebook. If the Databricks runtime starts elsewhere, set the `assets_zip` widget to the absolute workspace path of **Facility Finder Audit Assets.zip**.

## Access and inputs

- Attach compatible Databricks Python compute. Use a Git folder with Git CLI access and give `main_git_checkout` the Git folder's absolute workspace path. The notebook can reuse that checkout for the restored branch. If the compute cannot use the Git folder through the CLI, configure a workspace secret with GitHub read access and set `github_secret_scope` and `github_secret_key` to its scope and key names.
- The preflight checks whether compute can read the organizer's `facilities`, `india_post_pincode_directory`, and `nfhs_5_district_health_indicators` tables in `databricks_virtue_foundation_dataset_dais_2026.virtue_foundation_dataset`. If your team uses workspace exports instead, set `facilities_path`, `pin_path`, and `nfhs_path` to the approved files. If the paths and catalog are unavailable, the notebook stages the verified GitHub copies linked below.
- Use **Run All** to install dependencies, check the input paths and run the notebook. The defaults are `replay_level=full` and `download_all_data=yes`, which include retraining, the WorldPop rebuild and requests for cited publisher documents. Use a writable volume in `output_folder` to keep the downloaded files and receipts.

## What to verify

The notebook pins the restored app to `d682872` and the research code to `af89658`. The load experiments require the original capacity release from `ff7dd5e`. The exact forecast replay produces 181,386 rows. Its app handoff produces a 10,088-row facility snapshot and 38 eligible load projections, then opens the restored Streamlit app for an offline MRI search. The notebook writes a handoff receipt and map to the run directory. A deployed app and live TravelTime routes require their Databricks resources and credentials.

[Databricks Git folders](https://docs.databricks.com/aws/en/repos/git-operations-with-repos) · [Genie Code in notebooks](https://docs.databricks.com/aws/en/notebooks/ds-agent)

## Data downloads

The repository contains private CSV mirrors of the organizer exports at the pinned research commit. The notebook fetches them through the authenticated checkout and verifies their checksums, schemas and row counts before use.

| Organizer input | Pinned GitHub copy | Rows |
|---|---|---:|
| Virtue Foundation facilities | [Facilities CSV](https://github.com/augustchu/datathon-F26/blob/af8965890e4de2e5a5f2a24177425e5fdb77352d/load-balancing-pipeline/facilities%20dataset.csv) | 10,088 |
| India Post PIN directory | [PIN directory CSV](https://github.com/augustchu/datathon-F26/blob/af8965890e4de2e5a5f2a24177425e5fdb77352d/load-balancing-pipeline/india_post_pincode_directory.csv) | 165,627 |
| NFHS-5 district indicators | [NFHS-5 CSV](https://github.com/augustchu/datathon-F26/blob/af8965890e4de2e5a5f2a24177425e5fdb77352d/load-balancing-pipeline/NFHS-5%20Dataset.csv) | 706 |

For a direct organizer export, open the challenge workspace's Catalog and confirm read access to the three tables. Run the notebook preflight to export readable tables into the run directory, or set the three path widgets to approved exports you already have. Use the original ordered exports when comparing hashes from the archived experiments.

The companion ZIP stages the frozen external sources and model artifacts. The main checkout supplies the boundary, census and population files. The original capacity release comes from `ff7dd5e`. The WorldPop raster downloads from its publisher, while the reference manifest records the status of each requested source document. [Capacity release](https://github.com/augustchu/datathon-F26/releases/tag/capacity-newest-2026-11-22) · [WorldPop India raster](https://data.worldpop.org/GIS/Population/Global_2000_2020_Constrained/2020/BSGM/IND/ind_ppp_2020_UNadj_constrained.tif)

The run directory includes `organizer-data-manifest.csv`, `external-data-manifest.csv`, and `reference-download-manifest.csv`. These identify the staged files and distinguish verified local copies from publisher resources that could not be downloaded.
