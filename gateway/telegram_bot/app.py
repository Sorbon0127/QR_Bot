import os
import logging
import httpx
from io import BytesIO
from datetime import datetime

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
USERS_SERVICE_URL = os.getenv("USERS_SERVICE_URL", "http://users_service:8000")

ADMIN_IDS = [5502429477]


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def is_allowed(user_id: int) -> bool:
    if is_admin(user_id):
        return True

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{USERS_SERVICE_URL}/tg_users", timeout=5.0)
            resp.raise_for_status()
            users = resp.json()
        except Exception:
            return False

    return any(
        u.get("telegram_id") == user_id and u.get("allowed", True)
        for u in users
    )


def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = user.id
        if not is_admin(user_id):
            if update.message:
                await update.message.reply_text("❌ Доступ запрещён. Команда только для администратора.")
            return
        return await handler(update, context)

    return wrapper


def allowed_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return
        user_id = user.id

        if not await is_allowed(user_id):
            if update.message:
                await update.message.reply_text("❌ У вас нет доступа. Обратитесь к администратору.")
            return
        return await handler(update, context)

    return wrapper


async def reject_unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return

    user_id = user.id

    if is_admin(user_id):
        return

    if await is_allowed(user_id):
        return

    return


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    user_id = user.id

    if not await is_allowed(user_id):
        return

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{USERS_SERVICE_URL}/stats", timeout=5.0)
            resp.raise_for_status()
            stats = resp.json()
        except Exception:
            stats = None

    if stats:
        status = (
            f"🎭 Гостей: {stats['total_guests']} | "
            f"✅ Отмечено: {stats['total_scanned']}"
        )
    else:
        status = "❌ Не удалось получить статистику."

    if is_admin(user_id):
        keyboard = [
            ["📤 Загрузить список 2.0", "📱 Сканировать QR"],
            ["👤 Отметить по имени", "🔍 Найти гостя"],
            ["➕ Добавить гостя", "📊 Статистика"],
            ["📋 Показать гостей", "🧹 Очистить данные"],
            ["📦 Экспорт отчёта", "👥 Пользователи (TG ID)"],
            ["👑 Панель управления"],
        ]
    else:
        keyboard = [
            ["📤 Загрузить список", "📱 Сканировать QR"],
            ["👤 Отметить по имени", "🔍 Найти гостя"],
            ["➕ Добавить гостя", "📊 Статистика"],
            ["📋 Показать гостей", "🧹 Очистить данные"],
        ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    text = (
        "🤖 Бот для проверки QR-кодов (гости)\n\n"
        f"{status}\n\n"
        "Основные действия:\n"
        "📱 Кнопка «Сканировать QR» — отметка по коду\n"
        "🔍 Кнопка «Найти гостя» — поиск по имени\n"
        "📊 Кнопка «Статистика» — обновить статистику\n"
    )

    if is_admin(user_id):
        text += (
            "\n👑 Админ-команды:\n"
            "/add_guest CODE ФИО - добавить гостя\n"
            "/add_tg_user @username ИМЯ - добавить пользователя по нику\n"
            "/export - выгрузить отчёты\n"
            "/clear_all - очистить базу\n"
        )

    await update.message.reply_text(text, reply_markup=reply_markup)


@admin_only
async def add_guest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /add_guest CODE ФИО")
        return

    code = context.args[0]
    name = " ".join(context.args[1:])

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{USERS_SERVICE_URL}/guests",
                json={"code": code, "name": name},
                timeout=5.0,
            )
            if resp.status_code == 400:
                await update.message.reply_text("❌ Гость с таким кодом уже существует.")
                return
            resp.raise_for_status()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при добавлении гостя: {e}")
            return

    await update.message.reply_text(f"✅ Гость добавлен:\nКод: {code}\nИмя: {name}")


