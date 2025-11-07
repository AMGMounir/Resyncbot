import glob
import requests
import os
import logging
import subprocess
import discord
import aiohttp
import yt_dlp
from yt_dlp import YoutubeDL
import urllib.parse
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import asyncio
import io
from PIL import Image
from urllib.parse import urlparse
from typing import Optional
import time
import subprocess
from command_logger import safe_log_command
from config import Config
from error_handler import ProcessingError, format_user_error
import librosa
import tempfile
from error_handler import ValidationError
import traceback
from pathlib import Path
import numpy as np
import sys
import random
from premium_utils import premium_manager
from scipy import signal
import re

"""
VIDEO UTILITIES - Core Media Processing Functions

This module contains all the utility functions for downloading, processing, and manipulating
video and audio files. These are the building blocks used by the API endpoints.

MAIN CATEGORIES:
================

1. MEDIA DOWNLOAD:
   - download_audio() - Download audio from URLs (YouTube, SoundCloud, Spotify)
   - download_video_with_retry() - Download video with fallback attempts
   - download_tiktok_with_fallbacks() - TikTok-specific download logic
   - download_instagram_fallback() - Instagram fallback when yt-dlp fails

2. MEDIA PROCESSING:
   - combine_with_ffmpeg() - Combine video + audio (with optional watermark)
   - trim_video_ffmpeg() - Cut video to specific time range
   - trim_audio_ffmpeg() - Cut audio to specific time range
   - extract_audio_from_video() - Extract MP3 from video file

3. AUDIO ANALYSIS:
   - get_video_bpm() - Detect BPM/tempo of audio
   - find_best_audio_match() - Auto-sync using waveform correlation
   - find_best_beat_match() - Auto-sync using beat patterns

4. DATABASE OPERATIONS:
   - find_matching_tracks() - Find tracks in DB by BPM
   - download_audio_from_database() - Download specific track from DB

5. HELPERS:
   - parse_timestamp() - Convert "1:23" to seconds
   - get_duration() - Get media file duration
   - is_valid_video_file() - Validate video files
   - safe_cleanup() - Delete temporary files

IMPORTANT NOTES:
================
- All functions use FFmpeg for video/audio processing
- Temporary files are stored in /tmp/ and must be cleaned up
- yt-dlp is used for downloading from YouTube, SoundCloud, etc.
- Cookies (cookies.txt) are required for age-restricted content
- All paths should be absolute to avoid issues in production

DEPENDENCIES:
=============
- FFmpeg (system binary)
- yt-dlp (YouTube downloader)
- librosa (audio analysis, BPM detection)
- scipy (signal processing for sync detection)
- PIL/Pillow (image processing)
- PostgreSQL (for track database queries)
"""

BACKEND_DIR = Path(__file__).parent.absolute()
PROJECT_ROOT = BACKEND_DIR.parent
BACKEND_PATH = PROJECT_ROOT / "backend"

_last_progress_update = 0

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ResyncBot")

if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

