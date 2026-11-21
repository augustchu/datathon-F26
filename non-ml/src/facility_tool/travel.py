"""Optional point-to-point route estimates from the TravelTime time-filter API."""

from __future__ import annotations

from datetime import datetime, timezone
from http.client import HTTPException
import json
from math import isfinite
from numbers import Integral
import os
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pandas as pd

from .recommend import haversine_km


ENDPOINT = "https://api.traveltimeapp.com/v4/time-filter"
SOURCE = "TravelTime time-filter"
SOURCE_URL = "https://docs.traveltime.com/api/reference/travel-time-distance-matrix"
MODES = ("driving", "walking", "cycling", "public_transport")
SEARCH_ID = "from-origin"
BLOCKED_QUALITIES = (
    "missing", "invalid", "conflict", "ambiguous", "placeholder", "centroid",
    "approximate", "unresolved",
)
MAX_RESPONSE_BYTES = 2_000_000


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, status, message, headers, url):
        return None


def _post_json(payload, *, app_id, api_key, timeout):
    request = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Application-Id": app_id,
            "X-Api-Key": api_key,
        },
        method="POST",
    )
    with build_opener(_NoRedirects()).open(request, timeout=timeout) as response:
        data = response.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("Unexpected TravelTime response size.")
    return json.loads(data)


def _coordinate_pair(values):
    try:
        lat, lon = values
        if pd.api.types.is_bool(lat) or pd.api.types.is_bool(lon):
            return None
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat, lon) == (0, 0):
        return None
    return lat, lon


def _site_point(row):
    quality = str(row.get("location_quality", "")).casefold()
    conflict = str(row.get("location_conflict", "")).strip().casefold()
    if conflict in {"true", "1", "yes"} or any(term in quality for term in BLOCKED_QUALITIES):
        return None
    return _coordinate_pair((row["lat"], row["lon"]))


def _departure_timestamp(value):
    if value is None:
        value = datetime.now(timezone.utc)
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("departure_time must be a timezone-aware datetime or ISO 8601 string.") from None
    if not isinstance(value, datetime) or pd.isna(value) or value.utcoffset() is None:
        raise ValueError("departure_time must be a timezone-aware datetime or ISO 8601 string.")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _credential(value, environment_name):
    value = os.environ.get(environment_name) if value is None else value
    return value.strip() if isinstance(value, str) else ""


