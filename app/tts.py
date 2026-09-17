"""Text to speech for the dictation and Listen Then Speak drills (issues nhanpc/DET#10, #21).

Engine `kokoro` (default): Kokoro-82M through its KPipeline, American English, natural pace, on the GPU when
torch sees one; the model is loaded once, on the first miss, and every clip is peak-normalised so it is clearly
audible. Output is 24 kHz WAV, lossless. Engine `edge` (DET_TTS=edge): the old edge-tts neural voices, MP3,
needs the network. Either way one file per (voice, text) is cached under practice/listen-and-type/audio/ and
the engine's package is imported on the first miss only, so the tests and the offline drills never need it.
"""
from __future__ import annotations

import os
from hashlib import sha1
from pathlib import Path
from typing import Optional

from .bank import PRACTICE

AUDIO = PRACTICE / "listen-and-type" / "audio"
ENGINE = os.environ.get("DET_TTS", "kokoro")
VOICES = {"kokoro": ["af_heart", "af_bella", "am_michael"],       # American; am_fenrir dropped after the voice check (#22)
          "edge": ["en-US-AriaNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural", "en-IN-NeerjaNeural"]}
SUFFIX = {"kokoro": ".wav", "edge": ".mp3"}
MEDIA = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
REPO = "hexgrad/Kokoro-82M"
LANG = "a"                                  # Kokoro: American English
SPEED = 1.0                                 # natural pace, like the DET
PEAK = 0.9                                  # normalise each clip to this peak (−1 dBFS)
RATE = 24000
_pipeline = None


def voices() -> list[str]:
    return VOICES[ENGINE]


def voice_of(name: str) -> str:
    """A voice of the current engine: `name` itself when it belongs to it, else one picked from `name`
    deterministically — a sentences.csv row that still carries an edge-tts voice keeps one fixed Kokoro voice."""
    pool = voices()
    return name if name in pool else pool[int(sha1(name.encode()).hexdigest(), 16) % len(pool)]


def audio_path(text: str, voice: str, folder: Optional[Path] = None) -> Path:
    """`<sha1 of voice + newline + text>` + the engine's suffix — the same sentence in another voice, or from
    the other engine, is another file."""
    voice = voice_of(voice)
    return (folder or AUDIO) / (sha1(f"{voice}\n{text}".encode("utf-8")).hexdigest() + SUFFIX[ENGINE])


def pipeline():
    """The Kokoro pipeline, built once (downloads the model into the Hugging Face cache the first time)."""
    global _pipeline
    if _pipeline is None:
        from kokoro import KPipeline                                            # noqa: WPS433  (optional dependency)
        _pipeline = KPipeline(lang_code=LANG, repo_id=REPO)
    return _pipeline


def synthesise_kokoro(text: str, voice: str, path: Path) -> None:
    import numpy as np
    import soundfile as sf
    chunks = [a.detach().cpu().numpy() if hasattr(a, "detach") else np.asarray(a) for _, _, a in pipeline()(text, voice=voice, speed=SPEED)]
    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio = audio * (PEAK / peak)
    sf.write(str(path), audio.astype("float32"), RATE, format="WAV", subtype="PCM_16")   # the .part name carries no extension


def synthesise_edge(text: str, voice: str, path: Path) -> None:
    import edge_tts                                                            # noqa: WPS433  (optional dependency)
    edge_tts.Communicate(text, voice).save_sync(str(path))


def synthesise(text: str, voice: str, path: Path) -> None:
    """Write `path` with the current engine. A failure leaves no half file behind."""
    tmp = path.with_suffix(".part")
    try:
        (synthesise_kokoro if ENGINE == "kokoro" else synthesise_edge)(text, voice_of(voice), tmp)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def audio(text: str, voice: str, folder: Optional[Path] = None) -> Path:
    """The cached clip for (text, voice), generated on a miss."""
    folder = folder or AUDIO
    path = audio_path(text, voice, folder)
    if not path.exists() or path.stat().st_size == 0:
        folder.mkdir(parents=True, exist_ok=True)
        synthesise(text, voice, path)
    return path