def get_video_resolution(path: str) -> tuple[int, int]:
    """Returns (width, height) of the video using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                path
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        width, height = result.stdout.decode().strip().split("x")
        return int(width), int(height)
    except Exception as e:
        logger.warning(f"[⚠️] Failed to get video resolution: {e}")
        return (0, 0)

def parse_timestamp(ts: str) -> float:
    """Parses a timestamp string (e.g. '1:23' or '00:01:23') into total seconds as float."""
    try:
        parts = ts.strip().split(":")
        parts = [float(p) for p in parts]
        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return parts[0] * 60 + parts[1]
        elif len(parts) == 3:
            return parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError("Too many colon separators.")
    except Exception:
        raise ValueError("Invalid timestamp format.")

def parse_offset_string(offset_input: str) -> float:
    """
    Supports either a single timestamp ('0:30') or a subtractive format ('2:12-1:32').
    Returns: float: Calculated offset in seconds.
    """
    try:
        if "-" in offset_input:
            video_ts, audio_ts = offset_input.split("-")
            return abs(parse_timestamp(video_ts) - parse_timestamp(audio_ts))
        return parse_timestamp(offset_input)
    except Exception:
        raise ValueError("Invalid offset format. Use 'mm:ss' or 'mm:ss-mm:ss'.")

def combine_with_ffmpeg(video_path, audio_path, output_path, sfx_path=None, user_id=None):
    """
    Combines video, audio, and optional SFX in one FFmpeg pass with watermark for non-premium users
    
    WATERMARK LOGIC (Currently disabled):
    - Premium users: No watermark (clean video)
    - Free users: "ResyncBot" watermark in bottom-right corner
    - If watermark fails: Automatically retries without watermark (better to give clean video than fail)
    
    SFX MIXING:
    - SFX is mixed at 60% volume, main audio at 80%
    - Both are combined using FFmpeg's amix filter
    
    VIDEO PROCESSING:
    - Premium/no watermark: Video stream is copied (fast, no quality loss)
    - With watermark: Video is re-encoded with libx264 (slower but needed for overlay)
    """
    
    # Determine if watermark should be added
    add_watermark = Config.PREMIUM_ENABLED
    if user_id and premium_manager.is_premium_user(user_id):
        add_watermark = False
        logger.info(f"🏆 Premium user {user_id} - no watermark")
    else:
        logger.info(f"🔖 Adding watermark for user {user_id}")
    
    # Validate all input files exist
    if not os.path.exists(video_path):
        logger.error(f"❌ Video file missing: {video_path}")
        raise ProcessingError("Video file missing", "❌ Video file disappeared during processing")
    
    if not os.path.exists(audio_path):
        logger.error(f"❌ Audio file missing: {audio_path}")
        raise ProcessingError("Audio file missing", "❌ Audio file disappeared during processing")
    
    if sfx_path and not os.path.exists(sfx_path):
        logger.warning(f"⚠️ SFX file specified but missing: {sfx_path}")
        sfx_path = None  # Disable SFX if file doesn't exist
    
    # Ensure output directory exists and is writable
    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"📁 Created output directory: {output_dir}")
        except Exception as e:
            logger.error(f"❌ Cannot create output directory: {e}")
            raise ProcessingError(f"Cannot create output directory: {e}", "❌ File system error")
    
    # Log input file details
    video_size = os.path.getsize(video_path)
    audio_size = os.path.getsize(audio_path)
    logger.info(f"🔧 Video: {video_path} ({video_size} bytes)")
    logger.info(f"🔧 Audio: {audio_path} ({audio_size} bytes)")
    logger.info(f"🔧 Output: {output_path}")
    if sfx_path:
        sfx_size = os.path.getsize(sfx_path)
        logger.info(f"🔧 SFX: {sfx_path} ({sfx_size} bytes)")
    
    # Build FFmpeg command based on inputs and watermark settings
    command = ["ffmpeg", "-y"]
    
    command.extend(["-i", video_path])
    command.extend(["-i", audio_path])
    if sfx_path:
        command.extend(["-i", sfx_path])
    
    # Build filter complex and mapping
    if sfx_path:
        # Three inputs: video + audio + sfx
        if add_watermark:
            # Mix audio + add watermark
            filter_complex = (
                "[1:a][2:a]amix=inputs=2:duration=first:weights=0.8 0.6,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[mixed];"
                "[0:v]drawtext=text='ResyncBot':fontcolor=white@0.7:fontsize=h/25:x=w-tw-20:y=h-th-20[watermarked]"
            )
            command.extend(["-filter_complex", filter_complex])
            command.extend(["-map", "[watermarked]", "-map", "[mixed]"])
            command.extend(["-c:v", "libx264", "-preset", "veryfast"])
        else:
            # Mix audio only, copy video
            filter_complex = "[1:a][2:a]amix=inputs=2:duration=first:weights=0.8 0.6,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[mixed]"
            command.extend(["-filter_complex", filter_complex])
            command.extend(["-map", "0:v:0", "-map", "[mixed]"])
            command.extend(["-c:v", "copy"])
    else:
        # Two inputs: video + audio
        if add_watermark:
            # Add watermark to video
            filter_complex = "[0:v]drawtext=text='ResyncBot':fontcolor=white@0.7:fontsize=h/25:x=w-tw-20:y=h-th-20[watermarked]"
            command.extend(["-filter_complex", filter_complex])
            command.extend(["-map", "[watermarked]", "-map", "1:a:0"])
            command.extend(["-c:v", "libx264", "-preset", "veryfast"])
        else:
            # Simple copy
            command.extend(["-map", "0:v:0", "-map", "1:a:0"])
            command.extend(["-c:v", "copy", "-c:a", "aac"])
    
    # Add common audio and output settings
    command.extend([
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        "-threads", "0"
    ])
    
    # Add output path
    command.append(output_path)
    
    # Log the full command for debugging
    logger.info(f"🔧 FFmpeg command: {' '.join(command)}")
    
    # Execute FFmpeg
    try:
        logger.info(f"▶️ Running ffmpeg combine: {' '.join(command)}")
        result = subprocess.run(
            command, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            timeout=300  # 5 minute timeout
        )
        logger.info("✅ Combined video + audio")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ ffmpeg combine failed: {e.stderr.decode(errors='ignore')}")
        raise RuntimeError("ffmpeg combine failed") from e
    except subprocess.TimeoutExpired:
        logger.error("❌ FFmpeg process timed out")
        raise ProcessingError("FFmpeg timeout", "❌ Video processing took too long")
    except Exception as e:
        logger.error(f"❌ FFmpeg execution failed: {e}")
        raise ProcessingError(f"FFmpeg execution error: {e}", "❌ Failed to run video processor")
    
    # Check FFmpeg result
    stderr_output = result.stderr.decode() if result.stderr else ""
    
    if result.returncode != 0:
        stderr_output = result.stderr.decode() if result.stderr else ""
        
        # WATERMARK FALLBACK: If watermark fails (missing fonts, etc), retry without it
        # Better to give users a clean video than to fail completely
        # This can happen on some server configurations where fonts aren't installed
        if add_watermark and ("drawtext" in stderr_output or "fontfile" in stderr_output or "Invalid argument" in stderr_output):
            logger.warning("⚠️ Watermark failed, retrying without watermark...")
            
            # Retry without watermark - build simpler command
            retry_command = ["ffmpeg", "-y"]
            retry_command.extend(["-i", video_path, "-i", audio_path])
            
            if sfx_path:
                retry_command.extend(["-i", sfx_path])
                retry_command.extend([
                    "-filter_complex", "[1:a][2:a]amix=inputs=2:duration=first:weights=0.8 0.6,aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[mixed]",
                    "-map", "0:v:0", "-map", "[mixed]", "-c:v", "copy"
                ])
            else:
                retry_command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy"])
            
            retry_command.extend([
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                "-shortest", "-movflags", "+faststart", "-threads", "0", output_path
            ])
            
            # Try again without watermark
            retry_result = subprocess.run(retry_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
            
            if retry_result.returncode == 0 and os.path.exists(output_path):
                # Success! Set a flag so we can modify the embed later
                global watermark_failed_flag
                watermark_failed_flag = True
                logger.info("✅ FFmpeg combination successful without watermark (fallback)")
                return
            else:
                logger.error("❌ Even fallback without watermark failed")
    
    # Verify output file was created and is valid
    if not os.path.exists(output_path):
        logger.error(f"❌ Output file was not created: {output_path}")
        # List directory contents for debugging
        try:
            dir_contents = os.listdir(os.path.dirname(output_path))
            logger.error(f"❌ Directory contents: {dir_contents}")
        except:
            logger.error(f"❌ Could not list directory contents")
        raise ProcessingError("Output file not created", "❌ Failed to create output video file")
    
    # Check file size
    try:
        file_size = os.path.getsize(output_path)
        if file_size == 0:
            logger.error(f"❌ Output file is empty: {output_path}")
            safe_cleanup(output_path)  # Remove empty file
            raise ProcessingError("Empty output file created", "❌ Created video file is empty")
        
        if file_size < 1024:  # Less than 1KB is suspicious
            logger.warning(f"⚠️ Output file is very small: {file_size} bytes")
        
        logger.info(f"✅ FFmpeg success - output: {output_path} ({file_size:,} bytes)")
        
    except Exception as e:
        logger.error(f"❌ Could not verify output file: {e}")
        raise ProcessingError(f"Could not verify output: {e}", "❌ Failed to verify created video")
    
    # Final validation - try to get duration of output file
    try:
        output_duration = get_duration(output_path)
        if output_duration <= 0:
            logger.error(f"❌ Output video has invalid duration: {output_duration}")
            safe_cleanup(output_path)
            raise ProcessingError("Invalid output video", "❌ Created video file is corrupted")
        
        logger.info(f"✅ Output video validated - duration: {output_duration:.2f}s")
        
    except Exception as e:
        logger.warning(f"⚠️ Could not validate output duration: {e}")
        # Don't fail here - the file might still be valid
    
    watermark_status = "with watermark" if add_watermark else "without watermark"
    logger.info(f"✅ FFmpeg combination successful {watermark_status}")

def safe_cleanup(*paths):
    """Safely remove multiple file paths"""
    for path in paths:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

def create_loop_embed(user_id: int, command_name: str, audio_source: str, loop_info: dict = None) -> discord.Embed:
    """Creates a Discord embed for loopaudio command results."""
    
    # Format audio source
    if audio_source.endswith(('.mp3', '.wav', '.m4a')):
        audio_info = f"🎵 Audio: `{audio_source}`"
    else:
        audio_info = f"🎵 Audio: `{audio_source}`"

    description = f"📽️ <@{user_id}> used `{command_name}`\n{audio_info}"
    
    # Add loop information if provided
    if loop_info:
        loop_count = loop_info.get("loop_count", 5)
        segment_duration = loop_info.get("segment_duration", 0)
        description += f"\n🔄 Looped {loop_count}x ({segment_duration:.1f}s segment)"

    return discord.Embed(
        description=description,
        color=discord.Color.green()
    )

def create_resync_embed(user_id: int, command_name: str, audio_link: str, audio_offset: float = None, loop_info: dict = None, video_url: str = None, show_promo: bool = False, watermark_failed: bool = False) -> discord.Embed:
    """
    Create Discord embed showing resync details
    
    EMBED CONTENTS:
    - User who ran the command
    - Song/audio source (with clickable link if available)
    - Audio offset timestamp (when provided by auto-sync)
    - Usage limits remaining (for free users)
    - Promo messages or tips
    
    SPECIAL FORMATTING:
    - SoundCloud: Uses clean display URLs (not API URLs)
    - Spotify: Shows "[song] by [artist]" format
    - Uploaded files: Shows filename only
    - Video URLs: Adds video link for YouTube videos
    """
    # Handle loopaudio command differently
    if command_name == "loopaudio":
        if audio_link.endswith(('.mp3', '.wav', '.m4a')):
            audio_info = f"🎵 Audio: `{audio_link}`"
        else:
            audio_info = f"🎵 Audio: `{audio_link}`"
        
        description = f"📽️ <@{user_id}> used `{command_name}`\n{audio_info}"
        
        if loop_info:
            loop_count = loop_info.get("loop_count", 5)
            segment_duration = loop_info.get("segment_duration", 0)
            description += f"\n🔄 Looped {loop_count}x ({segment_duration:.1f}s segment)"
            
        return discord.Embed(description=description, color=discord.Color.green())
    
    # Original logic for other commands
    if audio_link.startswith("[") and "](" in audio_link and audio_link.endswith(")"):
        song_info = f"🎵 {audio_link}"
    elif audio_link.startswith("http://") or audio_link.startswith("https://"):
        song_info = f"[🎵 Song](<{audio_link}>)"
    elif audio_link == "random database track" or audio_link == "track will be determined by API":
        song_info = "🎵 Song: `uploaded file`"
    else:
        filename = audio_link if audio_link.endswith(".mp3") else audio_link
        song_info = f"🎵 Song: `{filename}`"

    # Add clean timestamp if provided 
    if audio_offset is not None:
        total_seconds = int(round(audio_offset))
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        timestamp = f"⏰ Audio starts at: `{minutes}:{seconds:02d}`"
        description = (
            f"📽️ <@{user_id}> used `{command_name}`\n"
            f"{song_info}\n"
            f"{timestamp}"
        )
    else:
        description = (
            f"📽️ <@{user_id}> used `{command_name}`\n"
            f"{song_info}"
        )
    manual_commands = ["resyncmedia", "resyncmp4", "resyncmp3"]
    if video_url and ("youtube.com" in video_url or "youtu.be" in video_url):
        description += f"\n🎥 [Video]({video_url})"
    if watermark_failed:
        description += f"\n\n*Note: Couldn't apply watermark due to technical issues, so here's your video without it!*"
    if show_promo:
        description += f"\n\nPremium has been removed for good, and Resyncbot has now migrated to AWS. Enjoy unlimited resyncs!"
    if command_name in manual_commands:
        description += f"\n\nUse /guide for a short guide on how to make the most out of manual resyncing!"
    description += f"\n\nEnjoying ResyncBot? Consider leaving a review [here](https://top.gg/bot/1372406004515475577#reviews) or donating using /donate!"
    return discord.Embed(description=description, color=discord.Color.blurple())

def is_soundcloud_url(url: str) -> bool:
    """Returns True if the URL is a SoundCloud link."""
    return "soundcloud.com" in urlparse(url).netloc.lower()

def get_audio_download_options(audio_url: str, output_path: str, format_id: str = None) -> dict:
    """Generates yt_dlp options for downloading audio, optionally with a specific format."""
    base_opts = {
        'outtmpl': output_path,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
    }

    if is_soundcloud_url(audio_url):
        if format_id:
            base_opts['format'] = format_id
        else:
            base_opts['format'] = 'bestaudio/best'
    else:
        base_opts.update({
            'cookiefile': Config.COOKIE_FILE,
            'format': 'bestaudio/best',
            'extract_audio': True,
            'audio_format': 'mp3',
            'audio_quality': '192',
        })

    return base_opts

def resolve_mp3_path(base_path):
    """Returns the actual .mp3 path if it exists, accounting for extension quirks."""
    if os.path.exists(base_path):
        return base_path
    elif os.path.exists(base_path + ".mp3"):
        return base_path + ".mp3"
    return None

def format_resync_error(error: str) -> str:
    if "spotify" in error.lower() and "youtube" in error.lower():
        return f"{error}"
    if "sign in to confirm" in error.lower() or "cookies" in error.lower():
        return "Audio requires login — my SoundCloud/YouTube cookies may be expired. Try `/resyncmp3` instead."
    if "404" in error or "not found" in error.lower():
        return "Audio not found — the link may be invalid or removed."
    if "fallback_failed" in error:
        return "All audio download attempts failed — maybe you used the incorrect audio link?"
    return error

def save_progress_to_db(session_id: str, message: str):
    import psycopg2
    try:
        conn = psycopg2.connect(Config.DATABASE_URL)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO progress_updates (session_id, message, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (session_id)
            DO UPDATE SET message = EXCLUDED.message, updated_at = NOW();
        """, (session_id, message))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logger.warning(f"[PROGRESS_DB] Failed to save progress: {e}")
        
def edit_progress(token, app_id, message_id, content, session_id=None, progress_queues=None):
    global _last_progress_update
    if time.time() - _last_progress_update < Config.PROGRESS_UPDATE_INTERVAL:
        return  # Skip if last update was too recent
    _last_progress_update = time.time()

    url = f"https://discord.com/api/v10/webhooks/{app_id}/{token}/messages/{message_id}"
    headers = {"Content-Type": "application/json"}
    data = {"content": content}
    try:
        r = requests.patch(url, headers=headers, json=data)
        logger.info(f"[PATCH] Status: {r.status_code} | Response: {r.text}")
        r.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to update progress message: {e}")

def edit_progress_web(message, session_id):
    logger.info(f"[WEB_PROGRESS] Updating: '{message}' for session '{session_id}'")
    save_progress_to_db(session_id, message)
        
def trim_audio_ffmpeg(audio_path, offset, max_duration=None) -> str:
    if max_duration is None:
        max_duration = Config.MAX_DURATION

    output_path = audio_path.replace(".mp3", "_trimmed.mp3")

    # FFmpeg command breakdown:
    # -y: Overwrite output file if exists
    # -i: Input file
    # -c copy: Copy streams without re-encoding (fast, no quality loss)
    # -preset veryfast: Encoding speed (when re-encoding is needed)
    # -movflags +faststart: Put metadata at start of file (better for web playback)
    # -threads 0: Use all available CPU threads
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(offset),            # start point
        "-i", audio_path,
        "-t", str(max_duration),           # trim to match video
        "-c", "copy",                  # no re-encode
        output_path
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ProcessingError(
            f"FFmpeg audio trim failed: {result.stderr.decode()}",
            "❌ Failed to trim audio file"
        )
    return output_path

