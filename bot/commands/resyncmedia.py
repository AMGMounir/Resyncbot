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
from backend.recent_usage import log_recent_command
from backend.command_logger import safe_log_command
from backend.error_handler import handle_command_error, ValidationError
from backend.premium_utils import premium_manager
from config import Config

"""
resyncmedia.py

This module defines the `/resyncmedia` slash command for the Discord bot.

Functionality:
    - Accepts a video URL (YouTube, Instagram, Streamable, etc.) and a SoundCloud audio URL.
    - Replaces the video's audio with the provided audio, starting at a user-defined offset.
    - Submits a background job to the media processing queue and informs the user once processing starts.
"""

def setup_resyncmedia(bot: commands.Bot):
    """
    Registers the /resyncmedia command to the given Discord bot.

    Args:
        bot (commands.Bot): The instance of the Discord bot.
    """
    @bot.tree.command(
            name="resyncmedia", 
            description="Replace video audio using YouTube/IG/Streamable + SoundCloud."
            )
    @app_commands.describe(
        video_url="A link to the video (yt, ig, streamable, etc).",
        audio_url="A SoundCloud or direct MP3 URL.",
        sfx_file="Optional SFX file to overlay on the final result",
        audio_start_input="Where the audio should start (e.g. 0:15)",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30, optional)"
    )
    @handle_command_error
    async def resyncmedia(
        interaction: discord.Interaction,
        video_url: str,
        audio_url: str,
        sfx_file: discord.Attachment = None,
        audio_start_input: str = "0",
        video_start_input: str = "0",
        video_end_input: str = "0"
    ):
        """
        Handles the /resyncmedia command execution.

        This command downloads a video and audio from the provided links,
        and queues a background job to replace the video’s audio track
        starting at the given offset time.

        Args:
            interaction (discord.Interaction): The Discord interaction context.
            video_url (str): The URL to the input video (e.g., YouTube).
            audio_url (str): The SoundCloud audio URL to be used.
            offset_seconds_input (str): Optional time offset (default is "0").
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
            if e.code != 10062:
                logger.warning(f"⚠️ Failed to defer interaction: {e}")

        # Validate video URL with manual logging
        if not video_url or not video_url.strip():
            safe_log_command(bot, interaction, "resyncmedia", {
                "video_url": video_url,
                "audio_url": audio_url[:100] if audio_url else ""
            }, status="fail", error="Empty video URL")
            
            raise ValidationError("Empty video URL provided", "🔗 Please provide a valid video URL")
        
        # Validate audio URL with manual logging
        if not audio_url or not audio_url.strip():
            safe_log_command(bot, interaction, "resyncmedia", {
                "video_url": video_url[:100],
                "audio_url": audio_url
            }, status="fail", error="Empty audio URL")
            
            raise ValidationError("Empty audio URL provided", "🎵 Please provide a valid audio URL")
        
        if "soundcloud.com" in video_url.lower():
            await interaction.response.send_message(
                "Looks like you provided a SoundCloud link for the **video source**!\n"
                "SoundCloud links should go in the **audio source** field instead.\n"
                "Please try again with a video link (YouTube, etc.) for the video source.",
                ephemeral=True
            )
            return        

        # Basic URL format validation with manual logging
        if not (video_url.startswith("http://") or video_url.startswith("https://")):
            safe_log_command(bot, interaction, "resyncmedia", {
                "video_url": video_url[:100],
                "audio_url": audio_url[:100]
            }, status="fail", error="Invalid video URL format")
            
            raise ValidationError(
                f"Invalid video URL format: {video_url}",
                "🔗 Please provide a valid URL starting with http:// or https://"
            )
        
        if not (audio_url.startswith("http://") or audio_url.startswith("https://")):
            safe_log_command(bot, interaction, "resyncmedia", {
                "video_url": video_url[:100],
                "audio_url": audio_url[:100]
            }, status="fail", error="Invalid audio URL format")
            
            raise ValidationError(
                f"Invalid audio URL format: {audio_url}",
                "🎵 Please provide a valid audio URL starting with http:// or https://"
            )
        
        msg = await interaction.followup.send("🔄 Queued... waiting for resync to begin.")

        form = FormData()
        form.add_field("video_url", video_url)
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
            
        async def job():
            await run_resync_job(
                bot=bot,
                interaction=interaction,
                msg=msg,
                form=form,
                api_endpoint=f"{get_url()}/resyncmedia",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source=audio_url,
                video_source=video_url,
                usage_type="manual",
                show_promo=True
            )


        await job_queue.put(job, interaction.user.id, f"resyncmedia_{interaction.id}")