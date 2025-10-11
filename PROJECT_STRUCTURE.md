# ResyncBot Project Structure

This document explains the organization of the ResyncBot codebase to help contributors navigate and understand the project.

## Root Directory

```
ResyncBot-Dev/
├── bot/                    # Discord bot frontend (commands, events, UI)
├── backend/                # API server and media processing
├── data/                   # Runtime data (cookies, logs)
├── database/               # Database schema and seed data
├── logs/                   # Application logs
├── venv/                   # Python virtual environment
├── .env                    # Environment variables (not in git)
├── .env-example            # Template for environment setup
├── .gitignore              # Git ignore rules
├── config.py               # Configuration management
├── CONTRIBUTING.md         # Contribution guidelines
├── database_builder.py     # Tool to expand track database
├── LICENSE                 # MIT License
├── main.py                 # **MAIN ENTRY POINT** - Starts the Discord bot
├── README.md               # Project documentation
└── requirements.txt        # Python dependencies
```

---

## `/bot` - Discord Bot Frontend

The Discord bot that users interact with. Handles commands, events, and Discord-specific logic.

### Core Files

- **`bot.py`** - Bot initialization, intents, and Discord client setup
- **`events.py`** - Discord event handlers (on_ready, on_guild_join, etc.)
- **`server_manager.py`** - Manages server database (tracks which servers bot is in)
- **`utils.py`** - General bot utilities (permission checks, formatting, helpers)

### `/bot/commands` - All Bot Commands

Each file contains related Discord slash commands:

- **`admin_commands.py`** - Owner-only management commands (/clearusage, /viewusage, /performance, etc.)
- **`autoresyncmedia.py`** - `/autoresyncmedia` - Auto-sync with video + audio URLs
- **`autoresyncmp3.py`** - `/autoresyncmp3` - Auto-sync with uploaded video + audio files
- **`autoresyncmp4.py`** - `/autoresyncmp4` - Auto-sync with uploaded video + audio URL
- **`cmds.py`** - General utility commands (/guide, /donate, /info, /invite)
- **`downloadaudio.py`** - `/downloadaudio` - Download audio from URLs
- **`downloadvideo.py`** - `/downloadvideo` - Download video from URLs
- **`loopaudio.py`** - `/loopaudio` - Loop audio segments for editing
- **`premium_commands.py`** - Premium subscription management (production only)
- **`resyncmedia.py`** - `/resyncmedia` - Manual resync with video + audio URLs
- **`resyncmp3.py`** - `/resyncmp3` - Manual resync with uploaded video + audio files
- **`resyncmp4.py`** - `/resyncmp4` - Manual resync with uploaded video + audio URL
- **`resyncrandomfile.py`** - `/resyncrandomfile` - Resync with random DB track (uploaded video)
- **`resyncrandommedia.py`** - `/resyncrandommedia` - Resync with random DB track (video URL)
- **`vote.py`** - `/vote` - Top.gg voting integration

---

## `/backend` - API Server & Media Processing

The Flask API server that handles all video/audio processing. This is where the heavy lifting happens.

### Core Files

- **`resync_api.py`** - **MAIN BACKEND FILE** - Flask API with all processing endpoints
- **`video_utils.py`** - **ALL THE MAGIC** - Video/audio processing functions (FFmpeg, yt-dlp, BPM detection, sync algorithms)
- **`config.py`** - Configuration (duplicated from root for AWS deployment)
- **`command_logger.py`** - Logs all command usage to Discord channel (for monitoring)
- **`error_handler.py`** - Custom error classes and user-friendly error formatting

### Supporting Modules

- **`performance_monitor.py`** - System resource monitoring (CPU, memory, disk) for debugging
- **`recent_usage.py`** - Command cooldown tracking via database (scalable rate limiting)
- **`resync_queue.py`** - Dual-queue system for premium vs free users (smart job distribution)

### Premium/Production (Can be ignored for local development)

- **`premium_utils.py`** - Premium subscription management
- **`stripe_handler.py`** - Stripe webhook handling for payments
- **`voting_utils.py`** - Top.gg voting rewards and tracking

---

## `/database`

Database schema and seed data for the track database.

- **`resyncbot_init.sql`** - PostgreSQL schema + 10,000 pre-loaded tracks
- **`README.md`** - Database setup instructions

---

## `/data`

Runtime data stored during bot operation:

