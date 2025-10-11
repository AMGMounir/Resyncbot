"""
ADMIN COMMANDS - Bot Owner Management Tools

This module contains administrative commands exclusively for the bot owner.
These commands provide advanced control over bot operation, user management,
and system monitoring.

IMPORTANT: All commands in this file are OWNER-ONLY and require the BOT_OWNER_ID
to be set in your .env file. These commands will not work for regular users or
even server administrators.

A lot of these commands were used to setup and debug premium, hence why most of them
are centered around managing a user's premium data. Premium is disabled on production,
and will be for a while, so this file does not have much of a use, but may help anyway.

AVAILABLE COMMANDS:
===================

User Management:
- /clearusage <user> - Reset a specific user's daily usage limits
- /viewusage <user> - View detailed usage statistics for any user
- /clearallusage - Clear all users' usage from the last 24 hours
- /setpremium <user> <true/false> - Grant or revoke premium status
- /adminrefresh <user_id> - Force refresh a user's premium cache
- /admindelete <user_id> - Safely delete all user subscription data

System Monitoring:
- /performance - View real-time CPU, memory, disk, and connection stats
- /queuestats - View dual queue statistics (regular vs priority)
- /servers - View detailed server statistics and distribution

Communication:
- /cookieupdate - Notify designated channel that cookies were updated
- /shout_recent <message> - Broadcast message to channels with recent activity

SETUP REQUIRED:
===============
In your .env file, set:
- BOT_OWNER_ID=your_discord_user_id (required for all commands)
- UPDATE_CHANNEL_ID=channel_id_for_updates (optional, for cookieupdate command)

To get your Discord user ID:
1. Enable Developer Mode in Discord (Settings > Advanced > Developer Mode)
2. Right-click your username and select "Copy User ID"
3. Paste the ID into your .env file
"""
import discord
import logging
import time
from discord import app_commands, Interaction, Embed, Color, utils
from discord.ext import commands
import psycopg2
from config import Config
from backend.performance_monitor import get_performance_stats
from backend.recent_usage import get_recent_commands, prune_old_commands 
from backend.premium_utils import premium_manager
from backend.resync_queue import get_queue_stats
from datetime import datetime, timedelta, timezone
from bot.server_manager import server_manager

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ResyncBot")