def trim_video_ffmpeg(input_path, start_time, end_time=None) -> str:
    """
    Trims a video from start_time to end_time.
    If end_time is None, it defaults to Config.MAX_DURATION.
    """
    output_path = input_path.replace(".mp4", "_trimmed.mp4")

    duration = Config.MAX_DURATION  # default fallback duration
    if end_time is not None and end_time > start_time:
        duration = max(0.1, end_time - start_time)
    
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
        "-vf", "scale='min(1280,iw)':-2",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "28",
        "-tune", "fastdecode",      # Optimize for fast decoding
        "-x264-params", "ref=1:me=hex:subme=1",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-threads", "0",
        output_path
    ]

    '''
    subprocess.run is what's used to execute the ffmpeg command.
    All the specifications for what is executed on the video are in the cmd list.
    '''
    try:
        logger.info(f"▶️ Running ffmpeg trim: {' '.join(cmd)}")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise ProcessingError(
                f"FFmpeg video trim failed: {result.stderr.decode()}",
                "❌ Failed to trim video file"
            )
        logger.info("✅ Trimmed video successfully")
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ ffmpeg trim failed: {e.stderr.decode(errors='ignore')}")
        raise RuntimeError("ffmpeg trim failed") from e    
    return output_path

def send_combined_video_response(video_path, audio_path, output_path, combine_with_ffmpeg, send_file, logger, extra_headers=None):
    """Combines video and audio, sends final video as Flask response."""
    try:
        combine_with_ffmpeg(video_path, audio_path, output_path)
        logger.info(f"[📤] Sending final video: {output_path}")
        
        headers = {"X-Temp-Path": output_path}
        if extra_headers:
            headers.update(extra_headers)
            
        return send_file(output_path, mimetype="video/mp4", as_attachment=True), 200, headers
    except ProcessingError:
        raise
    except Exception as e:
        raise ProcessingError(f"Failed to send video response: {e}", "❌ Failed to process final video")

async def download_audio_with_fallback(audio_url: str, output_path: str, logger_obj, interaction=None, cookiefile=None) -> bool:
    """
    Downloads an audio file from a SoundCloud or MP3 URL with fallbacks.
    Returns True if successful, False otherwise.
    """
    def get_opts(fmt=None):
        opts = {
            'format': fmt or 'bestaudio/best',
            'outtmpl': output_path.replace(".mp3", ""),
            'noplaylist': True,     
            'playlist_items': '1',   
            'extract_flat': False, 
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        }

        if cookiefile:
            opts['cookiefile'] = cookiefile
            logger_obj.info(f"🍪 Using cookies for audio download: {cookiefile}")
        return opts
    
    fallback_formats = [
        'http_mp3_128', 'http_mp3_0', 'mp3_0', 'progressive_mp3'
    ]

    # 1. Try bestaudio
    try:
        logger_obj.info(f"🔊 Trying bestaudio: {audio_url}")
        with YoutubeDL(get_opts()) as ydl:
            ydl.download([audio_url])
        if os.path.exists(output_path):
            return True
        final_path = resolve_mp3_path(output_path)

        if os.path.exists(final_path):
            os.rename(final_path, output_path)  # Rename to match your expected path
            return True
    except Exception as e:
        logger_obj.warning(f"❌ bestaudio failed: {e}")

    # 2. Try fallback SoundCloud formats
    for fmt in fallback_formats:
        try:
            logger_obj.info(f"🔁 Trying fallback format: {fmt}")
            with YoutubeDL(get_opts(fmt)) as ydl:
                ydl.download([audio_url])
            if os.path.exists(output_path):
                return True
            final_path = resolve_mp3_path(output_path)

            if os.path.exists(final_path):
                os.rename(final_path, output_path)  # Rename to match your expected path
                return True
        except Exception as e:
            logger_obj.warning(f"❌ Format {fmt} failed: {e}")

    # 3. Try with no format specified (let yt_dlp choose)
    try:
        logger_obj.info("🌐 Trying with no format specified")
        with YoutubeDL(get_opts(None)) as ydl:
            ydl.download([audio_url])
        if os.path.exists(output_path):
            return True
        final_path = resolve_mp3_path(output_path)

        if os.path.exists(final_path):
            os.rename(final_path, output_path)  # Rename to match your expected path
        return True
    except Exception as e:
        logger_obj.warning(f"❌ Fallback no-format failed: {e}")

    # 4. Direct .mp3 download as last resort
    if audio_url.lower().endswith(".mp3"):
        try:
            logger_obj.info("📥 Trying direct MP3 download")
            r = requests.get(audio_url)
            r.raise_for_status()
            with open(output_path, "wb") as f:
                f.write(r.content)
            if os.path.exists(output_path):
                return True
        except Exception as e:
            logger_obj.warning(f"❌ Direct MP3 download failed: {e}")


    # Final fail
    logger_obj.error("❌ All fallback audio downloads failed")
    if interaction:
        await interaction.followup.send("❌ Audio file could not be downloaded properly.")
    return False

async def post_to_resync_api(url, form: aiohttp.FormData, headers):
    logger.info(f"🌐 Sending request to Resync API: {url}")
    timeout = aiohttp.ClientTimeout(total=120)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.post(url, data=form, headers=headers) as response:
                logger.info(f"📩 API status: {response.status}")
                logger.info(f"📩 API headers: {dict(response.headers)}")

                if response.status != 200:
                    logger.info(f"[DEBUG] API returned status {response.status}")
                    # Always return 6-tuple
                    try:
                        error_json = await response.json()
                        return None, None, error_json.get("error", "Unknown error."), None, None, None
                    except Exception:
                        text = await response.text()
                        logger.error(f"❌ API error response body: {repr(text)}")
                        if "<html>" in text.lower():
                            return None, None, "⚠️ API Internal Server Error (500).", None, None, None
                        if not text.strip():
                            text = "No response body."
                        return None, None, f"Unexpected error: {text}", None, None, None

                output_bytes = await response.read()
                if not output_bytes:
                    return None, None, "API responded with empty content.", None, None, None

                temp_path = response.headers.get("X-Temp-Path")
                filename = response.headers.get("X-Filename")

                track_info = None
                audio_offset = None

                if response.headers.get("X-Selected-Song"):
                    track_info = {
                        "song": urllib.parse.unquote(response.headers.get("X-Selected-Song")),
                        "artist": urllib.parse.unquote(response.headers.get("X-Selected-Artist")),
                        "url": response.headers.get("X-Selected-URL"),
                        "platform": response.headers.get("X-Selected-Platform"),
                    }

                if response.headers.get("X-Audio-Offset"):
                    try:
                        audio_offset = float(response.headers.get("X-Audio-Offset"))
                    except Exception:
                        audio_offset = None

                logger.info(f"✅ Received {len(output_bytes)} bytes from API.")
                return output_bytes, temp_path, None, track_info, audio_offset, filename

        except asyncio.TimeoutError:
            logger.error("⏱️ Resync API request timed out.")
            return None, None, "Resync API request timed out. This sometimes happens when my memory gets overloaded, try the same command again!", None, None, None
        except Exception as e:
            logger.error(f"❌ Resync API request failed: {e}")
            return None, None, format_user_error(e), None, None, None

