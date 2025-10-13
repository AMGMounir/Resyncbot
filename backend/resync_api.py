"""
RESYNC API - Flask Backend Server

This is the main API server that handles all video/audio processing requests from the Discord bot.
It provides multiple resync endpoints for different use cases and handles media downloads, 
processing, and combination using FFmpeg.

MAIN ENDPOINTS:
===============
- /resyncmp4      - Resync with uploaded video + audio URL
- /resyncmp3      - Resync with uploaded video + uploaded audio file
- /resyncmedia    - Resync with video URL + audio URL
- /resyncrandomfile - Resync with uploaded video + random DB audio
- /resyncrandommedia - Resync with video URL + random DB audio
- /autoresyncmp4  - Auto-detect sync with uploaded video + audio URL
- /autoresyncmp3  - Auto-detect sync with uploaded video + uploaded audio
- /autoresyncmedia - Auto-detect sync with video URL + audio URL
- /loopaudio      - Loop a section of audio for editing inspiration
- /downloadaudio  - Download audio from URL (with optional trimming)
- /downloadvideo  - Download video from URL (with optional trimming)

WEBHOOK ENDPOINTS:
==================
- /topgg/webhook  - Handle Top.gg voting webhooks
- /stripe/webhook - Handle Stripe subscription webhooks

DEMO ENDPOINTS (for website):
==============================
- /demo/random-resync  - Demo with random audio from database
- /demo/custom-resync  - Demo with user-provided audio URL
- /demo/analyze-bpm    - Analyze BPM of audio URL
- /demo/preview-media  - Preview/stream media before processing

SECURITY:
=========
All non-demo endpoints require X-Resync-Secret header matching RESYNC_API_SECRET from .env
This prevents unauthorized access to processing endpoints.

HOW IT WORKS:
=============
1. Bot sends request to API endpoint with media files/URLs
2. API downloads media using yt-dlp (with cookies for age-restricted content)
3. API processes media with FFmpeg (trim, extract audio, detect BPM, sync)
4. API combines video + audio and returns processed file
5. Bot receives file and uploads to Discord

DEPENDENCIES:
=============
- Flask (web server)
- yt-dlp (download videos/audio from URLs)
- FFmpeg (process video/audio - trim, combine, extract)
- librosa (BPM detection and audio analysis)
- psutil (system performance monitoring)
- PostgreSQL (track database, usage tracking, progress updates)
- Stripe (premium subscriptions)

CONFIGURATION:
==============
See config.py for all configuration options. Key settings:
- RESYNC_API_SECRET: API authentication secret
- DATABASE_URL: PostgreSQL connection string
- MAX_VIDEO_DURATION: Max video length (seconds)
- MAX_FILE_SIZE: Max upload size (bytes)
- COOKIE_FILE: Path to cookies.txt for age-restricted videos

RUNNING:
========
python resync_api.py

The API runs on port 5000 by default (configurable via PORT environment variable).
In production, use a WSGI server like Gunicorn instead of Flask's development server.

IMPORTANT:
==========
- All temporary files are cleaned up after processing (in /tmp/)
- Progress updates are sent to Discord during long operations
- Error handling uses custom ValidationError and ProcessingError classes
- All user-facing errors are formatted with format_user_error() for clarity
"""

from flask import Flask, request, jsonify, send_file, abort, Response, stream_with_context
import uuid
import shutil
import psutil
import os
import time
import sys
from pathlib import Path
import yt_dlp
from dotenv import load_dotenv
import subprocess
import urllib.parse
import json
import queue
import psycopg2
load_dotenv()

# Get the absolute path of this file's directory (backend/)
BACKEND_DIR = Path(__file__).parent.absolute()
# Get the project root directory (parent of backend/)
PROJECT_ROOT = BACKEND_DIR.parent
# Get the backend directory path
BACKEND_PATH = PROJECT_ROOT / "backend"

# Add both project root and backend to Python path
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_PATH))

from resync_queue import job_queue
from werkzeug.exceptions import RequestEntityTooLarge
import requests
from config import Config
from stripe_handler import stripe_handler
from flask_cors import CORS
from multiprocessing import Manager

# Try to import psutil for direct stats
try:
    
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    print("⚠️ psutil not available - install with: pip install psutil")

from video_utils import (
    logger, parse_timestamp, safe_cleanup, combine_with_ffmpeg, 
    send_combined_video_response, trim_audio_ffmpeg, trim_video_ffmpeg,
    edit_progress, edit_progress_web, download_audio, cleanup_tmp_files, is_valid_video_file,
    get_cookiefile_for_url, download_video_with_retry, get_duration, parse_offset_string,
    format_resync_error, is_discord_cdn, extract_audio_from_video, clean_youtube_url,
    get_video_bpm, find_matching_tracks, download_audio_from_database, find_best_audio_match,
    find_best_beat_match, loop_audio_ffmpeg, handle_sfx_upload, download_tiktok_with_fallbacks,
    format_tiktok_error, trim_video_high_quality, find_downloaded_file, download_audio_high_quality,
    sanitize_filename, resolve_mp3_path)
from error_handler import ValidationError, ProcessingError, format_user_error
from voting_utils import voting_manager

app = Flask(__name__)

CORS(app, origins=["https://www.resyncbot.xyz"])
app.config["MAX_CONTENT_LENGTH"] = Config.MAX_FILE_SIZE  # 100 MB

@app.errorhandler(ValidationError)
def handle_validation_error(e):
    """Handle validation errors in API"""
    logger.warning(f"Validation error: {e}")
    return jsonify({"error": e.user_message}), 400

