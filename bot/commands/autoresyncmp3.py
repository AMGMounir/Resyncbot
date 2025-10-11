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
autoresyncmp3.py

Defines the `/autoresyncmp3` slash command for the Discord bot.

Functionality:
    - Accepts an uploaded video and uploaded audio file.
    - Automatically finds the best sync point without user timestamp input.
    - Uses intelligent audio matching to align the tracks.
"""

def setup_autoresyncmp3(bot: commands.Bot):
    """
    Registers the /autoresyncmp3 slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="autoresyncmp3",
        description="Automatically resyncs your video with uploaded audio - no timestamps needed!"
    )
    @app_commands.describe(
        video="The video file in .mp4 format.",
        audio_file="The audio file (.mp3, .wav, .m4a, or video file with audio)",
        sync_method="How should I find the best sync point?",
        sfx_file="Optional SFX file to overlay on the final result",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30)",
    )
    @app_commands.choices(sync_method=[
        app_commands.Choice(name="🔊 Match Audio Waveform (Most Accurate)", value="waveform"),
        app_commands.Choice(name="🎵 Match Beat Patterns (Good for Music)", value="beat"),
        app_commands.Choice(name="🎯 Both Methods (Slowest but Best)", value="both")
    ])
    @handle_command_error
    async def autoresyncmp3(
        interaction: discord.Interaction,
        video: discord.Attachment,
        audio_file: discord.Attachment,
        sync_method: app_commands.Choice[str],
        sfx_file: discord.Attachment = None,
        video_start_input: str = "0",
        video_end_input: str = "0",
    ):
        log_recent_command(interaction.user.id, interaction.channel_id)
        
        can_use, error_msg = premium_manager.check_rate_limits(interaction.user.id, "auto_resync")
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

        # Validate video file size
        if video.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Video file too large: {video.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Video file too large (max {max_mb}MB)"
            )
        
        # Validate audio file size
        if audio_file.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Audio file too large: {audio_file.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Audio file too large (max {max_mb}MB)"
            )
        
        # Validate video file type
        if not video.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            raise ValidationError(
                f"Invalid video format: {video.filename}",
                "📁 Please upload a valid video file (.mp4, .mov, .avi, .mkv)"
            )
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for auto-resync to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video", await video.read(), filename=video.filename, content_type="video/mp4")
        form.add_field("audio_file", await audio_file.read(), filename=audio_file.filename)
        form.add_field("token", interaction.token)
        form.add_field("application_id", str(interaction.application_id))
        form.add_field("interaction_id", str(interaction.id))
        form.add_field("message_id", str(msg.id))
        form.add_field("user_id", str(interaction.user.id))
        form.add_field("video_start", video_start_input)
        form.add_field("video_end", video_end_input)
        form.add_field("sync_method", sync_method.value)
        form.add_field("auto_sync", "true")
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
                api_endpoint=f"{get_url()}/autoresyncmp3",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source=audio_file.filename,
                video_source=video.filename,
                usage_type="auto_resync",
                show_promo=True,
            )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"autoresyncmp3_{interaction.id}")