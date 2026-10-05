import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai import prepare_note


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(
    BASE_DIR / ".env",
    encoding="utf-8-sig",
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
VAULT_SETTING = os.getenv("OBSIDIAN_VAULT", "").strip()

VAULT = Path(VAULT_SETTING)
INBOX = VAULT / "inbox"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

try:
    from voice import transcribe_audio

    VOICE_AVAILABLE = True
    VOICE_IMPORT_ERROR = None

except Exception as error:
    transcribe_audio = None
    VOICE_AVAILABLE = False
    VOICE_IMPORT_ERROR = error

    logging.exception(
        "Не удалось импортировать voice.py: %s",
        type(error).__name__,
    )


def authorized(update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat

    return (
        OWNER_ID > 0
        and user is not None
        and user.id == OWNER_ID
        and chat is not None
        and chat.type == "private"
    )


def safe_title(value: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    title = re.sub(r"\s+", " ", title)
    title = title[:80].strip(" .")

    if not title:
        title = "Заметка"

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    if title.split(".")[0].upper() in reserved:
        title = f"Заметка - {title}"

    return title


def clean_ai_body(body: str, title: str) -> str:
    lines = body.strip().splitlines()

    while lines:
        first = lines[0].strip()
        candidate = re.sub(r"^#{1,6}\s+", "", first)
        candidate = candidate.strip("*_ ").casefold()

        if not first or candidate == title.strip().casefold():
            lines.pop(0)
        else:
            break

    body = "\n".join(lines).strip()
    return re.sub(r"^#\s+", "## ", body, flags=re.MULTILINE)


def original_callout(text: str) -> str:
    quoted = "\n".join(
        f"> {line}" if line else ">"
        for line in text.splitlines()
    )

    return (
        "> [!quote]- Исходная расшифровка\n"
        f"{quoted}\n"
    )


def write_note(title: str, content: str) -> Path:
    INBOX.mkdir(parents=True, exist_ok=True)
    number = 1

    while True:
        suffix = "" if number == 1 else f" ({number})"
        path = INBOX / f"{title}{suffix}.md"

        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(content)
            return path

        except FileExistsError:
            number += 1


def command_text(update: Update) -> str:
    message = update.effective_message

    if message is None or not message.text:
        return ""

    parts = re.split(r"\s", message.text, maxsplit=1)
    return parts[1] if len(parts) == 2 else ""


def list_recent_notes(limit: int = 5) -> list[Path]:
    INBOX.mkdir(parents=True, exist_ok=True)

    return sorted(
        INBOX.glob("*.md"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )[:limit]


def format_note_preview(path: Path, max_chars: int = 1200) -> str:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return "Не удалось прочитать заметку."

    if len(raw) > max_chars:
        return raw[:max_chars].rstrip() + "…"

    return raw


def main_menu_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📥 Последние заметки",
                    callback_data="menu:inbox",
                ),
            ],
            [
                InlineKeyboardButton(
                    "ℹ️ Помощь",
                    callback_data="menu:help",
                ),
            ],
        ]
    )


def recent_notes_markup(notes: list[Path]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []

    for index, note in enumerate(notes, start=1):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"{index}. {note.stem[:40]}",
                    callback_data=f"note:{index}",
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="menu:inbox",
            ),
            InlineKeyboardButton(
                "🏠 Меню",
                callback_data="menu:start",
            ),
        ]
    )

    return InlineKeyboardMarkup(buttons)


def note_back_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ К списку",
                    callback_data="menu:inbox",
                ),
                InlineKeyboardButton(
                    "🏠 Меню",
                    callback_data="menu:start",
                ),
            ]
        ]
    )


