"""Regional MQTT Integration for Ohio Meshtastic Network.

This service connects to the regional MQTT broker to collect node data
from the broader Ohio mesh network. This is kept SEPARATE from local
mesh data - it's for regional awareness only.

Decrypts encrypted Meshtastic protobuf messages using the default
LongFast channel key.
"""

import base64
import logging
import threading
from datetime import datetime
from typing import Optional, Dict
from dataclasses import dataclass

import paho.mqtt.client as mqtt

# Meshtastic protobuf imports
from meshtastic import mesh_pb2, mqtt_pb2, portnums_pb2, telemetry_pb2

# Encryption
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

# Default LongFast encryption key (public, well-known)
DEFAULT_KEY = base64.b64decode('AQ==')  # 0x01


def decrypt_packet(encrypted_data: bytes, key: bytes, packet_id: int, from_id: int) -> Optional[bytes]:
    """Decrypt a Meshtastic packet using AES-CTR."""
    try:
        # Expand key to 32 bytes for AES-256
        expanded_key = (key * 32)[:32]
        
        # Build nonce from packet ID and sender ID
        nonce = bytes([
            packet_id >> 24 & 0xff, packet_id >> 16 & 0xff, 
            packet_id >> 8 & 0xff, packet_id & 0xff,
            0, 0, 0, 0,
            from_id >> 24 & 0xff, from_id >> 16 & 0xff,
            from_id >> 8 & 0xff, from_id & 0xff,
            0, 0, 0, 0
        ])
        
        cipher = Cipher(
            algorithms.AES(expanded_key),
            modes.CTR(nonce),
            backend=default_backend()
        )
        decryptor = cipher.decryptor()
        return decryptor.update(encrypted_data) + decryptor.finalize()
    except Exception as e:
        logger.debug(f"Decryption failed: {e}")
        return None


@dataclass
class RegionalNode:
    """A node discovered via regional MQTT."""
    node_id: str
    short_name: Optional[str] = None
    long_name: Optional[str] = None
    hardware: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[int] = None
    battery_level: Optional[int] = None
    last_rssi: Optional[int] = None
    last_snr: Optional[float] = None
    last_heard: Optional[datetime] = None
    message_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            'node_id': self.node_id,
            'short_name': self.short_name,
            'long_name': self.long_name,
            'hardware': self.hardware,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'altitude': self.altitude,
            'battery_level': self.battery_level,
            'last_rssi': self.last_rssi,
            'last_snr': self.last_snr,
            'last_heard': self.last_heard.isoformat() if self.last_heard else None,
            'message_count': self.message_count,
            'source': 'mqtt_regional',
        }


@dataclass
class RegionalMessage:
    """A message from the regional MQTT feed."""
    sender: str
    sender_name: Optional[str] = None
    text: str = ""
    channel: str = ""
    timestamp: Optional[datetime] = None
    rssi: Optional[int] = None
    snr: Optional[float] = None
    
    def to_dict(self) -> dict:
        return {
            'sender': self.sender,
            'sender_name': self.sender_name,
            'text': self.text,
            'channel': self.channel,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'rssi': self.rssi,
            'snr': self.snr,
            'source': 'mqtt_regional',
        }