async def run_resync_job(bot, interaction, msg, form, api_endpoint, user_id, command_name, audio_source, video_source=None, show_promo=False, usage_type: str | None = None, headers: dict | None = None):
    temp_path = None
    try:
        logger.info(f"[DEBUG] ===== JOB START for user {user_id} =====")
        logger.info(f"[DEBUG] Command: {command_name}")
        logger.info(f"[DEBUG] API endpoint: {api_endpoint}")
        await msg.edit(content=f"🎬 Resync starting...")
        try:
            msg = await msg.channel.fetch_message(msg.id)
            logger.info(f"[DEBUG] Refetched message content: {msg.content}")
        except Exception as e:
            logger.error(f"[DEBUG] Could not refetch message: {e}")

        logger.info(f"[DEBUG] Message edited successfully")
        if usage_type:
            try:
                premium_manager.log_command_usage(user_id, usage_type)
            except Exception as e:
                logger.warning(f"Usage logging failed (non-fatal): {e}")

        output_bytes, temp_path, error, track_info, audio_offset, filename = await post_to_resync_api(api_endpoint, form, headers=headers)

        logger.info(f"[DEBUG] post_to_resync_api returned - error: '{error}'")
        logger.info(f"[DEBUG] error type: {type(error)}")
        if error:
            logger.info(f"[DEBUG] Raw error: {repr(error)}")  # Use repr() to see exact string
            logger.info(f"[DEBUG] Error starts with VIDEO_: {'VIDEO_' in error}")
            logger.info(f"[DEBUG] Error starts with 🔗: {'🔗' in error}")
            if 'cannot connect to host' in error.lower() or 'fly.dev' in error.lower():
                logger.info("[DEBUG] Ignoring transient connection error")
                error = None

            if error:  # Only show error if it wasn't cleared above
                # Video error
                if any(prefix in error for prefix in ['VIDEO_', '🔗', 'Instagram']):
                    await msg.edit(content=f"❌ {error}")
                else:
                    # Audio error
                    await msg.edit(content=f"❌ API Error: {format_resync_error(error)}")
                safe_log_command(
                    bot, interaction, command_name,
                    {
                        "video_source": video_source,
                        "audio_source": audio_source
                    },
                    status="fail",
                    error=error
                )
                return

        # If we got track info from the API (random track selection), use it
        if track_info:
            song = track_info.get('song', 'Unknown Song')
            artist = track_info.get('artist', 'Unknown Artist') 
            url = track_info.get('url')
            
            if url:
                if track_info.get('platform') == 'soundcloud':
                    display_url = get_soundcloud_display_url(url, song, artist)
                    if display_url:
                        if artist and artist.strip():
                            audio_source = f"[{song} by {artist}]({display_url})"
                        else:
                            audio_source = f"[{song}]({display_url})"
                    else:
                        if artist and artist.strip():
                            audio_source = f"{song} by {artist}"
                        else:
                            audio_source = f"{song}"
                else:
                    if artist and artist.strip():
                        audio_source = f"[{song} by {artist}]({url})"
                    else:
                        audio_source = f"[{song}]({url})"
            else:
                # No URL available
                if artist and artist.strip():
                    audio_source = f"🎵 {song} by {artist}"
                else:
                    audio_source = f"🎵 {song}"
                
            logger.info(f"🎵 Using selected track info: {audio_source}")   
        
        # Fetching the global watermark failed flag
        watermark_failed = getattr(sys.modules[__name__], 'watermark_failed_flag', False)
    
        embed = create_resync_embed(
            user_id=user_id,
            command_name=command_name,
            audio_link=audio_source,
            audio_offset=audio_offset,
            video_url=video_source,
            show_promo=show_promo,
            watermark_failed=watermark_failed
        )


        # Resetting watermark failed flag after use
        if hasattr(sys.modules[__name__], 'watermark_failed_flag'):
            delattr(sys.modules[__name__], 'watermark_failed_flag')     
        if temp_path and os.path.exists(temp_path):
            with open(temp_path, "rb") as f:
                file = discord.File(f, filename="resynced.mp4")
        else:
            file = discord.File(io.BytesIO(output_bytes), filename="resynced.mp4")

        manual_commands = {"resyncmp4", "resyncmp3", "resyncmedia"}
        random_commands = {"resyncrandomfile", "resyncrandommedia"}
        auto_commands   = {"autoresyncmp4"}

        if Config.PREMIUM_ENABLED:
            try:
                usage = premium_manager.get_user_usage_stats(user_id)
                is_premium = usage.get("is_premium", False)

                if command_name in manual_commands:
                    if not is_premium:
                        rr_used = usage.get("random_resync", 0)
                        ar_used = usage.get("auto_resync", 0)
                        rr_left = max(0, Config.RANDOM_LIMITS - rr_used)
                        ar_left = max(0, Config.AUTO_LIMITS - ar_used)
                        if rr_left > 0:
                            embed.add_field(
                                name="You have some random resyncs remaining!",
                                value=f"{rr_left} left today (used {rr_used}/{Config.RANDOM_LIMITS})",
                                inline=False
                            )
                        if ar_left > 0:
                            embed.add_field(
                                name="You have some auto resyncs remaining!",
                                value=f"{ar_left} left today (used {ar_used}/{Config.AUTO_LIMITS})",
                                inline=False
                            )
                elif command_name in random_commands and not is_premium:
                    rr_used = usage.get("random_resync", 0)
                    rr_left = max(0, Config.RANDOM_LIMITS - rr_used)
                    embed.add_field(
                        name="Random Resyncs Remaining:",
                        value=f"{rr_left} left today (used {rr_used}/{Config.RANDOM_LIMITS})",
                        inline=False
                    )
                    embed.set_footer(text="Limits reset daily at midnight UTC, you can use /vote to reset your limits, or upgrade to premium for unlimited resyncs!")

                elif command_name in auto_commands and not is_premium:
                    ar_used = usage.get("auto_resync", 0)
                    ar_left = max(0, Config.AUTO_LIMITS - ar_used)
                    embed.add_field(
                        name="Auto Resyncs Remaining:",
                        value=f"{ar_left} left today (used {ar_used}/{Config.AUTO_LIMITS})",
                        inline=False
                    )
                    embed.set_footer(text="Limits reset daily at midnight UTC, you can use /vote to reset your limits, or upgrade to premium for unlimited resyncs!")
                if is_premium:
                    embed.add_field(
                        name="You're a premium user! Thanks for supporting ResyncBot.",
                    )
            except Exception as _e:
                logger.warning(f"Could not append quota fields: {_e}")
            
        try:
            await msg.edit(
                content=f"<@{user_id}> ✅ Resync complete!",
                embed=embed,
                attachments=[file]
            )
        except discord.Forbidden as e:
            # Handle Discord permission errors specifically
            if hasattr(e, 'code') and e.code == 50013:
                # Missing permissions error
                try:
                    # Try to send without attachment first to see if we can at least send messages
                    await msg.edit(content="🚫 Whoops! Looks like I don't have permissions to send videos in this channel.")
                    
                    # Try to DM the user as fallback
                    try:
                        user = bot.get_user(user_id)
                        if user:
                            await user.send(
                                content="✅ Your resync completed! Here's your video (sent via DM due to channel permissions):",
                                embed=embed,
                                file=file
                            )
                            # Update the channel message to let them know
                            await msg.edit(content="🚫 Whoops! Looks like I don't have permissions to send videos in this channel. Check your DMs! 📨")
                        else:
                            await msg.edit(content="🚫 Whoops! Looks like I don't have permissions to send videos in this channel, and I couldn't DM you either. Please check the bot's permissions!")
                    except discord.Forbidden:
                        # Can't DM the user either
                        await msg.edit(content="🚫 Whoops! Looks like I don't have permissions to send videos in this channel, and I couldn't DM you either. Please ask a server admin to check the bot's permissions!")
                    except Exception as dm_error:
                        logger.error(f"Failed to send DM fallback: {dm_error}")
                        await msg.edit(content="🚫 Whoops! Looks like I don't have permissions to send videos in this channel. Please ask a server admin to give me 'Attach Files' permissions!")
                        
                except discord.Forbidden:
                    # Can't even edit messages - log this and try followup
                    logger.error(f"No message permissions in channel for user {user_id}")
                    try:
                        await interaction.followup.send(
                            content=f"<@{user_id}> 🚫 I don't have permissions to send messages or files in that channel. Please ask a server admin to check my permissions!",
                            ephemeral=True
                        )
                    except Exception as followup_error:
                        logger.error(f"Could not send followup either: {followup_error}")
            else:
                # Other Discord forbidden error
                await msg.edit(content="🚫 Discord permissions error. Please ask a server admin to check the bot's permissions!")
                
        except TypeError:
            # Handle the existing TypeError case (Discord.py version compatibility)
            await msg.edit(content="✅ Resync complete! Sending separately...")
            try:
                await interaction.followup.send(
                    content=f"<@{user_id}>",
                    embed=embed,
                    file=file
                )
            except discord.Forbidden:
                # Permission error on followup too
                try:
                    await interaction.followup.send(
                        content=f"<@{user_id}> ✅ Resync complete! But I don't have permissions to send the video file. Please ask a server admin to give me 'Attach Files' permissions!",
                        ephemeral=True
                    )
                except Exception:
                    logger.error("Could not send any response due to permissions")
            except Exception as followup_error:
                logger.error(f"Followup send failed: {followup_error}")
                await msg.edit(content=f"⚠️ Resync succeeded, but failed to upload: `{followup_error}`")
            
            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"⚠️ Could not delete queue message: {e}")
                
        except Exception as e:
            # Handle any other unexpected errors during upload
            logger.error(f"[❌ Upload Failed] {e}")
            error_msg = "⚠️ Resync succeeded, but failed to upload"
            
            # Check if it might be a permission issue even if not caught above
            if "403" in str(e) or "Forbidden" in str(e) or "permissions" in str(e).lower():
                error_msg = "🚫 Whoops! Looks like I don't have permissions to send videos in this channel. Please ask a server admin to give me 'Attach Files' permissions!"
            else:
                error_msg += f": `{e}`"
            
            await msg.edit(content=error_msg)

        safe_log_command(
            bot, interaction, command_name,
            {
                "video_source": video_source,
                "audio_source": audio_source
            },
            status="success",
            file=file
        )

    except Exception as e:
        logger.info(f"[DEBUG] ===== EXCEPTION CAUGHT =====")
        logger.info(f"[DEBUG] Exception type: {type(e).__name__}")
        logger.info(f"[DEBUG] Exception message: {str(e)}")
        logger.error(f"❌ Unexpected error in resync job: {e}")
        logger.error("🔍 Full stack trace:\n%s", traceback.format_exc())
        try:
            error_message = format_user_error(e)
            await msg.edit(content=error_message)
        except Exception as discord_err:
            logger.error(f"Could not send error to user: {discord_err}")

        safe_log_command(
            bot, interaction, command_name,
            {
                "video_source": video_source,
                "audio_source": audio_source
            },
            status="fail",
            error=str(e)
        )

    finally:
        if temp_path:
            # CRITICAL: Always cleanup temp files to prevent disk space issues
            # The /tmp/ directory can fill up fast with video files
            # Even if an error occurred, we must clean up
            safe_cleanup(temp_path)

def download_audio(audio_url: str, audio_path: str, logger, cookiefile=None):
    try:
        logger.info(f"AUDIO LINK INPUTTED: {audio_url}")
        
        if "spotify.com/track/" in audio_url:
            return download_spotify_track(audio_url, audio_path, logger)
        
        if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
            audio_url = clean_youtube_url(audio_url)
        elif 'soundcloud.com' in audio_url:
            original_url = audio_url
            audio_url = clean_soundcloud_url(audio_url)
            if audio_url != original_url:
                logger.info(f"🧹 Cleaned SoundCloud URL: {original_url[:50]}... -> {audio_url[:50]}...")

        if audio_url.endswith(".mp3"):
            r = requests.get(audio_url)
            r.raise_for_status()
            with open(audio_path, "wb") as f:
                f.write(r.content)
            logger.info(f"[✅] MP3 downloaded to {audio_path}")
            return True, ""
        else:
            # Pass cookies to the fallback function
            result = asyncio.run(download_audio_with_fallback(audio_url, audio_path, logger, cookiefile=cookiefile))
            if result and os.path.exists(audio_path):
                duration = get_duration(audio_path)
                if duration > 0:
                    return True, ""
                else:
                    return False, "Invalid or corrupt audio file"
            return False, "fallback_failed"
    except Exception as e:
        logger.warning(f"[⚠️] Audio download failed: {e}")
        return False, format_user_error(e)

def cleanup_tmp_files():
    """Deletes leftover temporary media files in /tmp/ directory."""
    patterns = [
        "/tmp/*_output.mp4", "/tmp/*_trimmed.mp3", "/tmp/*_fixed.mp4",
        "/tmp/*.mp4", "/tmp/*.mp3"
    ]
    for pattern in patterns:
        for f in glob.glob(pattern):
            try:
                os.remove(f)
            except Exception:
                pass

