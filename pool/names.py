"""Matching rostered golfers to names on the ESPN leaderboard.

This automates the manual DNP check: a golfer only counts as DNP if nobody in
the field is a plausible match. Accent and punctuation differences
(Aberg/Åberg, JJ/J.J.) match automatically; looser matches are used but
flagged so you can confirm them; overrides in config.json settle anything.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

# Letters that Unicode does not split into "base letter + accent"
SPECIAL = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "ß": "ss", "ł": "l", "Ł": "L",
    "đ": "d", "Đ": "D", "ð": "d", "þ": "th", "œ": "oe", "Œ": "OE",
})


def plain(name: str) -> str:
    """Lowercase, no accents, words separated by single spaces."""
    s = unicodedata.normalize("NFKD", name.translate(SPECIAL))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[.'’`]", "", s)          # J.J. -> jj, O'Hair -> ohair
    s = re.sub(r"[^a-z0-9]+", " ", s)      # hyphens and the rest -> spaces
    return re.sub(r"\s+", " ", s).strip()


def key(name: str) -> str:
    """plain() with the spaces removed, so 'Si Woo' == 'Si-Woo' == 'Siwoo'."""
    return plain(name).replace(" ", "")


@dataclass
class Match:
    roster_name: str
    espn_name: str | None   # None means not in the field (DNP)
    kind: str               # exact | override | spelling | likely | dnp
    suggestion: str | None = None  # closest field name when kind == dnp


def match_roster(roster: list[str], field: list[str], overrides: dict | None = None) -> list[Match]:
    overrides = overrides or {}
    field_set = set(field)
    by_key: dict[str, list[str]] = {}
    for f in field:
        by_key.setdefault(key(f), []).append(f)
    plain_field = [(f, plain(f)) for f in field]

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
        same = by_key.get(key(name), [])
        if len(same) == 1:
            results.append(Match(name, same[0], "spelling"))
            continue

        p = plain(name)
        scored = sorted(((SequenceMatcher(None, p, pf).ratio(), f) for f, pf in plain_field), reverse=True)
        best = scored[0] if scored else (0, None)
        second = scored[1][0] if len(scored) > 1 else 0
        if best[0] >= 0.88 and best[0] - second >= 0.05:
            results.append(Match(name, best[1], "likely"))
            continue

        # Same surname and first initial, and only one such golfer
        parts = p.split()
        if len(parts) >= 2:
            cands = [f for f, pf in plain_field
                     if pf.split() and pf.split()[-1] == parts[-1] and pf[0] == parts[0][0]]
            if len(cands) == 1:
                results.append(Match(name, cands[0], "likely"))
                continue

        suggestion = best[1] if best[0] >= 0.6 else None
        results.append(Match(name, None, "dnp", suggestion=suggestion))
    return results
