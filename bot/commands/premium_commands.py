import discord
from discord import app_commands
from discord.ext import commands
from backend.premium_utils import premium_manager
from backend.stripe_handler import stripe_handler
import logging
import stripe
from config import Config
import psycopg2

logger = logging.getLogger(__name__)

def setup_premium_commands(bot: commands.Bot):
    """
    Registers premium-related slash commands to the Discord bot.
    """
    
    @bot.tree.command(
        name="premium",
        description="Check your premium status and see subscription options"
    )
    async def premium_status(interaction: discord.Interaction):
        """Show user's premium status and subscription options"""
        try:
            user_id = interaction.user.id
            username = str(interaction.user)
            
            # Get current premium status
            is_premium = premium_manager.is_premium_user(user_id)
            usage_stats = premium_manager.get_user_usage_stats(user_id)
            
            # Create embed
            if is_premium:
                embed = discord.Embed(
                    title="💎 Premium Status",
                    description="You have **ResyncBot Premium**! 🎉",
                    color=discord.Color.gold()
                )
                
                # Get subscription details from database
                try:
                    with psycopg2.connect(Config.DATABASE_URL) as conn:
                        with conn.cursor() as cursor:
                            cursor.execute("""
                                SELECT premium_expires_at, subscription_type 
                                FROM user_subscriptions 
                                WHERE user_id = %s
                            """, (user_id,))
                            result = cursor.fetchone()
                    
                    if result:
                        expires_at, sub_type = result
                        if expires_at is None:
                            embed.add_field(
                                name="🔄 Subscription",
                                value="**Lifetime Premium** ✨\nNever expires!",
                                inline=False
                            )
                        elif sub_type == 'yearly':
                            embed.add_field(
                                name="🔄 Subscription",
                                value=f"**Yearly Premium** 🏆\nRenews: <t:{int(expires_at.timestamp())}:F>",
                                inline=False
                            )
                        else:  # monthly
                            embed.add_field(
                                name="🔄 Subscription",
                                value=f"**Monthly Premium** 💎\nRenews: <t:{int(expires_at.timestamp())}:F>",
                                inline=False
                            )
                except Exception as e:
                    logger.error(f"Error fetching subscription details: {e}")
                
                embed.add_field(
                    name="✅ Premium Benefits",
                    value="• **Unlimited** auto resyncs and priority queues\n• **Unlimited** random resyncs\n• **Priority** processing queue",
                    inline=False
                )
                
                embed.add_field(
                    name="⚙️ Manage Subscription",
                    value="Use `/manage` to cancel, update payment method, or view billing history",
                    inline=False
                )
                
            else:
                embed = discord.Embed(
                    title="📊 Free Tier Status",
                    description="You're currently using the free tier",
                    color=discord.Color.blue()
                )
                
                embed.add_field(
                    name="📈 Today's Usage",
                    value=f"**Auto Resyncs:** {usage_stats['auto_resync']}/{Config.AUTO_LIMITS}\n**Random Resyncs:** {usage_stats['random_resync']}/{Config.RANDOM_LIMITS}",
                    inline=False
                )
                
                embed.add_field(
                    name="💎 Upgrade to Premium",
                    value="Get **unlimited** resyncs!\nUse `/subscribe` to see pricing options.",
                    inline=False
                )
            
            embed.set_footer(text=f"User ID: {user_id}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in premium status command: {e}")
            await interaction.response.send_message(
                "❌ Error checking premium status. Please try again later.",
                ephemeral=True
            )
    
    @bot.tree.command(
        name="subscribe",
        description="Subscribe to ResyncBot Premium for unlimited resyncs"
    )
    async def subscribe(interaction: discord.Interaction):
        """Generate subscription payment link with smart upgrade handling"""
        try:
            user_id = interaction.user.id
            username = str(interaction.user)
            
            await interaction.response.defer(ephemeral=True, thinking=True)
            
            # Check current subscription status
            with psycopg2.connect(Config.DATABASE_URL) as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT is_premium, subscription_type, premium_expires_at, stripe_customer_id
                        FROM user_subscriptions WHERE user_id = %s
                    """, (user_id,))
                    result = cursor.fetchone()

            # Handle different subscription states
            if result:
                is_premium, sub_type, expires_at, customer_id = result
                
                if is_premium and sub_type == 'lifetime':
                    # Already have lifetime - can't upgrade further
                    embed = discord.Embed(
                        title="✨ Already Lifetime Premium!",
                        description="You already have **ResyncBot Premium Lifetime** active!\n\nYou have unlimited access to all premium features forever. 🎉",
                        color=discord.Color.gold()
                    )
                    embed.add_field(
                        name="✅ Your Benefits",
                        value="• **Unlimited** auto resyncs and priority queues\n• **Unlimited** random resyncs\n• **Priority** processing queue\n• **Never expires** ✨",
                        inline=False
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return
                
                elif is_premium and sub_type == 'monthly':
                    # Has monthly - offer upgrade to yearly or lifetime
                    yearly_savings = (Config.MONTHLY_PREMIUM_PRICE * 12) - Config.YEARLY_PREMIUM_PRICE
                    
                    embed = discord.Embed(
                        title="💎 Upgrade Your Premium Plan",
                        description="You currently have **ResyncBot Premium Monthly**.\n\n**Choose your upgrade:**",
                        color=discord.Color.gold()
                    )
                    
                    embed.add_field(
                        name="🏆 Upgrade to Yearly",
                        value=f"• **Save ${yearly_savings}** vs monthly billing\n• **Same great** unlimited features\n• **Cancel anytime**\n• **${Config.YEARLY_PREMIUM_PRICE}/year** (vs ${Config.MONTHLY_PREMIUM_PRICE * 12}/year monthly)",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="✨ Upgrade to Lifetime", 
                        value=f"• **Pay once**, premium forever\n• **No more** charges ever\n• **Ultimate value** for power users\n• **${Config.LIFETIME_PREMIUM_PRICE} one-time**",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="💳 Current Plan",
                        value=f"**Monthly:** ${Config.MONTHLY_PREMIUM_PRICE}/month (you have this)",
                        inline=False
                    )
                    
                    # Create view with both upgrade options
                    view = MonthlyUpgradeView(user_id, username)
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                    return
            
                elif is_premium and sub_type == 'yearly':
                    # Has yearly - offer upgrade to lifetime only
                    embed = discord.Embed(
                        title="💎 Upgrade to Lifetime Premium?",
                        description="You currently have **ResyncBot Premium Yearly**.\n\n**Ready to upgrade to Lifetime?**",
                        color=discord.Color.gold()
                    )
                    
                    embed.add_field(
                        name="💰 Lifetime Benefits",
                        value="• **Pay once**, premium forever\n• **No more** yearly charges\n• **Same great** unlimited features and priority queues\n• **Save money** long-term",
                        inline=False
                    )
                    
                    embed.add_field(
                        name="💳 Pricing",
                        value=f"**Yearly:** ${Config.YEARLY_PREMIUM_PRICE}/year (you have this)\n**Lifetime:** ${Config.LIFETIME_PREMIUM_PRICE} one-time payment",
                        inline=False
                    )
                    
                    # Create view with upgrade option
                    view = YearlyUpgradeView(user_id, username)
                    await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                    return
            # No active premium subscription - show normal options
            embed = discord.Embed(
                title="💎 Choose Your Premium Plan",
                description="Get unlimited resyncs!",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name=f"💎 Monthly Premium - ${Config.MONTHLY_PREMIUM_PRICE}/month",
                value="• **Unlimited** auto & random resyncs\n• **Priority Queue** if things get busy (jump in line!)\n• **No watermark**\n• **Cancel anytime**\n• Perfect for trying premium",
                inline=False
            )

            yearly_savings = (Config.MONTHLY_PREMIUM_PRICE * 12) - Config.YEARLY_PREMIUM_PRICE
            embed.add_field(
                name=f"🏆 Yearly Premium - ${Config.YEARLY_PREMIUM_PRICE}/year",
                value=f"• **All monthly benefits**\n• **Save ${yearly_savings}** vs monthly billing\n• **Best value** for regular users\n• **Cancel anytime**",
                inline=False
            )

            embed.add_field(
                name=f"✨ Lifetime (LIMITED) Premium - ${Config.LIFETIME_PREMIUM_PRICE} one-time",
                value="• **All premium benefits**\n• **Pay once**, premium forever\n• **Never expires**\n• **Ultimate value** for frequent users",
                inline=False
            )
                        
            embed.add_field(
                name="🔒 Secure Payment",
                value="All payments processed securely through Stripe",
                inline=False
            )
            
            # Create view with both options
            view = SubscriptionView(user_id, username)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in subscribe command: {e}")
            try:
                await interaction.followup.send(
                    "❌ Error loading subscription options. Please try again later.",
                    ephemeral=True
                )
            except:
                pass

class SubscriptionView(discord.ui.View):
    """View for new subscribers to choose between monthly and lifetime"""
    
    def __init__(self, user_id: int, username: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.username = username
    
    @discord.ui.button(
        label=f"💎 Monthly - ${Config.MONTHLY_PREMIUM_PRICE}/month",
        style=discord.ButtonStyle.primary,
        emoji="💎"
    )
    async def monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create_payment_session(interaction, "monthly")
    
    @discord.ui.button(
        label=f"🏆 Yearly - ${Config.YEARLY_PREMIUM_PRICE}/year",
        style=discord.ButtonStyle.secondary,
        emoji="🏆"
    )
    async def yearly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create_payment_session(interaction, "yearly")

    @discord.ui.button(
        label=f"✨ Lifetime - ${Config.LIFETIME_PREMIUM_PRICE} one-time",
        style=discord.ButtonStyle.success,
        emoji="✨"
    )
    async def lifetime_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._create_payment_session(interaction, "lifetime")
    
    @discord.ui.button(
        label="❓ Need Help?",
        style=discord.ButtonStyle.gray,
        emoji="❓"
    )
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._show_help(interaction)
    
    async def _create_payment_session(self, interaction: discord.Interaction, plan_type: str):
        """Create payment session for the selected plan"""
        try:
            await interaction.response.defer(ephemeral=True)
            
            session = stripe_handler.create_payment_session(
                discord_user_id=self.user_id,
                product_type=plan_type,
                username=self.username
            )
            
            if not session:
                await interaction.followup.send(
                    "❌ Error creating payment session. Please try again later.",
                    ephemeral=True
                )
                return
            
            # Create embed with payment link
            if plan_type == "monthly":
                embed = discord.Embed(
                    title=f"💎 Monthly Premium - ${Config.MONTHLY_PREMIUM_PRICE}/month",
                    description="You've selected Monthly Premium!",
                    color=discord.Color.blue()
                )
            elif plan_type == "yearly":
                embed = discord.Embed(
                    title=f"✨ Yearly Premium - ${Config.YEARLY_PREMIUM_PRICE}/year",
                    description="You've selected Yearly Premium!",
                    color=discord.Color.green()
                )
            else:
                embed = discord.Embed(
                    title=f"✨ Lifetime Premium - ${Config.LIFETIME_PREMIUM_PRICE} one-time",
                    description="You've selected Lifetime Premium!",
                    color=discord.Color.gold()
                )
            
            embed.add_field(
                name="🔒 Secure Payment",
                value="Click the button below to complete your purchase securely through Stripe.",
                inline=False
            )
            
            # Create view with payment button
            view = PaymentView(session.url)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error creating payment session: {e}")
            await interaction.followup.send(
                "❌ Error creating payment session. Please try again later.",
                ephemeral=True
            )
    
    async def _show_help(self, interaction: discord.Interaction):
        """Show help information"""
        help_embed = discord.Embed(
            title="💳 Subscription Help",
            description="Everything you need to know about ResyncBot Premium",
            color=discord.Color.blue()
        )
        
        help_embed.add_field(
            name="🔒 Is it safe?",
            value="Yes! All payments are processed securely through Stripe, trusted by millions of companies worldwide.",
            inline=False
        )
        
        help_embed.add_field(
            name="💳 Payment methods",
            value="Most credit and debit cards are accepted (Visa, Mastercard, American Express, etc.)",
            inline=False
        )
        
        help_embed.add_field(
            name="⚡ Activation time",
            value="Premium activates instantly after successful payment!",
            inline=False
        )
        
        help_embed.add_field(
            name="❌ Cancellation",
            value="**Yearly and Monthly:** Cancel anytime, no questions asked\n**Lifetime:** One-time purchase, cannot be refunded",
            inline=False
        )
        
        await interaction.response.send_message(embed=help_embed, ephemeral=True)

class MonthlyUpgradeView(discord.ui.View):
    """View for monthly subscribers to upgrade to lifetime"""
    
    def __init__(self, user_id: int, username: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.username = username
    
    @discord.ui.button(
        label=f"🚀 Upgrade to Lifetime (${Config.LIFETIME_PREMIUM_PRICE})",
        style=discord.ButtonStyle.success,
        emoji="🚀"
    )
    async def upgrade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            
            # Create lifetime checkout session
            session = stripe_handler.create_payment_session(
                discord_user_id=self.user_id,
                product_type="lifetime",
                username=self.username
            )
            
            if not session:
                await interaction.followup.send(
                    "❌ Error creating upgrade session. Please try again later.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="🚀 Upgrade to Lifetime Premium",
                description="You're upgrading from Monthly to Lifetime Premium!",
                color=discord.Color.gold()
            )
            
            embed.add_field(
                name="✨ What happens next?",
                value=f"• Pay ${Config.LIFETIME_PREMIUM_PRICE} one-time\n• **Lifetime premium** activates instantly\n• Your monthly subscription will be cancelled\n• **No more** monthly charges",
                inline=False
            )
            
            embed.add_field(
                name="🔒 Secure Payment",
                value="Click below to complete your upgrade securely through Stripe.",
                inline=False
            )
            
            view = PaymentView(session.url)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error creating upgrade session: {e}")
            await interaction.followup.send(
                "❌ Error creating upgrade session. Please try again later.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label=f"💎 Keep Monthly (${Config.MONTHLY_PREMIUM_PRICE}/month)",
        style=discord.ButtonStyle.secondary,
        emoji="💎"
    )
    async def keep_monthly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="💎 Keeping Monthly Premium",
            description="No problem! You'll continue with your Monthly Premium subscription.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="✅ Your current benefits",
            value="• **Unlimited** auto & random resyncs\n• **Priority** processing queue\n• **Cancel anytime**",
            inline=False
        )
        
        embed.add_field(
            name="💡 Upgrade anytime",
            value="You can upgrade to Lifetime Premium anytime by using `/subscribe` again!",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class YearlyUpgradeView(discord.ui.View):
    """View for yearly subscribers to upgrade to lifetime"""
    
    def __init__(self, user_id: int, username: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.username = username
    
    @discord.ui.button(
        label=f"🚀 Upgrade to Lifetime (${Config.LIFETIME_PREMIUM_PRICE})",
        style=discord.ButtonStyle.success,
        emoji="🚀"
    )
    async def upgrade_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=True)
            
            session = stripe_handler.create_payment_session(
                discord_user_id=self.user_id,
                product_type="lifetime",
                username=self.username
            )
            
            if not session:
                await interaction.followup.send(
                    "❌ Error creating upgrade session. Please try again later.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="🚀 Upgrade to Lifetime Premium",
                description="You're upgrading from Yearly to Lifetime Premium!",
                color=discord.Color.gold()
            )
            
            embed.add_field(
                name="✨ What happens next?",
                value=f"• Pay ${Config.LIFETIME_PREMIUM_PRICE} one-time\n• **Lifetime premium** activates instantly\n• Your yearly subscription will be cancelled\n• **No more** yearly charges",
                inline=False
            )
            
            view = PaymentView(session.url)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error creating upgrade session: {e}")
            await interaction.followup.send(
                "❌ Error creating upgrade session. Please try again later.",
                ephemeral=True
            )
    
    @discord.ui.button(
        label=f"🏆 Keep Yearly (${Config.YEARLY_PREMIUM_PRICE}/year)",
        style=discord.ButtonStyle.secondary,
        emoji="🏆"
    )
    async def keep_yearly_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="🏆 Keeping Yearly Premium",
            description="No problem! You'll continue with your Yearly Premium subscription.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="✅ Your current benefits",
            value=f"• **Unlimited** auto & random resyncs\n• **Priority** processing queue\n• **Great value** at ${Config.YEARLY_PREMIUM_PRICE}/year\n• **Cancel anytime**",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class PaymentView(discord.ui.View):
    """View with payment button"""
    
    def __init__(self, payment_url: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.payment_url = payment_url
        
        # Add payment button
        button = discord.ui.Button(
            label="💳 Pay Securely with Stripe",
            style=discord.ButtonStyle.link,
            url=payment_url
        )
        self.add_item(button)
    
    @discord.ui.button(
        label="❓ Need Help?",
        style=discord.ButtonStyle.gray,
        emoji="❓"
    )
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Help button for payment issues"""
        help_embed = discord.Embed(
            title="💳 Payment Help",
            description="Having trouble with your subscription?",
            color=discord.Color.blue()
        )
        
        help_embed.add_field(
            name="🔒 Is it safe?",
            value="Yes! All payments are processed securely through Stripe, the same service used by millions of companies worldwide.",
            inline=False
        )
        
        help_embed.add_field(
            name="💳 What payment methods?",
            value="Most credit and debit cards are accepted (Visa, Mastercard, American Express, etc.)",
            inline=False
        )
        
        help_embed.add_field(
            name="🔄 How long until activation?",
            value="Premium activates instantly after successful payment!",
            inline=False
        )
        
        help_embed.add_field(
            name="❌ Can I cancel?",
            value="Monthly subscriptions can be cancelled anytime. Lifetime purchases are final.",
            inline=False
        )
        
        help_embed.add_field(
            name="📧 Still need help?",
            value="Contact support through our Discord server or DM the bot developer.",
            inline=False
        )
        
        await interaction.response.send_message(embed=help_embed, ephemeral=True)

