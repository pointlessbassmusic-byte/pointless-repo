"""Markov-chain scoring models for racket sports.

Tennis: point -> game -> tiebreak -> set -> match, driven by each player's
probability of winning a point on their own serve (the two-parameter model
standard since O'Malley 2008). Implemented as dynamic programs rather than
closed forms so every layer is independently testable.

Table tennis: point -> set (to 11, win by 2) -> match (best of 5/7) with a
single rally-win probability; serve alternation every 2 points is negligible
at this level.
"""

from __future__ import annotations

from functools import lru_cache


def _validate(p: float, name: str = "p") -> None:
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"{name} must be in [0,1], got {p}")


# --------------------------------------------------------------------------
# Tennis: game
# --------------------------------------------------------------------------

def game_win_prob(p: float) -> float:
    """P(server wins a standard game) given point-win-on-serve prob `p`."""
    _validate(p)
    q = 1.0 - p
    # From deuce, server wins with p^2 / (p^2 + q^2).
    if p in (0.0, 1.0):
        return p
    deuce = p * p / (p * p + q * q)

    @lru_cache(maxsize=None)
    def state(a: int, b: int) -> float:
        # a = server points, b = returner points (0..4 scale, 3-3 == deuce)
        if a >= 4 and a - b >= 2:
            return 1.0
        if b >= 4 and b - a >= 2:
            return 0.0
        if a >= 3 and b >= 3 and a == b:
            return deuce
        return p * state(a + 1, b) + q * state(a, b + 1)

    return state(0, 0)


# --------------------------------------------------------------------------
# Tennis: tiebreak
# --------------------------------------------------------------------------

def _tb_server_is_a(point_index: int) -> bool:
    """Server of 1-indexed tiebreak point: A, B, B, A, A, B, B, A, ..."""
    m = point_index % 4
    return m in (1, 0)


