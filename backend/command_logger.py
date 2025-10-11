import discord
import asyncio
import logging
from discord.utils import escape_markdown
from config import Config

logger = logging.getLogger("ResyncBot")

LOG_CHANNEL_ID = Config.LOG_CHANNEL_ID

async def log_command_usage(
    bot,
    interaction: discord.Interaction,
    command_name: str,
    args: dict,
    status: str = "success",  # or "fail"
    error: str = None,
    file: discord.File = None
):
    try:
        if interaction.user.id == Config.BOT_OWNER_ID:
            return
        
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if not log_channel:
            logger.warning("⚠️ Log channel not found.")
            return

        user = interaction.user
        embed = discord.Embed(
            title=f"{'✅ Success' if status == 'success' else '❌ Failed'}: /{command_name}",
            description=f"Used by <@{user.id}> in <#{interaction.channel_id}>",
            color=discord.Color.green() if status == "success" else discord.Color.red(),
            timestamp=discord.utils.utcnow()
        )

        # Truncate long args and escape markdown
        if args:
            formatted_args = []
            for k, v in args.items():
                v = str(v)
                if len(v) > 900:
                    v = v[:900] + "…"
                formatted_args.append(f"> **{escape_markdown(k)}**: `{escape_markdown(v)}`")
            embed.add_field(name="Arguments", value="\n".join(formatted_args), inline=False)
        else:
            embed.add_field(name="Arguments", value="No arguments", inline=False)

        if error:
            lines = error.strip().splitlines()
            last_line = lines[-1] if lines else error
            embed.add_field(name="Error", value=f"```{last_line}```", inline=False)

        embed.set_footer(text=f"User ID: {user.id} | Command ID: {interaction.id}")
        await log_channel.send(embed=embed, file=file)  

    except Exception as e:
        logger.warning(f"⚠️ Failed to log command usage: {e}", exc_info=True)

def safe_log_command(bot, interaction, command_name, args, status="success", error=None, file=None):
    try:
        if interaction.user.id == Config.BOT_OWNER_ID:
            return
        
        asyncio.create_task(log_command_usage(bot, interaction, command_name, args, status, error, file))
    except Exception as e:
        logger.warning(f"⚠️ [safe_log_command] Failed to queue log task: {e}", exc_info=True)
