"""
Shared utilities for iNaturalist scripts.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests
import yaml
from shapely.geometry import Point, shape

INATURALIST_API = "https://api.inaturalist.org/v1/observations?locale=sk"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# iNaturalist API max per-page
_MAX_PER_PAGE = 200


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def list_geojson_files() -> list[str]:
    """Return filenames of all GeoJSON files in DATA_DIR."""
    return sorted(p.name for p in DATA_DIR.glob("*.geojson"))


def parse_geojson(geojson_path: Path) -> tuple[dict | None, object | None, dict, str]:
    """Parse a GeoJSON file.

    Returns:
        (raw_geojson, shapely_poly, bbox_dict, message)
        raw_geojson and shapely_poly are None on failure.
        bbox_dict keys: swlng, swlat, nelng, nelat.
    """
    if not geojson_path.exists():
        return None, None, {}, f"GeoJSON file not found: {geojson_path}"
    try:
        with open(geojson_path, encoding="utf-8") as fh:
            geojson = json.load(fh)
    except Exception as exc:
        return None, None, {}, f"Cannot read file: {exc}"

    try:
        geotype = geojson.get("type", "")
        if geotype == "FeatureCollection":
            features = geojson.get("features", [])
            if not features:
                return geojson, None, {}, "FeatureCollection contains no features."
            geometry = features[0]["geometry"]
        elif geotype == "Feature":
            geometry = geojson["geometry"]
        else:
            geometry = geojson  # assume raw Geometry object

        poly = shape(geometry)
        if not poly.is_valid:
            poly = poly.buffer(0)  # attempt repair

        minx, miny, maxx, maxy = poly.bounds  # (swlng, swlat, nelng, nelat)
        bbox = {"swlng": minx, "swlat": miny, "nelng": maxx, "nelat": maxy}
        n = len(list(poly.exterior.coords))
        return geojson, poly, bbox, f"Polygon loaded ({n} vertices)."
    except Exception as exc:
        return geojson, None, {}, f"Cannot parse geometry: {exc}"


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def build_params(bbox: dict, date_from: str, date_to: str, per_page: int, page: int) -> dict:
    return {
        **bbox,
        "d1": date_from,
        "d2": date_to,
        "per_page": per_page,
        "page": page,
        "order": "desc",
        "order_by": "observed_on",
        "geo": "true",  # only georeferenced observations
    }


def in_polygon(obs: dict, poly) -> bool:
    """Return True if the observation's coordinates fall inside the polygon."""
    location = obs.get("location") or ""
    if "," not in location:
        return False
    try:
        lat_s, lon_s = location.split(",", 1)
        return poly.contains(Point(float(lon_s), float(lat_s)))
    except (ValueError, TypeError):
        return False


def in_date_range(obs: dict, date_from: str, date_to: str) -> bool:
    """Return True if observed_on falls within [date_from, date_to]."""
    observed_on = (obs.get("observed_on") or "")[:10]
    if not observed_on:
        return False
    return date_from <= observed_on <= date_to


def obs_to_row(obs: dict) -> dict:
    """Convert a raw iNaturalist API observation dict to a flat CSV row."""
    taxon = obs.get("taxon") or {}
    location = obs.get("location") or ""
    lat, lon = ("", "")
    if "," in location:
        parts = location.split(",", 1)
        lat, lon = parts[0].strip(), parts[1].strip()
    photos = obs.get("photos") or []
    photo_url = photos[0].get("url", "").replace("/square.", "/small.") if photos else ""
    return {
        "id": obs.get("id"),
        "observed_on": obs.get("observed_on"),
        "taxon_name": taxon.get("name"),
        "common_name": taxon.get("preferred_common_name"),
        "iconic_taxon": taxon.get("iconic_taxon_name"),
        "place_guess": obs.get("place_guess"),
        "latitude": lat,
        "longitude": lon,
        "quality_grade": obs.get("quality_grade"),
        "num_identification_agreements": obs.get("num_identification_agreements"),
        "user": (obs.get("user") or {}).get("name"),
        "url": f"https://www.inaturalist.org/observations/{obs.get('id')}?locale=sk",
        "photo_url": photo_url,
    }


def fetch_observations(
    geojson_path: Path,
    date_from: str,
    date_to: str,
    num_records: int,
) -> tuple[pd.DataFrame, str]:
    """Fetch observations from iNaturalist API, filtered to polygon.

    Args:
        geojson_path: Path to the GeoJSON polygon file.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        num_records: Maximum number of polygon-filtered records to collect.

    Returns:
        (df, status_message) — does not save; callers handle persistence.
    """
    _, poly, bbox, msg = parse_geojson(geojson_path)
    if poly is None:
        return pd.DataFrame(), msg

    num_records = max(1, int(num_records))
    records: list[dict] = []
    page = 1
    bbox_exhausted = False

    while len(records) < num_records and not bbox_exhausted:
        params = build_params(bbox, date_from, date_to, per_page=_MAX_PER_PAGE, page=page)
        try:
            resp = requests.get(INATURALIST_API, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            df_partial = pd.DataFrame([obs_to_row(o) for o in records])
            return df_partial, f"API HTTP error on page {page}: {exc}"
        except Exception as exc:
            df_partial = pd.DataFrame([obs_to_row(o) for o in records])
            return df_partial, f"Request failed on page {page}: {exc}"

        batch = data.get("results", [])
        if not batch:
            break
        records.extend(obs for obs in batch if in_polygon(obs, poly))
        if len(batch) < _MAX_PER_PAGE:
            bbox_exhausted = True
        page += 1

    in_range = [obs for obs in records[:num_records] if in_date_range(obs, date_from, date_to)]
    rows = [obs_to_row(obs) for obs in in_range]
    df = pd.DataFrame(rows)
    return df, f"Fetched {len(df)} records."


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def save_metadata(
    csv_path: Path,
    geojson_filename: str,
    date_from: str,
    date_to: str,
    num_records_requested: int,
    num_records_fetched: int,
) -> None:
    meta = {
        "csv_file": csv_path.name,
        "geojson_file": geojson_filename,
        "date_from": date_from,
        "date_to": date_to,
        "num_records_requested": num_records_requested,
        "num_records_fetched": num_records_fetched,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(csv_path.with_suffix(".yaml"), "w", encoding="utf-8") as fh:
        yaml.dump(meta, fh, allow_unicode=True, sort_keys=False)


def list_saved_results() -> list[tuple[str, str]]:
    """Return (display_label, yaml_filename) pairs for all saved result sets."""
    choices = []
    for yaml_path in sorted(DATA_DIR.glob("*.yaml"), reverse=True):
        try:
            with open(yaml_path, encoding="utf-8") as fh:
                meta = yaml.safe_load(fh)
            geojson = meta.get("geojson_file", "?").replace(".geojson", "")
            label = (
                f"{geojson}  |  "
                f"{meta.get('date_from', '?')} \u2013 {meta.get('date_to', '?')}  |  "
                f"{meta.get('num_records_fetched', '?')} records  |  "
                f"{str(meta.get('timestamp', '?'))[:10]}"
            )
            choices.append((label, yaml_path.name))
        except Exception:
            choices.append((yaml_path.stem, yaml_path.name))
    return choices
