# ECMWF Charts & Data Guide for Israel Weather

## 1. Open Charts (Visual Maps)

### URL Structure

```
https://charts.ecmwf.int/products/{product}?base_time={YYYYMMDDHHMM}&projection=opencharts_middle_east&valid_time={YYYYMMDDHHMM}
```

- **base_time**: Forecast run initialization (e.g., `202604040000` for 00Z April 4, 2026)
- **valid_time**: When the forecast is valid for
- **projection**: Map view / bounding box area

### Projections Covering Israel

| Projection ID                    | Coverage               |
|----------------------------------|------------------------|
| `opencharts_middle_east`         | Best fit — Israel centered in view |
| `opencharts_eastern_mediterranean` | Wider E.Med context   |
| `opencharts_europe`              | Full European domain   |

### Chart Products Relevant for Israel

| Product slug                | What it shows                           | Best for                          |
|-----------------------------|-----------------------------------------|-----------------------------------|
| `medium-mslp-rain`         | Mean sea level pressure + 6h precip     | Synoptic overview, rain events    |
| `medium-z500-t850`         | 500hPa geopotential + 850hPa temp       | Upper-level troughs, cold air     |
| `medium-2mt`               | 2m temperature                          | Heat waves, cold spells           |
| `medium-wind`              | 10m wind speed + direction              | Sharav wind events                |
| `medium-tp`                | Total precipitation                     | Accumulated rainfall              |
| `medium-rain-rate`         | Precipitation rate                      | Intensity of rain events          |
| `medium-clouds`            | Cloud cover                             | General conditions                |
| `medium-mslp-wind850`      | MSLP + 850hPa wind                      | Low-level jet, moisture transport |
| `opencharts_meteogram`      | Point meteogram (all params)           | Location-specific multi-day view  |

AIFS (AI model) variants exist for most products — prefix with `aifs_`:
- `aifs_medium-mslp-rain`, `aifs_medium-z500-t850`, etc.

### Example URLs

Latest MSLP + Rain for Middle East:
```
https://charts.ecmwf.int/products/medium-mslp-rain?projection=opencharts_middle_east
```

Specific run + validity:
```
https://charts.ecmwf.int/products/medium-mslp-rain?base_time=202604040000&valid_time=202604050600&projection=opencharts_middle_east
```

### Time Selection

- Runs available: 00Z, 06Z, 12Z, 18Z daily
- Steps: 0h to 144h by 3h, then 150h to 360h by 6h (00Z/12Z runs only)
- 06Z/18Z runs: up to 144h only
- Data appears ~7-9 hours after run initialization

### Access Notes

- **Chart Browser** (charts.ecmwf.int): Public, no login required — but **image only**
- **ecCharts** (eccharts.ecmwf.int): Login required, restricted access — interactive, customizable
- **WMS** (eccharts.ecmwf.int/wms): Token-based, OGC standard — for map overlay integration

---

## 2. Programmatic Data Access (Non-Image)

### Option A: Open-Meteo API (Recommended for this skill)

Free JSON API wrapping ECMWF IFS data. No auth required. Point-based queries.

**Base URL:**
```
https://api.open-meteo.com/v1/forecast
```

**Key parameters:**
| Param          | Description                              | Example                    |
|----------------|------------------------------------------|----------------------------|
| `latitude`     | WGS84 latitude                           | `32.902` (Tzurit)          |
| `longitude`    | WGS84 longitude                          | `35.247` (Tzurit)          |
| `models`       | Force ECMWF model specifically           | `ecmwf_ifs`                |
| `hourly`       | Comma-separated variable list            | `temperature_2m,precipitation,wind_speed_10m` |
| `forecast_days`| Number of forecast days (max 16)         | `7`                        |
| `timezone`     | Timezone for output                      | `Asia/Jerusalem`           |

**Available hourly variables (subset most useful for Israel):**
- `temperature_2m` — 2m temperature (°C)
- `apparent_temperature` — Feels-like temperature
- `relative_humidity_2m` — Relative humidity (%)
- `dewpoint_2m` — Dewpoint temperature
- `precipitation` — Total precipitation (mm)
- `rain` — Rain only (mm)
- `weather_code` — WMO weather condition code
- `cloud_cover` — Total cloud cover (%)
- `wind_speed_10m` — 10m wind speed (km/h)
- `wind_direction_10m` — Wind direction (°)
- `wind_gusts_10m` — Wind gusts (km/h)
- `pressure_msl` — Mean sea level pressure (hPa)
- `visibility` — Visibility (m)
- `cape` — Convective Available Potential Energy (J/kg)
- `sunshine_duration` — Sunshine duration (s)

