#!/usr/bin/env python3
"""Download and process Wikipedia for offline RAG.

This script:
1. Downloads the Wikipedia dump from HuggingFace (pre-processed, faster)
2. Creates SQLite database with FTS5 full-text search
3. Optionally creates vector embeddings with ChromaDB

Usage:
    python scripts/setup_wikipedia.py [--full|--top500k|--top100k]
    
Options:
    --full      Full English Wikipedia (~6.7M articles, ~100GB processed)
    --top500k   Top 500k articles by pageviews (~20GB processed) [default]
    --top100k   Top 100k articles (~5GB processed)
    --embeddings  Also create vector embeddings (adds significant time/space)
"""

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "wikipedia"
DB_PATH = DATA_DIR / "wikipedia.db"


def setup_database():
    """Create SQLite database with FTS5 for full-text search."""
    logger.info(f"Setting up database at {DB_PATH}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    
    # Main articles table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY,
            title TEXT UNIQUE NOT NULL,
            content TEXT NOT NULL,
            categories TEXT,
            pageviews INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # FTS5 virtual table for fast full-text search
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
            title,
            content,
            content='articles',
            content_rowid='id',
            tokenize='porter unicode61'
        )
    """)
    
    # Triggers to keep FTS in sync
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
            INSERT INTO articles_fts(rowid, title, content) 
            VALUES (new.id, new.title, new.content);
        END
    """)
    
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, content) 
            VALUES('delete', old.id, old.title, old.content);
        END
    """)
    
    cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
            INSERT INTO articles_fts(articles_fts, rowid, title, content) 
            VALUES('delete', old.id, old.title, old.content);
            INSERT INTO articles_fts(rowid, title, content) 
            VALUES (new.id, new.title, new.content);
        END
    """)
    
    # Metadata table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.commit()
    return conn


def download_wikipedia(conn: sqlite3.Connection, variant: str = "top500k"):
    """Download Wikipedia from HuggingFace datasets."""
    try:
        from datasets import load_dataset
        from tqdm import tqdm
    except ImportError:
        logger.error("Please install required packages: pip install datasets tqdm")
        sys.exit(1)
    
    logger.info(f"Downloading Wikipedia ({variant})...")
    logger.info("This may take a while depending on your connection...")
    
    # Different dataset configs
    if variant == "full":
        # Full Wikipedia - uses streaming to handle size
        dataset = load_dataset(
            "wikipedia", 
            "20220301.en",
            split="train",
            trust_remote_code=True
        )
        total = 6700000  # Approximate
    elif variant == "top500k":
        # Use a curated subset - Cohere's wikipedia-22-12
        dataset = load_dataset(
            "Cohere/wikipedia-22-12-en-embeddings",
            split="train",
            trust_remote_code=True
        )
        total = 500000
    else:  # top100k
        # Smaller subset
        dataset = load_dataset(
            "Cohere/wikipedia-22-12-en-embeddings",
            split="train[:100000]",
            trust_remote_code=True
        )
        total = 100000
    
    cursor = conn.cursor()
    batch = []
    batch_size = 1000
    processed = 0
    
    logger.info(f"Processing ~{total:,} articles...")
    
    for item in tqdm(dataset, total=total, desc="Importing"):
        # Handle different dataset formats
        if "title" in item and "text" in item:
            title = item["title"]
            content = item["text"]
        elif "title" in item and "content" in item:
            title = item["title"]
            content = item["content"]
        else:
            continue
        
        # Skip very short articles
        if len(content) < 100:
            continue
        
        batch.append((title, content))
        
        if len(batch) >= batch_size:
            cursor.executemany(
                "INSERT OR REPLACE INTO articles (title, content) VALUES (?, ?)",
                batch
            )
            conn.commit()
            processed += len(batch)
            batch = []
    
    # Final batch
    if batch:
        cursor.executemany(
            "INSERT OR REPLACE INTO articles (title, content) VALUES (?, ?)",
            batch
        )
        conn.commit()
        processed += len(batch)
    
    # Update metadata
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("last_updated", datetime.now().isoformat())
    )
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("variant", variant)
    )
    cursor.execute(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        ("article_count", str(processed))
    )
    conn.commit()
    
    logger.info(f"Imported {processed:,} articles")
    return processed


