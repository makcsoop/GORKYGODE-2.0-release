import logging
import os
import json
import time
import re
import urllib3
import requests
import telebot
from enum import Enum
from telebot import types
from keyboard import create_interests_keyboard, create_time_keyboard, create_location_keyboard
from bot_api import create_tour_plan, create_tour_plan_with_data
from api_yandex import create_route_with_custom_style, geocode_to_coordinates
import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

os.makedirs('logs', exist_ok=True)
logging.basicConfig(
    filename='logs/bot.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger("bot")

level_name = os.getenv('LOG_LEVEL', 'INFO').upper()
try:
    logging.getLogger().setLevel(getattr(logging, level_name, logging.INFO))
    logger.info(f"Log level set to {level_name}")
except Exception:
    logger.info("Log level fallback to INFO")

TELEGRAM_BOT_TOKEN = getattr(config, 'TELEGRAM_BOT_TOKEN', '').strip()
if not TELEGRAM_BOT_TOKEN:
    logger.warning('TELEGRAM_BOT_TOKEN не задан в config.py')
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN or 'invalid-token')


class BotState(Enum):
    START = 0
    WAITING_INTERESTS = 1
    ADDING_INTERESTS = 2  
    WAITING_TIME = 3
    WAITING_LOCATION = 4
    WAITING_GEOLOCATION = 5 
    WAITING_MANUAL_ADDRESS = 6 
    READY_TO_BUILD = 7 

user_states = {}
user_data = {}

def start_bot():
    session = requests.Session()
    session.verify = False
    bot.session = session
    while True:
        try:
            logger.info("Запуск бота. Подключение к Telegram API...")
            try:
                bot_info = bot.get_me()
                logger.info(f"Бот подключен: @{bot_info.username}")
            except Exception as e:
                logger.error(f"Ошибка подключения к Telegram API: {e}")
                logger.info("Повторная попытка через 5 секунд...")
                time.sleep(5)
                continue
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                none_stop=True,
                interval=1,
            )
        except requests.exceptions.ReadTimeout:
            logger.warning("Таймаут соединения. Перезапуск через 10 секунд...")
            time.sleep(10)
        except requests.exceptions.ConnectionError:
            logger.warning("Ошибка подключения. Перезапуск через 10 секунд...")
            time.sleep(10)
        except requests.exceptions.SSLError as e:
            logger.error(f"SSL ошибка: {e}. Перезапуск через 15 секунд...")
            time.sleep(15)
        except Exception as e:
            logger.exception(f"Неизвестная ошибка: {e}. Перезапуск через 10 секунд...")
            time.sleep(10)


def set_user_state(user_id, state):
    user_states[user_id] = state


def get_user_state(user_id):
    return user_states.get(user_id, BotState.START)
    

@bot.message_handler(commands=['start', 'hello', 'go'])
def start(message):
    user_id = message.from_user.id
    logger.info(f"/start от пользователя {user_id}")
    set_user_state(user_id, BotState.START)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "🚀 Построить маршрут",
        "ℹ️ Как это работает?",
        "⭐ Примеры маршрутов"
    ]
    markup.add(*[types.KeyboardButton(text) for text in buttons])
    
    bot.send_message(message.chat.id,
                     "🚀 Зовем тебя в путь! \n \n Бот команды Zovozblch на связи!\n \
Мы превращаем данные о достопримечательностях в твои личные впечатления. Наш алгоритм уже ждёт, чтобы создать для тебя уникальный маршрут. \n \n ✨ Для этого нам понадобятся твои параметры: \n \n 📌 Твои интересы: Перечисли всё, что тебя цепляет (стрит-арт, история, архитектура, необычные кафе, лучшие панорамы и т.д.).\n ⏰ Время на прогулку: Сколько часов ты готов посвятить прогулке? \n 🧭 Твоё местоположение: Откуда начнём? (Укажи адрес или координаты). \n 🤩 Готов запустить генератор впечатлений? \
Погружаемся! 🌆", reply_markup=markup)


