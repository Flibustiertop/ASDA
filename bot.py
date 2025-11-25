import os
import sys
import json
import logging
import asyncio
import traceback
from typing import Dict, Optional, List
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.constants import ParseMode, ChatMemberStatus
import aiohttp
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Файл для хранения данных
DATA_FILE = "bot_data.json"

# ID каналов для проверки подписки (загружаются из файла)
CHANNEL_IDS = [-1002209682372, -1002787956505]  # G1dra канал и новый канал

# Ссылки на каналы для подписки (загружаются из файла)
CHANNEL_LINKS = [
    "https://t.me/pro_tweaks",
    "https://t.me/+NAp6PQDiSNJjNDVi"
]

# ID главного администратора (по умолчанию)
MAIN_ADMIN_ID = 8211610309

# Ссылка на файл для загрузки (загружается из файла)
FILE_URL = "https://www.dropbox.com/scl/fi/qsq74prqeunndpcq1fuhg/ProTweaker-Installer-3.0.1.exe?rlkey=6nh4d13xm0xf9bayc3l6z973f&st=20obmgj4&dl=1"

# Хранилище для предыдущих сообщений (user_id -> message_id)
user_messages: Dict[int, Optional[int]] = {}

# Состояния для админ-панели (user_id -> state)
admin_states: Dict[int, Optional[str]] = {}

# Хранилище для логов действий
action_logs: List[Dict] = []


def load_data():
    """Загружает данные из файла"""
    global CHANNEL_IDS, CHANNEL_LINKS, FILE_URL
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                CHANNEL_IDS = data.get('channel_ids', CHANNEL_IDS)
                CHANNEL_LINKS = data.get('channel_links', CHANNEL_LINKS)
                FILE_URL = data.get('file_url', FILE_URL)
                
                # Убеждаемся, что главный админ всегда в списке
                admins = data.get('admins', [])
                if MAIN_ADMIN_ID not in admins:
                    admins.append(MAIN_ADMIN_ID)
                    data['admins'] = admins
                    save_data(data)
                
                return data
    except Exception as e:
        logger.error(f"Ошибка при загрузке данных: {e}")
    
    # Если файла нет, создаем с главным админом
    default_data = {
        'admins': [MAIN_ADMIN_ID], 
        'users': [], 
        'channel_ids': CHANNEL_IDS, 
        'channel_links': CHANNEL_LINKS, 
        'file_url': FILE_URL,
        'banned_users': [],
        'messages': {},
        'images': {},
        'settings': {}
    }
    save_data(default_data)
    return default_data


def save_data(data: dict):
    """Сохраняет данные в файл"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        # Обновляем глобальные переменные
        global CHANNEL_IDS, CHANNEL_LINKS, FILE_URL
        CHANNEL_IDS = data.get('channel_ids', CHANNEL_IDS)
        CHANNEL_LINKS = data.get('channel_links', CHANNEL_LINKS)
        FILE_URL = data.get('file_url', FILE_URL)
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных: {e}")
        return False


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    data = load_data()
    admins = data.get('admins', [])
    return user_id in admins


def add_admin(user_id: int) -> bool:
    """Добавляет администратора"""
    data = load_data()
    admins = data.get('admins', [])
    if user_id not in admins:
        admins.append(user_id)
        data['admins'] = admins
        return save_data(data)
    return False


def remove_admin(user_id: int) -> bool:
    """Удаляет администратора"""
    data = load_data()
    admins = data.get('admins', [])
    if user_id in admins:
        admins.remove(user_id)
        data['admins'] = admins
        return save_data(data)
    return False


def add_user(user_id: int) -> bool:
    """Добавляет пользователя в список (для рассылки)"""
    data = load_data()
    users = data.get('users', [])
    if user_id not in users:
        users.append(user_id)
        data['users'] = users
        return save_data(data)
    return False


def log_action(admin_id: int, action: str):
    """Логирует действие администратора"""
    from datetime import datetime
    log_entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'admin_id': admin_id,
        'action': action
    }
    action_logs.append(log_entry)
    # Ограничиваем количество логов (последние 1000)
    if len(action_logs) > 1000:
        action_logs.pop(0)


def is_banned(user_id: int) -> bool:
    """Проверяет, забанен ли пользователь"""
    data = load_data()
    banned = data.get('banned_users', [])
    return user_id in banned


def ban_user(user_id: int) -> bool:
    """Банит пользователя"""
    data = load_data()
    banned = data.get('banned_users', [])
    if user_id not in banned:
        banned.append(user_id)
        data['banned_users'] = banned
        return save_data(data)
    return False


def unban_user(user_id: int) -> bool:
    """Разбанивает пользователя"""
    data = load_data()
    banned = data.get('banned_users', [])
    if user_id in banned:
        banned.remove(user_id)
        data['banned_users'] = banned
        return save_data(data)
    return False


def get_all_users() -> List[int]:
    """Возвращает список всех пользователей"""
    data = load_data()
    return data.get('users', [])


# Загружаем данные при запуске
load_data()


async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Проверяет, подписан ли пользователь хотя бы на один из необходимых каналов.
    
    Логика простая:
    - Бот (который должен быть администратором канала) проверяет статус пользователя
    - Если статус 'member', 'administrator' или 'creator' - пользователь ПОДПИСАН (возвращает True)
    - Если статус 'left', 'kicked', 'restricted' или любой другой - пользователь НЕ ПОДПИСАН (возвращает False)
    - Если ошибка доступа (бот не админ) - пользователь НЕ ПОДПИСАН (возвращает False)
    """
    try:
        # Список каналов для проверки
        channels_to_check = ['@pro_tweaks'] + CHANNEL_IDS
        
        logger.info(f"🔍 Проверка подписки пользователя {user_id} на каналы: {channels_to_check}")
        
        subscribed_channels = []
        not_subscribed_channels = []
        
        # Проверяем подписку на каждый канал
        # Достаточно подписки хотя бы на один канал
        for channel in channels_to_check:
            try:
                logger.info(f"🔍 Проверяю канал {channel} для пользователя {user_id}")
                
                # Получаем информацию о пользователе в канале
                member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
                status = member.status
                
                logger.info(f"📊 Канал {channel}: статус пользователя = '{status}' (тип: {type(status).__name__})")
                
                # ПРАВИЛЬНАЯ ЛОГИКА: используем прямое сравнение с enum значениями
                # Только эти три статуса означают, что пользователь В КАНАЛЕ (подписан):
                is_subscribed = (status == ChatMemberStatus.MEMBER or 
                                status == ChatMemberStatus.ADMINISTRATOR or 
                                status == ChatMemberStatus.CREATOR)
                
                # Детальное логирование для диагностики
                if status == ChatMemberStatus.MEMBER:
                    logger.info(f"   Статус: MEMBER - пользователь является участником")
                elif status == ChatMemberStatus.ADMINISTRATOR:
                    logger.info(f"   Статус: ADMINISTRATOR - пользователь является администратором")
                elif status == ChatMemberStatus.CREATOR:
                    logger.info(f"   Статус: CREATOR - пользователь является создателем")
                elif status == ChatMemberStatus.LEFT:
                    logger.warning(f"   Статус: LEFT - пользователь покинул канал")
                elif status == ChatMemberStatus.KICKED:
                    logger.warning(f"   Статус: KICKED - пользователь был исключен")
                elif status == ChatMemberStatus.RESTRICTED:
                    logger.warning(f"   Статус: RESTRICTED - пользователь ограничен")
                else:
                    logger.warning(f"   Статус: {status} - неизвестный статус")
                
                if is_subscribed:
                    logger.info(f"✅ Пользователь {user_id} ПОДПИСАН на канал {channel} (статус: {status})")
                    subscribed_channels.append(channel)
                else:
                    logger.info(f"❌ Пользователь {user_id} НЕ ПОДПИСАН на канал {channel} (статус: {status})")
                    not_subscribed_channels.append(f"{channel} (статус: {status})")
                    
            except Exception as e:
                error_msg = str(e).lower()
                logger.error(f"⚠️ Ошибка при проверке канала {channel}: {error_msg}")
                
                # Анализируем тип ошибки
                if "user not found" in error_msg or "chat not found" in error_msg:
                    logger.warning(f"   Пользователь не найден в канале {channel} - НЕ ПОДПИСАН")
                elif "not enough rights" in error_msg or "administrator" in error_msg:
                    logger.warning(f"   Бот не является администратором канала {channel}")
                else:
                    logger.warning(f"   Неизвестная ошибка при проверке канала {channel}")
                
                logger.warning(f"   Считаем, что пользователь НЕ ПОДПИСАН на канал {channel}")
                
                # При ЛЮБОЙ ошибке считаем, что пользователь НЕ подписан
                not_subscribed_channels.append(f"{channel} (ошибка доступа)")
                continue
        
        # Итоговый результат
        logger.info(f"📊 Результаты проверки для пользователя {user_id}:")
        logger.info(f"   Подписан на каналы: {subscribed_channels}")
        logger.info(f"   НЕ подписан на каналы: {not_subscribed_channels}")
        
        # Если подписан хотя бы на один канал - возвращаем True
        if subscribed_channels:
            logger.info(f"✅ ИТОГ: Пользователь {user_id} ПОДПИСАН (на каналы: {subscribed_channels})")
            return True
        else:
            logger.warning(f"❌ ИТОГ: Пользователь {user_id} НЕ ПОДПИСАН ни на один канал")
            return False
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при проверке подписки: {e}")
        logger.error(f"   Полный traceback: {traceback.format_exc()}")
        # При критической ошибке считаем, что не подписан (безопаснее)
        return False


