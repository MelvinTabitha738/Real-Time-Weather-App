import os
import json
import re
import logging
import requests
import urllib.parse
import urllib.request
import urllib.error

from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from dotenv import load_dotenv

#Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)



#CITY EXTRACTION ( Regex Layer)

def extract_city(text: str):
    """
    Extract city name from user message.
    Works for patterns like:
    - "in Nairobi"
    - "at Dubai"
    - "weather Nairobi"
    """

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


#OPENWEATHER FETCH

def get_weather(city: str):
    """
    Fetch real weather data from OpenWeather API.
    """

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
            logger.warning(f"Weather fetch failed for '{city}': {response.text}")
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



#AI WEATHER CHAT (GROQ + WEATHER CONTEXT)

def weather_chat(request):
    if request.method != "POST":
        return JsonResponse({"reply": "Invalid request method."}, status=405)

    try:
        body = json.loads(request.body)
        message = body.get("message", "").strip()

        if not message:
            return JsonResponse({"reply": "Please enter a message."})

     
        #Extract city from user input
        
        city = extract_city(message)

        # fallback city (optional)
        if not city:
            city = "Nairobi"

        
        #Fetch live weather
        
        weather = get_weather(city)

        if weather:
            weather_context = json.dumps(weather, indent=2)
        else:
            weather_context = "NO LIVE WEATHER DATA AVAILABLE"

        
        #Groq API setup
        
        api_key = os.getenv("GROQ_API_KEY")

        if not api_key:
            logger.error("Missing GROQ_API_KEY")
            return JsonResponse({"reply": "Server configuration error."}, status=500)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

       
        #Prompt Injection (MOST IMPORTANT PART)
       
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a professional AI weather assistant.\n"
                        "You MUST base your answers ONLY on the provided weather data.\n"
                        "If data exists, use it to give clothing, travel, and activity advice.\n"
                        "If data is missing, clearly say you cannot access live weather."
                    )
                },
                {
                    "role": "user",
                    "content": f"""
User Question:
{message}

Extracted City:
{city}

Live Weather Data:
{weather_context}

Instructions:
- Use the weather data to answer naturally
- Give clothing or activity suggestions when relevant
- Be realistic and concise
"""
                }
            ],
            "temperature": 0.4,
            "max_tokens": 600
        }

       
        #Call Groq API
        
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=20
        )

        if response.status_code != 200:
            logger.error(f"Groq error {response.status_code}: {response.text}")
            return JsonResponse({"reply": "AI service error. Try again later."}, status=500)

        data = response.json()

        reply = data.get("choices", [{}])[0].get("message", {}).get("content")

        if not reply:
            logger.error(f"Malformed response: {data}")
            return JsonResponse({"reply": "Invalid AI response."}, status=500)

        return JsonResponse({"reply": reply})

    except Exception as e:
        logger.exception("weather_chat crashed")
        return JsonResponse({"reply": "Internal server error."}, status=500)



# WEATHER DASHBOARD (CORE LOGIC)

@require_http_methods(["GET", "POST"])
def index(request):
    if request.method == 'POST':
        city = request.POST.get('city', '').strip()

        if not city:
            return render(request, 'main/index.html', {
                'error': 'Please enter a city name.'
            })

        api_key = os.getenv("OPENWEATHER_API_KEY")

        if not api_key:
            return render(request, 'main/index.html', {
                'error': 'Server configuration error.'
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
            return render(request, 'main/index.html', {
                'error': 'Could not fetch weather. Check city name.'
            })

       
        # FORECAST API
        
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

        
        #  CONTEXT 
        
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

    return render(request, "main/index.html", {})
