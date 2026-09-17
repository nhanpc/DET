#!/usr/bin/env python3
"""Is it my ear or the voice? (issue nhanpc/DET#22) Synthesise a sample of dictation sentences, transcribe the
clips with Whisper and score the transcripts with the drill's own scorer (drills.dictation_score), per voice.

    .venv/bin/python scripts/tts_check.py                     # 40 sentences near the current θ, the engine's voices, whisper small
    .venv/bin/python scripts/tts_check.py --n 60 --theta 7.5 --model medium
    .venv/bin/python scripts/tts_check.py --engine edge        # the same sample through edge-tts, for comparison
    .venv/bin/python scripts/tts_check.py --attempts           # my listen-and-type rows: Whisper's credit beside mine
    .venv/bin/python scripts/tts_check.py --model tiny         # a weaker listener: tiny < base < small < medium

Besides the credit, Whisper reports its own confidence per word (`conf` = mean word probability, `min` = the
least certain word of a clip, `unsure` = clips with a word under 60 %) — the words even a strong listener
half-guessed are the ones the voice really blurs. A weaker model (--model tiny) is a listener closer to mine.

A voice Whisper gets right at ≥ 0.95 is intelligible: a low score of mine on it is listening, not audio. The
words Whisper also misses are the ones the voice mangles. Clips come from and go to the app's cache
(practice/listen-and-type/audio/), so nothing is synthesised twice.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import drills, store, tts                    # noqa: E402  (after the sys.path line)

WINDOW = 0.6                                          # |b_text − θ| for the sample, the drill's own window
PASS = 0.95                                           # a sentence Whisper "gets"
KEEP = 0.9                                            # mean credit under which a voice should leave the pool
UNSURE = 0.6                                          # a word Whisper transcribed with less confidence than this


def transcriber(name: str):
    """path → (text, [(word, probability)]): Whisper's transcript and its own confidence per word (the token
    probabilities behind the word timestamps). A word under UNSURE is one Whisper itself half-guessed."""
    import torch
    import whisper
    cuda = torch.cuda.is_available()
    model = whisper.load_model(name, device="cuda" if cuda else "cpu")

    def run(path: Path) -> tuple[str, list[tuple[str, float]]]:
        r = model.transcribe(str(path), language="en", fp16=cuda, temperature=0.0, word_timestamps=True)
        words = [(w["word"].strip(), float(w["probability"])) for seg in r["segments"] for w in seg.get("words", [])]
        return r["text"].strip(), words
    return run


def duration(path: Path) -> float:
    import soundfile as sf
    try:
        return sf.info(str(path)).duration
    except RuntimeError:                              # an MP3 soundfile cannot read (edge-tts): no timing
        return float("nan")


def score_clip(text: str, voice: str, asr) -> dict:
    path = tts.audio(text, voice)
    heard, words = asr(path)
    r = drills.dictation_score(text, heard)
    probs = [p for _, p in words] or [float("nan")]
    return {"text": text, "voice": voice, "heard": heard, "score": r["score"], "wrong": r["wrong"],
            "spw": duration(path) / max(1, drills.word_count(text)),
            "conf": sum(probs) / len(probs), "min": min(probs), "unsure": [w for w, p in words if p < UNSURE]}


def sample(theta: float, n: int, seed: int) -> list[dict]:
    from app import main
    rows = [r for r in main.sentence_bank() if r.get("b_text") and abs(float(r["b_text"]) - theta) <= WINDOW]
    rng = random.Random(seed)
    return rng.sample(rows, min(n, len(rows)))


def table(results: dict[str, list[dict]]) -> str:
    """Per voice: the credit Whisper earns, its pace, and Whisper's own confidence — the mean word probability,
    the mean of each clip's least certain word, and the share of clips with a word under UNSURE."""
    lines = [f"{'voice':20} {'credit':>6} {'≥ 0.95':>7} {'s/word':>7} {'conf':>6} {'min':>6} {'unsure':>7}  verdict"]
    for v, rs in results.items():
        mean = sum(r["score"] for r in rs) / len(rs)
        spw = [r["spw"] for r in rs if r["spw"] == r["spw"]]
        lines.append(f"{v:20} {mean:6.3f} {sum(r['score'] >= PASS for r in rs) / len(rs):6.0%} "
                     f"{sum(spw) / len(spw) if spw else float('nan'):7.2f} {sum(r['conf'] for r in rs) / len(rs):6.2f} "
                     f"{sum(r['min'] for r in rs) / len(rs):6.2f} {sum(bool(r['unsure']) for r in rs) / len(rs):6.0%}  "
                     + ("ok" if mean >= KEEP else "drop"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--theta", type=float, help="centre of the sample (default: the current θ)")
    ap.add_argument("--voices", help="comma-separated (default: the engine's pool)")
    ap.add_argument("--engine", choices=["kokoro", "edge"], default=tts.ENGINE)
    ap.add_argument("--model", default="small", help="whisper model: tiny, base, small, medium, large")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--worst", type=int, default=5)
    ap.add_argument("--attempts", action="store_true", help="my listen-and-type attempts: Whisper's credit beside mine")
    a = ap.parse_args(argv)

    tts.ENGINE = a.engine
    drills.VOICES[:] = tts.voices()
    asr = transcriber(a.model)
    if a.attempts:
        from app import main as app_main
        bank = {r["id"]: r for r in app_main.sentence_bank()}
        rows = [r for r in store.load_attempts() if r["task"] == "listen-and-type" and r["item"] in bank]
        if not rows:
            print("no listen-and-type attempts yet", file=sys.stderr)
            return 1
        print(f"{'attempt':24} {'me':>6} {'whisper':>8} {'conf':>5} {'min':>5}  sentence → whisper heard (wrong words) [unsure words]")
        for r in rows:
            s = bank[r["item"]]
            w = score_clip(s["sentence"], s["voice"], asr)
            print(f"{r['attempt']:24} {float(r['score'] or 0):6.2f} {w['score']:8.2f} {w['conf']:5.2f} {w['min']:5.2f}  {s['sentence']} → {w['heard']}"
                  + (f" ({', '.join(w['wrong'])})" if w["wrong"] else "") + (f" [{', '.join(w['unsure'])}]" if w["unsure"] else ""))
        return 0

    if a.theta is None:
        from app import main as app_main
        a.theta = app_main.current_state()["theta"] or 6.0
    rows = sample(a.theta, a.n, a.seed)
    voices = a.voices.split(",") if a.voices else tts.voices()
    print(f"{len(rows)} sentences with |b_text − {a.theta:.2f}| ≤ {WINDOW}, engine {a.engine}, whisper {a.model}", file=sys.stderr)
    results: dict[str, list[dict]] = {}
    for v in voices:
        results[v] = [score_clip(r["sentence"], v, asr) for r in rows]
        print(f"  {v}: done", file=sys.stderr)
    print(table(results))
    worst = sorted((r for rs in results.values() for r in rs), key=lambda r: (r["score"], r["min"]))[: a.worst]
    if worst:
        print("\nworst by credit:")
        for r in worst:
            print(f"  {r['score']:.2f} {r['voice']:12} {r['text']}\n       heard: {r['heard']}" + (f"  (wrong: {', '.join(r['wrong'])})" if r["wrong"] else ""))
    unsure = sorted((r for rs in results.values() for r in rs if r["unsure"]), key=lambda r: r["min"])[: a.worst]
    if unsure:
        print(f"\nleast confident (a word under {UNSURE:.0%}):")
        for r in unsure:
            print(f"  {r['min']:.2f} {r['voice']:12} {r['text']}   unsure: {', '.join(r['unsure'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
