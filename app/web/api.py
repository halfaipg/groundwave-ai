"""REST API and WebSocket endpoints for the Community Mesh Platform."""

import asyncio
from datetime import datetime
from typing import Optional, List
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query, Request
from pydantic import BaseModel

from ..config import config
from ..database import db, Message
from ..services.bbs import bbs_service
from ..services.weather import weather_service

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")


# Pydantic models for API
class SendMessageRequest(BaseModel):
    text: str
    destination: Optional[str] = None
    channel: int = 0


class BBSPostRequest(BaseModel):
    board: str = "General"
    content: str
    to_id: Optional[str] = None
    subject: Optional[str] = None


class MessageResponse(BaseModel):
    id: int
    from_id: str
    from_name: Optional[str]
    to_id: Optional[str]
    text: str
    channel: int
    is_direct: bool
    timestamp: str
    snr: Optional[float]
    rssi: Optional[int]


class NodeResponse(BaseModel):
    node_id: str
    short_name: str
    long_name: str
    hardware: str
    battery_level: Optional[int]
    latitude: Optional[float]
    longitude: Optional[float]
    last_heard: Optional[str]
    is_online: bool


class StatusResponse(BaseModel):
    connected: bool
    connection_state: str  # disconnected, connecting, connected, error
    node_count: int
    online_count: int
    message_count: int
    my_node_id: Optional[str]


