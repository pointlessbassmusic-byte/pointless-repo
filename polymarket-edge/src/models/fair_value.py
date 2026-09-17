"""Match Polymarket sports markets to sportsbook games and produce fair probabilities.

v1 scope: moneyline-style markets ("Will <Team> beat <Team>?" / "<Team> vs. <Team>" winner
markets). Matching is fuzzy on team names + game start time.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta

from ..clients.gamma import SportsMarket
from ..clients.odds_api import Game
from .devig import consensus_probs

log = logging.getLogger(__name__)

_STOPWORDS = {"the", "fc", "cf", "sc", "afc", "de", "of"}


def _tokens(name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return {w for w in words if w not in _STOPWORDS}


def _team_in_text(team: str, text_tokens: set[str]) -> bool:
    """A team matches if any distinctive token of its name appears in the text."""
    tt = _tokens(team)
    if not tt:
        return False
    # nickname (last token) or city tokens both count
    return bool(tt & text_tokens)


def _nickname(team: str) -> str:
    toks = re.findall(r"[a-z0-9]+", team.lower())
    return toks[-1] if toks else ""


def _match_score(g, text_tokens: set[str]) -> int:
    """How well a sportsbook game matches market text. Nicknames ('yankees',
    'bruins') are distinctive and score 2; city/other tokens score 1. Both
    teams must match something, and both nicknames matching dominates any
    city-token coincidence with another game in the same town."""
    score = 0
    for team in (g.home_team, g.away_team):
        tt = _tokens(team)
        if not (tt & text_tokens):
            return 0
        score += 1
        if _nickname(team) in text_tokens:
            score += 2
    return score


def _team_position(team: str, text: str) -> int | None:
    """Earliest character position at which any token of the team name appears in text."""
    low = text.lower()
    positions = []
    for tok in _tokens(team):
        m = re.search(rf"\b{re.escape(tok)}\b", low)
        if m:
            positions.append(m.start())
    return min(positions) if positions else None


_NEGATED_RE = re.compile(r"\b(lose|loses|lost|losing|eliminated|relegated|swept|fail to)\b")


def _question_negated(question: str) -> bool:
    """'Will the Bills lose to the Chiefs?' — Yes means the subject does NOT win."""
    return bool(_NEGATED_RE.search(question.lower()))


def _subject_team(cons: dict[str, float], question: str) -> str | None:
    """The team a Yes/No question is about. Both teams usually appear
    ("Will the Chiefs beat the Bills?"), so take the one named earliest —
    the grammatical subject — not just any team that matches."""
    best_team, best_pos = None, None
    for team in cons:
        pos = _team_position(team, question)
        if pos is not None and (best_pos is None or pos < best_pos):
            best_team, best_pos = team, pos
    return best_team


@dataclass
class FairEstimate:
    market: SportsMarket
    outcome_index: int
    outcome_name: str
    fair_prob: float
    consensus_prob: float
    matched_game: str
    n_books: int


def match_and_estimate(
    markets: list[SportsMarket],
    games: list[Game],
    min_books: int = 3,
    blend_market_weight: float = 0.15,
    time_slack: timedelta = timedelta(hours=12),
) -> list[FairEstimate]:
    """For each Polymarket market, find the sportsbook game it corresponds to and map
    each market outcome to a consensus probability."""
    estimates: list[FairEstimate] = []

    for mkt in markets:
        text_tokens = _tokens(mkt.question) | _tokens(mkt.event_title)

        # score every candidate and keep the best — first-match with shared city
        # tokens ('new', 'york', 'boston') can lock onto a different sport's game
        game, best_score = None, 0
        for g in games:
            if mkt.game_start and abs(g.commence_time - mkt.game_start) > time_slack:
                continue
            score = _match_score(g, text_tokens)
            if score > best_score:
                game, best_score = g, score
        if game is None:
            continue

        cons = consensus_probs(game.book_odds, min_books=min_books)
        if not cons:
            continue

        for idx, outcome in enumerate(mkt.outcomes):
            # map market outcome -> game team name
            prob, matched_team = None, None
            o_tokens = _tokens(outcome)
            for team, p in cons.items():
                if _tokens(team) & o_tokens:
                    prob, matched_team = p, team
                    break
            # binary "Yes/No" markets: Yes = the team the question is *about* wins.
            # Both teams appear in "Will X beat Y?", so use the earliest-named one.
            if prob is None and outcome.lower() in ("yes", "no"):
                team = _subject_team(cons, mkt.question)
                if team is not None:
                    p = cons[team]
                    if _question_negated(mkt.question):
                        p = 1 - p  # "Will <team> lose ...?" — Yes means they don't win
                    prob = p if outcome.lower() == "yes" else 1 - p
                    matched_team = team
            if prob is None:
                continue

            market_mid = (
                mkt.outcome_prices[idx] if idx < len(mkt.outcome_prices) else None
            )
            fair = prob
            if market_mid is not None and 0 < market_mid < 1:
                w = blend_market_weight
                fair = (1 - w) * prob + w * market_mid

            estimates.append(
                FairEstimate(
                    market=mkt,
                    outcome_index=idx,
                    outcome_name=outcome,
                    fair_prob=fair,
                    consensus_prob=prob,
                    matched_game=f"{game.away_team} @ {game.home_team}",
                    n_books=(
                        sum(1 for b in game.book_odds if matched_team in b)
                        if matched_team else len(game.book_odds)
                    ),
                )
            )

    log.info("fair_value: %d outcome estimates from %d markets", len(estimates), len(markets))
    return estimates
