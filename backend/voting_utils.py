'''
For top.gg voting, you don't need to do anything with this file.
'''
import psycopg2
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from config import Config
import logging

logger = logging.getLogger("VotingSystem")

class VotingManager:
    def __init__(self):
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL is required for voting system")
        
        self._ensure_voting_table_exists()
    
    def _ensure_voting_table_exists(self):
        """Create the user_votes table if it doesn't exist"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS user_votes (
                            user_id BIGINT PRIMARY KEY,
                            last_vote_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                            total_votes INTEGER NOT NULL DEFAULT 0,
                            limits_reset_today BOOLEAN NOT NULL DEFAULT FALSE,
                            last_reset_date DATE
                        )
                    """)
                    conn.commit()
            logger.info("Ensured user_votes table exists")
        except Exception as e:
            logger.error(f"Error creating votes table: {e}")
    
    def record_vote(self, user_id: int) -> bool:
        """
        Record a vote from Top.gg and check if user can reset limits.
        Returns True if limits were reset, False otherwise.
        """
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    now = datetime.now(timezone.utc)
                    today = now.date()
                    
                    # Check if user has already reset limits today
                    cursor.execute("""
                        SELECT limits_reset_today, last_reset_date, total_votes
                        FROM user_votes WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cursor.fetchone()
            
                    if result:
                        limits_reset_today, last_reset_date, total_votes = result
                        
                        # If it's a new day, reset the daily flag
                        if last_reset_date != today:
                            limits_reset_today = False
                        
                        # Update vote record
                        cursor.execute("""
                            UPDATE user_votes 
                            SET last_vote_at = %s, total_votes = %s + 1,
                                limits_reset_today = %s, last_reset_date = %s
                            WHERE user_id = %s
                        """, (now, total_votes, limits_reset_today, today, user_id))
                    else:
                        # New voter
                        limits_reset_today = False
                        cursor.execute("""
                            INSERT INTO user_votes (user_id, last_vote_at, total_votes, limits_reset_today, last_reset_date)
                            VALUES (%s, %s, 1, FALSE, %s)
                        """, (user_id, now, today))
            
                    # If they haven't reset limits today, do it now
                    if not limits_reset_today:
                        # Reset their usage counts by clearing today's entries
                        yesterday = now - timedelta(days=1)
                        cursor.execute("""
                            DELETE FROM user_usage 
                            WHERE user_id = %s AND used_at >= %s
                        """, (user_id, yesterday))
                        
                        # Mark as reset today
                        cursor.execute("""
                            UPDATE user_votes 
                            SET limits_reset_today = TRUE, last_reset_date = %s
                            WHERE user_id = %s
                        """, (today, user_id))
                        
                        conn.commit()
                        
                        logger.info(f"Reset daily limits for user {user_id} via vote")
                        return True
                    else:
                        conn.commit()
                        
                        logger.info(f"User {user_id} already reset limits today")
                        return False
                        
        except Exception as e:
            logger.error(f"Error recording vote: {e}")
            return False
    
    def can_reset_limits_today(self, user_id: int) -> bool:
        """Check if user can reset their limits today"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    today = datetime.now(timezone.utc).date()
                    
                    cursor.execute("""
                        SELECT limits_reset_today, last_reset_date
                        FROM user_votes WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cursor.fetchone()
            
            if not result:
                return True  # New user, can reset
            
            limits_reset_today, last_reset_date = result
            
            # If it's a new day, they can reset again
            if last_reset_date != today:
                return True
                
            return not limits_reset_today
            
        except Exception as e:
            logger.error(f"Error checking reset status: {e}")
            return False
    
    def get_user_vote_stats(self, user_id: int) -> dict:
        """Get user's voting statistics"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT total_votes, last_vote_at, limits_reset_today, last_reset_date
                        FROM user_votes WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cursor.fetchone()
            
            if not result:
                return {
                    'total_votes': 0,
                    'last_vote_at': None,
                    'can_reset_today': True,
                    'has_reset_today': False
                }
            
            total_votes, last_vote_at, limits_reset_today, last_reset_date = result
            today = datetime.now(timezone.utc).date()
            
            # If it's a new day, they can reset again
            can_reset_today = last_reset_date != today or not limits_reset_today
            has_reset_today = last_reset_date == today and limits_reset_today
            
            return {
                'total_votes': total_votes,
                'last_vote_at': last_vote_at,
                'can_reset_today': can_reset_today,
                'has_reset_today': has_reset_today
            }
            
        except Exception as e:
            logger.error(f"Error getting vote stats: {e}")
            return {'total_votes': 0, 'last_vote_at': None, 'can_reset_today': True, 'has_reset_today': False}

# Global instance
voting_manager = VotingManager()