def _response_estimates(response, window_seconds):
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise ValueError("Unexpected TravelTime response structure.")
    searches = [row for row in response["results"] if isinstance(row, dict) and row.get("search_id") == SEARCH_ID]
    if len(searches) != 1:
        raise ValueError("TravelTime response did not identify the requested search.")
    search = searches[0]
    if not isinstance(search.get("locations"), list) or not isinstance(search.get("unreachable"), list):
        raise ValueError("TravelTime response did not include route results.")
    estimates, seen = {}, set()
    unreachable = {key for key in search["unreachable"] if isinstance(key, str)}
    for row in search["locations"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        key = row["id"]
        if key in seen:
            estimates.pop(key, None)
            continue
        seen.add(key)
        properties = row.get("properties")
        if not isinstance(properties, list):
            continue
        seconds = [value.get("travel_time") for value in properties if isinstance(value, dict)]
        valid_seconds = [
            value for value in seconds
            if type(value) in (int, float) and 0 <= value <= window_seconds
        ]
        if valid_seconds and key not in unreachable:
            estimates[key] = min(valid_seconds) / 60
    return estimates, unreachable - seen


def estimate_travel(
    origin,
    facilities: pd.DataFrame,
    *,
    enabled: bool = False,
    mode: str = "driving",
    departure_time=None,
    max_travel_minutes: int = 90,
    max_destinations: int = 50,
    app_id: str | None = None,
    api_key: str | None = None,
    timeout: float = 12,
) -> pd.DataFrame:
    """Request a single, bounded matrix for one origin and travel mode.

    ``origin`` is ``(latitude, longitude)`` and facility rows require unique IDs
    and ``lat`` and ``lon`` columns. The closest routeable points by great-circle
    distance enter the matrix first, breaking ties by facility ID. The returned
    table preserves all input IDs and their order. A missing estimate remains
    null, including destinations the provider lists as unreachable within the
    search window. Use the same returned table across recommendation modes.

    The opt-in request sends coordinates, a departure timestamp, transport
    mode, a search window, and temporary destination IDs to TravelTime. The
    caller can provide credentials or set TRAVELTIME_APP_ID and
    TRAVELTIME_API_KEY. Destinations omitted or unreachable within this search
    window may still have viable routes.
    """
    required = {"facility_id", "lat", "lon"}
    if not required.issubset(facilities.columns):
        raise ValueError("facilities must contain facility_id, lat, and lon columns.")
    ids = facilities["facility_id"]
    if ids.isna().any() or ids.astype(str).str.strip().eq("").any() or ids.astype(str).duplicated().any():
        raise ValueError("facility_id must be present and unique for each destination.")

    result = pd.DataFrame({
        "facility_id": ids.reset_index(drop=True),
        "travel_minutes": float("nan"),
        "travel_source": "",
        "travel_status": "disabled",
        "travel_mode": "",
        "departure_time": "",
        "max_travel_minutes": None,
        "travel_source_url": "",
    })
    if not enabled or result.empty:
        return result
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}.")
    if pd.api.types.is_bool(max_destinations) or not isinstance(max_destinations, Integral) or not 1 <= max_destinations <= 200:
        raise ValueError("max_destinations must be a whole number from 1 through 200.")
    if pd.api.types.is_bool(max_travel_minutes) or not isinstance(max_travel_minutes, Integral) or not 1 <= max_travel_minutes <= 240:
        raise ValueError("max_travel_minutes must be a whole number from 1 through 240.")
    if type(timeout) not in (int, float) or not isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("timeout must be a positive number of seconds up to 30.")

    result["travel_mode"] = mode
    result["max_travel_minutes"] = int(max_travel_minutes)
    app_id = _credential(app_id, "TRAVELTIME_APP_ID")
    api_key = _credential(api_key, "TRAVELTIME_API_KEY")
    if not (app_id and api_key):
        result["travel_status"] = "missing_credentials"
        return result
    origin = _coordinate_pair(origin)
    if origin is None:
        result["travel_status"] = "invalid_origin"
        return result

    result["travel_status"] = "invalid_destination"
    candidates = []
    for position, row in enumerate(facilities.to_dict("records")):
        point = _site_point(row)
        if point is not None:
            result.at[position, "travel_status"] = "outside_request_limit"
            candidates.append((haversine_km(*origin, *point), str(row["facility_id"]), position, point))
    if not candidates:
        return result

    departure = _departure_timestamp(departure_time)
    window_seconds = int(max_travel_minutes) * 60
    locations = [{"id": "origin", "coords": {"lat": origin[0], "lng": origin[1]}}]
    destination_positions = {}
    for offset, (_, _, position, (lat, lon)) in enumerate(sorted(candidates)[:max_destinations], start=1):
        key = f"destination-{offset}"
        locations.append({"id": key, "coords": {"lat": lat, "lng": lon}})
        destination_positions[key] = position
    chosen = list(destination_positions.values())
    result.loc[chosen, "departure_time"] = departure
    result.loc[chosen, "travel_status"] = "missing_result"
    payload = {
        "locations": locations,
        "departure_searches": [{
            "id": SEARCH_ID,
            "departure_location_id": "origin",
            "arrival_location_ids": list(destination_positions),
            "departure_time": departure,
            "transportation": {"type": mode},
            "travel_time": window_seconds,
            "properties": ["travel_time"],
        }],
    }
    try:
        response = _post_json(payload, app_id=app_id, api_key=api_key, timeout=timeout)
        estimates, unreachable = _response_estimates(response, window_seconds)
    except HTTPError as error:
        status = {401: "credential_or_plan_rejected", 403: "credential_or_plan_rejected", 429: "rate_limited"}
        result.loc[chosen, "travel_status"] = status.get(
            error.code, "provider_unavailable" if 500 <= error.code <= 599 else "request_rejected",
        )
        error.close()
        return result
    except (OSError, HTTPException):
        result.loc[chosen, "travel_status"] = "network_error"
        return result
    except (ValueError, TypeError):
        result.loc[chosen, "travel_status"] = "invalid_response"
        return result

    for key, position in destination_positions.items():
        if key in estimates:
            result.at[position, "travel_minutes"] = estimates[key]
            result.at[position, "travel_source"] = SOURCE
            result.at[position, "travel_source_url"] = SOURCE_URL
            result.at[position, "travel_status"] = "estimated"
        elif key in unreachable:
            result.at[position, "travel_status"] = "not_routed_within_limit"
    return result