def setup_admin_commands(bot: commands.Bot):
    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="clearusage", description="Clear a user's daily usage (owner-only)")
    async def clearusage(interaction: discord.Interaction, user: discord.Member):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)  
        try:            
            # Connect to database
            conn = psycopg2.connect(Config.DATABASE_URL)
            cursor = conn.cursor()
            
            # Clear all usage entries for the user
            cursor.execute("DELETE FROM user_usage WHERE user_id = %s", (str(user.id),))
            rows_deleted = cursor.rowcount
            
            # Commit changes and close connection
            conn.commit()
            cursor.close()
            conn.close()
            
            # Success message
            if rows_deleted > 0:
                await interaction.followup.send(
                    f"✅ Cleared **{rows_deleted}** usage entries for {user.mention}\n"
                    f"Their daily limits have been reset.", 
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"ℹ️ No usage entries found for {user.mention} - nothing to clear.", 
                    ephemeral=True
                )
                
        except Exception as e:
            logger.exception("Error in clearusage command")
            await interaction.followup.send(
                f"❌ Error clearing usage: {str(e)}", 
                ephemeral=True
            )

    @app_commands.default_permissions(administrator=True) 
    @bot.tree.command(name="viewusage", description="View a user's current usage (owner-only)")
    async def viewusage(interaction: discord.Interaction, user: discord.Member):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        try:
            # Connect to database
            conn = psycopg2.connect(Config.DATABASE_URL)
            cursor = conn.cursor()
            
            # Get today's usage
            yesterday = datetime.now(timezone.utc) - timedelta(hours=24)
            cursor.execute("""
                SELECT command_type, COUNT(*) 
                FROM user_usage 
                WHERE user_id = %s AND used_at >= %s 
                GROUP BY command_type
            """, (str(user.id), yesterday))
            
            usage_data = cursor.fetchall()
            cursor.close()
            conn.close()
            
            # Check if user has premium (fix the method name!)
            is_premium = premium_manager.is_premium_user(user.id)
            status = "Premium ✨" if is_premium else "Free"
            
            # Count usage by type
            usage_counts = {}
            for command_type, count in usage_data:
                usage_counts[command_type] = count
                
            # Create embed
            embed = discord.Embed(
                title=f"📊 Usage for {user.display_name}",
                color=discord.Color.gold() if is_premium else discord.Color.blue(),
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="👤 User Status",
                value=f"**Tier:** {status}\n**User ID:** {user.id}",
                inline=False
            )
            
            # Show usage counts
            auto_count = usage_counts.get('auto_resync', 0)
            random_count = usage_counts.get('random_resync', 0)
            
            if is_premium:
                usage_text = (
                    f"🤖 **Auto Resyncs:** {auto_count} (Unlimited)\n"
                    f"🎲 **Random Resyncs:** {random_count} (Unlimited)\n"
                    f"✋ **Manual Resyncs:** Unlimited"
                )
            else:
                usage_text = (
                    f"🤖 **Auto Resyncs:** {auto_count}/5\n"
                    f"🎲 **Random Resyncs:** {random_count}/15\n"
                    f"✋ **Manual Resyncs:** Unlimited"
                )
                
            embed.add_field(
                name="📈 Today's Usage",
                value=usage_text,
                inline=False
            )
            
            # Remove the duplicate premium check - we already have it!
            # Just add the premium status to the embed if you want:
            embed.add_field(
                name="🔍 Premium Details",
                value=f"**Premium Status:** {'Yes' if is_premium else 'No'}",
                inline=False
            )
            
            # Show all recent usage
            if usage_data:
                all_usage = sum(count for _, count in usage_data)
                embed.add_field(
                    name="📊 Total Commands Today",
                    value=f"{all_usage} commands in last 24 hours",
                    inline=True
                )
            
            embed.set_footer(text="Usage data from last 24 hours")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.exception("Error in viewusage command")
            await interaction.followup.send(
                f"❌ Error viewing usage: {str(e)}", 
                ephemeral=True
            )
    
    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="cookieupdate", description="Notify channel that cookies were updated (owner-only)")
    async def cookieupdate(interaction: Interaction):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("Only the bot owner can use this command.", ephemeral=True)
            return

        channel_id = Config.UPDATE_CHANNEL_ID
        channel = bot.get_channel(channel_id)

        if channel is None:
            await interaction.response.send_message("❌ Couldn't find the update channel.", ephemeral=True)
            return

        permissions = channel.permissions_for(channel.guild.me)
        if not permissions.send_messages:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in the update channel.", ephemeral=True
            )
            return

        await channel.send("🍪 Cookies have been updated!")
        await interaction.response.send_message("✅ Notification sent.", ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="shout_recent", description="Shout to recent resync users (owner-only)")
    async def shout_recent(interaction: Interaction, message: str):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("🚫 Only the bot owner can use this command.", ephemeral=True)
            return

        recent = get_recent_commands(minutes=5)
        sent_channels = set()
        count = 0

        for entry in recent:
            channel_id = entry["channel_id"]
            if channel_id in sent_channels:
                continue 

            channel = bot.get_channel(channel_id)
            if channel:
                try:
                    await channel.send(f" **SHOUT**: {message}")
                    sent_channels.add(channel_id)
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to send shout: {e}")

        prune_old_commands(minutes=10)

        await interaction.response.send_message(
            f"📣 Shouted to `{count}` recent channel(s).", ephemeral=True
        )

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="performance", description="View system performance stats (owner-only)")
    async def performance(interaction: Interaction):
        """Show system performance metrics (Owner only)"""
        
        # Check if user is bot owner
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return
        
        # Defer the response since getting performance stats might take a moment
        await interaction.response.defer(ephemeral=True)
        
        try:
            stats = get_performance_stats()
            
            # Handle case where stats might be None or missing keys
            if not stats or "metrics" not in stats or "health" not in stats:
                await interaction.followup.send("❌ Unable to retrieve performance stats - monitor may not be running", ephemeral=True)
                return
                
            metrics = stats["metrics"]
            health = stats["health"]
            
            # Determine embed color based on health status
            if "🟢" in health:
                color = discord.Color.green()
            elif "🟡" in health:
                color = discord.Color.yellow()
            else:
                color = discord.Color.red()
            
            # Create embed
            embed = discord.Embed(
                title="🔍 System Performance",
                color=color,
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(
                name="🔋 Health Status", 
                value=health, 
                inline=False
            )
            
            embed.add_field(
                name="💻 CPU Usage", 
                value=f"{metrics['cpu_percent']:.1f}%", 
                inline=True
            )
            
            embed.add_field(
                name="🧠 Memory Usage", 
                value=f"{metrics['memory_percent']:.1f}%", 
                inline=True
            )
            
            embed.add_field(
                name="💾 Disk Usage", 
                value=f"{metrics['disk_usage']:.1f}%", 
                inline=True
            )
            
            embed.add_field(
                name="🔗 Active Connections", 
                value=str(metrics['active_connections']), 
                inline=True
            )
            
            # Better uptime formatting
            uptime_seconds = metrics['uptime']
            if uptime_seconds >= 86400:  # More than a day
                uptime_days = uptime_seconds / 86400
                uptime_str = f"{uptime_days:.1f} days"
            elif uptime_seconds >= 3600:  # More than an hour
                uptime_hours = uptime_seconds / 3600
                uptime_str = f"{uptime_hours:.1f} hours"
            else:
                uptime_minutes = uptime_seconds / 60
                uptime_str = f"{uptime_minutes:.1f} minutes"
                
            embed.add_field(
                name="⏱️ System Uptime", 
                value=uptime_str, 
                inline=True
            )
            
            # Add footer with timestamp
            embed.set_footer(text="Performance data collected at")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except ImportError:
            await interaction.followup.send(
                "❌ Performance monitoring module not available. Please ensure `psutil` is installed.", 
                ephemeral=True
            )
        except Exception as e:
            logger.exception("Error in performance command")
            await interaction.followup.send(
                f"❌ Error getting performance stats: {str(e)}", 
                ephemeral=True
            )

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="setpremium", description="Set user premium status (owner-only)")
    async def setpremium(interaction: discord.Interaction, user: discord.Member, premium: bool):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("Owner only!", ephemeral=True)
            return
        
        premium_manager.set_premium_status(user.id, premium)
        await interaction.response.send_message(f"✅ Set {user.mention} premium: {premium}", ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="servers", description="View server statistics (owner-only)")
    async def servers(interaction: Interaction):
        """Show detailed server statistics (Owner only)"""
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            servers = server_manager.get_all_servers()
            total_servers = len(servers)
            total_members = sum(s["member_count"] or 0 for s in servers)
            avg_members = total_members // total_servers if total_servers else 0

            # Top 10 largest
            top_servers = sorted(servers, key=lambda s: s["member_count"], reverse=True)[:10]
            top_list = "\n".join(
                f"`{i+1:2}.` **{s['name'][:30]}** - {s['member_count']:,} members"
                for i, s in enumerate(top_servers)
            )

            # Size Distribution
            size_ranges = {
                "🔸 Tiny (1–100)": 0,
                "🔹 Small (101–1,000)": 0,
                "🔸 Medium (1,001–10,000)": 0,
                "🔹 Large (10,001–50,000)": 0,
                "🔸 Huge (50,000+)": 0,
            }
            for s in servers:
                c = s["member_count"]
                if c <= 100:
                    size_ranges["🔸 Tiny (1–100)"] += 1
                elif c <= 1000:
                    size_ranges["🔹 Small (101–1,000)"] += 1
                elif c <= 10000:
                    size_ranges["🔸 Medium (1,001–10,000)"] += 1
                elif c <= 50000:
                    size_ranges["🔹 Large (10,001–50,000)"] += 1
                else:
                    size_ranges["🔸 Huge (50,000+)"] += 1
            size_dist = "\n".join(f"{k}: {v}" for k, v in size_ranges.items())

            # Recent joins (last 30 days)
            recent_cutoff = datetime.utcnow() - timedelta(days=30)
            recent_servers = []
            for s in servers:
                try:
                    dt = datetime.fromisoformat(s["joined_at"].replace("Z", "+00:00")) if s["joined_at"] else None
                    if dt and dt >= recent_cutoff:
                        recent_servers.append(s)
                except:
                    continue
            recent_list = "\n".join(
                f"• **{s['name'][:25]}** ({s['member_count']:,} members)"
                for s in recent_servers[:5]
            ) or "None"

            # Construct embed
            embed = Embed(
                title="🏠 Server Statistics",
                color=Color.blue(),
                timestamp=utils.utcnow()
            )
            embed.add_field(name="📊 Overview", value=(
                f"**Total Servers:** {total_servers}\n"
                f"**Total Members:** {total_members:,}\n"
                f"**Average Members:** {avg_members:,}"
            ), inline=False)

            if top_list:
                embed.add_field(name="🏆 Top 10 Largest Servers", value=top_list, inline=False)
            embed.add_field(name="📈 Server Size Distribution", value=size_dist, inline=True)
            embed.add_field(name="🆕 Recent Additions (30 days)", value=recent_list, inline=True)

            embed.set_footer(text="Server data collected at")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.exception("Error in /servers command")
            await interaction.followup.send("⚠️ Something went wrong!", ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="adminrefresh", description="Force refresh user's premium cache")
    async def admin_refresh(interaction: discord.Interaction, user_id: str):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("Admin only!", ephemeral=True)
            return
        
        target_user_id = int(user_id)
        premium_manager.force_cache_refresh(target_user_id)
        
        await interaction.response.send_message(f"✅ Forced cache refresh for user {target_user_id}", ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="admindelete", description="Safely delete user's subscription data")
    async def admin_delete(interaction: discord.Interaction, user_id: str):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("Admin only!", ephemeral=True)
            return
        
        target_user_id = int(user_id)
        premium_manager.admin_delete_user_data(target_user_id)
        
        await interaction.response.send_message(f"✅ Deleted all data for user {target_user_id}", ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="queuestats", description="View dual queue statistics (owner-only)")
    async def queuestats(interaction: discord.Interaction):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return
        
        stats = get_queue_stats()
        
        embed = discord.Embed(
            title="📊 Dual Queue Statistics",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow()
        )
        
        # Regular Queue Info
        regular_status = "🟢 Idle" if stats['regular_active_jobs'] == 0 else f"🔄 {stats['regular_active_jobs']} active"
        embed.add_field(
            name="🔷 Regular Queue",
            value=f"**Queued:** {stats['regular_queue_size']}\n**Active:** {stats['regular_active_jobs']}\n**Workers:** {stats['regular_workers']}\n**Status:** {regular_status}",
            inline=True
        )
        
        # Priority Queue Info
        priority_status = "🟢 Idle" if stats['priority_active_jobs'] == 0 else f"🔄 {stats['priority_active_jobs']} active"
        embed.add_field(
            name="🌟 Priority Queue", 
            value=f"**Queued:** {stats['priority_queue_size']}\n**Active:** {stats['priority_active_jobs']}\n**Workers:** {stats['priority_workers']}\n**Status:** {priority_status}",
            inline=True
        )
        
        # Total Summary
        total_jobs = stats['total_queued'] + stats['total_active']
        embed.add_field(
            name="📈 System Total",
            value=f"**Total Jobs:** {total_jobs}\n**Queued:** {stats['total_queued']}\n**Processing:** {stats['total_active']}\n**Workers:** {stats['regular_workers'] + stats['priority_workers']}",
            inline=True
        )
        
        # Show active job details if any
        if stats['total_active'] > 0:
            active_details = []
            
            # Regular queue active jobs
            for worker_id, job_info in stats['active_regular_jobs'].items():
                processing_time = time.time() - job_info['started_at']
                user_type = "Premium" if job_info['is_premium'] else "Free"
                active_details.append(f"🔷 Worker {worker_id}: {user_type} user ({processing_time:.0f}s)")
            
            # Priority queue active jobs  
            for worker_id, job_info in stats['active_priority_jobs'].items():
                processing_time = time.time() - job_info['started_at']
                active_details.append(f"🌟 Worker {worker_id}: Premium user ({processing_time:.0f}s)")
            
            if active_details:
                embed.add_field(
                    name="🔄 Currently Processing",
                    value="\n".join(active_details[:6]),  # Show max 6 to avoid embed limits
                    inline=False
                )
        
        # Queue utilization (only if there are jobs)
        if total_jobs > 0:
            regular_total = stats['regular_queue_size'] + stats['regular_active_jobs']
            priority_total = stats['priority_queue_size'] + stats['priority_active_jobs']
            
            regular_percent = (regular_total / total_jobs) * 100
            priority_percent = (priority_total / total_jobs) * 100
            
            embed.add_field(
                name="⚖️ Load Distribution",
                value=f"**Regular:** {regular_percent:.1f}%\n**Priority:** {priority_percent:.1f}%",
                inline=True
            )
        
        # Smart routing explanation
        embed.add_field(
            name="🧠 Smart Routing",
            value="Premium users are automatically routed to the shorter queue for optimal speed!",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.default_permissions(administrator=True)
    @bot.tree.command(name="clearallusage", description="Clear all users' usage from the last 24 hours (owner-only)")
    async def clear_all_usage(interaction: discord.Interaction):
        if interaction.user.id != Config.BOT_OWNER_ID:
            await interaction.response.send_message("❌ Only the bot owner can use this command.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        try:
            # Connect to database
            conn = psycopg2.connect(Config.DATABASE_URL)
            cursor = conn.cursor()

            # Calculate cutoff time
            yesterday = datetime.now(timezone.utc) - timedelta(hours=24)

            # Delete all usage from the last 24 hours
            cursor.execute("DELETE FROM user_usage WHERE used_at >= %s", (yesterday,))
            rows_deleted = cursor.rowcount

            # Commit and close
            conn.commit()
            cursor.close()
            conn.close()

            await interaction.followup.send(
                f"✅ Cleared `{rows_deleted}` usage entries from the last 24 hours.",
                ephemeral=True
            )

        except Exception as e:
            logger.exception("Error in clearallusage command")
            await interaction.followup.send(
                f"❌ Error clearing usage: {str(e)}",
                ephemeral=True
            )