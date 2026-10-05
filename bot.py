import logging
import os
import re
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

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("TELEGRAM_USER_ID", "0"))
VAULT = Path(os.getenv("OBSIDIAN_VAULT", ""))
INBOX = VAULT / "inbox"

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def authorized(update: Update) -> bool:
    return (
        OWNER_ID > 0
        and update.effective_user is not None
        and update.effective_user.id == OWNER_ID
        and update.effective_chat is not None
        and update.effective_chat.type == "private"
    )


async def show_id(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.type != "private":
        return
    await update.effective_message.reply_text(
        f"Твой Telegram ID: {update.effective_user.id}"
    )


async def start(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_chat.type != "private":
        return

    if OWNER_ID == 0:
        await update.effective_message.reply_text(
            f"Твой Telegram ID: {update.effective_user.id}\n\n"
            "Вставь его в TELEGRAM_USER_ID в файле .env "
            "и перезапусти бота. Пока сохранение отключено."
        )
        return

    if authorized(update):
        await update.effective_message.reply_text(
            "Отправь текст — сохраню его в Obsidian."
        )


async def save_note(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not authorized(update):
        return

    message = update.effective_message
    text = message.text.strip()

    if not text:
        await message.reply_text("Отправь непустой текст.")
        return

    first_line = text.splitlines()[0].strip()
    title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", first_line)
    title = re.sub(r"\s+", " ", title)
    title = title[:80].strip(" .") or "Заметка"

    reserved = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
    }
    if title.split(".")[0].upper() in reserved:
        title = f"Заметка - {title}"

    now = datetime.now().astimezone()
    content = (
        f"# {title}\n\n"
        f"Создано: {now.isoformat(timespec='seconds')}\n\n"
        f"{text}\n"
    )

    number = 1

    try:
        while True:
            suffix = "" if number == 1 else f" ({number})"
            path = INBOX / f"{title}{suffix}.md"

            try:
                with path.open("x", encoding="utf-8") as file:
                    file.write(content)
                break
            except FileExistsError:
                number += 1

    except OSError:
        logging.exception("Не удалось сохранить заметку")
        await message.reply_text(
            "Не удалось записать файл. Посмотри ошибку в PowerShell."
        )
        return

    await message.reply_text(f"Сохранено в inbox:\n{path.name}")


async def on_error(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    logging.error(
        "Ошибка обработки: %s", type(context.error).__name__
    )


def main() -> None:
    if not TOKEN:
        raise SystemExit("Заполни TELEGRAM_BOT_TOKEN в .env.")

    if not VAULT.is_dir():
        raise SystemExit(
            f"Хранилище не найдено: {VAULT}. Проверь OBSIDIAN_VAULT."
        )

    INBOX.mkdir(exist_ok=True)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, save_note)
    )
    app.add_error_handler(on_error)

    print("Бот запущен. Остановка: Ctrl+C.")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()