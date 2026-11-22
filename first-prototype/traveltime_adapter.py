"""TravelTime Fast matrix client for origin-to-facility driving estimates."""
from __future__ import annotations

import os

import requests

TRAVELTIME_URL = "https://api.traveltimeapp.com/v4/time-filter/fast"


def travel_time_matrix(origin, facilities, departure_period="weekday_morning",
                       max_travel_time_seconds=10800, app_id=None, api_key=None):
    """Return facility ID -> route minutes from one TravelTime one-to-many request."""
    app_id = app_id or os.getenv("TRAVELTIME_APP_ID")
    api_key = api_key or os.getenv("TRAVELTIME_API_KEY")
    if not app_id or not api_key:
        raise RuntimeError("TravelTime credentials are missing from this app's environment.")
    if facilities.empty:
        return {}
    if len(facilities) > 100000:
        raise ValueError("TravelTime Fast accepts at most 100,000 destinations per request.")
    if not 1 <= int(max_travel_time_seconds) <= 10800:
        raise ValueError("TravelTime Fast supports a maximum search time of 10,800 seconds.")
    records = facilities[["facility_id", "latitude", "longitude"]].copy()
    records["latitude"] = records.latitude.astype(float)
    records["longitude"] = records.longitude.astype(float)
    records = records.dropna(subset=["facility_id", "latitude", "longitude"])
    records = records.drop_duplicates("facility_id")
    origin_id = "carefinder_origin"
    destination_ids = [str(fid) for fid in records.facility_id]
    locations = [{"id": str(row.facility_id), "coords": {"lat": row.latitude, "lng": row.longitude}}
                 for row in records.itertuples(index=False)]
    locations.append({"id": origin_id, "coords": {"lat": float(origin[0]), "lng": float(origin[1])}})
    payload = {
        "locations": locations,
        "arrival_searches": {"one_to_many": [{
            "id": "origin_to_facilities",
            "departure_location_id": origin_id,
            "arrival_location_ids": destination_ids,
            "transportation": {"type": "driving+ferry"},
            "travel_time": int(max_travel_time_seconds),
            "arrival_time_period": departure_period,
            "properties": ["travel_time"],
        }]},
    }
    response = requests.post(
        TRAVELTIME_URL, json=payload,
        headers={"Content-Type": "application/json", "X-Application-Id": app_id, "X-Api-Key": api_key},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    minutes = {}
    for result in body.get("results", []):
        for item in result.get("locations", []):
            props = item.get("properties", {})
            if isinstance(props, list):
                props = props[0] if props else {}
            seconds = props.get("travel_time")
            minutes[str(item["id"])] = float(seconds) / 60.0 if seconds is not None else None
        for unreachable in result.get("unreachable", []):
            minutes[str(unreachable)] = None
    # Omitted destinations are not silently assigned a straight-line route.
    for facility_id in destination_ids:
        minutes.setdefault(facility_id, None)
    return minutes
