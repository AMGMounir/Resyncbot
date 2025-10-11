import discord
from aiohttp import FormData
from discord import app_commands
from discord.ext import commands
from bot.utils import get_url
from backend.resync_queue import job_queue
from backend.video_utils import (
    run_resync_job,
    logger
)
from backend.command_logger import safe_log_command
from backend.recent_usage import log_recent_command
from config import Config
from backend.error_handler import handle_command_error, ValidationError
from backend.premium_utils import premium_manager

"""
resyncrandommedia.py

Defines the `/resyncrandommedia` slash command for the Discord bot.

Functionality:
    - Accepts a video URL (YouTube, Streamable, etc.).
    - Downloads the video using the same logic as resyncmedia.
    - Analyzes the video's BPM and matches it with a random track from the database.
    - Sends the URLs and metadata to the backend Flask API (`/resyncrandommedia`) for processing.
"""

def setup_resyncrandommedia(bot: commands.Bot):
    """
    Registers the /resyncrandommedia slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="resyncrandommedia",
        description="Attempts to resync a video URL to a random audio!"
    )
    @app_commands.describe(
        video_url="The video URL (YouTube, Streamable, etc.)",
        sfx_file="Optional SFX file to overlay on the final result",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30)",
    )
    @handle_command_error
    async def resyncrandommedia(
        interaction: discord.Interaction,
        video_url: str,
        sfx_file: discord.Attachment = None,
        video_start_input: str = "0",
        video_end_input: str = "0"):
        log_recent_command(interaction.user.id, interaction.channel_id) 

        can_use, error_msg = premium_manager.check_rate_limits(interaction.user.id, "random_resync")
        if not can_use:
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_msg, ephemeral=True)
                else:
                    await interaction.followup.send(error_msg, ephemeral=True)
            except Exception as e:
                logger.warning(f"Failed to send rate limit message: {e}")
            return
        
        try:
            await interaction.response.defer(thinking=True)
        except discord.InteractionResponded:
            pass        
        except discord.HTTPException as e:
            logger.warning(f"⚠️ Failed to defer interaction: {e}")

        # Validate video URL
        if not video_url.strip():
            raise ValidationError("Empty video URL", "🔗 Please provide a video URL")
        
        # Basic URL validation
        if not video_url.startswith(("http://", "https://")):
            raise ValidationError("Invalid video URL format", "🔗 Please provide a valid video URL starting with http:// or https://")
        
        # Check for obviously invalid URLs
        if any(char in video_url for char in [" ", "<", ">", '"']):
            raise ValidationError("Invalid video URL characters", "🔗 Video URL contains invalid characters. Please check your link.")
        
        if "soundcloud.com" in video_url.lower():
            await interaction.response.send_message(
                "Looks like you provided a SoundCloud link for the **video source**!\n"
                "SoundCloud links should go in the **audio source** field instead.\n"
                "Please try again with a video link (YouTube, etc.) for the video source.",
                ephemeral=True
            )
            return     
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for resync to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video_url", video_url)
        form.add_field("token", interaction.token)
        form.add_field("application_id", str(interaction.application_id))
        form.add_field("interaction_id", str(interaction.id))
        form.add_field("message_id", str(msg.id))
        form.add_field("user_id", str(interaction.user.id))
        form.add_field("video_start", video_start_input)
        form.add_field("video_end", video_end_input)
        form.add_field("X-Resync-Secret", Config.RESYNC_API_SECRET)
        
        if sfx_file:
            sfx_bytes = await sfx_file.read()
            form.add_field("sfx_file", sfx_bytes, filename="sfx.mp3")
        # Define the async job
        async def job():
            await run_resync_job(
                bot=bot,
                interaction=interaction,
                msg=msg,
                form=form,
                api_endpoint=f"{get_url()}/resyncrandommedia",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source="random database track",
                video_source=video_url,
                usage_type="random_resync",
                show_promo=True
            )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"resyncrandommedia_{interaction.id}")