async def delete_previous_message(user_id: int, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Удаляет предыдущее сообщение бота пользователю"""
    if user_id in user_messages and user_messages[user_id] is not None:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=user_messages[user_id])
        except Exception as e:
            # Сообщение уже удалено или не существует - это нормально
            logger.debug(f"Не удалось удалить предыдущее сообщение (возможно, уже удалено): {e}")
        finally:
            user_messages[user_id] = None


async def get_main_menu(user_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Создает главное меню - всегда показывает кнопки подписки и проверки"""
    logger.info(f"🔍 Getting main menu for user {user_id}")
    
    # Всегда показываем кнопки подписки и проверки (не проверяем подписку сразу)
    keyboard = []
    for i, channel_link in enumerate(CHANNEL_LINKS, 1):
        keyboard.append([InlineKeyboardButton(
            f"📢 Подписаться на канал {i}",
            url=channel_link
        )])
    
    keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_subscription")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    caption = (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "⚠️ Для использования бота необходимо подписаться на наши каналы.\n\n"
        "Пожалуйста, подпишитесь на каналы ниже и нажмите кнопку проверки:"
    )
    image_path = "Preview.png"
    
    return caption, reply_markup, image_path


async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /getid - возвращает Telegram ID пользователя и проверяет подписку"""
    user_id = update.effective_user.id
    
    logger.info(f"🔍 User {user_id} requested /getid command")
    # Проверяем подписку
    is_subscribed = await check_subscription(user_id, context)
    logger.info(f"📊 Subscription check result for user {user_id}: {is_subscribed}")
    
    text = (
        f"🆔 <b>Ваш Telegram ID:</b>\n\n"
        f"<code>{user_id}</code>\n\n"
    )
    
    if is_subscribed:
        text += "✅ <b>Вы подписаны на каналы!</b>\n\n"
        text += "Используйте кнопку ниже для скачивания на сайте:"
    else:
        text += "❌ <b>Вы не подписаны на каналы</b>\n\n"
        text += "Пожалуйста, подпишитесь на каналы:\n"
        for i, link in enumerate(CHANNEL_LINKS, 1):
            text += f"{i}. {link}\n"
        text += "\nПосле подписки используйте кнопку ниже для проверки:"
    
    # Создаем кнопку для проверки подписки (только если это не localhost - Telegram не принимает http://localhost)
    site_url = os.getenv("SITE_URL", "")
    keyboard = []
    
    if site_url and not site_url.startswith("http://localhost"):
        check_link = f"{site_url}/?telegram_id={user_id}&check_subscription=true"
        keyboard.append([InlineKeyboardButton("✅ Проверить подписку на сайте", url=check_link)])
    else:
        # Если localhost, просто показываем текст с инструкцией
        text += "\n\n💡 <b>Для проверки подписки:</b>\n"
        text += "1. Скопируйте ваш ID выше\n"
        text += "2. Откройте сайт\n"
        text += "3. Введите ID в поле на сайте\n"
        text += "4. Нажмите 'Проверить с ID'"
    
    if keyboard:
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Проверяем, не забанен ли пользователь
    if is_banned(user_id):
        await update.message.reply_text("❌ Вы заблокированы и не можете использовать бота.")
        return
    
    # Обрабатываем параметр start=getid
    if update.message and update.message.text:
        command_args = update.message.text.split(' ', 1)
        if len(command_args) > 1 and 'getid' in command_args[1]:
            await get_id(update, context)
            return
    
    # Сохраняем пользователя для рассылки
    add_user(user_id)
    
    # Удаляем предыдущее сообщение
    await delete_previous_message(user_id, chat_id, context)
    
    # Получаем главное меню
    caption, reply_markup, _ = await get_main_menu(user_id, context)
    
    # При первом старте всегда используем Preview.png
    image_path = "Preview.png"
    
    # Отправляем изображение с главным меню
    try:
        with open(image_path, "rb") as photo:
            sent_message = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except FileNotFoundError:
        # Если файл не найден, отправляем текстовое сообщение
        sent_message = await update.message.reply_text(
            caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке изображения: {e}")
        # В случае ошибки отправляем текстовое сообщение
        sent_message = await update.message.reply_text(
            caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Сохраняем ID отправленного сообщения
    user_messages[user_id] = sent_message.message_id


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    
    if not query:
        return
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    # Сохраняем пользователя для рассылки
    add_user(user_id)
    
    # Пытаемся ответить на callback, игнорируем ошибки если запрос уже обработан
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Ошибка при ответе на callback query: {e}")
        # Продолжаем выполнение, даже если не удалось ответить
    
    if query.data == "copy_id":
        # Копируем ID в буфер обмена (показываем пользователю)
        user_id = query.from_user.id
        await query.answer(f"Ваш ID: {user_id}\nСкопируйте его и введите на сайте", show_alert=True)
        return
    
    if query.data == "check_subscription":
        # Удаляем предыдущее сообщение
        await delete_previous_message(user_id, chat_id, context)
        
        # Показываем сообщение о проверке
        await query.answer("Проверяем подписку...", show_alert=False)
        
        logger.info(f"🔍 User {user_id} requested subscription check")
        logger.info(f"📋 Channels to check: {['@pro_tweaks'] + CHANNEL_IDS}")
        
        # Детальная проверка каждого канала для отображения пользователю
        detailed_results = []
        for channel in ['@pro_tweaks'] + CHANNEL_IDS:
            try:
                member = await context.bot.get_chat_member(chat_id=channel, user_id=user_id)
                status = member.status
                status_str = str(status)
                # Определяем, подписан ли пользователь на этот канал
                # ИСПРАВЛЕНО: используем прямое сравнение с enum значениями
                is_sub = (status == ChatMemberStatus.MEMBER or 
                         status == ChatMemberStatus.ADMINISTRATOR or 
                         status == ChatMemberStatus.CREATOR)
                status_icon = "✅" if is_sub else "❌"
                detailed_results.append(f"{status_icon} {channel}: '{status_str}'")
                logger.info(f"📊 Channel {channel}: status = '{status_str}' (subscribed: {is_sub})")
                if not is_sub:
                    logger.warning(f"   User is NOT subscribed - status '{status_str}' is not MEMBER, ADMINISTRATOR, or CREATOR")
            except Exception as e:
                # При ошибке (включая отсутствие прав у бота) - считаем, что не подписан
                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                
                # Определяем тип ошибки для более информативного сообщения
                if "user not found" in error_msg_lower or "chat not found" in error_msg_lower:
                    error_type = "пользователь не найден"
                elif "not enough rights" in error_msg_lower or "administrator" in error_msg_lower:
                    error_type = "бот не админ"
                else:
                    error_type = "ошибка доступа"
                
                detailed_results.append(f"❌ {channel}: {error_type}")
                logger.error(f"❌ Channel {channel}: error - {e}")
                logger.warning(f"   Bot may not have admin rights in channel {channel} or user is not subscribed")
        
        is_subscribed = await check_subscription(user_id, context)
        logger.info(f"📊 Subscription check result for user {user_id}: {is_subscribed}")
        
        if is_subscribed:
            # Если подписан - показываем картинку succes и кнопки скачивания
            download_link = "https://protweakerinstall.netlify.app/"
            keyboard = [
                [InlineKeyboardButton("🌐 Скачать через сайт", url=download_link)],
                [InlineKeyboardButton("📥 Скачать через бота", callback_data="download_here")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            caption = (
                "✅ <b>Отлично!</b>\n\n"
                "Вы подписаны на все необходимые каналы!\n\n"
                "Выберите способ скачивания:"
            )
            image_path = "succes.png"
        else:
            # Если не подписан - показываем картинку error и кнопки подписки
            keyboard = []
            for i, channel_link in enumerate(CHANNEL_LINKS, 1):
                keyboard.append([InlineKeyboardButton(
                    f"📢 Подписаться на канал {i}",
                    url=channel_link
                )])
            
            keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_subscription")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            caption = (
                "❌ <b>Подписка не найдена</b>\n\n"
                "Пожалуйста, подпишитесь на каналы выше и нажмите кнопку проверки."
            )
            image_path = "error.png"
        
        # Отправляем изображение с результатом проверки
        try:
            with open(image_path, "rb") as photo:
                sent_message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
        except FileNotFoundError:
            # Если файл не найден, отправляем текстовое сообщение
            sent_message = await query.message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке изображения: {e}")
            # В случае ошибки отправляем текстовое сообщение
            sent_message = await query.message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        
        # Удаляем старое сообщение
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")
        
        # Сохраняем ID нового сообщения
        user_messages[user_id] = sent_message.message_id
    
    elif query.data == "channel_info":
        await query.answer("Пожалуйста, подпишитесь на каналы через кнопки выше", show_alert=True)
    
    elif query.data == "download_here":
        # Скачивание через Telegram
        await delete_previous_message(user_id, chat_id, context)
        
        logger.info(f"🔍 User {user_id} requested download via Telegram")
        is_subscribed = await check_subscription(user_id, context)
        logger.info(f"📊 Subscription check result for user {user_id}: {is_subscribed}")
        
        if is_subscribed:
            loading_message_id = None
            try:
                try:
                    with open("download.jpg", "rb") as photo:
                        loading_message = await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=photo,
                            caption="📥 Загрузка файла..."
                        )
                except FileNotFoundError:
                    loading_message = await query.message.reply_text("📥 Загрузка файла...")
                loading_message_id = loading_message.message_id
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(FILE_URL) as response:
                        if response.status == 200:
                            file_data = await response.read()
                            file_obj = BytesIO(file_data)
                            file_obj.name = "ProTweaker-Installer-3.0.1.exe"
                            
                            back_keyboard = [[InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]]
                            back_reply_markup = InlineKeyboardMarkup(back_keyboard)
                            
                            sent_message = await context.bot.send_document(
                                chat_id=chat_id,
                                document=InputFile(file_obj, filename="ProTweaker-Installer-3.0.1.exe"),
                                caption="📥 <b>Файл успешно загружен!</b>",
                                reply_markup=back_reply_markup,
                                parse_mode=ParseMode.HTML
                            )
                            
                            if loading_message_id:
                                try:
                                    await context.bot.delete_message(chat_id=chat_id, message_id=loading_message_id)
                                except Exception as e:
                                    logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
                            
                            try:
                                await query.message.delete()
                            except Exception as e:
                                logger.warning(f"Не удалось удалить исходное сообщение: {e}")
                            
                            user_messages[user_id] = sent_message.message_id
                        else:
                            if loading_message_id:
                                try:
                                    await context.bot.delete_message(chat_id=chat_id, message_id=loading_message_id)
                                except Exception as e:
                                    logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
                            
                            error_text = "❌ Ошибка при загрузке файла. Попробуйте позже."
                            sent_message = await query.message.reply_text(error_text)
                            user_messages[user_id] = sent_message.message_id
            except Exception as e:
                logger.error(f"Ошибка при загрузке файла: {e}")
                
                if loading_message_id:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=loading_message_id)
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение о загрузке: {e}")
                
                error_text = "❌ Произошла ошибка при загрузке файла. Попробуйте позже."
                sent_message = await query.message.reply_text(error_text)
                user_messages[user_id] = sent_message.message_id
        else:
            keyboard = []
            for i, channel_link in enumerate(CHANNEL_LINKS, 1):
                keyboard.append([InlineKeyboardButton(
                    f"📢 Подписаться на канал {i}",
                    url=channel_link
                )])
            
            keyboard.append([InlineKeyboardButton("✅ Проверить подписку", callback_data="check_subscription")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            text = (
                "❌ <b>Доступ запрещен</b>\n\n"
                "Для скачивания файла необходимо подписаться на все каналы."
            )
            
            sent_message = await query.message.reply_text(
                text,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            
            try:
                await query.message.delete()
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение: {e}")
            
            user_messages[user_id] = sent_message.message_id
    
    elif query.data.startswith("admin_"):
        # Перенаправляем админ-коллбэки в админ-обработчик
        await admin_callback_handler(update, context)
    
    elif query.data == "main_menu":
        # Удаляем предыдущее сообщение
        await delete_previous_message(user_id, chat_id, context)
        
        # Получаем главное меню
        caption, reply_markup, image_path = await get_main_menu(user_id, context)
        
        # Отправляем изображение с главным меню
        try:
            with open(image_path, "rb") as photo:
                sent_message = await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML
                )
        except FileNotFoundError:
            # Если файл не найден, отправляем текстовое сообщение
            sent_message = await query.message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке изображения: {e}")
            # В случае ошибки отправляем текстовое сообщение
            sent_message = await query.message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        
        # Удаляем старое сообщение
        try:
            await query.message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")
        
        # Сохраняем ID нового сообщения
        user_messages[user_id] = sent_message.message_id
    


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Сохраняем пользователя для рассылки
    add_user(user_id)
    
    # Проверяем, не является ли это сообщением для админ-панели
    if is_admin(user_id) and user_id in admin_states:
        await handle_admin_message(update, context)
        return
    
    # Удаляем предыдущее сообщение
    await delete_previous_message(user_id, chat_id, context)
    
    # Получаем главное меню
    caption, reply_markup, image_path = await get_main_menu(user_id, context)
    
    # Отправляем изображение с главным меню
    try:
        with open(image_path, "rb") as photo:
            sent_message = await context.bot.send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    except FileNotFoundError:
        # Если файл не найден, отправляем текстовое сообщение
        sent_message = await update.message.reply_text(
            caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке изображения: {e}")
        # В случае ошибки отправляем текстовое сообщение
        sent_message = await update.message.reply_text(
            caption,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    # Сохраняем ID отправленного сообщения
    user_messages[user_id] = sent_message.message_id


# ==================== АДМИН-ПАНЕЛЬ ====================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Расширенная админ-панель"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ У вас нет доступа к админ-панели.")
        return
    
    # Удаляем предыдущее сообщение
    await delete_previous_message(user_id, chat_id, context)
    
    # Получаем статистику
    data = load_data()
    users_count = len(data.get('users', []))
    admins_count = len(data.get('admins', []))
    channels_count = len(data.get('channel_ids', []))
    banned_count = len(data.get('banned_users', []))
    
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Управление каналами", callback_data="admin_channels")],
        [InlineKeyboardButton("🔗 Управление ссылками", callback_data="admin_links")],
        [InlineKeyboardButton("📁 Управление файлами", callback_data="admin_files")],
        [InlineKeyboardButton("👥 Управление админами", callback_data="admin_admins")],
        [InlineKeyboardButton("👤 Просмотр пользователей", callback_data="admin_users")],
        [InlineKeyboardButton("🚫 Бан/Разбан пользователей", callback_data="admin_ban")],
        [InlineKeyboardButton("📝 Управление текстами", callback_data="admin_texts")],
        [InlineKeyboardButton("🖼️ Управление изображениями", callback_data="admin_images")],
        [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("📋 Логи действий", callback_data="admin_logs")],
        [InlineKeyboardButton("⚙️ Настройки бота", callback_data="admin_settings")],
        [InlineKeyboardButton("💾 Экспорт/Импорт данных", callback_data="admin_export")],
        [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🔐 <b>Расширенная админ-панель</b>\n\n"
        f"📊 <b>Быстрая статистика:</b>\n"
        f"👥 Пользователей: <b>{users_count}</b>\n"
        f"👤 Админов: <b>{admins_count}</b>\n"
        f"📢 Каналов: <b>{channels_count}</b>\n"
        f"🚫 Забанено: <b>{banned_count}</b>\n\n"
        "Выберите действие:"
    )
    
    sent_message = await update.message.reply_text(
        text,
        reply_markup=reply_markup,
        parse_mode=ParseMode.HTML
    )
    
    user_messages[user_id] = sent_message.message_id


async def admin_channels_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления каналами"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    channels = data.get('channel_ids', [])
    
    keyboard = []
    for i, channel_id in enumerate(channels, 1):
        keyboard.append([InlineKeyboardButton(
            f"📢 Канал {i} (ID: {channel_id})",
            callback_data=f"admin_channel_edit_{i}"
        )])
    
    keyboard.extend([
        [InlineKeyboardButton("➕ Добавить канал", callback_data="admin_channel_add")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📢 <b>Управление каналами</b>\n\n"
        f"Текущие каналы: {len(channels)}\n\n"
        "Выберите канал для редактирования или добавьте новый:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_links_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления ссылками"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    links = data.get('channel_links', [])
    
    keyboard = []
    for i, link in enumerate(links, 1):
        keyboard.append([InlineKeyboardButton(
            f"🔗 Ссылка {i}",
            callback_data=f"admin_link_edit_{i}"
        )])
    
    keyboard.extend([
        [InlineKeyboardButton("➕ Добавить ссылку", callback_data="admin_link_add")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🔗 <b>Управление ссылками</b>\n\n"
        f"Текущие ссылки: {len(links)}\n\n"
        "Выберите ссылку для редактирования или добавьте новую:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_files_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления файлами"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    file_url = data.get('file_url', FILE_URL)
    
    keyboard = [
        [InlineKeyboardButton("📝 Изменить ссылку на файл", callback_data="admin_file_edit")],
        [InlineKeyboardButton("📤 Загрузить файл", callback_data="admin_file_upload")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📁 <b>Управление файлами</b>\n\n"
        f"Текущая ссылка:\n<code>{file_url}</code>\n\n"
        "Выберите действие:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_admins_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления администраторами"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        try:
            await query.answer("❌ У вас нет доступа", show_alert=True)
        except:
            pass
        return
    
    try:
        await query.answer()
    except:
        pass
    
    data = load_data()
    admins = data.get('admins', [])
    
    keyboard = []
    for admin_id in admins:
        try:
            user = await context.bot.get_chat(admin_id)
            username = user.username if user.username else (user.first_name or f"ID: {admin_id}")
            display_name = f"👤 {username}"
            if admin_id == user_id:
                display_name += " (Вы)"
            keyboard.append([InlineKeyboardButton(
                display_name,
                callback_data=f"admin_remove_{admin_id}"
            )])
        except Exception as e:
            logger.warning(f"Не удалось получить информацию о пользователе {admin_id}: {e}")
            display_name = f"👤 ID: {admin_id}"
            if admin_id == user_id:
                display_name += " (Вы)"
            keyboard.append([InlineKeyboardButton(
                display_name,
                callback_data=f"admin_remove_{admin_id}"
            )])
    
    keyboard.extend([
        [InlineKeyboardButton("➕ Добавить админа", callback_data="admin_add")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin_admins")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "👥 <b>Управление администраторами</b>\n\n"
        f"Текущих админов: <b>{len(admins)}</b>\n\n"
        "Выберите админа для удаления или добавьте нового:"
    )
    
    try:
        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.warning(f"Не удалось обновить сообщение: {e}")
        # Если не удалось обновить, отправляем новое
        try:
            sent_msg = await query.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            try:
                await query.message.delete()
            except:
                pass
        except:
            pass


async def admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню рассылки"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    # Получаем количество пользователей
    users = get_all_users()
    users_count = len(users)
    
    keyboard = [
        [InlineKeyboardButton("📨 Начать рассылку", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📨 <b>Рассылка</b>\n\n"
        f"👥 Пользователей в базе: <b>{users_count}</b>\n\n"
        "Отправьте сообщение, которое хотите разослать всем пользователям.\n\n"
        "Нажмите кнопку ниже, чтобы начать:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_stats_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню статистики"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    users = data.get('users', [])
    admins = data.get('admins', [])
    channels = data.get('channel_ids', [])
    links = data.get('channel_links', [])
    banned = data.get('banned_users', [])
    
    # Подсчитываем активных пользователей (тех, кто не забанен)
    active_users = [u for u in users if u not in banned]
    
    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin_stats")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📊 <b>Статистика бота</b>\n\n"
        f"👥 <b>Пользователи:</b>\n"
        f"   • Всего: <b>{len(users)}</b>\n"
        f"   • Активных: <b>{len(active_users)}</b>\n"
        f"   • Забанено: <b>{len(banned)}</b>\n\n"
        f"👤 <b>Администраторы:</b> <b>{len(admins)}</b>\n\n"
        f"📢 <b>Каналы:</b>\n"
        f"   • ID каналов: <b>{len(channels)}</b>\n"
        f"   • Ссылок: <b>{len(links)}</b>\n\n"
        f"📋 <b>Логи:</b> <b>{len(action_logs)}</b> записей\n\n"
        f"⏰ <b>Время:</b> {asyncio.get_event_loop().time():.0f}"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню просмотра пользователей"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    users = data.get('users', [])
    banned = data.get('banned_users', [])
    
    keyboard = [
        [InlineKeyboardButton("🔍 Поиск пользователя", callback_data="admin_user_search")],
        [InlineKeyboardButton("📋 Список всех пользователей", callback_data="admin_user_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "👤 <b>Просмотр пользователей</b>\n\n"
        f"👥 Всего пользователей: <b>{len(users)}</b>\n"
        f"🚫 Забанено: <b>{len(banned)}</b>\n"
        f"✅ Активных: <b>{len(users) - len(banned)}</b>\n\n"
        "Выберите действие:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_ban_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню бана/разбана пользователей"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    banned = data.get('banned_users', [])
    
    keyboard = [
        [InlineKeyboardButton("🚫 Забанить пользователя", callback_data="admin_ban_add")],
        [InlineKeyboardButton("✅ Разбанить пользователя", callback_data="admin_ban_remove")],
        [InlineKeyboardButton("📋 Список забаненных", callback_data="admin_ban_list")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🚫 <b>Бан/Разбан пользователей</b>\n\n"
        f"Забанено пользователей: <b>{len(banned)}</b>\n\n"
        "Выберите действие:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_texts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления текстами"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    messages = data.get('messages', {})
    
    keyboard = [
        [InlineKeyboardButton("✏️ Редактировать приветствие", callback_data="admin_text_welcome")],
        [InlineKeyboardButton("✏️ Редактировать текст успеха", callback_data="admin_text_success")],
        [InlineKeyboardButton("✏️ Редактировать текст ошибки", callback_data="admin_text_error")],
        [InlineKeyboardButton("📋 Просмотр всех текстов", callback_data="admin_text_view")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📝 <b>Управление текстами</b>\n\n"
        f"Сохранено текстов: <b>{len(messages)}</b>\n\n"
        "Выберите действие:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_images_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню управления изображениями"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    images = data.get('images', {})
    
    keyboard = [
        [InlineKeyboardButton("🖼️ Загрузить Preview.png", callback_data="admin_image_preview")],
        [InlineKeyboardButton("🖼️ Загрузить succes.png", callback_data="admin_image_success")],
        [InlineKeyboardButton("🖼️ Загрузить error.png", callback_data="admin_image_error")],
        [InlineKeyboardButton("🖼️ Загрузить download.jpg", callback_data="admin_image_download")],
        [InlineKeyboardButton("📋 Просмотр всех изображений", callback_data="admin_image_view")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "🖼️ <b>Управление изображениями</b>\n\n"
        f"Сохранено изображений: <b>{len(images)}</b>\n\n"
        "Выберите изображение для загрузки:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_logs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню логов действий"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    # Показываем последние 10 логов
    recent_logs = action_logs[-10:] if len(action_logs) > 10 else action_logs
    logs_text = ""
    for log in reversed(recent_logs):
        timestamp = log.get('timestamp', 'N/A')
        action = log.get('action', 'N/A')
        admin_id = log.get('admin_id', 'N/A')
        logs_text += f"⏰ {timestamp}\n📝 {action}\n👤 Admin: {admin_id}\n\n"
    
    keyboard = [
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin_logs")],
        [InlineKeyboardButton("🗑️ Очистить логи", callback_data="admin_logs_clear")],
        [InlineKeyboardButton("💾 Экспорт логов", callback_data="admin_logs_export")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "📋 <b>Логи действий</b>\n\n"
        f"Всего записей: <b>{len(action_logs)}</b>\n\n"
        f"<b>Последние действия:</b>\n\n{logs_text if logs_text else 'Логов пока нет'}"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню настроек бота"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    data = load_data()
    settings = data.get('settings', {})
    
    auto_delete = settings.get('auto_delete_messages', False)
    require_subscription = settings.get('require_subscription', True)
    
    keyboard = [
        [InlineKeyboardButton(f"{'✅' if auto_delete else '❌'} Автоудаление сообщений", callback_data="admin_setting_autodelete")],
        [InlineKeyboardButton(f"{'✅' if require_subscription else '❌'} Требовать подписку", callback_data="admin_setting_subscription")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "⚙️ <b>Настройки бота</b>\n\n"
        f"🗑️ Автоудаление сообщений: <b>{'Включено' if auto_delete else 'Выключено'}</b>\n"
        f"📢 Требовать подписку: <b>{'Да' if require_subscription else 'Нет'}</b>\n\n"
        "Выберите настройку для изменения:"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_export_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню экспорта/импорта данных"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not is_admin(user_id):
        await query.answer("❌ У вас нет доступа", show_alert=True)
        return
    
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("💾 Экспорт данных (JSON)", callback_data="admin_export_json")],
        [InlineKeyboardButton("📥 Импорт данных", callback_data="admin_import_data")],
        [InlineKeyboardButton("🔄 Резервная копия", callback_data="admin_backup")],
        [InlineKeyboardButton("🔙 Назад", callback_data="admin_panel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = (
        "💾 <b>Экспорт/Импорт данных</b>\n\n"
        "Выберите действие:\n\n"
        "💾 <b>Экспорт</b> - скачать все данные бота\n"
        "📥 <b>Импорт</b> - загрузить данные из файла\n"
        "🔄 <b>Резервная копия</b> - создать backup"
    )
    
    await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)


async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик callback для админ-панели"""
    query = update.callback_query
    
    if not query:
        return
    
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    
    if not is_admin(user_id):
        try:
            await query.answer("❌ У вас нет доступа", show_alert=True)
        except Exception as e:
            logger.warning(f"Ошибка при ответе на callback query: {e}")
        return
    
    # Пытаемся ответить на callback, игнорируем ошибки если запрос уже обработан
    try:
        await query.answer()
    except Exception as e:
        logger.warning(f"Ошибка при ответе на callback query: {e}")
        # Продолжаем выполнение, даже если не удалось ответить
    
    data = query.data
    
    if data == "admin_panel":
        # Получаем статистику
        data_obj = load_data()
        users_count = len(data_obj.get('users', []))
        admins_count = len(data_obj.get('admins', []))
        channels_count = len(data_obj.get('channel_ids', []))
        banned_count = len(data_obj.get('banned_users', []))
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Управление каналами", callback_data="admin_channels")],
            [InlineKeyboardButton("🔗 Управление ссылками", callback_data="admin_links")],
            [InlineKeyboardButton("📁 Управление файлами", callback_data="admin_files")],
            [InlineKeyboardButton("👥 Управление админами", callback_data="admin_admins")],
            [InlineKeyboardButton("👤 Просмотр пользователей", callback_data="admin_users")],
            [InlineKeyboardButton("🚫 Бан/Разбан пользователей", callback_data="admin_ban")],
            [InlineKeyboardButton("📝 Управление текстами", callback_data="admin_texts")],
            [InlineKeyboardButton("🖼️ Управление изображениями", callback_data="admin_images")],
            [InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast")],
            [InlineKeyboardButton("📋 Логи действий", callback_data="admin_logs")],
            [InlineKeyboardButton("⚙️ Настройки бота", callback_data="admin_settings")],
            [InlineKeyboardButton("💾 Экспорт/Импорт данных", callback_data="admin_export")],
            [InlineKeyboardButton("🔙 Главное меню", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            "🔐 <b>Расширенная админ-панель</b>\n\n"
            f"📊 <b>Быстрая статистика:</b>\n"
            f"👥 Пользователей: <b>{users_count}</b>\n"
            f"👤 Админов: <b>{admins_count}</b>\n"
            f"📢 Каналов: <b>{channels_count}</b>\n"
            f"🚫 Забанено: <b>{banned_count}</b>\n\n"
            "Выберите действие:"
        )
        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    
    elif data == "admin_channels":
        await admin_channels_menu(update, context)
    
    elif data == "admin_links":
        await admin_links_menu(update, context)
    
    elif data == "admin_files":
        await admin_files_menu(update, context)
    
    elif data == "admin_admins":
        await admin_admins_menu(update, context)
    
    elif data == "admin_broadcast":
        await admin_broadcast_menu(update, context)
    
    elif data == "admin_stats":
        await admin_stats_menu(update, context)
    
    elif data == "admin_users":
        await admin_users_menu(update, context)
    
    elif data == "admin_ban":
        await admin_ban_menu(update, context)
    
    elif data == "admin_texts":
        await admin_texts_menu(update, context)
    
    elif data == "admin_images":
        await admin_images_menu(update, context)
    
    elif data == "admin_logs":
        await admin_logs_menu(update, context)
    
    elif data == "admin_settings":
        await admin_settings_menu(update, context)
    
    elif data == "admin_export":
        await admin_export_menu(update, context)
    
    elif data.startswith("admin_channel_add"):
        admin_states[user_id] = "add_channel"
        await query.message.edit_text(
            "➕ <b>Добавление канала</b>\n\n"
            "Отправьте ID канала (например: -1001234567890):",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_link_edit_"):
        link_index = int(data.split("_")[-1]) - 1
        data_obj = load_data()
        links = data_obj.get('channel_links', [])
        if 0 <= link_index < len(links):
            admin_states[user_id] = f"edit_link_{link_index}"
            await query.message.edit_text(
                f"✏️ <b>Редактирование ссылки</b>\n\n"
                f"Текущая ссылка: {links[link_index]}\n\n"
                f"Отправьте новую ссылку или 'удалить' для удаления:",
                parse_mode=ParseMode.HTML
            )
    
    elif data.startswith("admin_link_add"):
        admin_states[user_id] = "add_link"
        await query.message.edit_text(
            "➕ <b>Добавление ссылки</b>\n\n"
            "Отправьте ссылку на канал (например: https://t.me/channel):",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_file_edit"):
        admin_states[user_id] = "edit_file_url"
        await query.message.edit_text(
            "📝 <b>Изменение ссылки на файл</b>\n\n"
            "Отправьте новую ссылку на файл:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_file_upload"):
        admin_states[user_id] = "upload_file"
        await query.message.edit_text(
            "📤 <b>Загрузка файла</b>\n\n"
            "Отправьте файл, который хотите использовать:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_add"):
        admin_states[user_id] = "add_admin"
        try:
            await query.message.edit_text(
                "➕ <b>Добавление администратора</b>\n\n"
                "Отправьте одним из способов:\n"
                "• ID пользователя (число)\n"
                "• Переслать сообщение от пользователя\n"
                "• Ответить на сообщение пользователя\n\n"
                "Для отмены отправьте /admin",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Не удалось обновить сообщение: {e}")
            try:
                sent_msg = await query.message.reply_text(
                    "➕ <b>Добавление администратора</b>\n\n"
                    "Отправьте одним из способов:\n"
                    "• ID пользователя (число)\n"
                    "• Переслать сообщение от пользователя\n"
                    "• Ответить на сообщение пользователя\n\n"
                    "Для отмены отправьте /admin",
                    parse_mode=ParseMode.HTML
                )
                try:
                    await query.message.delete()
                except:
                    pass
            except:
                pass
    
    elif data.startswith("admin_remove_"):
        admin_id = int(data.split("_")[-1])
        
        # Нельзя удалить самого себя
        if admin_id == user_id:
            try:
                await query.answer("❌ Вы не можете удалить самого себя", show_alert=True)
            except:
                pass
            return
        
        if remove_admin(admin_id):
            try:
                await query.answer("✅ Администратор удален", show_alert=True)
            except:
                pass
            await admin_admins_menu(update, context)
        else:
            try:
                await query.answer("❌ Ошибка при удалении", show_alert=True)
            except:
                pass
    
    elif data.startswith("admin_channel_edit_"):
        channel_index = int(data.split("_")[-1]) - 1
        data_obj = load_data()
        channels = data_obj.get('channel_ids', [])
        if 0 <= channel_index < len(channels):
            admin_states[user_id] = f"edit_channel_{channel_index}"
            await query.message.edit_text(
                f"✏️ <b>Редактирование канала</b>\n\n"
                f"Текущий ID: {channels[channel_index]}\n\n"
                f"Отправьте новый ID канала или 'удалить' для удаления:",
                parse_mode=ParseMode.HTML
            )
    
    elif data.startswith("admin_broadcast_start"):
        admin_states[user_id] = "broadcast"
        log_action(user_id, "Начал рассылку")
        await query.message.edit_text(
            "📨 <b>Рассылка</b>\n\n"
            "Отправьте сообщение, которое хотите разослать всем пользователям:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_ban_add"):
        admin_states[user_id] = "ban_user"
        await query.message.edit_text(
            "🚫 <b>Забанить пользователя</b>\n\n"
            "Отправьте ID пользователя для бана:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_ban_remove"):
        admin_states[user_id] = "unban_user"
        await query.message.edit_text(
            "✅ <b>Разбанить пользователя</b>\n\n"
            "Отправьте ID пользователя для разбана:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_ban_list"):
        data_obj = load_data()
        banned = data_obj.get('banned_users', [])
        if banned:
            banned_text = "\n".join([f"• <code>{uid}</code>" for uid in banned[:50]])
            if len(banned) > 50:
                banned_text += f"\n\n... и еще {len(banned) - 50} пользователей"
        else:
            banned_text = "Нет забаненных пользователей"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_ban")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"🚫 <b>Забаненные пользователи</b>\n\n{banned_text}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_user_search"):
        admin_states[user_id] = "search_user"
        await query.message.edit_text(
            "🔍 <b>Поиск пользователя</b>\n\n"
            "Отправьте ID пользователя для поиска:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_user_list"):
        data_obj = load_data()
        users = data_obj.get('users', [])
        if users:
            users_text = "\n".join([f"• <code>{uid}</code>" for uid in users[:50]])
            if len(users) > 50:
                users_text += f"\n\n... и еще {len(users) - 50} пользователей"
        else:
            users_text = "Нет пользователей"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_users")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"👥 <b>Список пользователей</b>\n\nВсего: {len(users)}\n\n{users_text}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_text_welcome"):
        admin_states[user_id] = "edit_text_welcome"
        await query.message.edit_text(
            "✏️ <b>Редактирование приветствия</b>\n\n"
            "Отправьте новый текст приветствия:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_text_success"):
        admin_states[user_id] = "edit_text_success"
        await query.message.edit_text(
            "✏️ <b>Редактирование текста успеха</b>\n\n"
            "Отправьте новый текст:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_text_error"):
        admin_states[user_id] = "edit_text_error"
        await query.message.edit_text(
            "✏️ <b>Редактирование текста ошибки</b>\n\n"
            "Отправьте новый текст:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_text_view"):
        data_obj = load_data()
        messages = data_obj.get('messages', {})
        texts = "\n".join([f"• <b>{key}</b>: {value[:50]}..." for key, value in list(messages.items())[:10]])
        if not texts:
            texts = "Нет сохраненных текстов"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_texts")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"📋 <b>Сохраненные тексты</b>\n\n{texts}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_image_preview"):
        admin_states[user_id] = "upload_image_preview"
        await query.message.edit_text(
            "🖼️ <b>Загрузка Preview.png</b>\n\n"
            "Отправьте изображение:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_image_success"):
        admin_states[user_id] = "upload_image_success"
        await query.message.edit_text(
            "🖼️ <b>Загрузка succes.png</b>\n\n"
            "Отправьте изображение:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_image_error"):
        admin_states[user_id] = "upload_image_error"
        await query.message.edit_text(
            "🖼️ <b>Загрузка error.png</b>\n\n"
            "Отправьте изображение:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_image_download"):
        admin_states[user_id] = "upload_image_download"
        await query.message.edit_text(
            "🖼️ <b>Загрузка download.jpg</b>\n\n"
            "Отправьте изображение:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_image_view"):
        data_obj = load_data()
        images = data_obj.get('images', {})
        images_text = "\n".join([f"• <b>{key}</b>" for key in list(images.keys())[:10]])
        if not images_text:
            images_text = "Нет сохраненных изображений"
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_images")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.edit_text(
            f"📋 <b>Сохраненные изображения</b>\n\n{images_text}",
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_logs_clear"):
        action_logs.clear()
        log_action(user_id, "Очистил логи")
        await query.answer("✅ Логи очищены", show_alert=True)
        await admin_logs_menu(update, context)
    
    elif data.startswith("admin_logs_export"):
        if action_logs:
            logs_json = json.dumps(action_logs, ensure_ascii=False, indent=2)
            logs_file = BytesIO(logs_json.encode('utf-8'))
            logs_file.name = "logs.json"
            await context.bot.send_document(
                chat_id=chat_id,
                document=InputFile(logs_file, filename="logs.json"),
                caption="📋 Логи действий"
            )
            log_action(user_id, "Экспортировал логи")
        else:
            await query.answer("❌ Нет логов для экспорта", show_alert=True)
    
    elif data.startswith("admin_setting_autodelete"):
        data_obj = load_data()
        settings = data_obj.get('settings', {})
        settings['auto_delete_messages'] = not settings.get('auto_delete_messages', False)
        data_obj['settings'] = settings
        save_data(data_obj)
        log_action(user_id, f"Изменил автоудаление: {settings['auto_delete_messages']}")
        await admin_settings_menu(update, context)
    
    elif data.startswith("admin_setting_subscription"):
        data_obj = load_data()
        settings = data_obj.get('settings', {})
        settings['require_subscription'] = not settings.get('require_subscription', True)
        data_obj['settings'] = settings
        save_data(data_obj)
        log_action(user_id, f"Изменил требование подписки: {settings['require_subscription']}")
        await admin_settings_menu(update, context)
    
    elif data.startswith("admin_export_json"):
        data_obj = load_data()
        export_data = json.dumps(data_obj, ensure_ascii=False, indent=2)
        export_file = BytesIO(export_data.encode('utf-8'))
        export_file.name = "bot_data_export.json"
        await context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(export_file, filename="bot_data_export.json"),
            caption="💾 Экспорт данных бота"
        )
        log_action(user_id, "Экспортировал данные")
    
    elif data.startswith("admin_import_data"):
        admin_states[user_id] = "import_data"
        await query.message.edit_text(
            "📥 <b>Импорт данных</b>\n\n"
            "Отправьте JSON файл с данными:",
            parse_mode=ParseMode.HTML
        )
    
    elif data.startswith("admin_backup"):
        data_obj = load_data()
        backup_data = json.dumps(data_obj, ensure_ascii=False, indent=2)
        backup_file = BytesIO(backup_data.encode('utf-8'))
        backup_file.name = f"backup_{int(asyncio.get_event_loop().time())}.json"
        await context.bot.send_document(
            chat_id=chat_id,
            document=InputFile(backup_file, filename=backup_file.name),
            caption="🔄 Резервная копия данных"
        )
        log_action(user_id, "Создал резервную копию")


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик сообщений для админ-панели"""
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not is_admin(user_id):
        return
    
    state = admin_states.get(user_id)
    
    if not state:
        return
    
    message = update.message
    
    if state == "add_channel":
        try:
            channel_id = int(message.text)
            data = load_data()
            channels = data.get('channel_ids', [])
            if channel_id not in channels:
                channels.append(channel_id)
                data['channel_ids'] = channels
                if save_data(data):
                    log_action(user_id, f"Добавил канал {channel_id}")
                    admin_states[user_id] = None
                    await message.reply_text(f"✅ Канал {channel_id} добавлен!")
                    await admin_channels_menu(update, context)
                else:
                    await message.reply_text("❌ Ошибка при сохранении")
            else:
                await message.reply_text("❌ Этот канал уже добавлен")
        except ValueError:
            await message.reply_text("❌ Неверный формат ID канала")
    
    elif state.startswith("edit_channel_"):
        try:
            channel_index = int(state.split("_")[-1])
            text = message.text.lower()
            if text == "удалить":
                data = load_data()
                channels = data.get('channel_ids', [])
                if 0 <= channel_index < len(channels):
                    removed = channels.pop(channel_index)
                    data['channel_ids'] = channels
                    if save_data(data):
                        admin_states[user_id] = None
                        await message.reply_text(f"✅ Канал {removed} удален!")
                        await admin_channels_menu(update, context)
                    else:
                        await message.reply_text("❌ Ошибка при сохранении")
            else:
                channel_id = int(message.text)
                data = load_data()
                channels = data.get('channel_ids', [])
                if 0 <= channel_index < len(channels):
                    channels[channel_index] = channel_id
                    data['channel_ids'] = channels
                    if save_data(data):
                        admin_states[user_id] = None
                        await message.reply_text(f"✅ Канал обновлен на {channel_id}!")
                        await admin_channels_menu(update, context)
                    else:
                        await message.reply_text("❌ Ошибка при сохранении")
        except ValueError:
            await message.reply_text("❌ Неверный формат ID канала")
    
    elif state.startswith("edit_link_"):
        link_index = int(state.split("_")[-1])
        text = message.text.lower()
        if text == "удалить":
            data = load_data()
            links = data.get('channel_links', [])
            if 0 <= link_index < len(links):
                removed = links.pop(link_index)
                data['channel_links'] = links
                if save_data(data):
                    admin_states[user_id] = None
                    await message.reply_text(f"✅ Ссылка удалена!")
                    await admin_links_menu(update, context)
                else:
                    await message.reply_text("❌ Ошибка при сохранении")
        else:
            link = message.text
            if link.startswith("http"):
                data = load_data()
                links = data.get('channel_links', [])
                if 0 <= link_index < len(links):
                    links[link_index] = link
                    data['channel_links'] = links
                    if save_data(data):
                        admin_states[user_id] = None
                        await message.reply_text(f"✅ Ссылка обновлена!")
                        await admin_links_menu(update, context)
                    else:
                        await message.reply_text("❌ Ошибка при сохранении")
            else:
                await message.reply_text("❌ Неверный формат ссылки")
    
    elif state == "add_link":
        link = message.text
        if link.startswith("http"):
            data = load_data()
            links = data.get('channel_links', [])
            if link not in links:
                links.append(link)
                data['channel_links'] = links
                if save_data(data):
                    admin_states[user_id] = None
                    await message.reply_text(f"✅ Ссылка добавлена!")
                    await admin_links_menu(update, context)
                else:
                    await message.reply_text("❌ Ошибка при сохранении")
            else:
                await message.reply_text("❌ Эта ссылка уже добавлена")
        else:
            await message.reply_text("❌ Неверный формат ссылки")
    
    elif state == "edit_file_url":
        file_url = message.text
        if file_url.startswith("http"):
            data = load_data()
            data['file_url'] = file_url
            if save_data(data):
                admin_states[user_id] = None
                await message.reply_text("✅ Ссылка на файл обновлена!")
                await admin_files_menu(update, context)
            else:
                await message.reply_text("❌ Ошибка при сохранении")
        else:
            await message.reply_text("❌ Неверный формат ссылки")
    
    elif state == "add_admin":
        try:
            admin_id = None
            
            # Проверяем пересланное сообщение
            if hasattr(message, 'forward_from') and message.forward_from:
                admin_id = message.forward_from.id
            elif hasattr(message, 'forward_from_chat') and message.forward_from_chat:
                # Если переслано из канала/группы, берем отправителя оригинального сообщения
                if message.forward_from_chat.type in ['channel', 'group']:
                    await message.reply_text("❌ Нельзя добавить канал или группу как администратора. Отправьте ID пользователя или перешлите сообщение от пользователя.")
                    return
            elif hasattr(message, 'reply_to_message') and message.reply_to_message:
                # Если ответ на сообщение, берем ID отправителя
                if hasattr(message.reply_to_message, 'from_user') and message.reply_to_message.from_user:
                    admin_id = message.reply_to_message.from_user.id
            
            # Если не нашли через пересылку, пытаемся получить из текста
            if admin_id is None:
                if hasattr(message, 'text') and message.text and message.text.strip().isdigit():
                    admin_id = int(message.text.strip())
                else:
                    await message.reply_text(
                        "❌ <b>Неверный формат</b>\n\n"
                        "Отправьте:\n"
                        "• ID пользователя (число)\n"
                        "• Переслать сообщение от пользователя\n"
                        "• Ответить на сообщение пользователя",
                        parse_mode=ParseMode.HTML
                    )
                    return
            
            if add_admin(admin_id):
                log_action(user_id, f"Добавил администратора {admin_id}")
                admin_states[user_id] = None
                try:
                    user_info = await context.bot.get_chat(admin_id)
                    username = user_info.username if user_info.username else f"ID: {admin_id}"
                    await message.reply_text(f"✅ Администратор <b>{username}</b> (ID: {admin_id}) добавлен!", parse_mode=ParseMode.HTML)
                except:
                    await message.reply_text(f"✅ Администратор (ID: {admin_id}) добавлен!")
                await admin_admins_menu(update, context)
            else:
                await message.reply_text("❌ Этот пользователь уже является администратором")
        except ValueError:
            await message.reply_text(
                "❌ <b>Неверный формат ID</b>\n\n"
                "Отправьте:\n"
                "• ID пользователя (число)\n"
                "• Переслать сообщение от пользователя\n"
                "• Ответить на сообщение пользователя",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Ошибка при добавлении администратора: {e}")
            await message.reply_text(
                f"❌ <b>Ошибка при добавлении администратора</b>\n\n"
                f"Попробуйте отправить ID пользователя числом.\n\n"
                f"Детали: {str(e)}",
                parse_mode=ParseMode.HTML
            )
    
    elif state == "upload_file":
        if message.document:
            file = await context.bot.get_file(message.document.file_id)
            # Сохраняем информацию о файле
            data = load_data()
            data['file_id'] = message.document.file_id
            data['file_name'] = message.document.file_name
            if save_data(data):
                admin_states[user_id] = None
                await message.reply_text("✅ Файл загружен и сохранен!")
                await admin_files_menu(update, context)
            else:
                await message.reply_text("❌ Ошибка при сохранении")
        else:
            await message.reply_text("❌ Пожалуйста, отправьте файл")
    
    elif state == "ban_user":
        try:
            ban_id = int(message.text)
            if ban_user(ban_id):
                log_action(user_id, f"Забанил пользователя {ban_id}")
                admin_states[user_id] = None
                await message.reply_text(f"✅ Пользователь {ban_id} забанен!")
                await admin_ban_menu(update, context)
            else:
                await message.reply_text("❌ Ошибка при бане пользователя")
        except ValueError:
            await message.reply_text("❌ Неверный формат ID пользователя")
    
    elif state == "unban_user":
        try:
            unban_id = int(message.text)
            if unban_user(unban_id):
                log_action(user_id, f"Разбанил пользователя {unban_id}")
                admin_states[user_id] = None
                await message.reply_text(f"✅ Пользователь {unban_id} разбанен!")
                await admin_ban_menu(update, context)
            else:
                await message.reply_text("❌ Пользователь не был забанен")
        except ValueError:
            await message.reply_text("❌ Неверный формат ID пользователя")
    
    elif state == "search_user":
        try:
            search_id = int(message.text)
            data = load_data()
            users = data.get('users', [])
            banned = data.get('banned_users', [])
            
            is_user = search_id in users
            is_banned = search_id in banned
            
            text = (
                f"🔍 <b>Информация о пользователе</b>\n\n"
                f"🆔 ID: <code>{search_id}</code>\n"
                f"👤 В базе: {'✅ Да' if is_user else '❌ Нет'}\n"
                f"🚫 Статус: {'Забанен' if is_banned else 'Активен'}\n"
            )
            
            try:
                user_info = await context.bot.get_chat(search_id)
                text += f"📛 Имя: {user_info.first_name or 'N/A'}\n"
                if user_info.username:
                    text += f"👤 Username: @{user_info.username}\n"
            except:
                text += "⚠️ Не удалось получить информацию о пользователе\n"
            
            admin_states[user_id] = None
            await message.reply_text(text, parse_mode=ParseMode.HTML)
            await admin_users_menu(update, context)
        except ValueError:
            await message.reply_text("❌ Неверный формат ID пользователя")
    
    elif state == "edit_text_welcome":
        data = load_data()
        messages = data.get('messages', {})
        messages['welcome'] = message.text
        data['messages'] = messages
        if save_data(data):
            log_action(user_id, "Изменил текст приветствия")
            admin_states[user_id] = None
            await message.reply_text("✅ Текст приветствия обновлен!")
            await admin_texts_menu(update, context)
    
    elif state == "edit_text_success":
        data = load_data()
        messages = data.get('messages', {})
        messages['success'] = message.text
        data['messages'] = messages
        if save_data(data):
            log_action(user_id, "Изменил текст успеха")
            admin_states[user_id] = None
            await message.reply_text("✅ Текст успеха обновлен!")
            await admin_texts_menu(update, context)
    
    elif state == "edit_text_error":
        data = load_data()
        messages = data.get('messages', {})
        messages['error'] = message.text
        data['messages'] = messages
        if save_data(data):
            log_action(user_id, "Изменил текст ошибки")
            admin_states[user_id] = None
            await message.reply_text("✅ Текст ошибки обновлен!")
            await admin_texts_menu(update, context)
    
    elif state == "upload_image_preview":
        if message.photo:
            file = await context.bot.get_file(message.photo[-1].file_id)
            await file.download_to_drive("Preview.png")
            data = load_data()
            images = data.get('images', {})
            images['preview'] = message.photo[-1].file_id
            data['images'] = images
            save_data(data)
            log_action(user_id, "Загрузил Preview.png")
            admin_states[user_id] = None
            await message.reply_text("✅ Изображение Preview.png загружено!")
            await admin_images_menu(update, context)
        else:
            await message.reply_text("❌ Пожалуйста, отправьте изображение")
    
    elif state == "upload_image_success":
        if message.photo:
            file = await context.bot.get_file(message.photo[-1].file_id)
            await file.download_to_drive("succes.png")
            data = load_data()
            images = data.get('images', {})
            images['success'] = message.photo[-1].file_id
            data['images'] = images
            save_data(data)
            log_action(user_id, "Загрузил succes.png")
            admin_states[user_id] = None
            await message.reply_text("✅ Изображение succes.png загружено!")
            await admin_images_menu(update, context)
        else:
            await message.reply_text("❌ Пожалуйста, отправьте изображение")
    
    elif state == "upload_image_error":
        if message.photo:
            file = await context.bot.get_file(message.photo[-1].file_id)
            await file.download_to_drive("error.png")
            data = load_data()
            images = data.get('images', {})
            images['error'] = message.photo[-1].file_id
            data['images'] = images
            save_data(data)
            log_action(user_id, "Загрузил error.png")
            admin_states[user_id] = None
            await message.reply_text("✅ Изображение error.png загружено!")
            await admin_images_menu(update, context)
        else:
            await message.reply_text("❌ Пожалуйста, отправьте изображение")
    
    elif state == "upload_image_download":
        if message.photo:
            file = await context.bot.get_file(message.photo[-1].file_id)
            await file.download_to_drive("download.jpg")
            data = load_data()
            images = data.get('images', {})
            images['download'] = message.photo[-1].file_id
            data['images'] = images
            save_data(data)
            log_action(user_id, "Загрузил download.jpg")
            admin_states[user_id] = None
            await message.reply_text("✅ Изображение download.jpg загружено!")
            await admin_images_menu(update, context)
        else:
            await message.reply_text("❌ Пожалуйста, отправьте изображение")
    
    elif state == "import_data":
        if message.document:
            try:
                file = await context.bot.get_file(message.document.file_id)
                file_data = await file.download_as_bytearray()
                import_data = json.loads(file_data.decode('utf-8'))
                
                # Сохраняем импортированные данные
                if save_data(import_data):
                    log_action(user_id, "Импортировал данные")
                    admin_states[user_id] = None
                    await message.reply_text("✅ Данные успешно импортированы!")
                    await admin_export_menu(update, context)
                else:
                    await message.reply_text("❌ Ошибка при импорте данных")
            except Exception as e:
                logger.error(f"Ошибка при импорте: {e}")
                await message.reply_text(f"❌ Ошибка при импорте: {str(e)}")
        else:
            await message.reply_text("❌ Пожалуйста, отправьте JSON файл")
    
    elif state == "broadcast":
        admin_states[user_id] = None
        
        # Получаем всех пользователей
        users = get_all_users()
        
        if not users:
            await message.reply_text(
                "❌ <b>Нет пользователей для рассылки</b>\n\n"
                "Пока нет пользователей, которые взаимодействовали с ботом.",
                parse_mode=ParseMode.HTML
            )
            await admin_broadcast_menu(update, context)
            return
        
        # Отправляем сообщение о начале рассылки
        status_msg = await message.reply_text(
            f"📨 <b>Рассылка начата</b>\n\n"
            f"Пользователей для рассылки: {len(users)}\n"
            f"Отправка сообщений...",
            parse_mode=ParseMode.HTML
        )
        
        sent_count = 0
        failed_count = 0
        
        # Копируем сообщение для рассылки
        broadcast_text = message.text or message.caption or ""
        broadcast_photo = None
        broadcast_document = None
        
        if message.photo:
            broadcast_photo = message.photo[-1].file_id
        elif message.document:
            broadcast_document = message.document.file_id
        
        # Отправляем сообщение всем пользователям
        for user_id_target in users:
            try:
                if broadcast_photo:
                    # Отправляем фото
                    await context.bot.send_photo(
                        chat_id=user_id_target,
                        photo=broadcast_photo,
                        caption=broadcast_text,
                        parse_mode=ParseMode.HTML if broadcast_text else None
                    )
                elif broadcast_document:
                    # Отправляем документ
                    await context.bot.send_document(
                        chat_id=user_id_target,
                        document=broadcast_document,
                        caption=broadcast_text,
                        parse_mode=ParseMode.HTML if broadcast_text else None
                    )
                else:
                    # Отправляем текстовое сообщение
                    await context.bot.send_message(
                        chat_id=user_id_target,
                        text=broadcast_text,
                        parse_mode=ParseMode.HTML
                    )
                sent_count += 1
            except Exception as e:
                failed_count += 1
                logger.warning(f"Не удалось отправить сообщение пользователю {user_id_target}: {e}")
                # Удаляем пользователя из списка, если он заблокировал бота
                if "blocked" in str(e).lower() or "chat not found" in str(e).lower():
                    data = load_data()
                    users_list = data.get('users', [])
                    if user_id_target in users_list:
                        users_list.remove(user_id_target)
                        data['users'] = users_list
                        save_data(data)
        
        # Обновляем статус рассылки
        await status_msg.edit_text(
            f"✅ <b>Рассылка завершена!</b>\n\n"
            f"📊 Статистика:\n"
            f"✅ Отправлено: {sent_count}\n"
            f"❌ Ошибок: {failed_count}\n"
            f"👥 Всего пользователей: {len(users)}",
            parse_mode=ParseMode.HTML
        )
        
        # Возвращаемся в меню рассылки через 3 секунды
        await asyncio.sleep(3)
        await admin_broadcast_menu(update, context)


def main():
    """Основная функция запуска бота"""
    # Исправление для Python 3.10+ на Windows: устанавливаем политику event loop
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    # Создаем новый event loop для главного потока
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    # Получаем токен из переменной окружения
    token = os.getenv("BOT_TOKEN")
    
    # Запасной вариант - токен из кода (если .env файл не создан)
    if not token:
        token = "8554165803:AAGc4bk3_WC0QQSgPjwn7JE9I4NLyEOUDrg"
        logger.warning("BOT_TOKEN не найден в .env файле. Используется токен из кода.")
        logger.info("Рекомендуется создать файл .env с токеном для безопасности.")
    
    if not token:
        logger.error("BOT_TOKEN не установлен! Установите переменную окружения BOT_TOKEN.")
        return
    
    # Создаем приложение с установкой меню команд
    async def post_init(app: Application) -> None:
        """Устанавливает меню команд после инициализации"""
        commands = [
            BotCommand("start", "🚀 Запустить бота"),
            BotCommand("admin", "🔐 Админ-панель (только для администраторов)")
        ]
        try:
            await app.bot.set_my_commands(commands)
            logger.info("Меню команд установлено")
        except Exception as e:
            logger.warning(f"Не удалось установить меню команд (это не критично): {e}")
            logger.info("Бот продолжит работу без установки команд меню")
    
    # Создаем приложение с настройками таймаутов
    from telegram.request import HTTPXRequest
    
    # Создаем request с увеличенными таймаутами
    request = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=60.0,
        write_timeout=60.0,
        connect_timeout=60.0
    )
    
    application = Application.builder().token(token).post_init(post_init).request(request).build()
    
    # Регистрируем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("getid", get_id))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_admin_message))
    
    # Запускаем бота
    logger.info("Бот запущен...")
    logger.info("Попытка подключения к Telegram API...")
    
    try:
        # Запускаем бота
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        logger.error(f"Полный traceback: {traceback.format_exc()}")
        logger.info("Проверьте:")
        logger.info("1. Интернет-соединение")
        logger.info("2. Доступность Telegram API")
        logger.info("3. Правильность токена бота")
        raise


if __name__ == "__main__":
    main()