@admin_only
async def add_tg_user_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /add_tg_user @username ИМЯ")
        return

    username_raw = context.args[0]
    if not username_raw.startswith("@"):
        await update.message.reply_text("❌ Первый аргумент должен быть ником, например: @username")
        return
    username = username_raw.lstrip("@")

    name = " ".join(context.args[1:])

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{USERS_SERVICE_URL}/tg_users",
                json={
                    "telegram_id": None,
                    "username": username,
                    "name": name,
                    "allowed": True,
                },
                timeout=5.0,
            )
            if resp.status_code not in (200, 201):
                await update.message.reply_text(
                    f"❌ Ошибка при добавлении пользователя. Код: {resp.status_code}"
                )
                return
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка подключения: {e}")
            return

    await update.message.reply_text(
        f"✅ Пользователь добавлен.\nUsername: @{username}\nИмя: {name}"
    )


@allowed_only
async def mark(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /mark CODE")
        return

    code = context.args[0]

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{USERS_SERVICE_URL}/mark",
                json={"code": code, "method": "manual"},
                timeout=5.0,
            )
            if resp.status_code == 404:
                await update.message.reply_text("❌ Код не найден в системе.")
                return
            resp.raise_for_status()
            body = resp.json()
            data = body["data"]
            already = body.get("already_marked", False)
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка при отметке: {e}")
            return

    if already:
        await update.message.reply_text("⚠️ Гость уже пришёл, повторная отметка не требуется.")
        return

    text = (
        "✅ Отметка сохранена\n"
        f"Код: {data['code']}\n"
        f"Имя: {data['name']}\n"
        f"Время: {data['timestamp']}\n"
        f"Метод: {data['method']}\n"
    )
    await update.message.reply_text(text)


@allowed_only
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /find часть_имени")
        return

    query_text = " ".join(context.args)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{USERS_SERVICE_URL}/search",
                params={"query": query_text},
                timeout=5.0,
            )
            resp.raise_for_status()
            results = resp.json()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка поиска: {e}")
            return

    if not results:
        await update.message.reply_text("❌ Никого не нашли.")
        return

    if len(results) == 1:
        r = results[0]
        if r.get("scanned"):
            await update.message.reply_text("⚠️ Гость уже пришёл.")
            return

        async with httpx.AsyncClient() as client:
            try:
                mark_resp = await client.post(
                    f"{USERS_SERVICE_URL}/mark",
                    json={"code": r["code"], "method": "search"},
                    timeout=5.0,
                )
                mark_resp.raise_for_status()
                body = mark_resp.json()
                data = body["data"]
                already = body.get("already_marked", False)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отметки: {e}")
                return

        if already:
            await update.message.reply_text("⚠️ Гость уже пришёл.")
            return

        text = (
            "✅ Отметка по поиску\n"
            f"Код: {data['code']}\n"
            f"Имя: {data['name']}\n"
            f"Время: {data['timestamp']}\n"
        )
        await update.message.reply_text(text)
        return

    keyboard = []
    for r in results[:10]:
        status = "✅" if r["scanned"] else "⏳"
        text_btn = f"{status} {r['name']} ({r['code']})"
        keyboard.append(
            [InlineKeyboardButton(text_btn, callback_data=f"mark_{r['code']}")]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🔍 Найдено, выберите гостя:", reply_markup=reply_markup)


@admin_only
async def clear_all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, очистить", callback_data="confirm_clear"),
            InlineKeyboardButton("❌ Отмена", callback_data="cancel_clear"),
        ]
    ])
    await update.message.reply_text(
        "⚠️ Вы уверены, что хотите полностью очистить базу (гости и отметки)?",
        reply_markup=keyboard,
    )


