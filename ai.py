import asyncio
import json
import re

import httpx


MODEL = "qwen3:8b"
API_URL = "http://127.0.0.1:11434/api/chat"
AI_LOCK = asyncio.Lock()


EDITOR_PROMPT = """
Ты — аккуратный редактор личных заметок пользователя для Obsidian.

Тебе передаётся исходный текст пользователя. Иногда это текст,
полученный автоматической расшифровкой русской речи Whisper.
В таком тексте могут быть ошибки распознавания: похожие по звучанию
слова, пропущенные окончания, неправильные границы слов,
повторы, отсутствие пунктуации и неверно распознанные названия.

Главная цель:
сохранить исходную мысль пользователя, сделав текст грамотным,
понятным и естественным на русском языке.

ПРАВИЛА ИСПРАВЛЕНИЯ

1. Исправляй орфографию, грамматику и пунктуацию.

2. Исправляй очевидные ошибки Whisper:
   - слова, которые не существуют или не подходят по смыслу;
   - слова, похожие по звучанию на правильное слово;
   - пропущенные или неправильные окончания;
   - слитные или неправильно разделённые слова;
   - повторы, возникшие из-за распознавания.

3. Проверяй каждое необычное слово по контексту всего предложения.
   Не копируй странное слово автоматически.

4. Если контекст явно указывает на правильный вариант,
   используй естественное русское слово.
   Например:
   "купить мой код" в контексте магазина и продуктов
   может быть ошибкой Whisper от "купить молоко".

5. Если возможны два или больше вариантов и нельзя уверенно
   понять исходное слово, оставь исходный вариант.
   Не выдумывай замену.

6. Не меняй смысл, намерение или порядок мыслей пользователя.

7. Сохраняй:
   - первое лицо;
   - вопросы как вопросы;
   - отрицания;
   - сомнения и слова "может быть", "думаю", "возможно";
   - даты;
   - время;
   - числа;
   - количество;
   - названия файлов;
   - пути;
   - команды;
   - ссылки;
   - валюты.

8. Не добавляй факты, которых нет в исходном тексте.

9. Не добавляй советы, выводы, объяснения или новые задачи.

10. Не превращай предположение в утверждение.

11. Не отвечай на вопрос пользователя.
    Сохраняй вопрос в отредактированном виде.

12. Не объединяй разные мысли в одну, если это меняет смысл.

13. Не удаляй важные детали только ради краткости.

14. Убирай слова-паразиты и повторы только тогда,
    когда очевидно, что они не несут смысла.

ПРОВЕРКА БЫТОВОГО КОНТЕКСТА

Если в тексте встречаются слова:
"магазин", "купить", "продукты", "молоко", "хлеб",
"яйца", "вода", "две штуки", "три штуки",
проверь, не является ли соседнее необычное слово
ошибкой автоматического распознавания.

Например:
"купить мой код две штуки"
может означать:
"купить молоко, две штуки".

Используй такую замену только при достаточной уверенности
по контексту. Если уверенности нет, не выдумывай.

ПРОВЕРКА НАЗВАНИЙ

Пиши правильно:
Telegram
Telegram-бот
Obsidian
Ollama
Whisper
Python
GitHub
Windows
Discord
PowerShell
FFmpeg
OpenAI

Не изменяй пользовательские названия, пути и команды,
если нет очевидной ошибки распознавания.

ФОРМАТ ЗАМЕТКИ

Верни:
- короткий заголовок в поле title;
- исправленный текст в поле body;
- не более трёх тегов в поле tags.

Поле body должно содержать только отредактированный текст пользователя.

Не помещай в body:
- заголовок;
- дату создания;
- теги;
- пояснения редактора;
- слова "исправлено";
- исходную расшифровку;
- комментарии о том, какие ошибки были найдены.

Если исходный текст — одна мысль, не превращай её
в список или план.

Если исходный текст содержит явные действия,
не превращай их в новые действия от себя:
только сохрани и грамотно оформи то,
что действительно сказал пользователь.

Перед ответом проверь:
- не добавил ли ты новый факт;
- не потерял ли отрицание;
- не изменил ли дату, время или количество;
- не заменил ли сомнительное слово без достаточных оснований;
- нет ли в body заголовка или комментариев редактора.

Верни только данные по JSON-схеме.
"""


NOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
        },
        "body": {
            "type": "string",
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "title",
        "body",
        "tags",
    ],
    "additionalProperties": False,
}


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
        },
        "steps": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "notes": {
            "type": "string",
        },
        "tags": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
    },
    "required": [
        "title",
        "steps",
        "notes",
        "tags",
    ],
    "additionalProperties": False,
}


