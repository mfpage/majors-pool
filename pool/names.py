"""Matching rostered golfers to names on the ESPN leaderboard.

Only three kinds of difference are accepted, and only when exactly one golfer
in the field fits:

- spelling: identical apart from accents, umlauts, apostrophes, periods,
  hyphens and spacing (Aberg / Åberg, JJ / J.J., Si-Woo / Si Woo)
- typo:     otherwise one letter different: replaced, added or missing
            (Jon Rahn / Jon Rahm)
- flipped:  the same words in a different order (Rahm Jon / Jon Rahm)

Anything else counts as not in the field (DNP). Overrides in config.json
settle any case by hand.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Letters that Unicode does not split into "base letter + accent"
SPECIAL = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ð": "d", "þ": "th", "œ": "oe", "Œ": "OE",
})


def plain(name: str) -> str:
    """Lowercase, no accents or punctuation, words separated by single spaces."""
    s = unicodedata.normalize("NFKD", name.translate(SPECIAL))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[.'’`]", "", s)          # J.J. -> jj, O'Hair -> ohair
    s = re.sub(r"[^a-z0-9]+", " ", s)      # hyphens and the rest -> spaces
    return re.sub(r"\s+", " ", s).strip()


SUFFIX = re.compile(r"\s+(jr|sr|ii|iii|iv)$")
TITLE = re.compile(r"^(sir|dr)\s+")


def key(name: str) -> str:
    """plain() with the spaces removed, so 'Si Woo' == 'Si-Woo' == 'Siwoo'.
    Titles and suffixes are dropped too: 'Sir Nick Faldo' == 'Nick Faldo',
    'Frank Lickliter II' == 'Frank Lickliter'."""
    p = TITLE.sub("", SUFFIX.sub("", plain(name)))
    return p.replace(" ", "")


def one_letter_apart(a: str, b: str) -> bool:
    """True if a and b differ by exactly one replaced, added or missing letter."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1:]


@dataclass
class Match:
    roster_name: str
    espn_name: str | None   # None means not in the field (DNP)
    kind: str               # exact | override | spelling | typo | flipped | dnp
    suggestion: str | None = None  # a near miss that wasn't unique enough to use


def find_close(name: str, field: list[str]) -> tuple[str | None, list[str]]:
    """The rule that fits (spelling, typo, flipped) and the field names that fit it."""
    k = key(name)
    words = sorted(TITLE.sub("", SUFFIX.sub("", plain(name))).split())
    for kind, test in (
        ("spelling", lambda f: key(f) == k),
        ("flipped", lambda f: sorted(TITLE.sub("", SUFFIX.sub("", plain(f))).split()) == words and key(f) != k),
        ("typo", lambda f: one_letter_apart(key(f), k)),
    ):
        hits = [f for f in field if test(f)]
        if hits:
            return kind, hits
    return None, []


def match_roster(roster: list[str], field: list[str], overrides: dict | None = None) -> list[Match]:
    overrides = overrides or {}
    field_set = set(field)
    results = []
    for name in roster:
        if name in overrides:
            target = overrides[name]
            if target is None:
                results.append(Match(name, None, "override"))
            elif target in field_set:
                results.append(Match(name, target, "override"))
            else:
                results.append(Match(name, None, "dnp", suggestion=f"override '{target}' not in field"))
            continue
        if name in field_set:
            results.append(Match(name, name, "exact"))
            continue
        same_case = [f for f in field if f.casefold() == name.casefold()]
        if len(same_case) == 1:  # only capitalization differs (e.g. Mcilroy / McIlroy)
            results.append(Match(name, same_case[0], "exact"))
            continue
        kind, hits = find_close(name, field)
        if len(hits) == 1:
            results.append(Match(name, hits[0], kind))
        else:
            # several equally close names: don't guess, but mention the first
            results.append(Match(name, None, "dnp", suggestion=hits[0] if hits else None))
    return results
