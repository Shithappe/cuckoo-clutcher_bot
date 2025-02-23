import os
from telegram.error import Forbidden
from datetime import time
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from pymongo import MongoClient, ASCENDING
from pymongo.errors import DuplicateKeyError
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

TOKEN = os.getenv('TELEGRAM_TOKEN')
MONGO_URI = os.getenv('MONGO_URI')
DB_NAME = os.getenv('DB_NAME', 'mood_tracker')

class Database:
    def __init__(self, mongo_uri, db_name):
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name]
        self.users = self.db.users
        self.moods = self.db.moods

    def init_db(self):
        try:
            # Создаем индекс для коллекции пользователей
            self.users.create_index("user_id", unique=True)
            # Создаем индексы для коллекции настроений
            self.moods.create_index([("user_id", ASCENDING)])
            self.moods.create_index([("timestamp", ASCENDING)])
            print(f"Database {DB_NAME} initialized successfully")
        except Exception as e:
            print(f"Error initializing database: {e}")
            raise

    def save_mood(self, user_id: int, mood: str) -> bool:
        try:
            result = self.moods.insert_one({
                "user_id": user_id,
                "mood": mood,
                "timestamp": datetime.now()
            })
            return bool(result.inserted_id)
        except Exception as e:
            print(f"Error saving mood: {e}")
            return False

    def get_user(self, user_id: int):
        return self.users.find_one({"user_id": user_id})

    def create_user(self, user_id: int, username: str):
        try:
            self.users.insert_one({
                "user_id": user_id,
                "username": username,
                "created_at": datetime.now()
            })
            return True
        except DuplicateKeyError:
            return True
        except Exception as e:
            print(f"Error creating user: {e}")
            return False

    def get_weekly_stats(self, user_id: int):
        week_ago = datetime.now() - timedelta(days=7)
        stats = {"good": 0, "neutral": 0, "bad": 0}
        cursor = self.moods.find({
            "user_id": user_id,
            "timestamp": {"$gte": week_ago}
        })
        for record in cursor:
            stats[record["mood"]] += 1
        return stats

# Инициализация базы данных
db = Database(MONGO_URI, DB_NAME)
db.init_db()

def get_main_keyboard():
    keyboard = [[KeyboardButton("Добавить настроение 📝")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_mood_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("Хорошо 😊", callback_data="good"),
            InlineKeyboardButton("Никак 😐", callback_data="neutral"),
            InlineKeyboardButton("Плохо 😞", callback_data="bad")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        success = db.create_user(user_id, update.effective_user.username or "")
        if not success:
            await update.message.reply_text("Произошла ошибка при регистрации. Попробуйте позже.")
            return
    
    # Исправленный текст с упоминанием 3 раз
    welcome_text = (
        "Привет! Я бот для отслеживания настроения. Буду спрашивать о твоем состоянии три раза в день. "
        "Также ты можешь сам добавить своё настроение, нажав на кнопку внизу.\n\n"
        "Доступные команды:\n"
        "/stats - Посмотреть статистику за неделю"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats_data = db.get_weekly_stats(user_id)
    stats_text = (
        "📊 Твоя статистика за неделю:\n\n"
        f"😊 Хорошо: {stats_data['good']}\n"
        f"😐 Никак: {stats_data['neutral']}\n"
        f"😞 Плохо: {stats_data['bad']}"
    )
    await update.message.reply_text(stats_text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Добавить настроение 📝":
        await update.message.reply_text("Как твое настроение сейчас?", reply_markup=get_mood_keyboard())

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    mood = query.data
    success = db.save_mood(user_id, mood)
    if success:
        mood_emojis = {"good": "😊", "neutral": "😐", "bad": "😞"}
        mood_text = {"good": "хорошее", "neutral": "нейтральное", "bad": "плохое"}
        await query.edit_message_text(
            f"Записал твое {mood_text[mood]} настроение {mood_emojis[mood]}\nМожешь добавить еще одну запись когда захочешь!"
        )
    else:
        await query.edit_message_text("Произошла ошибка при сохранении. Попробуй еще раз.")

async def send_mood_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминание о настроении всем пользователям"""
    users = db.users.find()
    for user in users:
        user_id = user['user_id']
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="Как твое настроение сейчас?",
                reply_markup=get_mood_keyboard()
            )
        except Forbidden:
            print(f"Пользователь {user_id} заблокировал бота.")
        except Exception as e:
            print(f"Ошибка при отправке сообщения пользователю {user_id}: {e}")

def setup_job_queue(app: Application):
    """Настраивает ежедневные опросы настроения"""
    times = [
        time(hour=9, minute=0),   # 09:00 UTC
        time(hour=14, minute=0),  # 14:00 UTC
        time(hour=20, minute=0)   # 20:00 UTC
    ]
    
    for t in times:
        app.job_queue.run_daily(
            send_mood_reminder,
            time=t,
            name=f"daily_mood_check_{t.hour}_{t.minute}"
        )



def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button))
    
    # Настройка заданий
    setup_job_queue(app)
    
    print("Bot started!")
    app.run_polling()

if __name__ == '__main__':
    main()
