"""Configuration management for groundwave-ai.

Loads configuration from:
1. .env file (secrets, deployment-specific)
2. config.yaml (features, prompts, behavior)
3. Environment variables (override both)
"""

import os
from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # python-dotenv not installed, skip


def env_str(key: str, default: str = "") -> str:
    """Get string from environment."""
    return os.getenv(key, default)


def env_int(key: str, default: int = 0) -> int:
    """Get int from environment."""
    val = os.getenv(key)
    return int(val) if val else default


def env_float(key: str, default: float = 0.0) -> float:
    """Get float from environment."""
    val = os.getenv(key)
    return float(val) if val else default


def env_bool(key: str, default: bool = False) -> bool:
    """Get bool from environment."""
    val = os.getenv(key, "").lower()
    if val in ("true", "1", "yes", "on"):
        return True
    if val in ("false", "0", "no", "off"):
        return False
    return default


class MeshConfig(BaseModel):
    protocol: str = env_str("MESH_PROTOCOL", "meshtastic")  # meshtastic or meshcore
    connection_type: str = env_str("MESH_CONNECTION_TYPE", "serial")
    serial_port: Optional[str] = env_str("MESH_SERIAL_PORT") or None
    tcp_host: str = env_str("MESH_TCP_HOST", "meshtastic.local")
    tcp_port: int = env_int("MESH_TCP_PORT", 4403)
    bot_name: str = env_str("BOT_LONG_NAME", "MeshBot AI")
    bot_short_name: str = env_str("BOT_SHORT_NAME", "ai01")
    bot_prefix: str = env_str("BOT_PREFIX", "[AI]")
    max_message_length: int = 175
    chunk_delay_seconds: int = 15


class LLMConfig(BaseModel):
    provider: str = env_str("LLM_PROVIDER", "lmstudio")
    lmstudio_url: str = env_str("LMSTUDIO_URL", "http://localhost:1234/v1/chat/completions")
    lmstudio_model: str = env_str("LMSTUDIO_MODEL", "local-model")
    ollama_url: str = env_str("OLLAMA_URL", "http://localhost:11434/api/generate")
    ollama_model: str = env_str("OLLAMA_MODEL", "llama3.2")
    system_prompt: str = "You are a helpful AI assistant on a community mesh network."
    local_knowledge: str = ""


class WebConfig(BaseModel):
    host: str = env_str("WEB_HOST", "0.0.0.0")
    port: int = env_int("WEB_PORT", 8000)
    community_name: str = env_str("COMMUNITY_NAME", "Community Mesh Network")
    community_description: str = env_str("COMMUNITY_DESCRIPTION", "A community-run LoRa mesh network")
    community_tagline: str = env_str("COMMUNITY_TAGLINE", "Offline-first community mesh")
    location_name: str = env_str("LOCATION_NAME", "your area")
    admin_enabled: bool = env_bool("ADMIN_ENABLED", True)
    admin_password: str = env_str("ADMIN_PASSWORD", "changeme")
    # Admin access control: "localhost" (default, most secure), "local" (192.168.x.x), or "all"
    admin_access: str = env_str("ADMIN_ACCESS", "localhost")
    # Branding assets (defaults to generic groundwave branding)
    favicon: str = env_str("FAVICON", "/static/groundwave-favicon.svg")
    logo_large: str = env_str("LOGO_LARGE", "/static/groundwave-logo.svg")
    logo_small: str = env_str("LOGO_SMALL", "/static/groundwave-icon.svg")


class BBSBoard(BaseModel):
    name: str
    description: str = ""


class BBSConfig(BaseModel):
    enabled: bool = True
    max_messages_per_user: int = 50
    message_expiry_days: int = 30
    boards: list[BBSBoard] = []


class WeatherConfig(BaseModel):
    enabled: bool = env_bool("WEATHER_ENABLED", True)
    default_lat: float = env_float("WEATHER_DEFAULT_LAT", 40.7128)
    default_lon: float = env_float("WEATHER_DEFAULT_LON", -74.0060)
    cache_minutes: int = 15
    temperature_unit: str = "fahrenheit"
    wind_speed_unit: str = "mph"


