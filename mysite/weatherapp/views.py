import os
import json
import re
import logging
import requests
import urllib.parse
import urllib.request

from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)



# CITY EXTRACTION

def extract_city(text: str):
    patterns = [
        r"in\s+([a-zA-Z\s]+)",
        r"at\s+([a-zA-Z\s]+)",
        r"for\s+([a-zA-Z\s]+)",
        r"weather\s+([a-zA-Z\s]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text.lower())
        if match:
            return match.group(1).strip().title()

    return None


# -----------------------------
# WEATHER FETCH (reusable)
# -----------------------------
def get_weather(city: str):
    api_key = os.getenv("OPENWEATHER_API_KEY")
    if not api_key:
        logger.error("Missing OPENWEATHER_API_KEY")
        return None

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={urllib.parse.quote(city)}&units=metric&appid={api_key}"
    )

    try:
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            logger.warning(f"Weather fetch failed for {city}: {response.text}")
            return None

        data = response.json()

        return {
            "city": data["name"],
            "country": data["sys"]["country"],
            "temperature": data["main"]["temp"],
            "feels_like": data["main"]["feels_like"],
            "condition": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "wind_speed": data["wind"]["speed"],
        }

    except Exception as e:
        logger.exception(f"Weather API error: {e}")
        return None



# MAIN DASHBOARD 

@require_http_methods(["GET", "POST"])
def index(request):


    if request.method == "POST":
        city = request.POST.get("city", "").strip()

        if not city:
            return render(request, "main/index.html", {
                "error": "Please enter a city name."
            })

        # redirect to GET with query param (IMPORTANT FIX)
        return redirect(f"/?city={city}")


    # GET → clean render

    city = request.GET.get("city", "").strip()

    if not city:
        return render(request, "main/index.html", {})

    api_key = os.getenv("OPENWEATHER_API_KEY")

    if not api_key:
        return render(request, "main/index.html", {
            "error": "Server configuration error."
        })


    # CURRENT WEATHER
    
    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?q={urllib.parse.quote(city)}&units=metric&appid={api_key}"
    )

    try:
        response = urllib.request.urlopen(url, timeout=10)
        current = json.loads(response.read())

    except Exception as e:
        logger.exception(e)
        return render(request, "main/index.html", {
            "error": "Could not fetch weather. Check city name."
        })


    # FORECAST WEATHER

    lat = current["coord"]["lat"]
    lon = current["coord"]["lon"]

    forecast_url = (
        "https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lon}&units=metric&appid={api_key}"
    )

    forecast_list = []

    try:
        forecast_resp = urllib.request.urlopen(forecast_url, timeout=10)
        forecast_data = json.loads(forecast_resp.read())

        seen = set()

        for item in forecast_data.get("list", []):
            date = item["dt_txt"].split(" ")[0]
            time = item["dt_txt"].split(" ")[1]

            if date not in seen and time == "12:00:00":
                seen.add(date)

                forecast_list.append({
                    "date": date,
                    "temp_max": round(item["main"]["temp_max"]),
                    "temp_min": round(item["main"]["temp_min"]),
                    "description": item["weather"][0]["description"].capitalize(),
                    "icon": item["weather"][0]["icon"],
                    "humidity": item["main"]["humidity"],
                    "wind_speed": round(item["wind"]["speed"], 1),
                })

                if len(forecast_list) == 5:
                    break

    except Exception as e:
        logger.warning(f"Forecast error: {e}")


    # CONTEXT DATA

    data = {
        "city": current["name"],
        "country_code": current["sys"]["country"],
        "coordinate": f"{current['coord']['lat']}, {current['coord']['lon']}",

        "temp": f"{round(current['main']['temp'])} °C",
        "feels_like": f"{round(current['main']['feels_like'])} °C",
        "temp_min": f"{round(current['main']['temp_min'])} °C",
        "temp_max": f"{round(current['main']['temp_max'])} °C",

        "pressure": current["main"]["pressure"],
        "humidity": current["main"]["humidity"],
        "visibility": round(current.get("visibility", 0) / 1000, 1),

        "wind_speed": round(current["wind"]["speed"], 1),
        "wind_dir": current["wind"].get("deg", 0),

        "main": current["weather"][0]["main"],
        "description": current["weather"][0]["description"].capitalize(),
        "icon": current["weather"][0]["icon"],

        "forecast": forecast_list,
    }

    return render(request, "main/index.html", data)



# AI CHAT

@require_http_methods(["POST"])
def weather_chat(request):

    try:
        body = json.loads(request.body)
        message = body.get("message", "").strip()

        if not message:
            return JsonResponse({"reply": "Please enter a message."})

        city = extract_city(message) or "Nairobi"

        weather = get_weather(city)
        weather_context = json.dumps(weather, indent=2) if weather else "NO LIVE WEATHER DATA AVAILABLE"

        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            return JsonResponse({"reply": "Server configuration error."}, status=500)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a professional AI weather assistant. "
                        "Use ONLY provided weather data."
                    )
                },
                {
                    "role": "user",
                    "content": f"""
User Question:
{message}

City:
{city}

Weather:
{weather_context}
"""
                }
            ],
            "temperature": 0.4,
            "max_tokens": 600
        }

        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20
        )

        if response.status_code != 200:
            return JsonResponse({"reply": "AI service error."}, status=500)

        data = response.json()
        reply = data.get("choices", [{}])[0].get("message", {}).get("content")

        return JsonResponse({"reply": reply or "No response."})

    except Exception as e:
        logger.exception("weather_chat crashed")
        return JsonResponse({"reply": "Internal server error."}, status=500)