@bot.message_handler(func=lambda message: message.text == "🚀 Построить маршрут")
def handle_build_route(message):
    user_id = message.from_user.id
    logger.info(f"Пользователь {user_id} начал построение маршрута")
    set_user_state(user_id, BotState.WAITING_INTERESTS)
    if user_id not in user_data:
        user_data[user_id] = {}
    user_data[user_id]['interests'] = []
    
    markup = create_interests_keyboard()
    bot.send_message(
        user_id,
        "📌 Выбери свои интересы! Можешь выбрать несколько категорий.\n\nВыбери первую категорию:",
        reply_markup=markup
    )


@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.WAITING_INTERESTS)
def handle_interests(message):
    user_id = message.from_user.id
    
    if message.text == "✅ Готово":
        # Пользователь готов перейти к следующему шагу
        if not user_data[user_id]['interests']:
            bot.send_message(user_id, "❌ Выбери хотя бы одну категорию интересов!")
            return
            
        set_user_state(user_id, BotState.WAITING_TIME)
        markup = create_time_keyboard()
        bot.send_message(
            user_id,
            f"✅ Отлично! Твои интересы: {', '.join(user_data[user_id]['interests'])}\n\n⏰ Сколько времени у тебя есть на прогулку?",
            reply_markup=markup
        )
    elif message.text == "❌ Очистить выбор":
        # Очищаем выбор
        user_data[user_id]['interests'] = []
        bot.send_message(user_id, "🗑️ Выбор очищен. Выбери свои интересы заново:")
    else:
        interest = parse_single_interest(message.text)
        if interest and interest not in user_data[user_id]['interests']:
            user_data[user_id]['interests'].append(interest)
            logger.info(f"Пользователь {user_id} добавил интерес: {interest}")
            bot.send_message(
                user_id, 
                f"✅ Добавлено: {interest}\n\nТекущие интересы: {', '.join(user_data[user_id]['interests'])}\n\nВыбери еще одну категорию или нажми '✅ Готово' для продолжения:"
            )
        elif interest in user_data[user_id]['interests']:
            bot.send_message(user_id, f"⚠️ {interest} уже добавлен! Выбери другую категорию.")
        else:
            bot.send_message(user_id, "❌ Неизвестная категория. Используй кнопки для выбора.")


@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.WAITING_TIME)
def handle_time(message):
    user_id = message.from_user.id
    
    time_value = parse_time(message.text)
    if time_value:
        user_data[user_id]['time'] = time_value
        logger.info(f"Пользователь {user_id} указал время: {time_value} ч")
        set_user_state(user_id, BotState.WAITING_LOCATION)
        
        markup = create_location_keyboard()
        bot.send_message(
            user_id,
            "🧭 Откуда начнем путь?",
            reply_markup=markup
        )
    else:
        bot.send_message(user_id, "Пожалуйста, укажи время в часах (например: 2 часа, 3.5 часа)")


@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.WAITING_LOCATION)
def handle_location_choice(message):
    user_id = message.from_user.id
    
    if message.text == "📍 Отправить местоположение":
        set_user_state(user_id, BotState.WAITING_GEOLOCATION)
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
        location_button = types.KeyboardButton("📍 Поделиться местоположением", request_location=True)
        cancel_button = types.KeyboardButton("❌ Отмена")
        markup.add(location_button)
        markup.add(cancel_button)
        logger.info(f"Запрошена геолокация для пользователя {user_id}")
        bot.send_message(
            user_id,
            "📍 Нажми кнопку ниже, чтобы поделиться своим местоположением:",
            reply_markup=markup
        )
    elif message.text == "🎯 Центр города":
        user_data[user_id]['location'] = "Нижний Новгород, площадь Минина и Пожарского"
        set_user_state(user_id, BotState.READY_TO_BUILD)
        show_confirmation(user_id)
    elif message.text == "📌 Указать адрес вручную":
        set_user_state(user_id, BotState.WAITING_MANUAL_ADDRESS)
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("❌ Отмена")
        bot.send_message(
            user_id,
            "📌 Введи адрес, откуда начнем маршрут:\n\nНапример:\n• Нижний Новгород, площадь Минина и Пожарского\n• Нижний Новгород, ул. Большая Покровская, 1\n• Нижний Новгород, Московский Вокзал",
            reply_markup=markup
        )
    else:
        bot.send_message(user_id, "Пожалуйста, выбери один из вариантов выше ⬆️")


