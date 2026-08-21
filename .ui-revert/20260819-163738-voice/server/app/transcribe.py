"""Speech to text, on this machine.

The browser has a recogniser built in and it would have been a tenth of the
code, but Chrome's ships the audio to Google and Safari's to Apple -- on the
phone, which is the device this is actually used from. A local-only tool that
posts your voice to a third party to hear it is not a local-only tool.

So: faster-whisper, behind a lazy singleton. The model loads on first use, not
at import, because a server nobody dictates to should not pay for it. That
mirrors providers/anthropic.py, which imports its SDK the same way.
"""

from __future__ import annotations

import asyncio
import io
import threading

from .config import Settings


class TranscriptionError(RuntimeError):
    """Refused or failed. The message is written to be shown to the user."""


# Guarded because the first request pays the model load, and two arriving
# together would otherwise load it twice.
_lock = threading.Lock()
_model = None
_loaded_as: tuple[str, str, str] | None = None

# Set once the GPU has been proven unusable, so the fallback is paid for once
# rather than on every recording.
_gpu_unusable = False


def _resolve_device(requested: str) -> tuple[str, str]:
    """(device, compute_type).

    CTranslate2's CUDA path needs cuBLAS and cuDNN beside it, which on Windows
    is a separate install most people don't have. Counting CUDA devices does
    not detect that: the model loads happily and then dies on the first encode
    with "Library cublas64_12.dll is not found". So `auto` means "try the GPU
    and fall back for real" -- see the retry in _run.
    """
    if requested == "cpu" or (requested == "auto" and _gpu_unusable):
        return "cpu", "int8"
    if requested == "cuda":
        return "cuda", "float16"
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


# Substrings of the CTranslate2 failure that means "the CUDA libraries aren't
# here", as opposed to a genuinely broken recording.
_CUDA_TROUBLE = ("cublas", "cudnn", "cuda", "not found or cannot be loaded")


def _is_cuda_trouble(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _CUDA_TROUBLE)


def _get_model(settings: Settings):
    global _model, _loaded_as

    device, compute = _resolve_device(settings.whisper_device)
    if settings.whisper_compute:
        compute = settings.whisper_compute
    signature = (settings.whisper_model, device, compute)

    with _lock:
        if _model is not None and _loaded_as == signature:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on install
            raise TranscriptionError(
                "Dictation needs `pip install faster-whisper` on the server."
            ) from exc

        try:
            _model = WhisperModel(settings.whisper_model, device=device, compute_type=compute)
        except Exception as exc:
            raise TranscriptionError(
                f"Could not load the speech model ({settings.whisper_model}): {exc}"
            ) from exc
        _loaded_as = signature
        print(f"[whisper] loaded {settings.whisper_model} on {device} ({compute})")
        return _model


def _decode(model, data: bytes) -> str:
    # A file-like object rather than a temp file: faster-whisper decodes
    # through PyAV, which reads WebM/Opus and MP4/AAC alike, so whatever the
    # browser chose to record in arrives readable.
    segments, _info = model.transcribe(io.BytesIO(data), beam_size=1)
    return " ".join(segment.text.strip() for segment in segments).strip()


def _run(settings: Settings, data: bytes) -> str:
    global _gpu_unusable, _model, _loaded_as

    try:
        return _decode(_get_model(settings), data)
    except TranscriptionError:
        raise
    except Exception as exc:
        # The GPU path only reveals itself as broken here, on the first encode.
        # Downgrade permanently and retry once, so dictation works on a machine
        # without the CUDA libraries instead of failing forever.
        if not (_is_cuda_trouble(exc) and settings.whisper_device == "auto" and not _gpu_unusable):
            raise TranscriptionError("That recording could not be read as audio.") from exc

        print(f"[whisper] GPU unusable ({exc}); falling back to CPU")
        with _lock:
            _gpu_unusable = True
            _model = None
            _loaded_as = None

    try:
        return _decode(_get_model(settings), data)
    except TranscriptionError:
        raise
    except Exception as exc:
        raise TranscriptionError("That recording could not be read as audio.") from exc


async def transcribe(settings: Settings, data: bytes) -> str:
    """Text of a recording, off the event loop.

    Whisper is CPU-bound and blocking; running it inline would stall every
    other request on this single-process server for the duration.
    """
    if not data:
        raise TranscriptionError("The recording was empty.")
    return await asyncio.to_thread(_run, settings, data)