async def send_main_menu(message) -> None:
    await message.reply_text(
        "Готово. Отправь текст или голосовое — "
        "бот исправит текст и сохранит заметку в Obsidian.",
        reply_markup=main_menu_markup(),
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if (
        message is None
        or chat is None
        or user is None
        or chat.type != "private"
    ):
        return

    if OWNER_ID == 0:
        await message.reply_text(
            f"Твой Telegram ID: {user.id}\n\n"
            "Укажи этот номер в TELEGRAM_USER_ID "
            "в файле .env и перезапусти бота."
        )
        return

    if authorized(update):
        await send_main_menu(message)


async def show_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if (
        message is not None
        and chat is not None
        and user is not None
        and chat.type == "private"
    ):
        await message.reply_text(
            f"Твой Telegram ID: {user.id}"
        )


async def process_text(
    update: Update,
    text: str,
    mode: str = "note",
) -> None:
    if not authorized(update):
        return

    message = update.effective_message

    if message is None:
        return

    if not text.strip():
        empty_text = (
            "Добавь текст после команды."
            if mode in {"raw", "plan"}
            else "Отправь непустой текст."
        )
        await message.reply_text(empty_text)
        return

    source = text.strip()

    first_line = next(
        line.strip()
        for line in source.splitlines()
        if line.strip()
    )

    body = source
    tags: list[str] = []
    used_ai = False
    status = "без ИИ"

    if mode != "raw":
        await message.reply_text(
            "Исправляю текст и оформляю заметку…"
        )

        try:
            note = await prepare_note(source, mode=mode)
            candidate = clean_ai_body(
                note["body"],
                note["title"],
            )

            if not candidate:
                raise ValueError("ИИ вернул пустой текст")

            first_line = note["title"]
            body = candidate
            tags = note["tags"]
            used_ai = True
            status = "с ИИ"

        except Exception as error:
            logging.exception(
                "Ошибка обработки через Ollama: %s",
                type(error).__name__,
            )
            await message.reply_text(
                "ИИ не ответил. Сохраняю исходный текст."
            )
            status = "без ИИ"

    title = safe_title(first_line)
    now = datetime.now().astimezone()

    content = (
        f"Создано: {now:%d.%m.%Y %H:%M}\n\n"
        f"{body}\n"
    )

    clean_tags: list[str] = []

    for tag in tags:
        tag = re.sub(r"[^\w-]", "", tag.lower())[:30]

        if tag and tag not in clean_tags:
            clean_tags.append(tag)

    if clean_tags:
        content += "\n"
        content += " ".join(
            f"#{tag}"
            for tag in clean_tags[:3]
        )
        content += "\n"

    if used_ai:
        content += "\n---\n\n"
        content += original_callout(source)

    try:
        path = write_note(title, content)

    except OSError:
        logging.exception("Ошибка записи заметки")
        await message.reply_text(
            "Не удалось сохранить заметку. "
            "Проверь ошибку в PowerShell."
        )
        return

    preview = body.strip()

    if len(preview) > 3000:
        preview = preview[:3000] + "…"

    await message.reply_text(
        f"Исправленный текст:\n\n"
        f"{preview}\n\n"
        f"Сохранено в inbox {status}:\n"
        f"{path.name}",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📥 Последние заметки",
                        callback_data="menu:inbox",
                    ),
                    InlineKeyboardButton(
                        "🏠 Меню",
                        callback_data="menu:start",
                    ),
                ]
            ]
        ),
    )


async def save_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if message is None:
        return

    await process_text(
        update,
        message.text or "",
        "note",
    )


async def save_raw(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if authorized(update):
        await process_text(
            update,
            command_text(update),
            "raw",
        )


async def save_plan(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if authorized(update):
        await process_text(
            update,
            command_text(update),
            "plan",
        )


async def save_voice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    print("ПОЛУЧЕНО ГОЛОСОВОЕ СООБЩЕНИЕ", flush=True)

    if not authorized(update):
        print(
            "ГОЛОСОВОЕ ОТКЛОНЕНО: пользователь не авторизован",
            flush=True,
        )
        return

    message = update.effective_message

    if message is None or message.voice is None:
        return

    if not VOICE_AVAILABLE or transcribe_audio is None:
        await message.reply_text(
            "Голосовое распознавание недоступно."
        )
        return

    await message.reply_text(
        "Скачиваю голосовое и распознаю речь…"
    )

    temporary_path = None

    try:
        telegram_file = await context.bot.get_file(
            message.voice.file_id
        )

        with tempfile.NamedTemporaryFile(
            suffix=".ogg",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        await telegram_file.download_to_drive(
            custom_path=temporary_path
        )

        print(f"Файл скачан: {temporary_path}", flush=True)
        print("Запускаю Whisper...", flush=True)

        text = await asyncio.to_thread(
            transcribe_audio,
            temporary_path,
        )

        print(f"Whisper распознал: {text}", flush=True)

        await process_text(update, text, "note")

    except Exception as error:
        logging.exception(
            "Ошибка обработки голосового: %s",
            type(error).__name__,
        )
        await message.reply_text(
            "Не удалось обработать голосовое.\n"
            f"Ошибка: {type(error).__name__}"
        )

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def open_recent_notes(
    query,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    notes = list_recent_notes(limit=5)

    if not notes:
        await query.message.reply_text(
            "В папке inbox пока нет заметок.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🏠 Меню",
                            callback_data="menu:start",
                        )
                    ]
                ]
            ),
        )
        return

    context.user_data["recent_notes"] = [str(note) for note in notes]

    text = "Последние заметки:\n\n" + "\n".join(
        f"{index}. {note.name}"
        for index, note in enumerate(notes, start=1)
    )

    await query.message.reply_text(
        text,
        reply_markup=recent_notes_markup(notes),
    )


