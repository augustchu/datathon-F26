"""Explanations and repository sources for calculated values shown in the care finder."""

from typing import NamedTuple


class Explanation(NamedTuple):
    meaning: str
    calculation: str
    reason: str
    sources: tuple[tuple[str, str], ...]


DECISIONS = ("Prototype implementation decisions", "first-prototype/DECISIONS.md")
LOCATION_AUDIT = ("Coordinate and address audit", "non-ml/LOCATION_AUDIT.md#when-a-point-is-held")
TRAVELTIME_SETUP = ("Prototype TravelTime configuration", "first-prototype/README.md#4-create-and-configure-the-databricks-app")


EXPLANATIONS = {
    "snapshot_records": Explanation(
        "The number of facility source records in the prepared snapshot used by this search.",
        "The app counts every row it loaded from the prepared facility table, before filtering on service evidence or location. A facility can have more than one source record.",
        "The snapshot identifies the dataset behind the results. Preserving separate records avoids merging similar names that may belong to different branches.",
        (DECISIONS, ("Capacity release and source row checks", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#3-verify-the-two-exports-together")),
    ),
    "scenario_share": Explanation(
        "The share of each modeled district population assumed to need the selected service in this scenario.",
        "You choose a value from 0% to 2%. The app multiplies the modeled district population by that share to get hypothetical district cases.",
        "A user-set share makes it possible to compare demand assumptions without treating the share as a measured rate or a forecast.",
        (DECISIONS,),
    ),
    "search_origin": Explanation(
        "The point the app uses to calculate routes and straight-line distances to facilities.",
        "For a PIN, city, or district, the app averages the coordinates of records with eligible locations that match the place, before filtering by service. It uses manually entered coordinates as supplied after checking the bounds.",
        "A facility-coordinate average gives the prototype an approximate search point when you know the area but do not have exact coordinates.",
        (DECISIONS, LOCATION_AUDIT),
    ),
    "reachable_count": Explanation(
        "The number of matching facility records for which TravelTime returned a drive estimate within its three-hour window.",
        "The app sends service matches with usable coordinates to TravelTime and counts records with a finite, nonnegative route time. It counts them before applying the number of options you asked to see.",
        "The count shows how much of the eligible set could be ranked using routes. A successful response with zero routes triggers the straight-line fallback.",
        (DECISIONS, TRAVELTIME_SETUP),
    ),
    "result_order": Explanation(
        "The facility's position among the options returned by this search.",
        "The app sorts by lowest ranking score, breaking ties by drive time when available and then straight-line distance. The optional projected load comparison can move one nearby routine-care option to the top. If TravelTime returns zero reachable matches, the app orders by straight-line distance alone.",
        "The standard order balances travel with the strength of the service evidence. The distance-only fallback surfaces the closest matches when route times are unavailable for the entire search.",
        (DECISIONS,),
    ),
    "travel_time": Explanation(
        "An estimated drive time from the search origin to the facility on a typical weekday morning.",
        "TravelTime's one-to-many matrix models driving and ferry routes within a three-hour travel window. The app converts returned seconds to minutes and rounds the displayed value.",
        "A route estimate reflects the travel network and gives a more useful basis for comparing trips when the provider can calculate a route.",
        (DECISIONS, TRAVELTIME_SETUP, ("TravelTime matrix assumptions", "load-balancing-pipeline/SIMULATOR.md#traveltime-api")),
    ),
    "straight_line_distance": Explanation(
        "The direct distance in kilometers from the search origin to the facility's recorded coordinates.",
        "The app uses the haversine formula, which calculates the shortest distance along a spherical model of the Earth from two latitude and longitude pairs.",
        "Direct distance provides a consistent comparison for every eligible point and works when driving routes are unavailable.",
        (DECISIONS, LOCATION_AUDIT),
    ),
    "service_match": Explanation(
        "How directly the record addresses the service you selected.",
        "A direct facility listing or a reviewed claim earns 100 points. A passing mention or a broader related term earns 40. Records without supporting evidence score zero and are omitted.",
        "A direct listing can be a strong match even when it has limited independent verification. Keeping match separate from confidence makes both judgments visible.",
        (DECISIONS,),
    ),
    "evidence_confidence": Explanation(
        "How strongly the available sources document the requested service at this branch, on a 0–100 evidence scale.",
        "The app adds up to 80 service evidence points and up to 20 branch identity points. Scores of 90 and above are High. Below 90, explicit facility-record listings are Moderate and other eligible results are Limited.",
        "Source quality and branch identity affect how much weight to give a listing. The score describes documentation, which can differ from the facility's current ability to provide care.",
        (DECISIONS,),
    ),
    "service_evidence_points": Explanation(
        "The part of evidence confidence contributed by the strongest available source for the requested service.",
        "Hospital evidence with independent corroboration earns 80, a hospital listing alone 75, a reviewed directory listing 70, an explicit facility-record listing 50, and a passing mention 15.",
        "The scale gives a direct listing meaningful weight and reserves the most points for service claims linked to reviewed sources.",
        (DECISIONS,),
    ),
    "branch_identity_points": Explanation(
        "The part of evidence confidence contributed by knowing which branch the service record describes.",
        "A branch with active reviewed supporting evidence linked by branch ID and PIN earns 20 points. A named facility with a plausible PIN earns 10, and other branch records earn zero.",
        "Similar facility names can refer to different locations. This part of the score rewards records that can be tied to a specific branch.",
        (DECISIONS, LOCATION_AUDIT),
    ),
    "ranking_score": Explanation(
        "The baseline score for this search. Lower scores rank higher in the ordinary order.",
        "Start with drive minutes or straight-line kilometers. Add `10 × (1 − match / 100)` for service match in minute-equivalents. "
        "For confidence `c`, use `s(c) = 1 / (1 + exp((c − 55) / 3.5))` and add `60 × (s(c) − s(100)) / (s(0) − s(100))` in minute-equivalents. "
        "Halve both adjustments for straight-line ranking. The optional projected load comparison runs separately from this score.",
        "Stronger evidence can justify a longer trip. The curve changes little across high confidence scores and makes weaker evidence matter more as confidence drops through the 50s. "
        "The 60-minute ceiling and the shape of the curve are working prototype choices.",
        (DECISIONS, ("Confidence-weight tradeoff and worked examples", "first-prototype/DECISIONS.md#confidence-weight-tradeoff")),
    ),
    "geography_status": Explanation(
        "A review flag for how the facility's recorded location matches the district geography used by the prototype.",
        "The app checks which district boundary contains the source coordinates, then looks for an exact normalized district and state match in the NFHS data. Travel calculations use the source coordinates.",
        "The flag reveals when a district link for population and demand is uncertain. Unusable coordinates and audited location holds exclude a facility from search.",
        (DECISIONS, LOCATION_AUDIT),
    ),
    "district_population": Explanation(
        "The modeled number of people living in the facility’s district in 2020.",
        "The population pipeline totals WorldPop raster cells within district boundaries and scales the results to official 2020 state projections. The app joins a unique district estimate by normalized NFHS district and state names.",
        "This gives the demand scenario a consistent population base. Missing, duplicate, and unmatched district estimates stay unavailable. The estimate reflects 2020 geography and modeled population.",
        (
            ("Population modeling and state scaling", "load-balancing-pipeline/README.md#supply-state-projections-and-finalize"),
            ("Prototype population join", "first-prototype/DECISIONS.md#product-and-architecture"),
        ),
    ),
    "district_cases": Explanation(
        "The hypothetical number of people in the district who need the selected service under this scenario.",
        "Multiply the modeled 2020 district population by the selected share, then divide by 100. A share of 1% corresponds to one modeled case for every 100 residents.",
        "A percentage slider lets you compare demand scenarios on a common population base. Actual prevalence, current patient volume, and facility catchments require separate data.",
        (("Prototype demand scenario", "first-prototype/DECISIONS.md#geographies-and-ranking"),),
    ),
    "facility_cases": Explanation(
        "This facility record’s assigned share of the district’s hypothetical demand for the selected service.",
        "Divide modeled district cases by the number of eligible facility records for the selected service in the same district and state. The split is calculated before route and result-count limits are applied.",
        "Equal allocation makes the scenario reproducible while catchment and patient-choice data are unavailable. The result is a modeled allocation of cases.",
        (("Prototype demand allocation", "first-prototype/DECISIONS.md#geographies-and-ranking"),),
    ),
    "allocation_count": Explanation(
        "The number of eligible facility records sharing the modeled district demand.",
        "Count records with usable locations and evidence for the selected service in the same NFHS district and state. This count is taken before reachable route filtering and truncation to the displayed results.",
        "The count supplies the denominator for the equal split. Each eligible facility record gets one share, reflecting the prototype’s source-row representation.",
        (
            ("Prototype facility identity", "first-prototype/DECISIONS.md#facility-identity-service-evidence-and-citations"),
            ("Prototype demand allocation", "first-prototype/DECISIONS.md#geographies-and-ranking"),
        ),
    ),
    "bed_proxy": Explanation(
        "The facility-size denominator used to scale hypothetical demand in the ranking scenario.",
        "Use the positive bed count from the capacity release, whether sourced or estimated. When that count is missing or withheld, use 50 solely in the scenario calculation.",
        "The same hypothetical demand creates more modeled pressure at a smaller facility. The 50-bed convention keeps the scenario calculable where capacity is unknown. Staffed beds, free beds, and service-specific slots require separate information.",
        (("Prototype capacity and demand rules", "first-prototype/DECISIONS.md#geographies-and-ranking"),),
    ),
    "load_ratio": Explanation(
        "A scenario pressure measure in hypothetical cases per bed used as a facility-size proxy.",
        "Divide the facility’s assigned hypothetical cases by its bed proxy. For example, 20 allocated cases divided by a proxy of 50 gives 0.4. If district population is unavailable, the calculation contributes zero scenario load.",
        "This scales the same demand by facility size before applying the regional indicator factor. The ratio describes a modeling input. Occupancy and wait times require separate measurements.",
        (("Prototype capacity and demand rules", "first-prototype/DECISIONS.md#geographies-and-ranking"),),
    ),
    "regional_demand_factor": Explanation(
        "A multiplier that adjusts scenario pressure using selected NFHS-5 district survey indicators. A factor of 1 leaves pressure unchanged.",
        "Average the usable indicators for the district, divide by the pooled mean across the available survey values, and limit the result to 0.5–2.0. The prototype halves parenthesized values, skips suppressed or unreadable values, and uses 1 where a usable mapping or reference is unavailable.",
        "This lets relative regional context affect the scenario while bounding its influence. Service-to-indicator mappings use prototype rules. Disease incidence and actual patient demand require separate data.",
        (
            ("Prototype NFHS factor", "first-prototype/DECISIONS.md#geographies-and-ranking"),
            ("NFHS and population inputs", "load-balancing-pipeline/README.md#supply-state-projections-and-finalize"),
        ),
    ),
    "model_capacity": Explanation(
        "An estimate of the branch’s whole-facility bed count where the published count is missing.",
        "The release applies a saved XGBoost model trained on 1,608 source-published bed counts with structured facility and district inputs. It estimates only routable branches with a missing original and cleaned count and no identity or count hold.",
        "The model fills eligible data gaps for the facility-size scenario. On 455 historical reported test counts, the average absolute error was about 110 beds, and larger facilities were often underestimated. Current staffed and available beds require separate confirmation.",
        (
            ("Newest-source capacity release", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#1-select-and-apply-capacity"),
            ("Model errors by facility size", "max-supplemented-data/experiments/capacity/DESCRIPTION_RECOVERY_EVALUATION.md#4-check-where-the-errors-remain"),
        ),
    ),
    "capacity_interval": Explanation(
        "A model uncertainty range for the estimated whole-facility bed count.",
        "The release subtracts and adds 278 beds to the point estimate and floors the lower bound at zero. The error allowance was calibrated on a separate set of 213 reported facility counts.",
        "The range shows how far the model can miss and discourages reading the point estimate as exact. It covered 89.9% of historical reported test counts. Coverage for facilities with genuinely missing counts is unknown.",
        (("Capacity estimate interval", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#how-to-identify-and-interpret-estimates"),),
    ),
    "release_capacity": Explanation(
        "The selected reported whole-facility bed count for the branch.",
        "The release selects the newest eligible dated claim for the same branch and whole-facility count. Publisher priority handles date ties and cases with only undated sources. Explicit reviews and holds take precedence.",
        "This keeps the reported value and its source traceable and gives the scenario a consistent facility-size input. Current staffed beds and availability require direct confirmation.",
        (("Bed count selection rules", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#1-select-and-apply-capacity"),),
    ),
    "capacity_unavailable": Explanation(
        "The release has no usable whole-facility bed count for this branch.",
        "A value stays blank when the release withholds the count for a branch, source, or quality issue, or when it falls outside the model’s eligibility rules. The scenario uses 50 as a separate denominator when it needs a bed proxy.",
        "Keeping the release value blank preserves uncertainty and avoids presenting a withheld or unsupported count as fact. The scenario can still run with its disclosed fallback.",
        (
            ("Capacity withheld from the release", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#why-542-capacity-cells-are-blank"),
            ("Prototype bed proxy", "first-prototype/DECISIONS.md#geographies-and-ranking"),
        ),
    ),
    "capacity_crosscheck": Explanation(
        "A comparison between the selected release bed count and the Marketplace bed count for the matched facility.",
        "The snapshot aligns records by stable facility ID and compares the numeric selected count with the Marketplace field. It also checks separately whether the release’s original capacity agrees with that field.",
        "The check makes source differences visible and helps verify the records were aligned. The app uses the curated release value for calculations and respects release holds.",
        (
            ("Prototype release alignment and cross-check", "first-prototype/DECISIONS.md#product-and-architecture"),
            ("Capacity export validation", "max-supplemented-data/experiments/capacity/NEWEST_CAPACITY_RELEASE.md#3-verify-the-two-exports-together"),
        ),
    ),
}