# WebSocket connection manager
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(f"WebSocket disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected clients."""
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


# REST API endpoints
@api_router.get("/status", response_model=StatusResponse)
async def get_status():
    """Get current system status."""
    from ..main import app_state
    
    message_count = await db.get_message_count()
    
    node_count = 0
    online_count = 0
    my_node_id = None
    connected = False
    connection_state = "disconnected"
    
    if app_state.mesh:
        connected = app_state.mesh.is_connected()
        connection_state = app_state.mesh.state.value
        nodes = app_state.mesh.get_all_nodes()
        node_count = len(nodes)
        online_count = sum(1 for n in nodes if n.is_online)
        my_node_id = app_state.mesh.my_node_id
    
    return StatusResponse(
        connected=connected,
        connection_state=connection_state,
        node_count=node_count,
        online_count=online_count,
        message_count=message_count,
        my_node_id=my_node_id
    )


@api_router.get("/messages")
async def get_messages(
    limit: int = Query(default=50, le=200),
    channel: Optional[int] = None
):
    """Get recent messages."""
    messages = await db.get_messages(limit=limit, channel=channel)
    return [msg.to_dict() for msg in messages]


@api_router.get("/nodes")
async def get_nodes():
    """Get all known nodes."""
    from ..main import app_state
    
    if not app_state.mesh:
        return []
    
    nodes = app_state.mesh.get_all_nodes()
    # Sort: online first, then by signal strength (SNR), then by last heard
    sorted_nodes = sorted(nodes, key=lambda n: (
        not n.is_online,  # False (online) sorts before True (offline)
        -(n.snr or -999),  # Higher SNR first
        -(n.last_heard.timestamp() if n.last_heard else 0)  # More recent first
    ))
    return [
        {
            "node_id": n.node_id,
            "short_name": n.short_name,
            "long_name": n.long_name,
            "hardware": n.hardware,
            "battery_level": n.battery_level,
            "latitude": n.latitude,
            "longitude": n.longitude,
            "last_heard": n.last_heard.isoformat() if n.last_heard else None,
            "is_online": n.is_online,
            "snr": n.snr,
            "hops_away": n.hops_away,
        }
        for n in sorted_nodes
    ]


@api_router.get("/stats/top")
async def get_top_talkers(limit: int = Query(default=20, le=100)):
    """Get top nodes by message count in last 24 hours."""
    from datetime import datetime, timedelta
    from sqlalchemy import func, and_
    from sqlalchemy.future import select
    from ..database import Message
    from ..main import app_state
    
    since = datetime.utcnow() - timedelta(hours=24)
    
    try:
        async with await db.get_session() as session:
            query = (
                select(Message.from_id, Message.from_name, func.count(Message.id).label('count'))
                .where(and_(Message.timestamp >= since, Message.from_id.isnot(None)))
                .group_by(Message.from_id, Message.from_name)
                .order_by(func.count(Message.id).desc())
                .limit(limit)
            )
            result = await session.execute(query)
            rows = result.all()
    except Exception as e:
        logger.error(f"Failed to get top talkers: {e}")
        return {"nodes": [], "total": 0, "error": str(e)}
    
    # Enrich with node info from live mesh
    nodes_list = []
    for row in rows:
        node_info = {
            "node_id": row.from_id,
            "name": row.from_name or row.from_id,
            "message_count": row.count,
            "short_name": None,
            "hardware": None,
            "is_online": False
        }
        
        # Try to get node details from mesh
        if app_state.mesh:
            for node in app_state.mesh.get_all_nodes():
                if str(node.node_id) == str(row.from_id):
                    node_info["name"] = node.long_name or node.short_name or row.from_name or row.from_id
                    node_info["short_name"] = node.short_name
                    node_info["hardware"] = node.hardware
                    node_info["is_online"] = node.is_online
                    break
        
        nodes_list.append(node_info)
    
    return {"nodes": nodes_list, "total": len(nodes_list)}


@api_router.get("/stats/summary")
async def get_stats_summary():
    """Get network statistics summary."""
    from ..main import app_state
    
    # Node stats
    local_nodes = []
    if app_state.mesh:
        local_nodes = app_state.mesh.get_all_nodes()
    
    online_count = sum(1 for n in local_nodes if n.is_online)
    hardware_breakdown = {}
    for node in local_nodes:
        hw = node.hardware or "Unknown"
        hardware_breakdown[hw] = hardware_breakdown.get(hw, 0) + 1
    
    # Message stats
    all_messages = await db.get_messages(limit=10000)
    total_messages = len(all_messages)
    
    from datetime import datetime, timedelta
    since_24h = datetime.utcnow() - timedelta(hours=24)
    messages_24h = [m for m in all_messages if m.timestamp and m.timestamp >= since_24h]
    
    return {
        "total_nodes": len(local_nodes),
        "online_nodes": online_count,
        "total_messages": total_messages,
        "messages_24h": len(messages_24h),
        "hardware_breakdown": hardware_breakdown
    }


@api_router.post("/send")
async def send_message(request: SendMessageRequest):
    """Send a message to the mesh network."""
    from ..main import app_state
    
    if not app_state.mesh or not app_state.mesh.is_connected():
        raise HTTPException(status_code=503, detail="Not connected to mesh")
    
    # Add bot prefix to identify web-sent messages
    text = f"{config.safety.ai_message_prefix}{request.text}"
    
    success = await app_state.mesh.send_message(
        text=text,
        destination=request.destination,
        channel=request.channel
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to send message")
    
    # Store in database
    msg = Message(
        from_id=app_state.mesh.my_node_id or "web",
        from_name="Web Portal",
        to_id=request.destination,
        text=request.text,
        channel=request.channel,
        is_direct=request.destination is not None,
        is_from_bot=True,
        timestamp=datetime.utcnow()
    )
    await db.add_message(msg)
    
    # Broadcast to WebSocket clients
    await manager.broadcast({
        "type": "message_sent",
        "data": msg.to_dict()
    })
    
    return {"status": "sent", "message_id": msg.id}


@api_router.get("/bbs/posts")
async def get_bbs_posts(
    board: Optional[str] = None,
    limit: int = Query(default=50, le=200)
):
    """Get BBS posts."""
    if board:
        posts = await bbs_service.get_board_posts(board, limit=limit)
    else:
        posts = await bbs_service.get_all_posts(limit=limit)
    
    return [post.to_dict() for post in posts]


@api_router.post("/bbs/post")
async def create_bbs_post(request: BBSPostRequest):
    """Create a new BBS post."""
    post = await bbs_service.post_message(
        board=request.board,
        from_id="web",
        content=request.content,
        from_name="Web Portal",
        to_id=request.to_id,
        subject=request.subject
    )
    
    # Broadcast to WebSocket clients
    await manager.broadcast({
        "type": "bbs_post",
        "data": post.to_dict()
    })
    
    return post.to_dict()


@api_router.get("/weather")
async def get_weather(
    lat: Optional[float] = None,
    lon: Optional[float] = None
):
    """Get current weather and forecast for dashboard."""
    current_data = await weather_service.get_current_weather_data(lat, lon)
    forecast_data = await weather_service.get_forecast_data(lat, lon, days=3)
    
    return {
        "current": current_data,
        "forecast": forecast_data
    }


@api_router.get("/forecast")
async def get_forecast(
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    days: int = Query(default=3, le=7)
):
    """Get weather forecast."""
    forecast = await weather_service.get_forecast(lat, lon, days)
    return {"forecast": forecast}


# WebSocket endpoint
@api_router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await manager.connect(websocket)
    
    try:
        # Send initial status
        from ..main import app_state
        
        await websocket.send_json({
            "type": "connected",
            "data": {
                "mesh_connected": app_state.mesh and app_state.mesh.is_connected()
            }
        })
        
        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=30.0
                )
                
                # Handle incoming WebSocket messages
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    
                elif data.get("type") == "send_message":
                    # Handle message send request from WebSocket
                    if app_state.mesh and app_state.mesh.is_connected():
                        text = data.get("text", "")
                        dest = data.get("destination")
                        channel = data.get("channel", 0)
                        
                        text = f"{config.safety.ai_message_prefix}{text}"
                        await app_state.mesh.send_message(text, dest, channel)
                        
            except asyncio.TimeoutError:
                # Send keepalive ping
                await websocket.send_json({"type": "keepalive"})
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)


