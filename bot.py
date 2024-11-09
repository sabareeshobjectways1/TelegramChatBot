import logging
import asyncio
import nest_asyncio
import streamlit as st
from telegram import Update, ChatMember
from telegram.ext import (filters, ApplicationBuilder, ContextTypes, CommandHandler, ConversationHandler,
                         MessageHandler, ChatMemberHandler)
from UserStatus import UserStatus
from config import BOT_TOKEN, ADMIN_ID
import db_connection
import threading

# Enable nested event loops if not already running
if not asyncio.get_event_loop().is_running():
    nest_asyncio.apply()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.WARNING
)

class TelegramBot:
    def __init__(self):
        self.application = None
        self.is_running = False

    async def start_bot(self):
        self.application = ApplicationBuilder().token(BOT_TOKEN).build()
        db_connection.create_db()
        db_connection.reset_users_status()

        # Set up conversation handler
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                USER_ACTION: [
                    ChatMemberHandler(self.blocked_bot_handler),
                    MessageHandler(
                        (filters.TEXT | filters.ATTACHMENT) & ~filters.COMMAND & ~filters.Regex("exit") & ~filters.Regex("chat")
                        & ~filters.Regex("newchat") & ~filters.Regex("stats"),
                        self.handle_message),
                    CommandHandler("exit", self.handle_exit_chat),
                    CommandHandler("chat", self.handle_chat),
                    CommandHandler("newchat", self.exit_then_chat),
                    CommandHandler("stats", self.handle_stats)]
            },
            fallbacks=[MessageHandler(filters.TEXT, self.handle_not_in_chat)]
        )
        
        self.application.add_handler(conv_handler)
        self.is_running = True
        await self.application.initialize()
        await self.application.start()
        await self.application.run_polling()

    async def stop_bot(self):
        if self.application and self.is_running:
            await self.application.stop()
            self.is_running = False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        await context.bot.send_message(chat_id=update.effective_chat.id,
                                     text="Welcome to this ChatBot! 🤖\nType /chat to start searching for a partner")
        user_id = update.effective_user.id
        db_connection.insert_user(user_id)
        return USER_ACTION

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if db_connection.get_user_status(user_id=user_id) == UserStatus.COUPLED:
            other_user_id = db_connection.get_partner_id(user_id)
            if other_user_id is None:
                return await self.handle_not_in_chat(update, context)
            else:
                return await self.in_chat(update, other_user_id)
        else:
            return await self.handle_not_in_chat(update, context)

    async def handle_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_user_id = update.effective_user.id
        current_user_status = db_connection.get_user_status(user_id=current_user_id)

        if current_user_status == UserStatus.PARTNER_LEFT:
            db_connection.set_user_status(user_id=current_user_id, new_status=UserStatus.IDLE)
            return await self.start_search(update, context)
        elif current_user_status == UserStatus.IN_SEARCH:
            return await self.handle_already_in_search(update, context)
        elif current_user_status == UserStatus.COUPLED:
            other_user = db_connection.get_partner_id(current_user_id)
            if other_user is not None:
                await context.bot.send_message(chat_id=current_user_id,
                                             text="🤖 You are already in a chat, type /exit to exit from the chat.")
                return None
            else:
                return await self.start_search(update, context)
        elif current_user_status == UserStatus.IDLE:
            return await self.start_search(update, context)

    async def handle_not_in_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_user_id = update.effective_user.id
        current_user_status = db_connection.get_user_status(user_id=current_user_id)

        if current_user_status in [UserStatus.IDLE, UserStatus.PARTNER_LEFT]:
            await context.bot.send_message(chat_id=current_user_id,
                                         text="🤖 You are not in a chat, type /chat to start searching for a partner.")
            return
        elif current_user_status == UserStatus.IN_SEARCH:
            await context.bot.send_message(chat_id=current_user_id,
                                         text="🤖 Message not delivered, you are still in search!")
            return

    async def handle_already_in_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="🤖 You are already in search!")
        return

    async def start_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_user_id = update.effective_chat.id
        db_connection.set_user_status(user_id=current_user_id, new_status=UserStatus.IN_SEARCH)
        await context.bot.send_message(chat_id=current_user_id, text="🤖 Searching for a partner...")
        other_user_id = db_connection.couple(current_user_id=current_user_id)
        if other_user_id is not None:
            await context.bot.send_message(chat_id=current_user_id, text="🤖 You have been paired with an user")
            await context.bot.send_message(chat_id=other_user_id, text="🤖 You have been paired with an user")
        return

    async def handle_exit_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self.exit_chat(update, context)
        return

    async def handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        user_id = update.effective_user.id
        if user_id == ADMIN_ID:
            total_users_number, paired_users_number = db_connection.retrieve_users_number()
            await context.bot.send_message(chat_id=user_id, text="Welcome to the admin panel")
            await context.bot.send_message(chat_id=user_id,
                                         text="Number of paired users: " + str(paired_users_number))
            await context.bot.send_message(chat_id=user_id,
                                         text="Number of active users: " + str(total_users_number))
        else:
            logging.warning("User " + str(user_id) + " tried to access the admin panel")
        return

    async def exit_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_user = update.effective_user.id
        if db_connection.get_user_status(user_id=current_user) != UserStatus.COUPLED:
            await context.bot.send_message(chat_id=current_user, text="🤖 You are not in a chat!")
            return
        other_user = db_connection.get_partner_id(current_user)
        if other_user is None:
            return
        db_connection.uncouple(user_id=current_user)
        await context.bot.send_message(chat_id=current_user, text="🤖 Ending chat...")
        await context.bot.send_message(chat_id=other_user,
                                     text="🤖 Your partner has left the chat, type /chat to start searching for a new partner.")
        await update.message.reply_text("🤖 You have left the chat.")
        return

    async def exit_then_chat(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        current_user = update.effective_user.id
        if db_connection.get_user_status(user_id=current_user) == UserStatus.IN_SEARCH:
            return await self.handle_already_in_search(update, context)
        await self.exit_chat(update, context)
        return await self.start_search(update, context)

    async def in_chat(self, update: Update, other_user_id) -> None:
        if update.message.reply_to_message is not None:
            if update.message.reply_to_message.from_user.id == update.effective_user.id:
                await update.effective_chat.copy_message(chat_id=other_user_id, message_id=update.message.message_id,
                                                       protect_content=True,
                                                       reply_to_message_id=update.message.reply_to_message.message_id + 1)
            elif update.message.reply_to_message.has_protected_content is None:
                await update.effective_chat.copy_message(chat_id=other_user_id, message_id=update.message.message_id,
                                                       protect_content=True)
            else:
                await update.effective_chat.copy_message(chat_id=other_user_id, message_id=update.message.message_id,
                                                       protect_content=True,
                                                       reply_to_message_id=update.message.reply_to_message.message_id - 1)
        else:
            await update.effective_chat.copy_message(chat_id=other_user_id, message_id=update.message.message_id,
                                                   protect_content=True)
        return

    async def blocked_bot_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_bot_blocked_by_user(update):
            user_id = update.effective_user.id
            user_status = db_connection.get_user_status(user_id=user_id)
            if user_status == UserStatus.COUPLED:
                other_user = db_connection.get_partner_id(user_id)
                db_connection.uncouple(user_id=user_id)
                await context.bot.send_message(chat_id=user_id, text="You blocked the bot, exiting from the chat!")
                await context.bot.send_message(chat_id=other_user, text="Your partner has blocked the bot, leaving the chat.")
        return
        

# Run the bot in a separate thread to allow Streamlit UI to function
def run_bot():
    bot = TelegramBot()
    asyncio.run(bot.start_bot())

# Launch the bot in a separate thread
threading.Thread(target=run_bot, daemon=True).start()

# Streamlit app content
st.title('Streamlit & Telegram Bot Integration')
st.write("This app demonstrates running a Telegram bot alongside a Streamlit app.")
