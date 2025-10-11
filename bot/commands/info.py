import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from config import Config
"""
info.py

Defines the `/info` slash command for the Discord bot.

Functionality:
    - Shows paginated information about bot commands
    - Page 1: Random Resync Commands
    - Page 2: Auto Resync Commands  
    - Page 3: Manual Resync Commands
    - Page 4: Other Commands
    - Navigation with reaction buttons
"""

def setup_info(bot: commands.Bot):
    """
    Registers the /info slash command to the given Discord bot instance.

    Args:
        bot (commands.Bot): The bot instance where the command should be registered.
    """
    
    class InfoView(discord.ui.View):
        def __init__(self, user_id: int):
            super().__init__(timeout=300)  # 5 minute timeout
            self.user_id = user_id
            self.current_page = 0
            self.max_pages = 4
            
        def get_embed(self) -> discord.Embed:
            """Get the embed for the current page"""
            if self.current_page == 0:
                return self.get_random_resync_embed()
            elif self.current_page == 1:
                return self.get_auto_resync_embed()
            elif self.current_page == 2:
                return self.get_manual_resync_embed()
            elif self.current_page == 3:
                return self.get_other_commands_embed()
            elif self.current_page == 4:
                return self.get_contributors_embed()
        
        def get_random_resync_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="Random Resync Commands",
                description="Get a random track from our database that matches your video's BPM!",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="🎵 `/resyncrandommedia`",
                value="• Provide a video URL (YouTube, etc.)\n• Bot finds a random track with matching BPM\n• Automatically syncs using optimal waveform analysis",
                inline=False
            )
            
            embed.add_field(
                name="📁 `/resyncrandomfile`", 
                value="• Upload your video file directly\n• Same BPM matching from database\n• Great for edited videos or downloaded content",
                inline=False
            )
            
            embed.add_field(
                name="How it Works",
                value="1. Analyzes your video's audio BPM\n2. Searches database for tracks with matching tempo\n3. Randomly selects one and syncs it perfectly\n4. Uses waveform analysis for precise timing",
                inline=False
            )
            
            embed.set_footer(text="Page 1/5 • Use buttons to navigate")
            return embed
        
        def get_auto_resync_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="Auto Resync Commands", 
                description="Intelligent sync detection - let the bot find the perfect timing!",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="🌐 `/autoresyncmedia`",
                value="• Video URL + Audio URL\n• Choose sync method (waveform/beat/both)\n• Bot analyzes and finds optimal sync point\n• No manual timing needed!",
                inline=False
            )
            
            embed.add_field(
                name="📁 `/autoresyncmp4`",
                value="• Upload video + provide audio URL\n• Same intelligent sync detection",
                inline=False
            )
            
            embed.add_field(
                name="🎵 `/autoresyncmp3`",
                value="• Upload both video and audio files\n• Complete offline processing\n• Best for when you have both files ready",
                inline=False
            )
            
            embed.add_field(
                name="🧠 Sync Methods",
                value="**🔊 Waveform:** Audio pattern matching (most accurate for tone/atmosphere matching, better overall)\n**🎵 Beat:** Rhythm/tempo matching (more accurate for precise beat matching)\n**🎯 Both:** Uses both methods for best results",
                inline=False
            )
            
            embed.set_footer(text="Page 2/5 • Use buttons to navigate")
            return embed
        
        def get_manual_resync_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="⚙️ Manual Resync Commands",
                description="Full control over timing - specify exactly when audio should start!\nUse /guide for tips.",
                color=discord.Color.orange()
            )
            
            embed.add_field(
                name="🌐 `/resyncmedia`",
                value="• Video URL + Audio URL + timing offset\n• Classic resync with manual control\n• Specify exactly when audio starts\n• Most precise when you know the timing",
                inline=False
            )
            
            embed.add_field(
                name="📁 `/resyncmp4`",
                value="• Upload video + provide audio URL + offset\n• Manual timing control",
                inline=False
            )
            
            embed.add_field(
                name="🎵 `/resyncmp3`",
                value="• Upload both video and audio + offset\n• Complete manual control",
                inline=False
            )
            
            embed.add_field(
                name="⏰ Timing Formats",
                value="• `0:30` - Start audio at 30 seconds\n• `1:15` - Start audio at 1 minute 15 seconds\n• `2:30-1:45` - Difference calculation (45 seconds)\n• Video start/end times also supported",
                inline=False
            )
            
            embed.set_footer(text="Page 3/5 • Use buttons to navigate")
            return embed
        
        def get_other_commands_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="🛠️ Other Commands",
                description="Additional tools and utilities!",
                color=discord.Color.purple()
            )
            embed.add_field(
                name=" `🎥 /downloadvideo`",
                value="• Download a video from a given video link in full quality\n• Supports YouTube, Instagram, TikTok, and more\n• No more need for third-party sites!",
                inline=False
            )
            embed.add_field(
                name="🎵 `/downloadaudio`",
                value="• Download an audio from a given audio link\n• Supports Spotify, Soundcloud, YouTube, and more\n• No more need for third-party sites!",
                inline=False
            )
            embed.add_field(
                name="🔄 `/loopaudio`",
                value="• Upload audio file\n• Specify start/end times and loop count\n• Creates looped audio for editing inspiration\n• Perfect for beat loops and samples",
                inline=False
            )
            embed.add_field(
                name="📊 `/guide`",
                value="• A guide to manual and auto resync techniques\n• Learn how to get perfect beat-matching\n• Tips for using different sync methods",
                inline=False
            )
            embed.add_field(
                name="📊 `/vote`",
                value="• Upvote ResyncBot to show your support!",
                inline=False
            )
            embed.set_footer(text="Page 4/5 • Use buttons to navigate")
            return embed
        
        def get_contributors_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="🌟 Contributors & Credits",
                description="Special thanks to everyone who helped build ResyncBot!",
                color=discord.Color.gold()
            )
            embed.add_field(
                name="Developer",
                value="Crptk",
                inline=False
            )
            embed.add_field(
                name="Special Recognition",
                value="**Junky** - The inspiration behind automatic resyncing!\n"
                    "Without his encouragement and vision, the core feature of ResyncBot would never have existed.",
                inline=False
            )
            embed.add_field(
                name="Early Database Contributors",
                value="These amazing people helped populate our music database in the early days:\n"
                    "• **Killbok** - Added 1797 tracks\n"
                    "• **Vinnscent** - Added 346 tracks\n"
                    "• **Damie** - Added 281 tracks\n"
                    "• **Leather** - Added 162 tracks\n"
                    "• **Murane** - Added 61 tracks\n"
                    "• **Akraken_** - Added 60 tracks",
                inline=False
            )
            embed.add_field(
                name="💝 Want to Contribute?",
                value="Join our support server and help us grow the music database!\n"
                    "Every contribution makes ResyncBot better for everyone.",
                inline=False
            )
            
            embed.set_footer(text="Page 5/5 • Use buttons to navigate")
            return embed

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            """Only allow the original user to use the buttons"""
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This info panel belongs to someone else!", ephemeral=True)
                return False
            return True
        
        @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.gray, disabled=True)
        async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page = max(0, self.current_page - 1)
            
            # Update button states
            self.previous_button.disabled = (self.current_page == 0)
            self.next_button.disabled = (self.current_page == self.max_pages)
            
            embed = self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.gray)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page = min(self.max_pages, self.current_page + 1)
            
            # Update button states  
            self.previous_button.disabled = (self.current_page == 0)
            self.next_button.disabled = (self.current_page == self.max_pages)
            
            embed = self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        async def on_timeout(self):
            """Disable all buttons when view times out"""
            for child in self.children:
                child.disabled = True
    
    @bot.tree.command(
        name="info",
        description="Learn about ResyncBot's commands and features"
    )
    async def info(interaction: discord.Interaction):
        """Show paginated information about bot commands"""
        
        view = InfoView(interaction.user.id)
        embed = view.get_embed()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

