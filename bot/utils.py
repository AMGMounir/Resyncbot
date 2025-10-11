import os
import logging
import atexit
import json
from discord import Guild
from config import Config
import tempfile
import shutil
import sys
from pathlib import Path
import asyncio
from datetime import datetime

BACKEND_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = BACKEND_DIR.parent
BACKEND_PATH = PROJECT_ROOT / "backend"

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_PATH))

from backend.premium_utils import premium_manager
from bot.server_manager import server_manager

def init_logging(name="ResyncBot"):
    """
    Configures the logging system with a standard format and log level.

    Args:
        name (str): The name of the logger to retrieve. Defaults to "ResyncBot".

    Returns:
        logging.Logger: A configured logger instance.
    """
    logging.basicConfig(
        level=logging.INFO, # timestamp
        format='[%(asctime)s] %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(name)

def prepare_folders():
    """
    Prepares required folders and lock files for the bot runtime.

    - Creates a `./data` directory if it doesn't exist.
    - Ensures a fresh `bot.lock` file is written to signal the bot is running.
    - Registers a cleanup hook to remove `bot.lock` on exit.
    """
    bot_lock_path = os.path.join(Config.DATA_DIR, "bot.lock")

    os.makedirs(Config.DATA_DIR, exist_ok=True)

    try:
        if os.path.exists(bot_lock_path):
            os.remove(bot_lock_path)
        with open(bot_lock_path, "w") as f:
            f.write("running")
    except Exception as e:
        print(f"ERROR preparing bot.lock: {e}")

    atexit.register(lambda: safe_remove(bot_lock_path))
    
def safe_remove(path):
    """
    Safely removes a file if it exists, ignoring errors.

    Args:
        path (str): The path of the file to remove.
    """
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"ERROR during cleanup of {path}: {e}")

def get_url() -> str:
    return Config.RESYNC_API_BASE

def load_servers():
    """Load server list from JSON file"""
    if not os.path.exists(Config.SERVER_LIST_FILE):
        return []
    try:
        with open(Config.SERVER_LIST_FILE, "r") as f:
            data = json.load(f)
            return data.get("servers", []) if isinstance(data, dict) else []
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading servers: {e}")
        return []

def save_servers(server_dicts):
    """Save server list safely and atomically."""
    try:
        os.makedirs(os.path.dirname(Config.SERVER_LIST_FILE), exist_ok=True)

        data = {
            "total_servers": len(server_dicts),
            "servers": server_dicts
        }

        print(f"[SAVE_SERVERS] Writing {len(server_dicts)} servers to file.")

        # Write to a temporary file first
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp, indent=2)
            temp_path = tmp.name

        # Replace the old file atomically
        shutil.move(temp_path, Config.SERVER_LIST_FILE)

    except Exception as e:
        print("Failed to save server list")

def save_guild_objects(guilds: list[Guild]):
    """Convert guilds to dicts and save them safely."""
    server_dicts = []
    for g in guilds:
        try:
            server_dicts.append({
                "id": g.id,
                "name": g.name,
                "member_count": g.member_count or 0,
                "joined_at": str(g.me.joined_at) if g.me and g.me.joined_at else None
            })
        except Exception as e:
            print(f"Error serializing guild: {e}")

    save_servers(server_dicts)

def add_server_to_list(guild: Guild):
    """Add a server to the tracked server list"""
    servers = load_servers()
    servers = [s for s in servers if s["id"] != guild.id]  # Remove if duplicate
    servers.append({
        "id": guild.id,
        "name": guild.name,
        "member_count": guild.member_count,
        "joined_at": str(guild.me.joined_at) if guild.me.joined_at else None
    })
    save_servers(servers)

def remove_server_from_list(guild: Guild):
    """Remove a server from the tracked server list"""
    servers = load_servers()
    servers = [s for s in servers if s["id"] != guild.id]
    save_servers(servers)

async def auto_refresh_premium_cache():
    while True:
        try:
            print(f"[⏱️ {datetime.now()}] Refreshing premium cache...")
            premium_manager.force_refresh_all_cached_users()
        except Exception as e:
            print(f"[❌] Failed to refresh premium cache: {e}")
        await asyncio.sleep(300)  # every 5 mins

async def auto_refresh_server_list():
    while True:
        try:
            print(f"[⏱️ {datetime.now()}] Refreshing server list...")
            server_manager.update_server_list()
        except Exception as e:
            print(f"[❌] Failed to refresh server list: {e}")
        await asyncio.sleep(600)