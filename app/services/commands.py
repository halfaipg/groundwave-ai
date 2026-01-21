"""Command router for handling mesh messages.

Routes incoming messages to appropriate handlers (AI, BBS, weather, etc.)
and formats responses for the mesh network.
"""

import re
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass
import logging

from ..interfaces.base import MeshMessage, MeshInterface
from ..config import config
from .ai import ai_service
from .bbs import bbs_service
from .weather import weather_service

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    """Result of command processing."""
    response: Optional[str] = None
    should_respond: bool = True
    is_private: bool = False


class CommandRouter:
    """Routes and handles mesh commands."""
    
    def __init__(self, mesh_interface: MeshInterface):
        self.mesh = mesh_interface
        self.prefix = config.safety.command_prefix
        self.bot_prefix = config.safety.ai_message_prefix
        
        # Command handlers
        self._commands: dict[str, Callable] = {
            "help": self._cmd_help,
            "ping": self._cmd_ping,
            "weather": self._cmd_weather,
            "wx": self._cmd_weather,
            "forecast": self._cmd_forecast,
            "bbs": self._cmd_bbs,
            "mail": self._cmd_mail,
            "post": self._cmd_post,
            "read": self._cmd_read,
            "nodes": self._cmd_nodes,
            "info": self._cmd_info,
            "ai": self._cmd_ai,
            "ask": self._cmd_ai,
            "clear": self._cmd_clear,
        }
    
    async def process_message(self, message: MeshMessage) -> Optional[str]:
        """Process an incoming message and return response if any.
        
        Args:
            message: The incoming mesh message
            
        Returns:
            Response text or None if no response needed
        """
        text = message.text.strip()
        from_id = message.from_id
        from_name = message.from_node.short_name if message.from_node else None
        
        # Ignore our own messages
        if config.safety.ignore_own_messages and from_id == self.mesh.my_node_id:
            return None
        
        # Ignore messages from other bots (bot prefix detection)
        if text.startswith(self.bot_prefix):
            logger.debug(f"Ignoring bot message from {from_id}")
            return None
        
        # Check for command prefix
        if self.prefix and text.startswith(self.prefix):
            # Parse command
            parts = text[len(self.prefix):].strip().split(maxsplit=1)
            if not parts:
                return None
            
            cmd = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""
            
            if cmd in self._commands:
                logger.info(f"Processing command: {cmd} from {from_id}")
                result = await self._commands[cmd](from_id, from_name, args, message)
                if result and result.response:
                    return f"{self.bot_prefix}{result.response}"
            else:
                # Unknown command - pass to AI
                return await self._handle_ai_message(from_id, from_name, text)
        
        # If it's a DM, always respond with AI
        elif message.is_direct:
            return await self._handle_ai_message(from_id, from_name, text)
        
        # For channel messages, only respond if mentioned or configured
        # For now, we'll just ignore non-command channel messages
        return None
    
    async def _handle_ai_message(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        text: str
    ) -> str:
        """Handle a message that should go to AI."""
        response = await ai_service.generate_response(
            message=text,
            user_id=from_id,
            user_name=from_name
        )
        return f"{self.bot_prefix}{response}"
    
    # Command handlers
    async def _cmd_help(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Show help information."""
        help_text = f"""📡 {config.web.community_name}
Commands:
{self.prefix}help - This help
{self.prefix}ping - Test connection
{self.prefix}weather - Current weather
{self.prefix}forecast - 3-day forecast
{self.prefix}bbs - Bulletin boards
{self.prefix}mail - Check your mail
{self.prefix}nodes - List nodes
{self.prefix}ai <msg> - Ask AI
DM me to chat!"""
        return CommandResult(response=help_text)
    
    async def _cmd_ping(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Respond to ping."""
        name = from_name or from_id[:8]
        snr = f"SNR:{message.snr}dB " if message.snr else ""
        rssi = f"RSSI:{message.rssi}dB" if message.rssi else ""
        return CommandResult(response=f"🏓 Pong {name}! {snr}{rssi}")
    
    async def _cmd_weather(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Get current weather."""
        # Try to use node's location if available
        lat, lon = None, None
        if message.from_node:
            lat = message.from_node.latitude
            lon = message.from_node.longitude
        
        weather = await weather_service.get_current_weather(lat, lon)
        return CommandResult(response=weather)
    
    async def _cmd_forecast(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Get weather forecast."""
        lat, lon = None, None
        if message.from_node:
            lat = message.from_node.latitude
            lon = message.from_node.longitude
        
        forecast = await weather_service.get_forecast(lat, lon)
        return CommandResult(response=forecast)
    
    async def _cmd_bbs(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Show BBS boards and recent posts."""
        posts = await bbs_service.get_board_posts("General", limit=5)
        
        if not posts:
            return CommandResult(response="📋 BBS - No posts yet.\n!post <msg> to add one")
        
        post_list = bbs_service.format_post_list(posts)
        return CommandResult(response=f"📋 BBS - General\n{post_list}\n\n!post <msg> to add")
    
    async def _cmd_mail(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Check user's mail."""
        count = await bbs_service.count_mail(from_id)
        
        if count == 0:
            return CommandResult(response="📬 No new mail")
        
        mail = await bbs_service.get_user_mail(from_id)
        mail_list = bbs_service.format_post_list(mail[:5])
        return CommandResult(response=f"📬 You have {count} message(s)\n{mail_list}")
    
    async def _cmd_post(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Post to BBS."""
        if not args:
            return CommandResult(response="Usage: !post <message>")
        
        # Check for mail format: !post @nodeid message
        mail_match = re.match(r'^@?([!\w]+)\s+(.+)$', args)
        if mail_match:
            to_id = mail_match.group(1)
            if not to_id.startswith("!"):
                to_id = f"!{to_id}"
            content = mail_match.group(2)
            
            await bbs_service.send_mail(
                from_id=from_id,
                to_id=to_id,
                content=content,
                from_name=from_name
            )
            return CommandResult(response=f"📧 Mail sent to {to_id[:8]}")
        
        # Regular post
        await bbs_service.post_message(
            board="General",
            from_id=from_id,
            content=args,
            from_name=from_name
        )
        return CommandResult(response="✅ Posted to BBS")
    
    async def _cmd_read(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Read a specific post."""
        if not args:
            return CommandResult(response="Usage: !read <post#>")
        
        try:
            post_num = int(args) - 1
            posts = await bbs_service.get_all_posts(limit=50)
            if 0 <= post_num < len(posts):
                post = posts[post_num]
                await bbs_service.mark_read(post.id)
                return CommandResult(response=bbs_service.format_single_post(post))
            else:
                return CommandResult(response="❌ Post not found")
        except ValueError:
            return CommandResult(response="Usage: !read <post#>")
    
    async def _cmd_nodes(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """List known nodes."""
        nodes = self.mesh.get_all_nodes()
        
        if not nodes:
            return CommandResult(response="📡 No nodes discovered yet")
        
        # Sort by last heard
        nodes.sort(key=lambda n: n.last_heard or 0, reverse=True)
        
        lines = [f"📡 {len(nodes)} nodes:"]
        for node in nodes[:10]:
            name = node.short_name or node.node_id[:8]
            online = "🟢" if node.is_online else "⚪"
            lines.append(f"{online} {name}")
        
        if len(nodes) > 10:
            lines.append(f"...and {len(nodes) - 10} more")
        
        return CommandResult(response="\n".join(lines))
    
    async def _cmd_info(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Show community info."""
        return CommandResult(response=f"""📡 {config.web.community_name}
{config.web.community_description}

🌐 Web: meshbot.local:8000
Commands: !help
AI Chat: DM me!""")
    
    async def _cmd_ai(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Send message to AI."""
        if not args:
            return CommandResult(response="Usage: !ai <your question>")
        
        response = await ai_service.generate_response(
            message=args,
            user_id=from_id,
            user_name=from_name
        )
        return CommandResult(response=response)
    
    async def _cmd_clear(
        self, 
        from_id: str, 
        from_name: Optional[str], 
        args: str,
        message: MeshMessage
    ) -> CommandResult:
        """Clear AI conversation history."""
        ai_service.clear_history(from_id)
        return CommandResult(response="🧹 Conversation history cleared")
