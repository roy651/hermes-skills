---
name: weather-lookup
category: leisure
tags: [weather, forecast, location, lookup]
description: Efficient weather lookup using direct APIs. Avoids slow web scraping for weather data.
---

# Efficient Weather Lookup

Use this skill whenever the user asks for weather forecasts, current conditions, or precipitation windows for any location.

## Primary Method: Open-Meteo API (fastest, structured data)

The Open-Meteo API returns structured JSON with no auth required:

```
curl "https://api.open-meteo.com/v1/forecast?latitude=32.85&longitude=35.25&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,weathercode&hourly=precipitation,precipitation_probability,weathercode&timezone=Asia%2FJerusalem&forecast_days=7"
```

- Free, no API key
- Returns daily + hourly data
- Supports 7-16 day forecasts
- WMO weather codes at <https://open-meteo.com/en/docs>
- Timezone-aware

**Location discovery:** For small towns (like Tzurit, Israel), approximate coordinates work well. You can search for coordinates with:
```
curl "https://geocoding-api.open-meteo.com/v1/search?name=Tzurit&count=1"
```

## Fallback Methods (in priority order)

### 1. wttr.in (quick terminal lookup)
```
curl wttr.in/Tzurit?format=3
curl wttr.in/Tzurit?lang=en  # 3-day forecast
```
- No auth, instant
- Good for "what's the weather like now" questions
- Limited detail

### 2. weather.com via browser (detailed but slow — use only when API fails)
```
browser_navigate to https://weather.com/weather/tenday/l/{location}
browser_snapshot to get 10-day table from accessibility tree
```

## Pitfalls

- **DO NOT use ims.gov.il** — the Israel Meteorological Service site is an Angular single-page app that renders as `{{template_variables}}` when scraped. web_extract returns zero useful data.
- **DO NOT start with web_search for weather** — search engines return monthly climate overviews, templated pages, or results for wrong locations (e.g., "Northern Cambria, Pennsylvania" instead of "Northern District, Israel"). Go direct to APIs.
- **DO NOT scrape weather.com via web_extract** — it returns sidebar/lifestyle content, not the forecast data. Use browser snapshot if needed.
- **Small towns may map to wrong locations** in generic searches — always use coordinates for precision, or specify "Northern District Israel" as the scope.

## Telegram Formatting

Any tabular data must be wrapped in a triple-backtick code block — Telegram uses a proportional font in regular messages, which garbles ASCII table alignment. Code blocks render in monospace and preserve the layout:
```
\`\`\`
Day   | High | Low | Rain
------|------|-----|-----
Mon   |  18° | 12° |  60%
\`\`\`
```

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