#!/usr/bin/env python3
"""
Generate a static HTML map from iNaturalist observations using Leaflet. Observation images are available in the tooltip.

Modes of operation
------------------
1. From a saved CSV:
       python3 generate_html_map.py --csv-path data/observations.csv

2. Fetch live from the iNaturalist API (requires --date-from, --date-to, --geojson):
       python3 generate_html_map.py \\
           --geojson chko_latorica.geojson \\
           --date-from 2026-07-26 --date-to 2026-07-28

   Observations are fetched for the bounding box of the polygon, then filtered
   to those that fall inside the exact polygon boundary.
   The downloaded CSV and metadata are saved to the data/ directory.
   --geojson may be a bare filename (looked up in data/) or a full path.
"""

import argparse
import json
import sys
from datetime import date as _date, datetime as _datetime
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
import shared


# ---------------------------------------------------------------------------
# Resolve a GeoJSON argument to an absolute path
# ---------------------------------------------------------------------------

def _resolve_geojson(value: str) -> Path:
    """Return a Path for a geojson argument (filename or path)."""
    p = Path(value)
    if p.is_absolute():
        return p
    # Bare filename → look in DATA_DIR first
    candidate = shared.DATA_DIR / value
    if candidate.exists():
        return candidate
    # Fall back to treating it as a relative path from cwd
    return p


# ---------------------------------------------------------------------------
# HTML map builder (Leaflet)
# ---------------------------------------------------------------------------

