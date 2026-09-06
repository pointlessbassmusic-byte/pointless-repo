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

        game = None
        for g in games:
            if mkt.game_start and abs(g.commence_time - mkt.game_start) > time_slack:
                continue
            if _team_in_text(g.home_team, text_tokens) and _team_in_text(g.away_team, text_tokens):
                game = g
                break
        if game is None:
            continue

        cons = consensus_probs(game.book_odds, min_books=min_books)
        if not cons:
            continue

        for idx, outcome in enumerate(mkt.outcomes):
            # map market outcome -> game team name
            prob = None
            o_tokens = _tokens(outcome)
            for team, p in cons.items():
                if _tokens(team) & o_tokens:
                    prob = p
                    break
            # binary "Yes/No" markets: question names the team; Yes = named team wins
            if prob is None and outcome.lower() in ("yes", "no"):
                for team, p in cons.items():
                    if _team_in_text(team, _tokens(mkt.question)):
                        prob = p if outcome.lower() == "yes" else 1 - p
                        break
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
                    n_books=len(game.book_odds),
                )
            )

    log.info("fair_value: %d outcome estimates from %d markets", len(estimates), len(markets))
    return estimates
