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
from config import Config
from backend.error_handler import handle_command_error, ValidationError
from backend.premium_utils import premium_manager

"""
autoresyncmedia.py

Defines the `/autoresyncmedia` slash command for the Discord bot.

Functionality:
    - Accepts a video URL and audio URL.
    - Automatically finds the best sync point without user timestamp input.
    - Uses intelligent audio matching to align the tracks.
"""

def setup_autoresyncmedia(bot: commands.Bot):
    """
    Registers the /autoresyncmedia slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="autoresyncmedia",
        description="Automatically resyncs video URL with audio URL - no timestamps needed!"
    )
    @app_commands.describe(
        video_url="The video URL (YouTube, Streamable, etc.)",
        audio_url="The audio URL (SoundCloud, YouTube, etc.)",
        sync_method="How should I find the best sync point?",
        sfx_file="Optional SFX file to overlay on the final result",
        video_start_input="Where the video should start (e.g. 0:05)",
        video_end_input="Where the video should end (e.g. 0:30)"
    )
    @app_commands.choices(sync_method=[
        app_commands.Choice(name="🔊 Match Audio Waveform (Most Accurate)", value="waveform"),
        app_commands.Choice(name="🎵 Match Beat Patterns (Good for Music)", value="beat"),
        app_commands.Choice(name="🎯 Both Methods (Slowest but Best)", value="both")
    ])
    @handle_command_error
    async def autoresyncmedia(
        interaction: discord.Interaction,
        video_url: str,
        audio_url: str,
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

        # Validate video URL
        if not video_url.strip():
            raise ValidationError("Empty video URL", "🔗 Please provide a video URL")
        if not video_url.startswith(("http://", "https://")):
            raise ValidationError("Invalid video URL format", "🔗 Please provide a valid video URL starting with http:// or https://")
        
        # Validate audio URL
        if not audio_url.strip():
            raise ValidationError("Empty audio URL", "🎵 Please provide an audio URL")  
        if not audio_url.startswith(("http://", "https://")):
            raise ValidationError("Invalid audio URL format", "🎵 Please provide a valid audio URL starting with http:// or https://")
        
        if "soundcloud.com" in video_url.lower():
            await interaction.response.send_message(
                "Looks like you provided a SoundCloud link for the **video source**!\n"
                "SoundCloud links should go in the **audio source** field instead.\n"
                "Please try again with a video link (YouTube, etc.) for the video source.",
                ephemeral=True
            )
            return     

        # Check for obviously invalid URLs
        if any(char in video_url for char in [" ", "<", ">", '"']):
            raise ValidationError("Invalid video URL characters", "🔗 Video URL contains invalid characters. Please check your link.")          
        if any(char in audio_url for char in [" ", "<", ">", '"']):
            raise ValidationError("Invalid audio URL characters", "🎵 Audio URL contains invalid characters. Please check your link.")
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for auto-resync to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video_url", video_url)
        form.add_field("audio_url", audio_url)
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
                api_endpoint=f"{get_url()}/autoresyncmedia",
                headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},
                user_id=interaction.user.id,
                command_name=interaction.command.name,
                audio_source=audio_url,
                video_source=video_url,
                usage_type="auto_resync",
                show_promo=True,
            )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"autoresyncmedia_{interaction.id}")