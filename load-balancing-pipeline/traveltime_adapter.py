"""TravelTime Matrix Fast adapter with a clearly labeled straight-line fallback."""
from __future__ import annotations

import math
import os
import requests

TRAVELTIME_URL = "https://api.traveltimeapp.com/v4/time-filter/fast"


def straight_line_minutes(origin, facilities, assumed_kmh=25.0):
    """Approximate road time; use only as a demo fallback, never label it as route time."""
    lat1, lon1 = map(math.radians, origin)
    rows = {}
    for row in facilities.to_dict("records"):
        lat2, lon2 = map(math.radians, (float(row["latitude"]), float(row["longitude"])))
        a = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
        km = 6371.0088 * 2 * math.asin(math.sqrt(a))
        rows[row["facility_id"]] = {
            "distance_km": km,
            "travel_time_minutes": km / assumed_kmh * 60,
            "basis": "straight_line_speed_proxy",
        }
    return rows


def travel_time_matrix(origin, facilities, departure_period="weekday_morning",
                       allow_fallback=False, app_id=None, api_key=None,
                       max_travel_time_seconds=10800):
    """One TravelTime Fast request for every supplied destination (up to 100k).

    Time Filter Fast is the fewest-call option for this workflow: one origin to
    many facility destinations. The request returns reachability and time up to
    the API's 3-hour Fast limit. India driving coverage is supported by using
    the Fast API's driving+ferry mode.
    """
    app_id = app_id or os.getenv("TRAVELTIME_APP_ID")
    api_key = api_key or os.getenv("TRAVELTIME_API_KEY")
    if not app_id or not api_key:
        if allow_fallback:
            return straight_line_minutes(origin, facilities), "straight-line proxy (no API credentials)"
        raise RuntimeError("TravelTime requires both TRAVELTIME_APP_ID and TRAVELTIME_API_KEY.")
    if facilities.empty:
        return {}, "TravelTime API"
    facilities = facilities.copy()
    facilities["latitude"] = facilities.latitude.astype(float)
    facilities["longitude"] = facilities.longitude.astype(float)
    valid = facilities.latitude.between(-90, 90) & facilities.longitude.between(-180, 180)
    invalid_ids = facilities.loc[~valid, "facility_id"].astype(str).tolist()
    facilities = facilities.loc[valid]
    if facilities.empty:
        return {fid: {"travel_time_minutes": None, "distance_km": None,
                      "basis": "invalid_coordinates"} for fid in invalid_ids}, "TravelTime API"
    if len(facilities) > 100000:
        raise ValueError("Time Filter Fast accepts at most 100,000 destinations per request.")
    if max_travel_time_seconds < 1 or max_travel_time_seconds > 10800:
        raise ValueError("Time Filter Fast travel_time must be between 1 and 10,800 seconds.")
    locations = [{
        "id": str(row["facility_id"]),
        "coords": {"lat": float(row["latitude"]), "lng": float(row["longitude"])},
    } for row in facilities.to_dict("records")]
    origin_id = "user_origin"
    locations.append({
        "id": origin_id,
        "coords": {"lat": float(origin[0]), "lng": float(origin[1])},
    })
    payload = {
        "locations": locations,
        "arrival_searches": {"one_to_many": [{
            "id": "user_to_facilities",
            "departure_location_id": origin_id,
            "arrival_location_ids": [str(row["facility_id"]) for row in facilities.to_dict("records")],
            "transportation": {"type": "driving+ferry"},
            "travel_time": int(max_travel_time_seconds),
            "arrival_time_period": departure_period,
            "properties": ["travel_time"],
        }]},
    }
    response = requests.post(
        TRAVELTIME_URL,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "X-Application-Id": app_id,
            "X-Api-Key": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    by_id = {}
    for result in body.get("results", []):
        for item in result.get("locations", []):
            properties = item.get("properties", {})
            if isinstance(properties, list):
                properties = properties[0] if properties else {}
            by_id[item["id"]] = {
                "travel_time_minutes": properties.get("travel_time", None) / 60
                    if properties.get("travel_time") is not None else None,
                "distance_km": None,
                "basis": "TravelTime driving matrix",
            }
    for result in body.get("results", []):
        for unreachable in result.get("unreachable", []):
            by_id[unreachable] = {"travel_time_minutes": None, "distance_km": None,
                                  "basis": "unreachable"}
    for facility_id in invalid_ids:
        by_id[facility_id] = {"travel_time_minutes": None, "distance_km": None,
                              "basis": "invalid_coordinates"}
    return by_id, "TravelTime Fast driving matrix"

