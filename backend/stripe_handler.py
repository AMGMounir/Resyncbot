'''
For stripe payments, you don't need to do anything with this file.
'''
from pathlib import Path
import sys
from dotenv import load_dotenv
load_dotenv()

# Get the absolute path of this file's directory (backend/)
BACKEND_DIR = Path(__file__).parent.absolute()
# Get the project root directory (parent of backend/)
PROJECT_ROOT = BACKEND_DIR.parent
# Get the backend directory path
BACKEND_PATH = PROJECT_ROOT / "backend"

# Add both project root and backend to Python path
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_PATH))

import stripe
import json
import psycopg2
from datetime import datetime, timezone
from flask import Flask, request, jsonify
from config import Config
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configure Stripe
stripe.api_key = Config.STRIPE_SECRET_API_KEY

class StripeHandler:
    def __init__(self):
        self.webhook_secret = Config.STRIPE_WEBHOOK_SECRET
        
    def get_db_connection(self):
        """Get database connection"""
        return psycopg2.connect(Config.DATABASE_URL)
    
    def notify_bot_premium_change(self, discord_user_id: int, is_premium: bool):
        """Notify the bot that a user's premium status has changed"""
        try:
            # Set a flag in the database that the bot can check
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Insert or update a cache refresh flag
            cursor.execute("""
                INSERT INTO premium_cache_refresh (user_id, needs_refresh, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET 
                    needs_refresh = EXCLUDED.needs_refresh,
                    updated_at = EXCLUDED.updated_at
            """, (discord_user_id, True, datetime.now(timezone.utc)))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Set cache refresh flag for Discord user {discord_user_id}")
            
        except Exception as e:
            logger.error(f"Error setting cache refresh flag: {e}")

    def create_stripe_customer(self, discord_user_id: int, email: str = None, username: str = None):
        """Create a Stripe customer for a Discord user"""
        try:
            customer_data = {
                'metadata': {
                    'discord_user_id': str(discord_user_id),
                    'discord_username': username or f"User{discord_user_id}"
                }
            }
            
            if email:
                customer_data['email'] = email
                
            customer = stripe.Customer.create(**customer_data)
            
            # Store the Stripe customer ID in database
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO user_subscriptions (user_id, stripe_customer_id, is_premium, premium_expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET stripe_customer_id = EXCLUDED.stripe_customer_id
            """, (discord_user_id, customer.id, False, None))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Created Stripe customer {customer.id} for Discord user {discord_user_id}")
            return customer
            
        except Exception as e:
            logger.error(f"Error creating Stripe customer: {e}")
            return None
    
    def get_or_create_customer(self, discord_user_id: int, username: str = None):
        """Get existing Stripe customer or create new one"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Check if customer already exists
            cursor.execute("""
                SELECT stripe_customer_id FROM user_subscriptions 
                WHERE user_id = %s AND stripe_customer_id IS NOT NULL
            """, (discord_user_id,))
            
            result = cursor.fetchone()
            cursor.close()
            conn.close()
            
            if result and result[0]:
                # Verify customer still exists in Stripe
                try:
                    customer = stripe.Customer.retrieve(result[0])
                    return customer
                except stripe.error.InvalidRequestError:
                    # Customer was deleted in Stripe, create new one
                    pass
            
            # Create new customer
            return self.create_stripe_customer(discord_user_id, username=username)
            
        except Exception as e:
            logger.error(f"Error getting/creating customer: {e}")
            return self.create_stripe_customer(discord_user_id, username=username)
    
    def create_payment_session(self, discord_user_id: int, product_type: str, username: str = None):
        """Create a Stripe Checkout session for payment"""
        try:
            customer = self.get_or_create_customer(discord_user_id, username)
            if not customer:
                return None
            
            # Define your product price IDs (you'll get these after creating products)
            # For now, we'll create prices on the fly
            if product_type == "monthly":
                session_data = {
                    'customer': customer.id,
                    'payment_method_types': ['card'],
                    'line_items': [{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': 'ResyncBot Premium Monthly',
                                'description': 'Unlimited auto & random resyncs for ResyncBot'
                            },
                            'unit_amount': Config.MONTHLY_PREMIUM_PRICE * 100,  # $3.00 in cents
                            'recurring': {
                                'interval': 'month'
                            }
                        },
                        'quantity': 1,
                    }],
                    'mode': 'subscription',
                    'success_url': 'https://discord.com/channels/@me',
                    'cancel_url': 'https://discord.com/channels/@me',
                    'metadata': {
                        'discord_user_id': str(discord_user_id),
                        'subscription_type': 'monthly'
                    }
                }
            elif product_type == "yearly":
                session_data = {
                    'customer': customer.id,
                    'payment_method_types': ['card'],
                    'line_items': [{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': 'ResyncBot Premium Yearly',
                                'description': 'Unlimited auto & random resyncs for ResyncBot - Best Value!'
                            },
                            'unit_amount': Config.YEARLY_PREMIUM_PRICE * 100,  # $13.00 in cents
                            'recurring': {
                                'interval': 'year'
                            }
                        },
                        'quantity': 1,
                    }],
                    'mode': 'subscription',
                    'success_url': 'https://discord.com/channels/@me',
                    'cancel_url': 'https://discord.com/channels/@me',
                    'metadata': {
                        'discord_user_id': str(discord_user_id),
                        'subscription_type': 'yearly'
                    }
                }
            elif product_type == "lifetime":
                session_data = {
                    'customer': customer.id,
                    'payment_method_types': ['card'],
                    'line_items': [{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {
                                'name': 'ResyncBot Premium Lifetime',
                                'description': 'Lifetime unlimited access to ResyncBot premium features'
                            },
                            'unit_amount': Config.LIFETIME_PREMIUM_PRICE * 100,  # $25.00 in cents
                        },
                        'quantity': 1,
                    }],
                    'mode': 'payment',
                    'success_url': 'https://discord.com/channels/@me',
                    'cancel_url': 'https://discord.com/channels/@me',
                    'metadata': {
                        'discord_user_id': str(discord_user_id),
                        'subscription_type': 'lifetime'
                    }
                }
            else:
                logger.error(f"Invalid product type: {product_type}")
                return None
            
            session = stripe.checkout.Session.create(**session_data)
            return session
            
        except Exception as e:
            logger.error(f"Error creating payment session: {e}")
            return None
    
    def handle_checkout_completed(self, session):
        """Handle successful checkout completion"""
        try:
            print(f"[DEBUG] handle_checkout_completed called with session: {session.get('id')}")
            
            discord_user_id = int(session['metadata']['discord_user_id'])
            session_type = session['metadata'].get('type', 'unknown')
            
            print(f"[DEBUG] Processing {session_type} for user {discord_user_id}")
            
            if session_type == 'donation':
                donation_amount = int(session['metadata'].get('donation_amount', 0))
                
                # Log the donation to database
                conn = self.get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT INTO donations (
                        user_id, amount, stripe_payment_id, donated_at
                    )
                    VALUES (%s, %s, %s, %s)
                """, (discord_user_id, donation_amount, session.get('payment_intent'), datetime.now(timezone.utc)))
                
                conn.commit()
                cursor.close()
                conn.close()
                
                logger.info(f"💝 Donation of ${donation_amount} from Discord user {discord_user_id}")
                print(f"[DEBUG] Donation logged successfully")
            
            else:
                logger.warning(f"Unknown session type: {session_type}")
                
        except Exception as e:
            print(f"[DEBUG] ERROR in handle_checkout_completed: {e}")
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
            logger.error(f"Error handling checkout completion: {e}")
    
    def handle_subscription_created(self, subscription):
        """Handle new subscription creation"""
        try:
            print(f"[DEBUG] ========== SUBSCRIPTION CREATED ==========")
            print(f"[DEBUG] Full subscription object: {json.dumps(subscription, indent=2, default=str)}")

            customer_id = subscription['customer']
            
            # Get Discord user ID from customer
            customer = stripe.Customer.retrieve(customer_id)
            discord_user_id = int(customer.metadata.get('discord_user_id'))
            
            if not discord_user_id:
                logger.error(f"No Discord user ID found for customer {customer_id}")
                return
            
            print(f"[DEBUG] Processing subscription for Discord user: {discord_user_id}")
            
            # Debug period end detection
            period_end = None
            if subscription.get('items') and subscription['items'].get('data'):
                first_item = subscription['items']['data'][0]
                period_end = first_item.get('current_period_end')
                print(f"[DEBUG] Period end from items: {period_end}")
            if not period_end:
                period_end = subscription.get('current_period_end')
                print(f"[DEBUG] Period end from subscription: {period_end}")
            if not period_end:
                logger.error(f"No period end found in subscription: {subscription}")
                return
            
            # Calculate expiration date
            current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
            print(f"[DEBUG] Calculated expiration: {current_period_end}")
            
            # Determine subscription type based on interval
            subscription_type = 'monthly'  # default
            if subscription.get('items') and subscription['items'].get('data'):
                first_item = subscription['items']['data'][0]
                print(f"[DEBUG] First item: {json.dumps(first_item, indent=2, default=str)}")
                if first_item.get('price') and first_item['price'].get('recurring'):
                    interval = first_item['price']['recurring'].get('interval')
                    print(f"[DEBUG] Detected interval: {interval}")
                    if interval == 'year':
                        subscription_type = 'yearly'
                    elif interval == 'month':
                        subscription_type = 'monthly'

            print(f"[DEBUG] Final subscription type: {subscription_type}")
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            print(f"[DEBUG] About to execute database insert/update...")
            cursor.execute("""
                INSERT INTO user_subscriptions (
                    user_id, is_premium, premium_expires_at, 
                    stripe_customer_id, stripe_subscription_id, subscription_type
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) 
                DO UPDATE SET 
                    is_premium = EXCLUDED.is_premium,
                    premium_expires_at = EXCLUDED.premium_expires_at,
                    stripe_customer_id = EXCLUDED.stripe_customer_id,
                    stripe_subscription_id = EXCLUDED.stripe_subscription_id,
                    subscription_type = EXCLUDED.subscription_type
            """, (
                discord_user_id, True, current_period_end,
                customer_id, subscription['id'], subscription_type
            ))
            
            print(f"[DEBUG] Database query executed, committing...")
            conn.commit()
            print(f"[DEBUG] Database commit successful!")
            
            cursor.close()
            conn.close()
            
            logger.info(f"Created {subscription_type} subscription for Discord user {discord_user_id}, expires {current_period_end}")
            
            print(f"[DEBUG] About to notify bot of premium change...")
            self.notify_bot_premium_change(discord_user_id, True)
            print(f"[DEBUG] ========== SUBSCRIPTION CREATED COMPLETE ==========")

        except Exception as e:
            print(f"[DEBUG] ========== SUBSCRIPTION CREATED ERROR ==========")
            print(f"[DEBUG] ERROR in handle_subscription_created: {e}")
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
            logger.error(f"Error handling subscription creation: {e}")
    
    def handle_subscription_updated(self, subscription):
        """Handle subscription updates (renewals, etc.)"""
        try:
            customer_id = subscription['customer']
            
            # Get Discord user ID from customer
            customer = stripe.Customer.retrieve(customer_id)
            discord_user_id = int(customer.metadata.get('discord_user_id'))
            
            if not discord_user_id:
                logger.error(f"No Discord user ID found for customer {customer_id}")
                return
            
            # Update expiration date
            current_period_end = datetime.fromtimestamp(
                subscription['current_period_end'], 
                tz=timezone.utc
            )
            
            # Check if subscription is active
            is_active = subscription['status'] in ['active', 'trialing']
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE user_subscriptions 
                SET is_premium = %s, premium_expires_at = %s
                WHERE user_id = %s
            """, (is_active, current_period_end, discord_user_id))
            
            conn.commit()
            cursor.close()
            conn.close()
            
            logger.info(f"Updated subscription for Discord user {discord_user_id}, active: {is_active}, expires: {current_period_end}")
            
            self.notify_bot_premium_change(discord_user_id, is_active)

        except Exception as e:
            logger.error(f"Error handling subscription update: {e}")
    
    def handle_subscription_deleted(self, subscription):
        """Handle subscription cancellation"""
        try:
            customer_id = subscription['customer']
            
            # Get Discord user ID from customer
            customer = stripe.Customer.retrieve(customer_id)
            discord_user_id = int(customer.metadata.get('discord_user_id'))
            
            if not discord_user_id:
                logger.error(f"No Discord user ID found for customer {customer_id}")
                return
            
            conn = self.get_db_connection()
            cursor = conn.cursor()
            
            # Don't immediately revoke access - let it expire naturally
            # Just log the cancellation
            logger.info(f"Subscription cancelled for Discord user {discord_user_id}")
            
            self.notify_bot_premium_change(discord_user_id, False)
            
            cursor.close()
            conn.close()
            
        except Exception as e:
            logger.error(f"Error handling subscription deletion: {e}")
    
    def handle_payment_succeeded(self, invoice):
        """Handle successful payment"""
        try:
            customer_id = invoice['customer']
            customer = stripe.Customer.retrieve(customer_id)
            discord_user_id = int(customer.metadata.get('discord_user_id'))
            
            logger.info(f"Payment succeeded for Discord user {discord_user_id}")
            
        except Exception as e:
            logger.error(f"Error handling payment success: {e}")
    
    def handle_payment_failed(self, invoice):
        """Handle failed payment"""
        try:
            customer_id = invoice['customer']
            customer = stripe.Customer.retrieve(customer_id)
            discord_user_id = int(customer.metadata.get('discord_user_id'))
            
            logger.warning(f"Payment failed for Discord user {discord_user_id}")
            
        except Exception as e:
            logger.error(f"Error handling payment failure: {e}")
    
    def verify_webhook_signature(self, payload, signature):
        if not self.webhook_secret:
            logger.info("[DEBUG] ❌ STRIPE_WEBHOOK_SECRET is not set!")
            return False
        try:
            stripe.Webhook.construct_event(payload, signature, self.webhook_secret)
            logger.info("[DEBUG] ✅ Webhook signature verified")
            return True
        except Exception as e:
            logger.info(f"[DEBUG] ❌ Webhook signature failed: {e}")
            return False

    def create_donation_session(self, discord_user_id: int, amount: int, username: str = None):
        """
        Create a Stripe Checkout session for a one-time donation
        
        Args:
            discord_user_id: Discord user ID
            amount: Donation amount in USD (e.g., 5 for $5.00)
            username: Discord username
        
        Returns:
            Stripe checkout session or None
        """
        try:
            customer = self.get_or_create_customer(discord_user_id, username)
            if not customer:
                return None
            
            session_data = {
                'customer': customer.id,
                'payment_method_types': ['card'],
                'line_items': [{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': f'ResyncBot Donation - ${amount}',
                            'description': 'Thank you for supporting ResyncBot! ❤️'
                        },
                        'unit_amount': amount * 100,  # Convert to cents
                    },
                    'quantity': 1,
                }],
                'mode': 'payment',
                'success_url': 'https://discord.com/channels/@me',
                'cancel_url': 'https://discord.com/channels/@me',
                'metadata': {
                    'discord_user_id': str(discord_user_id),
                    'donation_amount': str(amount),
                    'type': 'donation'
                }
            }
            
            session = stripe.checkout.Session.create(**session_data)
            logger.info(f"Created donation session for user {discord_user_id}, amount: ${amount}")
            return session
            
        except Exception as e:
            logger.error(f"Error creating donation session: {e}")
            return None   

    def handle_webhook(self, payload, signature):
        """Main webhook handler"""
        logger.info(f"[DEBUG] ============ WEBHOOK CALLED ============")
        logger.info(f"[DEBUG] Webhook handler called")
        
        if not self.verify_webhook_signature(payload, signature):
            logger.info(f"[DEBUG] Webhook signature verification failed")
            return False
        
        try:
            event = json.loads(payload)
            event_type = event['type']
            
            logger.info(f"[DEBUG] Processing webhook event: {event_type}")
            logger.info(f"Received Stripe webhook: {event_type}")
            
            if event_type == 'checkout.session.completed':
                logger.info(f"[DEBUG] *** CALLING handle_checkout_completed ***")
                logger.info(f"[DEBUG] Session metadata: {event['data']['object'].get('metadata', {})}")
                self.handle_checkout_completed(event['data']['object'])
                logger.info(f"[DEBUG] *** handle_checkout_completed RETURNED ***")
                
            elif event_type == 'customer.subscription.created':
                logger.info(f"[DEBUG] *** CALLING handle_subscription_created ***")
                self.handle_subscription_created(event['data']['object'])
                logger.info(f"[DEBUG] *** handle_subscription_created RETURNED ***")
            
            elif event_type == 'customer.subscription.updated':
                self.handle_subscription_updated(event['data']['object'])
            
            elif event_type == 'customer.subscription.deleted':
                self.handle_subscription_deleted(event['data']['object'])
            
            elif event_type == 'invoice.payment_succeeded':
                self.handle_payment_succeeded(event['data']['object'])
            
            elif event_type == 'invoice.payment_failed':
                self.handle_payment_failed(event['data']['object'])
            
            else:
                logger.info(f"Unhandled webhook event type: {event_type}")
            
            print(f"[DEBUG] ============ WEBHOOK COMPLETED SUCCESSFULLY ============")
            return True
            
        except Exception as e:
            print(f"[DEBUG] ============ WEBHOOK ERROR ============")
            print(f"[DEBUG] ERROR in webhook handler: {e}")
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
            logger.error(f"Error processing webhook: {e}")
            return False
        
stripe_handler = StripeHandler()