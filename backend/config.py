import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Centralized configuration management"""

    PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
    # Bot configuration
    DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
    BOT_OWNER_ID: int = int(os.getenv("BOT_OWNER_ID") or "688590681731891240")

    # API configuration
    RESYNC_API_BASE: str = os.getenv("RESYNC_API_BASE", "http://localhost:5000")
    ADMIN_TOKEN: str = os.getenv("ADMIN_TOKEN", "")
    SPOTIFY_CLIENT_ID: str = os.getenv("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET: str = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
    STRIPE_WEBHOOK_SECRET: str = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PUBLIC_API_KEY: str = os.getenv("STRIPE_PUBLIC_API_KEY", "")
    STRIPE_SECRET_API_KEY: str = os.getenv("STRIPE_SECRET_API_KEY", "")
    RESYNC_API_SECRET: str = "e2fd48a6f471431ea9e"

    # Discord channels
    LOG_CHANNEL_ID: int = int(os.getenv("LOG_CHANNEL_ID") or "1400257461142687827")
    UPDATE_CHANNEL_ID: int = int(os.getenv("UPDATE_CHANNEL_ID") or "1396262434146095246")
    
    # Processing limits
    MAX_FILE_SIZE: int = 100 * 1024 * 1024  # 200MB
    MAX_DURATION: int = 60  # seconds
    NUM_WORKERS: int = int(os.getenv("NUM_WORKERS", "3"))
    
    # Paths
    DATA_DIR = os.path.join(os.path.dirname(PROJECT_ROOT), "data")
    COOKIE_FILE = os.getenv("COOKIE_FILE", os.path.join(DATA_DIR, "cookies.txt"))
    SERVER_LIST_FILE = os.path.join(DATA_DIR, "servers.json")
    
    # Rate limiting
    PROGRESS_UPDATE_INTERVAL: int = 5  # seconds
    MAX_DOWNLOAD_DURATION: int = 300
    MAX_VIDEO_DURATION: int = 5 * 60 # 5 * 60 = 5 mins (600 secs) 
    MAX_DOWNLOAD_VIDEO_DURATION: int = 10 * 60 # For download_video command specifically

    # Pricing / Limits
    MONTHLY_PREMIUM_PRICE: int = 3 # in usd
    YEARLY_PREMIUM_PRICE: int = 15 # in usd
    LIFETIME_PREMIUM_PRICE: int = 25 # in usd
    RANDOM_LIMITS: int = 8
    AUTO_LIMITS: int = 4
    COOLDOWN: int = 0 
    
    TOPGG_TOKEN: str = os.getenv("TOPGG_TOKEN", "") 
    TOPGG_WEBHOOK_SECRET: str = os.getenv("TOPGG_WEBHOOK_SECRET", "")  # Webhook password
    TOPGG_BOT_ID: str = os.getenv("TOPGG_BOT_ID", "")

    PREMIUM_ENABLED: bool = False
    @classmethod
    def validate(cls) -> bool:
        """Validate required configuration"""
        if not cls.DISCORD_BOT_TOKEN:
            raise EnvironmentError("DISCORD_BOT_TOKEN is required")
        return True