def setup_guide(bot: commands.Bot):
    """
    Registers the /guide slash command for detailed resync techniques.
    """
    
    class GuideView(discord.ui.View):
        def __init__(self, user_id: int):
            super().__init__(timeout=300)  # 5 minute timeout
            self.user_id = user_id
            self.current_page = 0
            self.max_pages = 2
            
        def get_embed(self) -> discord.Embed:
            """Get the embed for the current page"""
            if self.current_page == 0:
                return self.get_manual_guide_embed()
            elif self.current_page == 1:
                return self.get_auto_guide_embed()
            elif self.current_page == 2:
                return self.get_tips_embed()
        
        def get_manual_guide_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="📐 Manual Resync Mastery",
                description="Learn some techniques for perfect beat-matching!",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="The Beat-Match Method",
                value="**Step 1:** Find when the beat starts in your **video**\n"
                      "**Step 2:** Find the same beat in your **audio**\n"
                      "**Step 3:** Subtract to get the perfect offset!",
                inline=False
            )
            
            embed.add_field(
                name="Example Walkthrough",
                value="• Video beat starts at: `0:12`\n"
                      "• Audio beat starts at: `0:30`\n"
                      "• **Audio offset:** `0:12-0:30` (ResyncBot calculates this as 18 seconds, value doesn't become negative)\n"
                      "• Result: Perfect beat sync!",
                inline=False
            )
            
            embed.add_field(
                name="Supported Formats",
                value="**Time formats:**\n"
                      "• `0:30` - 30 seconds\n"
                      "• `1:15.5` - 1 minute 15.5 seconds\n"
                      "• `12-30.5` - Difference calculation\n"
                      "• `0:12-0:30.2` - Precise decimal timing",
                inline=False
            )
            
            embed.add_field(
                name="Tips",
                value="• **Use decimals** for frame-perfect sync: `0:12.3-0:30.7`\n"
                      "• **video_start parameter** can make timing easier\n"
                      "• **Listen carefully** to identify the exact beat moment\n"
                      "• **Practice makes perfect** - try different reference points!",
                inline=False
            )
            
            embed.set_footer(text="Page 1/3 • Manual resync techniques")
            return embed
        
        def get_auto_guide_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="🤖 Auto Resync Method Guide",
                description="Choose the right sync method for your content!",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="Waveform Matching (Recommended)",
                value="Best for: Capturing atmosphere and tone\n"
                      "How it works: Analyzes audio patterns and intensity\n"
                      "Perfect for: Mellow/calm content where you want the quiet parts of the song to match quiet video moments\n"
                      "Example: Chill edit with soft music - waveform will sync the gentle parts together",
                inline=False
            )
            
            embed.add_field(
                name="Beat Matching",
                value="Best for: Rhythm-focused content\n"
                      "How it works: Analyzes tempo and beat patterns\n"
                      "Perfect for: When you need precise beat-to-beat alignment\n"
                      "Note: Sometimes more accurate, but rarely. Waveforms is better overall but this is good for experimentation!",
                inline=False
            )
            
            embed.add_field(
                name="Both Methods",
                value="Best for: Complex edits or when unsure\n"
                      "How it works: Tries both methods and uses whichever one is more accurate\n"
                      "Perfect for: When you want maximum accuracy\n"
                      "Trade-off: Slower processing but highest success rate",
                inline=False
            )
            
            embed.add_field(
                name="Atmosphere vs Precision",
                value="• Waveform: Matches the *feel* and *mood* of your content\n"
                      "• Beat: Matches the *rhythm* and *timing* precisely\n"
                      "• Both: Balances mood and timing for optimal results",
                inline=False
            )
            
            embed.set_footer(text="Page 2/3 • Auto sync method selection")
            return embed
        
        def get_tips_embed(self) -> discord.Embed:
            embed = discord.Embed(
                title="Tips & Tricks",
                description="Advanced techniques for perfect resyncs!",
                color=discord.Color.gold()
            )
            
            embed.add_field(
                name="When Auto Sync Fails",
                value="• Try **different sync methods** (waveform vs beat)\n"
                      "• Use **manual resync** with the beat-match technique\n"
                      "• Check if your **video has clear audio** for analysis\n"
                      "• Consider using **video_start** to focus on the main content",
                inline=False
            )
                        
            embed.add_field(
                name="Edits that work best with Auto Sync",
                value="**Edits with clear beats/audios** work best since the bot can easily identify timing patterns.\n"
                      "Most scale edits are a good example of this, they tend to have clear and easily identifiable beat patterns or waveform spikes,"
                      " whereas edits with difficult audio such as rock or heavy metal tend to be slightly inaccurate.",
                inline=False
            )

            embed.add_field(
                name="Audio Selection Tips",
                value="• **Higher quality** audio = better sync accuracy\n"
                      "• **Consistent tempo** songs work best for auto-sync\n"
                      "• **Clear percussion** helps with beat matching\n"
                      "• **Avoid heavily compressed** or distorted audio",
                inline=False
            )
            
            embed.add_field(
                name="Quick Workflow",
                value="1. **Try auto-sync first** (fastest)\n"
                      "2. **If unsatisfied,** use manual beat-match method\n"
                      "3. **Fine-tune with decimals** for perfection\n"
                      "4. **Experiment with sync methods** for best results",
                inline=False
            )
            
            embed.set_footer(text="Page 3/3 • General tips")
            return embed

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            """Only allow the original user to use the buttons"""
            if interaction.user.id != self.user_id:
                await interaction.response.send_message("This guide belongs to someone else!", ephemeral=True)
                return False
            return True
        
        @discord.ui.button(label="◀️ Previous", style=discord.ButtonStyle.gray, disabled=True)
        async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page = max(0, self.current_page - 1)
            
            # Update button states
            self.previous_button.disabled = (self.current_page == 0)
            self.next_button.disabled = (self.current_page == self.max_pages)
            
            embed = self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        @discord.ui.button(label="Next ▶️", style=discord.ButtonStyle.gray)
        async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.current_page = min(self.max_pages, self.current_page + 1)
            
            # Update button states  
            self.previous_button.disabled = (self.current_page == 0)
            self.next_button.disabled = (self.current_page == self.max_pages)
            
            embed = self.get_embed()
            await interaction.response.edit_message(embed=embed, view=self)
        
        async def on_timeout(self):
            """Disable all buttons when view times out"""
            for child in self.children:
                child.disabled = True
    
    @bot.tree.command(
        name="guide",
        description="Advanced resync techniques and tips"
    )
    async def guide(interaction: discord.Interaction):
        """Show detailed guide for resync techniques"""
        
        view = GuideView(interaction.user.id)
        embed = view.get_embed()
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)