async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "confirm_clear":
        if not is_admin(query.from_user.id):
            await query.edit_message_text("❌ Только администратор может очищать базу.")
            return

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.delete(f"{USERS_SERVICE_URL}/clear_all", timeout=10.0)
                if resp.status_code != 200:
                    await query.edit_message_text(
                        f"❌ Ошибка очистки: {resp.status_code}"
                    )
                    return
                data_resp = resp.json()
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка подключения: {e}")
                return

        await query.edit_message_text(
            f"✅ База очищена.\n"
            f"Удалено гостей: {data_resp.get('deleted_guests', 0)}\n"
            f"Удалено отметок: {data_resp.get('deleted_marks', 0)}"
        )

        async with httpx.AsyncClient() as client:
            try:
                export_resp = await client.get(f"{USERS_SERVICE_URL}/export", timeout=30.0)
                export_resp.raise_for_status()
                export_data = export_resp.json()
            except Exception:
                return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        csv_content = "\ufeff" + export_data["csv"]
        csv_file = BytesIO(csv_content.encode("utf-8-sig"))
        csv_file.name = f"stat_{timestamp}.csv"

        txt_file = BytesIO(export_data["txt"].encode("utf-8"))
        txt_file.name = f"stat_{timestamp}.txt"

        admin_id = ADMIN_IDS[0]

        await context.bot.send_document(
            chat_id=admin_id,
            document=csv_file,
            filename=csv_file.name,
            caption="📊 Итоговый CSV-отчёт после очистки",
        )

        await context.bot.send_document(
            chat_id=admin_id,
            document=txt_file,
            filename=txt_file.name,
            caption="📝 Итоговый текстовый отчёт после очистки",
        )
        return

    if data == "cancel_clear":
        await query.edit_message_text("Отмена очистки базы.")
        return

    if data.startswith("mark_"):
        if not await is_allowed(query.from_user.id):
            await query.edit_message_text("❌ У вас нет доступа.")
            return

        code = data[5:]

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{USERS_SERVICE_URL}/mark",
                    json={"code": code, "method": "search"},
                    timeout=5.0,
                )
                if resp.status_code == 404:
                    await query.edit_message_text("❌ Код не найден.")
                    return
                resp.raise_for_status()
                body = resp.json()
                data_resp = body["data"]
                already = body.get("already_marked", False)
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка отметки: {e}")
                return

        if already:
            await query.edit_message_text("⚠️ Гость уже пришёл.")
            return

        text = (
            "✅ Отметка сохранена\n"
            f"Код: {data_resp['code']}\n"
            f"Имя: {data_resp['name']}\n"
            f"Время: {data_resp['timestamp']}\n"
        )
        await query.edit_message_text(text)


