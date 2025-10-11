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
resyncmp3.py

Defines the `/resyncmp3` slash command for the Discord bot.

Functionality:
    - Accepts an uploaded video (`.mp4`) and audio file (`.mp3`).
    - Replaces the video's audio track with the uploaded `.mp3` starting at a user-defined offset.
    - Sends the files and metadata to the backend Flask API (`/resyncmp3`) for processing.
"""
def setup_resyncmp3(bot: commands.Bot):
    """
    Registers the /resyncmp3 slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="resyncmp3",
        description="Replace video audio with an uploaded .mp3 file, starting from a given second."
    )
    @app_commands.describe(
        video="The video file in .mp4 format.",
        audio="The audio file in .mp3 format.",
        sfx_file="Optional SFX file to overlay on the final result",
        audio_start_input="Where the audio should start (e.g. 1:25)",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30, optional)"
    )
    @handle_command_error
    async def resyncmp3(
        interaction: discord.Interaction,
        video: discord.Attachment,
        audio: discord.Attachment,
        sfx_file: discord.Attachment = None,
        audio_start_input: str = "0",
        video_start_input: str = "0",
        video_end_input: str = "0"
    ):
        """
        Executes the /resyncmp3 command.

        Behavior:
            - Defers the interaction to prevent timeout.
            - Sends a temporary status message to the user.
            - Packages the uploaded media and metadata into a FormData payload.
            - Enqueues a background job to call the backend API and process the media.

        Args:
            interaction (discord.Interaction): The context of the Discord command interaction.
            video (discord.Attachment): The uploaded MP4 video file.
            audio (discord.Attachment): The uploaded MP3 audio file.
            offset_seconds_input (str): Timestamp string indicating when to start the new audio.
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


        # Validate video file size with manual logging
        if video.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Video file too large: {video.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Video file too large (max {max_mb}MB)"
            )

        # Validate audio file size with manual logging
        if audio.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Audio file too large: {audio.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Audio file too large (max {max_mb}MB)"
            )
        
        # Validate video file type with manual logging
        if not video.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
            raise ValidationError(
                f"Invalid video format: {video.filename}",
                "📁 Please upload a valid video file (.mp4, .mov, .avi, .mkv)"
            )
        
        # Validate audio file type with manual logging
        valid_audio_extensions = ('.mp3', '.wav', '.m4a', '.aac', '.ogg', '.mp4', '.mov', '.avi', '.mkv')
        if not audio.filename.lower().endswith(valid_audio_extensions):
            raise ValidationError(
                f"Invalid audio format: {audio.filename}",
                "🎵 Please upload a valid audio file (.mp3, .wav, .m4a) or video file (audio will be extracted)"
            )
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for resync to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video", await video.read(), filename=video.filename, content_type="video/mp4")
        form.add_field("audio_file", await audio.read(), filename=audio.filename, content_type="audio/mpeg")
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
            
        # Define the async job
        async def job():
            await run_resync_job(
                bot=bot,
                interaction=interaction,
                msg=msg,
                form=form,
                api_endpoint=f"{get_url()}/resyncmp3",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source=audio.filename,
                usage_type="manual",
                show_promo=True
            )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"resyncmp3_{interaction.id}")