@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.WAITING_GEOLOCATION)
def handle_geolocation(message):
    user_id = message.from_user.id
    logger.debug(f"Геолокация: состояние WAITING_GEOLOCATION от {user_id}")
    
    if message.text == "📍 Поделиться местоположением":
        logger.debug("Пользователь нажал кнопку геолокации")
        bot.send_message(user_id, "📍 Нажми на кнопку '📍 Поделиться местоположением' еще раз, чтобы отправить геолокацию")
    elif message.text == "❌ Отмена":
        logger.info(f"Пользователь {user_id} отменил отправку геолокации")
        set_user_state(user_id, BotState.WAITING_LOCATION)
        markup = create_location_keyboard()
        bot.send_message(
            user_id,
            "🧭 Откуда начнем путь?",
            reply_markup=markup
        )
    else:
        logger.debug(f"Неожиданное сообщение при ожидании геолокации: {message.text}")
        bot.send_message(user_id, "📍 Пожалуйста, поделись местоположением или нажми 'Отмена'")


@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.WAITING_MANUAL_ADDRESS)
def handle_manual_address(message):
    user_id = message.from_user.id
    
    if message.text == "❌ Отмена":
        set_user_state(user_id, BotState.WAITING_LOCATION)
        markup = create_location_keyboard()
        bot.send_message(
            user_id,
            "🧭 Откуда начнем путь?",
            reply_markup=markup
        )
    else:
        user_data[user_id]['location'] = message.text
        logger.info(f"Пользователь {user_id} указал адрес вручную: {message.text}")
        set_user_state(user_id, BotState.READY_TO_BUILD)
        show_confirmation(user_id)


def parse_single_interest(text):
    interests_mapping = {
        '🏛️ Архитектура': 'архитектура',
        '🎨 Искусство': 'искусство', 
        '☕ Кафе и еда': 'кафе и еда',
        '🌳 Парки': 'парки',
        '📷 Фото-точки': 'фото-точки',
        '🎭 История': 'история',
        '🏞️ Природа': 'природа',
        '🎪 Развлечения': 'развлечения',
        '🛍️ Шопинг': 'шопинг',
        '🏛️ Музеи': 'музеи',
        '🎭 Театры': 'театры',
        '🍽️ Рестораны': 'рестораны'
    }
    
    return interests_mapping.get(text.strip(), None)

def parse_interests(text):
    interests_mapping = {
        '🏛️': 'архитектура', '🎨': 'искусство', '☕': 'кафе',
        '🌳': 'парки', '📷': 'фото', '🎭': 'история'
    }
    
    interests = []
    for emoji, interest in interests_mapping.items():
        if emoji in text:
            interests.append(interest)
    
    # Если нет эмодзи, пытаемся распарсить текст
    if not interests:
        interests = [interest.strip() for interest in text.split(',')]
    
    return interests


def parse_time(text):
    numbers = re.findall(r'\d+\.?\d*', text)
    if numbers:
        return float(numbers[0])
    return None


def show_confirmation(user_id):
    data = user_data.get(user_id, {})
    
    location_str = data.get('location')
    if isinstance(location_str, dict):
        location_str = f"📍 Координаты: {location_str['lat']:.6f}, {location_str['lon']:.6f}"
    else:
        location_str = f"📍 Адрес: {location_str}"
    
    confirmation_text = f"""
✅ Отлично! Проверь данные:

📌 Интересы: {', '.join(data.get('interests', []))}
⏰ Время: {data.get('time')} часа
🧭 Старт: {location_str}

Всё верно?
            """
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("✅ Да, строить маршрут!", "❌ Нет, изменить данные")
    
    bot.send_message(user_id, confirmation_text, reply_markup=markup)


