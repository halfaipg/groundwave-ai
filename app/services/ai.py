"""AI service for LLM integration.

Supports LM Studio, Ollama, and OpenAI-compatible endpoints.
Includes LLM-based intent classification for knowledge lookup.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Optional
import logging
import httpx

from ..config import config

logger = logging.getLogger(__name__)

# Intent classification prompt
CLASSIFY_PROMPT = """Determine if this message requires factual knowledge lookup.

Answer ONLY "yes" or "no".

YES for: factual questions, "what is", "how does", "who was", definitions, explanations, history, science, geography, how-to, survival tips, technical info
NO for: greetings, casual chat, personal questions, commands (!weather, !bbs), opinions, jokes, small talk

Message: {message}

Needs lookup:"""


# Cache for live context (weather, etc.)
_context_cache: dict = {
    "data": None,
    "expires": None
}
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes


async def get_live_context() -> str:
    """Fetch live data to inject into AI context (cached 15 min)."""
    from .weather import weather_service
    
    now = datetime.now()
    
    # Check cache
    if _context_cache["data"] and _context_cache["expires"] and now < _context_cache["expires"]:
        # Update just the time, keep cached weather
        lines = [f"Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}"]
        lines.append(_context_cache["data"])
        return "\n".join(lines)
    
    # Fetch fresh data
    from ..config import config
    lines = []
    lines.append(f"Location: {config.web.location_name}")
    
    # Current weather
    try:
        weather = await weather_service.get_current_weather()
        lines.append(f"Current weather: {weather}")
    except Exception as e:
        logger.debug(f"Could not fetch weather for context: {e}")
    
    # Forecast
    try:
        forecast = await weather_service.get_forecast(days=3)
        lines.append(f"3-day forecast:\n{forecast}")
    except Exception as e:
        logger.debug(f"Could not fetch forecast for context: {e}")
    
    # Cache it
    cached_data = "\n".join(lines)
    _context_cache["data"] = cached_data
    _context_cache["expires"] = now + timedelta(seconds=CACHE_TTL_SECONDS)
    logger.info("Weather context cached for 15 minutes")
    
    # Return with current time
    return f"Current date/time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}\n{cached_data}"


class AIService:
    """AI service for generating responses using LLM.
    
    Features:
    - LLM-based intent classification
    - Wikipedia RAG for factual questions
    - Conversation history per user
    """
    
    def __init__(self):
        self.provider = config.llm.provider
        self.system_prompt = config.llm.system_prompt
        self._client = httpx.AsyncClient(timeout=60.0)
        
        # Conversation history per user (simple memory)
        self._history: dict[str, list[dict]] = {}
        self._max_history = 10
        
        # Kiwix knowledge service (lazy loaded)
        self._kiwix = None
        self._kiwix_checked = False
    
    async def _get_kiwix_service(self):
        """Get the Kiwix service, initializing if needed."""
        if not self._kiwix_checked:
            self._kiwix_checked = True
            try:
                if config.kiwix.enabled:
                    from .kiwix import KiwixService
                    self._kiwix = KiwixService(
                        url=config.kiwix.url,
                        library=config.kiwix.library,
                        ai_enhanced=config.kiwix.ai_enhanced,
                    )
                    if await self._kiwix.initialize():
                        # Give Kiwix access to AI for enhanced mode
                        self._kiwix.set_ai_service(self)
                        logger.info(f"Kiwix loaded (AI-enhanced: {config.kiwix.ai_enhanced})")
                    else:
                        self._kiwix = None
            except Exception as e:
                logger.debug(f"Kiwix not available: {e}")
        return self._kiwix
    
    async def quick_complete(self, prompt: str, max_tokens: int = 50) -> str:
        """Quick LLM completion for short tasks like classification.
        
        Uses minimal tokens for fast response.
        """
        messages = [{"role": "user", "content": prompt}]
        
        if self.provider == "lmstudio":
            url = config.llm.lmstudio_url
            payload = {
                "model": config.llm.lmstudio_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.1,  # Low temp for classification
            }
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        else:
            # Fallback to regular call
            return await self._call_lmstudio(messages)
    
    async def needs_knowledge_lookup(self, message: str) -> bool:
        """Use LLM to classify if message needs knowledge lookup."""
        try:
            prompt = CLASSIFY_PROMPT.format(message=message)
            response = await self.quick_complete(prompt, max_tokens=5)
            return response.strip().lower().startswith("yes")
        except Exception as e:
            logger.debug(f"Classification failed: {e}")
            return False  # Default to fast path
    
    async def generate_response(
        self, 
        message: str, 
        user_id: str,
        user_name: Optional[str] = None,
        include_history: bool = True,
        use_knowledge: bool = True
    ) -> str:
        """Generate an AI response to a message.
        
        Args:
            message: The user's message
            user_id: The user's node ID (for conversation history)
            user_name: The user's name (optional, for context)
            include_history: Whether to include conversation history
            use_knowledge: Whether to check knowledge base for factual queries
            
        Returns:
            The AI's response text
        """
        try:
            # Build messages
            messages = [{"role": "system", "content": self.system_prompt}]
            
            # Check if we need knowledge lookup (LLM classifier + Kiwix)
            knowledge_context = None
            if use_knowledge:
                kiwix = await self._get_kiwix_service()
                if kiwix and kiwix.is_ready():
                    needs_lookup = await self.needs_knowledge_lookup(message)
                    if needs_lookup:
                        logger.info(f"Kiwix lookup triggered for: {message[:50]}...")
                        # Use AI-enhanced or vanilla based on config
                        wiki_response = await kiwix.query(message)
                        if wiki_response and "No results" not in wiki_response:
                            knowledge_context = f"Wikipedia information:\n{wiki_response}"
                            logger.info("Kiwix context added to response")
            
            # Inject knowledge context if available
            if knowledge_context:
                messages.append({
                    "role": "system",
                    "content": knowledge_context
                })
            
            # Inject live data (weather, time, etc.) into context
            try:
                live_context = await get_live_context()
                messages.append({
                    "role": "system", 
                    "content": f"LIVE DATA (use this to answer questions):\n{live_context}"
                })
            except Exception as e:
                logger.debug(f"Could not get live context: {e}")
            
            # Add user context if available
            if user_name:
                messages.append({
                    "role": "system",
                    "content": f"You are talking to {user_name} (node ID: {user_id})"
                })
            
            # Add conversation history
            if include_history and user_id in self._history:
                messages.extend(self._history[user_id][-self._max_history:])
            
            # Add current message
            messages.append({"role": "user", "content": message})
            
            # Generate response based on provider
            if self.provider == "lmstudio":
                response = await self._call_lmstudio(messages)
            elif self.provider == "ollama":
                response = await self._call_ollama(messages)
            elif self.provider == "openai":
                response = await self._call_openai(messages)
            else:
                logger.error(f"Unknown AI provider: {self.provider}")
                return "⚠️ AI provider not configured"
            
            # Update history
            if user_id not in self._history:
                self._history[user_id] = []
            self._history[user_id].append({"role": "user", "content": message})
            self._history[user_id].append({"role": "assistant", "content": response})
            
            # Trim history
            if len(self._history[user_id]) > self._max_history * 2:
                self._history[user_id] = self._history[user_id][-self._max_history * 2:]
            
            return response
            
        except Exception as e:
            logger.error(f"AI generation error: {e}")
            return f"⚠️ AI error: {str(e)[:50]}"
    
    async def _call_lmstudio(self, messages: list[dict]) -> str:
        """Call LM Studio API (OpenAI-compatible)."""
        url = config.llm.lmstudio_url
        
        payload = {
            "model": config.llm.lmstudio_model,
            "messages": messages,
            "max_tokens": 150,  # Chunked if over 200 chars
            "temperature": 0.7,
        }
        
        logger.debug(f"Calling LM Studio: {url}")
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    
    async def _call_ollama(self, messages: list[dict]) -> str:
        """Call Ollama API."""
        url = config.llm.ollama_url
        
        # Convert messages to Ollama format
        prompt = ""
        for msg in messages:
            role = msg["role"]
            content = msg["content"]
            if role == "system":
                prompt += f"System: {content}\n"
            elif role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        prompt += "Assistant: "
        
        payload = {
            "model": config.llm.ollama_model,
            "prompt": prompt,
            "stream": False,
        }
        
        logger.debug(f"Calling Ollama: {url}")
        response = await self._client.post(url, json=payload)
        response.raise_for_status()
        
        data = response.json()
        return data.get("response", "").strip()
    
    async def _call_openai(self, messages: list[dict]) -> str:
        """Call OpenAI-compatible API."""
        # For now, use LM Studio endpoint (it's OpenAI-compatible)
        return await self._call_lmstudio(messages)
    
    def clear_history(self, user_id: str) -> None:
        """Clear conversation history for a user."""
        if user_id in self._history:
            del self._history[user_id]
    
    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# Global AI service instance
ai_service = AIService()