async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query

    if query is None:
        return

    await query.answer()

    logging.info(
        "Нажата кнопка: user=%s data=%s",
        update.effective_user.id if update.effective_user else None,
        query.data,
    )

    if not authorized(update):
        await query.message.reply_text("Нет доступа.")
        return

    data = query.data

    if not isinstance(data, str):
        await query.message.reply_text("Некорректная команда кнопки.")
        return

    if data == "menu:start":
        await query.message.reply_text(
            "Главное меню:",
            reply_markup=main_menu_markup(),
        )
        return

    if data == "menu:help":
        await query.message.reply_text(
            "Команды:\n"
            "/plan текст — оформить действия списком.\n"
            "/raw текст — сохранить без ИИ.\n"
            "/id — показать Telegram ID.\n\n"
            "Можно отправлять обычный текст и голосовые сообщения."
        )
        return

    if data == "menu:inbox":
        await open_recent_notes(query, context)
        return

    if data.startswith("note:"):
        raw_index = data.split(":", maxsplit=1)[1]

        if not raw_index.isdigit():
            await query.message.reply_text(
                "Не удалось открыть заметку."
            )
            return

        index = int(raw_index) - 1
        stored = context.user_data.get("recent_notes", [])

        if not isinstance(stored, list) or index < 0 or index >= len(stored):
            await query.message.reply_text(
                "Список заметок устарел. Нажми «Последние заметки» ещё раз.",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "📥 Последние заметки",
                                callback_data="menu:inbox",
                            )
                        ]
                    ]
                ),
            )
            return

        path = Path(stored[index])

        if not path.is_file():
            await query.message.reply_text(
                "Файл заметки не найден.",
                reply_markup=note_back_markup(),
            )
            return

        preview = format_note_preview(path)

        await query.message.reply_text(
            f"📄 {path.name}\n\n{preview}",
            reply_markup=note_back_markup(),
        )
        return

    await query.message.reply_text(
        "Неизвестная команда меню.",
        reply_markup=main_menu_markup(),
    )


async def on_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logging.error(
        "Ошибка обработки обновления: %s",
        type(context.error).__name__,
    )
    logging.exception(
        "Подробности ошибки",
        exc_info=context.error,
    )


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "Заполни TELEGRAM_BOT_TOKEN в .env."
        )

    if not VAULT_SETTING:
        raise SystemExit(
            "Заполни OBSIDIAN_VAULT в .env."
        )

    if not VAULT.is_dir():
        raise SystemExit(
            f"Хранилище не найдено: {VAULT}"
        )

    INBOX.mkdir(parents=True, exist_ok=True)

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", start))
    application.add_handler(CommandHandler("id", show_id))
    application.add_handler(CommandHandler("raw", save_raw))
    application.add_handler(CommandHandler("plan", save_plan))

    application.add_handler(CallbackQueryHandler(callback_router))

    application.add_handler(
        MessageHandler(
            filters.VOICE,
            save_voice,
        )
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            save_note,
        )
    )

    application.add_error_handler(on_error)

    print(f"Папка заметок: {INBOX}", flush=True)
    print("Бот запущен. Остановка: Ctrl+C.", flush=True)

    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()