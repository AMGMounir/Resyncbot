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
resyncmp4.py

Defines the `/resyncmp4` slash command for the Discord bot.

Functionality:
    - Accepts an uploaded video file (.mp4) and an audio source via SoundCloud or direct MP3 URL.
    - Replaces the video’s audio track with the provided URL-based audio.
    - Begins the new audio from a user-defined offset (e.g. "0:30").
    - Defers interaction to prevent timeouts and notifies the user that the job is queued.
    - Submits a media processing job to the Flask backend via `/resyncmp4` endpoint using a background queue.

This command is useful when the user has a video file and wants to resync it using online audio.
"""

def setup_resyncmp4(bot: commands.Bot):
    """
    Registers the /resyncmp4 slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="resyncmp4",
        description="Replace video audio with a SoundCloud or MP3 URL and start audio from a specific timestamp."
    )
    @app_commands.describe(
        video="The video file in .mp4 format.",
        audio_url="A SoundCloud or direct .mp3 URL.",
        sfx_file="Optional SFX file to overlay on the final result",
        audio_start_input="Where the audio should start (e.g. 0:30)",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30, optional)"
    )
    @handle_command_error
    async def resyncmp4(
        interaction: discord.Interaction,
        video: discord.Attachment,
        audio_url: str,
        sfx_file: discord.Attachment = None,
        audio_start_input: str = "0",
        video_start_input: str = "0",
        video_end_input: str = "0"
    ):
        """
        Executes the /resyncmp4 command.

        Behavior:
            - Defers the command response to avoid timeouts.
            - Notifies the user that the job is queued.
            - Packages the uploaded video and audio URL into a FormData payload.
            - Submits a background job to the backend Flask API for audio replacement.

        Args:
            interaction (discord.Interaction): The command invocation context from Discord.
            video (discord.Attachment): The uploaded MP4 video file.
            audio_url (str): A direct or SoundCloud URL to the new audio.
            offset_seconds_input (str): A timestamp string representing the start time for the new audio.
        """
        log_recent_command(interaction.user.id, interaction.channel_id) 

        can_use, error_msg = premium_manager.check_rate_limits(interaction.user.id, "manual")
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

        # Validate file size using ValidationError
        if video.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"File too large: {video.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 File too large (max {max_mb}MB)"
            )
        
        # Validate audio URL
        if not audio_url or not audio_url.strip():
            raise ValidationError("Empty audio URL provided", "🔗 Please provide a valid audio URL")
        
        # Validate video file type
        if not video.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            raise ValidationError(
                f"Invalid video format: {video.filename}",
                "📁 Please upload a valid video file (.mp4, .mov, .avi, .mkv)"
            )
        
        # Send Initial response
        msg = await interaction.followup.send("🔄 Queued... waiting for resync to begin.")

        # Create FormData
        form = FormData()
        form.add_field("video", await video.read(), filename=video.filename, content_type="video/mp4")
        form.add_field("audio_url", audio_url)
        form.add_field("offset", audio_start_input)
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
            
        # Define job and enqueue
        async def job():
            await run_resync_job(
                bot=bot,
                interaction=interaction,
                msg=msg,
                form=form,
                api_endpoint=f"{get_url()}/resyncmp4",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source=audio_url,
                usage_type="manual",
                show_promo=True
            )

        await job_queue.put(job, interaction.user.id, f"resyncmp4_{interaction.id}")
