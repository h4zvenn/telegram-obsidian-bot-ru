from pathlib import Path

from faster_whisper import WhisperModel


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"

MODEL_SIZE = "small"

_model = None


def get_model() -> WhisperModel:
    global _model

    if _model is None:
        print(
            "Загружаю faster-whisper на CPU...",
            flush=True,
        )

        _model = WhisperModel(
            MODEL_SIZE,
            device="cpu",
            compute_type="int8",
            download_root=str(MODEL_DIR),
        )

        print(
            "Модель faster-whisper загружена на CPU.",
            flush=True,
        )

    return _model


def transcribe_audio(
    audio_path: str | Path,
) -> str:
    audio_path = Path(audio_path)

    if not audio_path.is_file():
        raise FileNotFoundError(
            f"Аудиофайл не найден: {audio_path}"
        )

    model = get_model()

    print(
        f"Начинаю распознавание файла: {audio_path}",
        flush=True,
    )

    segments, _ = model.transcribe(
        str(audio_path),
        language="ru",
        task="transcribe",
        beam_size=8,
        best_of=5,
        temperature=0.0,
        vad_filter=True,
        condition_on_previous_text=False,
        initial_prompt=(
            "Русская бытовая речь. "
            "Покупки: молоко, хлеб, яйца, продукты, "
            "магазин, две штуки. "
            "Заметки: напомни, завтра, сегодня, "
            "план, задача. "
            "Технические слова: Telegram, "
            "Telegram-бот, Obsidian, Ollama, "
            "Whisper, Python, GitHub, Windows."
        ),
    )

    parts = []

    for segment in segments:
        text = segment.text.strip()

        if text:
            parts.append(text)

    result = " ".join(parts).strip()

    if not result:
        raise ValueError(
            "Речь не распознана"
        )

    print(
        f"Whisper распознал: {result}",
        flush=True,
    )

    return result