def create_embeddings(conn: sqlite3.Connection):
    """Create vector embeddings for semantic search."""
    try:
        import chromadb
        from chromadb.config import Settings
        from sentence_transformers import SentenceTransformer
        from tqdm import tqdm
    except ImportError:
        logger.error("Please install: pip install chromadb sentence-transformers")
        return False
    
    logger.info("Creating vector embeddings (this will take a while)...")
    
    # Use a small but effective model
    model = SentenceTransformer('all-MiniLM-L6-v2')
    
    # Setup ChromaDB
    index_path = DATA_DIR / "embeddings"
    index_path.mkdir(parents=True, exist_ok=True)
    
    client = chromadb.PersistentClient(
        path=str(index_path),
        settings=Settings(anonymized_telemetry=False)
    )
    
    # Create or get collection
    collection = client.get_or_create_collection(
        name="wikipedia",
        metadata={"hnsw:space": "cosine"}
    )
    
    # Get articles
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, content FROM articles")
    
    batch_size = 100
    batch_ids = []
    batch_docs = []
    batch_metas = []
    
    for row in tqdm(cursor.fetchall(), desc="Embedding"):
        article_id, title, content = row
        
        # Truncate content for embedding (first 1000 chars usually capture the essence)
        doc_text = f"{title}\n\n{content[:1000]}"
        
        batch_ids.append(str(article_id))
        batch_docs.append(doc_text)
        batch_metas.append({"title": title})
        
        if len(batch_ids) >= batch_size:
            collection.add(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas
            )
            batch_ids = []
            batch_docs = []
            batch_metas = []
    
    # Final batch
    if batch_ids:
        collection.add(
            ids=batch_ids,
            documents=batch_docs,
            metadatas=batch_metas
        )
    
    logger.info(f"Created embeddings for {collection.count()} articles")
    return True


def main():
    parser = argparse.ArgumentParser(description="Setup Wikipedia for offline RAG")
    parser.add_argument("--full", action="store_true", help="Download full Wikipedia")
    parser.add_argument("--top500k", action="store_true", help="Download top 500k articles (default)")
    parser.add_argument("--top100k", action="store_true", help="Download top 100k articles")
    parser.add_argument("--embeddings", action="store_true", help="Create vector embeddings")
    parser.add_argument("--skip-download", action="store_true", help="Skip download, just create embeddings")
    args = parser.parse_args()
    
    # Determine variant
    if args.full:
        variant = "full"
    elif args.top100k:
        variant = "top100k"
    else:
        variant = "top500k"
    
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║         WIKIPEDIA OFFLINE KNOWLEDGE BASE SETUP                ║
╠═══════════════════════════════════════════════════════════════╣
║  Variant: {variant:15}                                       ║
║  This will download and process Wikipedia for offline use.    ║
║  Estimated time: 30 min - 4 hours depending on variant.       ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    # Setup database
    conn = setup_database()
    
    # Download Wikipedia
    if not args.skip_download:
        download_wikipedia(conn, variant)
    
    # Optimize database
    logger.info("Optimizing database...")
    conn.execute("PRAGMA optimize")
    conn.execute("VACUUM")
    
    # Create embeddings if requested
    if args.embeddings:
        create_embeddings(conn)
    
    # Final stats
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM articles")
    count = cursor.fetchone()[0]
    
    db_size = DB_PATH.stat().st_size / (1024 * 1024 * 1024)  # GB
    
    print(f"""
╔═══════════════════════════════════════════════════════════════╗
║                    SETUP COMPLETE!                            ║
╠═══════════════════════════════════════════════════════════════╣
║  Articles: {count:,}                                          
║  Database size: {db_size:.2f} GB                              
║  Location: {DB_PATH}
╚═══════════════════════════════════════════════════════════════╝

Your AI now has offline access to Wikipedia!
    """)
    
    conn.close()


if __name__ == "__main__":
    main()
