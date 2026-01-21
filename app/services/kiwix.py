"""Kiwix Knowledge Service - Offline Wikipedia with optional AI enhancement.

This module provides access to offline Wikipedia through a local Kiwix server.
Supports two modes:
  - Vanilla: Direct Kiwix search, returns raw article text
  - AI-Enhanced: LLM query rewriting + multi-term search + synthesis

Configuration in config.yaml:
  kiwix:
    enabled: true
    url: "http://localhost:8080"
    library: "wikipedia_en_all_nopic"
    ai_enhanced: true  # Enable LLM-powered search & synthesis
"""

import logging
from typing import Optional, List
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup
from urllib.parse import quote

logger = logging.getLogger(__name__)


@dataclass
class KiwixResult:
    """A search result from Kiwix."""
    title: str
    content: str
    url: str
    
    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content,
            "url": self.url,
        }


class KiwixService:
    """Offline Wikipedia access via local Kiwix server.
    
    Works in two modes:
    - Vanilla: Direct search, returns article text
    - AI-Enhanced: Smart query rewriting and answer synthesis
    """
    
    def __init__(
        self,
        url: str = "http://localhost:8080",
        library: str = "wikipedia_en_all_nopic",
        ai_enhanced: bool = False,
        timeout: float = 10.0,
    ):
        self.url = url.rstrip("/")
        self.library = library
        self.ai_enhanced = ai_enhanced
        self._client = httpx.AsyncClient(timeout=timeout)
        self._connected = False
        self._ai_service = None
    
    async def initialize(self) -> bool:
        """Check if Kiwix server is available."""
        try:
            response = await self._client.get(f"{self.url}/")
            self._connected = response.status_code == 200
            if self._connected:
                logger.info(f"Kiwix connected: {self.url}")
            return self._connected
        except Exception as e:
            logger.warning(f"Kiwix not available: {e}")
            self._connected = False
            return False
    
    def is_ready(self) -> bool:
        return self._connected
    
    def set_ai_service(self, ai_service):
        """Inject AI service for enhanced mode."""
        self._ai_service = ai_service
    
    # =========================================================================
    # VANILLA KIWIX - Direct search, no AI
    # =========================================================================
    
    async def search_raw(self, query: str, limit: int = 3) -> List[KiwixResult]:
        """Search Kiwix directly, return raw results.
        
        This is the vanilla mode - no AI involved.
        """
        if not self._connected:
            return []
        
        try:
            search_url = f"{self.url}/search?content={self.library}&pattern={quote(query)}"
            response = await self._client.get(search_url)
            
            if response.status_code != 200:
                return []
            
            if "No results were found" in response.text:
                return []
            
            # Parse search results
            soup = BeautifulSoup(response.text, 'html.parser')
            results = soup.select('div.results ul li')[:limit]
            
            kiwix_results = []
            for li in results:
                a = li.find('a', href=True)
                if not a:
                    continue
                
                article_url = f"{self.url}{a['href']}"
                title = a.get_text(strip=True)
                
                # Fetch article content
                content = await self._fetch_article(article_url)
                if content:
                    kiwix_results.append(KiwixResult(
                        title=title,
                        content=content,
                        url=article_url,
                    ))
            
            return kiwix_results
            
        except Exception as e:
            logger.error(f"Kiwix search error: {e}")
            return []
    
    async def _fetch_article(self, url: str, max_chars: int = 2000) -> Optional[str]:
        """Fetch and extract text from a Kiwix article."""
        try:
            response = await self._client.get(url)
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find main content
            main = soup.find('div', class_='mw-parser-output')
            if not main:
                main = soup.body
            if not main:
                return None
            
            # Extract text, skip non-content elements
            skip_tags = ['style', 'script', 'head', 'title', 'meta', 'nav', 'footer']
            texts = []
            for element in main.find_all(string=True):
                if element.parent.name not in skip_tags:
                    text = element.strip()
                    if text:
                        texts.append(text)
            
            content = " ".join(texts)
            return content[:max_chars] if len(content) > max_chars else content
            
        except Exception as e:
            logger.debug(f"Error fetching article: {e}")
            return None
    
    async def get_summary(self, query: str) -> str:
        """Get a summary for a query - vanilla mode.
        
        Returns raw article text, no AI processing.
        """
        results = await self.search_raw(query, limit=1)
        if results:
            return f"**{results[0].title}**\n\n{results[0].content}"
        return f"No results found for '{query}'"
    
    # =========================================================================
    # AI-ENHANCED KIWIX - Smart search with LLM
    # =========================================================================
    
    async def search_smart(self, question: str) -> str:
        """AI-enhanced search: query rewriting + multi-search + synthesis.
        
        Requires ai_enhanced=True and AI service to be set.
        Falls back to vanilla if AI not available.
        """
        if not self.ai_enhanced or not self._ai_service:
            # Fallback to vanilla
            return await self.get_summary(question)
        
        try:
            # Step 1: LLM extracts optimal search terms
            search_terms = await self._extract_search_terms(question)
            logger.info(f"AI extracted search terms: {search_terms}")
            
            # Step 2: Search Kiwix with each term
            all_context = []
            seen_titles = set()
            
            for term in search_terms[:3]:  # Max 3 searches
                results = await self.search_raw(term, limit=2)
                for r in results:
                    if r.title not in seen_titles:
                        seen_titles.add(r.title)
                        # Truncate for context
                        snippet = r.content[:1000] if len(r.content) > 1000 else r.content
                        all_context.append(f"[{r.title}]: {snippet}")
            
            if not all_context:
                return f"No information found for '{question}'"
            
            # Step 3: LLM synthesizes answer from context
            context_text = "\n\n".join(all_context)
            answer = await self._synthesize_answer(question, context_text)
            
            return answer
            
        except Exception as e:
            logger.error(f"AI-enhanced search failed: {e}")
            # Fallback to vanilla
            return await self.get_summary(question)
    
    async def _extract_search_terms(self, question: str) -> List[str]:
        """Use LLM to extract optimal Wikipedia search terms."""
        prompt = """Extract 2-3 Wikipedia search terms from this question.
Return ONLY the search terms, one per line, nothing else.

Question: {question}

Search terms:"""
        
        try:
            response = await self._ai_service.quick_complete(
                prompt.format(question=question),
                max_tokens=50
            )
            
            # Parse response into list
            terms = [t.strip().strip('"\'-.') for t in response.strip().split('\n')]
            terms = [t for t in terms if t and len(t) > 2]
            
            if not terms:
                # Fallback: use question as search term
                return [question[:50]]
            
            return terms[:3]
            
        except Exception as e:
            logger.debug(f"Term extraction failed: {e}")
            return [question[:50]]
    
    async def _synthesize_answer(self, question: str, context: str) -> str:
        """Use LLM to synthesize an answer from Kiwix context."""
        prompt = """Using ONLY the information below, answer the question concisely.
If the information doesn't contain the answer, say so.
Keep response under 400 characters for mesh transmission.

INFORMATION:
{context}

QUESTION: {question}

ANSWER:"""
        
        try:
            response = await self._ai_service.quick_complete(
                prompt.format(context=context, question=question),
                max_tokens=150
            )
            return response.strip()
            
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            # Return raw context as fallback
            return context[:500]
    
    # =========================================================================
    # PUBLIC API - Auto-selects mode based on config
    # =========================================================================
    
    async def query(self, question: str) -> str:
        """Query Kiwix - automatically uses AI mode if enabled.
        
        This is the main entry point. It will:
        - Use AI-enhanced search if ai_enhanced=True and AI service available
        - Fall back to vanilla Kiwix otherwise
        """
        if self.ai_enhanced and self._ai_service:
            return await self.search_smart(question)
        else:
            return await self.get_summary(question)
    
    async def close(self):
        """Close HTTP client."""
        await self._client.aclose()
    
    def get_stats(self) -> dict:
        """Get service stats."""
        return {
            "connected": self._connected,
            "url": self.url,
            "library": self.library,
            "ai_enhanced": self.ai_enhanced,
            "ai_available": self._ai_service is not None,
        }


# Singleton instance (initialized in main.py)
kiwix_service: Optional[KiwixService] = None
