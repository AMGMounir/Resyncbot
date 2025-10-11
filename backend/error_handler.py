import logging
from typing import Union, Optional
import discord
from functools import wraps
import traceback

logger = logging.getLogger("ResyncBot")

class BotError(Exception):
    """Base exception for bot-specific errors"""
    def __init__(self, message: str, user_message: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.user_message = user_message or message

class ValidationError(BotError):
    """Error for input validation failures"""
    pass

class ProcessingError(BotError):
    """Error during media processing"""
    pass

class DownloadError(BotError):
    """Error during media download"""
    pass

def format_user_error(error: Exception) -> str:
    """Format errors for user-friendly display"""
    error_str = str(error).lower()

    logger.info(f"Error_str: {error_str}")
    if 'cannot connect to host' in error_str or 'fly.dev' in error_str:
        return "⚙️ Processing media... (this may take a moment)"
    
    error_messages = {
        "cookies_expired": "🍪 My access cookies have expired. Please try `/resyncmp4` or `/resyncmp3` instead!",
        "file_too_large": "🚫 File is too large (max 100MB). Please use a smaller file.",
        "invalid_timestamp": "⏰ Invalid timestamp format. Use format like `1:30` or `0:45`.",
        "audio_offset_exceeds": "📏 Audio start time is longer than the audio file duration.",
        "video_start_exceeds": "📏 Video start time is longer than the video duration.",
        "download_failed": "📥 Failed to download media. Please check your URLs and try again.",
        "processing_failed": "⚙️ Media processing failed. Please try with different files.",
        "invalid_url": "🔗 Invalid or unsupported URL. Please check your link.",
        "rate_limited": "⏳ Too many requests. Please wait a moment before trying again.",
    }
    
    error_str = str(error).lower()
    for key, message in error_messages.items():
        if key in error_str:
            return message
    
    return f"⚠️ An unexpected error occurred: {str(error)[:100]}..."


def handle_command_error(func):
    """Decorator for consistent command error handling"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        interaction = args[0]  # First arg should be interaction

        bot = getattr(interaction, 'client', None) or getattr(interaction, '_state', {}).get('client', None)

        try:
            return await func(*args, **kwargs)

        except BotError as e:
            # Log the error
            if bot:
                try:
                    from backend.command_logger import safe_log_command
                    command_data = {}
                    if hasattr(interaction, 'data') and interaction.data:
                        options = interaction.data.get('options', [])
                        for option in options:
                            if option['name'] in ['video_url', 'audio_url']:
                                command_data[option['name']] = option['value'][:100]
                            elif option['name'] in ['video', 'audio']:
                                command_data[option['name']] = getattr(option.get('value'), 'filename', 'unknown')

                    await safe_log_command(bot, interaction, func.__name__, command_data, status="fail", error=str(e))
                except Exception as log_error:
                    logger.warning(f"Failed to log command error: {log_error}")

            # Send to user
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(e.user_message, ephemeral=True)
                else:
                    await interaction.response.send_message(e.user_message, ephemeral=True)
            except discord.InteractionResponded:
                try:
                    await interaction.followup.send(e.user_message, ephemeral=True)
                except Exception as followup_error:
                    logger.error(f"Failed to send error message: {followup_error}")

            logger.warning(f"Bot error in {func.__name__}: {e}")

        except discord.HTTPException as e:
            error_msg = "⏳ Rate limited. Please try again in a few seconds." if e.status == 429 else "❌ Discord API error. Please try again."

            try:
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(error_msg, ephemeral=True)
            except Exception:
                pass

            logger.error(f"Discord error in {func.__name__}: {e}")

        except Exception as e:
            logger.error(f"[ERROR_HANDLER] Caught exception: {type(e).__name__}: {e}")
            logger.error(f"[ERROR_HANDLER] Error string: {str(e)}")
            error_msg = format_user_error(e)
            logger.error(f"[ERROR_HANDLER] Formatted message: {error_msg}")
            
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(error_msg, ephemeral=True)
            except Exception:
                pass

            logger.error(f"Unexpected error in {func.__name__}: {e}")
            logger.error(traceback.format_exc())

    return wrapper

async def _log_command_error(interaction, command_name: str, error: Exception, error_type: str):
    """Internal helper to log command errors"""
    try:
        from backend.command_logger import safe_log_command
        
        command_data = {}
        if hasattr(interaction, 'data') and interaction.data:
            options = interaction.data.get('options', [])
            for option in options:
                if option['name'] in ['video_url', 'audio_url']:
                    command_data[option['name']] = option['value'][:100]
                elif option['name'] in ['video', 'audio']:
                    command_data[option['name']] = getattr(option.get('value'), 'filename', 'unknown')
        
        await safe_log_command(
            None,
            interaction, 
            command_name, 
            command_data,
            status="fail", 
            error=str(error)
        )
    except Exception as log_error:
        logger.warning(f"Failed to log command error: {log_error}")

async def safe_send_message(channel, content: str = None, embed: discord.Embed = None, file: discord.File = None) -> bool:
    """Safely send a message with error handling"""
    try:
        await channel.send(content=content, embed=embed, file=file)
        return True
    except discord.Forbidden:
        logger.warning(f"Missing permissions to send message in channel {channel.id}")
    except discord.HTTPException as e:
        logger.warning(f"HTTP error sending message: {e}")
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
    
    return False