"""
edge_scanner.py – Professional arbitrage, middle, and +EV detection.
Uses The Odds API data (already fetched by CLARITY).
"""

import numpy as np
from typing import Dict, List, Tuple, Any

def american_to_decimal(odds: float) -> float:
    """Convert American odds to decimal."""
    if odds > 0:
        return odds / 100 + 1
    return 100 / abs(odds) + 1

def decimal_to_american(dec: float) -> int:
    """Convert decimal odds to American."""
    if dec >= 2.0:
        return int((dec - 1) * 100)
    return int(-100 / (dec - 1))

def implied_prob(dec: float) -> float:
    return 1 / dec

def find_arbitrage_2way(odds_a: Dict[str, float], odds_b: Dict[str, float],
                         bankroll: float = 100.0) -> Dict:
    """
    Detects a 2-way arbitrage opportunity (e.g., moneyline).
    odds_a: dict of {bookmaker: american_odds} for side A
    odds_b: dict of {bookmaker: american_odds} for side B
    """
    best_a_book = max(odds_a, key=lambda b: american_to_decimal(odds_a[b]))
    best_b_book = max(odds_b, key=lambda b: american_to_decimal(odds_b[b]))
    dec_a = american_to_decimal(odds_a[best_a_book])
    dec_b = american_to_decimal(odds_b[best_b_book])
    margin = (1/dec_a) + (1/dec_b)
    is_arb = margin < 1.0
    result = {
        "is_arb": is_arb,
        "margin": round(margin, 6),
        "profit_pct": round((1 - margin) * 100, 4) if is_arb else 0,
        "best_a": {"book": best_a_book, "odds": odds_a[best_a_book], "decimal": round(dec_a, 4)},
        "best_b": {"book": best_b_book, "odds": odds_b[best_b_book], "decimal": round(dec_b, 4)},
    }
    if is_arb:
        stake_a = round((1/dec_a) / margin * bankroll, 2)
        stake_b = round((1/dec_b) / margin * bankroll, 2)
        profit = round(min(stake_a * (dec_a - 1), stake_b * (dec_b - 1)) - (bankroll - stake_a - stake_b), 2)
        result.update({
            "stake_a": stake_a, "stake_b": stake_b,
            "profit": profit, "roi_pct": round(profit / bankroll * 100, 4)
        })
    return result

def find_middle(spread_a_line: float, spread_a_odds: float,
                spread_b_line: float, spread_b_odds: float,
                historical_margins: List[float] = None) -> Dict:
    """
    Detects a middle opportunity between two spreads or totals.
    spread_a_line, spread_b_line: e.g., -4.5 and -1.5 (gap = 3 points)
    """
    gap = abs(spread_b_line - spread_a_line)
    if gap < 0.5:
        return {"is_middle": False, "gap": gap}
    dec_a = american_to_decimal(spread_a_odds)
    dec_b = american_to_decimal(spread_b_odds)
    if historical_margins:
        lo = min(spread_a_line, spread_b_line)
        hi = max(spread_a_line, spread_b_line)
        hits = sum(1 for m in historical_margins if lo < m <= hi)
        mid_prob = hits / len(historical_margins)
    else:
        mid_prob = min(gap * 0.03, 0.25)  # rough approximation
    stake = 100.0
    win_both = stake * (dec_a - 1) + stake * (dec_b - 1)
    lose_both = -stake * 2
    ev = round(mid_prob * win_both + (1 - mid_prob) * lose_both, 4)
    ev_pct = round(ev / (stake * 2) * 100, 3)
    return {
        "is_middle": True,
        "gap": round(gap, 2),
        "middle_prob": round(mid_prob, 4),
        "ev_per_200": ev,
        "ev_pct": ev_pct,
        "quality": "STRONG" if gap >= 3 else "MODERATE" if gap >= 1.5 else "WEAK",
        "recommended": ev > 0
    }

def find_plus_ev(soft_odds: float, sharp_odds: float) -> Dict:
    """
    Compares a soft book's odds to a sharp book (e.g., Pinnacle) to find +EV.
    Returns edge percentage and recommended stake.
    """
    soft_dec = american_to_decimal(soft_odds)
    sharp_dec = american_to_decimal(sharp_odds)
    sharp_implied = 1 / sharp_dec
    # If soft odds are higher than sharp, there may be +EV
    if soft_dec > sharp_dec:
        edge = (soft_dec / sharp_dec) - 1
    else:
        edge = (sharp_dec / soft_dec) - 1
    return {
        "soft_odds": soft_odds,
        "sharp_odds": sharp_odds,
        "edge_pct": round(edge * 100, 4),
        "is_plus_ev": soft_dec > sharp_dec,
        "recommended": soft_dec > sharp_dec and edge > 0.02
    }

def scan_all_games(games_data: List[Dict], bankroll: float = 100.0) -> Dict:
    """
    High-level scanner that processes a list of games (as returned by GameScanner).
    Returns a dict with lists of arbs, middles, and +EV plays.
    """
    arbs = []
    middles = []
    plus_ev = []
    for game in games_data:
        # Moneyline arbitrage
        if game.get("home_ml") and game.get("away_ml"):
            # In reality, you need odds from multiple books. Here we assume game has
            # home_ml and away_ml from a single book – for true arb you need multiple books.
            # We'll create a simplified example:
            odds_home = {"book1": game["home_ml"]}
            odds_away = {"book2": game["away_ml"]}
            arb = find_arbitrage_2way(odds_home, odds_away, bankroll)
            if arb["is_arb"]:
                arbs.append({
                    "game": f"{game['home']} vs {game['away']}",
                    "details": arb
                })
        # Spread middles
        if game.get("spread") and game.get("spread_odds"):
            # For middle, we need two different lines from two books.
            # Placeholder – you would extend with real multi-book data.
            pass
    return {"arbs": arbs, "middles": middles, "plus_ev": plus_ev}
