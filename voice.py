from pathlib import Path

from faster_whisper import WhisperModel


MODEL_SIZE = "small"
MODEL_DIR = Path(__file__).resolve().parent / "models"

_model = None


def get_model() -> WhisperModel:
    global _model

    if _model is None:
        try:
            _model = WhisperModel(
                MODEL_SIZE,
                device="cuda",
                compute_type="float16",
                download_root=str(MODEL_DIR),
            )
        except Exception:
            _model = WhisperModel(
                MODEL_SIZE,
                device="cpu",
                compute_type="int8",
                download_root=str(MODEL_DIR),
            )

    return _model


def transcribe_audio(audio_path: str | Path) -> str:
    model = get_model()

    segments, _ = model.transcribe(
        str(audio_path),
        language="ru",
        beam_size=5,
        vad_filter=True,
    )

    text = " ".join(
        segment.text.strip()
        for segment in segments
        if segment.text.strip()
    ).strip()

    if not text:
        raise ValueError("Речь не распознана")

    return text