def text_field(
    result: dict,
    key: str,
    allow_empty: bool = False,
) -> str:
    value = result.get(key)

    if not isinstance(value, str):
        raise ValueError(
            f"Некорректное поле: {key}"
        )

    value = value.strip()

    if not value and not allow_empty:
        raise ValueError(
            f"Пустое поле: {key}"
        )

    return value


def string_list(
    result: dict,
    key: str,
) -> list[str]:
    value = result.get(key)

    if not isinstance(value, list):
        raise ValueError(
            f"Некорректный список: {key}"
        )

    if not all(
        isinstance(item, str)
        for item in value
    ):
        raise ValueError(
            f"Список содержит не строки: {key}"
        )

    return value


def clean_tags(
    tags: list[str],
) -> list[str]:
    result = []

    for tag in tags:
        tag = re.sub(
            r"[^\w-]",
            "",
            tag.lower(),
        )[:30]

        if tag and tag not in result:
            result.append(tag)

    return result[:3]


async def prepare_note(
    text: str,
    mode: str = "note",
) -> dict:
    text = text.strip()

    if not text:
        raise ValueError(
            "Пустой текст"
        )

    if len(text) > 6000:
        raise ValueError(
            "Текст слишком длинный"
        )

    if mode == "plan":
        schema = PLAN_SCHEMA

        instruction = """
Верни title, steps, notes, tags.

steps — только действия, которые явно есть
в исходном сообщении.

notes — только пояснения и ограничения
из исходного текста.

Не добавляй новые действия.
"""

    else:
        schema = NOTE_SCHEMA

        instruction = """
Верни title, body, tags.

body — исправленный русский текст пользователя.

Перед возвратом body проверь:
1. Нет ли слов, похожих на явные ошибки Whisper.
2. Согласуются ли слова между собой.
3. Соответствуют ли существительные числам
   и словам "одна", "две", "три", "штуки".
4. Не является ли необычное слово ошибкой распознавания.
5. Не изменился ли смысл после исправления.

Если исправление недостаточно очевидно,
сохрани исходное слово.
Не повторяй title в body.
"""

    messages = [
        {
            "role": "system",
            "content": (
                EDITOR_PROMPT
                + instruction
                + "\n/no_think"
            ),
        },
        {
            "role": "user",
            "content": text,
        },
    ]

    async with AI_LOCK:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                180.0,
                connect=5.0,
            ),
            trust_env=False,
        ) as client:
            response = await client.post(
                API_URL,
                json={
                    "model": MODEL,
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "messages": messages,
                    "options": {
                        "temperature": 0,
                        "num_ctx": 8192,
                        "num_predict": 2500,
                    },
                },
            )

            response.raise_for_status()
            payload = response.json()

    if payload.get("done_reason") == "length":
        raise ValueError(
            "Ответ ИИ обрезан"
        )

    raw_content = (
        payload
        .get("message", {})
        .get("content")
    )

    if (
        not isinstance(raw_content, str)
        or not raw_content.strip()
    ):
        raise ValueError(
            "ИИ вернул пустой ответ"
        )

    result = json.loads(raw_content)

    if not isinstance(result, dict):
        raise ValueError(
            "Ответ ИИ не является JSON-объектом"
        )

    title = text_field(
        result,
        "title",
    )

    tags = clean_tags(
        string_list(
            result,
            "tags",
        )
    )

    if mode == "plan":
        steps = string_list(
            result,
            "steps",
        )

        notes = text_field(
            result,
            "notes",
            allow_empty=True,
        )

        clean_steps = []

        for step in steps:
            step = re.sub(
                r"\s+",
                " ",
                step,
            ).strip()

            step = re.sub(
                r"^(?:\d+[.)]|[-*])\s+",
                "",
                step,
            )

            if step:
                clean_steps.append(step)

        if not clean_steps:
            raise ValueError(
                "ИИ не нашёл действий"
            )

        body = "\n".join(
            f"{number}. {step}"
            for number, step in enumerate(
                clean_steps,
                start=1,
            )
        )

        if notes:
            body += f"\n\n{notes}"

        return {
            "title": title,
            "body": body.strip(),
            "tags": tags,
            "has_steps": True,
        }

    body = text_field(
        result,
        "body",
    )

    return {
        "title": title,
        "body": body,
        "tags": tags,
        "has_steps": False,
    }