@app.errorhandler(ProcessingError)
def handle_processing_error(e):
    """Handle processing errors in API"""
    logger.error(f"Processing error: {e}")
    return jsonify({"error": e.user_message}), 500

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Handle unexpected errors in API"""
    if isinstance(e, (ValidationError, ProcessingError)):
        raise e  # Let the specific handlers deal with it
    logger.exception("Unexpected API error ({type(e).__name__})")
    return jsonify({"error": format_user_error(e)}), 500

def format_file_size(size_bytes):
    """Convert bytes to human readable format"""
    if size_bytes >= 1024 * 1024 * 1024:  # GB
        return f"{size_bytes / (1024 * 1024 * 1024):.1f}GB"
    elif size_bytes >= 1024 * 1024:  # MB
        return f"{size_bytes / (1024 * 1024):.1f}MB"
    elif size_bytes >= 1024:  # KB
        return f"{size_bytes / 1024:.1f}KB"
    else:
        return f"{size_bytes} bytes"

@app.errorhandler(RequestEntityTooLarge)
def handle_large_file(e):
    max_mb = Config.MAX_FILE_SIZE // (1024 * 1024)
    
    try:
        content_length = request.content_length
        if content_length:
            actual_size = format_file_size(content_length)
            return jsonify({
                "error": f"🚫 Video file too large: {actual_size}. Max upload size is {max_mb}MB. Try compressing your video or use a video URL instead."
            }), 413
    except:
        pass
    
    # Fallback if we can't get the actual size
    return jsonify({
        "error": f"🚫 Video file too large. Max upload size is {max_mb}MB. Try compressing your video or use a video URL instead."
    }), 413

# Initialize the job queue and cleanup temporary files
cleanup_tmp_files()
START_TIME = time.time()

def require_api_secret():
    """
    Verify the request has valid API secret in X-Resync-Secret header.
    This prevents unauthorized access to processing endpoints.
    Called at the start of every non-public endpoint.
    """
    secret = request.headers.get("X-Resync-Secret")
    logger.info(f"[DEBUG] Incoming X-Resync-Secret: {secret}")
    logger.info(f"[DEBUG] Expected RESYNC_API_SECRET: {Config.RESYNC_API_SECRET}")
    if not Config.RESYNC_API_SECRET or str(secret).strip() != str(Config.RESYNC_API_SECRET).strip():
        abort(401, description="Unauthorized: Invalid API Secret")

def get_current_performance_stats():
    """Get current performance stats directly using psutil"""
    if not PSUTIL_AVAILABLE:
        return {
            "health": "❌ psutil not installed",
            "metrics": {
                "cpu_percent": 0,
                "memory_percent": 0,
                "disk_usage": 0,
                "active_connections": 0,
                "uptime": int(time.time() - START_TIME)
            }
        }
    
    try:
        # Get current stats
        cpu_percent = psutil.cpu_percent(interval=0.1)  # Quick sample
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        try:
            connections = len(psutil.net_connections())
        except psutil.AccessDenied:
            connections = 0  # Fallback if permission denied
        
        # Determine health status
        if cpu_percent > 90 or memory.percent > 95 or (disk.used / disk.total) * 100 > 95:
            health = "🔴 Critical"
        elif cpu_percent > 70 or memory.percent > 80 or (disk.used / disk.total) * 100 > 85:
            health = "🟡 Warning"
        else:
            health = "🟢 Healthy"
        
        return {
            "health": health,
            "metrics": {
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(memory.percent, 1),
                "disk_usage": round((disk.used / disk.total) * 100, 1),
                "active_connections": connections,
                "uptime": int(time.time() - START_TIME)
            }
        }
    except Exception as e:
        logger.error(f"Error getting performance stats: {e}")
        return {
            "health": "❌ Error",
            "metrics": {
                "cpu_percent": 0,
                "memory_percent": 0,
                "disk_usage": 0,
                "active_connections": 0,
                "uptime": int(time.time() - START_TIME)
            }
        }

@app.route("/")
def health_check():
    return "Resync API is live!"

@app.route("/stats")
def stats():
    token = request.args.get("token", "")
    if token != Config.ADMIN_TOKEN:
        return jsonify({"error": "Unauthorized"}), 403

    try:
        from bot.bot import bot
        server_count = len(bot.guilds)
    except Exception:
        server_count = "Unknown"

    # Get performance stats
    perf_stats = get_current_performance_stats()

    return jsonify({
        "status": "🟢 running",
        "workers": Config.NUM_WORKERS,
        "queue_size": job_queue.qsize(),
        "uptime_seconds": int(time.time() - START_TIME),
        "server_count": server_count,
        
        # Performance metrics
        "performance": {
            "health": perf_stats["health"],
            "cpu_percent": perf_stats["metrics"]["cpu_percent"],
            "memory_percent": perf_stats["metrics"]["memory_percent"],
            "disk_usage": perf_stats["metrics"]["disk_usage"],
            "active_connections": perf_stats["metrics"]["active_connections"],
            "system_uptime": perf_stats["metrics"]["uptime"]
        }
    })

@app.route("/resyncmp4", methods=["POST"])
def resyncmp4():
    """Handle MP4 resync with URL audio source"""
    if request.method == "OPTIONS":
        return ("", 200)
    
    require_api_secret()
    
    token = request.form.get("token", default='')
    app_id = request.form.get("application_id", default='')
    message_id = request.form.get("message_id", default='')
    user_id = request.form.get("user_id", default='')
    video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None

    try:
        # Validate inputs
        video = request.files.get("video")
        audio_url = request.form.get("audio_url", default='')
        if not video or not audio_url:
            raise ValidationError("Missing required files", "Missing video or audio_url")
        
        # Validate timestamps
        try:
            offset_seconds = parse_offset_string(request.form.get("offset", "0"))
            video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
            video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
        except ValueError as ve:
            raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")

        # Validate audio URL
        if not audio_url.strip():
            raise ValidationError("Empty audio URL", "Missing audio URL")
        
        # Set up file paths
        base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
        video_path = f"/tmp/{uuid.uuid4()}.mp4"
        audio_path = f"/tmp/{uuid.uuid4()}.mp3"
        output_path = f"/tmp/{uuid.uuid4()}_resynced.mp4"
        
        # Sfx handling
        sfx_path = handle_sfx_upload(
            request.files.get("sfx_file"), 
            base, 
            video_start_seconds, 
            video_end_seconds
        )

        # Update Discord message to show progress (prevents "bot not responding" errors)
        # This sends progress updates back to the Discord bot which edits the user's message
        edit_progress(token, app_id, message_id, "📥 Downloading video/audio... (30%)")

        # Save uploaded video
        with open(video_path, "wb") as f:
            f.write(video.read())

        # Validate video file
        if not is_valid_video_file(video_path, logger):
            raise ProcessingError("Invalid video file", "Uploaded video file is invalid or corrupted.")

        # Download audio
        cookiefile = None
        if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
            # Get cookies file for age-restricted or login-required videos
            # Cookies are stored in data/cookies.txt and ignored by git
            # Different platforms (YouTube, TikTok) may require different cookie files
            cookiefile = get_cookiefile_for_url(audio_url)

        success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
        if not success:
            if any(keyword in error_msg for keyword in ['bot', 'authentication', 'cookies']):
                raise ProcessingError("COOKIES_ERROR", "⚠️ Spotify links are temporarily unavailable due to authentication issues. Please try using soundcloud link or an mp3 upload. We're working on a fix!")
            raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))
        
        # Get durations for validation
        audio_duration = get_duration(audio_path)
        video_duration = get_duration(video_path)

        # Validate time ranges
        if offset_seconds >= audio_duration:
            raise ValidationError(
                f"Audio offset exceeds duration", 
                f"📏 Audio start time ({offset_seconds:.1f}s) exceeds audio length ({audio_duration:.1f}s)"
            )
    
        if video_start_seconds >= video_duration:
            raise ValidationError(
                f"Video start exceeds duration",
                f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
            )
        
        if video_end_seconds and video_end_seconds > video_duration:
            video_end_seconds = video_duration

        # Process media
        edit_progress(token, app_id, message_id, "✂️ Trimming video/audio... (60%)")

        trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)
        trimmed_audio = trim_audio_ffmpeg(audio_path, offset_seconds)
        extra_headers = { "X-Audio-Offset": str(offset_seconds) }
        edit_progress(token, app_id, message_id, "🔀 Combining media... (90%)")
        
        return send_combined_video_response(trimmed_video, 
                                            trimmed_audio, 
                                            output_path, 
                                            lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                                            send_file, 
                                            logger, 
                                            extra_headers=extra_headers)

    except (ValidationError, ProcessingError):
        raise
    except Exception as e:
        logger.exception("ResyncMP4 failed")
        raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
    finally:
        # CRITICAL: Always cleanup temporary files to prevent disk space issues
        # /tmp/ can fill up quickly with video files if not cleaned properly
        safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)

@app.route("/resyncmp3", methods=["POST"])
def resyncmp3():  
    """Handle MP3 resync with uploaded video and audio files"""
    if request.method == "OPTIONS":
        return ("", 200)
    
    require_api_secret()
    token = request.form.get("token", default='')
    app_id = request.form.get("application_id", default='')
    message_id = request.form.get("message_id", default='')
    user_id = request.form.get("user_id", default='')
    video_path = audio_path = trimmed_video = trimmed_audio = output_path = raw_audio_path = sfx_path = trimmed_sfx = None

    try:
        # Validate inputs
        video = request.files.get("video")
        audio_file = request.files.get("audio_file")

        if not video or not audio_file:
            raise ValidationError("Missing required files", "Missing video or audio file")

        # Validate timestamps
        try:
            offset_seconds = parse_offset_string(request.form.get("offset", "0"))
            video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
            video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
        except ValueError as ve:
            raise ValidationError(f"Invalid timestamp format: {ve}", f"Invalid timestamp format: {ve}")
        
        # Set up file paths
        base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
        video_path = f"/tmp/{uuid.uuid4()}.mp4"
        audio_path = f"/tmp/{uuid.uuid4()}.mp3"
        output_path = f"/tmp/{uuid.uuid4()}_resynced.mp4"
        raw_audio_path = f"/tmp/{uuid.uuid4()}_raw"

        # Sfx handling
        sfx_path = handle_sfx_upload(
            request.files.get("sfx_file"), 
            base, 
            video_start_seconds, 
            video_end_seconds
        )


        edit_progress(token, app_id, message_id, "📥 Downloading video/audio... (30%)")

        # Save uploaded video
        with open(video_path, "wb") as f:
            f.write(video.read())

        # Handle audio file (might be video with audio)
        audio_bytes = audio_file.read()
        with open(raw_audio_path, "wb") as f:
            f.write(audio_bytes)

        # Check if audio file is actually a video
        is_video = is_valid_video_file(raw_audio_path, logger)
        
        if is_video:
            logger.info("🎥 Detected video file as audio source. Extracting audio...")
            if not extract_audio_from_video(raw_audio_path, audio_path):
                raise ProcessingError("Audio extraction failed", "❌ Failed to extract audio from the provided video file.")
        else:
            # Copy raw audio to final path
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)


        if not is_valid_video_file(video_path, logger):
            raise ProcessingError("Invalid video file", "Uploaded video is invalid or corrupted.")
        
        # Get durations and validate ranges
        audio_duration = get_duration(audio_path)
        video_duration = get_duration(video_path)
        
        if offset_seconds >= audio_duration:
            raise ValidationError(f"Audio offset exceeds audio length", f"Audio offset ({offset_seconds}s) exceeds audio length ({audio_duration:.2f}s).")

        if video_start_seconds >= video_duration:
            raise ValidationError("Video start time exceeds video length", f"Video start time ({video_start_seconds}s) exceeds video length ({video_duration:.2f}s).")
        
        if video_end_seconds and video_end_seconds > video_duration:
            video_end_seconds = video_duration

        # Process media
        edit_progress(token, app_id, message_id, "✂️ Trimming media... (60%)")

        trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)
        trimmed_audio = trim_audio_ffmpeg(audio_path, offset_seconds)

        edit_progress(token, app_id, message_id, "🔀 Combining... (90%)")
        extra_headers = { "X-Audio-Offset": str(offset_seconds) }
        return send_combined_video_response(trimmed_video, 
                                            trimmed_audio, 
                                            output_path, 
                                            lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                                            send_file, 
                                            logger, 
                                            extra_headers=extra_headers)

    except (ValidationError, ProcessingError):
        raise
    except Exception as e:
        logger.exception("ResyncMP3 failed")
        raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
    finally:
        # CRITICAL: Always cleanup temporary files to prevent disk space issues
        # /tmp/ can fill up quickly with video files if not cleaned properly
        safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, raw_audio_path, sfx_path)

@app.route("/resyncmedia", methods=["POST"])
def resyncmedia():
    """Handle media resync with URL sources"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()
        
        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None

        try:
            video_url = request.form.get("video_url", default='')
            audio_url = request.form.get("audio_url", default='')
            if not video_url or not audio_url:
                raise ValidationError("Missing URLs", "Missing video_url or audio_url")
            
            # Clean YouTube URLs to remove problematic parameters
            original_video_url = video_url
            video_url = clean_youtube_url(video_url)
            if video_url != original_video_url:
                logger.info(f"🧹 Cleaned YouTube URL: {original_video_url[:50]}... -> {video_url[:50]}...")
            
            # Basic URL validation
            if not video_url.startswith(("http://", "https://")):
                raise ValidationError("Invalid video URL format", "🔗 Please provide a valid video URL starting with http:// or https://")
            
            if not audio_url.startswith(("http://", "https://")):
                raise ValidationError("Invalid audio URL format", "🎵 Please provide a valid audio URL starting with http:// or https://")
            
            # Check for obviously invalid URLs
            if any(char in video_url for char in [" ", "<", ">", '"']):
                raise ValidationError("Invalid video URL characters", "🔗 Video URL contains invalid characters. Please check your link.")
                
            if any(char in audio_url for char in [" ", "<", ">", '"']):
                raise ValidationError("Invalid audio URL characters", "🎵 Audio URL contains invalid characters. Please check your link.")
            
            # Validate timestamps
            try:
                offset_seconds = parse_offset_string(request.form.get("offset", "0"))
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"⏰ Invalid timestamp format: {ve}")
            
            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "📥 Downloading video/audio... (30%)")

            # DURATION CHECK: Validate video length before downloading
            # This prevents users from submitting 2-hour videos that would timeout
            # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
            if not is_discord_cdn(video_url):
                # edit_progress(token, app_id, message_id, "🔍 Checking video duration...")
                
                try:
                    # Get video info without downloading
                    info_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,  # Explicitly disable playlist extraction

                        'age_limit': 99,
                        'geo_bypass': True,
                        'geo_bypass_country': 'US',

                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        },

                        'youtube_include_dash_manifest': False,
                        'extractor_args': {
                            'youtube': {
                                'skip': ['hls', 'dash'],
                            }
                        },

                        'writesubtitles': False,
                        'writeautomaticsub': False,
                        'writedescription': False,
                        'writeinfojson': False,
                    }
                    
                    # Get cookies file for age-restricted or login-required videos
                    # Cookies are stored in data/cookies.txt and ignored by git
                    # Different platforms (YouTube, TikTok) may require different cookie files
                    cookiefile = get_cookiefile_for_url(video_url)
                    if cookiefile:
                        info_opts['cookiefile'] = cookiefile
                    
                    logger.info(f"🔍 Getting video info for URL: {video_url[:50]}...")
                    with yt_dlp.YoutubeDL(info_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        raise ProcessingError("No video info found", "🔗 Invalid video URL. Please check your link and try again.")
                    
                    video_duration_total = info.get('duration', 0)
                    
                    if 'instagram.com' not in video_url and video_duration_total:
                        duration_minutes = video_duration_total / 60
                        logger.info(f"Video duration: {duration_minutes:.1f} minutes")
                        
                        if video_duration_total > Config.MAX_VIDEO_DURATION:
                            raise ValidationError(
                                f"Video too long: {duration_minutes:.1f} minutes",
                                f"📏 This video is {duration_minutes:.1f} minutes long. "
                                f"Videos longer than {Config.MAX_VIDEO_DURATION // 60} minutes are not supported. "
                                f"Please use a shorter video or manually download and use `/autoresyncmp4`."
                            )
                    elif 'instagram.com' in video_url:
                        logger.info("Instagram video detected - skipping duration validation")
                    else:
                        # For non-Instagram videos without duration, still error
                        if video_duration_total == 0:
                            raise ProcessingError("Invalid video source", "Invalid video URL...")
                        
                except ValidationError:
                    # Re-raise validation errors to stop the function
                    raise
                except ProcessingError:
                    # Re-raise processing errors to stop the function
                    raise
                except yt_dlp.utils.DownloadError as e:
                    error_str = str(e).lower()
                    logger.info(f"🔍 DEBUG: Instagram check - video_url contains instagram: {'instagram.com' in video_url}")
                    logger.info(f"🔍 DEBUG: Error string: {error_str}")
                    logger.info(f"🔍 DEBUG: Contains rate-limit: {'rate-limit' in error_str}")
                    logger.info(f"🔍 DEBUG: Contains login required: {'login required' in error_str}")
                    if 'instagram.com' in video_url and any(keyword in error_str for keyword in ['rate-limit', 'login required', 'not available']):
                        raise ProcessingError(
                            "Instagram video failed",
                            "📸 Instagram is blocking my download requests right now. "
                            "This is expected with Instagram and happens sometimes, please upload the video using one of the file resync commands instead, or try a YouTube link."
                        )
                    if any(keyword in error_str for keyword in ['not found', '404', 'unavailable', 'private', 'removed']):
                        raise ProcessingError("VIDEO_NOT_FOUND", "🔗 Video not found or unavailable. Please check your video link.")
                    elif any(keyword in error_str for keyword in ['unsupported', 'no suitable formats']):
                        raise ProcessingError("VIDEO_UNSUPPORTED", "🔗 Unsupported video source or format. Please try a different video.")
                    elif any(keyword in error_str for keyword in ['bot', 'authentication', 'cookies']):
                        raise ProcessingError("COOKIES_ERROR", "⚠️ YouTube videos are temporarily unavailable due to authentication issues. Please try using a file upload or a different video source for now. We're working on a fix!")
                    else:
                        raise ProcessingError("VIDEO_ACCESS_FAILED", f"🔗 Could not access video: {str(e)}")
                except Exception as e:
                    logger.warning(f"⚠️ Duration check failed: {e}")
                    raise ProcessingError("VIDEO_VALIDATION_FAILED", "🔗 Invalid video URL or could not validate video. Please check your video link.")
                except Exception as e:
                    logger.warning(f"⚠️ Duration check failed: {e}")
                    # For any other error during duration check, assume invalid URL
                    raise ProcessingError("VIDEO_VALIDATION_FAILED", "🔗 Invalid video URL or could not validate video. Please check your video link.")
            
            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = base + "_video.mp4"
            audio_path = base + "_audio.mp3"
            output_path = base + "_resynced.mp4"

            # Sfx handling
            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )


            # Configure yt-dlp options
            # These settings optimize for quality while avoiding YouTube restrictions
            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio[height<=1080]/best[height<=1080]', # Select best quality up to 1080p
                'outtmpl': video_path, # Bypass age restrictions using cookies
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
                'ffmpeg_location': shutil.which("ffmpeg") or "/usr/bin/ffmpeg",
                
                # Age / Geo bypass stuff
                'age_limit': 99,              # Bypass age restrictions
                'geo_bypass': True,           # Bypass geo-restrictions
                'geo_bypass_country': 'US',   # Pretend to be from US
                'extractor_args': {
                    'youtube': {
                        'skip': ['hls', 'dash'],  # Sometimes helps with restricted content
                    }
                },

                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                },

                # Some checks that might trigger restrictions:
                'writesubtitles': False,
                'writeautomaticsub': False,
                'writedescription': False,
                'writeinfojson': False,
            }

            cookiefile = get_cookiefile_for_url(video_url)
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile
            
            # Download media
            edit_progress(token, app_id, message_id, "📥 Downloading video/audio... (30%)")

            try:
                if is_discord_cdn(video_url):
                    r = requests.get(video_url)
                    r.raise_for_status()
                    with open(video_path, "wb") as f:
                        f.write(r.content)
                    logger.info(f"[✅] Downloaded Discord CDN video to {video_path}")
                elif 'tiktok.com' in video_url:
                    # TikTok-specific handling
                    logger.info(f"🎵 TikTok video detected: {video_url}")
                    success, error_msg = download_tiktok_with_fallbacks(video_url, video_path)
                    
                    if not success:
                        raise ProcessingError("TikTok download failed", format_tiktok_error(error_msg))
                else:
                    try:
                        download_video_with_retry(video_url, ydl_opts)
                    except RuntimeError as e:
                        if "Instagram" in str(e):
                            raise ProcessingError(
                                "Instagram video failed",
                                "📸 Instagram is blocking this download right now. "
                                "Please upload the video using one of the file resync commands instead."
                            )
                        if "cookies_expired" in str(e):
                            raise ProcessingError(
                                "Cookies expired",
                                "Looks like my cookies have expired! Try using one of the file resync commands until I update them."
                            )
                        raise ProcessingError("Video download failed", f"🔗 Failed to download video: {e}")

                    logger.info(f"[✅] Downloaded video to {video_path}")
                        
                    # Validate that we actually got a proper video file
                    if not is_valid_video_file(video_path, logger):
                        if 'instagram.com' in video_url:
                            raise ProcessingError("Instagram video unreadable", "🔗 This Instagram video couldn't be processed. Some Instagram carousel items can't be downloaded. Try a different video or use the direct video file upload instead.")
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    # Check if the downloaded file is suspiciously small (likely an error page)
                    if os.path.exists(video_path):
                        file_size = os.path.getsize(video_path)
                        if file_size < 1024:
                            raise ProcessingError("Downloaded file too small", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                        
            except requests.exceptions.RequestException as e:
                # Handle Discord CDN and other HTTP errors
                raise ProcessingError("HTTP download failed", f"🔗 Could not download video: Invalid URL or network error.")
            except RuntimeError as e:
                if str(e) == "cookies_expired":
                    raise ProcessingError(
                        "Cookies expired",
                        "Looks like my cookies have expired! I'll need to refresh them soon. In the meantime, try using /resyncmp4 or /resyncmp3!"
                    )
                raise ProcessingError(f"Video download failed: {e}", f"Video download failed: {e}")
            except Exception as e:
                logger.exception("Unexpected error during video download")
                raise ProcessingError(f"Video download error: {e}", format_user_error(e))
            logger.info(f"[✅] Downloaded video to {video_path}")

            cookiefile = get_cookiefile_for_url(audio_url)
            success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
            if not success:
                if any(keyword in error_msg for keyword in ['bot', 'authentication', 'cookies']):
                    raise ProcessingError("COOKIES_ERROR", "⚠️ Spotify links are temporarily unavailable due to authentication issues. Please try using soundcloud link or an mp3 upload. We're working on a fix!")
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Validate time ranges
            audio_duration = get_duration(audio_path)
            video_duration = get_duration(video_path)

            # Check if we got a valid video duration
            if video_duration <= 0:
                raise ProcessingError("Invalid video file", "🔗 Invalid video URL or corrupted video file. Please check your link.")

            if offset_seconds >= audio_duration:
                raise ValidationError(
                    f"Audio offset exceeds duration", 
                    f"📏 Audio start time ({offset_seconds:.1f}s) exceeds audio length ({audio_duration:.1f}s)"
                )

            if video_start_seconds >= video_duration:
                # Give better error for very short videos (likely invalid downloads)
                if video_duration < 5:  # Less than 5 seconds is suspicious
                    raise ProcessingError("Invalid or very short video", "🔗 Invalid video URL or the downloaded video is too short. Please check your link.")
                else:
                    raise ValidationError(
                        f"Video start exceeds duration",
                        f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                    )
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            # Process media
            edit_progress(token, app_id, message_id, "✂️ Trimming video/audio... (60%)")

            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)
            trimmed_audio = trim_audio_ffmpeg(audio_path, offset_seconds)

            edit_progress(token, app_id, message_id, "🔀 Combining... (90%)")

            extra_headers = {"X-Video-URL": video_url,
                            "X-Audio-Offset": str(offset_seconds)}

            return send_combined_video_response(trimmed_video, 
                                                trimmed_audio, 
                                                output_path,
                                                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id), 
                                                send_file, 
                                                logger, 
                                                extra_headers=extra_headers)

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("Unexpected error in resync_media")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[MEDIA ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[MEDIA UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500

@app.route("/resyncrandomfile", methods=["POST"])
def resyncrandomfile():
    """Handle file resync with audio from database"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()
        
        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None
        
        try:
            # Validate inputs
            video = request.files.get("video")
            if not video:
                raise ValidationError("Missing required files", "Missing video file")
            
            # Validate timestamps
            try:
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")

            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = f"/tmp/{uuid.uuid4()}.mp4"
            audio_path = f"/tmp/{uuid.uuid4()}.mp3"
            output_path = f"/tmp/{uuid.uuid4()}_resynced.mp4"
            
            # Sfx handling
            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )

            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "📥 Processing uploaded video... (20%)")

            # Save uploaded video
            with open(video_path, "wb") as f:
                f.write(video.read())

            # Validate video file
            if not os.path.exists(video_path):
                raise ProcessingError("File save failed", "❌ Failed to save uploaded video")

            file_size = os.path.getsize(video_path)
            if file_size == 0:
                raise ProcessingError("Empty video file", "❌ Uploaded video file is empty")

            if file_size < 1024:
                raise ProcessingError("Video file too small", "❌ Uploaded video file appears to be corrupted")

            # Get video duration for validation
            video_duration = get_duration(video_path)

            # Validate time ranges BEFORE trimming
            if video_start_seconds >= video_duration:
                raise ValidationError(
                    f"Video start exceeds duration",
                    f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                )
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            # Trim video first before bpm analysis, 
            edit_progress(token, app_id, message_id, "✂️ Trimming video to analyze section... (30%)")
            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)

            # Extract BPM from TRIMMED video
            edit_progress(token, app_id, message_id, "🎵 Analyzing video's audio BPM... (40%)")
            video_bpm = get_video_bpm(trimmed_video)  # Use trimmed_video instead of video_path
            if not video_bpm:
                raise ProcessingError("BPM detection failed", "❌ Could not analyze the audio in your video")

            logger.info(f"🎵 Video BPM detected from trimmed section: {video_bpm}")

            # Find matching track in database
            edit_progress(token, app_id, message_id, f"🎲 Finding random track matching BPM {video_bpm}... (60%)")
            selected_track = find_matching_tracks(video_bpm, tolerance=5)
            if not selected_track:
                raise ProcessingError(
                    "No matching tracks", 
                    f"❌ No audio tracks found matching BPM {video_bpm}±5. Try a different video!"
                )

            logger.info(f"🎲 Selected track: {selected_track['song']} by {selected_track['uploader']}")

            # Download the selected audio track
            edit_progress(token, app_id, message_id, f"📥 Downloading selected song... (70%)")
            success, error_msg = download_audio_from_database(
                selected_track["song"], 
                selected_track["uploader"], 
                selected_track["platform"],
                selected_track["song_id"],
                audio_path
            )
            if not success:
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Get audio duration for validation
            audio_duration = get_duration(audio_path)

            # INTELLIGENT AUTO-SYNC: Automatically find where audio should start
            # This extracts audio from the video and compares it against the full audio track
            # to find the best matching point (where the audio "lines up")
            temp_video_audio = f"/tmp/{uuid.uuid4()}_video_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn",  # Use trimmed_video instead of video_path
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Find best sync point using both waveform and beat analysis
            edit_progress(token, app_id, message_id, "🎯 Finding optimal sync point... (75%)")
            waveform_offset = find_best_audio_match(temp_video_audio, audio_path)
            audio_start_offset = waveform_offset 

            logger.info(f"🎯 Using audio offset: {audio_start_offset:.2f}s (based on trimmed video)")

            audio_start_offset = min(audio_start_offset, audio_duration - 30)

            # Trim audio based on calculated offset
            edit_progress(token, app_id, message_id, "✂️ Trimming audio... (85%)")
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress(token, app_id, message_id, "🔀 Combining media... (95%)")
            
            track_headers = {
                "X-Selected-Song": urllib.parse.quote(selected_track['song'].encode('utf-8')),
                "X-Selected-Artist": urllib.parse.quote(selected_track['uploader'].encode('utf-8')), 
                "X-Selected-URL": selected_track['url'],  # URLs should be fine
                "X-Selected-Platform": selected_track['platform'],  # Should be "soundcloud" or "spotify"
                "X-Audio-Offset": str(audio_start_offset)
            }

            return send_combined_video_response(trimmed_video, 
                                                trimmed_audio, 
                                                output_path, 
                                                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                                                send_file, 
                                                logger, 
                                                extra_headers=track_headers)

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("ResyncrandomFile failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[RANDFILE ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[RANDFILE UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/resyncrandommedia", methods=["POST"])
def resyncrandommedia():
    """Handle media resync with video URL and random database audio"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()
        
        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')  
        user_id = request.form.get("user_id", default='')
        video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None

        try:
            # Get video URL
            video_url = request.form.get("video_url", default='')
            if not video_url:
                raise ValidationError("Missing video URL", "Missing video_url")
            
            # Clean YouTube URLs to remove problematic parameters
            original_video_url = video_url
            video_url = clean_youtube_url(video_url)
            if video_url != original_video_url:
                logger.info(f"🧹 Cleaned YouTube URL: {original_video_url[:50]}... -> {video_url[:50]}...")
            
            # Validate timestamps
            try:
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"⏰ Invalid timestamp format: {ve}")
            
            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "🔍 Validating video URL... (20%)")
            
            # DURATION CHECK: Validate video length before downloading
            # This prevents users from submitting 2-hour videos that would timeout
            # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
            if not is_discord_cdn(video_url):
                try:
                    # Get video info without downloading
                    info_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,

                        'age_limit': 99,
                        'geo_bypass': True,
                        'geo_bypass_country': 'US',

                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        },

                        'youtube_include_dash_manifest': False,
                        'extractor_args': {
                            'youtube': {
                                'skip': ['hls', 'dash'],
                            }
                        },

                        
                    }

                    # Get cookies file for age-restricted or login-required videos
                    # Cookies are stored in data/cookies.txt and ignored by git
                    # Different platforms (YouTube, TikTok) may require different cookie files
                    cookiefile = get_cookiefile_for_url(video_url)
                    if cookiefile:
                        info_opts['cookiefile'] = cookiefile
                    
                    logger.info(f"🔍 Getting video info for URL: {video_url[:50]}...")
                    with yt_dlp.YoutubeDL(info_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        raise ProcessingError("No video info found", "🔗 Invalid video URL. Please check your link and try again.")
                    
                    video_duration_total = info.get('duration', 0)
                    
                    if 'instagram.com' not in video_url and video_duration_total:
                        duration_minutes = video_duration_total / 60
                        logger.info(f"Video duration: {duration_minutes:.1f} minutes")
                        
                        if video_duration_total > Config.MAX_VIDEO_DURATION:
                            raise ValidationError(
                                f"Video too long: {duration_minutes:.1f} minutes",
                                f"📏 This video is {duration_minutes:.1f} minutes long. "
                                f"Videos longer than {Config.MAX_VIDEO_DURATION // 60} minutes are not supported. "
                                f"Please use a shorter video or manually download and use `/autoresyncmp4`."
                            )
                    elif 'instagram.com' in video_url:
                        logger.info("Instagram video detected - skipping duration validation")
                    else:
                        # For non-Instagram videos without duration, still error
                        if video_duration_total == 0:
                            raise ProcessingError("Invalid video source", "Invalid video URL...")
                        
                except ValidationError:
                    raise
                except ProcessingError:
                    raise
                except yt_dlp.utils.DownloadError as e:
                    error_str = str(e).lower()
                    logger.info(f"🔍 DEBUG: Instagram check - video_url contains instagram: {'instagram.com' in video_url}")
                    logger.info(f"🔍 DEBUG: Error string: {error_str}")
                    logger.info(f"🔍 DEBUG: Contains rate-limit: {'rate-limit' in error_str}")
                    logger.info(f"🔍 DEBUG: Contains login required: {'login required' in error_str}")
                    if 'instagram.com' in video_url and any(keyword in error_str for keyword in ['rate-limit', 'login required', 'not available']):
                        raise ProcessingError(
                            "Instagram video failed",
                            "📸 Instagram is blocking my download requests right now. "
                            "This is expected with Instagram and happens sometimes, please upload the video using one of the file resync commands instead, or try a YouTube link."
                        )
                    if any(keyword in error_str for keyword in ['not found', '404', 'unavailable', 'private', 'removed']):
                        raise ProcessingError("VIDEO_NOT_FOUND", "🔗 Video not found or unavailable. Please check your video link.")
                    elif any(keyword in error_str for keyword in ['unsupported', 'no suitable formats']):
                        raise ProcessingError("VIDEO_UNSUPPORTED", "🔗 Unsupported video source or format. Please try a different video.")
                    elif any(keyword in error_str for keyword in ['bot', 'authentication', 'cookies']):
                        raise ProcessingError("COOKIES_ERROR", "⚠️ YouTube videos are temporarily unavailable due to authentication issues. Please try using a file upload or a different video source for now. We're working on a fix!")
                    else:
                        raise ProcessingError("VIDEO_ACCESS_FAILED", f"🔗 Could not access video: {str(e)}")
                except Exception as e:
                    logger.warning(f"⚠️ Duration check failed: {e}")
                    raise ProcessingError("VIDEO_VALIDATION_FAILED", "🔗 Invalid video URL or could not validate video. Please check your video link.")
            
            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = base + "_video.mp4"
            audio_path = base + "_audio.mp3"
            output_path = base + "_resynced.mp4"

            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )

            # Download video
            edit_progress(token, app_id, message_id, "📥 Downloading video... (30%)")
            
            # Configure yt-dlp options
            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio[height<=1080]/best[height<=1080]',
                'outtmpl': video_path,
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
                'ffmpeg_location': shutil.which("ffmpeg") or "/usr/bin/ffmpeg",
                
                # Age / Geo bypass stuff
                'age_limit': 99,              # Bypass age restrictions
                'geo_bypass': True,   
                'geo_bypass_country': 'US',   # Pretend to be from US
                'extractor_args': {
                    'youtube': {
                        'skip': ['hls', 'dash'],  # Sometimes helps with restricted content
                    }
                },
                # Some checks that might trigger restrictions:
                'writesubtitles': False,
                'writeautomaticsub': False,
                'writedescription': False,
                'writeinfojson': False,
            }
            # In resyncrandommedia, before the download_video_with_retry call:
            logger.info(f"[DEBUG] yt-dlp options: {ydl_opts}")
            logger.info(f"[DEBUG] URL being downloaded: {video_url}")

            cookiefile = get_cookiefile_for_url(video_url)
            if cookiefile:
                logger.info(f"🍪 DEBUG: Cookie file path: {cookiefile}")
                logger.info(f"🍪 DEBUG: Cookie file exists: {os.path.exists(cookiefile)}")
                if os.path.exists(cookiefile):
                    logger.info(f"🍪 DEBUG: Cookie file size: {os.path.getsize(cookiefile)} bytes")
                    # Read first few lines to verify format
                    try:
                        with open(cookiefile, 'r') as f:
                            first_lines = f.readlines()[:3]
                            logger.info(f"🍪 DEBUG: First 3 lines: {first_lines}")
                    except Exception as e:
                        logger.error(f"🍪 DEBUG: Can't read cookie file: {e}")
                ydl_opts['cookiefile'] = cookiefile
            else:
                logger.error("🍪 DEBUG: NO COOKIEFILE RETURNED!")
                
            try:
                if is_discord_cdn(video_url):
                    r = requests.get(video_url)
                    r.raise_for_status()
                    with open(video_path, "wb") as f:
                        f.write(r.content)
                    logger.info(f"[✅] Downloaded Discord CDN video to {video_path}")
                elif 'tiktok.com' in video_url:
                    # TikTok-specific handling
                    logger.info(f"🎵 TikTok video detected: {video_url}")
                    success, error_msg = download_tiktok_with_fallbacks(video_url, video_path)
                    
                    if not success:
                        raise ProcessingError("TikTok download failed", format_tiktok_error(error_msg))
                else:
                    try:
                        download_video_with_retry(video_url, ydl_opts)
                    except RuntimeError as e:
                        if "Instagram" in str(e):
                            raise ProcessingError(
                                "Instagram video failed",
                                "📸 Instagram is blocking this download right now. "
                                "Please upload the video using `/resyncmp4` instead."
                            )
                        if "cookies_expired" in str(e):
                            raise ProcessingError(
                                "Cookies expired",
                                "Looks like my cookies have expired! Try using `/resyncmp4` or `/resyncmp3` until I update them."
                            )
                        raise ProcessingError("Video download failed", f"🔗 Failed to download video: {e}")
                    
                    logger.info(f"[✅] Downloaded video to {video_path}")

                    if not is_valid_video_file(video_path, logger):
                        if 'instagram.com' in video_url:
                            raise ProcessingError("Instagram video unreadable", "🔗 This Instagram video couldn't be processed. Some Instagram carousel items can't be downloaded. Try a different video or use the direct video file upload instead.")
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    # Validate that we actually got a proper video file
                    if not is_valid_video_file(video_path, logger):
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    # Check if the downloaded file is suspiciously small
                    if os.path.exists(video_path):
                        file_size = os.path.getsize(video_path)
                        if file_size < 1024:  # Less than 1KB is probably an error
                            raise ProcessingError("Downloaded file too small", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                        
            except requests.exceptions.RequestException as e:
                raise ProcessingError("HTTP download failed", f"🔗 Could not download video: Invalid URL or network error.")
            except RuntimeError as e:
                if str(e) == "cookies_expired":
                    raise ProcessingError(
                        "VIDEO_COOKIES_EXPIRED", 
                        "🔗 Looks like my cookies have expired! I'll need to refresh them soon. In the meantime, try using /resyncrandomfile!"
                    )
                raise ProcessingError(f"Video download failed: {e}", f"Video download failed: {e}")
            except Exception as e:
                logger.exception("Unexpected error during video download")
                raise ProcessingError(f"Video download error: {e}", format_user_error(e))

            # Get video duration for validation
            video_duration = get_duration(video_path)

            # Check if we got a valid video duration
            if video_duration <= 0:
                raise ProcessingError("Invalid video file", "🔗 Invalid video URL or corrupted video file. Please check your link.")

            # Validate time ranges BEFORE trimming
            if video_start_seconds >= video_duration:
                if video_duration < 5:  # Less than 5 seconds is suspicious
                    raise ProcessingError("Invalid or very short video", "🔗 Invalid video URL or the downloaded video is too short. Please check your link.")
                else:
                    raise ValidationError(
                        f"Video start exceeds duration",
                        f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                    )
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            # TRIM VIDEO FIRST - before BPM analysis
            edit_progress(token, app_id, message_id, "✂️ Trimming video to analyze section... (40%)")
            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)

            # Extract BPM from TRIMMED video
            edit_progress(token, app_id, message_id, "🎵 Analyzing video's BPM... (50%)")
            video_bpm = get_video_bpm(trimmed_video)
            if not video_bpm:
                raise ProcessingError("BPM detection failed", "❌ Could not analyze the audio in your video")

            logger.info(f"🎵 Video BPM detected from trimmed section: {video_bpm}")

            # Find matching track in database
            edit_progress(token, app_id, message_id, f"🎲 Finding random track matching BPM {video_bpm}... (60%)")
            selected_track = find_matching_tracks(video_bpm, tolerance=5)
            if not selected_track:
                raise ProcessingError(
                    "No matching tracks", 
                    f"❌ No audio tracks found matching BPM {video_bpm}±5. Try a different video!"
                )

            logger.info(f"🎲 Selected track: {selected_track['song']} by {selected_track['uploader']}")

            # Download the selected audio track
            edit_progress(token, app_id, message_id, f"📥 Downloading selected song... (70%)")
            success, error_msg = download_audio_from_database(
                selected_track["song"], 
                selected_track["uploader"], 
                selected_track["platform"],
                selected_track["song_id"],
                audio_path
            )
            if not success:
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Get audio duration for validation
            audio_duration = get_duration(audio_path)

            # INTELLIGENT AUTO-SYNC: Automatically find where audio should start
            # This extracts audio from the video and compares it against the full audio track
            # to find the best matching point (where the audio "lines up")
            temp_video_audio = f"/tmp/{uuid.uuid4()}_video_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn",
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Find best sync point
            edit_progress(token, app_id, message_id, "🔊 Finding optimal sync point... (75%)")
            audio_start_offset = find_best_audio_match(temp_video_audio, audio_path)
            logger.info(f"🎯 Using audio offset: {audio_start_offset:.2f}s (based on trimmed video)")

            audio_start_offset = min(audio_start_offset, audio_duration - 30)

            # Trim audio based on calculated offset
            edit_progress(token, app_id, message_id, "✂️ Trimming audio... (85%)")
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress(token, app_id, message_id, "🔀 Combining media... (95%)")
            
            track_headers = {
                "X-Selected-Song": urllib.parse.quote(selected_track['song'].encode('utf-8')),
                "X-Selected-Artist": urllib.parse.quote(selected_track['uploader'].encode('utf-8')), 
                "X-Selected-URL": selected_track['url'], 
                "X-Selected-Platform": selected_track['platform'], 
                "X-Audio-Offset": str(audio_start_offset),
                "X-Video-URL": video_url
            }

            return send_combined_video_response(
                trimmed_video, 
                trimmed_audio, 
                output_path, 
                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                send_file, 
                logger, 
                extra_headers=track_headers
            )

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("resyncrandommedia failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[RESYNCRANDOM ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[RESYNCRANDOM UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/autoresyncmp4", methods=["POST"])
def autoresyncmp4():
    """Handle auto MP4 resync with intelligent sync detection"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()

        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        sync_method = request.form.get("sync_method", "waveform")

        video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None

        try:
            # Validate inputs
            video = request.files.get("video")
            audio_url = request.form.get("audio_url", default='')
            if not video or not audio_url:
                raise ValidationError("Missing required files", "Missing video or audio_url")
            
            # Validate timestamps
            try:
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0")) 
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")

            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = f"/tmp/{uuid.uuid4()}.mp4"
            audio_path = f"/tmp/{uuid.uuid4()}.mp3"
            output_path = f"/tmp/{uuid.uuid4()}_resynced.mp4"
            
            # Sfx paths
            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )

            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "📥 Processing uploaded video... (20%)")

            # Save uploaded video
            with open(video_path, "wb") as f:
                f.write(video.read())

            # Validate video file
            if not is_valid_video_file(video_path, logger):
                raise ProcessingError("Invalid video file", "Uploaded video file is invalid or corrupted.")

            # Download audio
            edit_progress(token, app_id, message_id, "📥 Downloading audio... (30%)")

            # Get cookies file for age-restricted or login-required videos
            # Cookies are stored in data/cookies.txt and ignored by git
            # Different platforms (YouTube, TikTok) may require different cookie files
            cookiefile = None
            if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
                cookiefile = get_cookiefile_for_url(audio_url)
            success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
            if not success:
                if any(keyword in error_msg for keyword in ['bot', 'authentication', 'cookies']):
                    raise ProcessingError("COOKIES_ERROR", "⚠️ Spotify links are temporarily unavailable due to authentication issues. Please try using soundcloud link or an mp3 upload. We're working on a fix!")
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Get durations for validation
            audio_duration = get_duration(audio_path)
            video_duration = get_duration(video_path)

            # Validate time ranges
            if video_start_seconds >= video_duration:
                raise ValidationError(
                    f"Video start exceeds duration",
                    f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                )
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            max_video_duration = min(video_duration, video_start_seconds + Config.MAX_DURATION)
            if video_end_seconds == 0 or video_end_seconds > max_video_duration:
                video_end_seconds = max_video_duration

            edit_progress(token, app_id, message_id, "✂️ Trimming video... (40%)")
            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)

            # INTELLIGENT AUTO-SYNC: Automatically find where audio should start
            # This extracts audio from the video and compares it against the full audio track
            # to find the best matching point (where the audio "lines up")
            edit_progress(token, app_id, message_id, "🤖 Automatically finding best sync point... (50%)")
            temp_video_audio = f"/tmp/{uuid.uuid4()}_video_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn", 
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Three sync detection methods available:
            # - "waveform": Compare audio waveforms (fast, works well for most content)
            # - "beat": Analyze beat patterns using BPM (better for music videos)
            # - "both": Use both methods and pick the best result (most accurate but slower)
            if sync_method == "waveform":
                edit_progress(token, app_id, message_id, "🔊 Analyzing audio waveforms... (60%)")
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "beat":
                edit_progress(token, app_id, message_id, "🎵 Analyzing beat patterns... (60%)")
                # First detect BPM of video for beat matching
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    audio_start_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                else:
                    logger.warning("Could not detect video BPM, falling back to waveform matching")
                    audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "both":
                edit_progress(token, app_id, message_id, "🎯 Using both waveform and beat analysis... (60%)")
                waveform_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
                
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    beat_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                    
                    # Actually choose the better result
                    offset_difference = abs(waveform_offset - beat_offset)
                    if offset_difference < 2.0:  # If they agree within 2 seconds
                        audio_start_offset = waveform_offset
                        method_used = "waveform (consensus)"
                    else:
                        audio_start_offset = waveform_offset 
                        method_used = "waveform (divergent)"
                    logger.info(f"🎯 Waveform: {waveform_offset:.2f}s, Beat: {beat_offset:.2f}s - using {method_used}")
                else:
                    audio_start_offset = waveform_offset
            else:
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)

            logger.info(f"🤖 Auto-detected best audio offset: {audio_start_offset:.2f}s")

            audio_start_offset = min(audio_start_offset, audio_duration - 30)

            # Process media
            edit_progress(token, app_id, message_id, "✂️ Trimming audio to sync point... (80%)")
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress(token, app_id, message_id, "🔀 Combining... (90%)")

            # Add audio offset to response headers for display
            extra_headers = {"X-Audio-Offset": str(audio_start_offset)}

            return send_combined_video_response(
                trimmed_video, 
                trimmed_audio, 
                output_path, 
                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                send_file, 
                logger,
                extra_headers=extra_headers
            )

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("AutoResyncmp4 failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[AUTOMP4 ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[AUTOMP4 UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/autoresyncmp3", methods=["POST"])
def autoresyncmp3():
    """Handle auto MP3 resync with intelligent sync detection"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()

        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        sync_method = request.form.get("sync_method", "waveform")

        video_path = audio_path = trimmed_video = trimmed_audio = output_path = raw_audio_path = sfx_path = trimmed_sfx = None

        try:
            # Validate inputs
            video = request.files.get("video")
            audio_file = request.files.get("audio_file")

            if not video or not audio_file:
                raise ValidationError("Missing required files", "Missing video or audio file")

            # Validate timestamps
            try:
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp format: {ve}", f"Invalid timestamp format: {ve}")
            
            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = f"/tmp/{uuid.uuid4()}.mp4"
            audio_path = f"/tmp/{uuid.uuid4()}.mp3"
            output_path = f"/tmp/{uuid.uuid4()}_resynced.mp4"
            raw_audio_path = f"/tmp/{uuid.uuid4()}_raw"

            # Sfx handling
            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )

            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "📥 Processing uploads... (20%)")

            # Save uploaded video
            with open(video_path, "wb") as f:
                f.write(video.read())

            # Handle audio file (might be video with audio)
            audio_bytes = audio_file.read()
            with open(raw_audio_path, "wb") as f:
                f.write(audio_bytes)

            # Check if audio file is actually a video
            is_video = is_valid_video_file(raw_audio_path, logger)
            
            if is_video:
                logger.info("🎥 Detected video file as audio source. Extracting audio...")
                if not extract_audio_from_video(raw_audio_path, audio_path):
                    raise ProcessingError("Audio extraction failed", "❌ Failed to extract audio from the provided video file.")
            else:
                # Copy raw audio to final path
                with open(audio_path, "wb") as f:
                    f.write(audio_bytes)


            if not is_valid_video_file(video_path, logger):
                raise ProcessingError("Invalid video file", "Uploaded video is invalid or corrupted.")
            
            # Get durations and validate ranges
            audio_duration = get_duration(audio_path)
            video_duration = get_duration(video_path)

            if video_start_seconds >= video_duration:
                raise ValidationError("Video start time exceeds video length", f"Video start time ({video_start_seconds}s) exceeds video length ({video_duration:.2f}s).")
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            max_video_duration = min(video_duration, video_start_seconds + Config.MAX_DURATION)

            if video_end_seconds == 0 or video_end_seconds > max_video_duration:
                video_end_seconds = max_video_duration

            edit_progress(token, app_id, message_id, "✂️ Trimming video... (40%)")
            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)

            # INTELLIGENT AUTO-SYNC: Automatically find where audio should start
            # This extracts audio from the video and compares it against the full audio track
            # to find the best matching point (where the audio "lines up")
            edit_progress(token, app_id, message_id, "🤖 Automatically finding best sync point... (50%)")
            temp_video_audio = f"/tmp/{uuid.uuid4()}_video_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn", 
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Three sync detection methods available:
            # - "waveform": Compare audio waveforms (fast, works well for most content)
            # - "beat": Analyze beat patterns using BPM (better for music videos)
            # - "both": Use both methods and pick the best result (most accurate but slower)
            if sync_method == "waveform":
                edit_progress(token, app_id, message_id, "🔊 Analyzing audio waveforms... (60%)")
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "beat":
                edit_progress(token, app_id, message_id, "🎵 Analyzing beat patterns... (60%)")
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    audio_start_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                else:
                    logger.warning("Could not detect video BPM, falling back to waveform matching")
                    audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "both":
                edit_progress(token, app_id, message_id, "🎯 Using both waveform and beat analysis... (60%)")
                waveform_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
                
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    beat_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                    
                    # Actually choose the better result
                    offset_difference = abs(waveform_offset - beat_offset)
                    if offset_difference < 2.0:  # If they agree within 2 seconds
                        audio_start_offset = waveform_offset
                        method_used = "waveform (consensus)"
                    else:
                        audio_start_offset = waveform_offset 
                        method_used = "waveform (divergent)"
                    logger.info(f"🎯 Waveform: {waveform_offset:.2f}s, Beat: {beat_offset:.2f}s - using {method_used}")
                else:
                    audio_start_offset = waveform_offset
            else:
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)

            logger.info(f"🤖 Auto-detected best audio offset: {audio_start_offset:.2f}s")

            audio_start_offset = min(audio_start_offset, audio_duration - 30)

            # Process media
            edit_progress(token, app_id, message_id, "✂️ Trimming audio to sync point... (80%)")
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress(token, app_id, message_id, "🔀 Combining... (90%)")

            # Add audio offset to response headers for display
            extra_headers = {"X-Audio-Offset": str(audio_start_offset)}

            return send_combined_video_response(
                trimmed_video, 
                trimmed_audio, 
                output_path, 
                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id),
                send_file, 
                logger,
                extra_headers=extra_headers
            )

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("AutoResyncMP3 failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, raw_audio_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[AUTOMP3 ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[AUTOMP3 UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/autoresyncmedia", methods=["POST"])
def autoresyncmedia():
    """Handle auto media resync with intelligent sync detection"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()

        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        sync_method = request.form.get("sync_method", "waveform")

        video_path = audio_path = trimmed_video = trimmed_audio = output_path = sfx_path = trimmed_sfx = None

        try:
            video_url = request.form.get("video_url", default='')
            audio_url = request.form.get("audio_url", default='')
            if not video_url or not audio_url:
                raise ValidationError("Missing URLs", "Missing video_url or audio_url")
            
            # Clean YouTube URLs to remove problematic parameters
            original_video_url = video_url
            video_url = clean_youtube_url(video_url)
            if video_url != original_video_url:
                logger.info(f"🧹 Cleaned YouTube URL: {original_video_url[:50]}... -> {video_url[:50]}...")

            # Basic URL validation
            if not video_url.startswith(("http://", "https://")):
                raise ValidationError("Invalid video URL format", "🔗 Please provide a valid video URL starting with http:// or https://")
            
            if not audio_url.startswith(("http://", "https://")):
                raise ValidationError("Invalid audio URL format", "🎵 Please provide a valid audio URL starting with http:// or https://")
            
            # Check for obviously invalid URLs
            if any(char in video_url for char in [" ", "<", ">", '"']):
                raise ValidationError("Invalid video URL characters", "🔗 Video URL contains invalid characters. Please check your link.")
                
            if any(char in audio_url for char in [" ", "<", ">", '"']):
                raise ValidationError("Invalid audio URL characters", "🎵 Audio URL contains invalid characters. Please check your link.")
            
            # Validate timestamps
            try:
                video_start_seconds = parse_timestamp(request.form.get("video_start", "0"))
                video_end_seconds = parse_timestamp(request.form.get("video_end", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"⏰ Invalid timestamp format: {ve}")
            
            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "🔍 Validating video URL... (20%)")
            
            # DURATION CHECK: Validate video length before downloading
            # This prevents users from submitting 2-hour videos that would timeout
            # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
            if not is_discord_cdn(video_url):
                try:
                    info_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,

                        'age_limit': 99,
                        'geo_bypass': True,
                        'geo_bypass_country': 'US',

                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        },

                        'youtube_include_dash_manifest': False,
                        'extractor_args': {
                            'youtube': {
                                'skip': ['hls', 'dash'],
                            }
                        },
                    }
                    
                    # Get cookies file for age-restricted or login-required videos
                    # Cookies are stored in data/cookies.txt and ignored by git
                    # Different platforms (YouTube, TikTok) may require different cookie files
                    cookiefile = get_cookiefile_for_url(video_url)
                    if cookiefile:
                        info_opts['cookiefile'] = cookiefile
                    
                    logger.info(f"🔍 Getting video info for URL: {video_url[:50]}...")
                    with yt_dlp.YoutubeDL(info_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        raise ProcessingError("No video info found", "🔗 Invalid video URL. Please check your link and try again.")
                    
                    video_duration_total = info.get('duration', 0)
                    if 'instagram.com' not in video_url and video_duration_total:
                        duration_minutes = video_duration_total / 60
                        logger.info(f"Video duration: {duration_minutes:.1f} minutes")
                        
                        if video_duration_total > Config.MAX_VIDEO_DURATION:
                            raise ValidationError(
                                f"Video too long: {duration_minutes:.1f} minutes",
                                f"📏 This video is {duration_minutes:.1f} minutes long. "
                                f"Videos longer than {Config.MAX_VIDEO_DURATION // 60} minutes are not supported. "
                                f"Please use a shorter video or manually download and use `/autoresyncmp4`."
                            )
                    elif 'instagram.com' in video_url:
                        logger.info("Instagram video detected - skipping duration validation")
                    else:
                        # For non-Instagram videos without duration, still error
                        if video_duration_total == 0:
                            raise ProcessingError("Invalid video source", "Invalid video URL...")
                        
                except ValidationError:
                    raise
                except ProcessingError:
                    raise
                except yt_dlp.utils.DownloadError as e:
                    error_str = str(e).lower()
                    logger.info(f"🔍 DEBUG: Instagram check - video_url contains instagram: {'instagram.com' in video_url}")
                    logger.info(f"🔍 DEBUG: Error string: {error_str}")
                    logger.info(f"🔍 DEBUG: Contains rate-limit: {'rate-limit' in error_str}")
                    logger.info(f"🔍 DEBUG: Contains login required: {'login required' in error_str}")
                    if 'instagram.com' in video_url and any(keyword in error_str for keyword in ['rate-limit', 'login required', 'not available']):
                        raise ProcessingError(
                            "Instagram video failed",
                            "📸 Instagram is blocking my download requests right now. "
                            "This is expected with Instagram and happens sometimes, please upload the video using one of the file resync commands instead, or try a YouTube link."
                        )
                    if any(keyword in error_str for keyword in ['not found', '404', 'unavailable', 'private', 'removed']):
                        raise ProcessingError("VIDEO_NOT_FOUND", "🔗 Video not found or unavailable. Please check your video link.")
                    elif any(keyword in error_str for keyword in ['unsupported', 'no suitable formats']):
                        raise ProcessingError("VIDEO_UNSUPPORTED", "🔗 Unsupported video source or format. Please try a different video.")
                    elif any(keyword in error_str for keyword in ['bot', 'authentication', 'cookies']):
                        raise ProcessingError("COOKIES_ERROR", "⚠️ YouTube videos are temporarily unavailable due to authentication issues. Please try using a file upload or a different video source for now. We're working on a fix!")
                    else:
                        raise ProcessingError("VIDEO_ACCESS_FAILED", f"🔗 Could not access video: {str(e)}")
                except Exception as e:
                    logger.warning(f"⚠️ Duration check failed: {e}")
                    raise ProcessingError("VIDEO_VALIDATION_FAILED", "🔗 Invalid video URL or could not validate video. Please check your video link.")
            
            # Set up file paths
            base = f"/tmp/{uuid.uuid4()}_{int(time.time())}_{os.getpid()}"
            video_path = base + "_video.mp4"
            audio_path = base + "_audio.mp3"
            output_path = base + "_resynced.mp4"

            # Sfx handling
            sfx_path = handle_sfx_upload(
                request.files.get("sfx_file"), 
                base, 
                video_start_seconds, 
                video_end_seconds
            )
                    
            # Download video (same logic as resyncmedia)
            edit_progress(token, app_id, message_id, "📥 Downloading video... (30%)")
            
            ydl_opts = {
                'format': 'bestvideo[height<=1080]+bestaudio[height<=1080]/best[height<=1080]',
                'outtmpl': video_path,
                'merge_output_format': 'mp4',
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
                'ffmpeg_location': shutil.which("ffmpeg") or "/usr/bin/ffmpeg",

                # Age / Geo bypass stuff
                'age_limit': 99,              # Bypass age restrictions
                'geo_bypass': True,   
                'geo_bypass_country': 'US',   # Pretend to be from US
                'extractor_args': {
                    'youtube': {
                        'skip': ['hls', 'dash'],  # Sometimes helps with restricted content
                    }
                },
                # Some checks that might trigger restrictions:
                'writesubtitles': False,
                'writeautomaticsub': False,
                'writedescription': False,
                'writeinfojson': False,
            }

            cookiefile = get_cookiefile_for_url(video_url)
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

            logger.info(f"=== COMPARISON DEBUG ===")
            logger.info(f"Endpoint: {'resyncmedia' if 'resyncmedia' in request.endpoint else 'resyncrandommedia'}")
            logger.info(f"Video URL: {video_url}")
            logger.info(f"yt-dlp options: {json.dumps(ydl_opts, indent=2, default=str)}")
            logger.info(f"Current working directory: {os.getcwd()}")
            logger.info(f"Temp file path: {video_path}")
            logger.info(f"Cookie file size: {os.path.getsize(ydl_opts['cookiefile']) if ydl_opts.get('cookiefile') else 'NO COOKIEFILE'}")
            logger.info(f"========================")
            try:
                if is_discord_cdn(video_url):
                    r = requests.get(video_url)
                    r.raise_for_status()
                    with open(video_path, "wb") as f:
                        f.write(r.content)
                    logger.info(f"[✅] Downloaded Discord CDN video to {video_path}")
                elif 'tiktok.com' in video_url:
                    # TikTok-specific handling
                    logger.info(f"🎵 TikTok video detected: {video_url}")
                    success, error_msg = download_tiktok_with_fallbacks(video_url, video_path)
                    
                    if not success:
                        raise ProcessingError("TikTok download failed", format_tiktok_error(error_msg))
                else:
                    try:
                        download_video_with_retry(video_url, ydl_opts)
                    except RuntimeError as e:
                        if "Instagram" in str(e):
                            raise ProcessingError(
                                "Instagram video failed",
                                "📸 Instagram is blocking this download right now. "
                                "Please upload the video using `/resyncmp4` instead."
                            )
                        if "cookies_expired" in str(e):
                            raise ProcessingError(
                                "Cookies expired",
                                "Looks like my cookies have expired! Try using `/resyncmp4` or `/resyncmp3` until I update them."
                            )
                        raise ProcessingError("Video download failed", f"🔗 Failed to download video: {e}")
                    
                    logger.info(f"[✅] Downloaded video to {video_path}")
                    
                    if not is_valid_video_file(video_path, logger):
                        if 'instagram.com' in video_url:
                            raise ProcessingError("Instagram video unreadable", "🔗 This Instagram video couldn't be processed. Some Instagram carousel items can't be downloaded. Try a different video or use the direct video file upload instead.")
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    if not is_valid_video_file(video_path, logger):
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    if os.path.exists(video_path):
                        file_size = os.path.getsize(video_path)
                        if file_size < 1024:
                            raise ProcessingError("Downloaded file too small", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                        
            except requests.exceptions.RequestException as e:
                raise ProcessingError("HTTP download failed", f"🔗 Could not download video: Invalid URL or network error.")
            except RuntimeError as e:
                if str(e) == "cookies_expired":
                    raise ProcessingError(
                        "Cookies expired",
                        "Looks like my cookies have expired! I'll need to refresh them soon. In the meantime, try using /autoresyncmp4!"
                    )
                raise ProcessingError(f"Video download failed: {e}", f"Video download failed: {e}")
            except Exception as e:
                logger.exception("Unexpected error during video download")
                raise ProcessingError(f"Video download error: {e}", format_user_error(e))

            # Download audio
            edit_progress(token, app_id, message_id, "📥 Downloading audio... (40%)")
            cookiefile = None
            if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
                cookiefile = get_cookiefile_for_url(audio_url)

            success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
            if not success:
                if any(keyword in error_msg for keyword in ['bot', 'authentication', 'cookies']):
                    raise ProcessingError("COOKIES_ERROR", "⚠️ Spotify links are temporarily unavailable due to authentication issues. Please try using soundcloud link or an mp3 upload. We're working on a fix!")
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Validate time ranges
            audio_duration = get_duration(audio_path)
            video_duration = get_duration(video_path)

            if video_duration <= 0:
                raise ProcessingError("Invalid video file", "🔗 Invalid video URL or corrupted video file. Please check your link.")

            if video_start_seconds >= video_duration:
                if video_duration < 5:
                    raise ProcessingError("Invalid or very short video", "🔗 Invalid video URL or the downloaded video is too short. Please check your link.")
                else:
                    raise ValidationError(
                        f"Video start exceeds duration",
                        f"📏 Video start time ({video_start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                    )
            
            if video_end_seconds and video_end_seconds > video_duration:
                video_end_seconds = video_duration

            max_video_duration = min(video_duration, video_start_seconds + Config.MAX_DURATION)
            if video_end_seconds == 0 or video_end_seconds > max_video_duration:
                video_end_seconds = max_video_duration

            edit_progress(token, app_id, message_id, "✂️ Trimming video... (50%)")
            trimmed_video = trim_video_ffmpeg(video_path, video_start_seconds, video_end_seconds)

            # INTELLIGENT AUTO-SYNC: Automatically find where audio should start
            # This extracts audio from the video and compares it against the full audio track
            # to find the best matching point (where the audio "lines up")
            edit_progress(token, app_id, message_id, "🤖 Automatically finding best sync point... (60%)")
            temp_video_audio = f"/tmp/{uuid.uuid4()}_video_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn", 
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Three sync detection methods available:
            # - "waveform": Compare audio waveforms (fast, works well for most content)
            # - "beat": Analyze beat patterns using BPM (better for music videos)
            # - "both": Use both methods and pick the best result (most accurate but slower)
            if sync_method == "waveform":
                edit_progress(token, app_id, message_id, "🔊 Analyzing audio waveforms... (70%)")
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "beat":
                edit_progress(token, app_id, message_id, "🎵 Analyzing beat patterns... (70%)")
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    audio_start_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                else:
                    logger.warning("Could not detect video BPM, falling back to waveform matching")
                    audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            elif sync_method == "both":
                edit_progress(token, app_id, message_id, "🎯 Using both waveform and beat analysis... (70%)")
                waveform_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
                
                video_bpm = get_video_bpm(trimmed_video)
                if video_bpm:
                    beat_offset = find_best_beat_match(temp_video_audio, audio_path, video_bpm)
                    
                    # Actually choose the better result
                    offset_difference = abs(waveform_offset - beat_offset)
                    if offset_difference < 2.0:  # If they agree within 2 seconds
                        audio_start_offset = waveform_offset
                        method_used = "waveform (consensus)"
                    else:
                        audio_start_offset = waveform_offset 
                        method_used = "waveform (divergent)"
                    logger.info(f"🎯 Waveform: {waveform_offset:.2f}s, Beat: {beat_offset:.2f}s - using {method_used}")
                else:
                    audio_start_offset = waveform_offset
            else:
                audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)

            logger.info(f"🤖 Auto-detected best audio offset: {audio_start_offset:.2f}s")

            audio_start_offset = min(audio_start_offset, audio_duration - 30)

            # Process media
            edit_progress(token, app_id, message_id, "✂️ Trimming audio to sync point... (80%)")
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress(token, app_id, message_id, "🔀 Combining... (90%)")

            # Add audio offset to response headers for display
            extra_headers = {"X-Audio-Offset": str(audio_start_offset),
                            "X-Video-URL": video_url}


            return send_combined_video_response(
                trimmed_video, 
                trimmed_audio, 
                output_path, 
                lambda v, a, o: combine_with_ffmpeg(v, a, o, sfx_path, user_id=user_id), 
                send_file, 
                logger,
                extra_headers=extra_headers
            )

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("AutoResyncMedia failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
          # CRITICAL: Always cleanup temporary files to prevent disk space issues
          # /tmp/ can fill up quickly with video files if not cleaned properly
          safe_cleanup(video_path, audio_path, trimmed_video, trimmed_audio, output_path, sfx_path)
    except ProcessingError as e:
        logger.warning(f"[AUTOMEDIA ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[AUTOMEDIA UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/loopaudio", methods=["POST"])
def loopaudio():
    """Handle audio looping for editing inspiration"""
    token = request.form.get("token", default='')
    app_id = request.form.get("application_id", default='')
    message_id = request.form.get("message_id", default='')

    audio_path = output_path = raw_audio_path = None

    try:
        # Validate inputs
        audio_file = request.files.get("audio_file")
        start_time = request.form.get("start_time", default='')
        end_time = request.form.get("end_time", default='')
        loop_count = int(request.form.get("loop_count", default='5'))

        if not audio_file:
            raise ValidationError("Missing required files", "Missing audio file")
        
        if not start_time or not end_time:
            raise ValidationError("Missing time parameters", "Missing start_time or end_time")

        # Validate and parse timestamps
        try:
            start_seconds = parse_timestamp(start_time)
            end_seconds = parse_timestamp(end_time)
        except ValueError as ve:
            raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")

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

        # Set up file paths
        audio_path = f"/tmp/{uuid.uuid4()}.mp3"
        output_path = f"/tmp/{uuid.uuid4()}_looped.mp3"
        raw_audio_path = f"/tmp/{uuid.uuid4()}_raw"

        # Update Discord message to show progress (prevents "bot not responding" errors)
        # This sends progress updates back to the Discord bot which edits the user's message
        edit_progress(token, app_id, message_id, "📥 Processing audio file... (20%)")

        # Save uploaded audio
        audio_bytes = audio_file.read()
        with open(raw_audio_path, "wb") as f:
            f.write(audio_bytes)

        # Check if audio file is actually a video and extract audio if needed
        is_video = is_valid_video_file(raw_audio_path, logger)
        
        if is_video:
            logger.info("🎥 Detected video file as audio source. Extracting audio...")
            if not extract_audio_from_video(raw_audio_path, audio_path):
                raise ProcessingError("Audio extraction failed", "❌ Failed to extract audio from the provided file.")
        else:
            # Copy raw audio to final path
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)

        # Get audio duration for validation
        audio_duration = get_duration(audio_path)
        
        if audio_duration <= 0:
            raise ProcessingError("Invalid audio file", "❌ Invalid or corrupted audio file.")

        # Validate time ranges against actual audio duration
        if start_seconds >= audio_duration:
            raise ValidationError(
                f"Start time exceeds duration",
                f"📏 Start time ({start_seconds:.1f}s) exceeds audio length ({audio_duration:.1f}s)"
            )
        
        if end_seconds > audio_duration:
            end_seconds = audio_duration
            logger.info(f"Adjusted end time to audio duration: {end_seconds:.1f}s")

        edit_progress(token, app_id, message_id, f"🔄 Creating {loop_count}x loop... (60%)")

        # Create the looped audio using FFmpeg
        loop_audio_ffmpeg(audio_path, start_seconds, end_seconds, loop_count, output_path)

        edit_progress(token, app_id, message_id, "📤 Finalizing looped audio... (90%)")
        
        # Send the looped audio file
        logger.info(f"[📤] Sending looped audio: {output_path}")
        return send_file(output_path, mimetype="audio/mpeg", as_attachment=True, download_name="looped_audio.mp3"), 200, {
            "X-Temp-Path": output_path,
            "X-Loop-Count": str(loop_count),
            "X-Segment-Duration": str(end_seconds - start_seconds)
        }

    except (ValidationError, ProcessingError):
        raise
    except Exception as e:
        logger.exception("LoopAudio failed")
        raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
    finally:
        # CRITICAL: Always cleanup temporary files to prevent disk space issues
        # /tmp/ can fill up quickly with video files if not cleaned properly
        safe_cleanup(audio_path, output_path, raw_audio_path)

@app.route("/downloadaudio", methods=["POST"])
def downloadaudio():
    """Handle audio download requests"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()

        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        
        audio_path = trimmed_audio = None
        audio_title = "downloaded_audio"

        try:
            # Validate inputs
            audio_url = request.form.get("audio_url", default='')
            if not audio_url:
                raise ValidationError("Missing audio URL", "Missing audio_url")

            # Validate timestamps
            try:
                start_seconds = parse_timestamp(request.form.get("start_time", "0"))
                end_seconds = parse_timestamp(request.form.get("end_time", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")
            
            if not audio_url.endswith('.mp3') and not is_discord_cdn(audio_url):
                try:
                    info_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,
                        'age_limit': 99,
                        'geo_bypass': True,
                        'geo_bypass_country': 'US',
                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept-Language': 'en-US,en;q=0.9',
                        },
                        'youtube_include_dash_manifest': False,
                        'writesubtitles': False,
                        'writeautomaticsub': False,
                        'writedescription': False,
                        'writeinfojson': False,
                    }
                    
                    # Get cookies file for age-restricted or login-required videos
                    # Cookies are stored in data/cookies.txt and ignored by git
                    # Different platforms (YouTube, TikTok) may require different cookie files
                    cookiefile = None                    
                    if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
                        cookiefile = get_cookiefile_for_url(audio_url)
                        if cookiefile:
                            info_opts['cookiefile'] = cookiefile
                    
                    logger.info(f"🔍 Getting audio info for URL: {audio_url[:50]}...")
                    with yt_dlp.YoutubeDL(info_opts) as ydl:
                        info = ydl.extract_info(audio_url, download=False)
                    
                    if info:
                        # Extract title here - ALWAYS, regardless of duration check
                        audio_title = info.get('title', 'downloaded_audio')
                        audio_title = sanitize_filename(audio_title)
                        logger.info(f"🎵 Audio title: {audio_title}")
                        
                        # THEN do duration check
                        audio_duration_total = info.get('duration', 0)
                        if audio_duration_total:
                            duration_minutes = audio_duration_total / 60
                            logger.info(f"🎵 Audio duration: {duration_minutes:.1f} minutes")
                            
                            # Block very long audio files (20 minutes)
                            max_audio_duration = 1200  # 20 minutes in seconds
                            if audio_duration_total > max_audio_duration:
                                raise ValidationError(
                                    f"Audio too long for download: {duration_minutes:.1f} minutes",
                                    f"🎵 This audio is {duration_minutes:.1f} minutes long. "
                                    f"Audio downloads are limited to 20 minutes to prevent server overload. "
                                    f"Please use a shorter audio file or trim it first."
                                )
                            
                            logger.info(f"✅ Audio duration OK for download: {duration_minutes:.1f} minutes")
                        else:
                            logger.info("🤷‍♂️ No duration found, continuing anyway")
                            
                except ValidationError:
                    raise  # Re-raise duration errors
                except Exception as e:
                    logger.warning(f"⚠️ Could not get audio info: {e}")
                    logger.info("🤷‍♂️ Continuing with generic filename")
            else:
                logger.info("⏭️ Using generic filename for direct MP3/Discord CDN")

            # Set up file paths
            audio_path = f"/tmp/{uuid.uuid4()}.mp3"
            
            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "🎵 Downloading audio... (50%)")

            # Download audio with high quality settings
            cookiefile = None
            if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
                cookiefile = get_cookiefile_for_url(audio_url)

            success, error_msg = download_audio_high_quality(audio_url, audio_path, logger, cookiefile)
            if not success:
                raise ProcessingError(f"Audio download failed: {error_msg}", format_resync_error(error_msg))

            # Trim audio if requested
            if start_seconds > 0 or end_seconds > 0:
                edit_progress(token, app_id, message_id, "✂️ Trimming audio... (80%)")
                
                audio_duration = get_duration(audio_path)
                if end_seconds == 0:
                    end_seconds = audio_duration
                
                # Validate time ranges
                if start_seconds >= audio_duration:
                    raise ValidationError(
                        f"Start time exceeds duration",
                        f"📏 Start time ({start_seconds:.1f}s) exceeds audio length ({audio_duration:.1f}s)"
                    )
                
                if end_seconds > audio_duration:
                    end_seconds = audio_duration
                    
                trimmed_audio = trim_audio_ffmpeg(audio_path, start_seconds, end_seconds - start_seconds)
                audio_path = trimmed_audio

            edit_progress(token, app_id, message_id, "📤 Finalizing... (95%)")
            
            # Send the audio file
            filename = f"{audio_title}.mp3"
            logger.info(f"[📤] Sending downloaded audio: {audio_path}")
            return send_file(audio_path, mimetype="audio/mpeg", as_attachment=True, download_name=filename), 200, {
                "X-Temp-Path": audio_path,
                "X-Filename": filename
            }

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("DownloadAudio failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(audio_path, trimmed_audio)
    except ProcessingError as e:
        logger.warning(f"[DOWNLOADAUDIO ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[DOWNLOADAUDIO UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
@app.route("/downloadvideo", methods=["POST"])
def downloadvideo():
    """Handle video download requests"""
    try:
        if request.method == "OPTIONS":
            return ("", 200)
        
        require_api_secret()

        token = request.form.get("token", default='')
        app_id = request.form.get("application_id", default='')
        message_id = request.form.get("message_id", default='')
        user_id = request.form.get("user_id", default='')
        
        video_path = trimmed_video = None

        try:
            # Validate inputs
            video_url = request.form.get("video_url", default='')
            if not video_url:
                raise ValidationError("Missing video URL", "Missing video_url")

            quality = request.form.get("quality", "best")

            # Validate timestamps
            try:
                start_seconds = parse_timestamp(request.form.get("start_time", "0"))
                end_seconds = parse_timestamp(request.form.get("end_time", "0"))
            except ValueError as ve:
                raise ValidationError(f"Invalid timestamp: {ve}", f"Invalid timestamp format: {ve}")

            # Set up file paths - use a base name without extension
            base_path = f"/tmp/{uuid.uuid4()}"
            video_path = base_path + ".mp4"  # This is our target path
            
            video_title = "downloaded_video"

            # DURATION CHECK: Validate video length before downloading
            # This prevents users from submitting 2-hour videos that would timeout
            # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
            if not is_discord_cdn(video_url):
                try:
                    # Get video info without downloading (same logic as resyncmedia)
                    info_opts = {
                        'quiet': True,
                        'no_warnings': True,
                        'extract_flat': False,
                        'skip_download': True,
                        'noplaylist': True,

                        'age_limit': 99,
                        'geo_bypass': True,
                        'geo_bypass_country': 'US',

                        'http_headers': {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Accept-Encoding': 'gzip, deflate',
                            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                            'Connection': 'keep-alive',
                            'Upgrade-Insecure-Requests': '1',
                        },

                        'youtube_include_dash_manifest': False,
                        'extractor_args': {
                            'youtube': {
                                'skip': ['hls', 'dash'],
                            }
                        },

                        'writesubtitles': False,
                        'writeautomaticsub': False,
                        'writedescription': False,
                        'writeinfojson': False,
                    }
                    
                    # Get cookies file for age-restricted or login-required videos
                    # Cookies are stored in data/cookies.txt and ignored by git
                    # Different platforms (YouTube, TikTok) may require different cookie files
                    cookiefile = get_cookiefile_for_url(video_url)
                    if cookiefile:
                        info_opts['cookiefile'] = cookiefile
                    
                    logger.info(f"🔍 Getting video info for URL: {video_url[:50]}...")
                    with yt_dlp.YoutubeDL(info_opts) as ydl:
                        info = ydl.extract_info(video_url, download=False)
                    
                    if not info:
                        raise ProcessingError("No video info found", "🎥 Invalid video URL. Please check your link and try again.")
                    
                    video_title = info.get('title', 'downloaded_video')
                    video_title = sanitize_filename(video_title)[:50]
                    logger.info(f"📹 Video title: {video_title}")

                    video_duration_total = info.get('duration', 0)
                    
                    if 'instagram.com' not in video_url and video_duration_total:
                        duration_minutes = video_duration_total / 60
                        logger.info(f"Video duration: {duration_minutes:.1f} minutes")
                        
                        if video_duration_total > Config.MAX_DOWNLOAD_VIDEO_DURATION:
                            raise ValidationError(
                                f"Video too long: {duration_minutes:.1f} minutes",
                                f"📏 This video is {duration_minutes:.1f} minutes long. "
                                f"Videos longer than {Config.MAX_DOWNLOAD_VIDEO_DURATION // 60} minutes are not supported. "
                                f"Please use a shorter video or manually download and use `/autoresyncmp4`."
                            )
                    elif 'instagram.com' in video_url:
                        logger.info("Instagram video detected - skipping duration validation")
                    else:
                        # For non-Instagram videos without duration, still error
                        if video_duration_total == 0:
                            raise ProcessingError("Invalid video source", "Invalid video URL...")
                        
                except ValidationError:
                    # Re-raise validation errors to stop the function
                    raise
                except ProcessingError:
                    # Re-raise processing errors to stop the function
                    raise
                except yt_dlp.utils.DownloadError as e:
                    error_str = str(e).lower()
                    logger.info(f"🔍 DEBUG: Instagram check - video_url contains instagram: {'instagram.com' in video_url}")
                    logger.info(f"🔍 DEBUG: Error string: {error_str}")
                    logger.info(f"🔍 DEBUG: Contains rate-limit: {'rate-limit' in error_str}")
                    logger.info(f"🔍 DEBUG: Contains login required: {'login required' in error_str}")
                    if 'instagram.com' in video_url and any(keyword in error_str for keyword in ['rate-limit', 'login required', 'not available']):
                        raise ProcessingError(
                            "Instagram video failed",
                            "📸 Instagram is blocking my download requests right now. "
                            "This is expected with Instagram and happens sometimes, please upload the video using one of the file resync commands instead, or try a YouTube link."
                        )
                    if any(keyword in error_str for keyword in ['not found', '404', 'unavailable', 'private', 'removed']):
                        raise ProcessingError("VIDEO_NOT_FOUND", "🔗 Video not found or unavailable. Please check your video link.")
                    elif any(keyword in error_str for keyword in ['unsupported', 'no suitable formats']):
                        raise ProcessingError("VIDEO_UNSUPPORTED", "🔗 Unsupported video source or format. Please try a different video.")
                    elif any(keyword in error_str for keyword in ['bot', 'authentication', 'cookies']):
                        raise ProcessingError("COOKIES_ERROR", "⚠️ YouTube videos are temporarily unavailable due to authentication issues. Please try using a file upload or a different video source for now. We're working on a fix!")
                    else:
                        raise ProcessingError("VIDEO_ACCESS_FAILED", f"🔗 Could not access video: {str(e)}")
                except Exception as e:
                    logger.warning(f"⚠️ Duration check failed: {e}")
                    raise ProcessingError("VIDEO_VALIDATION_FAILED", "🎥 Invalid video URL or could not validate video. Please check your video link.")

            # Update Discord message to show progress (prevents "bot not responding" errors)
            # This sends progress updates back to the Discord bot which edits the user's message
            edit_progress(token, app_id, message_id, "🎥 Downloading video... (30%)")

            # Configure high-quality download options
            if quality == "best":
                format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            elif quality == "1080p":
                format_selector = "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best[height<=1080]"
            elif quality == "720p":
                format_selector = "bestvideo[ext=mp4][height<=720]+bestaudio[ext=m4a]/best[ext=mp4][height<=720]/best[height<=720]"
            elif quality == "480p":
                format_selector = "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]/best[ext=mp4][height<=480]/best[height<=480]"
            else:
                format_selector = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"


            ydl_opts = {
                'format': format_selector,
                'outtmpl': base_path + ".%(ext)s",  # Let yt-dlp add the extension
                'merge_output_format': 'mp4',  # Force merge to MP4 when possible
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'writedescription': False,
                'writeinfojson': False,
                'postprocessors': [{
                    'key': 'FFmpegVideoConvertor',
                    'preferedformat': 'mp4',
                }],
                'ffmpeg_location': shutil.which("ffmpeg") or "/usr/bin/ffmpeg",
            }

            cookiefile = get_cookiefile_for_url(video_url)
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

            # Download video
            try:
                if is_discord_cdn(video_url):
                    r = requests.get(video_url)
                    r.raise_for_status()
                    with open(video_path, "wb") as f:
                        f.write(r.content)
                    logger.info(f"[✅] Downloaded Discord CDN video to {video_path}")
                elif 'tiktok.com' in video_url:
                    success, error_msg = download_tiktok_with_fallbacks(video_url, video_path)
                    if not success:
                        raise ProcessingError("TikTok download failed", format_tiktok_error(error_msg))
                else:
                    try:
                        download_video_with_retry(video_url, ydl_opts)
                    except RuntimeError as e:
                        if "Instagram" in str(e):
                            raise ProcessingError(
                                "Instagram video failed",
                                "📸 Instagram is blocking this download right now. "
                                "Please upload the video using `/resyncmp4` instead."
                            )
                        if "cookies_expired" in str(e):
                            raise ProcessingError(
                                "Cookies expired",
                                "Looks like my cookies have expired! Try using `/resyncmp4` or `/resyncmp3` until I update them."
                            )
                        raise ProcessingError("Video download failed", f"🔗 Failed to download video: {e}")
                    
                    if not is_valid_video_file(video_path, logger):
                        if 'instagram.com' in video_url:
                            raise ProcessingError("Instagram video unreadable", "🔗 This Instagram video couldn't be processed. Some Instagram carousel items can't be downloaded. Try a different video or use the direct video file upload instead.")
                        raise ProcessingError("Invalid video downloaded", "🔗 Invalid video URL or the video could not be downloaded. Please check your link.")
                    
                    # Find the actual downloaded file (yt-dlp might have added a different extension
                    actual_video_path = find_downloaded_file(base_path)
                    if not actual_video_path:
                        raise ProcessingError("Download failed", "🎥 Video download failed - no file was created.")
                    video_path = actual_video_path

                    # If the actual path is not a .mp4 file, convert it to .mp4
                    if not video_path.endswith(".mp4"):
                        mp4_path = base_path + ".mp4"
                        try:
                            result = subprocess.run([
                                "ffmpeg", "-y", "-i", video_path, "-c:v", "copy", "-c:a", "copy", mp4_path
                            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                            if result.returncode != 0:
                                raise ProcessingError("Video conversion failed", "🎥 Could not convert video to MP4 format.")
                            
                            video_path = mp4_path
                            logger.info(f"[✅] Converted video to MP4: {video_path}")
                        except Exception as e:
                            raise ProcessingError("Video conversion error", f"🎥 Video downloaded but couldn't be converted to MP4: {e}")
                    
                    logger.info(f"[✅] Downloaded video to {video_path}")
                    
                    if not is_valid_video_file(video_path, logger):
                        raise ProcessingError("Invalid video downloaded", "🎥 Invalid video URL or the video could not be downloaded.")

            except Exception as e:
                logger.exception("Video download error")
                raise ProcessingError(f"Video download error: {e}", f"🎥 Could not download video: {format_user_error(e)}")

            video_duration = get_duration(video_path)
            max_duration = Config.MAX_DOWNLOAD_VIDEO_DURATION

            if video_duration > max_duration:
                logger.info(f"📏 Video too long ({video_duration/60:.1f} min), trimming to 10 minutes")
                edit_progress(token, app_id, message_id, "✂️ Video longer than 10 min, trimming... (60%)")
                
                # Force trim to 10 minutes from start
                trimmed_video = trim_video_high_quality(video_path, 0, max_duration)
                video_path = trimmed_video
                
                logger.info(f"✅ Trimmed video to 10 minutes")
            
            # Trim video if requested
            if start_seconds > 0 or end_seconds > 0:
                edit_progress(token, app_id, message_id, "✂️ Trimming video... (70%)")
                
                video_duration = get_duration(video_path)
                if end_seconds == 0:
                    end_seconds = video_duration
                
                # Validate time ranges
                if start_seconds >= video_duration:
                    raise ValidationError(
                        f"Start time exceeds duration",
                        f"📏 Start time ({start_seconds:.1f}s) exceeds video length ({video_duration:.1f}s)"
                    )
                
                if end_seconds > video_duration:
                    end_seconds = video_duration

                # Use high-quality trimming (no compression)
                trimmed_video = trim_video_high_quality(video_path, start_seconds, end_seconds)
                video_path = trimmed_video

            edit_progress(token, app_id, message_id, "📤 Finalizing... (95%)")
            
            filename = f"{video_title}.mp4"
            # Send the video file
            logger.info(f"[📤] Sending downloaded video: {video_path}")
            return send_file(video_path, mimetype="video/mp4", as_attachment=True, download_name=filename), 200, {
                "X-Temp-Path": video_path,
                "X-Filename": filename
            }

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            logger.exception("DownloadVideo failed")
            raise ProcessingError(f"Unexpected processing error: {e}", format_user_error(e))
        finally:
            # CRITICAL: Always cleanup temporary files to prevent disk space issues
            # /tmp/ can fill up quickly with video files if not cleaned properly
            safe_cleanup(video_path, trimmed_video)
    except ProcessingError as e:
        logger.warning(f"[DOWNLOADVID ERR] {e}")
        return jsonify({"error": e.user_message}), 400
    except Exception as e:
        logger.exception("[DOWNLOADVID UNHANDLED ERROR]")
        return jsonify({"error": f"VIDEO_Internal server error: {e.user_message}"}), 500
    
"""
-----------------------------------------------------------------------------------------
                                    IMPORTANT
All this code below is **NOT** relevant to the bot itself, it's simply the backend for the
website, as well as any other webhooks used such as top.gg and stripe. Don't modify.
-----------------------------------------------------------------------------------------
"""
@app.route('/topgg/webhook', methods=['POST'])
def topgg_webhook():
    """Handle Top.gg voting webhooks"""
    try:
        # Verify the webhook (Top.gg sends a secret header)
        auth_header = request.headers.get('Authorization', '')
        
        if Config.TOPGG_WEBHOOK_SECRET and auth_header != Config.TOPGG_WEBHOOK_SECRET:
            logger.warning("Invalid Top.gg webhook authorization")
            return jsonify({'error': 'Unauthorized'}), 401
        
        # Add this logging at the very start
        logger.info(f"[TOPGG] Webhook called - Headers: {dict(request.headers)}")
        logger.info(f"[TOPGG] Request data: {request.get_data()}")
        
        # Verify the webhook (Top.gg sends a secret header)
        auth_header = request.headers.get('Authorization', '')
        logger.info(f"[TOPGG] Auth header: {auth_header}")
        
        if Config.TOPGG_WEBHOOK_SECRET and auth_header != Config.TOPGG_WEBHOOK_SECRET:
            logger.warning("Invalid Top.gg webhook authorization")
            return jsonify({'error': 'Unauthorized'}), 401
        
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        user_id = data.get('user')
        vote_type = data.get('type', 'upvote')  # Top.gg sends 'upvote' or 'test'
        
        if not user_id:
            return jsonify({'error': 'No user ID provided'}), 400
        
        try:
            user_id = int(user_id)
        except ValueError:
            return jsonify({'error': 'Invalid user ID'}), 400
        
        logger.info(f"Received Top.gg vote from user {user_id}, type: {vote_type}")
        
        # Record the vote and potentially reset limits
        if vote_type == 'upvote':  # Only process actual votes, not tests
            limits_reset = voting_manager.record_vote(user_id)
            
            if limits_reset:
                logger.info(f"Reset daily limits for user {user_id} via Top.gg vote")
                return jsonify({'status': 'success', 'limits_reset': True}), 200
            else:
                logger.info(f"User {user_id} voted but already reset limits today")
                return jsonify({'status': 'success', 'limits_reset': False}), 200
        else:
            # Test webhook or other type
            return jsonify({'status': 'success', 'message': 'Test webhook received'}), 200
            
    except Exception as e:
        logger.error(f"Error processing Top.gg webhook: {e}")
        return jsonify({'error': 'Internal server error'}), 500

@app.route('/topgg/health', methods=['GET'])
def topgg_health():
    """Top.gg webhook health check"""
    return jsonify({'status': 'voting system healthy'}), 200

@app.route('/stripe/webhook', methods=['POST'])
def stripe_webhook():
    """Stripe webhook endpoint"""
    
    try:
        payload = request.get_data()
        signature = request.headers.get('Stripe-Signature')

        result = stripe_handler.handle_webhook(payload, signature)
        
        
        if result:
            return jsonify({'status': 'success'}), 200
        else:
            return jsonify({'status': 'error'}), 400
            
    except Exception as e:
        import traceback
        return jsonify({'status': 'error'}), 500

@app.route('/stripe/health', methods=['GET'])
def stripe_health():
    """Stripe health check"""
    return jsonify({'status': 'stripe integration healthy'}), 200

@app.route("/demo/random-resync", methods=["POST"])
def demo_random_resync():
    """Handle demo random resync with hardcoded video"""
    try:
        data = request.get_json()
        session_id = data.get("session_id", None)
        edit_progress_web("Downloading Video.. (25%)", session_id=session_id)
        DEMO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "demo_assets", "demo_edit_purple.mp4")
        
        if not os.path.exists(DEMO_VIDEO_PATH):
            return jsonify({"error": "Demo video not found"}), 500
        
        base = f"/tmp/demo_{uuid.uuid4()}_{int(time.time())}"
        audio_path = f"{base}_audio.mp3"
        trimmed_video = f"{base}_video_trimmed.mp4"
        trimmed_audio = f"{base}_audio_trimmed.mp3"
        output_path = f"{base}_resynced.mp4"
        
        try:
            shutil.copy2(DEMO_VIDEO_PATH, trimmed_video)
            
            video_bpm = get_video_bpm(trimmed_video)
            if not video_bpm:
                return jsonify({"error": "Could not analyze demo video audio"}), 500
            
            logger.info(f"Demo video BPM: {video_bpm}")
            
            # Find matching track in database
            selected_track = find_matching_tracks(video_bpm, tolerance=5)
            if not selected_track:
                return jsonify({"error": f"No audio tracks found matching BPM {video_bpm}"}), 500
            
            logger.info(f"Selected demo track: {selected_track['song']} by {selected_track['uploader']}")

            edit_progress_web("Fetching a random matching audio... (40%)", session_id=session_id)

            # Download the selected audio track
            success, error_msg = download_audio_from_database(
                selected_track["song"],
                selected_track["uploader"], 
                selected_track["platform"],
                selected_track["song_id"],
                audio_path
            )
            
            if not success:
                return jsonify({"error": f"Audio download failed: {error_msg}"}), 500
            
            # Get audio duration
            audio_duration = get_duration(audio_path)
            
            # Extract audio from demo video for sync analysis
            temp_video_audio = f"{base}_demo_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn",
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Find best sync point
            edit_progress_web("Finding the best resync... (70%)", session_id=session_id)
            audio_start_offset = find_best_audio_match(temp_video_audio, audio_path)
            logger.info(f"Demo auto-detected audio offset: {audio_start_offset:.2f}s")
            
            audio_start_offset = min(audio_start_offset, audio_duration - 30)
            
            # Trim audio based on calculated offset
            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress_web("Processing and sending resync... (80%)", session_id=session_id)
            # Combine video and audio
            combine_with_ffmpeg(trimmed_video, trimmed_audio, output_path, user_id="demo")
            
            # Return the video file
            track_info = {
                "song": selected_track['song'],
                "artist": selected_track['uploader'],
                "url": selected_track['url'],
                "platform": selected_track['platform'],
                "audio_offset": audio_start_offset
            }
            
            return send_file(output_path, mimetype="video/mp4", as_attachment=True), 200, {
                "X-Track-Info": json.dumps(track_info),
                "X-Audio-Offset": str(audio_start_offset)
            }
            
        except Exception as e:
            logger.exception("Demo random resync failed")
            return jsonify({"error": format_user_error(e)}), 500
            
        finally:
            safe_cleanup(audio_path, trimmed_video, trimmed_audio, output_path)
            
    except Exception as e:
        logger.exception("Demo random resync endpoint failed")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/demo/custom-resync", methods=["POST"])
def demo_custom_resync():
    """Handle demo custom resync with hardcoded video and user audio URL"""
    try:
        data = request.get_json()
        session_id = data.get("session_id", None)
        
        edit_progress_web("Downloading Video.. (25%)", session_id=session_id)
        if not data or 'audio_url' not in data:
            return jsonify({"error": "Missing audio_url in request"}), 400

        audio_url = data['audio_url'].strip()
        if not audio_url.startswith(("http://", "https://")):
            return jsonify({"error": "Invalid audio URL format"}), 400
        
        DEMO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "demo_assets", "demo_edit_green.mp4")
        if not os.path.exists(DEMO_VIDEO_PATH):
            return jsonify({"error": "Demo video not found"}), 500

        # DURATION CHECK: Validate video length before downloading
        # This prevents users from submitting 2-hour videos that would timeout
        # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
        if not is_discord_cdn(audio_url) and not audio_url.endswith(".mp3"):
            try:
                info_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": False,
                    "skip_download": True,
                    "noplaylist": True,
                    "age_limit": 99,
                    "geo_bypass": True,
                    "geo_bypass_country": "US",
                }
                # Get cookies file for age-restricted or login-required videos
                # Cookies are stored in data/cookies.txt and ignored by git
                # Different platforms (YouTube, TikTok) may require different cookie files
                cookiefile = get_cookiefile_for_url(audio_url)
                if cookiefile:
                    info_opts["cookiefile"] = cookiefile

                with yt_dlp.YoutubeDL(info_opts) as ydl:
                    info = ydl.extract_info(audio_url, download=False)
                    dur = info.get("duration", 0)
                    if dur > 7 * 60:
                        return jsonify({
                            "error": f"Audio too long: {dur / 60:.1f} minutes. Max allowed is 7 minutes."
                        }), 400
            except Exception as e:
                logger.warning(f"Could not pre-check audio duration via yt_dlp: {e}")

        base = f"/tmp/demo_{uuid.uuid4()}_{int(time.time())}"
        audio_path = f"{base}_audio.mp3"
        trimmed_video = f"{base}_video_trimmed.mp4"
        trimmed_audio = f"{base}_audio_trimmed.mp3"
        output_path = f"{base}_resynced.mp4"

        try:
            shutil.copy2(DEMO_VIDEO_PATH, trimmed_video)

            cookiefile = get_cookiefile_for_url(audio_url) if 'youtube.com' in audio_url or 'youtu.be' in audio_url else None
            success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
            if not success:
                return jsonify({"error": f"Audio download failed: {format_resync_error(error_msg)}"}), 500
            
            temp_video_audio = f"{base}_demo_audio.wav"
            extract_cmd = [
                "ffmpeg", "-y", "-i", trimmed_video, "-vn",
                "-acodec", "pcm_s16le", "-ar", "22050", "-ac", "1",
                temp_video_audio
            ]
            subprocess.run(extract_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Find best offset
            edit_progress_web("Finding the best sync..(50%)", session_id=session_id)
            audio_start_offset = find_best_audio_match(temp_video_audio, audio_path, max_search_duration=120)
            logger.info(f"Demo custom resync audio offset: {audio_start_offset:.2f}s")

            audio_duration = get_duration(audio_path)
            audio_start_offset = min(audio_start_offset, max(audio_duration - 30, 0))

            trimmed_audio = trim_audio_ffmpeg(audio_path, audio_start_offset)

            edit_progress_web("Processing and sending resync... (80%)", session_id=session_id)
            combine_with_ffmpeg(trimmed_video, trimmed_audio, output_path, user_id="demo")

            edit_progress_web("Resync complete! Downloading... (100%)", session_id=session_id)
            return send_file(output_path, mimetype="video/mp4", as_attachment=True), 200, {
                "X-Audio-URL": audio_url,
                "X-Audio-Offset": str(audio_start_offset)
            }

        except Exception as e:
            logger.exception("Demo custom resync failed")
            return jsonify({"error": format_user_error(e)}), 500

        finally:
            safe_cleanup(audio_path, trimmed_video, trimmed_audio, output_path)

    except Exception as e:
        logger.exception("Demo custom resync endpoint failed")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/demo/video_green", methods=["GET"])
def get_demo_video_green():
    try:
        DEMO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "demo_assets", "demo_edit_green.mp4")
        
        if not os.path.exists(DEMO_VIDEO_PATH):
            return jsonify({"error": "Demo video not found"}), 404
        
        # Add headers to discourage downloading
        response = send_file(
            DEMO_VIDEO_PATH, 
            mimetype="video/mp4",
            as_attachment=False
        )
        
        # Add cache control and security headers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        return response
        
    except Exception as e:
        logger.error(f"Error serving demo video: {e}")
        return jsonify({"error": "Could not load demo video"}), 500

@app.route("/demo/video_purple", methods=["GET"])
def get_demo_video_purple():
    try:
        DEMO_VIDEO_PATH = os.path.join(os.path.dirname(__file__), "demo_assets", "demo_edit_purple.mp4")
        
        if not os.path.exists(DEMO_VIDEO_PATH):
            return jsonify({"error": "Demo video not found"}), 404
        
        # Add headers to discourage downloading
        response = send_file(
            DEMO_VIDEO_PATH, 
            mimetype="video/mp4",
            as_attachment=False
        )
        
        # Add cache control and security headers
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        
        return response
        
    except Exception as e:
        logger.error(f"Error serving demo video: {e}")
        return jsonify({"error": "Could not load demo video"}), 500
    
@app.route("/demo/analyze-bpm", methods=["POST"])
def demo_analyze_bpm():
    """Analyze BPM of user-provided audio URL"""
    audio_path = None
    
    try:
        data = request.get_json()
        
        if not data or 'audio_url' not in data:
            return jsonify({"error": "Missing audio_url in request"}), 400
        
        audio_url = data['audio_url']
        
        # Basic URL validation
        if not audio_url.startswith(("http://", "https://")):
            return jsonify({"error": "Invalid audio URL format"}), 400
        
        # Add duration check before download (same logic as your other endpoints)
        if not audio_url.endswith('.mp3') and not is_discord_cdn(audio_url):
            try:
                info_opts = {
                    'quiet': True,
                    'no_warnings': True,
                    'extract_flat': False,
                    'skip_download': True,
                    'noplaylist': True,
                    'age_limit': 99,
                    'geo_bypass': True,
                    'geo_bypass_country': 'US',
                }
                
                cookiefile = get_cookiefile_for_url(audio_url)
                if cookiefile:
                    info_opts['cookiefile'] = cookiefile
                
                with yt_dlp.YoutubeDL(info_opts) as ydl:
                    info = ydl.extract_info(audio_url, download=False)
                
                if info:
                    audio_duration_total = info.get('duration', 0)
                    if audio_duration_total:
                        duration_minutes = audio_duration_total / 60
                        MAX_DURATION = 7 * 60
                        if audio_duration_total > MAX_DURATION:
                            return jsonify({
                                "error": f"Audio too long for bpm analysis: {duration_minutes:.1f} minutes. Please use audio shorter than 5 minutes."
                            }), 400
                        
            except Exception as e:
                logger.warning(f"Could not check audio duration: {e}")
        
        # Set up temporary file path
        audio_path = f"/tmp/bpm_analysis_{uuid.uuid4()}.mp3"
        
        # Download audio
        cookiefile = None
        if 'youtube.com' in audio_url or 'youtu.be' in audio_url:
            cookiefile = get_cookiefile_for_url(audio_url)
        
        success, error_msg = download_audio(audio_url, audio_path, logger, cookiefile)
        if not success:
            return jsonify({"error": f"Audio download failed: {format_resync_error(error_msg)}"}), 500
        
        # Additional duration check after download
        audio_duration = get_duration(audio_path)
        if audio_duration > 300:  # 5 minutes
            return jsonify({
                "error": f"Audio too long for bpm analysis: {audio_duration/60:.1f} minutes. Please use audio shorter than 5 minutes."
            }), 400
        
        # Analyze BPM using your existing function
        detected_bpm = get_video_bpm(audio_path)
        
        if not detected_bpm:
            return jsonify({"error": "Could not detect BPM from this audio"}), 500
        
        logger.info(f"Demo BPM analysis: {detected_bpm} BPM for {audio_url}")
        
        # Return the BPM value
        return jsonify({
            "bpm": detected_bpm,
            "audio_url": audio_url
        }), 200
        
    except Exception as e:
        logger.exception("Demo BPM analysis failed")
        return jsonify({"error": format_user_error(e)}), 500
        
    finally:
        safe_cleanup(audio_path)

@app.route("/demo/preview-media", methods=["POST", "OPTIONS"])
def preview_media():
    try:
        if request.method == "OPTIONS":
            return ("", 200)

        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()

        if not url or not url.startswith(("http://", "https://")):
            return jsonify({"error": "Provide a valid media URL"}), 400

        # Determine media type based on URL
        lower_url = url.lower()
        is_audio = any(x in lower_url for x in ("soundcloud.com", "spotify.com", "audio", ".mp3", ".m4a", ".ogg"))
        media_kind = "audio" if is_audio else "video"

        def add_headers(resp, filename):
            resp.headers['Content-Disposition'] = f'inline; filename="{filename}"'
            resp.headers['Cache-Control'] = 'no-store'
            resp.headers['X-Content-Type-Options'] = 'nosniff'
            resp.headers['Accept-Ranges'] = 'none'
            return resp

        # DURATION CHECK: Validate video length before downloading
        # This prevents users from submitting 2-hour videos that would timeout
        # Max duration is configured in Config.MAX_VIDEO_DURATION (usually 10 minutes)
        if not is_discord_cdn(url):
            try:
                info_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "extract_flat": False,
                    "skip_download": True,
                    "noplaylist": True,
                    "age_limit": 99,
                    "geo_bypass": True,
                    "geo_bypass_country": "US",
                }
                cookiefile = get_cookiefile_for_url(url)
                if cookiefile:
                    info_opts["cookiefile"] = cookiefile

                with yt_dlp.YoutubeDL(info_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        dur = info.get("duration", 0)
                        if media_kind == "audio" and dur > 20 * 60:
                            return jsonify({
                                "error": f"Audio too long: {dur/60:.1f} minutes. Max 20 minutes."
                            }), 400
                        elif media_kind == "video" and dur > 10 * 60:
                            return jsonify({
                                "error": f"Video too long: {dur/60:.1f} minutes. Max 10 minutes."
                            }), 400
            except Exception as e:
                logger.warning(f"Could not fetch duration via yt_dlp: {e}")

        if media_kind == "audio":
            base = f"/tmp/preview_{uuid.uuid4()}"
            audio_path = f"{base}.mp3"
            cookiefile = get_cookiefile_for_url(url)

            ok, err = download_audio_high_quality(url, audio_path, logger, cookiefile=cookiefile)
            final_path = resolve_mp3_path(audio_path) or audio_path

            if not ok or not os.path.exists(final_path):
                return jsonify({"error": f"Audio download failed: {format_resync_error(err or '')}"}), 500

            resp = send_file(final_path, mimetype="audio/mpeg", as_attachment=False, conditional=False)
            return add_headers(resp, "preview.mp3")

        else:  # video
            import mimetypes
            base = f"/tmp/preview_{uuid.uuid4()}"
            outtmpl = base
            ydl_opts = {
                'outtmpl': outtmpl,
                'merge_output_format': 'mp4',
                'quiet': True,
                'no_warnings': True,
            }
            cookiefile = get_cookiefile_for_url(url)
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

            try:
                download_video_with_retry(url, ydl_opts, retries=2)
            except Exception as e:
                return jsonify({"error": format_user_error(e)}), 500

            file_path = find_downloaded_file(base)
            if not file_path or not os.path.exists(file_path):
                return jsonify({"error": "Could not locate downloaded preview"}), 500

            if not file_path.endswith(".mp4"):
                remux = f"{base}_remux.mp4"
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", file_path, "-c", "copy", "-movflags", "+faststart", remux],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=120
                    )
                    if os.path.exists(remux):
                        file_path = remux
                except Exception:
                    pass

            mime = "video/mp4" if file_path.endswith(".mp4") else (
                mimetypes.guess_type(file_path)[0] or "video/mp4"
            )

            resp = send_file(file_path, mimetype=mime, as_attachment=False, conditional=False)
            return add_headers(resp, os.path.basename(file_path))

    except Exception as e:
        logger.exception("preview-media failed")
        return jsonify({"error": format_user_error(e)}), 500

