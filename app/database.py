"""Database models and operations for the Community Mesh Platform.

Uses SQLAlchemy with async SQLite for persistence of messages,
bulletin board posts, and other platform data.
"""

from datetime import datetime
from typing import Optional
import asyncio
from pathlib import Path

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, create_engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.future import select

Base = declarative_base()


class Message(Base):
    """Stored mesh messages for history and logging."""
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), unique=True, nullable=True)
    from_id = Column(String(32), nullable=False, index=True)
    from_name = Column(String(64), nullable=True)
    to_id = Column(String(32), nullable=True, index=True)
    text = Column(Text, nullable=False)
    channel = Column(Integer, default=0)
    is_direct = Column(Boolean, default=False)
    is_from_bot = Column(Boolean, default=False)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    snr = Column(Float, nullable=True)
    rssi = Column(Integer, nullable=True)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "from_id": self.from_id,
            "from_name": self.from_name,
            "to_id": self.to_id,
            "text": self.text,
            "channel": self.channel,
            "is_direct": self.is_direct,
            "is_from_bot": self.is_from_bot,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "snr": self.snr,
            "rssi": self.rssi,
        }


class BBSPost(Base):
    """Bulletin board posts."""
    __tablename__ = "bbs_posts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    board = Column(String(32), nullable=False, index=True)
    from_id = Column(String(32), nullable=False, index=True)
    from_name = Column(String(64), nullable=True)
    to_id = Column(String(32), nullable=True, index=True)  # For private messages
    subject = Column(String(128), nullable=True)
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=True)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "board": self.board,
            "from_id": self.from_id,
            "from_name": self.from_name,
            "to_id": self.to_id,
            "subject": self.subject,
            "content": self.content,
            "is_read": self.is_read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


class Node(Base):
    """Cached node information."""
    __tablename__ = "nodes"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    node_id = Column(String(32), unique=True, nullable=False, index=True)
    short_name = Column(String(16), nullable=True)
    long_name = Column(String(64), nullable=True)
    hardware = Column(String(32), nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    altitude = Column(Float, nullable=True)
    battery_level = Column(Integer, nullable=True)
    last_heard = Column(DateTime, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    message_count = Column(Integer, default=0)
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "short_name": self.short_name,
            "long_name": self.long_name,
            "hardware": self.hardware,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "altitude": self.altitude,
            "battery_level": self.battery_level,
            "last_heard": self.last_heard.isoformat() if self.last_heard else None,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "message_count": self.message_count,
        }


class Database:
    """Async database manager."""
    
    def __init__(self, db_path: str = "data/meshbot.db"):
        self.db_path = db_path
        self._engine = None
        self._session_factory = None
    
    async def initialize(self) -> None:
        """Initialize the database and create tables."""
        # Ensure data directory exists
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        
        # Create async engine
        self._engine = create_async_engine(
            f"sqlite+aiosqlite:///{self.db_path}",
            echo=False
        )
        
        # Create tables
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        # Create session factory
        self._session_factory = sessionmaker(
            self._engine, 
            class_=AsyncSession, 
            expire_on_commit=False
        )
    
    async def get_session(self) -> AsyncSession:
        """Get a database session."""
        if not self._session_factory:
            await self.initialize()
        return self._session_factory()
    
    # Message operations
    async def add_message(self, message: Message) -> Message:
        """Add a message to the database."""
        async with await self.get_session() as session:
            session.add(message)
            await session.commit()
            await session.refresh(message)
            return message
    
    async def get_messages(
        self, 
        limit: int = 100, 
        channel: Optional[int] = None,
        from_id: Optional[str] = None
    ) -> list[Message]:
        """Get recent messages."""
        async with await self.get_session() as session:
            query = select(Message).order_by(Message.timestamp.desc()).limit(limit)
            if channel is not None:
                query = query.where(Message.channel == channel)
            if from_id:
                query = query.where(Message.from_id == from_id)
            result = await session.execute(query)
            return result.scalars().all()
    
    async def get_message_count(self) -> int:
        """Get total message count (efficient)."""
        async with await self.get_session() as session:
            from sqlalchemy import func
            result = await session.execute(select(func.count(Message.id)))
            return result.scalar() or 0
    
    # BBS operations
    async def add_bbs_post(self, post: BBSPost) -> BBSPost:
        """Add a BBS post."""
        async with await self.get_session() as session:
            session.add(post)
            await session.commit()
            await session.refresh(post)
            return post
    
    async def get_bbs_posts(
        self, 
        board: Optional[str] = None,
        to_id: Optional[str] = None,
        include_read: bool = True,
        limit: int = 50
    ) -> list[BBSPost]:
        """Get BBS posts."""
        async with await self.get_session() as session:
            query = select(BBSPost).order_by(BBSPost.created_at.desc()).limit(limit)
            if board:
                query = query.where(BBSPost.board == board)
            if to_id:
                query = query.where(
                    (BBSPost.to_id == to_id) | (BBSPost.to_id == None)
                )
            if not include_read:
                query = query.where(BBSPost.is_read == False)
            result = await session.execute(query)
            return result.scalars().all()
    
    async def get_user_mail(self, to_id: str, unread_only: bool = True) -> list[BBSPost]:
        """Get mail for a specific user."""
        async with await self.get_session() as session:
            query = select(BBSPost).where(
                BBSPost.to_id == to_id
            ).order_by(BBSPost.created_at.desc())
            if unread_only:
                query = query.where(BBSPost.is_read == False)
            result = await session.execute(query)
            return result.scalars().all()
    
    async def mark_post_read(self, post_id: int) -> None:
        """Mark a BBS post as read."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(BBSPost).where(BBSPost.id == post_id)
            )
            post = result.scalar_one_or_none()
            if post:
                post.is_read = True
                await session.commit()
    
    async def delete_bbs_post(self, post_id: int) -> bool:
        """Delete a BBS post."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(BBSPost).where(BBSPost.id == post_id)
            )
            post = result.scalar_one_or_none()
            if post:
                await session.delete(post)
                await session.commit()
                return True
            return False
    
    async def count_user_mail(self, to_id: str) -> int:
        """Count unread mail for a user."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(BBSPost).where(
                    BBSPost.to_id == to_id,
                    BBSPost.is_read == False
                )
            )
            return len(result.scalars().all())
    
    # Node operations
    async def update_node(self, node_data: dict) -> Node:
        """Update or create a node record."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(Node).where(Node.node_id == node_data["node_id"])
            )
            node = result.scalar_one_or_none()
            
            if node:
                for key, value in node_data.items():
                    if hasattr(node, key) and value is not None:
                        setattr(node, key, value)
            else:
                node = Node(**node_data)
                session.add(node)
            
            await session.commit()
            await session.refresh(node)
            return node
    
    async def get_nodes(self) -> list[Node]:
        """Get all known nodes."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(Node).order_by(Node.last_heard.desc())
            )
            return result.scalars().all()
    
    async def increment_message_count(self, node_id: str) -> None:
        """Increment message count for a node."""
        async with await self.get_session() as session:
            result = await session.execute(
                select(Node).where(Node.node_id == node_id)
            )
            node = result.scalar_one_or_none()
            if node:
                node.message_count += 1
                await session.commit()


# Global database instance
db = Database()
