"""Abstract base interface for mesh protocols.

This abstraction layer allows the platform to support multiple mesh protocols
(Meshtastic now, MeshCore in the future) with a unified API.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional, Any
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class MeshNode:
    """Represents a node on the mesh network."""
    node_id: str
    short_name: str = ""
    long_name: str = ""
    hardware: str = ""
    battery_level: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None
    last_heard: Optional[datetime] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
    hops_away: Optional[int] = None
    is_online: bool = False
    raw_data: dict = field(default_factory=dict)


@dataclass
class MeshMessage:
    """Represents a message on the mesh network."""
    message_id: str
    from_id: str
    to_id: Optional[str]  # None for broadcast
    text: str
    timestamp: datetime = field(default_factory=datetime.now)
    channel: int = 0
    is_direct: bool = False
    hop_start: Optional[int] = None
    hop_limit: Optional[int] = None
    snr: Optional[float] = None
    rssi: Optional[int] = None
    from_node: Optional[MeshNode] = None
    raw_packet: dict = field(default_factory=dict)


class MeshInterface(ABC):
    """Abstract base class for mesh network interfaces.
    
    Implementations of this interface handle the specifics of different
    mesh protocols (Meshtastic, MeshCore, etc.) while exposing a unified API.
    """
    
    def __init__(self):
        self._message_callbacks: list[Callable[[MeshMessage], None]] = []
        self._node_callbacks: list[Callable[[MeshNode], None]] = []
        self._connection_callbacks: list[Callable[[ConnectionState], None]] = []
        self._state = ConnectionState.DISCONNECTED
        self._nodes: dict[str, MeshNode] = {}
        self._my_node_id: Optional[str] = None
    
    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state
    
    @property
    def my_node_id(self) -> Optional[str]:
        """This node's ID."""
        return self._my_node_id
    
    @property
    def nodes(self) -> dict[str, MeshNode]:
        """All known nodes on the network."""
        return self._nodes.copy()
    
    # Connection methods
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the mesh network. Returns True on success."""
        pass
    
    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the mesh network."""
        pass
    
    @abstractmethod
    def is_connected(self) -> bool:
        """Check if currently connected."""
        pass
    
    # Messaging methods
    @abstractmethod
    async def send_message(
        self, 
        text: str, 
        destination: Optional[str] = None,
        channel: int = 0,
        want_ack: bool = False
    ) -> bool:
        """Send a message to the mesh.
        
        Args:
            text: Message text to send
            destination: Node ID to send to (None for broadcast)
            channel: Channel number to send on
            want_ack: Whether to request acknowledgment
            
        Returns:
            True if message was sent successfully
        """
        pass
    
    async def send_chunked_message(
        self,
        text: str,
        destination: Optional[str] = None,
        channel: int = 0,
        chunk_size: int = 200,
        delay_seconds: float = 2.0,
        want_ack: bool = True
    ) -> bool:
        """Send a long message in chunks.
        
        Args:
            text: Full message text
            destination: Node ID to send to
            channel: Channel number
            chunk_size: Maximum characters per chunk
            delay_seconds: Delay between chunks
            want_ack: Request acknowledgment for each chunk (recommended for reliability)
            
        Returns:
            True if all chunks were sent
        """
        import asyncio
        
        if len(text) <= chunk_size:
            return await self.send_message(text, destination, channel, want_ack)
        
        # Split into chunks
        chunks = []
        for i in range(0, len(text), chunk_size):
            chunk = text[i:i + chunk_size]
            chunks.append(chunk)
        
        # Send each chunk with acknowledgment
        for i, chunk in enumerate(chunks):
            if i > 0:
                await asyncio.sleep(delay_seconds)
            
            # Add chunk indicator if multiple chunks
            if len(chunks) > 1:
                prefix = f"[{i+1}/{len(chunks)}] "
                chunk = prefix + chunk[:chunk_size - len(prefix)]
            
            success = await self.send_message(chunk, destination, channel, want_ack)
            if not success:
                logger.warning(f"Failed to send chunk {i+1}/{len(chunks)}")
                return False
        
        return True
    
    # Node methods
    @abstractmethod
    def get_node(self, node_id: str) -> Optional[MeshNode]:
        """Get info about a specific node."""
        pass
    
    @abstractmethod
    def get_all_nodes(self) -> list[MeshNode]:
        """Get all known nodes."""
        pass
    
    # Callback registration
    def on_message(self, callback: Callable[[MeshMessage], None]) -> None:
        """Register a callback for incoming messages."""
        self._message_callbacks.append(callback)
    
    def on_node_update(self, callback: Callable[[MeshNode], None]) -> None:
        """Register a callback for node updates."""
        self._node_callbacks.append(callback)
    
    def on_connection_change(self, callback: Callable[[ConnectionState], None]) -> None:
        """Register a callback for connection state changes."""
        self._connection_callbacks.append(callback)
    
    # Internal callback dispatch
    def _dispatch_message(self, message: MeshMessage) -> None:
        """Dispatch a message to all registered callbacks."""
        for callback in self._message_callbacks:
            try:
                callback(message)
            except Exception as e:
                print(f"Error in message callback: {e}")
    
    def _dispatch_node_update(self, node: MeshNode) -> None:
        """Dispatch a node update to all registered callbacks."""
        for callback in self._node_callbacks:
            try:
                callback(node)
            except Exception as e:
                print(f"Error in node callback: {e}")
    
    def _set_connection_state(self, state: ConnectionState) -> None:
        """Update connection state and notify callbacks."""
        self._state = state
        for callback in self._connection_callbacks:
            try:
                callback(state)
            except Exception as e:
                print(f"Error in connection callback: {e}")