# Function to broadcast new messages (called from message handler)
async def broadcast_message(message: dict):
    """Broadcast a new message to all WebSocket clients."""
    await manager.broadcast({
        "type": "new_message",
        "data": message
    })


async def broadcast_node_update(node: dict):
    """Broadcast a node update to all WebSocket clients."""
    await manager.broadcast({
        "type": "node_update",
        "data": node
    })


# =============================================================================
# Regional MQTT Endpoints
# =============================================================================

@api_router.get("/regional/nodes")
async def get_regional_nodes():
    """Get nodes discovered via regional MQTT.
    
    This is SEPARATE from your local mesh - these are nodes
    discovered from the regional MQTT feed.
    """
    from ..services.mqtt_regional import mqtt_regional
    
    if not mqtt_regional or not mqtt_regional.is_connected():
        return []
    
    return mqtt_regional.get_nodes()


@api_router.get("/regional/stats")
async def get_regional_stats():
    """Get regional MQTT service statistics."""
    from ..services.mqtt_regional import mqtt_regional
    
    if not mqtt_regional:
        return {
            "enabled": False,
            "connected": False,
            "message": "Regional MQTT is disabled in config"
        }
    
    stats = mqtt_regional.get_stats()
    stats["enabled"] = True
    return stats


@api_router.get("/regional/node/{node_id}")
async def get_regional_node(node_id: str):
    """Get a specific regional node."""
    from ..services.mqtt_regional import mqtt_regional
    
    if not mqtt_regional:
        raise HTTPException(status_code=503, detail="Regional MQTT not enabled")
    
    node = mqtt_regional.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    
    return node


@api_router.get("/regional/messages")
async def get_regional_messages(limit: int = Query(50, le=100)):
    """Get recent text messages from the regional MQTT feed.
    
    These are messages sent by nodes on the regional network.
    """
    from ..services.mqtt_regional import mqtt_regional
    
    if not mqtt_regional:
        return []
    
    return mqtt_regional.get_messages(limit=limit)


# ─────────────────────────────────────────────────────────────
# ADMIN API ENDPOINTS
# ─────────────────────────────────────────────────────────────

class ConfigUpdateRequest(BaseModel):
    """Request to update configuration."""
    section: str
    key: str
    value: str


