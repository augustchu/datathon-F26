"""Render the care finder's map from the already-ranked facility rows."""

from html import escape
import re

import folium
import pandas as pd


def safe_text(value):
    """Escape text inserted into Folium's HTML and JavaScript templates."""
    return escape(str(value)).replace("`", "&#96;").replace("$", "&#36;").replace("\\", "&#92;")


def rank_icon(rank):
    return folium.DivIcon(
        html=(
            '<div style="position:relative;width:36px;height:42px">'
            '<div style="position:absolute;left:3px;top:3px;width:30px;height:30px;'
            'background:#1d70b8;color:#fff;border:2px solid #fff;border-radius:50% 50% 50% 0;'
            'transform:rotate(-45deg);box-shadow:0 1px 4px rgba(0,0,0,.5)">'
            '<div style="display:grid;place-items:center;width:100%;height:100%;'
            f'transform:rotate(45deg);font:bold 13px sans-serif">{rank}</div></div></div>'
        ),
        icon_size=(36, 42),
        icon_anchor=(18, 42),
    )


def facility_popup(row, rank, point, using_routes):
    location = ", ".join(dict.fromkeys(
        str(value).strip() for value in (row.city, row.district, row.state, row.pin)
        if pd.notna(value) and str(value).strip()
    ))
    phone = str(row.phone).strip() if pd.notna(row.phone) else ""
    phone_text = safe_text(phone or "Not listed")
    dial = re.sub(r"[\s().-]", "", phone)
    if re.fullmatch(r"\+?[0-9]{7,15}", dial):
        phone_text = f'<a href="tel:{dial}">{phone_text}</a>'
    drive = f"<b>Estimated drive:</b> {row.travel_time_minutes:.0f} min<br>" if using_routes else ""
    beds = "Unavailable or withheld"
    if pd.notna(row.beds_for_scenario):
        label = "Estimated" if str(row.capacity_release_status).casefold() == "xgboost_estimate" else "Released"
        beds = f"{label}: {row.beds_for_scenario:g} beds"
    directions = f"https://www.google.com/maps/dir/?api=1&amp;destination={point[0]:.6f},{point[1]:.6f}"
    return (
        '<div style="min-width:210px;font:13px/1.45 sans-serif">'
        f"<b>{rank}. {safe_text(row.facility_name)}</b><br>"
        f"<b>Location:</b> {safe_text(location or 'Not listed')}<br>"
        f"<b>Phone:</b> {phone_text}<br>"
        f"{drive}<b>Straight-line distance:</b> {row.distance_km:.1f} km<br>"
        f"<b>Bed capacity:</b> {beds}<br>"
        f'<a href="{directions}" target="_blank" rel="noopener noreferrer">Directions</a><br>'
        "<i>Call the facility to confirm the service and current availability.</i></div>"
    )


def build_map(origin=None, ranked=None, *, origin_label="", using_routes=False):
    """Show the origin and the same rows, in the same order, as the result cards."""
    center = (22.5, 80.0) if origin is None else origin
    result = folium.Map(location=center, zoom_start=5 if origin is None else 11,
                        tiles="OpenStreetMap", control_scale=True)
    if origin is None:
        return result.get_root().render()

    folium.Marker(origin, tooltip=f"Your location: {safe_text(origin_label)}",
                  icon=folium.Icon(color="red", icon="home", prefix="fa")).add_to(result)
    if ranked is not None and not ranked.empty:
        points = [origin]
        for rank, row in enumerate(ranked.itertuples(index=False), 1):
            point = (float(row.latitude), float(row.longitude))
            folium.Marker(
                point, icon=rank_icon(rank), tooltip=f"{rank}. {safe_text(row.facility_name)}",
                popup=folium.Popup(facility_popup(row, rank, point, using_routes), max_width=320),
            ).add_to(result)
            points.append(point)
        result.fit_bounds(points, padding=(30, 30))
    return result.get_root().render()