def tiebreak_win_prob(p_a: float, p_b: float, target: int = 7) -> float:
    """P(A wins the tiebreak). `p_a`/`p_b` are point-win probs on own serve.

    A is defined as the player serving the first point; for pre-match use the
    caller can average over both orders (the asymmetry is < 1e-3).
    `target=7` for set tiebreaks, `target=10` for match tiebreaks.
    """
    _validate(p_a, "p_a")
    _validate(p_b, "p_b")
    # From n-n (n >= target-1): decided by two-point blocks with one serve each.
    a_sweep = p_a * (1.0 - p_b)
    b_sweep = (1.0 - p_a) * p_b
    denom = a_sweep + b_sweep
    late = 0.5 if denom == 0 else a_sweep / denom

    @lru_cache(maxsize=None)
    def state(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a == b and a >= target - 1:
            return late
        point_index = a + b + 1
        pa_wins_point = p_a if _tb_server_is_a(point_index) else 1.0 - p_b
        return pa_wins_point * state(a + 1, b) + (1.0 - pa_wins_point) * state(a, b + 1)

    return state(0, 0)


# --------------------------------------------------------------------------
# Tennis: set and match
# --------------------------------------------------------------------------

def set_win_prob(p_a: float, p_b: float, a_serves_first: bool = True) -> float:
    """P(A wins a tiebreak set) from both players' serve-point probs."""
    _validate(p_a, "p_a")
    _validate(p_b, "p_b")
    g_a = game_win_prob(p_a)          # A holds serve
    g_b = game_win_prob(p_b)          # B holds serve
    # Tiebreak at 6-6; first tiebreak server is whoever did NOT serve game 12.
    # Game 12 server: if A served game 1, B serves even games, so B serves
    # game 12 and A serves the tiebreak first (and vice versa).
    tb = tiebreak_win_prob(p_a, p_b) if a_serves_first else 1.0 - tiebreak_win_prob(p_b, p_a)

    @lru_cache(maxsize=None)
    def state(a: int, b: int) -> float:
        if a >= 6 and a - b >= 2:
            return 1.0
        if b >= 6 and b - a >= 2:
            return 0.0
        if a == 6 and b == 6:
            return tb
        game_index = a + b + 1  # 1-indexed
        a_serving = (game_index % 2 == 1) == a_serves_first
        pa_wins_game = g_a if a_serving else 1.0 - g_b
        return pa_wins_game * state(a + 1, b) + (1.0 - pa_wins_game) * state(a, b + 1)

    return state(0, 0)


def best_of_win_prob(p_set: float, best_of: int = 3) -> float:
    """P(win a best-of-N series) given per-set/game win prob `p_set`."""
    _validate(p_set, "p_set")
    if best_of % 2 == 0 or best_of < 1:
        raise ValueError("best_of must be odd and >= 1")
    need = best_of // 2 + 1

    @lru_cache(maxsize=None)
    def state(a: int, b: int) -> float:
        if a == need:
            return 1.0
        if b == need:
            return 0.0
        return p_set * state(a + 1, b) + (1.0 - p_set) * state(a, b + 1)

    return state(0, 0)


def tennis_match_win_prob(p_a: float, p_b: float, best_of: int = 3) -> float:
    """P(A wins the match) from serve-point probs, symmetrized over serve order."""
    s1 = set_win_prob(p_a, p_b, a_serves_first=True)
    s2 = set_win_prob(p_a, p_b, a_serves_first=False)
    p_set = (s1 + s2) / 2.0
    return best_of_win_prob(p_set, best_of)


def serve_probs_for_match_prob(
    target: float,
    base: float = 0.62,
    best_of: int = 3,
    tol: float = 1e-6,
) -> tuple[float, float]:
    """Invert the chain: find symmetric serve probs (base+d, base-d) whose
    match win probability equals `target`. Lets an Elo match probability be
    decomposed into point-level parameters (for derivative markets or blends).
    """
    _validate(target, "target")
    lo, hi = -base, min(1.0 - base, base)  # keep both probs in [0,1]
    for _ in range(80):
        mid = (lo + hi) / 2.0
        p = tennis_match_win_prob(base + mid, base - mid, best_of)
        if abs(p - target) < tol:
            break
        if p < target:
            lo = mid
        else:
            hi = mid
    d = (lo + hi) / 2.0
    return base + d, base - d


# --------------------------------------------------------------------------
# Table tennis
# --------------------------------------------------------------------------

def tt_set_win_prob(p: float, target: int = 11) -> float:
    """P(win a table-tennis set to `target`, win by 2) with rally-win prob `p`."""
    _validate(p)
    if p in (0.0, 1.0):
        return p
    q = 1.0 - p
    deuce = p * p / (p * p + q * q)  # from (target-1, target-1), win by 2

    @lru_cache(maxsize=None)
    def state(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a == b and a >= target - 1:
            return deuce
        return p * state(a + 1, b) + q * state(a, b + 1)

    return state(0, 0)


def tt_match_win_prob(p_point: float, best_of: int = 5) -> float:
    """P(win a table-tennis match, best of 5/7 sets) from rally-win prob."""
    return best_of_win_prob(tt_set_win_prob(p_point), best_of)


def tt_point_prob_for_match_prob(target: float, best_of: int = 5, tol: float = 1e-6) -> float:
    """Invert tt_match_win_prob: match prob -> rally-win prob."""
    _validate(target, "target")
    lo, hi = 0.0, 1.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if tt_match_win_prob(mid, best_of) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


# --------------------------------------------------------------------------
# In-play: win probability from a live score, and point leverage
# --------------------------------------------------------------------------
# Used for (1) fair value during play and (2) DEFENSIVE quoting: the
# leverage of the next point (how far fair value jumps on it) says when a
# resting order is about to be picked off by someone who saw the point first.
# A maker should pull or widen before high-leverage points (deuce at 4-4 in a
# deciding set moves fair ~10c), not after.

def _game_state(p: float, s: int, r: int) -> float:
    """P(server wins the game) from server points `s`, returner points `r`."""
    if s >= 4 and s - r >= 2:
        return 1.0
    if r >= 4 and r - s >= 2:
        return 0.0
    if p in (0.0, 1.0):
        return p
    q = 1.0 - p
    if s >= 3 and r >= 3:
        deuce = p * p / (p * p + q * q)
        if s == r:
            return deuce
        return p + q * deuce if s > r else p * deuce
    return p * _game_state(p, s + 1, r) + q * _game_state(p, s, r + 1)


def _tb_state(p_a: float, p_b: float, a: int, b: int, a_first: bool, target: int) -> float:
    """P(A wins a tiebreak at a-b points) when `a_first` = A served point 1."""
    if a >= target and a - b >= 2:
        return 1.0
    if b >= target and b - a >= 2:
        return 0.0
    if a == b and a >= target - 1:
        a_sweep, b_sweep = p_a * (1.0 - p_b), (1.0 - p_a) * p_b
        return 0.5 if a_sweep + b_sweep == 0 else a_sweep / (a_sweep + b_sweep)
    if a >= target - 1 and b >= target - 1:           # one point ahead late: reduce
        lead = a - b
        a, b = target - 1 + max(lead, 0), target - 1 + max(-lead, 0)
    a_serving = _tb_server_is_a(a + b + 1) == a_first
    pa = p_a if a_serving else 1.0 - p_b
    return pa * _tb_state(p_a, p_b, a + 1, b, a_first, target) + \
        (1.0 - pa) * _tb_state(p_a, p_b, a, b + 1, a_first, target)


def tennis_win_prob_from_state(p_a: float, p_b: float, *, sets_a: int = 0, sets_b: int = 0,
                               games_a: int = 0, games_b: int = 0, pts_a: int = 0,
                               pts_b: int = 0, server_a: bool = True, best_of: int = 3,
                               final_tb_target: int = 7) -> float:
    """P(A wins the match) from a live score.

    `pts_*` are points in the current game (0,1,2,3,4... — 40-40 is 3-3), or
    tiebreak points when the set is at 6-6. `server_a` is who serves the
    current game; during a tiebreak it is who served the tiebreak's FIRST
    point. `final_tb_target` = 10 for deciding-set match tiebreaks.
    """
    _validate(p_a, "p_a")
    _validate(p_b, "p_b")
    if best_of % 2 == 0 or best_of < 1:
        raise ValueError("best_of must be odd and >= 1")
    need = best_of // 2 + 1
    g_a, g_b = game_win_prob(p_a), game_win_prob(p_b)

    @lru_cache(maxsize=None)
    def at_game_start(sa: int, sb: int, ga: int, gb: int, srv_a: bool) -> float:
        if sa >= need:
            return 1.0
        if sb >= need:
            return 0.0
        if ga == 6 and gb == 6:
            target = final_tb_target if sa + sb == best_of - 1 else 7
            pt = _tb_state(p_a, p_b, 0, 0, srv_a, target)
            return pt * at_game_start(sa + 1, sb, 0, 0, not srv_a) + \
                (1.0 - pt) * at_game_start(sa, sb + 1, 0, 0, not srv_a)
        pg = g_a if srv_a else 1.0 - g_b
        return pg * after_game(sa, sb, ga + 1, gb, srv_a) + \
            (1.0 - pg) * after_game(sa, sb, ga, gb + 1, srv_a)

    def after_game(sa: int, sb: int, ga: int, gb: int, last_srv_a: bool) -> float:
        if ga >= 6 and ga - gb >= 2:
            return at_game_start(sa + 1, sb, 0, 0, not last_srv_a)
        if gb >= 6 and gb - ga >= 2:
            return at_game_start(sa, sb + 1, 0, 0, not last_srv_a)
        return at_game_start(sa, sb, ga, gb, not last_srv_a)

    if sets_a >= need:
        return 1.0
    if sets_b >= need:
        return 0.0
    if games_a == 6 and games_b == 6:                 # mid-tiebreak
        target = final_tb_target if sets_a + sets_b == best_of - 1 else 7
        pt = _tb_state(p_a, p_b, pts_a, pts_b, server_a, target)
        return pt * at_game_start(sets_a + 1, sets_b, 0, 0, not server_a) + \
            (1.0 - pt) * at_game_start(sets_a, sets_b + 1, 0, 0, not server_a)
    if server_a:
        pg = _game_state(p_a, pts_a, pts_b)
    else:
        pg = 1.0 - _game_state(p_b, pts_b, pts_a)
    return pg * after_game(sets_a, sets_b, games_a + 1, games_b, server_a) + \
        (1.0 - pg) * after_game(sets_a, sets_b, games_a, games_b + 1, server_a)


def tennis_point_leverage(p_a: float, p_b: float, **state) -> float:
    """How far P(A wins the match) moves on the next point: P(.|A wins it) −
    P(.|A loses it), from the same live-score arguments. In [0, 1]."""
    a_won = dict(state, pts_a=state.get("pts_a", 0) + 1)
    b_won = dict(state, pts_b=state.get("pts_b", 0) + 1)
    return (tennis_win_prob_from_state(p_a, p_b, **a_won)
            - tennis_win_prob_from_state(p_a, p_b, **b_won))


def tt_win_prob_from_state(p: float, *, sets_a: int = 0, sets_b: int = 0, pts_a: int = 0,
                           pts_b: int = 0, best_of: int = 5, target: int = 11) -> float:
    """Table tennis: P(A wins the match) from sets and points in the current set."""
    _validate(p)
    need = best_of // 2 + 1
    if sets_a >= need:
        return 1.0
    if sets_b >= need:
        return 0.0

    def set_state(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a >= target - 1 and b >= target - 1:
            q = 1.0 - p
            deuce = 0.5 if p * p + q * q == 0 else p * p / (p * p + q * q)
            if a == b:
                return deuce
            return p + q * deuce if a > b else p * deuce
        return p * set_state(a + 1, b) + (1.0 - p) * set_state(a, b + 1)

    p_set = tt_set_win_prob(p, target)

    @lru_cache(maxsize=None)
    def from_sets(sa: int, sb: int) -> float:
        if sa >= need:
            return 1.0
        if sb >= need:
            return 0.0
        return p_set * from_sets(sa + 1, sb) + (1.0 - p_set) * from_sets(sa, sb + 1)

    ps = set_state(pts_a, pts_b)
    return ps * from_sets(sets_a + 1, sets_b) + (1.0 - ps) * from_sets(sets_a, sets_b + 1)