**Pressure level variables** (append level, e.g., `temperature_850hPa`):
- `temperature`, `wind_speed`, `wind_direction`, `geopotential_height`
- Levels: 1000, 925, 850, 700, 500, 300, 200 hPa

**Model options:**
| Model ID         | Resolution | Notes                                 |
|------------------|-----------|---------------------------------------|
| `ecmwf_ifs`      | 9 km      | Full native IFS HRES — best resolution|
| `ecmwf_ifs025`   | ~25 km    | Open-data 0.25° subset               |
| `ecmwf_aifs025`  | ~28 km    | AI model, 6-hourly only              |

**Example request:**
```
https://api.open-meteo.com/v1/forecast?latitude=32.902&longitude=35.247&models=ecmwf_ifs&hourly=temperature_2m,precipitation,wind_speed_10m,cloud_cover,weather_code&forecast_days=7&timezone=Asia/Jerusalem
```

**Response format:** JSON with `hourly.time[]` and `hourly.{variable}[]` arrays.

### Option B: ecmwf-opendata (GRIB files)

Direct download of ECMWF open data in GRIB2 format. No auth required. Global grid — you filter locally.

```bash
pip install ecmwf-opendata
```

```python
from ecmwf.opendata import Client
client = Client(source="ecmwf")  # also: "aws", "azure", "google"
client.retrieve(
    type="fc",
    param=["2t", "tp", "10u", "10v", "msl"],
    step=[0, 6, 12, 24, 48, 72],
    target="forecast.grib2",
)
```

**Useful param codes:**
| Code  | Variable                      |
|-------|-------------------------------|
| `2t`  | 2m temperature                |
| `tp`  | Total precipitation           |
| `10u` | 10m U-wind component          |
| `10v` | 10m V-wind component          |
| `msl` | Mean sea level pressure       |
| `tcc` | Total cloud cover             |
| `sp`  | Surface pressure              |

**Requires** `eccodes` or `cfgrib` + `xarray` to decode GRIB2 → usable arrays.

**Trade-offs vs Open-Meteo:**
- Pro: Raw model output, full global grid, official ECMWF source
- Con: Needs GRIB tooling, no point extraction built in, heavier dependencies

### Option C: CDS API (Reanalysis / S2S / Seasonal)

For historical data (ERA5) or sub-seasonal/seasonal forecasts. Requires free ECMWF account + Personal Access Token.

```bash
pip install cdsapi
```

Not needed for real-time forecasts — use Option A or B instead.

---

## 3. Israel-Specific Coordinates

| Location        | Lat     | Lon     | Notes                    |
|-----------------|---------|---------|--------------------------|
| Tzurit          | 32.9020 | 35.2470 | Home base                |
| Tel Aviv        | 32.0853 | 34.7818 | Coastal reference        |
| Jerusalem       | 31.7683 | 35.2137 | Elevated, mountain climate|
| Haifa           | 32.7940 | 34.9896 | Northern coast           |
| Eilat           | 29.5577 | 34.9519 | Desert / Red Sea         |
| Beer Sheva      | 31.2520 | 34.7915 | Negev reference          |

## 4. Seasonal Context for Israel

| Season       | Months    | Key weather patterns                          |
|--------------|-----------|-----------------------------------------------|
| Winter       | Dec-Feb   | Mediterranean cyclones, rain, occasional snow in Jerusalem/Golan |
| Transition   | Mar-Apr   | Last rains, Sharav (hot dry easterly) events  |
| Dry season   | May-Oct   | Stable, hot, nearly zero rain                 |
| Autumn onset | Nov       | First rains, thunderstorms                    |

**Watch parameters for Israel rain events:**
- MSLP: Cyprus low / Red Sea trough patterns
- 500hPa: Upper trough approaching from west
- 850hPa wind: Southwesterly = moisture transport
- CAPE: Convective storms, especially autumn
