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
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
        "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
    }

    if title.split(".")[0].upper() in reserved:
        title = f"Заметка - {title}"

    return title


def clean_ai_body(body: str, title: str) -> str:
    lines = body.strip().splitlines()

    # Убираем повтор заголовка только в начале ответа.
    while lines:
        first = lines[0].strip()
        candidate = re.sub(r"^#{1,6}\s+", "", first)
        candidate = candidate.strip("*_ ").casefold()

        if not first or candidate == title.strip().casefold():
            lines.pop(0)
        else:
            break

    body = "\n".join(lines).strip()

    # Заголовки первого уровня превращаем во второй.
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


async def show_id(
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

    await update.effective_message.reply_text(
        f"Твой Telegram ID: {update.effective_user.id}"
    )


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
            "Вставь его в TELEGRAM_USER_ID в файле .env "
            "и перезапусти бота. Пока сохранение отключено."
        )
        return

    if authorized(update):
        await update.effective_message.reply_text(
            "Отправь текст — оформлю его через ИИ "
            "и сохраню в Obsidian → inbox.\n\n"
            "Если ИИ недоступен, сохраню исходный текст."
        )


async def save_note(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not authorized(update):
        return

    message = update.effective_message
    if message is None or message.text is None:
        return

    text = message.text.strip()
    if not text:
        await message.reply_text("Отправь непустой текст.")
        return

    await message.reply_text("Оформляю заметку…")

    used_ai = False
    tags = []

    try:
        note = await prepare_note(text)
        first_line = note["title"].strip()
        body = clean_ai_body(note["body"], first_line)

        if not body:
            raise ValueError("ИИ вернул пустое содержимое")

        tags = note["tags"]
        used_ai = True

    except Exception as error:
        logging.warning(
            "ИИ недоступен или ответ некорректен: %s",
            type(error).__name__,
        )
        first_line = text.splitlines()[0].strip()
        body = text

    title = safe_title(first_line)
    now = datetime.now().astimezone()

    clean_tags = []
    for tag in tags[:3]:
        tag = re.sub(r"[^\w-]", "", tag.lower())[:30]
        if tag and tag not in clean_tags:
            clean_tags.append(tag)

    # Название показывается в Obsidian как имя заметки.
    # Повторный заголовок # в содержимое не добавляем.
    content = (
        f"Создано: {now:%d.%m.%Y %H:%M}\n\n"
        f"{body}\n"
    )

    if clean_tags:
        tag_line = " ".join(f"#{tag}" for tag in clean_tags)
        content += f"\n{tag_line}\n"

    if used_ai:
        content += f"\n---\n\n{original_callout(text)}"

    number = 1

    try:
        INBOX.mkdir(exist_ok=True)

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
            "Не удалось записать файл. "
            "Посмотри ошибку в PowerShell."
        )
        return

    mode = "с ИИ" if used_ai else "без ИИ"
    await message.reply_text(
        f"Сохранено в inbox {mode}:\n{path.name}"
    )


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
        raise SystemExit("Заполни TELEGRAM_BOT_TOKEN в .env.")

    if not VAULT_SETTING:
        raise SystemExit("Заполни OBSIDIAN_VAULT в .env.")

    if not VAULT.is_dir():
        raise SystemExit(
            f"Хранилище не найдено: {VAULT}. "
            "Проверь OBSIDIAN_VAULT."
        )

    INBOX.mkdir(exist_ok=True)

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", show_id))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            save_note,
        )
    )
    app.add_error_handler(on_error)

    print(f"Папка заметок: {INBOX}")
    print("Бот запущен. Остановка: Ctrl+C.")

    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()