def is_valid_video_file(path: str, logger=None) -> bool:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        output = result.stdout.strip()
        if not output:
            if logger:
                logger.warning(f"[Video Validation] ffprobe returned empty output for {path}")
            return False
            
        duration = float(output)
        return duration > 0
    except ValueError as e:
        if logger:
            logger.warning(f"[Video Validation] Could not parse duration: {e}")
        return False
    except Exception as e:
        if logger:
            logger.warning(f"[Video Validation] Invalid video file: {e}")
        return False

def get_cookiefile_for_url(url: str) -> Optional[str]:
    """
    Return path to cookies.txt if the URL requires authentication
    
    COOKIES ARE NEEDED FOR:
    - Age-restricted YouTube videos
    - Private/unlisted content
    - Some Instagram content
    - Login-required SoundCloud tracks
    
    Cookies are stored in data/cookies.txt (ignored by git)
    Export cookies from your browser using a cookies.txt extension
    
    Returns:
        str: Absolute path to cookies file, or None if not needed/not found
    """
    if any(domain in url for domain in ["youtube.com", "youtu.be", "instagram.com",  'soundcloud.com']):
        cookie_path = Path(Config.COOKIE_FILE)
        if cookie_path.exists():
            logger.info(f"🍪 Using cookiefile: {cookie_path}")
            return str(cookie_path)
        else:
            logger.warning(f"⚠️ Cookie file not found at {cookie_path}")
    return None

def download_video_with_retry(url, ydl_opts, retries=2):
    """
    Download video with retry logic for transient failures

    RETRY STRATEGY:
    - Attempt 1: Standard download with provided options
    - Attempt 2: If failed, try again (handles temporary network issues)
    - Instagram: If format fails, retry with most flexible format ('worst')
    - Cookies expired: Raise special error so user knows to update cookies

    PLATFORM-SPECIFIC HANDLING:
    - YouTube: Uses yt-dlp with best quality settings
    - Instagram: Extra flexible format selection + carousel support
    - TikTok: Redirected to dedicated TikTok handler
    """
    if "youtube.com" in url or "youtu.be" in url:
        # Use yt-dlp for YouTube downloads
        ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'

    elif "instagram.com" in url:
        # Instagram-specific: be very flexible with formats
        clean_url, carousel_index = parse_instagram_carousel_index(url)

        ydl_opts.update({
            'format': 'best[ext=mp4]/best/worst',
            'merge_output_format': 'mp4',
            'noplaylist': False,
            'playliststart': carousel_index,
            'playlistend': carousel_index,
        })
        url = clean_url
        logger.info(f"Instagram: Using carousel item #{carousel_index}")

    for attempt in range(1, retries + 1):
        try:
            logger.info(f"[yt_dlp] Attempt {attempt} to download video: {url}")
            with yt_dlp.YoutubeDL(ydl_opts.copy()) as ydl:
                ydl.download([url])
            return True
        except yt_dlp.utils.DownloadError as e:
            err_str = str(e)
            logger.warning(f"[yt_dlp] Attempt {attempt} failed: {err_str}")
            
            if "instagram.com" in url and "format" in err_str.lower():
                if attempt == 1:
                    # Try even more flexible format on retry
                    ydl_opts['format'] = 'worst'  # Accept literally anything
                    logger.info(f"🔄 Instagram format failed, trying most flexible fallback")
                    continue
            if 'instagram.com' in url:
                logger.warning(f"[Retry] yt-dlp failed for Instagram: {e}, trying fallback...")
                success, err = download_instagram_fallback(url, ydl_opts['outtmpl'], logger=logger)
                if not success:
                    raise RuntimeError(f"Instagram fallback failed: {err}")
                return
            
            if "403" in err_str or "cookies" in err_str.lower() or "login" in err_str.lower():
                raise RuntimeError("cookies_expired")

            if attempt == retries:
                raise RuntimeError(f"Download failed after {retries} attempts: {err_str}")
            raise  
        except Exception as e:
            logger.error(f"[yt_dlp] Unexpected error: {e}")
            if attempt == retries:
                raise

def get_duration(path: str) -> float:
    """Returns duration of a media file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        output = result.stdout.strip()
        if not output:
            # Check if it's a very small file (likely corrupted)
            if os.path.exists(path) and os.path.getsize(path) < 1024:
                return -1.0
            # ffprobe returned empty - file might be corrupted
            logger.warning(f"ffprobe returned empty output for {path}")
            return -1.0
            
        return float(output)
    except ValueError as e:
        logger.warning(f"Could not parse duration from ffprobe output: {e}")
        return -1.0
    except Exception as e:
        logger.warning(f"Error getting duration: {e}")
        return -1.0
    
def is_discord_cdn(url: str) -> bool:
    return any(domain in url.lower() for domain in [
        "cdn.discordapp.com", 
        "media.discordapp.net"
    ])

def extract_audio_from_video(video_path: str, output_mp3_path: str) -> bool:
    """
    Extracts audio from a video file using ffmpeg and saves it as an MP3.
    Returns True if successful, False otherwise.
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",                     # no video
            "-acodec", "libmp3lame",
            "-b:a", "192k",
            output_mp3_path
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            logger.warning(f"❌ ffmpeg audio extraction failed: {result.stderr.decode()}")
            return False
        return os.path.exists(output_mp3_path)
    except Exception as e:
        logger.error(f"❌ Exception extracting audio: {e}")
        return False

def clean_youtube_url(url):
    """Clean YouTube URLs to remove problematic parameters like mix playlists"""
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        # Remove problematic parameters that can cause yt-dlp to hang
        problematic_params = ['list', 'start_radio', 'index']
        
        # Keep only essential parameters
        cleaned_params = {}
        for key, value in query_params.items():
            if key not in problematic_params:
                cleaned_params[key] = value
        
        # Reconstruct URL
        new_query = urlencode(cleaned_params, doseq=True)
        cleaned_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        
        return cleaned_url
    except Exception:
        # If URL parsing fails, return original
        return url
    
def get_video_bpm(video_path):
    """
    Detect BPM/tempo of video's audio track using librosa
    
    ALGORITHM:
    1. Extract first 30 seconds of audio to WAV
    2. Find where music actually starts (skip silence/intro)
    3. Analyze 15 seconds from music start point
    4. Use librosa's beat detection to find tempo
    
    This is used for:
    - Random resync: Find tracks in database with matching BPM
    - Auto-sync: Beat-based synchronization method
    
    Returns:
        int: BPM value, or None if detection fails
    """
    temp_audio = tempfile.mktemp(suffix=".wav")
    
    try:
        # Extract first 30 seconds
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-t", "30", "-vn",
            "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1", temp_audio 
        ]
        
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        y, sr = librosa.load(temp_audio, sr=16000)
        
        # Find when the music actually starts (skip silence/quiet intro)
        # Calculate RMS energy in 1-second windows
        window_size = sr  # 1 second
        energy_threshold = np.max(y**2) * 0.1  # 10% of peak energy
        
        music_start = 0
        for i in range(0, len(y) - window_size, window_size):
            window_energy = np.mean(y[i:i + window_size] ** 2)
            if window_energy > energy_threshold:
                music_start = i
                break
        
        # Analyze BPM starting from where music begins
        music_segment = y[music_start:music_start + 15 * sr]  # 15 seconds from music start
        tempo, beats = librosa.beat.beat_track(
            y=music_segment, sr=sr, 
            hop_length=1024,  # vs 512 - 2x faster
            start_bpm=120
        )
        
        bpm = int(tempo.item())
        start_time = music_start / sr
        logger.info(f"Video BPM: {bpm} (music starts at {start_time:.1f}s)")
        return bpm
    
    except Exception as e:
        logger.error(f"Error detecting BPM: {e}")
        return None
    finally:
        # CRITICAL: Always cleanup temp files to prevent disk space issues
        # The /tmp/ directory can fill up fast with video files
        # Even if an error occurred, we must clean up
        if os.path.exists(temp_audio):
            os.remove(temp_audio)

def find_matching_tracks(target_bpm, tolerance=5):
    import psycopg2
    
    try:
        # Connect to database
        print("[DEBUG] Connection string:", repr(Config.DATABASE_URL))
        if not Config.DATABASE_URL:
            raise ValueError("DATABASE_URL is empty or missing!")

        conn = psycopg2.connect(Config.DATABASE_URL)
        cursor = conn.cursor()
        
        # Query for matching tracks
        query = """
        SELECT uploader, song, bpm, url, song_id, playlist_id, duration, platform 
        FROM tracks 
        WHERE bpm BETWEEN %s AND %s
        """
        
        min_bpm = target_bpm - tolerance
        max_bpm = target_bpm + tolerance
        
        cursor.execute(query, (min_bpm, max_bpm))
        results = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        if not results:
            logger.info(f"🚫 No tracks found for BPM range {min_bpm}-{max_bpm}")
            return None
        
        # Convert to list of dictionaries for easier handling
        tracks = []
        for row in results:
            track = {
                "uploader": row[0],
                "song": row[1],
                "bpm": row[2],
                "url": row[3],
                "song_id": row[4],
                "playlist_id": row[5],
                "duration": row[6],
                "platform": row[7]
            }
            tracks.append(track)
        
        logger.info(f"🎵 Found {len(tracks)} matching tracks for BPM {target_bpm}±{tolerance}")
        
        # Return a random track from the matches
        selected_track = random.choice(tracks)
        logger.info(f"🎲 Selected: {selected_track['song']} by {selected_track['uploader']} (BPM: {selected_track['bpm']})")
        
        return selected_track
        
    except psycopg2.Error as e:
        logger.error(f"Database error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error finding matching tracks: {e}")
        return None

