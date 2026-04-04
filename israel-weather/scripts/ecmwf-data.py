#!/usr/bin/env python3
"""Generate ECMWF chart URLs and pull forecast data for Israel locations.

Dual purpose:
  1. Build chart URLs for visual map links (charts.ecmwf.int)
  2. Pull numerical forecast data via Open-Meteo ECMWF API (JSON)

Usage:
  python3 ecmwf-chart-urls.py                          # default: Tzurit, 3 days, charts + data
  python3 ecmwf-chart-urls.py --mode charts             # chart URLs only
  python3 ecmwf-chart-urls.py --mode data               # forecast data only
  python3 ecmwf-chart-urls.py --lat 32.08 --lon 34.78 --days 5  # custom location
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Israel locations
# ---------------------------------------------------------------------------
LOCATIONS = {
    "tzurit":     {"lat": 32.902, "lon": 35.247, "label": "Tzurit"},
    "tel_aviv":   {"lat": 32.09, "lon": 34.78, "label": "Tel Aviv"},
    "jerusalem":  {"lat": 31.77, "lon": 35.21, "label": "Jerusalem"},
    "haifa":      {"lat": 32.79, "lon": 34.99, "label": "Haifa"},
    "eilat":      {"lat": 29.56, "lon": 34.95, "label": "Eilat"},
    "beer_sheva": {"lat": 31.25, "lon": 34.79, "label": "Beer Sheva"},
}

# ---------------------------------------------------------------------------
# Chart URL generation
# ---------------------------------------------------------------------------
CHART_BASE = "https://charts.ecmwf.int/products"

CHART_PRODUCTS = {
    "mslp_rain":    {"slug": "medium-mslp-rain",    "desc": "MSLP + 6h precipitation"},
    "z500_t850":    {"slug": "medium-z500-t850",     "desc": "500hPa geopot. + 850hPa temp"},
    "temperature":  {"slug": "medium-2mt",           "desc": "2m temperature"},
    "wind":         {"slug": "medium-wind",          "desc": "10m wind speed + direction"},
    "total_precip": {"slug": "medium-tp",            "desc": "Total precipitation"},
    "clouds":       {"slug": "medium-clouds",        "desc": "Cloud cover"},
    "rain_rate":    {"slug": "medium-rain-rate",     "desc": "Precipitation rate"},
}

DEFAULT_PROJECTION = "opencharts_middle_east"


def latest_base_time():
    """Estimate the most recent available ECMWF run.

    Data is available ~7-9h after init. Runs at 00, 06, 12, 18 UTC.
    """
    now = datetime.now(timezone.utc)
    # Subtract 8h buffer for data availability
    available = now - timedelta(hours=8)
    run_hour = (available.hour // 6) * 6
    base = available.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    return base


def format_ecmwf_time(dt):
    """Format datetime as YYYYMMDDHHMM for ECMWF chart URLs."""
    return dt.strftime("%Y%m%d%H%M")


def build_chart_urls(products=None, projection=DEFAULT_PROJECTION,
                     base_time=None, valid_hours=None):
    """Build chart URLs for given products and validity times.

    Args:
        products: List of product keys from CHART_PRODUCTS. None = all.
        projection: Map projection ID.
        base_time: datetime of forecast run. None = latest.
        valid_hours: List of forecast hours to generate URLs for.
                     None = [0, 24, 48, 72].

    Returns:
        List of dicts with url, product, valid_time, step_h.
    """
    if products is None:
        products = list(CHART_PRODUCTS.keys())
    if base_time is None:
        base_time = latest_base_time()
    if valid_hours is None:
        valid_hours = [0, 24, 48, 72]

    results = []
    bt_str = format_ecmwf_time(base_time)

    for prod_key in products:
        prod = CHART_PRODUCTS.get(prod_key)
        if not prod:
            continue
        for step in valid_hours:
            vt = base_time + timedelta(hours=step)
            vt_str = format_ecmwf_time(vt)
            params = {
                "base_time": bt_str,
                "valid_time": vt_str,
                "projection": projection,
            }
            url = f"{CHART_BASE}/{prod['slug']}?{urlencode(params)}"
            results.append({
                "product": prod_key,
                "description": prod["desc"],
                "step_h": step,
                "base_time": bt_str,
                "valid_time": vt_str,
                "url": url,
            })
    return results


# ---------------------------------------------------------------------------
# Forecast data via Open-Meteo
# ---------------------------------------------------------------------------
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

DEFAULT_HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "rain",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "pressure_msl",
    "visibility",
]


def build_open_meteo_url(lat, lon, days=7, hourly_vars=None, model="ecmwf_ifs"):
    """Build Open-Meteo ECMWF API URL.

    Args:
        lat: Latitude.
        lon: Longitude.
        days: Forecast days (max 16).
        hourly_vars: List of variable names. None = DEFAULT_HOURLY_VARS.
        model: ecmwf_ifs | ecmwf_ifs025 | ecmwf_aifs025

    Returns:
        URL string.
    """
    if hourly_vars is None:
        hourly_vars = DEFAULT_HOURLY_VARS

    params = {
        "latitude": lat,
        "longitude": lon,
        "models": model,
        "hourly": ",".join(hourly_vars),
        "forecast_days": min(days, 16),
        "timezone": "Asia/Jerusalem",
    }
    return f"{OPEN_METEO_BASE}?{urlencode(params)}"


def fetch_forecast(lat, lon, days=7, hourly_vars=None, model="ecmwf_ifs"):
    """Fetch forecast data from Open-Meteo.

    Returns parsed JSON dict, or None on failure.
    """
    url = build_open_meteo_url(lat, lon, days, hourly_vars, model)

    import urllib.request
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"Error fetching forecast: {e}", file=sys.stderr)
        return None


def summarize_forecast(data, location_label=""):
    """Produce a concise text summary from Open-Meteo JSON response.

    Returns a markdown-formatted string.
    """
    if not data or "hourly" not in data:
        return f"No forecast data available for {location_label}."

    hourly = data["hourly"]
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    precip = hourly.get("precipitation", [])
    wind = hourly.get("wind_speed_10m", [])
    cloud = hourly.get("cloud_cover", [])

    if not times:
        return f"No time steps in forecast for {location_label}."

    lines = []
    if location_label:
        lines.append(f"### {location_label} — ECMWF IFS Forecast")
    lines.append("")

    # Group by day
    days = {}
    for i, t in enumerate(times):
        day = t[:10]  # YYYY-MM-DD
        if day not in days:
            days[day] = {"temps": [], "precip": 0.0, "wind_max": 0.0, "clouds": []}
        if i < len(temps) and temps[i] is not None:
            days[day]["temps"].append(temps[i])
        if i < len(precip) and precip[i] is not None:
            days[day]["precip"] += precip[i]
        if i < len(wind) and wind[i] is not None:
            days[day]["wind_max"] = max(days[day]["wind_max"], wind[i])
        if i < len(cloud) and cloud[i] is not None:
            days[day]["clouds"].append(cloud[i])

    lines.append("| Date | Temp Range (°C) | Precip (mm) | Max Wind (km/h) | Avg Cloud (%) |")
    lines.append("|------|-----------------|-------------|-----------------|---------------|")

    for day, vals in days.items():
        t_min = min(vals["temps"]) if vals["temps"] else "—"
        t_max = max(vals["temps"]) if vals["temps"] else "—"
        prcp = f'{vals["precip"]:.1f}'
        wnd = f'{vals["wind_max"]:.0f}'
        cld = f'{sum(vals["clouds"]) / len(vals["clouds"]):.0f}' if vals["clouds"] else "—"
        t_range = f"{t_min:.0f}–{t_max:.0f}" if isinstance(t_min, (int, float)) else "—"
        lines.append(f"| {day} | {t_range} | {prcp} | {wnd} | {cld} |")

    lines.append("")
    lines.append(f"*Model: ECMWF IFS via Open-Meteo | "
                 f"Coords: {data.get('latitude', '?')}N, {data.get('longitude', '?')}E*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(description="ECMWF chart URLs and forecast data for Israel")
    parser.add_argument("--mode", choices=["charts", "data", "both"], default="both",
                        help="Output mode: charts (URLs only), data (forecast only), both")
    parser.add_argument("--location", default="tzurit",
                        help=f"Location key: {', '.join(LOCATIONS.keys())}")
    parser.add_argument("--lat", type=float, default=None, help="Custom latitude")
    parser.add_argument("--lon", type=float, default=None, help="Custom longitude")
    parser.add_argument("--days", type=int, default=3, help="Forecast days (max 16)")
    parser.add_argument("--products", nargs="*", default=None,
                        help=f"Chart products: {', '.join(CHART_PRODUCTS.keys())}")
    parser.add_argument("--steps", nargs="*", type=int, default=None,
                        help="Forecast steps in hours (e.g., 0 24 48 72)")
    parser.add_argument("--json", action="store_true", help="Output raw JSON (data mode)")
    args = parser.parse_args()

    # Resolve location
    loc = LOCATIONS.get(args.location, LOCATIONS["tzurit"])
    lat = args.lat if args.lat is not None else loc["lat"]
    lon = args.lon if args.lon is not None else loc["lon"]
    label = loc["label"] if args.lat is None else f"Custom ({lat}, {lon})"

    output_parts = []

    # --- Charts ---
    if args.mode in ("charts", "both"):
        steps = args.steps or [0, 24, 48, 72]
        urls = build_chart_urls(
            products=args.products,
            valid_hours=steps,
        )
        output_parts.append("## ECMWF Chart URLs\n")
        for u in urls:
            output_parts.append(f"- **{u['description']}** (T+{u['step_h']}h, valid {u['valid_time']})")
            output_parts.append(f"  {u['url']}")
        output_parts.append("")

    # --- Data ---
    if args.mode in ("data", "both"):
        output_parts.append(f"## Forecast Data — {label}\n")
        api_url = build_open_meteo_url(lat, lon, args.days)
        output_parts.append(f"API URL: {api_url}\n")

        data = fetch_forecast(lat, lon, args.days)
        if data:
            if args.json:
                output_parts.append(json.dumps(data, indent=2))
            else:
                output_parts.append(summarize_forecast(data, label))
        else:
            output_parts.append("*Failed to fetch forecast data.*")

    print("\n".join(output_parts))


if __name__ == "__main__":
    main()
