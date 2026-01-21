"""Meshtastic implementation of the mesh interface.

This module provides the Meshtastic-specific implementation of the MeshInterface
abstract class, handling connection to T-Deck and other Meshtastic devices.
"""

import asyncio
import threading
from datetime import datetime
from typing import Optional, Any
import logging

from .base import MeshInterface, MeshMessage, MeshNode, ConnectionState

logger = logging.getLogger(__name__)


class MeshtasticInterface(MeshInterface):
    """Meshtastic-specific mesh interface implementation.
    
    Supports serial (USB), TCP (WiFi), and BLE connections to Meshtastic devices.
    """
    
    def __init__(
        self,
        connection_type: str = "serial",
        serial_port: Optional[str] = None,
        tcp_host: str = "meshtastic.local",
        tcp_port: int = 4403,
    ):
        super().__init__()
        self.connection_type = connection_type
        self.serial_port = serial_port
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self._interface = None
        self._loop = None
        self._running = False
        self._reconnect_task = None
        self._reconnecting = False
        self._max_reconnect_attempts = 20
        self._reconnect_delay = 15  # seconds between attempts
        self._last_heard: dict[str, float] = {}  # Track when we last heard from each node
    
    async def connect(self) -> bool:
        """Connect to the Meshtastic device."""
        try:
            self._set_connection_state(ConnectionState.CONNECTING)
            
            # Import meshtastic modules
            import meshtastic
            import meshtastic.serial_interface
            import meshtastic.tcp_interface
            from pubsub import pub
            
            # Connect based on type
            if self.connection_type == "serial":
                if self.serial_port:
                    logger.info(f"Connecting to Meshtastic via serial: {self.serial_port}")
                    self._interface = meshtastic.serial_interface.SerialInterface(
                        devPath=self.serial_port
                    )
                else:
                    logger.info("Connecting to Meshtastic via serial (auto-detect)")
                    self._interface = meshtastic.serial_interface.SerialInterface()
                    
            elif self.connection_type == "tcp":
                logger.info(f"Connecting to Meshtastic via TCP: {self.tcp_host}:{self.tcp_port}")
                self._interface = meshtastic.tcp_interface.TCPInterface(
                    hostname=self.tcp_host,
                    portNumber=self.tcp_port
                )
            else:
                raise ValueError(f"Unknown connection type: {self.connection_type}")
            
            # Get our node ID
            if self._interface.myInfo:
                self._my_node_id = f"!{self._interface.myInfo.my_node_num:08x}"
                logger.info(f"Connected as node: {self._my_node_id}")
            
            # Subscribe to events
            pub.subscribe(self._on_receive, "meshtastic.receive")
            pub.subscribe(self._on_connection, "meshtastic.connection.established")
            pub.subscribe(self._on_node_update_event, "meshtastic.node.updated")
            pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")
            
            # Load existing nodes
            self._load_nodes()
            
            self._running = True
            self._set_connection_state(ConnectionState.CONNECTED)
            logger.info("Meshtastic connection established")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._set_connection_state(ConnectionState.ERROR)
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from the Meshtastic device."""
        self._running = False
        if self._interface:
            try:
                from pubsub import pub
                pub.unsubscribe(self._on_receive, "meshtastic.receive")
                pub.unsubscribe(self._on_connection, "meshtastic.connection.established")
                pub.unsubscribe(self._on_node_update_event, "meshtastic.node.updated")
                pub.unsubscribe(self._on_disconnect, "meshtastic.connection.lost")
            except:
                pass
            
            try:
                self._interface.close()
            except:
                pass
            self._interface = None
        
        self._set_connection_state(ConnectionState.DISCONNECTED)
        logger.info("Meshtastic disconnected")
    
    def is_connected(self) -> bool:
        """Check if actually connected to Meshtastic device."""
        if self._interface is None or not self._running:
            return False
        
        # Actually verify the connection is alive
        try:
            # Check if the underlying stream/serial is still open
            if hasattr(self._interface, '_sendQueue'):
                # Serial interface - check if the reader thread is alive
                if hasattr(self._interface, '_rxThread'):
                    return self._interface._rxThread is not None and self._interface._rxThread.is_alive()
            # Fallback: try to access nodes (will fail if disconnected)
            _ = self._interface.nodes
            return True
        except Exception:
            return False
    
    async def send_message(
        self,
        text: str,
        destination: Optional[str] = None,
        channel: int = 0,
        want_ack: bool = False
    ) -> bool:
        """Send a message via Meshtastic."""
        if not self.is_connected():
            logger.error("Cannot send message: not connected")
            return False
        
        try:
            if destination:
                # Direct message
                logger.info(f"Sending DM to {destination}: {text[:50]}...")
                self._interface.sendText(
                    text,
                    destinationId=destination,
                    wantAck=want_ack,
                    channelIndex=channel
                )
            else:
                # Broadcast
                logger.info(f"Broadcasting on channel {channel}: {text[:50]}...")
                self._interface.sendText(
                    text,
                    wantAck=want_ack,
                    channelIndex=channel
                )
            return True
            
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False
    
    def get_node(self, node_id: str) -> Optional[MeshNode]:
        """Get info about a specific node."""
        return self._nodes.get(node_id)
    
    def get_all_nodes(self) -> list[MeshNode]:
        """Get all known nodes with current online status."""
        from dataclasses import replace
        nodes = []
        for node in self._nodes.values():
            # Update is_online based on current tracking
            updated_node = replace(node, is_online=self._is_node_online(node.node_id))
            nodes.append(updated_node)
        return nodes
    
    def _load_nodes(self) -> None:
        """Load nodes from the Meshtastic interface."""
        if not self._interface or not self._interface.nodes:
            return
        
        for node_id, node_data in self._interface.nodes.items():
            # Normalize node ID
            if not node_id.startswith("!"):
                node_id = f"!{node_id}"
            
            # Initialize _last_heard from Meshtastic's lastHeard timestamp
            if node_data.get("lastHeard"):
                self._last_heard[node_id] = node_data["lastHeard"]
            
            node = self._parse_node(node_id, node_data)
            if node:
                self._nodes[node.node_id] = node
        
        logger.info(f"Loaded {len(self._nodes)} nodes")
    
    def _is_node_online(self, node_id: str) -> bool:
        """Check if we've heard from a node recently (last 2 hours)."""
        if node_id not in self._last_heard:
            return False
        age = datetime.now().timestamp() - self._last_heard[node_id]
        return age < 7200  # 2 hours - mesh nodes don't always broadcast frequently
    
    def _parse_node(self, node_id: str, node_data: dict) -> Optional[MeshNode]:
        """Parse node data from Meshtastic format."""
        try:
            user = node_data.get("user", {})
            position = node_data.get("position", {})
            metrics = node_data.get("deviceMetrics", {})
            
            # Normalize node ID format
            if not node_id.startswith("!"):
                node_id = f"!{node_id}"
            
            return MeshNode(
                node_id=node_id,
                short_name=user.get("shortName", ""),
                long_name=user.get("longName", ""),
                hardware=user.get("hwModel", ""),
                battery_level=metrics.get("batteryLevel"),
                latitude=position.get("latitude"),
                longitude=position.get("longitude"),
                altitude=position.get("altitude"),
                last_heard=datetime.fromtimestamp(node_data.get("lastHeard", 0)) 
                    if node_data.get("lastHeard") else None,
                snr=node_data.get("snr"),
                hops_away=node_data.get("hopsAway"),
                is_online=self._is_node_online(node_id),  # Check our own tracking
                raw_data=node_data
            )
        except Exception as e:
            logger.warning(f"Failed to parse node {node_id}: {e}")
            return None
    
    def _on_receive(self, packet: dict, interface: Any) -> None:
        """Handle incoming Meshtastic packet."""
        try:
            from_id = packet.get('fromId')
            logger.info(f"RAW PACKET RECEIVED: {from_id} -> portnum={packet.get('decoded', {}).get('portnum', 'unknown')}")
            
            # Track when we heard from this node (for online status)
            if from_id and from_id != "^all":
                # Normalize node ID format
                if not from_id.startswith("!"):
                    from_id = f"!{from_id}"
                self._last_heard[from_id] = datetime.now().timestamp()
            
            # Only process text messages
            if "decoded" not in packet or "text" not in packet.get("decoded", {}):
                logger.debug(f"Ignoring non-text packet from {packet.get('fromId')}")
                return
            
            # Parse the message
            decoded = packet["decoded"]
            from_id = packet.get("fromId", "")
            to_id = packet.get("toId", "")
            
            # Determine if it's a direct message
            is_direct = to_id != "^all" and to_id == self._my_node_id
            
            # Get sender node info
            from_node = self.get_node(from_id)
            
            message = MeshMessage(
                message_id=str(packet.get("id", "")),
                from_id=from_id,
                to_id=to_id if to_id != "^all" else None,
                text=decoded.get("text", ""),
                timestamp=datetime.now(),
                channel=packet.get("channel", 0),
                is_direct=is_direct,
                hop_start=packet.get("hopStart"),
                hop_limit=packet.get("hopLimit"),
                snr=packet.get("rxSnr"),
                rssi=packet.get("rxRssi"),
                from_node=from_node,
                raw_packet=packet
            )
            
            logger.info(f"Received {'DM' if is_direct else 'broadcast'} from {from_id}: {message.text[:50]}...")
            self._dispatch_message(message)
            
        except Exception as e:
            logger.error(f"Error processing received packet: {e}")
    
    def _on_connection(self, interface: Any, topic: Any = None) -> None:
        """Handle connection established event."""
        logger.info("Meshtastic connection established event")
        self._set_connection_state(ConnectionState.CONNECTED)
    
    def _on_node_update_event(self, node: dict, interface: Any = None) -> None:
        """Handle node update event."""
        try:
            # Extract node ID from the update
            node_id = node.get("num")
            if node_id:
                node_id = f"!{node_id:08x}"
                
                # Get full node data from interface
                if self._interface and self._interface.nodes:
                    full_data = self._interface.nodes.get(node_id.replace("!", ""), {})
                    parsed = self._parse_node(node_id, full_data)
                    if parsed:
                        self._nodes[node_id] = parsed
                        self._dispatch_node_update(parsed)
        except Exception as e:
            logger.warning(f"Error handling node update: {e}")
    
    def _on_disconnect(self, interface: Any = None, topic: Any = None) -> None:
        """Handle disconnection event - trigger reconnection."""
        logger.warning("Meshtastic connection lost, will attempt to reconnect...")
        self._set_connection_state(ConnectionState.DISCONNECTED)
        self._running = False
        
        # Clean up old interface
        if self._interface:
            try:
                self._interface.close()
            except:
                pass
            self._interface = None
        
        # Start reconnection in background
        if not self._reconnecting:
            self._start_reconnect()
    
    def _start_reconnect(self) -> None:
        """Start the reconnection process."""
        if self._reconnecting:
            return
        
        self._reconnecting = True
        
        # Run reconnection in a separate thread to avoid blocking
        def reconnect_loop():
            import time
            attempts = 0
            
            while attempts < self._max_reconnect_attempts and self._reconnecting:
                attempts += 1
                wait_time = self._reconnect_delay  # Fixed delay between attempts
                
                logger.info(f"Reconnect attempt {attempts}/{self._max_reconnect_attempts} in {wait_time}s...")
                self._set_connection_state(ConnectionState.CONNECTING)
                time.sleep(wait_time)
                
                if not self._reconnecting:
                    break
                
                try:
                    # Try to reconnect
                    import meshtastic
                    import meshtastic.serial_interface
                    import meshtastic.tcp_interface
                    from pubsub import pub
                    
                    if self.connection_type == "serial":
                        if self.serial_port:
                            self._interface = meshtastic.serial_interface.SerialInterface(
                                devPath=self.serial_port
                            )
                        else:
                            self._interface = meshtastic.serial_interface.SerialInterface()
                    elif self.connection_type == "tcp":
                        self._interface = meshtastic.tcp_interface.TCPInterface(
                            hostname=self.tcp_host,
                            portNumber=self.tcp_port
                        )
                    
                    # Get our node ID
                    if self._interface.myInfo:
                        self._my_node_id = f"!{self._interface.myInfo.my_node_num:08x}"
                    
                    # Re-subscribe to events
                    pub.subscribe(self._on_receive, "meshtastic.receive")
                    pub.subscribe(self._on_connection, "meshtastic.connection.established")
                    pub.subscribe(self._on_node_update_event, "meshtastic.node.updated")
                    pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")
                    
                    # Reload nodes
                    self._load_nodes()
                    
                    self._running = True
                    self._reconnecting = False
                    self._set_connection_state(ConnectionState.CONNECTED)
                    logger.info(f"Reconnected successfully as {self._my_node_id}")
                    return
                    
                except Exception as e:
                    logger.error(f"Reconnect attempt {attempts} failed: {e}")
                    if self._interface:
                        try:
                            self._interface.close()
                        except:
                            pass
                        self._interface = None
            
            # All attempts failed
            self._reconnecting = False
            self._set_connection_state(ConnectionState.ERROR)
            logger.error(f"Failed to reconnect after {self._max_reconnect_attempts} attempts")
        
        thread = threading.Thread(target=reconnect_loop, daemon=True)
        thread.start()