- **`cookies.txt`** - YouTube/SoundCloud cookies for age-restricted content (you need to create this)
- Other runtime files (logs, temp data)

---

## `/logs`

Application logs for debugging and monitoring.

---

## Root Configuration Files

- **`main.py`** - **ENTRY POINT** - Run this to start the Discord bot
- **`config.py`** - Centralized configuration (reads from `.env`)
- **`.env`** - Environment variables (secrets, API keys, database URL)
- **`.env-example`** - Template showing what variables are needed
- **`requirements.txt`** - Python package dependencies
- **`database_builder.py`** - Optional tool to add more tracks to the database

---

## How Everything Connects

### Command Flow (Example: User runs `/resyncmp4`)

1. **User executes command** in Discord
2. **`bot/commands/resyncmp4.py`** receives the interaction
3. Command validation and checks happen in bot layer
4. **Job is added to queue** (`backend/resync_queue.py`)
5. **Bot sends request** to Flask API (`backend/resync_api.py`)
6. **API endpoint** (`/resyncmp4`) processes the request
7. **`video_utils.py`** does the actual work:
   - Downloads video (yt-dlp)
   - Downloads audio (yt-dlp)
   - Trims both (FFmpeg)
   - Combines them (FFmpeg)
8. **API returns** processed video to bot
9. **Bot uploads** video to Discord
10. **`command_logger.py`** logs the command to monitoring channel

### Data Flow Diagram

```
┌─────────────┐
│   Discord   │
│    User     │
└──────┬──────┘
       │ /resyncmp4
       ▼
┌─────────────────┐
│   bot/commands  │ ◄── bot/utils.py (helpers)
│  resyncmp4.py   │ ◄── bot/events.py (context)
└────────┬────────┘
         │ HTTP POST
         ▼
┌──────────────────────┐
│  backend/resync_api  │
│   Flask Endpoint     │
└─────────┬────────────┘
          │
          ▼
┌──────────────────────┐
│ backend/video_utils  │
│  - download_audio()  │
│  - trim_video()      │
│  - combine_ffmpeg()  │
└─────────┬────────────┘
          │
          ▼
┌──────────────────────┐
│  Processed Video     │
│  (sent back to bot)  │
└──────────────────────┘
```

---

## Key Architecture Decisions

### Why Split Frontend/Backend?

- **Bot (frontend)**: Handles Discord-specific logic, user interactions, permissions
- **Backend (API)**: Heavy processing (FFmpeg, yt-dlp) isolated from Discord connection
- **Benefits**: Scalable, can restart API without disconnecting bot, easier to debug

### Why Dual Queue System?

Premium users get faster processing via dedicated queue. Smart routing ensures premium users always get shortest wait time.

### Why PostgreSQL?

- Track database needs complex queries (BPM matching, random selection)
- User usage tracking requires ACID properties
- Handles concurrent writes from multiple workers

---

## Essential Tools & Dependencies

### External Binaries (Must be installed)

- **FFmpeg** - Video/audio processing
- **FFprobe** - Media file inspection
- **PostgreSQL** - Database server

### Python Libraries (in requirements.txt)

- **discord.py** - Discord bot framework
- **Flask** - API web server
- **yt-dlp** - Download videos/audio from URLs
- **librosa** - Audio analysis and BPM detection
- **psycopg2** - PostgreSQL database driver
- **aiohttp** - Async HTTP requests
- **Pillow** - Image processing
- **scipy** - Signal processing for audio sync

---

## Additional Documentation

- **README.md** - Main project documentation
- **CONTRIBUTING.md** - How to contribute
- **database/README.md** - Database setup guide
- **LICENSE** - MIT License

---

## Debugging Tips

### Bot not responding?

- Check `logs/` directory for errors
- Verify `.env` has correct `DISCORD_BOT_TOKEN`
- Ensure bot has proper intents enabled in Discord Developer Portal

### API failing?

- Check `backend/resync_api.py` is running (`python backend/resync_api.py`)
- Verify `RESYNC_API_SECRET` matches in both bot and backend `.env`
- Look for FFmpeg errors in backend logs

### Video processing failing?

- Check FFmpeg is installed: `ffmpeg -version`
- Verify cookies.txt exists in `data/` for age-restricted content
- Check disk space in `/tmp/` directory

---

## Need Help?

- Open an issue on GitHub
- Check existing issues for similar problems
- Read the inline code comments - they're there to help!

---

**Last Updated**: October 2025