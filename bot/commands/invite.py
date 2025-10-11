import discord
from discord.ext import commands

def setup_invite(bot: commands.Bot):
    @bot.tree.command(name="invite", description="Invite ResyncBot to your server!")
    async def invite(interaction: discord.Interaction):
        bot_id = bot.user.id
        invite_url = f"https://discord.com/oauth2/authorize?client_id={bot_id}&permissions=2147600384&scope=bot+applications.commands"
        support_server_url = "https://discord.gg/b2WcVxrN"  # Replace with your actual Discord server invite

        embed = discord.Embed(
            title="Invite ResyncBot",
            description=(
                f"Click [here]({invite_url}) to invite ResyncBot to your server.\n\n"
                f"You can also join the [official support server]({support_server_url}) "
                f"to report bugs or receive updates about the bot!"
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Thank you for using ResyncBot!")

        await interaction.response.send_message(embed=embed, ephemeral=True)