def _load_texts():
    try:
        with open('texts.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.exception(f"Не удалось загрузить texts.json: {e}")
        return {}

_TEXTS_CACHE = _load_texts()

def get_how_it_works_info():
    return _TEXTS_CACHE.get('how_it_works', 'Информация временно недоступна')


def get_examples_info():
    return _TEXTS_CACHE.get('examples', 'Информация временно недоступна')


def generate_yandex_maps_link(places, start_location):
    try:
        # Формируем координаты точек маршрута
        points = []
        logger.debug(f"Генерация ссылки Яндекс.Карт. start={start_location}")
        # Добавляем стартовую точку
        if isinstance(start_location, dict):
            points.append(f"{start_location['lat']},{start_location['lon']}")
        else:
            if(start_location == "Нижний Новгород, площадь Минина и Пожарского"):
                points.append("56.3287,44.0020")
            else:
                if(geocode_to_coordinates(start_location)):
                    start_location = geocode_to_coordinates(start_location)
                    points.append(f"{start_location['lat']},{start_location['lon']}")
                    logger.debug(f"Геокод преобразован: {start_location}")
                else:
                    points.append("56.3287,44.0020")
            
        # Добавляем точки маршрута (строго в переданном порядке)
        for place in places:
            if 'lat' in place and 'lon' in place:
                points.append(f"{place['lat']},{place['lon']}")
        
        # Создаем ссылку на маршрут
        points_str = "~".join(points)
        yandex_link = f"https://yandex.ru/maps/?rtext={points_str}&rtt=pd"
        
        return yandex_link
    except Exception as e:
        logger.exception(f"Ошибка генерации ссылки Яндекс.Карт: {e}")
        return "https://yandex.ru/maps/nizhniy-novgorod/"


def generate_static_map_image(places, start_location):
    try:
        YANDEX_MAPS_API_KEY = os.getenv('YANDEX_MAPS_API_KEY', '').strip()
        
        points = []
        
        if isinstance(start_location, dict):
            start_lat, start_lon = start_location['lat'], start_location['lon']
        else:
            start_lat, start_lon = 56.3287, 44.0020
        
        points.append(f"{start_lat},{start_lon}")
        
        for place in places:
            if 'lat' in place and 'lon' in place:
                points.append(f"{place['lat']},{place['lon']}")
        
        points_str = "~".join(points)
        static_map_url = f"https://static-maps.yandex.ru/1.x/?l=map&pt={points_str}&size=600,400&z=13"
        
        return static_map_url
    except Exception as e:
        logger.exception(f"Ошибка генерации статичной карты: {e}")
        return None


def _extract_ordered_titles_from_plan(plan_text: str):
    try:
        lines = [l.strip() for l in plan_text.splitlines() if l.strip()]
        titles = []
        import re
        num_re = re.compile(r"^(\d+\D{0,3})\s*(.+)$")
        for i, line in enumerate(lines):
            m = num_re.match(line)
            if m:
                title = m.group(2).strip()
                titles.append(title)
        if not titles:
            for i, line in enumerate(lines):
                if line.startswith('📍 Адрес') and i > 0:
                    titles.append(lines[i-1])
        return [t for t in titles if t]
    except Exception:
        return []


def _filter_and_reorder_places_by_titles(places, ordered_titles):
    if not ordered_titles:
        return places
    title_to_place = {p.get('title'): p for p in places if p.get('title')}
    filtered = [title_to_place[t] for t in ordered_titles if t in title_to_place]
    return filtered


def send_route_with_maps(user_id, plan_text, places, start_location, type=None):
    try:
        bot.send_message(user_id, plan_text)
        logger.info(f"Отправлен план пользователю {user_id}")
        
        logger.debug(f"Координаты начальной точки: {start_location}")

        if type:
            tmp = start_location.split(", ")
            start_location = {'lat': float(tmp[0]), 'lon': float(tmp[1])}
            logger.debug(f"Преобразованные координаты начальной точки: {start_location}")
       
        ordered_titles = _extract_ordered_titles_from_plan(plan_text)
        places_for_maps = _filter_and_reorder_places_by_titles(places, ordered_titles)
        if not places_for_maps:
            places_for_maps = places

        yandex_link = generate_yandex_maps_link(places_for_maps, start_location)
        static_map_url = create_route_with_custom_style(places_for_maps, start_location)
        logger.debug(f"Static map URL: {static_map_url}")
        template = _TEXTS_CACHE.get('maps_message', '')
        maps_message = template.format(yandex_link=yandex_link)
        if static_map_url:
            try:
                bot.send_photo(user_id, static_map_url, caption=maps_message, parse_mode='HTML')
            except Exception as e:
                logger.exception(f"Ошибка отправки карты пользователю {user_id}: {e}")
                bot.send_message(user_id, maps_message, parse_mode='HTML')
        else:
            bot.send_message(user_id, maps_message, parse_mode='HTML')
            
    except Exception as e:
        logger.exception(f"Ошибка отправки маршрута с картой пользователю {user_id}: {e}")



@bot.message_handler(func=lambda message: get_user_state(message.from_user.id) == BotState.READY_TO_BUILD)
def handle_confirmation(message):
    user_id = message.from_user.id
    
    if message.text == "✅ Да, строить маршрут!":
        remove_keyboard = types.ReplyKeyboardRemove()
        bot.send_message(user_id, "🔄 Создаю твой уникальный маршрут... Это может занять несколько минут.", reply_markup=remove_keyboard)
        logger.info(f"Пользователь {user_id} подтвердил построение маршрута")
        
        data = user_data.get(user_id, {})
        location_data = data.get('location')
        if isinstance(location_data, dict):
            location_str = f"{location_data['lon']}, {location_data['lat']}"
        else:
            location_str = location_data
        logger.debug(f"Данные для маршрута {user_id}: {data}")
        type = None
        try:
            if(55.307892 < float(location_data['lat']) < 57.307892):
                location_str = f"{location_data['lat']}, {location_data['lon']}"
                type = True
        except:
            pass
        
        try:
            plan, places, user_coords = create_tour_plan_with_data(
                ', '.join(data.get('interests', [])), 
                data.get('time'), 
                location_str
            )
            logger.info(f"Маршрут для {user_id} создан. Старт: {location_str}, точек: {len(places)}")
            send_route_with_maps(user_id, plan, places, location_str, type)
            
        except Exception as e:
            bot.send_message(user_id, f"❌ Произошла ошибка при создании маршрута: {e}")
            logger.exception(f"Ошибка создания маршрута для {user_id}: {e}")
        
        set_user_state(user_id, BotState.START)
        start_handler(message)
    else:
        set_user_state(user_id, BotState.START)
        start_handler(message)


def start_handler(message):
    user_id = message.from_user.id
    set_user_state(user_id, BotState.START)
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        "🚀 Построить маршрут",
        "ℹ️ Как это работает?",
        "⭐ Примеры маршрутов"
    ]
    markup.add(*[types.KeyboardButton(text) for text in buttons])
    
    bot.send_message(user_id, "🏠 Главное меню", reply_markup=markup)


