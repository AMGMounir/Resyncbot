import os
import logging
import atexit
import psycopg2
from datetime import datetime, timedelta
from discord import Guild
from config import Config
from psycopg2.extras import execute_values

def init_logging(name="ResyncBot"):
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(name)

logger = init_logging()

class ServerManager:
    def __init__(self):
        self._ensure_table_exists()
        
    def _ensure_table_exists(self):
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        CREATE TABLE IF NOT EXISTS tracked_servers (
                            id BIGINT PRIMARY KEY,
                            name TEXT NOT NULL,
                            member_count INTEGER NOT NULL,
                            joined_at TIMESTAMPTZ
                        )
                    """)
            logger.info("[DEBUG] Ensured tracked_servers table exists")
        except Exception as e:
            logger.error(f"[DEBUG] Error creating tracked_servers table: {e}")

    def save_guild_objects(self, guilds: list[Guild]):
        data = []
        for g in guilds:
            try:
                data.append((
                    g.id,
                    g.name,
                    g.member_count or 0,
                    g.me.joined_at if g.me and g.me.joined_at else None
                ))
            except Exception as e:
                logger.warning(f"[WARN] Error serializing guild {g.id}: {e}")

        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    execute_values(cursor, """
                        INSERT INTO tracked_servers (id, name, member_count, joined_at)
                        VALUES %s
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            member_count = EXCLUDED.member_count,
                            joined_at = EXCLUDED.joined_at
                    """, data)
            logger.info(f"[DEBUG] Saved {len(data)} guilds to DB")
        except Exception as e:
            logger.error(f"[ERROR] Failed to bulk insert guilds: {e}")

    def add_server(self, guild: Guild):
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO tracked_servers (id, name, member_count, joined_at)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            member_count = EXCLUDED.member_count,
                            joined_at = EXCLUDED.joined_at
                    """, (
                        guild.id,
                        guild.name,
                        guild.member_count or 0,
                        guild.me.joined_at if guild.me and guild.me.joined_at else None
                    ))
            logger.info(f"[DEBUG] Added/updated server {guild.id}")
        except Exception as e:
            logger.error(f"[ERROR] Failed to add server {guild.id}: {e}")


    def remove_server(self, guild: Guild):
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM tracked_servers WHERE id = %s", (guild.id,))
            logger.info(f"[DEBUG] Removed server {guild.id} from DB")
        except Exception as e:
            logger.error(f"[ERROR] Failed to remove server {guild.id}: {e}")

    def get_all_servers(self):
        try:
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT id, name, member_count, joined_at FROM tracked_servers")
                    rows = cursor.fetchall()
                    return [
                        {
                            "id": row[0],
                            "name": row[1],
                            "member_count": row[2],
                            "joined_at": row[3].isoformat() if row[3] else None
                        }
                        for row in rows
                    ]
        except Exception as e:
            logger.error(f"[ERROR] Failed to fetch server list: {e}")
            return []

    def update_server_list(self):
        """
        Re-saves all current bot guilds into the DB and JSON cache.
        Should be called periodically to ensure server list stays fresh.
        """
        try:
            from bot.bot import bot
            guilds = bot.guilds
            self.save_guild_objects(guilds)
            print(f"[🔁] Server list refreshed ({len(guilds)} servers).")
        except Exception as e:
            print(f"[❌] Failed to update server list: {e}")

# Global instance
server_manager = ServerManager()
