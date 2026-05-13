---
name: weather-lookup
description: Efficient weather lookup using direct APIs. Avoids slow web scraping for weather data.
---

# Efficient Weather Lookup

Use this skill whenever the user asks for weather forecasts, current conditions, or precipitation windows for any location.

## Default Method: Open-Meteo API (fastest, structured data)

The Open-Meteo API returns structured JSON with no auth required:

```
curl "https://api.open-meteo.com/v1/forecast?latitude=32.85&longitude=35.25&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode&hourly=precipitation,precipitation_probability,weathercode&timezone=Asia%2FJerusalem&forecast_days=7"
```

- Free, no API key
- Returns daily + hourly data
- Supports 7-16 day forecasts
- WMO weather codes at <https://open-meteo.com/en/docs>
- Timezone-aware

**Location discovery:** For small towns, approximate coordinates work well. You can search for coordinates with:

```
curl "https://geocoding-api.open-meteo.com/v1/search?name=Stadt&count=1"
```

## Fallback Methods (in priority order)

### 1. wttr.in (quick terminal lookup)

```
curl wttr.in/CityName?format=3
curl wttr.in/CityName?lang=en  # 3-day forecast
```

- No auth, instant
- Good for "what's the weather like now" questions
- Limited detail

### 2. weather.com via browser (detailed but slow — use only when API fails)

```
browser_navigate to https://weather.com/weather/tenday/l/{location}
browser_snapshot to get 10-day table from accessibility tree
```

---

## Israel-Specific Weather

Get accurate weather forecasts for Israel, with emphasis on precipitation timing windows and localized data. Always prioritize weather2day.co.il as the primary source — it's the most accurate for Israeli weather.

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

**Quick lookup for Tzurit:**
```
https://api.open-meteo.com/v1/forecast?latitude=32.902&longitude=35.247&models=ecmwf_ifs&hourly=temperature_2m,precipitation,weather_code,wind_speed_10m&forecast_days=7&timezone=Asia/Jerusalem
```

### 3. weather.com (Supplementary)

10-day forecast with daily precipitation percentages. Use when weather2day text is vague or you need probability numbers.

**URL:** `https://weather.com/weather/tenday/l/f61a2c3de645525fc6405ce07dba08d6a0e07e5cbb648e98d666bf8e02788f56`

### 4. ECMWF Charts (Synoptic maps) — ON-DEMAND VISUAL

Professional-grade rainfall + MSLP + upper-air maps. Visual only, not extractable as structured data.

**Quick lookup — MSLP + Rain, Middle East:**
```
https://charts.ecmwf.int/products/medium-mslp-rain?projection=opencharts_middle_east
```

**When to use:** User asks for synoptic overview, wants to see the low-pressure system, or asks "show me the rain map."

---

## Forecast Delivery Pattern

When the user asks about weather, follow this order:

1. **Fetch primary source via API** (Open-Meteo for most locations; weather2day/forecast for Israel)
2. **Fetch supplementary data** (hourly precipitation from Open-Meteo for Israel; weather.com for probability numbers)
3. **Optionally deliver maps** — if user wants visuals or if significant weather is expected
4. **Synthesize** — combine all sources into a clear forecast focused on precipitation timing

## Precipitation Focus

Always highlight:
- **When** rain is expected (date + time windows)
- **How much** (mm from Open-Meteo)
- **Intensity** (light showers vs thunderstorms)
- **Synoptic context** (Cyprus low? Red Sea trough?) when relevant
- **Warnings** (flash floods, rough seas, dust) when available

## Output Language & Formatting

### General Case

- Present in user's language
- Daily conditions with precipitation percentages
- Specific time windows for precipitation (use hourly data from Open-Meteo)
- Clear identification of wettest/driest days
- Temperature ranges

### Israel-Specific Formatting

- **Language:** Always write the forecast report in Hebrew, regardless of what language the user asked in.
- **Tables:** Any tabular data (day-by-day forecasts, hourly precipitation, etc.) must be wrapped in a triple-backtick code block so it renders correctly in Telegram:

```
\\`\\`\\`
יום  | טמפ' | גשם
-----|------|-----
שני  | 18°  | 60%
\\`\\`\\`
```

Never output raw ASCII tables outside a code block — they render as garbled text in Telegram's proportional font.

## Response Format

When presenting weather data, always include:
- Daily conditions with precipitation percentages
- Specific time windows for precipitation (use hourly data from Open-Meteo)
- Clear identification of wettest/driest days
- Temperature ranges

## WMO Weather Code Mapping

Key codes to translate:
- 0: Clear sky
- 1-3: Partly to overcast
- 45, 48: Fog
- 51-57: Drizzle
- 61-67: Rain
- 71-77: Snow
- 80-82: Rain showers
- 95-99: Thunderstorm

---

## General Pitfalls

- **DO NOT use ims.gov.il** — the Israel Meteorological Service site is an Angular single-page app that renders as `{{template_variables}}` when scraped. web_extract returns zero useful data.
- **DO NOT start with web_search for weather** — search engines return monthly climate overviews, templated pages, or results for wrong locations (e.g., "Northern Cambria, Pennsylvania" instead of "Northern District, Israel"). Go direct to APIs.
- **DO NOT scrape weather.com via web_extract** — it returns sidebar/lifestyle content, not the forecast data. Use browser snapshot if needed.
- **Small towns may map to wrong locations** in generic searches — always use coordinates for precision, or specify region as the scope.

## Israel-Specific Pitfalls

- **Tzurit (צורית) location:** NEVER rely on Hebrew auto-correction or name-based lookup for Tzurit. Similar-sounding Hebrew place names exist and have caused wrong-data errors before. Always use exact coordinates **32.902, 35.247**.
- Weather2day is in Hebrew — always translate to English
- weather.com searches for "Northern District" often return Pennsylvania matches
- ECMWF chart images are visual-only — use vision to interpret if needed
- Open-Meteo `ecmwf_ifs` model updates ~7-9 hours after run initialization (00Z, 06Z, 12Z, 18Z)

## Coordinates Reference

For Israel locations, use exact coordinates:
- **Tzurit:** 32.902, 35.247

---

## Telegram Formatting

Any tabular data must be wrapped in a triple-backtick code block — Telegram uses a proportional font in regular messages, which garbles ASCII table alignment. Code blocks render in monospace and preserve the layout:

```
\\`\\`\\`
Day   | High | Low | Rain
------|------|-----|-----
Mon   |  18° | 12° |  60%
\\`\\`\\`
```

---

## Default Location (for Israel weather)

The default focus city is **Tzurit** (32.902, 35.247) — used by the daily weather job. To check a different city, specify it explicitly in your request (e.g. "weather in Tel Aviv" or "weather at 32.079, 34.781"). Always prefer coordinates over city names when querying APIs.

---

## Attribution

General weather lookup methodology inspired by https://dotenvx.com/docs/quickstart — efficient, direct API access patterns.

Israel-specific weather sourcing and pitfall knowledge from dedicated Israel weather workflows.
