"""Entity resolution: map venue market participants to model entity keys.

Venue names are messy: Polymarket tennis outcomes are player names
("Marta Kostyuk"), MLB outcomes are full team names, table-tennis Setka
outcomes are often truncated ("Vadim Veacesl..."); model keys are normalized
full names (Sackmann spellings, MLB Stats API team names).

Policy: match conservatively; an unmatched market is SKIPPED and logged,
never guessed — a wrong entity match is an unbounded loss.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_SUFFIXES = re.compile(r"\b(jr|sr|ii|iii|iv)\b\.?", re.IGNORECASE)


def normalize(name: str) -> str:
    """Lowercase, strip accents/punctuation/suffixes, collapse whitespace."""
    name = unicodedata.normalize("NFKD", name)
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().replace("-", " ").replace(".", " ").replace(",", " ")
    name = _SUFFIXES.sub(" ", name)
    return " ".join(name.split())


def _token_set(name: str) -> set[str]:
    return set(normalize(name).split())


def similarity(a: str, b: str) -> float:
    """Blend of sequence and token-set similarity in [0, 1]."""
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = _token_set(a), _token_set(b)
    jacc = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    # Surname-containment helps truncated names ("vadim veacesl" vs the
    # full registered name) and "last, first" orderings.
    containment = 0.0
    if ta and tb:
        smaller, larger = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        containment = len(smaller & larger) / len(smaller)
    return max(seq, 0.5 * jacc + 0.5 * containment)


def match_entity(
    venue_name: str,
    candidates: Iterable[str],
    threshold: float = 0.85,
    ambiguity_margin: float = 0.08,
) -> Optional[str]:
    """Best candidate for a venue participant name, or None.

    Returns None (skip the market) when the best score is below `threshold`
    or when the runner-up is within `ambiguity_margin` (ambiguous match).
    """
    scored = sorted(
        ((similarity(venue_name, c), c) for c in candidates), key=lambda x: -x[0]
    )
    if not scored:
        return None
    best_score, best = scored[0]
    if best_score < threshold:
        log.info("no entity match for %r (best %r @ %.2f)", venue_name, best, best_score)
        return None
    if normalize(venue_name) == normalize(best):
        return best  # exact normalized match is trusted even with close runners-up
    if len(scored) > 1 and scored[1][0] > best_score - ambiguity_margin and scored[1][1] != best:
        log.warning(
            "ambiguous entity match for %r: %r (%.2f) vs %r (%.2f) — skipping",
            venue_name, best, best_score, scored[1][1], scored[1][0],
        )
        return None
    return best
