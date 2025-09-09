import json, math
from datetime import datetime, timedelta
from flask import current_app

# Simple Haversine distance (meters)
EARTH_R = 6371000


def haversine_m(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dl / 2) ** 2
    )
    return 2 * EARTH_R * math.asin(math.sqrt(a))


def route_points_from_geojson(route_geojson_text):
    if not route_geojson_text:
        return []
    data = json.loads(route_geojson_text)
    coords = []
    if data.get("type") == "LineString":
        coords = data["coordinates"]  # [lon,lat]
    elif (
        data.get("type") == "Feature"
        and data.get("geometry", {}).get("type") == "LineString"
    ):
        coords = data["geometry"]["coordinates"]
    return [(c[1], c[0]) for c in coords]


def min_distance_to_polyline_m(lat, lng, polyline_latlngs):
    if not polyline_latlngs:
        return float("inf")
    return min(haversine_m(lat, lng, p[0], p[1]) for p in polyline_latlngs)


def overdue_threshold():
    # 24 hours
    return timedelta(hours=24)


def default_buffer_m():
    try:
        return int(current_app.config.get("DEFAULT_BUFFER_METERS", 150))
    except Exception:
        return 150