def build_html_map(df: pd.DataFrame, geojson_data, poly, args, title: str = "") -> str:
    """Build a Leaflet HTML map with observation markers and an optional polygon."""

    # Summary statistics shown in the legend metadata panel.
    total_observations = len(df)

    users = (
        df["user"]
        if "user" in df.columns
        else pd.Series(dtype="object")
    )
    unique_contributors = users.dropna().astype(str).str.strip()
    unique_contributors = unique_contributors[
        (unique_contributors != "")
        & (unique_contributors.str.lower() != "nan")
        & (unique_contributors.str.lower() != "none")
    ].nunique()

    taxa = (
        df["taxon_name"]
        if "taxon_name" in df.columns
        else pd.Series(dtype="object")
    )
    unique_taxa = taxa.dropna().astype(str).str.strip()
    unique_taxa = unique_taxa[
        (unique_taxa != "")
        & (unique_taxa.str.lower() != "nan")
        & (unique_taxa.str.lower() != "none")
    ].nunique()

    observed_from = ""
    observed_to = ""
    if "observed_on" in df.columns and not df.empty:
        observed = df["observed_on"].dropna().astype(str).str.slice(0, 10)
        observed = observed[(observed != "") & (observed.str.lower() != "nan")]
        if not observed.empty:
            observed_from = observed.min()
            observed_to = observed.max()

    # Calculate bounds for auto-zoom
    if poly is not None:
        bounds = poly.bounds  # (minx, miny, maxx, maxy)
        sw_lat, sw_lon = bounds[1], bounds[0]
        ne_lat, ne_lon = bounds[3], bounds[2]
        fit_bounds = f"map.fitBounds([[{sw_lat}, {sw_lon}], [{ne_lat}, {ne_lon}]]);"
    else:
        if not df.empty:
            lats = pd.to_numeric(df["latitude"], errors="coerce")
            lons = pd.to_numeric(df["longitude"], errors="coerce")
            valid = lats.notna() & lons.notna()
            if valid.any():
                sw_lat, ne_lat = lats[valid].min(), lats[valid].max()
                sw_lon, ne_lon = lons[valid].min(), lons[valid].max()
                fit_bounds = f"map.fitBounds([[{sw_lat}, {sw_lon}], [{ne_lat}, {ne_lon}]]);"
            else:
                fit_bounds = "map.setView([48.7, 19.1], 9);"
        else:
            fit_bounds = "map.setView([48.7, 19.1], 9);"

    # Build observation markers
    markers_js = ""
    if not df.empty:
        lats = pd.to_numeric(df["latitude"], errors="coerce")
        lons = pd.to_numeric(df["longitude"], errors="coerce")
        valid = lats.notna() & lons.notna()
        valid_df = df[valid]

        for idx, row in valid_df.iterrows():
            lat, lon = float(lats[idx]), float(lons[idx])
            taxon = str(row.get("taxon_name", "Unknown"))
            common = str(row.get("common_name", ""))
            obs_date = str(row.get("observed_on", ""))
            place = str(row.get("place_guess", ""))
            user = str(row.get("user", ""))
            grade = str(row.get("quality_grade", "unknown"))
            url = str(row.get("url", ""))

            photo_url = str(row.get("photo_url", ""))
            img_html = f'<img src="{photo_url}" style="width:100%;max-width:100%;border-radius:4px;margin-bottom:4px;display:block;"><br>' if photo_url and photo_url != "nan" else ""
            url_html = f'<a href="{url}" target="_blank">View on iNaturalist</a><br>' if url and url != "nan" else ""
            popup_html = (
                f"{img_html}"
                f"<b>{taxon}</b><br>"
                f"{common}<br>"
                f"{obs_date}<br>"
                f"{place}<br>"
                f"\U0001f464 {user}<br>"
                f"<strong>Grade: {grade}</strong><br>"
                f"{url_html}"
            ).replace('"', '\\"')

            markers_js += f"""
    L.circleMarker([{lat}, {lon}], {{
        radius: 6,
        color: 'green',
        weight: 2,
        opacity: 0.8,
        fillOpacity: 0.6
    }}).bindPopup("{popup_html}", {{maxWidth: 400, minWidth: 400}}).addTo(map);
    """

    # Build polygon GeoJSON layer
    polygon_js = ""
    if geojson_data:
        polygon_js = f"""
    var geoJsonData = {json.dumps(geojson_data)};
    L.geoJSON(geoJsonData, {{
        style: {{
            color: 'royalblue',
            weight: 2,
            opacity: 0.8,
            fillOpacity: 0.1
        }}
    }}).addTo(map);
"""

    page_title = f"iNaturalist – VSTOP {args.date_from[:4]}" if title else "iNaturalist Observations Map"
    now = _datetime.now()
    timestamp_str = f"Mapa bola naposledy aktualizovaná {now.day:02d}.{now.month:02d}.{now.year} o {now.hour:02d}:{now.minute:02d}"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        #map {{ width: 100vw; height: 100vh; }}
        .leaflet-popup-content {{ font-size: 13px; line-height: 1.5; }}
        .leaflet-popup-content b {{ font-size: 14px; }}
        .leaflet-popup-content strong {{ color: #333; }}
        #legend {{
            position: fixed;
            top: 10px;
            right: 10px;
            z-index: 1000;
            width: min(320px, calc(100vw - 20px));
            background: rgba(255,255,255,0.92);
            border: 1px solid rgba(0,0,0,0.12);
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
            padding: 10px 12px;
            color: #2f2f2f;
            font-size: 13px;
            line-height: 1.4;
        }}
        #legend h3 {{
            margin: 0 0 6px 0;
            font-size: 14px;
            line-height: 1.2;
        }}
        .legend-row {{
            display: flex;
            justify-content: space-between;
            gap: 10px;
            margin: 2px 0;
        }}
        .legend-row span:last-child {{
            font-weight: 600;
        }}
        .legend-separator {{
            border-top: 1px solid rgba(0,0,0,0.1);
            margin: 8px 0;
        }}
        #timestamp {{
            position: fixed;
            bottom: 10px;
            right: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.85);
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            color: #444;
            box-shadow: 0 1px 4px rgba(0,0,0,0.2);
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <div id="legend">
        <h3>VS TOP {args.date_from[:4] if args.date_from else now.year} - záznamy v iNaturalist</h3>
        <div class="legend-row"><span>Pozorovania</span><span>{total_observations}</span></div>
        <div class="legend-row"><span>Prispievatelia</span><span>{unique_contributors}</span></div>
        <div class="legend-row"><span>Taxóny</span><span>{unique_taxa}</span></div>
        <div class="legend-separator"></div>
        <div class="legend-row"><span>Od </span><span>{observed_from or "-"}</span></div>
        <div class="legend-row"><span>Do </span><span>{observed_to or "-"}</span></div>
    </div>
    <div id="timestamp">{timestamp_str}</div>
    <script>
        var map = L.map('map').setView([48.7, 19.1], 9);

        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            attribution: '\\u00a9 OpenStreetMap contributors',
            maxZoom: 19
        }}).addTo(map);

        {polygon_js}
        {markers_js}
        {fit_bounds}
    </script>
