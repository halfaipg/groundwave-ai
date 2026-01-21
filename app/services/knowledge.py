"""Local Knowledge Base Service - Offline Wikipedia RAG.

This service provides offline access to Wikipedia through:
1. BM25 (keyword) search for fast candidate retrieval
2. Vector embeddings for semantic re-ranking
3. LLM-based intent classification to decide when to search

The system is designed to work 100% offline once set up.
"""

import asyncio
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Data paths
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "wikipedia"
DB_PATH = DATA_DIR / "wikipedia.db"
INDEX_PATH = DATA_DIR / "embeddings"


@dataclass
class SearchResult:
    """A search result from the knowledge base."""
    title: str
    content: str
    score: float
    source: str = "wikipedia"
    
    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
            "score": self.score,
            "source": self.source,
        }


class KnowledgeService:
    """Offline knowledge base with Wikipedia.
    
    Uses hybrid BM25 + vector search for fast, accurate retrieval.
    """
    
    def __init__(self):
        self._db: Optional[sqlite3.Connection] = None
        self._embedder = None
        self._vector_index = None
        self._initialized = False
        self._article_count = 0
        
    async def initialize(self) -> bool:
        """Initialize the knowledge base."""
        try:
            # Ensure data directory exists
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            
            # Check if we have the database
            if not DB_PATH.exists():
                logger.warning("Wikipedia database not found. Run the download script first.")
                logger.info(f"Expected path: {DB_PATH}")
                return False
            
            # Connect to SQLite with FTS5
            self._db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            self._db.row_factory = sqlite3.Row
            
            # Check article count
            cursor = self._db.execute("SELECT COUNT(*) FROM articles")
            self._article_count = cursor.fetchone()[0]
            logger.info(f"Knowledge base loaded: {self._article_count:,} articles")
            
            # Try to load vector index if available
            await self._load_vector_index()
            
            self._initialized = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize knowledge base: {e}")
            return False
    
    async def _load_vector_index(self):
        """Load the vector embedding index if available."""
        try:
            import chromadb
            from chromadb.config import Settings
            
            if INDEX_PATH.exists():
                self._vector_index = chromadb.PersistentClient(
                    path=str(INDEX_PATH),
                    settings=Settings(anonymized_telemetry=False)
                )
                logger.info("Vector index loaded")
        except ImportError:
            logger.info("ChromaDB not installed - using BM25 only")
        except Exception as e:
            logger.warning(f"Could not load vector index: {e}")
    
    def is_ready(self) -> bool:
        """Check if knowledge base is ready for queries."""
        return self._initialized and self._db is not None
    
    def get_stats(self) -> dict:
        """Get knowledge base statistics."""
        return {
            "initialized": self._initialized,
            "article_count": self._article_count,
            "has_vector_index": self._vector_index is not None,
            "db_path": str(DB_PATH),
        }
    
    async def search(
        self, 
        query: str, 
        limit: int = 3,
        use_vectors: bool = True
    ) -> List[SearchResult]:
        """Search the knowledge base.
        
        Uses hybrid BM25 + vector search for best results.
        """
        if not self.is_ready():
            return []
        
        results = []
        
        try:
            # BM25 search using SQLite FTS5
            cursor = self._db.execute("""
                SELECT title, content, bm25(articles_fts) as score
                FROM articles_fts
                WHERE articles_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, limit * 2))  # Get more for re-ranking
            
            for row in cursor.fetchall():
                results.append(SearchResult(
                    title=row["title"],
                    content=row["content"],
                    score=abs(row["score"]),  # BM25 returns negative scores
                ))
            
            # Vector re-ranking if available
            if use_vectors and self._vector_index and results:
                results = await self._rerank_with_vectors(query, results, limit)
            else:
                results = results[:limit]
            
            return results
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    async def _rerank_with_vectors(
        self, 
        query: str, 
        candidates: List[SearchResult],
        limit: int
    ) -> List[SearchResult]:
        """Re-rank BM25 results using vector similarity."""
        try:
            collection = self._vector_index.get_collection("wikipedia")
            
            # Get embeddings for query
            query_results = collection.query(
                query_texts=[query],
                n_results=limit,
                where={"title": {"$in": [r.title for r in candidates]}}
            )
            
            # Merge scores
            vector_scores = {
                title: 1 - dist  # Convert distance to similarity
                for title, dist in zip(
                    query_results["ids"][0],
                    query_results["distances"][0]
                )
            }
            
            # Combine BM25 and vector scores
            for result in candidates:
                vector_score = vector_scores.get(result.title, 0)
                # Weighted combination: 40% BM25, 60% vector
                result.score = 0.4 * result.score + 0.6 * vector_score
            
            # Re-sort by combined score
            candidates.sort(key=lambda x: x.score, reverse=True)
            return candidates[:limit]
            
        except Exception as e:
            logger.warning(f"Vector re-ranking failed: {e}")
            return candidates[:limit]
    
    async def get_article(self, title: str) -> Optional[str]:
        """Get full article content by title."""
        if not self.is_ready():
            return None
        
        try:
            cursor = self._db.execute(
                "SELECT content FROM articles WHERE title = ?",
                (title,)
            )
            row = cursor.fetchone()
            return row["content"] if row else None
        except Exception as e:
            logger.error(f"Error getting article: {e}")
            return None
    
    def close(self):
        """Close database connections."""
        if self._db:
            self._db.close()
            self._db = None


# Singleton instance
knowledge_service = KnowledgeService()


# Intent classifier prompts
CLASSIFY_PROMPT = """Determine if this user message requires looking up factual information from a knowledge base like Wikipedia.

Answer ONLY "yes" or "no".

- Answer "yes" for: factual questions, "what is", "how does", "who was", historical events, science, geography, definitions, explanations, how-to guides, technical questions
- Answer "no" for: casual chat, greetings, personal questions, commands, weather requests, BBS operations, opinions, jokes

User message: {message}

Needs knowledge lookup:"""


async def needs_knowledge_lookup(ai_service, message: str) -> bool:
    """Use the LLM to classify if a message needs knowledge lookup.
    
    Args:
        ai_service: The AI service instance for LLM calls
        message: The user's message
        
    Returns:
        True if the message needs factual knowledge lookup
    """
    try:
        # Quick classification call
        prompt = CLASSIFY_PROMPT.format(message=message)
        response = await ai_service.quick_complete(prompt, max_tokens=5)
        
        answer = response.strip().lower()
        return answer.startswith("yes")
        
    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        # Default to no lookup on error (faster path)
        return False


def format_context_for_llm(results: List[SearchResult]) -> str:
    """Format search results as context for the LLM."""
    if not results:
        return ""
    
    context_parts = ["Relevant information from Wikipedia:\n"]
    
    for i, result in enumerate(results, 1):
        # Truncate content to keep context manageable
        content = result.content[:1500] if len(result.content) > 1500 else result.content
        context_parts.append(f"[{i}] {result.title}:\n{content}\n")
    
    context_parts.append("\nUse this information to answer the user's question accurately and concisely.")
    
    return "\n".join(context_parts)
