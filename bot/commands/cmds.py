import discord
from discord.ext import commands

def setup_cmds(bot: commands.Bot):
    @bot.tree.command(name="cmds", description="List available commands.")
    async def list_commands(interaction: discord.Interaction):
        """
        Sends an embed listing available slash commands, customized for admins or regular users.

        Args:
            interaction: The Discord interaction context.

        Behavior:
            - If the user is an admin, shows both admin and user commands.
            - If not, only shows user-accessible commands.
            - Response is ephemeral (only visible to the user).
        """
        # Commands accessible to everyone
        user_commands_info = [
            ("`/resyncmp4 [video] [SoundCloud link] [audio_start_input?] [video_start_input?] [video_end_input?]`",
            "Start the new audio from a start time"),
            ("`/resyncmp3 [video] [mp3 upload] [audio_start_input?] [video_start_input?] [video_end_input?]`",
            "Upload a video and .mp3 file, set audio start time"),
            ("`/resyncmedia [Video link] [SoundCloud link] [audio_start_input?] [video_start_input?] [video_end_input?]`",
            "Replace a video’s links audio directly from a specific second\n"
            "(KNOWN SUPPORTED: Youtube, Streamable, Discord Attachments)")
        ]

        # Create and customize the embed
        embed = discord.Embed(title="🛠️ Bot Commands",
                            color=discord.Color.blurple())
        for name, desc in user_commands_info:
            embed.add_field(name=name, value=desc, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)