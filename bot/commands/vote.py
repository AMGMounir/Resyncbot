'''
For top.gg voting, you don't need to do anything with this file.
'''
import discord
from discord.ext import commands
from discord import app_commands
from backend.voting_utils import voting_manager
from backend.premium_utils import premium_manager
from config import Config

def setup_vote_command(bot: commands.Bot):
    @bot.tree.command(
        name="vote",
        description="Vote for ResyncBot on Top.gg to reset your daily limits!"
    )
    async def vote(interaction: discord.Interaction):
        try:
            user_id = interaction.user.id
            
            # Check if user is premium (they don't need to vote)
            if premium_manager.is_premium_user(user_id):
                embed = discord.Embed(
                    title="Premium User",
                    description="You're already a premium user with unlimited commands! Thanks for supporting ResyncBot.\nIf you still want to vote, you can do so [here](https://top.gg/bot/1372406004515475577/vote) to help us grow!",
                    color=discord.Color.gold()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            # Get user's current usage and voting stats
            usage_stats = premium_manager.get_user_usage_stats(user_id)
            vote_stats = voting_manager.get_user_vote_stats(user_id)
            
            auto_used = usage_stats.get('auto_resync', 0)
            random_used = usage_stats.get('random_resync', 0)
            auto_left = max(0, Config.AUTO_LIMITS - auto_used)
            random_left = max(0, Config.RANDOM_LIMITS - random_used)
            
            embed = discord.Embed(
                title="Vote for ResyncBot!",
                color=discord.Color.blue()
            )
            
            # Current usage status
            embed.add_field(
                name="Current Daily Usage",
                value=f"🤖 Auto Resyncs: {auto_used}/{Config.AUTO_LIMITS} ({auto_left} left)\n"
                      f"🎲 Random Resyncs: {random_used}/{Config.RANDOM_LIMITS} ({random_left} left)",
                inline=False
            )
            
            # Voting info
            if vote_stats['can_reset_today']:
                embed.add_field(
                    name="Reset Your Limits!",
                    value="Vote for ResyncBot on Top.gg to **instantly reset** your daily command limits!",
                    inline=False
                )
                embed.add_field(
                    name="How it works:",
                    value="1. Click the vote button below\n"
                          "2. Vote on Top.gg\n" 
                          "3. Your limits are automatically reset!\n"
                          "4. You can do this once per day",
                    inline=False
                )
                embed.color = discord.Color.green()
            else:
                embed.add_field(
                    name="Already Voted Today!",
                    value="You've already voted and reset your limits today. Come back tomorrow for another reset!",
                    inline=False
                )
                embed.color = discord.Color.orange()
            
            # Vote history
            if vote_stats['total_votes'] > 0:
                embed.add_field(
                    name="Your Vote History",
                    value=f"Total votes: {vote_stats['total_votes']}\n"
                          f"Last vote: {vote_stats['last_vote_at'].strftime('%Y-%m-%d %H:%M UTC') if vote_stats['last_vote_at'] else 'Never'}",
                    inline=False
                )
            
            # Create vote button
            view = discord.ui.View()
            vote_button = discord.ui.Button(
                label="Vote on Top.gg",
                style=discord.ButtonStyle.link,
                url=f"https://top.gg/bot/{Config.TOPGG_BOT_ID}/vote",
                emoji="🗳️"
            )
            view.add_item(vote_button)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            await interaction.response.send_message(
                "An error occurred while processing your vote command. Please try again later.",
                ephemeral=True
            )
            print(f"Error in vote command: {e}")