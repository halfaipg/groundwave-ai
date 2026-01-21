"""Protocol abstraction layer for mesh networks.

groundwave-ai supports multiple mesh protocols through a unified interface:
- Meshtastic: Fully supported (legacy)
- MeshCore: In development (recommended for new deployments)
"""

from .base import MeshInterface, MeshMessage, MeshNode, ConnectionState
from .meshtastic_interface import MeshtasticInterface
from .meshcore_interface import MeshCoreInterface

__all__ = [
    "MeshInterface",
    "MeshMessage", 
    "MeshNode",
    "ConnectionState",
    "MeshtasticInterface",
    "MeshCoreInterface"
]
