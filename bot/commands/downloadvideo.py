import discord
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
downloadvideo.py

Defines the `/downloadvideo` slash command for the Discord bot.

Functionality:
    - Accepts a video URL (YouTube, TikTok, etc.).
    - Downloads the video in high quality without compression.
    - Sends the URL to the backend Flask API (`/downloadvideo`) for processing.
"""

def setup_downloadvideo(bot: commands.Bot):
    """
    Registers the /downloadvideo slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="downloadvideo",
        description="Download video from YouTube, TikTok, etc. in high quality"
    )
    @app_commands.describe(
        video_url="The video URL (YouTube, TikTok, etc.)",
        start_time="Optional: Start time for trimming (e.g. 0:30)",
        end_time="Optional: End time for trimming (e.g. 2:15)",
        quality="Video quality preference (default: best available)"
    )
    @app_commands.choices(quality=[
        app_commands.Choice(name="Best Available", value="best"),
        app_commands.Choice(name="1080p", value="1080p"),
        app_commands.Choice(name="720p", value="720p"),
        app_commands.Choice(name="480p", value="480p")
    ])
    @handle_command_error
    async def downloadvideo(
        interaction: discord.Interaction,
        video_url: str,
        start_time: str = "0",
        end_time: str = "0",
        quality: str = "best"):
        
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


        if video_url.lower() in [
            "https://www.tiktok.com/", 
            "https://www.tiktok.com", 
            "https://youtube.com/", 
            "https://youtube.com",
            "https://www.youtube.com/",
            "https://www.youtube.com"
        ]:
            raise ValidationError(
                "Incomplete URL",
                "🔗 You provided just the website URL, not a specific video link!\n"
                "Please copy the full video URL (like `https://www.tiktok.com/@user/video/123...`)"
            )

        # Check for TikTok URLs without video ID
        if "tiktok.com" in video_url.lower() and "/video/" not in video_url.lower():
            raise ValidationError(
                "Invalid TikTok URL",
                "🎵 This doesn't look like a TikTok video link!\n"
                "Make sure you copy the full URL that includes `/video/` and the video ID."
            )

        # Check for YouTube URLs without video ID
        if any(domain in video_url.lower() for domain in ["youtube.com", "youtu.be"]) and not any(param in video_url for param in ["?v=", "/watch?", "youtu.be/"]):
            raise ValidationError(
                "Invalid YouTube URL", 
                "🎵 This doesn't look like a YouTube video link!\n"
                "Make sure you copy the full video URL, not just the YouTube homepage."
            )
        
        for link, message in Config.INVALID_LINK_MESSAGES.items():
            print(f"Iterating through video links..")
            if link in video_url.lower():
                raise ValidationError("Blacklisted link", message)

        # Validate video URL
        if not video_url.strip():
            raise ValidationError("Empty video URL", "🎥 Please provide a video URL")
        
        # Basic URL validation
        if not video_url.startswith(("http://", "https://")):
            raise ValidationError("Invalid video URL format", "🎥 Please provide a valid video URL starting with http:// or https://")
        
        # Check for obviously invalid URLs
        if any(char in video_url for char in [" ", "<", ">", '"']):
            raise ValidationError("Invalid video URL characters", "🎥 Video URL contains invalid characters. Please check your link.")
        
        # Initial followup message
        msg = await interaction.followup.send("🔄 Queued... waiting for video download to begin.")

        # Prepare FormData
        form = FormData()
        form.add_field("video_url", video_url)
        form.add_field("start_time", start_time)
        form.add_field("end_time", end_time)
        form.add_field("quality", quality)
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
                await msg.edit(content="Starting Video Download...")

                api_result = await post_to_resync_api(f"{get_url()}/downloadvideo", form, headers={"X-Resync-Secret": Config.RESYNC_API_SECRET},)
                output_bytes, temp_path, error, track_info, audio_offset, filename = api_result

                if error:
                    await msg.edit(content=f"❌ Video Download Error: {error}")
                    safe_log_command(
                        bot, interaction, "downloadvideo",
                        {"video_url": video_url},
                        status="fail",
                        error=error
                    )
                    return

                # Create embed for the result
                embed = discord.Embed(
                    description=f"🎥 <@{interaction.user.id}> used `/downloadvideo`\n[🎥 Video](<{video_url}>)",
                    color=discord.Color.blue()
                )

                # Send the video file
                if output_bytes:
                    import io
                    file = discord.File(io.BytesIO(output_bytes), filename=filename)
                else:
                    # This should not happen, but fallback just in case
                    await msg.edit(content="❌ No video data received from API")
                    return

                try:
                    await msg.edit(
                        content=f"<@{interaction.user.id}> ✅ Video download complete!",
                        embed=embed,
                        attachments=[file]
                    )
                except Exception as e:
                    logger.error(f"[❌ Upload Failed] {e}")
                    await msg.edit(content=f"⚠️ Video download succeeded, but failed to upload: `{e}`")

                safe_log_command(
                    bot, interaction, "downloadvideo",
                    {"video_url": video_url},
                    status="success"
                )

            except Exception as e:
                logger.error(f"❌ Unexpected error in download video job: {e}")
                try:
                    await msg.edit(content=f"❌ Unexpected error: {str(e)}")
                except Exception:
                    pass

                safe_log_command(
                    bot, interaction, "downloadvideo",
                    {"video_url": video_url},
                    status="fail",
                    error=str(e)
                )

        # Enqueue job
        await job_queue.put(job, interaction.user.id, f"downloadvideo_{interaction.id}")