</body>
</html>
"""
    return html


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a static Leaflet HTML map from iNaturalist observations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    source = parser.add_argument_group("data source (use one mode)")
    source.add_argument(
        "--csv-path",
        metavar="PATH",
        help="Load observations from an existing CSV file.",
    )
    source.add_argument(
        "--date-from",
        metavar="YYYY-MM-DD",
        help="Start of observation date range (fetch mode).",
    )
    source.add_argument(
        "--date-to",
        metavar="YYYY-MM-DD",
        help="End of observation date range (fetch mode).",
    )
    source.add_argument(
        "--geojson",
        metavar="FILE",
        help=(
            "GeoJSON polygon file — bare filename (looked up in data/) or a path. "
            "Required in fetch mode; optional in CSV mode (overrides yaml metadata)."
        ),
    )
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="Output HTML file path. Defaults to <csv>.html or data/<name>.html.",
    )

    args = parser.parse_args()

    fetch_mode = bool(args.date_from or args.date_to)
    csv_mode = bool(args.csv_path)

    if not csv_mode and not fetch_mode:
        parser.error("Provide either --csv-path or --date-from/--date-to/--geojson.")
    if fetch_mode and not (args.date_from and args.date_to and args.geojson):
        parser.error("Fetch mode requires --date-from, --date-to, and --geojson.")

    geojson_data = None
    poly = None
    geojson_label = ""

    # ------------------------------------------------------------------
    # CSV mode: load existing file
    # ------------------------------------------------------------------
    if csv_mode:
        csv_path = Path(args.csv_path)
        if not csv_path.exists():
            print(f"Error: CSV file not found: {csv_path}")
            sys.exit(1)

        try:
            df = pd.read_csv(csv_path)
            print(f"Loaded {len(df)} records from {csv_path.name}")
        except Exception as exc:
            print(f"Error: Could not load CSV: {exc}")
            sys.exit(1)

        # Resolve GeoJSON: explicit arg > yaml metadata
        geojson_filename = args.geojson
        if not geojson_filename:
            yaml_path = csv_path.with_suffix(".yaml")
            if yaml_path.exists():
                try:
                    with open(yaml_path, encoding="utf-8") as fh:
                        meta = yaml.safe_load(fh)
                    geojson_filename = meta.get("geojson_file")
                    if geojson_filename:
                        print(f"Using polygon from metadata: {geojson_filename}")
                except Exception as exc:
                    print(f"Warning: Could not load metadata: {exc}")

        if geojson_filename:
            geojson_path = _resolve_geojson(geojson_filename)
            geojson_data, poly, _, msg = shared.parse_geojson(geojson_path)
            if poly:
                print(f"Polygon loaded: {geojson_path.name}")
            else:
                print(f"Warning: {msg}")
            geojson_label = geojson_path.stem

        html_path = Path(args.output) if args.output else csv_path.with_suffix(".html")

    # ------------------------------------------------------------------
    # Fetch mode: download from iNaturalist API
    # ------------------------------------------------------------------
    else:
        geojson_path = _resolve_geojson(args.geojson)
        geojson_data, poly, _, msg = shared.parse_geojson(geojson_path)
        if poly is None:
            print(f"Error: {msg}")
            sys.exit(1)
        print(msg)
        geojson_label = geojson_path.stem

        print(f"Fetching observations {args.date_from} – {args.date_to} …")
        df, status = shared.fetch_observations(
            geojson_path, args.date_from, args.date_to, num_records=10_000
        )
        print(status)

        if df.empty:
            print("No observations fetched — HTML map will be empty.")

        # Save CSV + metadata
        timestamp = _date.today().isoformat()
        csv_name = (
            f"inaturalist_{args.date_from}_{args.date_to}"
            f"_{len(df)}_obs_{timestamp}.csv"
        )
        csv_path = shared.DATA_DIR / csv_name
        df.to_csv(csv_path, index=False)
        shared.save_metadata(
            csv_path, geojson_path.name, args.date_from, args.date_to, 10_000, len(df)
        )
        print(f"CSV saved to {csv_path}")

        html_path = Path(args.output) if args.output else csv_path.with_suffix(".html")

    # ------------------------------------------------------------------
    # Build and save HTML
    # ------------------------------------------------------------------
    title = f"{geojson_label}  {args.date_from or ''}–{args.date_to or ''}".strip(" –")
    html = build_html_map(df, geojson_data, poly, args, title)

    try:
        html_path.write_text(html, encoding="utf-8")
        print(f"✓ Map saved to {html_path}")
    except Exception as exc:
        print(f"Error: Could not save HTML: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()

