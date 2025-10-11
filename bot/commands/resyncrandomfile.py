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
resyncrandomfile.py

Defines the `/resyncrandomfile` slash command for the Discord bot.

Functionality:
    - Accepts an uploaded video (`.mp4`).
    - Replaces the video's audio track with a random track from the "tracks" table in ResyncBot database.
    - Sends the files and metadata to the backend Flask API (`/resyncrandomfile`) for processing.
"""

def setup_resyncrandomfile(bot: commands.Bot):
    """
    Registers the /resyncrandomfile slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="resyncrandomfile",
        description="Attempts to resync your edit to a random audio!"
    )
    @app_commands.describe(
        video="The video file in .mp4 format.",
        sfx_file="Optional SFX file to overlay on the final result",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30)",
    )
    @handle_command_error
    async def resyncrandomfile(
        interaction: discord.Interaction,
        video: discord.Attachment,
        sfx_file: discord.Attachment = None,
        video_start_input: str = "0",
        video_end_input: str = "0",
    ):
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

        # Validate video file size with manual logging
        if video.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Video file too large: {video.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Video file too large (max {max_mb}MB)"
            )
        
        # Validate video file type with manual logging
        if not video.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            raise ValidationError(
                f"Invalid video format: {video.filename}",
                "📁 Please upload a valid video file (.mp4, .mov, .avi, .mkv)"
            )
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for resync to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video", await video.read(), filename=video.filename, content_type="video/mp4")
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
                api_endpoint=f"{get_url()}/resyncrandomfile",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source="random database track",
                video_source=video.filename,
                usage_type="random_resync",
                show_promo=True
            )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"resyncrandomfile_{interaction.id}")