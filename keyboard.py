from telebot import types

def create_interests_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [
        ["🏛️ Архитектура", "🎨 Искусство", "☕ Кафе и еда"],
        ["🌳 Парки", "📷 Фото-точки", "🎭 История"],
        ["🏞️ Природа", "🎪 Развлечения", "🛍️ Шопинг"],
        ["🏛️ Музеи", "🎭 Театры", "🍽️ Рестораны"],
        ["✅ Готово", "❌ Очистить выбор"]
    ]
    for btn in buttons:
        markup.row(*btn)
    return markup

def create_time_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [["1-2 часа", "3-4 часа"], ["5-6 часов", "24 часа"]]
    for btn in buttons:
        markup.row(*btn)
    return markup

def create_location_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("📍 Отправить местоположение", "🎯 Центр города")
    markup.add("📌 Указать адрес вручную")
    return markup