"""Weather service using Open-Meteo API.

Provides weather information without requiring an API key.
"""

from datetime import datetime, timedelta
from typing import Optional, Tuple
import logging
import httpx

from ..config import config

logger = logging.getLogger(__name__)

# Cache TTL
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes


class WeatherService:
    """Weather service using Open-Meteo API (free, no API key required)."""
    
    WEATHER_CODES = {
        0: "Clear ☀️",
        1: "Mostly Clear 🌤️",
        2: "Partly Cloudy ⛅",
        3: "Overcast ☁️",
        45: "Foggy 🌫️",
        48: "Rime Fog 🌫️",
        51: "Light Drizzle 🌧️",
        53: "Drizzle 🌧️",
        55: "Heavy Drizzle 🌧️",
        61: "Light Rain 🌧️",
        63: "Rain 🌧️",
        65: "Heavy Rain 🌧️",
        66: "Freezing Rain 🌨️",
        67: "Heavy Freezing Rain 🌨️",
        71: "Light Snow 🌨️",
        73: "Snow 🌨️",
        75: "Heavy Snow 🌨️",
        77: "Snow Grains 🌨️",
        80: "Light Showers 🌦️",
        81: "Showers 🌦️",
        82: "Heavy Showers 🌦️",
        85: "Light Snow Showers 🌨️",
        86: "Snow Showers 🌨️",
        95: "Thunderstorm ⛈️",
        96: "Thunderstorm w/ Hail ⛈️",
        99: "Severe Thunderstorm ⛈️",
    }
    
    def __init__(self):
        self.default_lat = config.weather.default_lat
        self.default_lon = config.weather.default_lon
        self._client = httpx.AsyncClient(timeout=30.0)
        
        # Cache for weather data
        self._cache = {
            "current": None,
            "current_data": None,
            "forecast": None,
            "forecast_data": None,
            "expires": None
        }
    
    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache["expires"]:
            return False
        return datetime.now() < self._cache["expires"]
    
    def _update_cache_expiry(self):
        """Update cache expiration time."""
        self._cache["expires"] = datetime.now() + timedelta(seconds=CACHE_TTL_SECONDS)
        logger.info("Weather cache updated, expires in 15 minutes")
    
    async def get_current_weather(
        self, 
        lat: Optional[float] = None, 
        lon: Optional[float] = None
    ) -> str:
        """Get current weather conditions.
        
        Args:
            lat: Latitude (uses default if not provided)
            lon: Longitude (uses default if not provided)
            
        Returns:
            Formatted weather string
        """
        lat = lat or self.default_lat
        lon = lon or self.default_lon
        
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            }
            
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            current = data.get("current", {})
            temp = current.get("temperature_2m", "?")
            humidity = current.get("relative_humidity_2m", "?")
            wind = current.get("wind_speed_10m", "?")
            code = current.get("weather_code", 0)
            
            condition = self.WEATHER_CODES.get(code, "Unknown")
            
            return f"{condition}\n🌡️ {temp}°F | 💧 {humidity}% | 💨 {wind}mph"
            
        except Exception as e:
            logger.error(f"Weather API error: {e}")
            return f"⚠️ Weather unavailable: {str(e)[:30]}"
    
    async def get_forecast(
        self, 
        lat: Optional[float] = None, 
        lon: Optional[float] = None,
        days: int = 3
    ) -> str:
        """Get weather forecast.
        
        Args:
            lat: Latitude
            lon: Longitude
            days: Number of days (1-7)
            
        Returns:
            Formatted forecast string
        """
        from datetime import datetime
        
        lat = lat or self.default_lat
        lon = lon or self.default_lon
        days = min(max(days, 1), 7)
        
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "forecast_days": days,
            }
            
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            codes = daily.get("weather_code", [])
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            precip = daily.get("precipitation_probability_max", [])
            
            lines = []
            for i in range(min(len(dates), days)):
                # Get day name
                date_obj = datetime.strptime(dates[i], "%Y-%m-%d")
                day_name = day_names[date_obj.weekday()]
                
                code = codes[i] if i < len(codes) else 0
                high = int(highs[i]) if i < len(highs) else "?"
                low = int(lows[i]) if i < len(lows) else "?"
                rain_chance = precip[i] if i < len(precip) else 0
                
                condition = self.WEATHER_CODES.get(code, "Unknown")
                
                line = f"{day_name}: {condition}, {high}°/{low}°"
                if rain_chance and rain_chance > 20:
                    line += f" ({rain_chance}% precip)"
                lines.append(line)
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Forecast API error: {e}")
            return f"⚠️ Forecast unavailable"
    
    async def get_current_weather_data(
        self, 
        lat: Optional[float] = None, 
        lon: Optional[float] = None
    ) -> dict:
        """Get current weather as structured data for API (cached 15 min)."""
        # Return cached data if valid
        if self._is_cache_valid() and self._cache["current_data"]:
            return self._cache["current_data"]
        
        lat = lat or self.default_lat
        lon = lon or self.default_lon
        
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
            }
            
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            current = data.get("current", {})
            code = current.get("weather_code", 0)
            condition_full = self.WEATHER_CODES.get(code, "Unknown")
            # Extract just the emoji
            icon = condition_full.split()[-1] if condition_full else "❓"
            condition_text = condition_full.rsplit(" ", 1)[0] if " " in condition_full else condition_full
            
            result = {
                "temp": round(current.get("temperature_2m", 0)),
                "humidity": current.get("relative_humidity_2m", 0),
                "wind": round(current.get("wind_speed_10m", 0)),
                "condition": condition_text,
                "icon": icon,
                "code": code
            }
            
            # Cache the result
            self._cache["current_data"] = result
            self._update_cache_expiry()
            
            return result
            
        except Exception as e:
            logger.error(f"Weather API error: {e}")
            return {"temp": 0, "condition": "Unavailable", "icon": "❓", "humidity": 0, "wind": 0}
    
    async def get_forecast_data(
        self, 
        lat: Optional[float] = None, 
        lon: Optional[float] = None,
        days: int = 3
    ) -> list:
        """Get forecast as structured data for API (cached 15 min).
        
        Returns the NEXT 'days' days, excluding today.
        """
        # Return cached data if valid
        if self._is_cache_valid() and self._cache["forecast_data"]:
            return self._cache["forecast_data"]
        
        lat = lat or self.default_lat
        lon = lon or self.default_lon
        days = min(max(days, 1), 7)
        
        day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        
        try:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "forecast_days": days + 1,  # Request extra day so we can skip today
            }
            
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            daily = data.get("daily", {})
            dates = daily.get("time", [])
            codes = daily.get("weather_code", [])
            highs = daily.get("temperature_2m_max", [])
            lows = daily.get("temperature_2m_min", [])
            
            forecast = []
            # Skip index 0 (today), start from index 1
            for i in range(1, min(len(dates), days + 1)):
                date_obj = datetime.strptime(dates[i], "%Y-%m-%d")
                day_name = day_names[date_obj.weekday()]
                
                code = codes[i] if i < len(codes) else 0
                condition_full = self.WEATHER_CODES.get(code, "Unknown")
                icon = condition_full.split()[-1] if condition_full else "❓"
                
                forecast.append({
                    "day": day_name,
                    "high": int(highs[i]) if i < len(highs) else 0,
                    "low": int(lows[i]) if i < len(lows) else 0,
                    "icon": icon,
                    "code": code
                })
            
            # Cache the result
            self._cache["forecast_data"] = forecast
            self._update_cache_expiry()
            
            return forecast
            
        except Exception as e:
            logger.error(f"Forecast API error: {e}")
            return []
    
    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# Global weather service instance
weather_service = WeatherService()