def download_audio_from_database(song_title: str, uploader: str, platform: str, song_id: str, output_path: str):
    try:
        if platform == "soundcloud":
            logger.info(f"🎵 Searching SoundCloud for: '{song_title}' by '{uploader}'")
            
            # Get cookiefile for SoundCloud
            cookiefile = get_cookiefile_for_url("https://soundcloud.com")
            logger.info(f"🍪 Using SoundCloud cookiefile: {cookiefile}")
            
            song_settings = {
                'format': 'bestaudio[ext=mp3]/bestaudio',
                'outtmpl': output_path.replace('.mp3', ''),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True,
                'cookiefile': cookiefile  # Add cookiefile support
            }
            
            # Search by name + artist only
            search_query = f"{song_title} {uploader}"
            
            search_settings = {
                'quiet': True, 
                'extract_flat': True,
                'cookiefile': cookiefile  # Add cookiefile to search too
            }
            search_sc = YoutubeDL(search_settings)
            
            search_result = search_sc.extract_info(f"scsearch1:{search_query}", download=False)
            
            if not search_result["entries"]:
                return False, f"No SoundCloud results for '{search_query}'"
            
            found_song = search_result["entries"][0]
            
            # Get the webpage URL instead of API URL
            if 'webpage_url' in found_song:
                found_url = found_song['webpage_url']
                logger.info(f"🔗 Using webpage URL: {found_url}")
            elif 'url' in found_song and not found_song['url'].startswith('https://api.soundcloud.com'):
                found_url = found_song['url']
                logger.info(f"🔗 Using direct URL: {found_url}")
            else:
                # Fallback: try to construct web URL from available data
                logger.warning(f"🔗 Got API URL, attempting fallback: {found_song.get('url', 'No URL')}")
                # Try using the uploader and title to construct a URL
                uploader_clean = found_song.get('uploader', uploader).replace(' ', '-').lower()
                title_clean = found_song.get('title', song_title).replace(' ', '-').lower()
                found_url = f"https://soundcloud.com/{uploader_clean}/{title_clean}"
                logger.info(f"🔗 Constructed fallback URL: {found_url}")
            
            logger.info(f"✅ Found via name search: {found_song['title']}")
            
            # Download using the web URL
            song_downloader = YoutubeDL(song_settings)
            song_downloader.download([found_url])
                    
        elif platform == "spotify":
            logger.info(f"🔍 Searching YouTube for: '{uploader} - {song_title}'")
            
            yt_settings = {
                'format': 'bestaudio/best',
                'outtmpl': output_path.replace('.mp3', ''),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'cookiefile': Config.COOKIE_FILE if hasattr(Config, 'COOKIE_FILE') else None,
                'quiet': True,
                'no_warnings': True,
            }
            
            # Search using artist - song format
            search_query = f"{uploader} - {song_title}"
            
            search_settings = {'quiet': True, 'extract_flat': True}
            yt_searcher = YoutubeDL(search_settings)
            
            search_result = yt_searcher.extract_info(f"ytsearch1:{search_query}", download=False)
            
            if not search_result["entries"]:
                return False, f"No YouTube results for '{search_query}'"
            
            video_info = search_result["entries"][0]
            search_url = video_info["url"]
            logger.info(f"✅ Found via search: {video_info['title']}")
            
            # Download using search result
            yt_dl = YoutubeDL(yt_settings)
            yt_dl.download([search_url])
        
        # Final check - did we get the file?
        final_path = resolve_mp3_path(output_path)
        if final_path and os.path.exists(final_path):
            if final_path != output_path:
                os.rename(final_path, output_path)
            logger.info(f"✅ Successfully downloaded audio")
            return True, ""
        else:
            return False, f"Download failed - no file created"
            
    except Exception as e:
        logger.error(f"❌ Complete download failure: {e}")
        return False, str(e)
    
def find_best_audio_match(video_audio_path, database_audio_path, max_search_duration=60):
    """
    AUTO-SYNC: Find where audio should start using waveform cross-correlation
    
    HOW IT WORKS:
    1. Load short sample from video (30 seconds)
    2. Load longer sample from database audio (60+ seconds)
    3. Slide video audio across database audio, comparing waveforms
    4. Find the position with highest correlation (best match)
    5. Apply slight bias toward later parts (favors chorus/drop over intro)
    
    This is the "waveform" sync method - fast and reliable for most content.
    Works by finding where the audio patterns match, similar to how Shazam works.
    
    Returns:
        float: Best matching timestamp in seconds (where audio should start)
    """
    try:
        # Load both audio files
        video_audio, sr = librosa.load(video_audio_path, sr=22050, duration=30)  # Video audio sample
        db_audio, sr = librosa.load(database_audio_path, sr=22050, duration=max_search_duration)

        # Normalize to prevent amplitude mismatch
        def safe_normalize(audio):
            max_val = np.max(np.abs(audio))
            return audio / max_val if max_val > 0 else audio

        video_audio = safe_normalize(video_audio)
        db_audio = safe_normalize(db_audio)

        # Perform cross-correlation
        correlation = signal.correlate(db_audio, video_audio, mode='valid')

        # Apply bias toward later sections
        bias = np.linspace(0.95, 1.05, len(correlation))
        biased_correlation = correlation * bias

        # Find the position with the highest score
        best_sample = np.argmax(biased_correlation)
        best_time = best_sample / sr

        logger.info(f"🎯 Best waveform match at {best_time:.2f}s (biased toward later parts)")
        return best_time

    except Exception as e:
        logger.error(f"Waveform-based audio match failed: {e}")
        return 0  # Fallback to beginning
    
def find_best_beat_match(video_audio_path, database_audio_path, video_bpm):
    """
    Find best beat-aligned position by comparing beat patterns
    """
    import librosa
    import numpy as np
    
    try:
        # Load audio files
        video_audio, sr = librosa.load(video_audio_path, sr=22050, duration=20)
        db_audio, sr = librosa.load(database_audio_path, sr=22050, duration=120)
        
        # Get beat tracks for both
        _, video_beats = librosa.beat.beat_track(y=video_audio, sr=sr, hop_length=512)
        _, db_beats = librosa.beat.beat_track(y=db_audio, sr=sr, hop_length=512)
        
        # Convert beat frames to time
        video_beat_times = librosa.frames_to_time(video_beats, sr=sr, hop_length=512)
        db_beat_times = librosa.frames_to_time(db_beats, sr=sr, hop_length=512)
        
        # Calculate beat intervals (time between beats)
        video_intervals = np.diff(video_beat_times)
        
        best_score = -1
        best_position = 0
        
        # Test different starting positions in the database audio
        beat_duration = 60.0 / video_bpm
        
        for i in range(0, min(len(db_beat_times) - len(video_intervals), int(60 / beat_duration))):
            # Get corresponding section of database beat intervals
            db_section = np.diff(db_beat_times[i:i+len(video_intervals)+1])
            
            if len(db_section) >= len(video_intervals):
                # Compare beat interval patterns
                score = np.corrcoef(video_intervals, db_section[:len(video_intervals)])[0,1]
                
                if not np.isnan(score) and score > best_score:
                    best_score = score
                    best_position = db_beat_times[i]
        
        logger.info(f"🎯 Best beat match at {best_position:.2f}s (score: {best_score:.3f})")
        
        return best_position
        
    except Exception as e:
        logger.error(f"Beat matching failed: {e}")
        # Fallback: random beat-aligned position
        beat_duration = 60.0 / video_bpm
        return np.random.uniform(0, 2) * beat_duration
    
def loop_audio_ffmpeg(input_path: str, start_time: float, end_time: float, loop_count: int, output_path: str):
    """
    Create a looped audio file using FFmpeg - simpler approach
    """
    try:
        # Calculate the segment duration
        segment_duration = end_time - start_time
        
        # Use FFmpeg to extract and loop in one command
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-filter_complex", 
            f"[0:a]atrim=start={start_time}:duration={segment_duration},aloop=loop={loop_count-1}:size={int(44100 * segment_duration)}[looped]",
            "-map", "[looped]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.returncode != 0:
            logger.error(f"FFmpeg loop failed: {result.stderr.decode()}")
            raise ProcessingError(
                f"FFmpeg loop failed: {result.stderr.decode()}", 
                "❌ Failed to create looped audio"
            )
        
        logger.info(f"✅ Created {loop_count}x loop from {start_time}s to {end_time}s (duration: {segment_duration}s each)")
        
    except Exception as e:
        logger.error(f"Error in loop_audio_ffmpeg: {e}")
        raise ProcessingError(f"Audio looping failed: {e}", "❌ Failed to loop audio")

async def process_audio_loop_direct(bot, interaction, msg, audio_file, start_time, end_time, loop_count):
    """
    Process audio looping directly without using the job queue system.
    Much faster and more efficient for simple audio operations.
    """
    import uuid
    import os
    
    audio_path = None
    output_path = None
    raw_audio_path = None
    
    try:
        # Parse timestamps
        start_seconds = parse_timestamp(start_time)
        end_seconds = parse_timestamp(end_time)
        
        # Validate time logic
        if start_seconds >= end_seconds:
            raise ValidationError(
                "Invalid time range", 
                f"⏰ Start time ({start_seconds:.1f}s) must be before end time ({end_seconds:.1f}s)"
            )

        segment_duration = end_seconds - start_seconds
        if segment_duration > 30:  # Max 30 second segments
            raise ValidationError(
                "Segment too long",
                f"⏰ Audio segment too long ({segment_duration:.1f}s). Maximum 30 seconds per loop."
            )
        
        # Set up paths
        base_uuid = uuid.uuid4()
        audio_path = f"/tmp/{base_uuid}.mp3"
        output_path = f"/tmp/{base_uuid}_looped.mp3"
        raw_audio_path = f"/tmp/{base_uuid}_raw"
        
        # Quick progress update
        await msg.edit(content="📥 Processing audio file... (30%)")
        
        # Save and process audio file
        audio_bytes = await audio_file.read()
        with open(raw_audio_path, "wb") as f:
            f.write(audio_bytes)
        
        # Handle video extraction if needed
        if is_valid_video_file(raw_audio_path, logger):
            logger.info("🎥 Extracting audio from video file...")
            if not extract_audio_from_video(raw_audio_path, audio_path):
                raise ProcessingError("Audio extraction failed", "❌ Failed to extract audio")
        else:
            # Copy raw audio to final path
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
        
        safe_cleanup(raw_audio_path)
        
        # Validate audio duration
        audio_duration = get_duration(audio_path)
        if audio_duration <= 0:
            raise ProcessingError("Invalid audio", "❌ Invalid audio file")
        
        # Validate time ranges against actual audio duration
        if start_seconds >= audio_duration:
            raise ValidationError(
                f"Start time exceeds duration",
                f"📏 Start time ({start_seconds:.1f}s) exceeds audio length ({audio_duration:.1f}s)"
            )
        
        if end_seconds > audio_duration:
            end_seconds = audio_duration
            logger.info(f"Adjusted end time to audio duration: {end_seconds:.1f}s")
        
        await msg.edit(content=f"🔄 Creating {loop_count}x loop... (70%)")
        
        # Create the loop
        loop_audio_ffmpeg(audio_path, start_seconds, end_seconds, loop_count, output_path)
        safe_cleanup(audio_path)
        
        await msg.edit(content="📤 Finalizing... (90%)")
        
        # Create embed with loop info
        loop_info = {
            "loop_count": loop_count,
            "segment_duration": end_seconds - start_seconds
        }
        
        embed = create_loop_embed(
            user_id=interaction.user.id,
            command_name="loopaudio",
            audio_source=audio_file.filename,
            loop_info=loop_info
        )
        
        # Send the result
        with open(output_path, "rb") as f:
            file = discord.File(f, filename="looped_audio.mp3")
        
        await msg.edit(
            content=f"<@{interaction.user.id}> ✅ Audio loop complete!",
            embed=embed,
            attachments=[file]
        )
        
        # Log the command
        safe_log_command(
            bot, interaction, "loopaudio",
            {
                "audio_source": audio_file.filename, 
                "loop_count": loop_count,
                "segment_duration": segment_duration
            },
            status="success"
        )
        
    except (ValidationError, ProcessingError):
        # Re-raise these to be handled by the caller
        raise
    except Exception as e:
        logger.exception("Audio loop processing failed")
        
        # Log the failure
        safe_log_command(
            bot, interaction, "loopaudio",
            {"audio_source": audio_file.filename},
            status="fail",
            error=str(e)
        )
        
        raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        
    finally:
        # CRITICAL: Always cleanup temp files to prevent disk space issues
        # The /tmp/ directory can fill up fast with video files
        # Even if an error occurred, we must clean up
        safe_cleanup(audio_path, output_path, raw_audio_path)

