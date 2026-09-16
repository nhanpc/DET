"""Metaphone (Lawrence Philips, 1990) in pure Python — the sound key the dictation drill uses to tell a hearing
slip (`ward` for `word`: same key, WRT) from a vocabulary miss (`avoid` for `evict`: AFT / EFKT). GitHub issue
nhanpc/DET#16. Letters only; the classic rules, one pass left to right:

  initial   KN, GN, PN, AE, WR → drop the first letter;  X → S;  WH → W
  vowels    kept only at the start
  B         silent after M at the end (dumb, lamb)
  C         X in CIA / CH (SCH → SK);  S before I, E, Y;  silent in SCI / SCE / SCY;  else K
  D         J in DGE / DGY / DGI (the G is then silent);  else T
  G         silent in GH not at the end nor before a vowel, and in GN / GNED at the end;  J before I, E, Y;  else K
  H         silent after a vowel with no vowel after it, and after C, S, P, T, G
  K         silent after C
  P         F before H
  Q         K
  S         X before H and in SIO / SIA
  T         X in TIA / TIO;  0 (theta) before H;  silent in TCH
  V         F;   W, Y  silent unless a vowel follows;   X → KS;   Z → S
Doubled letters other than C collapse first. tests/test_phon.py checks 20 reference words.
"""
from __future__ import annotations

VOWELS = set("AEIOU")
FRONT = set("EIY")            # the letters that soften C and G


def metaphone(word: str) -> str:
    w = "".join(c for c in word.upper() if "A" <= c <= "Z")
    if not w:
        return ""
    # doubled letters collapse, except C
    s = w[0]
    for c in w[1:]:
        if c != s[-1] or c == "C":
            s += c
    w = s
    if w[:2] in ("KN", "GN", "PN", "AE", "WR"):
        w = w[1:]
    elif w[0] == "X":
        w = "S" + w[1:]
    elif w[:2] == "WH":
        w = "W" + w[2:]
    out = []
    n = len(w)
    at = lambda i: w[i] if 0 <= i < n else ""                          # noqa: E731
    for i, c in enumerate(w):
        prev, nxt, nxt2 = at(i - 1), at(i + 1), at(i + 2)
        if c in VOWELS:
            if i == 0:
                out.append(c)
        elif c == "B":
            if not (prev == "M" and i == n - 1):
                out.append("B")
        elif c == "C":
            if nxt == "I" and nxt2 == "A":
                out.append("X")
            elif nxt == "H":
                out.append("K" if prev == "S" else "X")
            elif nxt in FRONT:
                if prev != "S":
                    out.append("S")
            else:
                out.append("K")
        elif c == "D":
            out.append("J" if nxt == "G" and nxt2 in FRONT else "T")
        elif c == "G":
            if nxt == "H" and not (i + 1 == n - 1 or nxt2 in VOWELS):
                continue
            if nxt == "N" and (i + 1 == n - 1 or w[i + 1:] == "NED"):
                continue
            if prev == "D" and nxt in FRONT:
                continue                                               # the D of DGE / DGY / DGI already gave J
            if nxt in FRONT and prev != "G":
                out.append("J")
            else:
                out.append("K")
        elif c == "H":
            if prev in VOWELS and nxt not in VOWELS:
                continue
            if prev in ("C", "S", "P", "T", "G"):
                continue
            out.append("H")
        elif c == "K":
            if prev != "C":
                out.append("K")
        elif c == "P":
            out.append("F" if nxt == "H" else "P")
        elif c == "Q":
            out.append("K")
        elif c == "S":
            out.append("X" if nxt == "H" or (nxt == "I" and nxt2 in ("O", "A")) else "S")
        elif c == "T":
            if nxt == "I" and nxt2 in ("O", "A"):
                out.append("X")
            elif nxt == "H":
                out.append("0")
            elif not (nxt == "C" and nxt2 == "H"):
                out.append("T")
        elif c == "V":
            out.append("F")
        elif c in ("W", "Y"):
            if nxt in VOWELS:
                out.append(c)
        elif c == "X":
            out.append("KS")
        elif c == "Z":
            out.append("S")
        else:
            out.append(c)                                              # F, J, L, M, N, R
    return "".join(out)


def sounds_alike(a: str, b: str) -> bool:
    """Same non-empty Metaphone key."""
    ka, kb = metaphone(a), metaphone(b)
    return bool(ka) and ka == kb