def setup_limits_command(bot: commands.Bot):
    """Enhanced /limits command with premium info"""
    
    @bot.tree.command(
        name="limits",
        description="Check your current usage limits and premium status"
    )
    async def limits(interaction: discord.Interaction):
        """Show detailed usage and limits"""
        try:
            user_id = interaction.user.id
            is_premium = premium_manager.is_premium_user(user_id)
            usage_stats = premium_manager.get_user_usage_stats(user_id)
            
            if is_premium:
                embed = discord.Embed(
                    title="💎 Premium Limits",
                    description="You have unlimited access to all commands!",
                    color=discord.Color.gold()
                )
                
                embed.add_field(
                    name="🎵 Random Resyncs",
                    value=f"**Today:** {usage_stats['random_resync']}\n**Limit:** Unlimited ∞",
                    inline=True
                )
                
                embed.add_field(
                    name="🤖 Auto Resyncs", 
                    value=f"**Today:** {usage_stats['auto_resync']}\n**Limit:** Unlimited ∞",
                    inline=True
                )
                
                embed.add_field(
                    name="⚙️ Manual Resyncs",
                    value="**Limit:** Unlimited ∞",
                    inline=True
                )
                
            else:
                embed = discord.Embed(
                    title="📊 Free Tier Limits",
                    description="Your current usage and daily limits",
                    color=discord.Color.blue()
                )
                
                # Calculate remaining
                random_remaining = max(0, Config.RANDOM_LIMITS - usage_stats['random_resync'])
                auto_remaining = max(0, Config.AUTO_LIMITS - usage_stats['auto_resync'])
                
                embed.add_field(
                    name="🎵 Random Resyncs",
                    value=f"**Used:** {usage_stats['random_resync']}/{Config.RANDOM_LIMITS}\n**Remaining:** {random_remaining}",
                    inline=True
                )
                
                embed.add_field(
                    name="🤖 Auto Resyncs",
                    value=f"**Used:** {usage_stats['auto_resync']}/{Config.AUTO_LIMITS}\n**Remaining:** {auto_remaining}",
                    inline=True
                )
                
                embed.add_field(
                    name="⚙️ Manual Resyncs",
                    value="**Limit:** Unlimited ∞",
                    inline=True
                )
                
                if random_remaining == 0 or auto_remaining == 0:
                    embed.add_field(
                        name="💎 Upgrade to Premium",
                        value="Get unlimited resyncs and no watermark! Use `/subscribe` to upgrade.",
                        inline=False
                    )
            
            embed.add_field(
                name="🔄 Reset Time",
                value="Limits reset daily at midnight UTC",
                inline=False
            )
            
            embed.set_footer(text=f"User ID: {user_id}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error in limits command: {e}")
            await interaction.response.send_message(
                "❌ Error checking limits. Please try again later.",
                ephemeral=True
            )

    @bot.tree.command(name="manage", description="Manage your subscription")
    async def manage_subscription(interaction: discord.Interaction):
        user_id = interaction.user.id
        
        # Get their Stripe customer ID from your database
        with psycopg2.connect(Config.DATABASE_URL) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT stripe_customer_id FROM user_subscriptions WHERE user_id = %s", (user_id,))
                result = cursor.fetchone()
        
        if not result or not result[0]:
            await interaction.response.send_message("❌ No subscription found!", ephemeral=True)
            return
            
        customer_id = result[0]
        
        # Create portal session
        portal_session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url='https://discord.com/channels/@me'
        )
        
        await interaction.response.send_message(
            f"🔗 [Manage your subscription]({portal_session.url})", 
            ephemeral=True
        )