@app.route("/progress/status", methods=["GET"])
def get_progress_status():
    session_id = request.args.get("session_id")
    if not session_id:
        return jsonify({"error": "Missing session_id"}), 400

    try:
        conn = psycopg2.connect(Config.DATABASE_URL)
        cur = conn.cursor()
        cur.execute("SELECT message FROM progress_updates WHERE session_id = %s", (session_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if row:
            return jsonify({"message": row[0]}), 200
        else:
            return jsonify({"message": "🔄 Starting..."}), 200

    except Exception as e:
        logger.warning(f"[PROGRESS_DB] Failed to fetch progress: {e}")
        return jsonify({"message": "⚠️ Error getting progress"}), 500

progress_queues = {}
@app.route("/progress/<session_id>")
def progress_stream(session_id):
    def event_stream(q: queue.Queue):
        while True:
            message = q.get()
            yield f"data: {message}\n\n"

    q = progress_queues.setdefault(session_id, queue.Queue())
    return Response(stream_with_context(event_stream(q)), mimetype="text/event-stream")

@app.route("/metrics/servers", methods=["GET"])
def metrics_servers():
    try:
        conn = psycopg2.connect(Config.DATABASE_URL)
        cursor = conn.cursor()
        
        # Get server count from tracked_servers table
        cursor.execute("SELECT COUNT(*) FROM tracked_servers")
        total_servers = cursor.fetchone()[0]
        
        # Get total members
        cursor.execute("SELECT SUM(member_count) FROM tracked_servers")
        total_members = cursor.fetchone()[0] or 0
        
        cursor.close()
        conn.close()

        return jsonify({
            "total_servers": total_servers,
            "total_members": total_members
        }), 200

    except Exception as e:
        logger.exception("metrics_servers failed")
        # Return empty instead of crashing
        return jsonify({
            "total_servers": 0,
            "total_members": 0
        }), 200

if __name__ == "__main__":
    cookie_path = Config.COOKIE_FILE
    print(f"🔍 Checking for cookies.txt at: {cookie_path}")
    print(f"✅ Exists: {os.path.exists(cookie_path)}")
    
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
