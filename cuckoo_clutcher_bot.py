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

    def save_mood(self, user_id: int, mood_value: int) -> bool:
        try:
            result = self.moods.insert_one({
                "user_id": user_id,
                "mood_value": mood_value,
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
        
        # Получаем все записи за неделю
        cursor = self.moods.find({
            "user_id": user_id,
            "timestamp": {"$gte": week_ago}
        })
        
        # Считаем среднее значение и группируем по категориям
        records = list(cursor)
        stats = {
            "total_entries": len(records),
            "average_mood": 0,
            "mood_distribution": {
                "0-3": 0,  # Плохое настроение
                "4-6": 0,  # Нейтральное настроение
                "7-10": 0  # Хорошее настроение
            }
        }
        
        if records:
            total_mood = 0
            for record in records:
                # Проверяем, какой формат данных используется
                if "mood_value" in record:
                    mood_value = record["mood_value"]
                elif "mood" in record:
                    # Конвертируем старый строковый формат в числовой
                    mood_str = record["mood"]
                    if mood_str == "good":
                        mood_value = 8
                    elif mood_str == "neutral":
                        mood_value = 5
                    elif mood_str == "bad":
                        mood_value = 2
                    else:
                        # Если неизвестный формат, используем нейтральное значение
                        mood_value = 5
                else:
                    # Если нет известных полей, используем нейтральное значение
                    mood_value = 5
                    
                total_mood += mood_value
                
                # Группируем по категориям
                if 0 <= mood_value <= 3:
                    stats["mood_distribution"]["0-3"] += 1
                elif 4 <= mood_value <= 6:
                    stats["mood_distribution"]["4-6"] += 1
                elif 7 <= mood_value <= 10:
                    stats["mood_distribution"]["7-10"] += 1
            
            stats["average_mood"] = round(total_mood / len(records), 1)
        
        return stats

# Инициализация базы данных
db = Database(MONGO_URI, DB_NAME)
db.init_db()

def get_main_keyboard():
    keyboard = [
        [KeyboardButton("Посмотреть статистику 📊")],
        [KeyboardButton("Добавить настроение 📝")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_mood_keyboard():
    # Создаем клавиатуру со шкалой от 0 до 10
    keyboard = []
    
    # Первый ряд: 0-4
    row1 = [InlineKeyboardButton(f"{i}", callback_data=f"mood_{i}") for i in range(5)]
    keyboard.append(row1)
    
    # Второй ряд: 5-10
    row2 = [InlineKeyboardButton(f"{i}", callback_data=f"mood_{i}") for i in range(5, 11)]
    keyboard.append(row2)
    
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        success = db.create_user(user_id, update.effective_user.username or "")
        if not success:
            await update.message.reply_text("Произошла ошибка при регистрации. Попробуйте позже.")
            return
    
    welcome_text = (
        "Привет! Я бот для отслеживания настроения. Буду спрашивать о твоем состоянии три раза в день. "
        "Оцени свое настроение по шкале от 0 (очень плохо) до 10 (отлично).\n\n"
        "Ты можешь сам добавить своё настроение или посмотреть статистику, используя кнопки внизу.\n\n"
        "Доступные команды:\n"
        "/stats - Посмотреть статистику за неделю"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard())


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    stats_data = db.get_weekly_stats(user_id)
    
    if stats_data["total_entries"] == 0:
        await update.message.reply_text("У тебя пока нет записей о настроении за последнюю неделю.")
        return
    
    stats_text = (
        "📊 Твоя статистика за неделю:\n\n"
        f"Всего записей: {stats_data['total_entries']}\n"
        f"Среднее настроение: {stats_data['average_mood']}/10\n\n"
        f"😔 Плохое (0-3): {stats_data['mood_distribution']['0-3']}\n"
        f"😐 Нейтральное (4-6): {stats_data['mood_distribution']['4-6']}\n"
        f"😊 Хорошее (7-10): {stats_data['mood_distribution']['7-10']}"
    )
    await update.message.reply_text(stats_text, reply_markup=get_main_keyboard())

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Добавить настроение 📝":
        await update.message.reply_text(
            "Как твое настроение сейчас? Оцени по шкале от 0 до 10:\n"
            "0 = очень плохо, 10 = отлично", 
            reply_markup=get_mood_keyboard()
        )
    elif update.message.text == "Посмотреть статистику 📊":
        await stats(update, context)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # Получаем числовое значение настроения из callback_data
    callback_data = query.data
    if callback_data.startswith("mood_"):
        mood_value = int(callback_data.split("_")[1])
        success = db.save_mood(user_id, mood_value)
        
        if success:
            # Определяем эмодзи в зависимости от значения настроения
            if 0 <= mood_value <= 3:
                emoji = "😔"
            elif 4 <= mood_value <= 6:
                emoji = "😐"
            else:
                emoji = "😊"
                
            await query.edit_message_text(
                f"Записал твое настроение: {mood_value}/10 {emoji}\n"
                "Можешь добавить еще одну запись когда захочешь!"
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
                text="Как твое настроение сейчас? Оцени по шкале от 0 до 10:",
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