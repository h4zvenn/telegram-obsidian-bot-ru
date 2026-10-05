import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ai import prepare_note


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
VAULT_SETTING = os.getenv("OBSIDIAN_VAULT", "").strip()

VAULT = Path(VAULT_SETTING)
INBOX = VAULT / "inbox"

logging.basicConfig(
    level=logging.WARNING,
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

    logging.warning(
        "Распознавание голоса недоступно: %s",
        type(error).__name__,
    )


def authorized(update: Update) -> bool:
    return (
        OWNER_ID > 0
        and update.effective_user is not None
        and update.effective_user.id == OWNER_ID
        and update.effective_chat is not None
        and update.effective_chat.type == "private"
    )


def safe_title(value: str) -> str:
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value)
    title = re.sub(r"\s+", " ", title)
    title = title[:80].strip(" .") or "Заметка"

    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹",
        "COM²",
        "COM³",
        "LPT¹",
        "LPT²",
        "LPT³",
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
        "> [!quote]- Исходное сообщение\n"
        f"{quoted}\n"
    )


def write_note(title: str, content: str) -> Path:
    INBOX.mkdir(exist_ok=True)
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


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is None
        or update.effective_chat.type != "private"
        or update.effective_user is None
        or update.effective_message is None
    ):
        return

    if OWNER_ID == 0:
        await update.effective_message.reply_text(
            f"Твой Telegram ID: {update.effective_user.id}\n\n"
            "Укажи его в TELEGRAM_USER_ID в .env "
            "и перезапусти бота."
        )
        return

    if authorized(update):
        await update.effective_message.reply_text(
            "Команды:\n"
            "/plan текст — оформить действия списком.\n"
            "/raw текст — сохранить без ИИ.\n"
            "/id — показать Telegram ID.\n\n"
            "Обычный текст обрабатывается через ИИ."
        )


async def show_id(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if (
        update.effective_chat is not None
        and update.effective_chat.type == "private"
        and update.effective_user is not None
        and update.effective_message is not None
    ):
        await update.effective_message.reply_text(
            f"Твой Telegram ID: {update.effective_user.id}"
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
        await message.reply_text(
            "Добавь текст после команды."
            if mode in {"raw", "plan"}
            else "Отправь непустой текст."
        )
        return

    source = text if mode == "raw" else text.strip()

    first_line = next(
        line.strip()
        for line in source.splitlines()
        if line.strip()
    )

    body = source
    tags = []
    used_ai = False
    status = "без ИИ"

    if mode != "raw":
        await message.reply_text("Оформляю заметку…")

        try:
            note = await prepare_note(source, mode=mode)
            candidate = clean_ai_body(
                note["body"],
                note["title"],
            )

            if not candidate:
                raise ValueError("Пустое содержимое")

            first_line = note["title"]
            body = candidate
            tags = note["tags"]
            used_ai = True
            status = "с ИИ"

        except Exception as error:
            logging.exception(
                "Ошибка обработки текста через ИИ: %s",
                type(error).__name__,
            )
            status = "без ИИ — сохранён исходный текст"

    title = safe_title(first_line)
    now = datetime.now().astimezone()

    content = (
        f"Создано: {now:%d.%m.%Y %H:%M}\n\n"
        f"{body}\n"
    )

    clean_tags = []

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
        content += f"\n---\n\n{original_callout(source)}"

    try:
        path = write_note(title, content)

    except OSError:
        logging.exception("Ошибка записи заметки")
        await message.reply_text(
            "Не удалось сохранить файл. "
            "Посмотри ошибку в PowerShell."
        )
        return

    await message.reply_text(
        f"Сохранено в inbox {status}:\n{path.name}"
    )


async def save_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if update.effective_message is None:
        return

    await process_text(
        update,
        update.effective_message.text or "",
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
    if not authorized(update):
        return

    message = update.effective_message

    if message is None or message.voice is None:
        return

    if not VOICE_AVAILABLE or transcribe_audio is None:
        await message.reply_text(
            "Голосовые пока недоступны: Windows заблокировал "
            "модуль PyAV/faster-whisper.\n\n"
            "Текстовые сообщения работают."
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

        text = await asyncio.to_thread(
            transcribe_audio,
            temporary_path,
        )

        await message.reply_text(
            f"Распознано:\n\n{text}"
        )

        await process_text(
            update,
            text,
            "note",
        )

    except Exception as error:
        logging.exception(
            "Ошибка обработки голосового: %s",
            type(error).__name__,
        )
        await message.reply_text(
            "Не удалось распознать голосовое.\n"
            "Посмотри ошибку в PowerShell."
        )

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def on_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    logging.error(
        "Ошибка обработки: %s",
        type(context.error).__name__,
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

    INBOX.mkdir(exist_ok=True)

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(CommandHandler("raw", save_raw))
    app.add_handler(CommandHandler("plan", save_plan))

    app.add_handler(
        MessageHandler(
            filters.VOICE,
            save_voice,
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            save_note,
        )
    )

    app.add_error_handler(on_error)

    print(f"Папка заметок: {INBOX}")
    print("Бот запущен. Остановка: Ctrl+C.")

    app.run_polling(
        allowed_updates=["message"]
    )


if __name__ == "__main__":
    main()