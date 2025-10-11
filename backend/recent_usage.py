from datetime import datetime, timedelta

# Global list to track recent commands
RECENT_COMMANDS = []

def log_recent_command(user_id: int, channel_id: int):
    """Log a new command usage."""
    RECENT_COMMANDS.append({
        "user_id": user_id,
        "channel_id": channel_id,
        "timestamp": datetime.utcnow()
    })

def get_recent_commands(minutes: int = 5):
    """Get all recent command logs within the last `minutes`."""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    return [entry for entry in RECENT_COMMANDS if entry["timestamp"] >= cutoff]

def prune_old_commands(minutes: int = 10):
    """Clean out command entries older than `minutes`."""
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    RECENT_COMMANDS[:] = [entry for entry in RECENT_COMMANDS if entry["timestamp"] >= cutoff]
