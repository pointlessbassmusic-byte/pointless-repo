"""Canonical team names for the US team sports the venues list under
different labels.

Kalshi labels a side by city ("Memphis") and carries the league code in
the ticker tail (KXNBAGAME-26OCT09MEMCHI-MEM); Polymarket labels it by
nickname ("Grizzlies"); The Odds API, which the sharp line comes from,
uses the full name ("Memphis Grizzlies"). The entity matcher is
deliberately conservative (both participants must clear 0.85), and
"Grizzlies" against "Memphis Grizzlies" scores 0.75, so without one
canonical form no market in these sports would ever be priced.

Resolution is per sport (the Panthers, Kings, Jets and Giants exist in two
leagues each) and refuses ambiguity: a bare city shared by two teams in a
league ("Los Angeles", "New York") resolves to nothing unless the code
says which. Unknown -> None, never a guess.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from sportsbot.core.types import Sport

NBA: dict[str, str] = {
    "ATL": "Atlanta Hawks", "BOS": "Boston Celtics", "BKN": "Brooklyn Nets",
    "CHA": "Charlotte Hornets", "CHI": "Chicago Bulls", "CLE": "Cleveland Cavaliers",
    "DAL": "Dallas Mavericks", "DEN": "Denver Nuggets", "DET": "Detroit Pistons",
    "GSW": "Golden State Warriors", "HOU": "Houston Rockets", "IND": "Indiana Pacers",
    "LAC": "Los Angeles Clippers", "LAL": "Los Angeles Lakers", "MEM": "Memphis Grizzlies",
    "MIA": "Miami Heat", "MIL": "Milwaukee Bucks", "MIN": "Minnesota Timberwolves",
    "NOP": "New Orleans Pelicans", "NYK": "New York Knicks", "OKC": "Oklahoma City Thunder",
    "ORL": "Orlando Magic", "PHI": "Philadelphia 76ers", "PHX": "Phoenix Suns",
    "POR": "Portland Trail Blazers", "SAC": "Sacramento Kings", "SAS": "San Antonio Spurs",
    "TOR": "Toronto Raptors", "UTA": "Utah Jazz", "WAS": "Washington Wizards",
}
NBA_ALIASES = {"GS": "GSW", "NO": "NOP", "NY": "NYK", "SA": "SAS", "PHO": "PHX",
               "BRK": "BKN", "CHO": "CHA", "UTAH": "UTA", "WSH": "WAS"}

NFL: dict[str, str] = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LAC": "Los Angeles Chargers", "LAR": "Los Angeles Rams",
    "LV": "Las Vegas Raiders", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}
NFL_ALIASES = {"JAC": "JAX", "WSH": "WAS", "GNB": "GB", "KAN": "KC", "NWE": "NE",
               "NOR": "NO", "SFO": "SF", "TAM": "TB", "LVR": "LV"}

NHL: dict[str, str] = {
    "ANA": "Anaheim Ducks", "BOS": "Boston Bruins", "BUF": "Buffalo Sabres",
    "CGY": "Calgary Flames", "CAR": "Carolina Hurricanes", "CHI": "Chicago Blackhawks",
    "COL": "Colorado Avalanche", "CBJ": "Columbus Blue Jackets", "DAL": "Dallas Stars",
    "DET": "Detroit Red Wings", "EDM": "Edmonton Oilers", "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings", "MIN": "Minnesota Wild", "MTL": "Montreal Canadiens",
    "NSH": "Nashville Predators", "NJD": "New Jersey Devils", "NYI": "New York Islanders",
    "NYR": "New York Rangers", "OTT": "Ottawa Senators", "PHI": "Philadelphia Flyers",
    "PIT": "Pittsburgh Penguins", "SJS": "San Jose Sharks", "SEA": "Seattle Kraken",
    "STL": "St. Louis Blues", "TBL": "Tampa Bay Lightning", "TOR": "Toronto Maple Leafs",
    "UTA": "Utah Mammoth", "VAN": "Vancouver Canucks", "VGK": "Vegas Golden Knights",
    "WSH": "Washington Capitals", "WPG": "Winnipeg Jets",
}
NHL_ALIASES = {"LA": "LAK", "NJ": "NJD", "SJ": "SJS", "TB": "TBL", "UTAH": "UTA",
               "MON": "MTL", "WAS": "WSH", "VEG": "VGK", "CLB": "CBJ"}

TABLES: dict[Sport, tuple[dict[str, str], dict[str, str]]] = {
    Sport.BASKETBALL: (NBA, NBA_ALIASES),
    Sport.FOOTBALL: (NFL, NFL_ALIASES),
    Sport.HOCKEY: (NHL, NHL_ALIASES),
}

# The Odds API sport keys these tables serve, so the sharp collector can
# canonicalise the book's names as it stores them.
ODDS_KEY_SPORT = {"basketball_nba": Sport.BASKETBALL,
                  "americanfootball_nfl": Sport.FOOTBALL,
                  "icehockey_nhl": Sport.HOCKEY}

# Full names The Odds API spells differently from the canonical table.
ODDS_NAME_ALIASES = {
    Sport.BASKETBALL: {"la clippers": "Los Angeles Clippers"},
    Sport.FOOTBALL: {},
    Sport.HOCKEY: {"montreal canadiens": "Montreal Canadiens",
                   "st louis blues": "St. Louis Blues", "utah hockey club": "Utah Mammoth"},
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).strip()


def is_team_sport(sport: Optional[Sport]) -> bool:
    return sport in TABLES


def by_code(sport: Sport, code: str) -> Optional[str]:
    table, aliases = TABLES[sport]
    c = (code or "").upper().strip()
    return table.get(aliases.get(c, c))


def canonical(sport: Optional[Sport], text: str) -> Optional[str]:
    """Full canonical name for any venue label of a team in `sport`, or
    None. Accepts a league code, the full name, the nickname (including
    two-word nicknames), 'City Nickname' variants, or a city that names
    exactly one team in the league."""
    if sport not in TABLES or not text:
        return None
    table, _ = TABLES[sport]
    raw = str(text).strip()
    hit = by_code(sport, raw)
    if hit and len(raw) <= 4:
        return hit
    t = _norm(raw)
    if not t:
        return None
    alias = ODDS_NAME_ALIASES.get(sport, {}).get(t)
    if alias:
        return alias
    full = {_norm(v): v for v in table.values()}
    if t in full:
        return full[t]
    # nickname: the trailing one or two words of the full name
    nick_hits = []
    for name in table.values():
        words = _norm(name).split()
        for k in (1, 2):
            if len(words) > k and t == " ".join(words[-k:]):
                nick_hits.append(name)
                break
    if len(set(nick_hits)) == 1:
        return nick_hits[0]
    # 'city nickname' with a different city spelling, e.g. 'LA Lakers'
    tail_hits = [name for name in table.values()
                 if t.endswith(_norm(name).split()[-1]) and len(t.split()) >= 2
                 and _norm(name).split()[-1] in t.split()]
    if len(set(tail_hits)) == 1 and t.split()[-1] == _norm(tail_hits[0]).split()[-1]:
        return tail_hits[0]
    # bare city, only when unique in the league
    city_hits = [name for name in table.values()
                 if _norm(name).startswith(t + " ") or _norm(name).rsplit(" ", 1)[0] == t]
    if len(set(city_hits)) == 1:
        return city_hits[0]
    return None


def canonical_for_odds_key(sport_key: str, name: str) -> str:
    """Canonicalise a sportsbook participant name when the sport has a
    table; other sports (tennis players) pass through unchanged."""
    sport = ODDS_KEY_SPORT.get(sport_key)
    if sport is None:
        return name
    return canonical(sport, name) or name
