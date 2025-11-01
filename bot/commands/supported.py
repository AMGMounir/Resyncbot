import discord
from discord.ext import commands

def setup_supported(bot: commands.Bot):
    @bot.tree.command(name="supported", description="View supported links for ResyncBot.")
    async def supported(interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎧 Supported Links",
            description=(
                "**Video Links:**\n"
                "• Streamable links\n"
                "• Discord-hosted video links\n"
                "• Any direct `.mp4` or video link that doesn’t require verification/login\n\n"
                "**Audio Links:**\n"
                "• SoundCloud links\n"
                "• Direct `.mp3` or `.wav` file links\n\n"
                "*ResyncBot will get an error from links that block third-party access.*"
            ),
            color=discord.Color.blurple()
        )

        embed.set_footer(
            text=(
                "Developer Note: Spotify, YouTube, Instagram, and TikTok are no longer supported.\n"
                "I really didn’t want it to come to this, but I’m tired of fighting with these "
                "platforms just to let ResyncBot download content. You can still use file resync "
                "commands, or copy Discord-hosted video URLs if you don’t have Nitro.\n"
                "Thanks for understanding ❤️"
            )
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)