@bot.message_handler(commands=['cancel', 'stop'])
def cancel_handler(message):
    user_id = message.from_user.id
    set_user_state(user_id, BotState.START)
    bot.send_message(user_id, "Диалог прерван. Начнем заново /start")


@bot.message_handler(commands=['restart'])
def restart_handler(message):
    user_id = message.from_user.id
    if user_id in user_data:
        del user_data[user_id]
    set_user_state(user_id, BotState.START)
    start_handler(message)


@bot.message_handler(commands=['data'])
def show_data_handler(message):
    user_id = message.from_user.id
    data = user_data.get(user_id, {})
    bot.send_message(user_id, f"Текущие данные: {data}")


@bot.message_handler(content_types=['location'])
def handle_location_message(message):
    user_id = message.from_user.id
    current_state = get_user_state(user_id)
    
    logger.info(f"Геолокация от {user_id} в состоянии {current_state}: lat={message.location.latitude}, lon={message.location.longitude}")
    
    if current_state == BotState.WAITING_GEOLOCATION:
        user_data[user_id]['location'] = {
            'lat': message.location.latitude,
            'lon': message.location.longitude
        }
        set_user_state(user_id, BotState.READY_TO_BUILD)
        bot.send_message(user_id, "✅ Местоположение получено!")
        show_confirmation(user_id)
    else:
        bot.send_message(user_id, "📍 Сначала выбери 'Отправить местоположение' в меню выше")


@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    """Обработчик всех остальных сообщений"""
    user_id = message.from_user.id
    current_state = get_user_state(user_id)
    
    if current_state == BotState.START:
        if message.text == "ℹ️ Как это работает?":
            info_text = get_how_it_works_info()
            bot.send_message(user_id, info_text, parse_mode='HTML')
        elif message.text == "⭐ Примеры маршрутов":
            examples_text = get_examples_info()
            bot.send_message(user_id, examples_text, parse_mode='HTML')
        else:
            bot.send_message(user_id, "Используй кнопки для навигации или нажми '🚀 Построить маршрут' чтобы начать!")
    else:
        bot.send_message(user_id, "Пожалуйста, следуй инструкциям выше ⬆️")


if __name__ == "__main__":
    logger.info("Bot start working...")
    start_bot()