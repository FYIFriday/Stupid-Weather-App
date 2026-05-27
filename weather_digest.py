"""
Daily weather digest for three cities, delivered via Pushover.

Pulls forecast data from Open-Meteo (free, no API key required) and sends a
formatted push notification to the user's phone.

Environment variables required:
    PUSHOVER_USER_KEY  - User key from pushover.net dashboard
    PUSHOVER_API_TOKEN - Application API token from pushover.net

Run locally:
    PUSHOVER_USER_KEY=... PUSHOVER_API_TOKEN=... python weather_digest.py

Run in GitHub Actions: see .github/workflows/weather_digest.yml
"""

import os
import sys
from datetime import datetime
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import json


# -------- Configuration --------

CITIES = [
    {
        "name": "Grand Rapids",
        "label": "GRR",
        "latitude": 42.9634,
        "longitude": -85.6681,
        "timezone": "America/Detroit",
    },
    {
        "name": "Jackson",
        "label": "JXN",
        "latitude": 32.2988,   # Jackson, Mississippi
        "longitude": -90.1848,
        "timezone": "America/Chicago",
    },
    {
        "name": "Amsterdam",
        "label": "AMS",
        "latitude": 52.3676,
        "longitude": 4.9041,
        "timezone": "Europe/Amsterdam",
    },
    {
        "name": "Manchester",
        "label": "MAN",
        "latitude": 53.4808,
        "longitude": -2.2426,
        "timezone": "Europe/London",
    },
]

# WMO weather code -> human-readable condition
# Source: https://open-meteo.com/en/docs (WMO Weather interpretation codes)
WEATHER_CODES = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Light freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Violent showers",
    85: "Light snow showers",
    86: "Snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm w/ hail",
    99: "Severe thunderstorm w/ hail",
}


# -------- Open-Meteo fetch --------

def fetch_forecast(city):
    """Return today's forecast dict for one city, or None on failure."""
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "daily": ",".join([
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "apparent_temperature_max",
            "precipitation_probability_max",
            "precipitation_sum",
            "wind_speed_10m_max",
            "relative_humidity_2m_mean",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": city["timezone"],
        "forecast_days": 1,
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"

    try:
        req = Request(url, headers={"User-Agent": "weather-digest/1.0"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        print(f"[error] Failed to fetch {city['name']}: {e}", file=sys.stderr)
        return None

    daily = data.get("daily", {})
    # Each field is a list; we want the first (and only) entry, today
    def first(key, default=None):
        vals = daily.get(key, [])
        return vals[0] if vals else default

    return {
        "condition": WEATHER_CODES.get(first("weather_code"), "Unknown"),
        "high": first("temperature_2m_max"),
        "low": first("temperature_2m_min"),
        "feels_like_max": first("apparent_temperature_max"),
        "precip_chance": first("precipitation_probability_max"),
        "precip_amount": first("precipitation_sum"),
        "wind_max": first("wind_speed_10m_max"),
        "humidity": first("relative_humidity_2m_mean"),
    }


# -------- Formatting --------

def format_city(city, forecast):
    """Format one city's forecast as a multi-line block."""
    if forecast is None:
        return f"{city['label']} — data unavailable"

    def f(val, suffix="", fmt="{:.0f}"):
        """Format a value, returning '—' if missing."""
        if val is None:
            return "—"
        try:
            return fmt.format(val) + suffix
        except (ValueError, TypeError):
            return str(val) + suffix

    lines = [
        f"*{city['label']} — {city['name']}*",
        f"{forecast['condition']}, {f(forecast['high'], '°')}/{f(forecast['low'], '°')} (feels {f(forecast['feels_like_max'], '°')})",
        f"Humidity {f(forecast['humidity'], '%')} · Wind {f(forecast['wind_max'], ' mph', '{:.1f}')}",
        f"Rain {f(forecast['precip_chance'], '%')} chance · {f(forecast['precip_amount'], '\"', '{:.2f}')} expected",
    ]
    return "\n".join(lines)


def build_message(cities):
    """Build the full multi-city digest."""
    today = datetime.now().strftime("%A, %B %-d")
    blocks = [f"Weather for {today}", ""]
    for city in cities:
        forecast = fetch_forecast(city)
        blocks.append(format_city(city, forecast))
        blocks.append("")  # blank line between cities
    return "\n".join(blocks).strip()


# -------- Pushover delivery --------

def send_pushover(message, user_key, api_token):
    """Send the message via Pushover. Returns True on success."""
    payload = urlencode({
        "token": api_token,
        "user": user_key,
        "message": message,
        "title": "Daily Weather",
        "html": 0,  # plain text; * in our format is just decorative
    }).encode("utf-8")

    req = Request(
        "https://api.pushover.net/1/messages.json",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("status") == 1:
                return True
            print(f"[error] Pushover rejected: {result}", file=sys.stderr)
            return False
    except (URLError, HTTPError) as e:
        print(f"[error] Pushover request failed: {e}", file=sys.stderr)
        return False


# -------- Main --------

def main():
    user_key = os.environ.get("PUSHOVER_USER_KEY")
    api_token = os.environ.get("PUSHOVER_API_TOKEN")

    if not user_key or not api_token:
        print("[error] PUSHOVER_USER_KEY and PUSHOVER_API_TOKEN required", file=sys.stderr)
        sys.exit(1)

    message = build_message(CITIES)
    print(message)  # always log to stdout for debugging in Actions

    if send_pushover(message, user_key, api_token):
        print("[ok] Sent.")
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
