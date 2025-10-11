"""
bot.py

This module initializes the Discord bot instance using the discord.py library,
configures its command prefix, and sets required intents for operation.

Features:
    - Enables message content and member access intents for proper bot interaction.
    - Sets up a command-based bot using "/" as the command prefix.
    - Stores basic metadata such as version and creator information.

Attributes:
    bot (commands.Bot): The main bot instance configured with necessary intents and command prefix.
    bot.config (dict): A dictionary containing metadata like version and creator.
"""

import discord
from discord.ext import commands

intents = discord.Intents.default()

bot = commands.Bot(command_prefix="/", intents=intents)

bot.config = {
    "version": "1.0.0",
    "creator": "Crptk"
}
