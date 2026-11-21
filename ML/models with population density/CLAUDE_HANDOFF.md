# Population density data handoff

Supply a table with one population-density value per Indian district or per PIN code. The notebooks attach that area-level value to each facility and compare held-out bed-capacity predictions. Put the prepared data in a Spark-readable location shared by the team. The teammate’s actual density table has yet to be supplied.

## Required fields

Choose the geographic key that matches the data’s published granularity. The names below are examples for a prepared table. Existing names can be mapped in `DENSITY_COLUMNS`.

| Dataset grain | Required key columns | Value columns |
| --- | --- | --- |
| District | `state` and `district` together, using Indian state or union territory and district names | `population_density` in people per square kilometre, or `population` and `land_area_km2` |
| PIN | `pincode` as a six-digit text code | `population_density` in people per square kilometre, or `population` and `land_area_km2` |

Keep one row per geographic key. For districts, the same district name can occur in multiple states, so keep the two fields together. For either grain, use a consistent boundary release and population period. District codes, Census IDs, or alternate district names need a documented crosswalk to the state and district names used in the India Post data. PINs should refer to the same postal areas used to produce the density values.

The join collapses identical repeated values and prefers a unique available value over duplicate nulls. Conflicting values for a key stop the join for review. Resolve the source disagreement or choose a dated release upstream.

Direct density values must already be in people/km². For calculations from components, supply a population count and positive land area for the same geography. Set one area unit for the entire input. The helper accepts `km2`, `m2`, `ha`, and `mi2`. Keep a reported zero only when the source explicitly records zero population for a valid area. Leave unavailable values null. Include source title or URL, population year or estimate period, and boundary year or release in the handoff.

## Adapt the input to a table

- **Tabular district or PIN data** can be projected directly to the required columns. Resolve duplicate keys upstream. Where several publications disagree, record which release you chose and why. When computing the density of an area from its non-overlapping parts, divide the sum of their population by the sum of their land area.
- **Points, grids, rasters, and polygons** need spatial aggregation before being passed to the notebook. Use a documented district or PIN boundary release, align coordinate systems, and document how you handled partially overlapping source cells and NoData. Use an area-appropriate projection or geodesic area for the denominator. For count grids, allocate and sum population over each target area under a documented overlap assumption. For density grids, account for each cell’s contributing area. Each output row should describe the target district or PIN area as a whole.
- **One location per facility** will require an upstream conversion to district or PIN grain. Passing a point value or raster tile as though it represented an entire district would give the model different semantics.

To deliver a district CSV with direct density values, use this header and replace the illustrative row with the source’s actual values.

```csv
state,district,population_density
Example State,Example District,525.0
```

To deliver a PIN CSV with population and land area, use this shape. The row shows the units only.

```csv
pincode,population,land_area_km2
123456,48000,80.0
```

## Wire the data into Databricks

Open the notebooks through the Databricks Git folder. Set exactly one of `DENSITY_TABLE` to a Unity Catalog `catalog.schema.table` name or `DENSITY_PATH` to a path Spark can read. For a path, set `DENSITY_FORMAT` to `csv` or `parquet`. A CSV must have a header row. Use a team-readable location such as a Unity Catalog volume. Set the same density input and source metadata in both notebooks.

Example for a Unity Catalog district table whose columns are named `state_name`, `district_name`, and `people_per_sq_km`.

```python
DENSITY_TABLE = "team_catalog.reference.district_population_density"
DENSITY_PATH = ""
DENSITY_KEY = "district"
DENSITY_COLUMNS = {
    "state_column": "state_name",
    "district_column": "district_name",
    "density_column": "people_per_sq_km",
}
DENSITY_SOURCE = "Replace with the source title or URL"
DENSITY_VINTAGE = "Replace with the population period and boundary release"
```

Example for a volume CSV with `postal_code`, `total_people`, and `land_area_hectares`. Passing `density_column=None` selects conversion from population and area.

```python
DENSITY_TABLE = ""
DENSITY_PATH = "/Volumes/team_catalog/reference/team_volume/pin_density.csv"
DENSITY_FORMAT = "csv"
DENSITY_KEY = "pin"
DENSITY_COLUMNS = {
    "pin_column": "postal_code",
    "density_column": None,
    "population_column": "total_people",
    "area_column": "land_area_hectares",
    "area_unit": "ha",
}
DENSITY_SOURCE = "Replace with the source title or URL"
DENSITY_VINTAGE = "Replace with the population period and boundary release"
```

## How facilities match to areas

- A PIN input joins directly on the facility’s `address_zipOrPostcode`. District and NFHS joins use the provided India Post PIN directory to resolve the state and district together. The facility’s published state narrows the possible district matches. A conflicting state or a tie between distinct districts leaves density missing for district-based input. The existing facility city's name is not used as a district substitute.
- A found area with an unavailable density appears as `value_missing`. An unmatched key appears as `key_not_found`. The district report also identifies geography conflicts and ambiguous or invalid PINs. The joined fields are `population_density_per_km2`, `population_density_missing`, and `population_density_join_status`.

## Run and compare

1. Open `Capacity with Population Density.ipynb` for the comparison with the density feature.
2. Open `Capacity with Population Density and NFHS.ipynb` for the comparison with density and NFHS-5 district indicators. This notebook reads the provided NFHS table. A second NFHS extract is unnecessary.
3. Use the same facility snapshot and split seed in both notebooks. They use a seeded 60/20/20 split of facility groups and report a SHA-256 `split_fingerprint`. Compare test metrics only if the split fingerprints agree.
4. Check join coverage. The left join keeps otherwise eligible facilities with missing density in the comparison and marks those rows. Training requires at least one matched density value in the training partition. Review MAE and RMSE for all held-out rows and the density-matched and density-missing cohorts.

The two variants test the effect of adding NFHS to a model that already includes density. Measuring the added value of density itself requires a facilities-only model on these same partitions. The earlier artifact evaluation used a different split.

The notebooks use the provided facility table and, for district linkage, the provided India Post PIN directory. They read those tables without writing enriched values back to them. W&B tracking defaults to offline.
