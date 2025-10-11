import discord
from aiohttp import FormData
from discord import app_commands
from discord.ext import commands
from bot.utils import get_url
from backend.resync_queue import job_queue
from backend.video_utils import (
    run_resync_job,
    logger,
    process_audio_loop_direct,
    format_user_error
)
from backend.command_logger import safe_log_command
from backend.recent_usage import log_recent_command
from config import Config
from backend.error_handler import handle_command_error, ValidationError, ProcessingError
from backend.premium_utils import premium_manager

"""
loopaudio.py

Defines the `/loopaudio` slash command for the Discord bot.

Functionality:
    - Accepts an uploaded audio file (.mp3).
    - Takes start and end time parameters.
    - Returns the audio segment looped 5 times for inspiration/editing purposes.
"""

def setup_loopaudio(bot: commands.Bot):
    """
    Registers the /loopaudio slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    @bot.tree.command(
        name="loopaudio",
        description="Loop a section of your audio 5 times for editing inspiration!"
    )
    @app_commands.describe(
        audio_file="The audio file (.mp3, .wav, .m4a, etc.)",
        start_time="When to start the loop (e.g. 1:23)",
        end_time="When to end the loop (e.g. 1:45)",
        loop_count="How many times to loop (default: 5, max: 10)"
    )
    async def loopaudio(
        interaction: discord.Interaction,
        audio_file: discord.Attachment,
        start_time: str,
        end_time: str,
        loop_count: int = 5
    ):
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

        # Validate audio file size
        if audio_file.size > Config.MAX_FILE_SIZE:
            max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
            raise ValidationError(
                f"Audio file too large: {audio_file.size} bytes (max: {Config.MAX_FILE_SIZE})",
                f"🚫 Audio file too large (max {max_mb}MB)"
            )
        
        # Validate loop count
        if loop_count < 1 or loop_count > 10:
            raise ValidationError(
                f"Invalid loop count: {loop_count}",
                "🔄 Loop count must be between 1 and 10"
            )
        
        # Validate time parameters
        if not start_time or not end_time:
            raise ValidationError(
                "Missing time parameters",
                "⏰ Please provide both start and end times"
            )

        # Process the audio looping directly
        try:
            msg = await interaction.followup.send("🔄 Processing audio loop...")
            
            await process_audio_loop_direct(
                bot=bot,
                interaction=interaction,
                msg=msg,
                audio_file=audio_file,
                start_time=start_time,
                end_time=end_time,
                loop_count=loop_count
            )
            
        except (ValidationError, ProcessingError):
            # Re-raise these to be handled by the error handler decorator
            raise
        except Exception as e:
            logger.exception("LoopAudio failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))