class BrandingConfigRequest(BaseModel):
    """Branding/community settings."""
    community_name: Optional[str] = None
    community_description: Optional[str] = None
    location_name: Optional[str] = None
    bot_short_name: Optional[str] = None
    bot_long_name: Optional[str] = None
    about_heading: Optional[str] = None
    about_text: Optional[str] = None
    about_footer: Optional[str] = None


class MeshConfigRequest(BaseModel):
    """Mesh connection settings."""
    protocol: Optional[str] = None
    connection_type: Optional[str] = None
    serial_port: Optional[str] = None
    max_message_length: Optional[int] = None
    chunk_delay_seconds: Optional[int] = None


class LLMConfigRequest(BaseModel):
    """LLM settings."""
    provider: Optional[str] = None
    lmstudio_url: Optional[str] = None
    lmstudio_model: Optional[str] = None
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None


class WeatherConfigRequest(BaseModel):
    """Weather settings."""
    enabled: Optional[bool] = None
    default_lat: Optional[float] = None
    default_lon: Optional[float] = None
    temperature_unit: Optional[str] = None


class MQTTConfigRequest(BaseModel):
    """MQTT settings."""
    enabled: Optional[bool] = None
    broker: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    topic: Optional[str] = None
    region_name: Optional[str] = None


def check_admin_auth(request):
    """Check if request is from authenticated admin."""
    from .routes import is_authenticated
    from fastapi import Request
    # For API calls, check session cookie
    session_id = request.cookies.get("session_id")
    from .routes import _sessions
    if session_id not in _sessions:
        raise HTTPException(status_code=401, detail="Admin authentication required")
    return True


@api_router.get("/admin/config")
async def get_admin_config(request: Request):
    """Get current configuration (admin only)."""
    check_admin_auth(request)
    
    return {
        "branding": {
            "community_name": config.web.community_name,
            "community_description": config.web.community_description,
            "location_name": config.web.location_name,
            "bot_short_name": config.mesh.bot_short_name,
            "bot_long_name": config.mesh.bot_name,
        },
        "mesh": {
            "protocol": config.mesh.protocol,
            "connection_type": config.mesh.connection_type,
            "serial_port": config.mesh.serial_port,
            "max_message_length": config.mesh.max_message_length,
            "chunk_delay_seconds": config.mesh.chunk_delay_seconds,
        },
        "llm": {
            "provider": config.llm.provider,
            "lmstudio_url": config.llm.lmstudio_url,
            "lmstudio_model": config.llm.lmstudio_model,
            "ollama_url": config.llm.ollama_url,
            "ollama_model": config.llm.ollama_model,
        },
        "weather": {
            "enabled": config.weather.enabled,
            "default_lat": config.weather.default_lat,
            "default_lon": config.weather.default_lon,
            "temperature_unit": config.weather.temperature_unit,
        },
        "mqtt": {
            "enabled": config.mqtt.enabled,
            "broker": config.mqtt.broker,
            "port": config.mqtt.port,
            "username": config.mqtt.username,
            "topic": config.mqtt.topic,
            "region_name": config.mqtt.region_name,
        },
        "bbs": {
            "enabled": config.bbs.enabled,
            "max_messages_per_user": config.bbs.max_messages_per_user,
            "message_expiry_days": config.bbs.message_expiry_days,
        },
        "kiwix": {
            "enabled": config.kiwix.enabled,
            "url": config.kiwix.url,
            "library": config.kiwix.library,
            "ai_enhanced": config.kiwix.ai_enhanced,
        }
    }


