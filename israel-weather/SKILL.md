---
name: israel-weather
description: Get weather forecasts for Israel with emphasis on precipitation timing. Uses weather2day.co.il for local textual forecasts, weather.com for day-by-day precipitation percentages, Open-Meteo API for ECMWF-point data, and ECMWF charts for synoptic maps.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [weather, israel, forecast, precipitation, ecmwf, weather2day]
    related_skills: []
---

# Israel Weather Forecast

Get accurate weather forecasts for Israel, with emphasis on precipitation timing windows and localized data. Always prioritize weather2day.co.il as the primary source — it's the most accurate for Israeli weather.

## Output Language & Formatting

**Language:** Always write the forecast report in Hebrew, regardless of what language the user asked in.

**Tables:** Any tabular data (day-by-day forecasts, hourly precipitation, etc.) must be wrapped in a triple-backtick code block so it renders correctly in Telegram:
```
\`\`\`
יום  | טמפ' | גשם
-----|------|-----
שני  | 18°  | 60%
\`\`\`
```
Never output raw ASCII tables outside a code block — they render as garbled text in Telegram's proportional font.

## Primary Data Sources

### 1. weather2day.co.il/forecast (Textual forecasts) — PRIMARY

This is the most accurate textual forecast source for Israel. Access via browser snapshot.

**URL:** `https://www.weather2day.co.il/forecast`

**What you get from browser_snapshot:**
- IMS textual forecasts in Hebrew for today/tonight and next 3 days
- Live temperature readings for ~12 cities across Israel
- Active weather warnings (dust, rough seas, rain, heat)
- Forum activity where weather enthusiasts discuss incoming systems

**Workflow:**
```
browser_navigate(url="https://www.weather2day.co.il/forecast")
browser_snapshot()
# Translate Hebrew text to English for the user
# Extract temperatures, warnings, and forecast text
```

**Key pages on weather2day:**
| Page | Purpose | Method |
|------|---------|--------|
| `/forecast` | Textual weekly forecast + warnings | browser_snapshot (structured text) |
| `/models` | Precipitation/rain maps (GFS/ECMWF/COSMO/ICON) | browser_vision (visual only) |
| `/warnings` | Active weather alerts | browser_snapshot |
| `/satellite` | Satellite imagery | browser_vision (visual only) |
| `/מצלמות-אונליין` | Live weather cameras | browser_vision (visual only) |

### 2. Open-Meteo API (ECMWF point data) — PRECIPITATION NUMBERS

Free JSON API for ECMWF IFS data. No auth required. Use this to extract exact hourly precipitation amounts for specific locations.

**See:** `references/ecmwf-charts-guide.md` — Section 2, Option A for full API documentation.

**Quick lookup for Tzurit:**
```
https://api.open-meteo.com/v1/forecast?latitude=32.902&longitude=35.247&models=ecmwf_ifs&hourly=temperature_2m,precipitation,weather_code,wind_speed_10m&forecast_days=7&timezone=Asia/Jerusalem
```

**Workflow:**
```python
from hermes_tools import execute_code
# Use execute_code or web_extract to fetch and parse the JSON
```

### 3. weather.com (Supplementary)

10-day forecast with daily precipitation percentages. Use when weather2day text is vague or you need probability numbers.

**URL:** `https://weather.com/weather/tenday/l/f61a2c3de645525fc6405ce07dba08d6a0e07e5cbb648e98d666bf8e02788f56`

**Workflow:**
```
browser_navigate(url=...)
browser_snapshot() → gives day-by-day conditions and precip %
```

### 4. ECMWF Charts (Synoptic maps) — ON-DEMAND VISUAL

Professional-grade rainfall + MSLP + upper-air maps. Visual only, not extractable as structured data.

**See:** `references/ecmwf-charts-guide.md` for full chart products, URL patterns, and parameter selection.

**Quick lookup — MSLP + Rain, Middle East:**
```
https://charts.ecmwf.int/products/medium-mslp-rain?projection=opencharts_middle_east
```

**When to use:** User asks for synoptic overview, wants to see the low-pressure system, or asks "show me the rain map."

## Forecast Delivery Pattern

When the user asks about weather, follow this order:

1. **Fetch weather2day/forecast via browser_snapshot** — get textual forecast, warnings, live readings
2. **Fetch Open-Meteo API for hourly precipitation data** — get exact mm values and timing windows
3. **Optionally deliver maps** — if user wants visuals or if significant weather is expected
4. **Synthesize** — combine all sources into a clear forecast focused on precipitation timing

## Precipitation Focus

Always highlight:
- **When** rain is expected (date + time windows)
- **How much** (mm from Open-Meteo)
- **Intensity** (light showers vs thunderstorms)
- **Synoptic context** (Cyprus low? Red Sea trough? Sharav?) when relevant
- **Warnings** (flash floods, rough seas, dust) from weather2day

## Never Try To Extract

- **ims.gov.il** — Heavy Angular/JS, extracts as `{{template_variables}}`
- **Generic web search for weather** — Too noisy, returns wrong locations

## Default Location

The default focus city is **Tzurit** (32.902, 35.247) — used by the daily weather job. To check a different city, specify it explicitly in your request (e.g. "weather in Tel Aviv" or "weather at 32.079, 34.781"). Always prefer coordinates over city names when querying APIs.

## Pitfalls

- **Tzurit (צורית) location:** NEVER rely on Hebrew auto-correction or name-based lookup for Tzurit. Similar-sounding Hebrew place names exist and have caused wrong-data errors before. Always use exact coordinates **32.902, 35.247**.
- Weather2day is in Hebrew — always translate to English
- weather.com searches for "Northern District" often return Pennsylvania matches
- ECMWF chart images are visual-only — use vision to interpret if needed
- Open-Meteo `ecmwf_ifs` model updates ~7-9 hours after run initialization (00Z, 06Z, 12Z, 18Z)

## Coordinates Reference

See `references/ecmwf-charts-guide.md` Section 3 for Israel location coordinates (Tzurit: 32.902, 35.247).

## Seasonal Context

See `references/ecmwf-charts-guide.md` Section 4 for Israel seasonal patterns and watch parameters.
