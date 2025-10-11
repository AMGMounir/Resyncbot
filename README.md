# ResyncBot

A powerful Discord bot that allows editors and edit-enjoyers alike to resync edits. What was once done manually in 30 minutes within an editor can now be done in 30 seconds automatically.


**[Visit ResyncBot Website](https://www.resyncbot.xyz/)**
[![ResyncBot Demo](https://img.youtube.com/vi/zV8PnmXrJCs/maxresdefault.jpg)](https://youtu.be/zV8PnmXrJCs?si=TCUyh3CiLv3rNoF2)

## Features
- Audio track management with 10,000+ pre-loaded tracks
- Spotify link integration (optional)
- Server tracking and analytics
- Premium user subscriptions
- User voting system
- Usage statistics and progress tracking

## Prerequisites
- Python 3.8 or higher
- PostgreSQL database
- Discord account with Developer Mode enabled
- (Optional) Spotify Developer account for Spotify features

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/resyncbot.git
cd resyncbot
```

### 2. Install Dependencies

```bash
# Create a virtual environment (recommended)
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install required packages
pip install -r requirements.txt
```

### 3. Set Up PostgreSQL Database

**Install PostgreSQL:**
- **macOS**: `brew install postgresql && brew services start postgresql`
- **Ubuntu/Debian**: `sudo apt install postgresql postgresql-contrib`
- **Windows**: Download from [postgresql.org](https://www.postgresql.org/download/windows/)

**Create and initialize the database:**

```bash
# Create database
createdb resyncbot

# Import schema and seed data (includes 10,000 tracks)
psql -d resyncbot -f database/resyncbot_init.sql
```

**Alternative:** You can use hosted PostgreSQL services like [Railway](https://railway.app/), [Supabase](https://supabase.com/), or [Neon](https://neon.tech/). Just update your `DATABASE_URL` in `.env` with their connection string.

### 4. Create Your Discord Bot

Follow Discord's official guide to create a bot application:

**[Discord Developer Portal - Creating a Bot Account](https://discord.com/developers/docs/getting-started#creating-an-app)**

**Quick summary:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to the "Bot" tab and click "Add Bot"
4. Under "Token", click "Copy" to save your bot token
5. Enable the following Privileged Gateway Intents:
   - Server Members Intent
   - Message Content Intent
   - Presence Intent
6. Go to "OAuth2" > "URL Generator":
   - Select scopes: `bot`, `applications.commands`
   - Select necessary bot permissions (Administrator for full functionality)
   - Copy the generated URL and open it to invite the bot to your server

### 5. Configure Environment Variables

```bash
# Copy the example environment file
cp .env.example .env
```

**Edit `.env` and add your bot token:**

```env
DISCORD_BOT_TOKEN=your_bot_token_from_step_4
DATABASE_URL=postgresql://postgres:password@localhost:5432/resyncbot
RESYNC_API_BASE=http://localhost:8000
DEBUG_MODE=true
```
### 6. (Optional) Setup YouTube Cookies

**Most users can skip this step!** Cookies are only needed if you encounter issues with certain YouTube videos.

yt-dlp can download most YouTube videos without authentication. However, if you need to access age-restricted, private, or region-locked videos, you'll need to provide cookies:

1. Create `data/cookies.txt` in your project directory
2. Export your YouTube cookies using a browser extension like [Cookie Editor](https://chromewebstore.google.com/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)
3. Paste the cookies into `data/cookies.txt`

**For local development:** Use your own YouTube account - it's safe since the file is ignored by git.

**For deployment:** Create a separate/burner YouTube account for security.

**Note:** The bot will work fine without cookies for most YouTube videos. Only add them if you encounter "Sign in to confirm your age" or similar errors.
### 7. (Optional) Set Up Spotify Integration

**Only needed if you want Spotify link features!**

1. Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. Click "Create an App"
3. Copy your Client ID and Client Secret
4. Add them to your `.env`:

```env
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
```

If you skip this step, ResyncBot will work fine - you just won't be able to use Spotify-related commands.

### 8. Run ResyncBot

You need to run both the API backend and the bot:

```bash
# Terminal 1 - Start the API backend
python resync_api.py

# Terminal 2 - Start the bot
python main.py
```

**Note:** You may see this error initially:

```
API Error: ⚠️ An unexpected error occurred: Cannot connect to host resync-bot-dev.fly.dev:443 ssl:default [None]...
```

**This is normal!** It's due to Discord's caching. The bot will still work correctly - just ignore this message.

### 9. Test Your Bot

Once both services are running, go to your Discord server and try using ResyncBot commands! If everything is set up correctly, the bot should respond to commands.

## Project Structure

This project is organized into two main parts:

- **`/bot`** - Discord bot frontend (commands, events, user interactions)
- **`/backend`** - Flask API server (video/audio processing, FFmpeg operations)

### Quick Navigation

- **Start here:** `main.py` - Entry point to run the Discord bot
- **All commands:** `/bot/commands/` - Each file is a Discord slash command
- **Processing logic:** `backend/video_utils.py` - All video/audio manipulation
- **API endpoints:** `backend/resync_api.py` - Main Flask server with all endpoints
- **Database setup:** `/database/` - PostgreSQL schema with 10,000 pre-loaded tracks

For a complete breakdown of every file and directory, see [PROJECT_STRUCTURE.md](PROJECT_STRUCTURE.md).

### How It Works

1. User runs a command in Discord (e.g., `/resyncmp4`)
2. Bot receives command and validates input
3. Bot sends HTTP request to backend API
4. Backend downloads media (yt-dlp), processes it (FFmpeg), and returns result
5. Bot uploads the processed video back to Discord

## Configuration

### Environment Variables

- `DISCORD_BOT_TOKEN` - Your Discord bot token (required)
- `DATABASE_URL` - PostgreSQL connection string (required)
- `RESYNC_API_BASE` - API backend URL (default: http://localhost:8000)
- `DEBUG_MODE` - Enable verbose logging (default: true)
- `SPOTIFY_CLIENT_ID` - Spotify API client ID (optional)
- `SPOTIFY_CLIENT_SECRET` - Spotify API client secret (optional)
- `LOG_CHANNEL_ID` - Discord channel ID for bot logs (optional)

### Database Tables

See [database/README.md](database/README.md) for detailed database documentation.

## Troubleshooting

**"Cannot connect to host" error on startup:**
- This is expected! Discord's caching causes this. The bot works normally - ignore it.

**Bot not responding to commands:**
- Make sure both `main.py` and `resync_api.py` are running
- Check that Message Content Intent is enabled in Discord Developer Portal
- Verify your bot token is correct in `.env`

**Database connection errors:**
- Ensure PostgreSQL is running
- Check your `DATABASE_URL` is correct
- Verify the database was initialized with `resyncbot_init.sql`

**Spotify features not working:**
- Spotify integration is optional - the bot works without it
- If you want Spotify features, verify your credentials are correct
- Check the Spotify Developer Dashboard for API status

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Support

If you encounter issues:
- Check the Troubleshooting section above
- Open an issue on GitHub with details about your problem
- Include error messages and your environment (OS, Python version, etc.)

## License

[MIT License](LICENSE) - Feel free to use this project for your own Discord server!

## Acknowledgments

- Built with [discord.py](https://github.com/Rapptz/discord.py)
- Spotify integration via [Spotipy](https://github.com/plamere/spotipy)
- Database: PostgreSQL