@api_router.post("/admin/config/branding")
async def update_branding_config(request: Request, data: BrandingConfigRequest):
    """Update branding/community settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    if not config_path.exists():
        raise HTTPException(status_code=500, detail="Config file not found")
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    # Update web section
    if 'web' not in cfg:
        cfg['web'] = {}
    
    if data.community_name is not None:
        cfg['web']['community_name'] = data.community_name
    if data.community_description is not None:
        cfg['web']['community_description'] = data.community_description
    if data.location_name is not None:
        cfg['web']['location_name'] = data.location_name
    if data.about_heading is not None:
        cfg['web']['about_heading'] = data.about_heading
    if data.about_text is not None:
        cfg['web']['about_text'] = data.about_text
    if data.about_footer is not None:
        cfg['web']['about_footer'] = data.about_footer
    
    # Update mesh section for bot name
    if 'mesh' not in cfg:
        cfg['mesh'] = {}
    
    if data.bot_short_name is not None:
        cfg['mesh']['bot_short_name'] = data.bot_short_name
    if data.bot_long_name is not None:
        cfg['mesh']['bot_name'] = data.bot_long_name
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "Branding config updated. Restart to apply."}


@api_router.post("/admin/config/mesh")
async def update_mesh_config(request: Request, data: MeshConfigRequest):
    """Update mesh connection settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    if 'mesh' not in cfg:
        cfg['mesh'] = {}
    
    if data.protocol is not None:
        cfg['mesh']['protocol'] = data.protocol
    if data.connection_type is not None:
        cfg['mesh']['connection_type'] = data.connection_type
    if data.serial_port is not None:
        cfg['mesh']['serial_port'] = data.serial_port
    if data.max_message_length is not None:
        cfg['mesh']['max_message_length'] = data.max_message_length
    if data.chunk_delay_seconds is not None:
        cfg['mesh']['chunk_delay_seconds'] = data.chunk_delay_seconds
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "Mesh config updated. Restart to apply."}


@api_router.post("/admin/config/llm")
async def update_llm_config(request: Request, data: LLMConfigRequest):
    """Update LLM settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    if 'llm' not in cfg:
        cfg['llm'] = {}
    
    if data.provider is not None:
        cfg['llm']['provider'] = data.provider
    if data.lmstudio_url is not None:
        cfg['llm']['lmstudio_url'] = data.lmstudio_url
    if data.lmstudio_model is not None:
        cfg['llm']['lmstudio_model'] = data.lmstudio_model
    if data.ollama_url is not None:
        cfg['llm']['ollama_url'] = data.ollama_url
    if data.ollama_model is not None:
        cfg['llm']['ollama_model'] = data.ollama_model
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "LLM config updated. Restart to apply."}


@api_router.post("/admin/config/weather")
async def update_weather_config(request: Request, data: WeatherConfigRequest):
    """Update weather settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    if 'weather' not in cfg:
        cfg['weather'] = {}
    
    if data.enabled is not None:
        cfg['weather']['enabled'] = data.enabled
    if data.default_lat is not None:
        cfg['weather']['default_lat'] = data.default_lat
    if data.default_lon is not None:
        cfg['weather']['default_lon'] = data.default_lon
    if data.temperature_unit is not None:
        cfg['weather']['temperature_unit'] = data.temperature_unit
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "Weather config updated. Restart to apply."}


