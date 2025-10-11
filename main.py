import asyncio
import discord
import traceback
import sys
import os
from dotenv import load_dotenv

# Add current directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

# Load environment variables early if in development
if os.environ.get("RAILWAY_ENVIRONMENT_NAME") is None:
    load_dotenv()

from config import Config
from bot.utils import init_logging, prepare_folders

from backend.performance_monitor import start_performance_monitoring, get_performance_stats

# Initialize configuration and validate
try:
    Config.validate()
except EnvironmentError as e:
    print(f"Configuration Error: {e}")
    sys.exit(1)

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
logger = init_logging()
if not DEBUG_MODE:
    logger.disabled = True

from backend.resync_queue import start_worker_pool
from bot.bot import bot
from bot.commands.resyncmedia import setup_resyncmedia
from bot.commands.resyncmp3 import setup_resyncmp3
from bot.commands.resyncmp4 import setup_resyncmp4
from bot.commands.info import setup_info
from bot.commands.info import setup_guide
from bot.commands.admin_commands import setup_admin_commands
from bot.events import register_event_handlers
from bot.commands.invite import setup_invite
from bot.commands.resyncrandomfile import setup_resyncrandomfile
from bot.commands.resyncrandommedia import setup_resyncrandommedia
from bot.commands.autoresyncmp4 import setup_autoresyncmp4
from bot.commands.autoresyncmp3 import setup_autoresyncmp3
from bot.commands.autoresyncmedia import setup_autoresyncmedia
from bot.commands.loopaudio import setup_loopaudio
from bot.commands.premium_commands import setup_premium_commands, setup_limits_command
from bot.commands.downloadaudio import setup_downloadaudio
from bot.commands.downloadvideo import setup_downloadvideo
from bot.commands.vote import setup_vote_command
from bot.commands.donate import setup_donate_command
"""
main.py

This is the main entry point for the ResyncBot Discord bot.

Responsibilities:
    - Initializes environment variables, logging, and required folders.
    - Registers all command modules and event handlers.
    - Starts the background worker pool for handling media processing jobs.
    - Launches the Discord bot with automatic retry logic for rate limits.

The bot will only start if this file is executed directly.
"""

async def cleanup():
    """Cleanup function for graceful shutdown"""
    logger.info("🧹 Cleaning up resources...")
    
    # Close the bot connection
    if not bot.is_closed():
        await bot.close()
    
    # Cancel any running tasks
    tasks = [task for task in asyncio.all_tasks() if not task.done()]
    if tasks:
        logger.info(f"Cancelling {len(tasks)} running tasks...")
        for task in tasks:
            task.cancel()
        
        # Wait for tasks to complete cancellation
        await asyncio.gather(*tasks, return_exceptions=True)

prepare_folders()
register_event_handlers(bot)

async def bot_safe_start():
    """Safely start the bot with all components"""
    setup_resyncmedia(bot)
    setup_resyncmp3(bot)
    setup_resyncmp4(bot)
    setup_admin_commands(bot)    
    setup_info(bot)           
    setup_guide(bot)
    setup_invite(bot)
    setup_resyncrandomfile(bot)
    setup_resyncrandommedia(bot)
    setup_autoresyncmp3(bot)
    setup_autoresyncmp4(bot)
    setup_autoresyncmedia(bot)
    setup_loopaudio(bot)
    setup_limits_command(bot)
    setup_downloadaudio(bot)
    setup_downloadvideo(bot)
    setup_vote_command(bot)
    setup_donate_command(bot)
    if Config.PREMIUM_ENABLED:
        setup_premium_commands(bot)    
    logger.info("🔍 Starting performance monitoring...")
    start_performance_monitoring()

    # Start background worker pool
    await start_worker_pool()

    # Start bot with retry logic
    retry_count = 0
    max_retries = 3
    
    while retry_count < max_retries:
        try:
            logger.info("Starting Discord bot...")
            await bot.start(Config.DISCORD_BOT_TOKEN)
            return
        except discord.HTTPException as e:
            if e.status == 429:
                await bot.close() 
                cooldown = 60 * (retry_count + 1)  # Exponential backoff
                logger.error(f"Rate limited. Waiting {cooldown}s before retry {retry_count + 1}/{max_retries}")
                await asyncio.sleep(cooldown)
                retry_count += 1
            else:
                logger.error(f"HTTP Exception: {e}")
                raise
        except discord.LoginFailure:
            logger.error("Invalid Discord token")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Unexpected error starting bot: {e}")
            await cleanup()
            raise
    
    if retry_count >= max_retries:
        logger.error("Failed to start bot after maximum retries")
        sys.exit(1)

if __name__ == "__main__":
    try:
        logger.info(f"Starting ResyncBot v{getattr(bot, 'config', {}).get('version', 'unknown')}")
        asyncio.run(bot_safe_start())
    except KeyboardInterrupt:
        logger.info("Bot shutdown requested by user")
    except Exception as e:
        logger.error("CRITICAL CRASH:")
        logger.error(traceback.format_exc())
    finally:
        logger.info("Bot process ended")
        sys.exit(1)