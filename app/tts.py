"""Text to speech for the dictation and Listen Then Speak drills (issue nhanpc/DET#10): edge-tts neural voices,
one MP3 per (voice, text) cached under practice/listen-and-type/audio/ so a sentence needs the network once.
The app's handlers are sync, so this uses `Communicate.save_sync()`; edge_tts is imported on the first miss
only, so the tests and the offline drills never need it."""
from __future__ import annotations

from hashlib import sha1
from pathlib import Path
from typing import Optional

from .bank import PRACTICE

AUDIO = PRACTICE / "listen-and-type" / "audio"


def audio_path(text: str, voice: str, folder: Optional[Path] = None) -> Path:
    """`<sha1 of voice + newline + text>.mp3` — the same sentence in another voice is another file."""
    return (folder or AUDIO) / (sha1(f"{voice}\n{text}".encode("utf-8")).hexdigest() + ".mp3")


def synthesise(text: str, voice: str, path: Path) -> None:
    """Write `path` with edge-tts (needs the internet). A failed download leaves no half file behind."""
    import edge_tts                                                        # noqa: WPS433  (optional dependency)
    tmp = path.with_suffix(".part")
    try:
        edge_tts.Communicate(text, voice).save_sync(str(tmp))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def audio(text: str, voice: str, folder: Optional[Path] = None) -> Path:
    """The cached MP3 for (text, voice), generated on a miss."""
    folder = folder or AUDIO
    path = audio_path(text, voice, folder)
    if not path.exists() or path.stat().st_size == 0:
        folder.mkdir(parents=True, exist_ok=True)
        synthesise(text, voice, path)
    return path