class SafetyConfig(BaseModel):
    ai_message_prefix: str = env_str("BOT_PREFIX", "[AI]")
    ignore_own_messages: bool = True
    rate_limit_per_minute: int = 10
    command_prefix: str = "!"


class MQTTConfig(BaseModel):
    """Regional MQTT configuration."""
    enabled: bool = env_bool("MQTT_ENABLED", False)
    broker: str = env_str("MQTT_BROKER", "")
    port: int = env_int("MQTT_PORT", 1883)
    username: str = env_str("MQTT_USERNAME", "")
    password: str = env_str("MQTT_PASSWORD", "")
    topic: str = env_str("MQTT_TOPIC", "msh/US/#")
    region_name: str = env_str("MQTT_REGION_NAME", "Regional")
    collect_nodes: bool = True
    collect_messages: bool = False
    collect_telemetry: bool = True
    show_on_status_page: bool = True
    separate_section: bool = True


class KiwixConfig(BaseModel):
    """Kiwix offline Wikipedia configuration."""
    enabled: bool = env_bool("KIWIX_ENABLED", False)
    url: str = env_str("KIWIX_URL", "http://localhost:8080")
    library: str = env_str("KIWIX_LIBRARY", "wikipedia_en_simple")
    ai_enhanced: bool = env_bool("KIWIX_AI_ENHANCED", True)
    wikipedia_fallback: bool = False


class AppConfig(BaseModel):
    mesh: MeshConfig = MeshConfig()
    llm: LLMConfig = LLMConfig()
    web: WebConfig = WebConfig()
    bbs: BBSConfig = BBSConfig()
    weather: WeatherConfig = WeatherConfig()
    safety: SafetyConfig = SafetyConfig()
    mqtt: MQTTConfig = MQTTConfig()
    kiwix: KiwixConfig = KiwixConfig()


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file, with env var overrides."""
    if config_path is None:
        # Look for config in common locations
        search_paths = [
            Path("config.yaml"),
            Path("config.yml"),
            Path(__file__).parent.parent / "config.yaml",
        ]
        for path in search_paths:
            if path.exists():
                config_path = str(path)
                break
    
    if config_path and Path(config_path).exists():
        with open(config_path, "r") as f:
            data = yaml.safe_load(f) or {}
            
        # Parse nested configs, merging with env defaults
        config_data = {}
        
        if "mesh" in data:
            mesh_data = {**MeshConfig().model_dump(), **data["mesh"]}
            config_data["mesh"] = MeshConfig(**mesh_data)
        
        if "llm" in data:
            llm_data = {**LLMConfig().model_dump(), **data["llm"]}
            config_data["llm"] = LLMConfig(**llm_data)
        
        if "web" in data:
            web_data = {**WebConfig().model_dump(), **data["web"]}
            config_data["web"] = WebConfig(**web_data)
        
        if "bbs" in data:
            boards = [BBSBoard(**b) for b in data["bbs"].get("boards", [])]
            bbs_data = {k: v for k, v in data["bbs"].items() if k != "boards"}
            config_data["bbs"] = BBSConfig(boards=boards, **bbs_data)
        
        if "weather" in data:
            weather_data = {**WeatherConfig().model_dump(), **data["weather"]}
            config_data["weather"] = WeatherConfig(**weather_data)
        
        if "safety" in data:
            safety_data = {**SafetyConfig().model_dump(), **data["safety"]}
            config_data["safety"] = SafetyConfig(**safety_data)
        
        if "mqtt" in data:
            mqtt_data = {**MQTTConfig().model_dump(), **data["mqtt"]}
            config_data["mqtt"] = MQTTConfig(**mqtt_data)
        
        if "kiwix" in data:
            kiwix_data = {**KiwixConfig().model_dump(), **data["kiwix"]}
            config_data["kiwix"] = KiwixConfig(**kiwix_data)
            
        return AppConfig(**config_data)
    
    # No config file, use env defaults
    return AppConfig()


# Global config instance
config = load_config()
