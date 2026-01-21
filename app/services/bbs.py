"""Bulletin Board System service.

Provides BBS functionality for the mesh network including public boards
and private mail between users.
"""

from datetime import datetime, timedelta
from typing import Optional
import logging

from ..database import db, BBSPost
from ..config import config

logger = logging.getLogger(__name__)


class BBSService:
    """Bulletin Board System service."""
    
    def __init__(self):
        self.boards = [b.name for b in config.bbs.boards]
        self.expiry_days = config.bbs.message_expiry_days
    
    async def post_message(
        self,
        board: str,
        from_id: str,
        content: str,
        from_name: Optional[str] = None,
        to_id: Optional[str] = None,
        subject: Optional[str] = None
    ) -> BBSPost:
        """Post a message to a board or as mail.
        
        Args:
            board: Board name (e.g., "General", "Mail")
            from_id: Sender's node ID
            content: Message content
            from_name: Sender's name (optional)
            to_id: Recipient's node ID for mail (optional)
            subject: Message subject (optional)
            
        Returns:
            The created BBSPost
        """
        expires_at = None
        if self.expiry_days > 0:
            expires_at = datetime.utcnow() + timedelta(days=self.expiry_days)
        
        post = BBSPost(
            board=board,
            from_id=from_id,
            from_name=from_name,
            to_id=to_id,
            subject=subject,
            content=content,
            expires_at=expires_at
        )
        
        post = await db.add_bbs_post(post)
        logger.info(f"BBS post created: {board} from {from_id} ({from_name})")
        return post
    
    async def send_mail(
        self,
        from_id: str,
        to_id: str,
        content: str,
        from_name: Optional[str] = None,
        subject: Optional[str] = None
    ) -> BBSPost:
        """Send a private mail message.
        
        Args:
            from_id: Sender's node ID
            to_id: Recipient's node ID
            content: Message content
            from_name: Sender's name
            subject: Message subject
            
        Returns:
            The created BBSPost
        """
        return await self.post_message(
            board="Mail",
            from_id=from_id,
            to_id=to_id,
            content=content,
            from_name=from_name,
            subject=subject
        )
    
    async def get_board_posts(
        self,
        board: str,
        limit: int = 10
    ) -> list[BBSPost]:
        """Get posts from a specific board.
        
        Args:
            board: Board name
            limit: Maximum number of posts
            
        Returns:
            List of BBSPost objects
        """
        posts = await db.get_bbs_posts(board=board, limit=limit)
        return posts
    
    async def get_user_mail(
        self,
        user_id: str,
        unread_only: bool = True
    ) -> list[BBSPost]:
        """Get mail for a specific user.
        
        Args:
            user_id: User's node ID
            unread_only: Only return unread mail
            
        Returns:
            List of BBSPost objects
        """
        return await db.get_user_mail(user_id, unread_only=unread_only)
    
    async def count_mail(self, user_id: str) -> int:
        """Count unread mail for a user.
        
        Args:
            user_id: User's node ID
            
        Returns:
            Number of unread messages
        """
        return await db.count_user_mail(user_id)
    
    async def mark_read(self, post_id: int) -> None:
        """Mark a post as read.
        
        Args:
            post_id: Post ID to mark
        """
        await db.mark_post_read(post_id)
    
    async def delete_post(self, post_id: int, user_id: str) -> bool:
        """Delete a post (only if user owns it).
        
        Args:
            post_id: Post ID to delete
            user_id: User requesting deletion
            
        Returns:
            True if deleted, False otherwise
        """
        # Get post to verify ownership
        posts = await db.get_bbs_posts(limit=1000)
        post = next((p for p in posts if p.id == post_id), None)
        
        if post and (post.from_id == user_id or post.to_id == user_id):
            return await db.delete_bbs_post(post_id)
        return False
    
    async def get_all_posts(self, limit: int = 50) -> list[BBSPost]:
        """Get all recent BBS posts across all boards.
        
        Args:
            limit: Maximum number of posts
            
        Returns:
            List of BBSPost objects
        """
        return await db.get_bbs_posts(limit=limit)
    
    def format_post_list(self, posts: list[BBSPost], include_content: bool = False) -> str:
        """Format a list of posts for display.
        
        Args:
            posts: List of posts
            include_content: Whether to include full content
            
        Returns:
            Formatted string
        """
        if not posts:
            return "No messages."
        
        lines = []
        for i, post in enumerate(posts, 1):
            from_str = post.from_name or post.from_id[:8]
            date_str = post.created_at.strftime("%m/%d") if post.created_at else ""
            
            if include_content:
                lines.append(f"{i}. [{date_str}] {from_str}: {post.content[:50]}...")
            else:
                subject = post.subject or post.content[:30]
                lines.append(f"{i}. [{date_str}] {from_str}: {subject}...")
        
        return "\n".join(lines)
    
    def format_single_post(self, post: BBSPost) -> str:
        """Format a single post for display.
        
        Args:
            post: Post to format
            
        Returns:
            Formatted string
        """
        from_str = post.from_name or post.from_id
        date_str = post.created_at.strftime("%Y-%m-%d %H:%M") if post.created_at else ""
        
        lines = [
            f"From: {from_str}",
            f"Date: {date_str}",
        ]
        
        if post.subject:
            lines.append(f"Subject: {post.subject}")
        
        lines.append(f"\n{post.content}")
        
        return "\n".join(lines)


# Global BBS service instance
bbs_service = BBSService()