def get_soundcloud_display_url(api_url, song_title, artist):
    """Get the actual SoundCloud display URL, with fallback if track is deleted"""
    # Extract track ID from API URL
    if "api-v2.soundcloud.com/tracks/" in api_url:
        track_id = api_url.split("/tracks/")[-1].split("?")[0]
        
        # Try to get real permalink from SoundCloud API
        try:
            import requests
            response = requests.get(f"https://api-v2.soundcloud.com/tracks/{track_id}", timeout=5)
            if response.status_code == 200:
                data = response.json()
                permalink = data.get('permalink_url')
                if permalink:
                    logger.info(f"✅ Got real SoundCloud URL: {permalink}")
                    return permalink
        except Exception as e:
            logger.warning(f"⚠️ Failed to fetch SoundCloud permalink: {e}")
        
        # Fallback: Don't include URL in embed since track is likely deleted
        logger.warning(f"⚠️ SoundCloud track {track_id} appears to be deleted")
        return None
    
    return api_url  # Return original if not a SoundCloud API URL

def download_spotify_track(spotify_url: str, audio_path: str, logger):
    """
    Download Spotify track by searching YouTube for it
    
    PROCESS:
    1. Use Spotify API to get track name and artist
    2. Search YouTube for "[artist] - [song name]"
    3. Verify the result matches (artist or song in title)
    4. Download audio from YouTube
    
    WHY: Spotify doesn't allow direct downloads, so we find the same song on YouTube
    This is the same method used in database_builder.py for building the track database
    
    Note: Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in config
    """
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        logger.info("Spotify link detected! Attempting to download audio...")
        # Initialize Spotify client
        client_credentials_manager = SpotifyClientCredentials(
            client_id=Config.SPOTIFY_CLIENT_ID,
            client_secret=Config.SPOTIFY_CLIENT_SECRET
        )
        spotify = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
        
        # Extract track ID from URL
        if "/track/" in spotify_url:
            track_id = spotify_url.split("/track/")[1].split('?')[0]
        else:
            return False, "Invalid Spotify URL format"
        
        # Get track info from Spotify
        track = spotify.track(track_id)
        song_artist = track['artists'][0]['name']
        song_name = track['name']
        
        logger.info(f"🎵 Spotify track: {song_name} by {song_artist}")
        
        # Search YouTube for the track (same logic as database_builder)
        search_query = f"{song_artist} - {song_name}"
        
        yt_search_settings = {
            'quiet': True,
            'extract_flat': True
        }
        yt_searcher = YoutubeDL(yt_search_settings)
        
        search_result = yt_searcher.extract_info(f"ytsearch1:{search_query}", download=False)
        
        if not search_result["entries"]:
            return False, f"❌ Couldn't find '{song_name} by {song_artist}' on YouTube. Try using SoundCloud or download the audio manually."
        
        # Get first search result
        video_title = search_result["entries"][0]["title"]
        search_url = search_result["entries"][0]["url"]
        
        # Check if it's a reasonable match (same logic as database_builder)
        if song_artist.lower() in video_title.lower() or song_name.lower() in video_title.lower():
            logger.info(f"✅ Found YouTube match: {video_title}")
            
            # Download from YouTube
            yt_download_settings = {
                'format': 'bestaudio/best',  # Much more flexible
                'outtmpl': audio_path.replace('.mp3', ''),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'cookiefile': Config.COOKIE_FILE if hasattr(Config, 'COOKIE_FILE') else None,
                'quiet': True,
                'no_warnings': True,
            }
            
            yt_dl = YoutubeDL(yt_download_settings)
            yt_dl.download([search_url])
            
            # Check if download was successful
            final_path = resolve_mp3_path(audio_path)
            if final_path and os.path.exists(final_path):
                if final_path != audio_path:
                    os.rename(final_path, audio_path)
                logger.info(f"✅ Successfully downloaded Spotify track from YouTube")
                return True, ""
            else:
                return False, "Failed to download from YouTube"
        else:
            return False, f"❌ No good YouTube match found for '{song_name} by {song_artist}'. Try SoundCloud or download the audio manually."
            
    except Exception as e:
        logger.error(f"❌ Spotify track download failed: {e}")
        return False, f"Failed to process Spotify link: {str(e)}"
    
def handle_sfx_upload(sfx_file, base_path, video_start_seconds, video_end_seconds=None):
    """
    Handle SFX file upload, validation, and trimming to match video segment.
    
    Args:
        sfx_file: Flask file upload object
        base_path: Base path for temp files (e.g., "/tmp/uuid")
        video_start_seconds: When the video segment starts
        video_end_seconds: When the video segment ends (optional)
    
    Returns:
        str|None: Path to processed SFX file, or None if invalid/failed
    """
    if not sfx_file:
        return None
    
    sfx_path = base_path + "_sfx.mp3"
    
    try:
        # Save the SFX file
        sfx_data = sfx_file.read()
        with open(sfx_path, "wb") as f:
            f.write(sfx_data)
        
        logger.info(f"🎵 SFX uploaded: {sfx_file.filename}, size: {len(sfx_data)} bytes")
        
        # Validate SFX file
        sfx_duration = get_duration(sfx_path)
        if sfx_duration <= 0:
            logger.warning(f"⚠️ Invalid SFX file, removing: {sfx_path}")
            safe_cleanup(sfx_path)
            return None
        
        logger.info(f"🎵 SFX duration: {sfx_duration:.2f}s")
        
        # Calculate video segment duration for trimming
        video_segment_duration = None
        if video_end_seconds and video_end_seconds > video_start_seconds:
            video_segment_duration = video_end_seconds - video_start_seconds
        
        # Only trim SFX if we're trimming the video OR if SFX is longer than video segment
        should_trim_sfx = (
            video_start_seconds > 0 or 
            (video_segment_duration and sfx_duration > video_segment_duration)
        )
        
        if should_trim_sfx:
            logger.info(f"🎵 Trimming SFX from {video_start_seconds}s, duration: {video_segment_duration or 'auto'}s")
            
            # Create trimmed SFX with better error handling
            trimmed_sfx_path = sfx_path.replace(".mp3", "_trimmed.mp3")
            
            trim_cmd = [
                "ffmpeg", "-y",
                "-ss", str(video_start_seconds),
                "-i", sfx_path,
            ]
            
            if video_segment_duration:
                trim_cmd.extend(["-t", str(video_segment_duration)])
            
            trim_cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", "192k",
                "-ar", "44100",
                "-ac", "2",
                trimmed_sfx_path
            ])
            
            result = subprocess.run(trim_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode == 0 and os.path.exists(trimmed_sfx_path):
                safe_cleanup(sfx_path)  # Clean up original
                sfx_path = trimmed_sfx_path  # Use trimmed version
                logger.info(f"✅ SFX trimmed successfully to {get_duration(sfx_path):.2f}s")
            else:
                logger.warning(f"⚠️ SFX trimming failed: {result.stderr.decode()}")
                # Keep original SFX if trimming fails
        else:
            logger.info(f"🎵 No SFX trimming needed")
        
        return sfx_path
        
    except Exception as e:
        logger.error(f"❌ SFX processing failed: {e}")
        safe_cleanup(sfx_path)
        return None
    
def get_tiktok_ydl_opts(base_opts):
    """
    Enhanced yt-dlp options specifically for TikTok
    
    TIKTOK CHALLENGES:
    - Aggressive bot detection
    - Frequent user-agent blocking
    - Rate limiting
    - Regional restrictions
    
    SOLUTIONS:
    - Use mobile user agents (TikTok prefers mobile traffic)
    - Randomize user agents between requests
    - Add proper referer and origin headers
    - Use TikTok-specific API endpoints
    - Add small delays between requests
    """
    
    # TikTok-specific user agents that work well
    tiktok_user_agents = [
        # Mobile browsers (TikTok's preferred traffic)
        'Mozilla/5.0 (iPhone; CPU iPhone OS 15_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.6.1 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (iPhone; CPU iPhone OS 16_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Mobile/15E148 Safari/604.1',
        'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Mobile Safari/537.36',
        'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Mobile Safari/537.36'
    ]
    
    tiktok_opts = base_opts.copy()
    tiktok_opts.update({
        'format': 'best[height<=1080]',  # Don't try to get 4K from TikTok
        'http_headers': {
            'User-Agent': random.choice(tiktok_user_agents),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.tiktok.com/',
            'Origin': 'https://www.tiktok.com',
            'Connection': 'keep-alive',
            'Sec-Fetch-Dest': 'video',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
        },
        'extractor_args': {
            'tiktok': {
                'api_hostname': 'api.tiktokv.com',
                'app_version': '20.1.0',
                'manifest_app_version': '2018',
            }
        },
        'sleep_interval_requests': random.uniform(1, 2),  # Small delays
        'socket_timeout': 30,
        'retries': 3,
    })
    
    return tiktok_opts

def download_tiktok_with_fallbacks(video_url, output_path, retries=3):
    """Multi-method TikTok download with smart fallbacks"""
    
    # Handle shortened TikTok URLs inline
    if "/t/" in video_url:
        try:
            import requests
            response = requests.head(video_url, allow_redirects=True, timeout=10)
            resolved_url = response.url
            logger.info(f"Resolved shortened TikTok URL: {video_url[:50]}... -> {resolved_url[:50]}...")
            video_url = resolved_url
        except Exception as e:
            logger.warning(f"Failed to resolve TikTok shortened URL: {e}")

    # Method 1: Standard yt-dlp with TikTok optimization
    for attempt in range(retries):
        try:
            logger.info(f"🎵 TikTok attempt {attempt + 1}: Standard method")
            
            base_opts = {
                'outtmpl': output_path,
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
            }
            
            tiktok_opts = get_tiktok_ydl_opts(base_opts)
            
            with yt_dlp.YoutubeDL(tiktok_opts) as ydl:
                ydl.download([video_url])
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
                logger.info(f"✅ TikTok download successful (standard method)")
                return True, None
                
        except yt_dlp.utils.DownloadError as e:
            error_str = str(e)
            logger.warning(f"⚠️ TikTok attempt {attempt + 1} failed: Standard method blocked")
            
            # Check for specific errors that indicate we should stop trying
            if any(keyword in error_str.lower() for keyword in ['not found', '404', 'unavailable', 'private']):
                return False, "Video not found or unavailable"
            
            # For rate limiting, wait a bit before retry
            if "429" in error_str or "rate limit" in error_str.lower():
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                continue
            
        except Exception as e:
            logger.warning(f"⚠️ TikTok attempt {attempt + 1} unexpected error: {e}")
            
        # Small delay between retries
        if attempt < retries - 1:
            time.sleep(1)
    
    # Method 2: Try with different extractor args
    try:
        logger.info("🎵 TikTok fallback: Different extractor settings")
        
        fallback_opts = base_opts.copy()
        fallback_opts.update({
            'format': 'best',
            'http_headers': {
                'User-Agent': 'TikTok 26.2.0 rv:262018 (iPhone; iOS 14.4.2; en_US) Cronet',
                'Accept': '*/*',
            },
            'extractor_args': {
                'tiktok': {
                    'webpage_download': True,  # Force webpage extraction
                }
            }
        })
        
        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
            ydl.download([video_url])
        
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1024:
            logger.info("✅ TikTok download successful (fallback method)")
            return True, None
            
    except Exception as e:
        logger.warning(f"⚠️ TikTok fallback method failed: {e}")
    
    return False, "All TikTok download methods failed"
    
def format_tiktok_error(error: str) -> str:
    """Format TikTok-specific errors with helpful guidance"""
    if "not found" in error.lower() or "404" in error:
        return "🎵 TikTok video not found or may be private. Please check your link."
    elif "rate limit" in error.lower() or "429" in error:
        return "🎵 TikTok is temporarily limiting requests. Try again in a few minutes."
    elif "unavailable" in error.lower():
        return "🎵 TikTok video is unavailable in this region or has been removed."
    else:
        return f"🎵 TikTok download failed: {error}. Try using `/resyncmp4` as alternative."    
    
def trim_video_high_quality(input_path, start_time, end_time) -> str:
    """
    Trims a video with minimal quality loss for download purposes
    """
    output_path = input_path.replace(".mp4", "_trimmed.mp4")
    
    duration = end_time - start_time if end_time > start_time else None
    
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-c", "copy",
        "-movflags", "+faststart",
    ]
    
    # Add duration parameter properly
    if duration:
        cmd.extend(["-t", str(duration)])
    else:
        cmd.extend(["-t", str(Config.MAX_DOWNLOAD_VIDEO_DURATION)])  # Max 1 hour if no end time
    
    cmd.append(output_path)

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise ProcessingError(
            f"FFmpeg video trim failed: {result.stderr.decode()}",
            "Failed to trim video file"
        )

    return output_path

