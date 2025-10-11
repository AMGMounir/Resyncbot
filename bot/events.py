import discord

import logging
from bot.server_manager import server_manager
from bot.utils import auto_refresh_premium_cache
from bot.utils import auto_refresh_server_list

logger = logging.getLogger("ResyncBot")

def register_event_handlers(bot: discord.Client):
    """
    Registers core Discord event handlers for the bot.

    Args:
        bot (discord.Client): The main Discord client or bot instance.
    """
    @bot.event
    async def on_ready():
        """
        Called when the bot has successfully connected to Discord.

        - Logs the bot’s login username and ID.
        - Syncs and registers all slash commands with Discord.
        - Logs the successful initialization of slash commands.
        """
        logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
        bot.loop.create_task(auto_refresh_premium_cache())
        bot.loop.create_task(auto_refresh_server_list())
        synced = await bot.tree.sync()
        if synced:
            logger.info("Slash commands Initialized!")
        server_manager.save_guild_objects(bot.guilds)
        logger.info(f"📦 Backfilled {len(bot.guilds)} servers into DB")

    @bot.event
    async def on_guild_join(guild):
        server_manager.add_server(guild)
        logger.info(f"+ Joined server: {guild.name} ({guild.id})")


    @bot.event
    async def on_guild_remove(guild):
        server_manager.remove_server(guild)
        logger.info(f"- Left server: {guild.name} ({guild.id})")