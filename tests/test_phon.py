"""Metaphone (issue #16, app/phon.py): 20 reference words with their classic keys, and the pairs the dictation
drill relies on."""
from app import phon

REFERENCE = [("knight", "NT"), ("ward", "WRT"), ("word", "WRT"), ("tenant", "TNNT"), ("evict", "EFKT"), ("avoid", "AFT"),
             ("rent", "RNT"), ("which", "WX"), ("thought", "0T"), ("edge", "EJ"), ("judge", "JJ"), ("gnome", "NM"),
             ("wright", "RT"), ("bought", "BT"), ("knee", "N"), ("whale", "WL"), ("ginger", "JNJR"), ("vision", "FXN"),
             ("question", "KSXN"), ("dumb", "TM"), ("lamb", "LM"), ("shell", "XL"), ("mission", "MXN"), ("city", "ST"),
             ("cake", "KK"), ("cash", "KX"), ("bishop", "BXP"), ("character", "XRKTR"), ("Thomas", "0MS"),
             ("philosophy", "FLSF"), ("psychology", "PSXLJ"), ("ache", "AX"), ("chemist", "XMST"), ("exact", "EKSKT"),
             ("sugar", "SKR")]


def test_reference_words():
    assert len(REFERENCE) >= 20
    for word, key in REFERENCE:
        assert phon.metaphone(word) == key, word


def test_pairs_and_edge_cases():
    assert phon.sounds_alike("ward", "word") and phon.sounds_alike("tennant", "tenant") and phon.sounds_alike("their", "there")
    assert not phon.sounds_alike("avoid", "evict") and not phon.sounds_alike("rent", "went")
    assert phon.metaphone("Tennant") == phon.metaphone("tenant") == "TNNT"          # doubled letters collapse, case ignored
    assert phon.metaphone("accept") == "AKSPT" and phon.metaphone("school") == "SKL"   # C is not collapsed; SCH → SK
    assert phon.metaphone("don't") == phon.metaphone("dont") == "TNT"               # letters only
    assert phon.metaphone("") == "" and phon.metaphone("123") == "" and not phon.sounds_alike("", "")
    assert phon.metaphone("sign") == "SN" and phon.metaphone("tough") == "TK"       # GN at the end, GH at the end