@api_router.post("/admin/config/mqtt")
async def update_mqtt_config(request: Request, data: MQTTConfigRequest):
    """Update MQTT settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    if 'mqtt' not in cfg:
        cfg['mqtt'] = {}
    
    if data.enabled is not None:
        cfg['mqtt']['enabled'] = data.enabled
    if data.broker is not None:
        cfg['mqtt']['broker'] = data.broker
    if data.port is not None:
        cfg['mqtt']['port'] = data.port
    if data.username is not None:
        cfg['mqtt']['username'] = data.username
    if data.password is not None:
        cfg['mqtt']['password'] = data.password
    if data.topic is not None:
        cfg['mqtt']['topic'] = data.topic
    if data.region_name is not None:
        cfg['mqtt']['region_name'] = data.region_name
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "MQTT config updated. Restart to apply."}


@api_router.post("/admin/restart")
async def restart_services(request: Request):
    """Trigger a service restart (requires manual confirmation)."""
    check_admin_auth(request)
    
    # We can't actually restart from within the process safely
    # Instead, return instructions
    return {
        "status": "manual_required",
        "message": "To apply changes, restart the service manually.",
        "command": "cd /path/to/meshbot && ./restart-all.sh"
    }


@api_router.post("/admin/test/mesh")
async def test_mesh_connection(request: Request):
    """Test mesh connection."""
    check_admin_auth(request)
    
    from ..main import app_state
    
    if not app_state.mesh:
        return {"status": "error", "message": "Mesh interface not initialized"}
    
    connected = app_state.mesh.is_connected()
    state = app_state.mesh.get_connection_state()
    
    return {
        "status": "ok" if connected else "error",
        "connected": connected,
        "state": state,
        "node_id": app_state.mesh.my_node_id
    }


@api_router.post("/admin/test/llm")
async def test_llm_connection(request: Request):
    """Test LLM connection."""
    check_admin_auth(request)
    
    from ..services.ai import ai_service
    
    try:
        response = await ai_service.quick_complete("Say 'OK' if you can hear me.")
        return {
            "status": "ok",
            "response": response[:100] if response else "Empty response",
            "provider": config.llm.provider
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "provider": config.llm.provider
        }


class KiwixConfigRequest(BaseModel):
    """Kiwix settings."""
    enabled: Optional[bool] = None
    url: Optional[str] = None
    library: Optional[str] = None
    ai_enhanced: Optional[bool] = None


@api_router.post("/admin/config/kiwix")
async def update_kiwix_config(request: Request, data: KiwixConfigRequest):
    """Update Kiwix settings."""
    check_admin_auth(request)
    
    import yaml
    from pathlib import Path
    
    config_path = Path(__file__).parent.parent.parent / "config.yaml"
    
    with open(config_path, 'r') as f:
        cfg = yaml.safe_load(f) or {}
    
    if 'kiwix' not in cfg:
        cfg['kiwix'] = {}
    
    if data.enabled is not None:
        cfg['kiwix']['enabled'] = data.enabled
    if data.url is not None:
        cfg['kiwix']['url'] = data.url
    if data.library is not None:
        cfg['kiwix']['library'] = data.library
    if data.ai_enhanced is not None:
        cfg['kiwix']['ai_enhanced'] = data.ai_enhanced
    
    with open(config_path, 'w') as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    
    return {"status": "saved", "message": "Kiwix config updated. Restart to apply."}


@api_router.post("/admin/test/kiwix")
async def test_kiwix_connection(request: Request):
    """Test Kiwix server connection."""
    check_admin_auth(request)
    
    import httpx
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(config.kiwix.url)
            if response.status_code == 200:
                return {
                    "status": "ok",
                    "message": f"Server responding at {config.kiwix.url}"
                }
            else:
                return {
                    "status": "error", 
                    "message": f"Server returned {response.status_code}"
                }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Cannot connect: {str(e)}"
        }


@api_router.get("/admin/detect/serial")
async def detect_serial_ports(request: Request):
    """Detect available serial ports for Meshtastic devices."""
    check_admin_auth(request)
    
    import glob
    import platform
    
    ports = []
    system = platform.system()
    
    if system == "Darwin":  # macOS
        # Look for USB modems (Meshtastic devices)
        patterns = [
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.SLAB*",
            "/dev/cu.wchusbserial*"
        ]
    elif system == "Linux":
        patterns = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
            "/dev/serial/by-id/*"
        ]
    else:  # Windows
        # On Windows, use serial.tools.list_ports
        try:
            import serial.tools.list_ports
            for port in serial.tools.list_ports.comports():
                ports.append({
                    "path": port.device,
                    "description": port.description,
                    "manufacturer": port.manufacturer or ""
                })
            return {"status": "ok", "ports": ports}
        except:
            return {"status": "error", "message": "Cannot detect ports on Windows", "ports": []}
    
    # Find matching ports
    for pattern in patterns:
        for path in glob.glob(pattern):
            # Try to get more info about the device
            description = ""
            if "usbmodem" in path:
                description = "USB Modem (likely Meshtastic)"
            elif "usbserial" in path:
                description = "USB Serial"
            elif "SLAB" in path:
                description = "Silicon Labs USB"
            
            ports.append({
                "path": path,
                "description": description,
                "manufacturer": ""
            })
    
    # Sort by path
    ports.sort(key=lambda x: x["path"])
    
    return {
        "status": "ok",
        "ports": ports,
        "current": config.mesh.serial_port
    }
