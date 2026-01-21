"""Main entry point for the Community Mesh Platform.

This module initializes and runs the FastAPI application with:
- Meshtastic/MeshCore mesh interface
- LLM integration (LM Studio)
- Web portal and API
- Real-time WebSocket updates
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import config
from .database import db, Message
from .interfaces import MeshtasticInterface, MeshCoreInterface
from .interfaces.base import MeshMessage, MeshNode
from .services.commands import CommandRouter
from .services.mqtt_regional import init_mqtt_regional, mqtt_regional
from .web.routes import router as web_router
from .web.api import api_router, broadcast_message, broadcast_node_update

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AppState:
    """Global application state."""
    def __init__(self):
        self.mesh: MeshtasticInterface = None
        self.command_router: CommandRouter = None
        self.running = False


app_state = AppState()


async def handle_message(message: MeshMessage) -> None:
    """Handle incoming mesh messages."""
    try:
        # Log the message
        logger.info(f"Message from {message.from_id}: {message.text[:50]}...")
        
        # Store in database
        msg = Message(
            message_id=message.message_id,
            from_id=message.from_id,
            from_name=message.from_node.short_name if message.from_node else None,
            to_id=message.to_id,
            text=message.text,
            channel=message.channel,
            is_direct=message.is_direct,
            is_from_bot=False,
            timestamp=message.timestamp,
            snr=message.snr,
            rssi=message.rssi
        )
        await db.add_message(msg)
        
        # Broadcast to WebSocket clients
        await broadcast_message(msg.to_dict())
        
        # Process through command router
        if app_state.command_router:
            response = await app_state.command_router.process_message(message)
            
            if response:
                # Send response
                destination = message.from_id if message.is_direct else None
                await app_state.mesh.send_chunked_message(
                    text=response,
                    destination=destination,
                    channel=message.channel,
                    chunk_size=config.mesh.max_message_length,
                    delay_seconds=config.mesh.chunk_delay_seconds
                )
                
                # Store bot response
                bot_msg = Message(
                    from_id=app_state.mesh.my_node_id or "bot",
                    from_name=config.mesh.bot_name,
                    to_id=destination,
                    text=response,
                    channel=message.channel,
                    is_direct=destination is not None,
                    is_from_bot=True,
                    timestamp=datetime.utcnow()
                )
                await db.add_message(bot_msg)
                await broadcast_message(bot_msg.to_dict())
                
    except Exception as e:
        logger.error(f"Error handling message: {e}", exc_info=True)


def handle_node_update(node: MeshNode) -> None:
    """Handle node updates."""
    try:
        logger.debug(f"Node update: {node.node_id} ({node.short_name})")
        
        # Broadcast to WebSocket clients
        asyncio.create_task(broadcast_node_update({
            "node_id": node.node_id,
            "short_name": node.short_name,
            "long_name": node.long_name,
            "is_online": node.is_online,
            "battery_level": node.battery_level,
            "last_heard": node.last_heard.isoformat() if node.last_heard else None
        }))
        
        # Update database
        asyncio.create_task(db.update_node({
            "node_id": node.node_id,
            "short_name": node.short_name,
            "long_name": node.long_name,
            "hardware": node.hardware,
            "latitude": node.latitude,
            "longitude": node.longitude,
            "altitude": node.altitude,
            "battery_level": node.battery_level,
            "last_heard": node.last_heard
        }))
        
    except Exception as e:
        logger.error(f"Error handling node update: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    logger.info("=" * 60)
    logger.info("Community Mesh Platform Starting...")
    logger.info("=" * 60)
    
    # Initialize database
    logger.info("Initializing database...")
    await db.initialize()
    
    # Create mesh interface
    logger.info(f"Connecting to mesh ({config.mesh.protocol})...")
    
    if config.mesh.protocol == "meshtastic":
        app_state.mesh = MeshtasticInterface(
            connection_type=config.mesh.connection_type,
            serial_port=config.mesh.serial_port,
            tcp_host=config.mesh.tcp_host,
            tcp_port=config.mesh.tcp_port
        )
    elif config.mesh.protocol == "meshcore":
        app_state.mesh = MeshCoreInterface(
            serial_port=config.mesh.serial_port,
            tcp_host=config.mesh.tcp_host,
            tcp_port=config.mesh.tcp_port
        )
    else:
        logger.error(f"Unknown mesh protocol: {config.mesh.protocol}")
        logger.error("Supported protocols: meshtastic, meshcore")
        raise ValueError(f"Unknown protocol: {config.mesh.protocol}")
    
    # Register message handler - needs to work from Meshtastic's thread
    loop = asyncio.get_running_loop()
    
    def sync_message_handler(message: MeshMessage):
        asyncio.run_coroutine_threadsafe(handle_message(message), loop)
    
    app_state.mesh.on_message(sync_message_handler)
    app_state.mesh.on_node_update(handle_node_update)
    
    # Connect to mesh
    connected = await app_state.mesh.connect()
    if connected:
        logger.info(f"✓ Connected to mesh as {app_state.mesh.my_node_id}")
    else:
        logger.warning("⚠ Failed to connect to mesh - running in offline mode")
    
    # Create command router
    app_state.command_router = CommandRouter(app_state.mesh)
    
    # Start regional MQTT service (optional, for Ohio network)
    mqtt_config = {
        'mqtt': {
            'enabled': config.mqtt.enabled,
            'broker': config.mqtt.broker,
            'port': config.mqtt.port,
            'username': config.mqtt.username,
            'password': config.mqtt.password,
            'topic': config.mqtt.topic,
        }
    }
    mqtt_svc = init_mqtt_regional(mqtt_config)
    if mqtt_svc:
        logger.info(f"✓ Regional MQTT connected to {mqtt_svc.broker}")
    
    app_state.running = True
    logger.info("=" * 60)
    logger.info(f"✓ {config.web.community_name} is ready!")
    logger.info(f"  Web Portal: http://{config.web.host}:{config.web.port}")
    logger.info(f"  Dashboard:  http://{config.web.host}:{config.web.port}/dashboard")
    logger.info("=" * 60)
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    app_state.running = False
    
    if app_state.mesh:
        await app_state.mesh.disconnect()
    
    from .services.ai import ai_service
    from .services.weather import weather_service
    from .services.mqtt_regional import mqtt_regional as mqtt_regional_svc
    
    await ai_service.close()
    await weather_service.close()
    
    if mqtt_regional_svc:
        mqtt_regional_svc.stop()
    
    logger.info("Goodbye!")


# Create FastAPI app
app = FastAPI(
    title=config.web.community_name,
    description="Community Mesh Platform - AI Assistant, BBS, and more",
    version="1.0.0",
    lifespan=lifespan
)

# Mount static files
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Include routers
app.include_router(web_router)
app.include_router(api_router)


def main():
    """Run the application."""
    import uvicorn
    
    print("""
    ╔═══════════════════════════════════════════════════════════╗
    ║                                                           ║
    ║   📡 COMMUNITY MESH PLATFORM                              ║
    ║                                                           ║
    ║   AI Assistant • Bulletin Board • Weather • Web Portal    ║
    ║                                                           ║
    ╚═══════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        "app.main:app",
        host=config.web.host,
        port=config.web.port,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