@allowed_only
async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    if not document:
        return

    filename = document.file_name.lower()
    await update.message.reply_text(f"Получен файл: {filename}")

    file = await context.bot.get_file(document.file_id)
    file_bytes = await file.download_as_bytearray()

    await update.message.reply_text(f"Размер файла: {len(file_bytes)} байт")

    if not (filename.endswith(".xlsx") or filename.endswith(".xls")):
        await update.message.reply_text("❌ Это не Excel-файл (.xlsx/.xls).")
        return

    file_obj = BytesIO(file_bytes)

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{USERS_SERVICE_URL}/import_excel",
                files={
                    "file": (
                        document.file_name,
                        file_obj,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                timeout=60.0,
            )
            if resp.status_code != 200:
                await update.message.reply_text(
                    "❌ Ошибка импорта Excel.\n"
                    f"Код: {resp.status_code}\n"
                    f"Текст: {resp.text[:300]}"
                )
                return

            res = resp.json()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка сервера при импорте: {e}")
            return

    await update.message.reply_text(
        f"✅ Импорт завершён.\nДобавлено гостей: {res.get('added_guests', 0)}"
    )


@admin_only
async def send_reports(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{USERS_SERVICE_URL}/export", timeout=30.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка получения отчёта: {e}")
            return

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    csv_content = "\ufeff" + data["csv"]
    csv_file = BytesIO(csv_content.encode("utf-8-sig"))
    csv_file.name = f"stat_{timestamp}.csv"

    await update.message.reply_document(
        document=csv_file,
        filename=csv_file.name,
        caption="📊 CSV-отчёт",
    )

    txt_file = BytesIO(data["txt"].encode("utf-8"))
    txt_file.name = f"stat_{timestamp}.txt"

    await update.message.reply_document(
        document=txt_file,
        filename=txt_file.name,
        caption="📝 Текстовый отчёт",
    )


@allowed_only
async def show_guests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{USERS_SERVICE_URL}/guests", timeout=10.0)
            resp.raise_for_status()
            guests = resp.json()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка получения списка гостей: {e}")
            return

    if not guests:
        await update.message.reply_text("Список гостей пуст.")
        return

    keyboard = []
    for g in guests[:50]:
        text_btn = f"{g['name']} ({g['code']})"
        keyboard.append(
            [InlineKeyboardButton(text_btn, callback_data=f"mark_{g['code']}")]
        )

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📋 Все гости, выберите кого отметить:", reply_markup=reply_markup)


async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user = update.effective_user
    if not user:
        return
    user_id = user.id

    if text == "📊 Статистика":
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        return await start(update, context)

    elif text == "🔍 Найти гостя":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["search_mode"] = True
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        await update.message.reply_text("Введите часть имени гостя:")
        return

    elif text == "📱 Сканировать QR":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["mark_mode"] = True
        context.user_data["search_mode"] = False
        context.user_data["add_guest_mode"] = False
        await update.message.reply_text("Отправьте код из QR:")
        return

    elif text == "👤 Отметить по имени":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["search_mode"] = True
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        await update.message.reply_text("Введите часть имени гостя:")
        return

    elif text == "📤 Загрузить список":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        await update.message.reply_text(
            "Отправьте Excel-файл (.xlsx/.xls) со столбцами: Код, ФИО"
        )
        return

    elif text == "🧹 Очистить данные":
        if not is_admin(user_id):
            await update.message.reply_text("❌ Только для администратора.")
            return
        return await clear_all_cmd(update, context)

    elif text == "➕ Добавить гостя":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = True
        await update.message.reply_text(
            "Отправьте ФИО гостя одной строкой:\n\nПример:\nИванов Иван"
        )
        return

    elif text == "📋 Показать гостей":
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        return await show_guests(update, context)

    elif text == "📦 Экспорт отчёта":
        if not is_admin(user_id):
            await update.message.reply_text("❌ Только для администратора.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        return await send_reports(update, context)

    elif text == "👥 Пользователи (TG ID)":
        if not is_admin(user_id):
            await update.message.reply_text("❌ Только для администратора.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        await update.message.reply_text(
            "👥 Управление пользователями:\n"
            "Добавить: /add_tg_user @username ИМЯ"
        )
        return

    elif text == "👑 Панель управления":
        if not is_admin(user_id):
            await update.message.reply_text("❌ Только для администратора.")
            return
        context.user_data["search_mode"] = False
        context.user_data["mark_mode"] = False
        context.user_data["add_guest_mode"] = False
        keyboard = [
            ["📊 Статистика", "🔍 Найти гостя"],
            ["📤 Загрузить список", "🧹 Очистить данные"],
            ["📋 Показать гостей", "📦 Экспорт отчёта"],
            ["👥 Пользователи (TG ID)"],
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text("👑 Админ-панель:", reply_markup=reply_markup)
        return

    # Режим добавления одного гостя (только ФИО)
    if context.user_data.get("add_guest_mode"):
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return

        name = text.strip()
        if not name:
            await update.message.reply_text("Имя не должно быть пустым. Отправьте ФИО гостя:")
            return

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{USERS_SERVICE_URL}/guests",
                    json={"code": "", "name": name},
                    timeout=5.0,
                )
                if resp.status_code == 400:
                    await update.message.reply_text("❌ Гость с таким кодом уже существует.")
                    return
                resp.raise_for_status()
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка при добавлении гостя: {e}")
                return

        await update.message.reply_text(f"✅ Гость добавлен:\nИмя: {name}")
        return

    # Режим поиска по имени
    if context.user_data.get("search_mode"):
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return

        query_text = text.strip()
        if not query_text:
            await update.message.reply_text("Введите часть имени гостя:")
            return

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(
                    f"{USERS_SERVICE_URL}/search",
                    params={"query": query_text},
                    timeout=5.0,
                )
                resp.raise_for_status()
                results = resp.json()
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка поиска: {e}")
                return

        if not results:
            await update.message.reply_text("❌ Никого не нашли.")
            return

        if len(results) == 1:
            r = results[0]
            if r.get("scanned"):
                await update.message.reply_text("⚠️ Гость уже пришёл.")
                return

            async with httpx.AsyncClient() as client:
                try:
                    mark_resp = await client.post(
                        f"{USERS_SERVICE_URL}/mark",
                        json={"code": r["code"], "method": "search"},
                        timeout=5.0,
                    )
                    mark_resp.raise_for_status()
                    body = mark_resp.json()
                    data = body["data"]
                    already = body.get("already_marked", False)
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка отметки: {e}")
                    return

            if already:
                await update.message.reply_text("⚠️ Гость уже пришёл.")
                return

            text_resp = (
                "✅ Отметка по поиску\n"
                f"Код: {data['code']}\n"
                f"Имя: {data['name']}\n"
                f"Время: {data['timestamp']}\n"
            )
            await update.message.reply_text(text_resp)
            return

        keyboard = []
        for r in results[:10]:
            status = "✅" if r["scanned"] else "⏳"
            text_btn = f"{status} {r['name']} ({r['code']})"
            keyboard.append(
                [InlineKeyboardButton(text_btn, callback_data=f"mark_{r['code']}")]
            )

        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("🔍 Найдено, выберите гостя:", reply_markup=reply_markup)
        return

    # Режим отметки по коду
    if context.user_data.get("mark_mode"):
        if not await is_allowed(user_id):
            await update.message.reply_text("❌ Нет доступа.")
            return

        code = text.strip()
        if not code:
            await update.message.reply_text("Отправьте код из QR:")
            return

        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{USERS_SERVICE_URL}/mark",
                    json={"code": code, "method": "manual"},
                    timeout=5.0,
                )
                if resp.status_code == 404:
                    await update.message.reply_text("❌ Код не найден в системе.")
                    return
                resp.raise_for_status()
                body = resp.json()
                data = body["data"]
                already = body.get("already_marked", False)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка отметки: {e}")
                return

        if already:
            await update.message.reply_text("⚠️ Гость уже пришёл.")
            return

        text_resp = (
            "✅ Отметка сохранена\n"
            f"Код: {data['code']}\n"
            f"Имя: {data['name']}\n"
            f"Время: {data['timestamp']}\n"
            f"Метод: {data['method']}\n"
        )
        await update.message.reply_text(text_resp)
        return


def main():
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN не задан")

    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(
        MessageHandler(filters.ALL, reject_unauthorized),
        group=0,
    )

    application.add_handler(CommandHandler("start", start), group=1)
    application.add_handler(CommandHandler("mark", mark), group=1)
    application.add_handler(CommandHandler("add_guest", add_guest_cmd), group=1)
    application.add_handler(CommandHandler("add_tg_user", add_tg_user_cmd), group=1)
    application.add_handler(CommandHandler("find", find), group=1)
    application.add_handler(CommandHandler("export", send_reports), group=1)
    application.add_handler(CommandHandler("clear_all", clear_all_cmd), group=1)
    application.add_handler(CallbackQueryHandler(button), group=1)
    application.add_handler(MessageHandler(filters.Document.ALL, handle_file), group=1)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu),
        group=1,
    )

    application.run_polling()


if __name__ == "__main__":
    main()