class MQTTRegionalService:
    """Service to collect regional mesh data via MQTT.
    
    Connects to the Ohio Meshtastic MQTT broker and decrypts
    encrypted protobuf messages to collect node information
    from the regional network.
    """
    
    def __init__(
        self,
        broker: str = "mqtt.neomesh.org",
        port: int = 1883,
        username: str = "neomesh",
        password: str = "meshneo",
        topic: str = "msh/US/OH/#",
    ):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self.topic = topic
        
        self._client: Optional[mqtt.Client] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        
        # Regional node storage (in-memory, separate from local)
        self._nodes: Dict[str, RegionalNode] = {}
        self._messages: list[RegionalMessage] = []
        self._max_messages = 100
        self._lock = threading.Lock()
        
        # Stats
        self._connected = False
        self._messages_received = 0
        self._decoded_ok = 0
        self._decoded_fail = 0
        self._text_messages_received = 0
        self._last_message_time: Optional[datetime] = None
        self._connection_time: Optional[datetime] = None
    
    def start(self) -> bool:
        """Start the MQTT service in background thread."""
        if self._running:
            logger.warning("MQTT Regional service already running")
            return True
        
        try:
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            self._client.username_pw_set(self.username, self.password)
            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message
            
            logger.info(f"Connecting to regional MQTT: {self.broker}:{self.port}")
            self._client.connect(self.broker, self.port, 60)
            
            self._running = True
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            
            logger.info("MQTT Regional service started")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start MQTT Regional service: {e}")
            return False
    
    def stop(self):
        """Stop the MQTT service."""
        self._running = False
        if self._client:
            self._client.disconnect()
            self._client = None
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("MQTT Regional service stopped")
    
    def _run_loop(self):
        """Background thread for MQTT client loop."""
        while self._running and self._client:
            try:
                self._client.loop(timeout=1.0)
            except Exception as e:
                logger.error(f"MQTT loop error: {e}")
                break
    
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT connection."""
        if reason_code == 0 or str(reason_code) == "Success":
            self._connected = True
            self._connection_time = datetime.now()
            logger.info(f"Connected to regional MQTT broker")
            # Subscribe to all Ohio traffic (encrypted protobuf)
            client.subscribe(self.topic)
            logger.info(f"Subscribed to {self.topic}")
        else:
            logger.error(f"MQTT connection failed: {reason_code}")
    
    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        """Handle MQTT disconnection."""
        self._connected = False
        logger.warning(f"Disconnected from regional MQTT: {reason_code}")
        
        if self._running:
            try:
                logger.info("Attempting MQTT reconnect...")
                client.reconnect()
            except Exception as e:
                logger.error(f"MQTT reconnect failed: {e}")
    
    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT message (encrypted or pre-decoded)."""
        try:
            self._messages_received += 1
            self._last_message_time = datetime.now()
            
            # Parse the ServiceEnvelope protobuf
            env = mqtt_pb2.ServiceEnvelope()
            env.ParseFromString(msg.payload)
            
            mp = env.packet
            from_id = getattr(mp, 'from')
            sender = f'!{from_id:08x}'
            
            data = None
            
            # Check if already decoded (some MQTT servers send pre-decoded)
            if mp.HasField('decoded'):
                data = mp.decoded
                self._decoded_ok += 1
            # Otherwise try to decrypt
            elif len(mp.encrypted) > 0:
                decrypted = decrypt_packet(mp.encrypted, DEFAULT_KEY, mp.id, from_id)
                if decrypted:
                    try:
                        data = mesh_pb2.Data()
                        data.ParseFromString(decrypted)
                        self._decoded_ok += 1
                    except:
                        self._decoded_fail += 1
                        return
                else:
                    self._decoded_fail += 1
                    return
            else:
                # No data to process
                return
            
            if not data:
                return
            
            # Update or create node
            with self._lock:
                if sender not in self._nodes:
                    self._nodes[sender] = RegionalNode(node_id=sender)
                
                node = self._nodes[sender]
                node.last_heard = datetime.now()
                node.message_count += 1
                
                # Update signal info from packet
                if mp.rx_rssi:
                    node.last_rssi = mp.rx_rssi
                if mp.rx_snr:
                    node.last_snr = mp.rx_snr
                
                # Process based on portnum
                portnum = data.portnum
                
                if portnum == portnums_pb2.TEXT_MESSAGE_APP:
                    text = data.payload.decode('utf-8', errors='replace')
                    
                    message = RegionalMessage(
                        sender=sender,
                        sender_name=node.short_name,
                        text=text,
                        channel=env.channel_id or "LongFast",
                        timestamp=datetime.now(),
                        rssi=mp.rx_rssi if mp.rx_rssi else None,
                        snr=mp.rx_snr if mp.rx_snr else None,
                    )
                    
                    self._messages.append(message)
                    self._text_messages_received += 1
                    
                    if len(self._messages) > self._max_messages:
                        self._messages = self._messages[-self._max_messages:]
                    
                    logger.info(f"💬 Regional [{node.short_name or sender}]: {text[:50]}...")
                
                elif portnum == portnums_pb2.NODEINFO_APP:
                    try:
                        info = mesh_pb2.User()
                        info.ParseFromString(data.payload)
                        # The User message contains info about a specific node (may differ from sender)
                        info_node_id = f"!{info.id}" if info.id and not info.id.startswith('!') else info.id
                        if info_node_id:
                            if info_node_id not in self._nodes:
                                self._nodes[info_node_id] = RegionalNode(node_id=info_node_id)
                            target = self._nodes[info_node_id]
                            target.short_name = info.short_name or target.short_name
                            target.long_name = info.long_name or target.long_name
                            target.hardware = info.hw_model_str if hasattr(info, 'hw_model_str') else None
                            target.last_heard = datetime.now()
                            logger.info(f"👤 Regional node: {info.short_name} \"{info.long_name}\" ({info_node_id})")
                        else:
                            # Fallback to sender if no id in info
                            node.short_name = info.short_name or node.short_name
                            node.long_name = info.long_name or node.long_name
                            logger.info(f"👤 Regional node: {info.short_name} \"{info.long_name}\"")
                    except Exception as e:
                        logger.debug(f"Failed to parse NODEINFO: {e}")
                
                elif portnum == portnums_pb2.POSITION_APP:
                    try:
                        pos = mesh_pb2.Position()
                        pos.ParseFromString(data.payload)
                        if pos.latitude_i:
                            node.latitude = pos.latitude_i / 1e7
                            node.longitude = pos.longitude_i / 1e7
                        if pos.altitude:
                            node.altitude = pos.altitude
                    except:
                        pass
                
                elif portnum == portnums_pb2.TELEMETRY_APP:
                    try:
                        tel = telemetry_pb2.Telemetry()
                        tel.ParseFromString(data.payload)
                        if tel.HasField('device_metrics'):
                            if tel.device_metrics.battery_level:
                                node.battery_level = tel.device_metrics.battery_level
                    except:
                        pass
            
        except Exception as e:
            logger.debug(f"Error processing MQTT message: {e}")
    
    def get_nodes(self) -> list[dict]:
        """Get all regional nodes as list of dicts."""
        with self._lock:
            nodes = [n.to_dict() for n in self._nodes.values()]
        nodes.sort(key=lambda x: x.get('last_heard') or '', reverse=True)
        return nodes
    
    def get_node(self, node_id: str) -> Optional[dict]:
        """Get a specific regional node."""
        with self._lock:
            node = self._nodes.get(node_id)
            return node.to_dict() if node else None
    
    def get_messages(self, limit: int = 50) -> list[dict]:
        """Get recent regional text messages."""
        with self._lock:
            messages = [m.to_dict() for m in reversed(self._messages[-limit:])]
        return messages
    
    def get_stats(self) -> dict:
        """Get service statistics."""
        with self._lock:
            node_count = len(self._nodes)
            msg_count = len(self._messages)
        
        return {
            'connected': self._connected,
            'broker': self.broker,
            'topic': self.topic,
            'node_count': node_count,
            'messages_received': self._messages_received,
            'decoded_ok': self._decoded_ok,
            'decoded_fail': self._decoded_fail,
            'text_messages': self._text_messages_received,
            'cached_messages': msg_count,
            'last_message': self._last_message_time.isoformat() if self._last_message_time else None,
            'connected_since': self._connection_time.isoformat() if self._connection_time else None,
        }
    
    def is_connected(self) -> bool:
        """Check if connected to MQTT broker."""
        return self._connected


# Global instance
mqtt_regional: Optional[MQTTRegionalService] = None


def init_mqtt_regional(config: dict) -> Optional[MQTTRegionalService]:
    """Initialize the MQTT regional service from config."""
    global mqtt_regional
    
    mqtt_config = config.get('mqtt', {})
    if not mqtt_config.get('enabled', False):
        logger.info("MQTT Regional service disabled in config")
        return None
    
    # Use the encrypted topic format (not /json/)
    topic = mqtt_config.get('topic', 'msh/US/OH/#')
    # Ensure we're subscribing to all traffic, not just JSON
    if '/json/' in topic:
        topic = topic.replace('/json/', '/')
    
    mqtt_regional = MQTTRegionalService(
        broker=mqtt_config.get('broker', 'mqtt.neomesh.org'),
        port=mqtt_config.get('port', 1883),
        username=mqtt_config.get('username', 'neomesh'),
        password=mqtt_config.get('password', 'meshneo'),
        topic=topic,
    )
    
    if mqtt_regional.start():
        return mqtt_regional
    else:
        mqtt_regional = None
        return None