def find_downloaded_file(base_path):
    """Find the actual downloaded file when yt-dlp might have used a different extension"""
    import glob
    import os
    
    # Common video extensions yt-dlp might use
    possible_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.m4v']
    
    # First check if the exact file exists
    if os.path.exists(base_path + '.mp4'):
        return base_path + '.mp4'
    
    # Check for files with different extensions
    for ext in possible_extensions:
        potential_path = base_path + ext
        if os.path.exists(potential_path):
            logger.info(f"📁 Found downloaded file: {potential_path}")
            return potential_path
    
    # Use glob to find any file starting with our base path
    pattern = base_path + '*'
    matches = glob.glob(pattern)
    
    if matches:
        # Filter out thumbnail files and other non-video files
        video_files = [f for f in matches if not any(skip in f for skip in ['.jpg', '.png', '.txt', '.info.json'])]
        if video_files:
            logger.info(f"📁 Found downloaded file via glob: {video_files[0]}")
            return video_files[0]
    
    logger.error(f"❌ No downloaded file found for base path: {base_path}")
    return None

def download_audio_high_quality(audio_url: str, audio_path: str, logger, cookiefile=None):
    """Download audio in the highest quality possible - no compression"""
    try:
        logger.info(f"🎵 HIGH QUALITY AUDIO DOWNLOAD: {audio_url}")

        if "spotify.com/track/" in audio_url:
            return download_spotify_track(audio_url, audio_path, logger)

        if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
            audio_url = clean_youtube_url(audio_url)

        if audio_url.endswith(".mp3"):
            r = requests.get(audio_url)
            r.raise_for_status()
            with open(audio_path, "wb") as f:
                f.write(r.content)
            logger.info(f"[✅] Direct MP3 downloaded to {audio_path}")
            return True, ""
        else:
            # High-quality yt-dlp settings - best possible audio
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',  # Prefer M4A, then best audio
                'outtmpl': audio_path.replace(".mp3", ""),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '0',  # 0 = highest quality, no compression
                }],
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
            }
            
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile
                logger.info(f"🍪 Using cookies for high-quality audio download")
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([audio_url])
            
            # Find the actual downloaded file
            final_path = resolve_mp3_path(audio_path)
            if final_path and os.path.exists(final_path):
                if final_path != audio_path:
                    os.rename(final_path, audio_path)
                duration = get_duration(audio_path)
                if duration > 0:
                    logger.info(f"[✅] High-quality audio downloaded: {duration:.1f}s")
                    return True, ""
                else:
                    return False, "Invalid or corrupt audio file"
            return False, "High-quality audio download failed"
            
    except Exception as e:
        logger.warning(f"[⚠️] High-quality audio download failed: {e}")
        return False, format_user_error(e)
    
def sanitize_filename(title):
    """Clean up a title to be safe for use as a filename"""
    import re
    import unicodedata
    
    if not title:
        return "untitled"
    
    # Convert to ASCII, replacing non-ASCII chars
    # This will convert "на грани болевого порога" to something like "na_grani_bolevogo_poroga"
    title = unicodedata.normalize('NFKD', title)
    title = title.encode('ascii', 'ignore').decode('ascii')
    
    # Remove or replace problematic characters
    title = re.sub(r'[<>:"/\\|?*]', '_', title)
    
    # Replace multiple spaces/underscores with single underscore
    title = re.sub(r'[\s_]+', '_', title)
    
    # Remove leading/trailing whitespace and underscores
    title = title.strip('_').strip()
    
    # Ensure it's not empty after cleaning
    if not title or title in ['', '.', '..']:
        title = "untitled"
    
    # Limit length
    if len(title) > 100:
        title = title[:97] + "..."
    
    logger.info(f"🏷️ Sanitized filename: '{title}'")
    return title

def clean_soundcloud_url(url):
    """Clean SoundCloud URLs to remove problematic parameters"""
    try:
        parsed = urlparse(url)
        
        # For SoundCloud, we only want the base path without query parameters
        # This removes playlist info, tracking params, etc.
        cleaned_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            '',  # Remove params
            '',  # Remove query
            ''   # Remove fragment
        ))
        
        return cleaned_url
    except Exception:
        # If URL parsing fails, return original
        return url
    
def parse_instagram_carousel_index(url):
    """
    Extract carousel/album index from Instagram URL
    
    Instagram posts can have multiple images/videos (carousels)
    URL format: instagram.com/p/ABC123/?img_index=2
    
    This function:
    1. Extracts the img_index parameter (which item in the carousel)
    2. Cleans the URL by removing that parameter
    3. Returns both for yt-dlp to download the specific item
    
    Example:
        Input: "instagram.com/p/ABC/?img_index=3"
        Output: ("instagram.com/p/ABC/", 3)
    """
    try:
        parsed = urlparse(url)
        query_params = parse_qs(parsed.query)
        
        # Look for img_index parameter
        if 'img_index' in query_params:
            carousel_index = int(query_params['img_index'][0])
            
            # Clean URL by removing img_index parameter
            clean_params = {k: v for k, v in query_params.items() if k != 'img_index'}
            clean_query = '&'.join([f"{k}={v[0]}" for k, v in clean_params.items()])
            
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if clean_query:
                clean_url += f"?{clean_query}"
                
            return clean_url, carousel_index
            
        return url, 1
        
    except Exception as e:
        logger.warning(f"Failed to parse Instagram carousel index: {e}")
        return url, 1

def download_instagram_fallback(url: str, output_path: str, logger=None) -> tuple[bool, str]:
    """
    Fallback method to download Instagram videos without login,
    by scraping the direct MP4 URL from the page source.
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 15_2 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.2 Mobile/15E148 Safari/604.1"
        }

        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return False, f"Failed to load Instagram page (status {resp.status_code})"

        # Attempt to extract video URL from source
        match = re.search(r'"video_url":"([^"]+)"', resp.text)
        if not match:
            return False, "Direct video URL not found in page"

        video_url = match.group(1).replace("\\u0026", "&").replace("\\", "")
        video_resp = requests.get(video_url, headers=headers)
        if video_resp.status_code != 200:
            return False, f"Failed to download Instagram video (status {video_resp.status_code})"

        with open(output_path, "wb") as f:
            f.write(video_resp.content)

        if logger:
            logger.info(f"[✅] Fallback: Downloaded Instagram video to {output_path}")
        return True, ""
    except Exception as e:
        if logger:
            logger.warning(f"[Fallback] Instagram download failed: {e}")
        return False, str(e)   