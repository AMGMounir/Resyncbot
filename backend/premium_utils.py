import psycopg2
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional
from config import Config

class PremiumManager:
    def __init__(self):
        print("[DEBUG] Connection string:", repr(Config.DATABASE_URL))
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL is empty or missing!")

        self._premium_cache = {}
        self._cache_expiry = {}

        self._ensure_cache_table_exists()

    def _ensure_cache_table_exists(self):
        """Create the premium_cache_refresh table if it doesn't exist"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS premium_cache_refresh (
                            user_id BIGINT PRIMARY KEY,
                            needs_refresh BOOLEAN NOT NULL DEFAULT FALSE,
                            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                        )
                    """)
            print("[DEBUG] Ensured premium_cache_refresh table exists")
        except Exception as e:
            print(f"[DEBUG] Error creating cache refresh table: {e}")

    def _clear_premium_cache(self, user_id: int):
        """Clear cached premium status for a user"""
        if user_id in self._premium_cache:
            del self._premium_cache[user_id]
        if user_id in self._cache_expiry:
            del self._cache_expiry[user_id]
        print(f"[DEBUG] Cleared premium cache for user {user_id}")
    
    def _check_cache_refresh_flag(self, user_id: int):
        """Check if premium cache needs to be refreshed for this user"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    # Check if cache needs refresh for this user
                    cursor.execute("""
                        SELECT needs_refresh FROM premium_cache_refresh 
                        WHERE user_id = %s AND needs_refresh = TRUE
                    """, (user_id,))
                    
                    needs_refresh = cursor.fetchone()
                    
                    if needs_refresh:
                        print(f"[DEBUG] Cache refresh flag found for user {user_id}")
                        # Clear the refresh flag
                        cursor.execute("""
                            UPDATE premium_cache_refresh 
                            SET needs_refresh = FALSE 
                            WHERE user_id = %s
                        """, (user_id,))
                        
                        # Clear cached premium status for this user
                        self._clear_premium_cache(user_id)
                        return True
            return False
            
        except Exception as e:
            print(f"[DEBUG] Error checking cache refresh flag: {e}")
            return False
    
    def is_premium_user(self, user_id: int) -> bool:
        """Check if user has active premium subscription with cache refresh support"""
        try:
            # First check if we need to refresh the cache
            self._check_cache_refresh_flag(user_id)
            
            # Check if we have a cached result that's still valid (5 minutes)
            now = datetime.now(timezone.utc)
            if (user_id in self._premium_cache and 
                user_id in self._cache_expiry and 
                self._cache_expiry[user_id] > now):
                print(f"[DEBUG] Using cached premium status for user {user_id}: {self._premium_cache[user_id]}")
                return self._premium_cache[user_id]
            
            # Get fresh data from database
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT is_premium, premium_expires_at FROM user_subscriptions
                        WHERE user_id = %s
                    """, (user_id,))
                    
                    result = cursor.fetchone()
                
                    is_premium = False
                    needs_db_update = False
                    
                    if not result or not result[0]:  # No record or is_premium is False
                        is_premium = False
                    else:
                        # Check expiration (None = lifetime, future date = active)
                        expires_at = result[1]
                        if expires_at is None:  # Lifetime subscription
                            is_premium = True
                        else:
                            # Handle timezone-aware comparison
                            if expires_at.tzinfo is None:
                                expires_at = expires_at.replace(tzinfo=timezone.utc)
                            
                            if expires_at > now:
                                is_premium = True
                            else:
                                # Subscription has expired - update database
                                is_premium = False
                                needs_db_update = True
                                print(f"[DEBUG] Subscription expired for user {user_id} at {expires_at}")
                
                    # Update database if subscription expired
                    if needs_db_update:
                        cursor.execute("""
                            UPDATE user_subscriptions 
                            SET is_premium = FALSE 
                            WHERE user_id = %s
                        """, (user_id,))
                        conn.commit()
                        print(f"[DEBUG] Updated database: set is_premium = FALSE for user {user_id}")
           
            # Cache the result for 5 minutes
            self._premium_cache[user_id] = is_premium
            self._cache_expiry[user_id] = now + timedelta(minutes=5)
            
            print(f"[DEBUG] Fresh premium check for user {user_id}: {is_premium}")
            return is_premium
            
        except Exception as e:
            print(f"Error checking premium status: {e}")
            return False
    
    def check_rate_limits(self, user_id: int, command_type: str) -> Tuple[bool, Optional[str]]:
        """
        Check universal 30-second cooldown for all users
        Returns: (can_use, error_message)
        """
        print(f"[DEBUG] Checking rate limits for user {user_id}, command: {command_type}")

        try:
            now = datetime.now(timezone.utc)

            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT used_at FROM user_usage 
                        WHERE user_id = %s 
                        ORDER BY used_at DESC 
                        LIMIT 1
                    """, (user_id,))
                    
                    last_use = cursor.fetchone()
                    if last_use:
                        used_at = last_use[0]
                        if used_at.tzinfo is None:
                            used_at = used_at.replace(tzinfo=timezone.utc)
                        
                        print(f"[DEBUG] Last command used at: {used_at}, now: {now}")

                        if used_at > now:
                            print(f"[WARN] last_use timestamp is in the future: {used_at} > {now}")
                            used_at = now

                        elapsed = now - used_at
                        if elapsed < timedelta(seconds=Config.COOLDOWN):
                            remaining = timedelta(seconds=Config.COOLDOWN) - elapsed
                            print(f"[DEBUG] Cooldown active, {remaining.seconds}s remaining")
                            return False, f"⏱️ Command cooldown: {remaining.seconds}s remaining"

            if not Config.PREMIUM_ENABLED:
                print(f"[DEBUG] Premium disabled - unlimited usage allowed")
                return True, None
            if self.is_premium_user(user_id):
                print(f"[DEBUG] User is premium, allowing command")
                return True, None

            print(f"[DEBUG] User is not premium, checking daily limits")

            if command_type in ['auto_resync', 'random_resync']:
                yesterday = now - timedelta(days=1)
                print(f"[DEBUG] Checking usage since: {yesterday}")

                with psycopg2.connect(Config.DATABASE_URL) as conn:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            SELECT COUNT(*) FROM user_usage
                            WHERE user_id = %s AND command_type = %s
                            AND used_at >= %s
                        """, (user_id, command_type, yesterday))

                        usage_count = cursor.fetchone()[0]
                        limit = Config.AUTO_LIMITS if command_type == 'auto_resync' else Config.RANDOM_LIMITS
                        
                        print(f"[DEBUG] Usage count: {usage_count}/{limit}")

                        if usage_count >= limit:
                            print(f"[DEBUG] Daily limit reached!")
                            return False, f"🚫 Daily limit reached: {usage_count}/{limit} {command_type.replace('_', ' ')}s used.\n\n💡 **Reset your limits**: Vote for ResyncBot on Top.gg using `/vote`\nor upgrade to premium with `/premium`!"

            print(f"[DEBUG] All checks passed, allowing command")
            return True, None

        except Exception as e:
            print(f"Error checking rate limits: {e}")
            return True, None

        
    def log_command_usage(self, user_id: int, command_type: str):
        """Log command usage for cooldown tracking"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    # Use timezone-aware timestamp
                    now = datetime.now(timezone.utc)
                    cursor.execute("""
                        INSERT INTO user_usage (user_id, command_type, used_at) 
                        VALUES (%s, %s, %s)
                    """, (user_id, command_type, now))
                    conn.commit()
        except Exception as e:
            print(f"Error logging usage: {e}")
    
    def set_premium_status(self, user_id: int, is_premium: bool, expires_at: Optional[datetime] = None):
        """Set user premium status"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO user_subscriptions (user_id, is_premium, premium_expires_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) 
                        DO UPDATE SET 
                            is_premium = EXCLUDED.is_premium,
                            premium_expires_at = EXCLUDED.premium_expires_at
                    """, (user_id, is_premium, expires_at))
                    conn.commit()
                    
                    # Clear cache since we just updated the status
                    self._clear_premium_cache(user_id)
                    
                    print(f"Set user {user_id} premium status: {is_premium}, expires: {expires_at}")
        except Exception as e:
            print(f"Error setting premium status: {e}")

    def get_user_usage_stats(self, user_id: int) -> dict:
        """Get user's current daily usage for display"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    # Get usage counts for today
                    cursor.execute("""
                        SELECT command_type, COUNT(*) FROM user_usage
                        WHERE user_id = %s AND used_at >= %s
                        GROUP BY command_type
                    """, (user_id, datetime.now() - timedelta(days=1)))
                    
                    usage = dict(cursor.fetchall())

            is_premium = self.is_premium_user(user_id)

            return {
                'auto_resync': usage.get('auto_resync', 0),
                'random_resync': usage.get('random_resync', 0),
                'is_premium': is_premium
            }

        except Exception as e:
            print(f"Error getting usage stats: {e}")
            return {'auto_resync': 0, 'random_resync': 0, 'is_premium': False}


    def force_cache_refresh(self, user_id: int):
        """Force refresh cache for a specific user (for admin use)"""
        try:
            # Clear in-memory cache
            self._clear_premium_cache(user_id)
            
            # Set database cache refresh flag
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO premium_cache_refresh (user_id, needs_refresh, updated_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id)
                        DO UPDATE SET 
                            needs_refresh = TRUE,
                            updated_at = EXCLUDED.updated_at
                    """, (user_id, True, datetime.now(timezone.utc)))
                    
                    conn.commit()
            
            print(f"[DEBUG] Forced cache refresh for user {user_id}")
            
        except Exception as e:
            print(f"Error forcing cache refresh: {e}")

    def admin_delete_user_data(self, user_id: int):
        """Admin function to safely delete user data and clear cache"""
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    # Delete user subscription data
                    cursor.execute("DELETE FROM user_subscriptions WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM premium_cache_refresh WHERE user_id = %s", (user_id,))
                    cursor.execute("DELETE FROM user_usage WHERE user_id = %s", (user_id,))
                    
                    conn.commit()
            
            # Clear in-memory cache
            self._clear_premium_cache(user_id)
            
            print(f"[ADMIN] Deleted all data for user {user_id}")
            
        except Exception as e:
            print(f"Error deleting user data: {e}")
            
    def force_refresh_all_cached_users(self):
        """Manually refresh premium status cache for all currently premium users"""
        try:
            count = 0
            now = datetime.now(timezone.utc)

            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT user_id FROM user_subscriptions
                        WHERE is_premium = TRUE
                    """)
                    rows = cursor.fetchall()

                    for row in rows:
                        user_id = int(row[0])
                        self._premium_cache[user_id] = True
                        self._cache_expiry[user_id] = now + timedelta(minutes=5)
                        count += 1

            print(f"[✅] Premium cache updated for {count} users.")
        except Exception as e:
            print(f"[❌] Error during full premium cache refresh: {e}")


# Global instance
premium_manager = PremiumManager()