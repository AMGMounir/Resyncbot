import discord
import io
from aiohttp import FormData
from discord import app_commands
from discord.ext import commands
from bot.utils import get_url
from backend.resync_queue import job_queue
from backend.video_utils import (
    post_to_resync_api,
    logger,
    format_resync_error,
    safe_cleanup
)
from backend.command_logger import safe_log_command
from backend.recent_usage import log_recent_command
from config import Config
from backend.error_handler import handle_command_error, ValidationError
from backend.premium_utils import premium_manager

"""
downloadaudio.py

Defines the `/downloadaudio` slash command for the Discord bot.

Functionality:
    - Accepts an audio URL (YouTube, SoundCloud, Spotify, etc.).
    - Downloads the audio in high quality MP3 format.
    - Sends the URL to the backend Flask API (`/downloadaudio`) for processing.
"""

def setup_downloadaudio(bot: commands.Bot):
    """
    Registers the /downloadaudio slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="downloadaudio",
        description="Download audio from YouTube, SoundCloud, Spotify, etc. as MP3"
    )
    @app_commands.describe(
        audio_url="The audio URL (YouTube, SoundCloud, Spotify, etc.)",
        start_time="Optional: Start time for trimming (e.g. 0:30)",
        end_time="Optional: End time for trimming (e.g. 2:15)"
    )
    @handle_command_error
    async def downloadaudio(
        interaction: discord.Interaction,
        audio_url: str,
        start_time: str = "0",
        end_time: str = "0"):
        
        log_recent_command(interaction.user.id, interaction.channel_id)

        can_use, error_msg = premium_manager.check_rate_limits(interaction.user.id, "download")
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

        # Validate audio URL
        if not audio_url.strip():
            raise ValidationError("Empty audio URL", "🎵 Please provide an audio URL")
        
        # Basic URL validation
        if not audio_url.startswith(("http://", "https://")):
            raise ValidationError("Invalid audio URL format", "🎵 Please provide a valid audio URL starting with http:// or https://")
        
        # Check for obviously invalid URLs
        if any(char in audio_url for char in [" ", "<", ">", '"']):
            raise ValidationError("Invalid audio URL characters", "🎵 Audio URL contains invalid characters. Please check your link.")
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for audio download to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("audio_url", audio_url)
        form.add_field("start_time", start_time)
        form.add_field("end_time", end_time)
        form.add_field("token", interaction.token)
        form.add_field("application_id", str(interaction.application_id))
        form.add_field("interaction_id", str(interaction.id))
        form.add_field("message_id", str(msg.id))
        form.add_field("user_id", str(interaction.user.id))
        form.add_field("X-Resync-Secret", Config.RESYNC_API_SECRET)

        # Define the async job
        async def job():
            temp_path = None
            try:
                await msg.edit(content="Starting Audio Download...")

                api_result = await post_to_resync_api(f"{get_url()}/downloadaudio", form, headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},)
                output_bytes, temp_path, error, track_info, audio_offset, filename = api_result

                if error:
                    await msg.edit(content=f"❌ Audio Download Error: {format_resync_error(error)}")
                    safe_log_command(
                        bot, interaction, "downloadaudio",
                        {"audio_url": audio_url},
                        status="fail",
                        error=error
                    )
                    return

                # Create embed for the result
                embed = discord.Embed(
                    description=f"🎵 <@{interaction.user.id}> used `/downloadaudio`\n[🎵 Audio](<{audio_url}>)",
                    color=discord.Color.green()
                )

                # Send the audio file
                file = discord.File(io.BytesIO(output_bytes), filename=filename)

                try:
                    await msg.edit(
                        content=f"<@{interaction.user.id}> ✅ Audio download complete!",
                        embed=embed,
                        attachments=[file]
                    )
                except Exception as e:
                    logger.error(f"[❌ Upload Failed] {e}")
                    await msg.edit(content=f"⚠️ Audio download succeeded, but failed to upload: `{e}`")

                safe_log_command(
                    bot, interaction, "downloadaudio",
                    {"audio_url": audio_url},
                    status="success"
                )

            except Exception as e:
                logger.error(f"❌ Unexpected error in download audio job: {e}")
                try:
                    await msg.edit(content=f"❌ Unexpected error: {str(e)}")
                except Exception:
                    pass

                safe_log_command(
                    bot, interaction, "downloadaudio",
                    {"audio_url": audio_url},
                    status="fail",
                    error=str(e)
                )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"downloadaudio_{interaction.id}")