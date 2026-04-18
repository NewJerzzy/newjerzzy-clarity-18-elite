"""
CLARITY 18.0 ELITE – UNIFIED QUICK SCANNER (Final 5-tab, Clarity-colored engine)

Tabs:
1. 🎮 GAME MARKETS        – Live game lines, alternate lines, parlays
2. 📋 PASTE & SCAN        – Paste text OR upload screenshot → OCR → props, slips, tickets
3. 📊 SCANNERS & ACCURACY – Best odds, arbitrage, middles, win rate
4. 🎯 PLAYER PROPS        – Manual dropdown analyzer
5. 🔧 SELF EVALUATION     – Auto-settle, pending bets, tuning history, SEM-style evaluation

Key behavior:
- PASTE & SCAN separates PLAYER PROPS and GAME MARKETS into two clean tables.
- Player props: Clarity ignores the slip’s pick and chooses OVER / UNDER / PASS itself.
- Each prop row shows:
    🟢/🔴 badge + Clarity pick + Edge % + Win % + Last 8 Avg.
- Game markets: ML / spreads / totals / alternate lines labeled and evaluated.
- Alternate lines supported (ALT_ML, ALT_SPREAD, ALT_TOTAL_OVER, ALT_TOTAL_UNDER).
- OCR supported via ocr.space.
"""

import re
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd
import requests
import streamlit as st
from scipy.stats import norm

# =============================================================================
# CONFIGURATION – YOUR API KEYS (kept hard-coded)
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

VERSION = "18.0 Elite (Unified Quick Scanner – Final 5-tab Clarity Colored)"
BUILD_DATE = "2026-04-17"

ODDS_API_IO_BASE = "https://api.odds-api.io/v4"
BALLSDONTLIE_BASE = "https://api.balldontlie.io/v1"
API_SPORTS_BASE = "https://v1.api-sports.io"

DB_PATH = "clarity_elite.db"

# =============================================================================
# BASIC SPORT CONFIG
# =============================================================================
SPORT_MODELS: Dict[str, Dict[str, Any]] = {
    "NBA": {"avg_total": 228.5},
    "NFL": {"avg_total": 44.5},
    "MLB": {"avg_total": 8.5},
    "NHL": {"avg_total": 6.0},
}

STAT_CONFIG: Dict[str, Dict[str, Any]] = {
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "STL": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "BLK": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "THREES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
}

RED_TIER_PROPS = ["PRA", "PR", "PA"]

# =============================================================================
# DB HELPERS (SAFE)
# =============================================================================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            source TEXT,
            sport TEXT,
            player TEXT,
            market TEXT,
            line REAL,
            pick TEXT,
            opponent TEXT,
            game_date TEXT,
            result TEXT,
            actual REAL
        )
        """
    )
    conn.commit()
    conn.close()

def insert_bet(row: Dict[str, Any]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO bets (
            created_at, source, sport, player, market, line, pick,
            opponent, game_date, result, actual
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.utcnow().isoformat(),
            row.get("source", ""),
            row.get("sport", ""),
            row.get("player", ""),
            row.get("market", ""),
            row.get("line", 0.0),
            row.get("pick", ""),
            row.get("opponent", ""),
            row.get("game_date", ""),
            row.get("result", ""),
            row.get("actual", 0.0),
        ),
    )
    conn.commit()
    conn.close()

def get_pending_bets() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, sport, player, market, line, pick, opponent, game_date "
        "FROM bets WHERE result = '' OR result IS NULL"
    )
    rows = cur.fetchall()
    conn.close()
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "sport": r[1],
                "player": r[2],
                "market": r[3],
                "line": r[4],
                "pick": r[5],
                "opponent": r[6],
                "game_date": r[7],
            }
        )
    return out

def get_recent_bets(limit: int = 100) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, created_at, sport, player, market, line, pick, opponent, game_date, result, actual "
        "FROM bets ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    cols = ["ID", "Created", "Sport", "Player", "Market", "Line", "Pick", "Opponent", "Game Date", "Result", "Actual"]
    return pd.DataFrame(rows, columns=cols)

def update_bet_result(bet_id: int, result: str, actual: float) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE bets SET result = ?, actual = ? WHERE id = ?",
        (result, actual, bet_id),
    )
    conn.commit()
    conn.close()

# =============================================================================
# TIMING WARNING
# =============================================================================
def check_scan_timing(sport: str) -> None:
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()

    if sport in ["NBA", "MLB", "NHL"]:
        if hour not in [6, 14, 21]:
            st.warning(
                "⏰ Optimal scanning times for NBA/MLB/NHL are 6 AM, 2 PM, and 9 PM. "
                "Current time may yield less stable lines."
            )
    elif sport == "NFL":
        if not (
            (weekday == 0 and 9 <= hour <= 11)
            or (weekday == 1 and 5 <= hour <= 7)
            or (weekday == 6 and 9 <= hour <= 11)
        ):
            st.warning(
                "🏈 NFL lines are best scanned Monday 10 AM, Tuesday 6 AM, or Sunday 10 AM. "
                "Current time may not capture optimal value."
            )

# =============================================================================
# OCR.SPACE HELPER
# =============================================================================
def ocr_space_image(image_bytes: bytes) -> str:
    try:
        url = "https://api.ocr.space/parse/image"
        files = {"file": ("image.png", image_bytes)}
        data = {
            "apikey": OCR_SPACE_API_KEY,
            "language": "eng",
            "OCREngine": 2,
        }
        r = requests.post(url, files=files, data=data, timeout=30)
        if r.status_code != 200:
            return ""
        js = r.json()
        parsed_results = js.get("ParsedResults", [])
        texts = []
        for pr in parsed_results:
            t = pr.get("ParsedText", "")
            if t:
                texts.append(t)
        return "\n".join(texts).strip()
    except Exception:
        return ""

# =============================================================================
# BALSDONTLIE HELPERS (NBA)
# =============================================================================
def balldontlie_request(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        headers = {"Authorization": BALLSDONTLIE_API_KEY}
        url = f"{BALLSDONTLIE_BASE}{endpoint}"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def fetch_nba_recent_stat(player_name: str, market: str, num_games: int = 8) -> List[float]:
    try:
        players_data = balldontlie_request("/players", params={"search": player_name})
        if not players_data or not players_data.get("data"):
            return []
        player_id = players_data["data"][0]["id"]

        stats_data = balldontlie_request(
            "/stats",
            params={"player_ids[]": player_id, "per_page": num_games},
        )
        if not stats_data or not stats_data.get("data"):
            return []

        vals: List[float] = []
        market_upper = market.upper()
        for g in stats_data["data"]:
            if market_upper == "PTS":
                v = g.get("pts", 0)
            elif market_upper == "REB":
                v = g.get("reb", 0)
            elif market_upper == "AST":
                v = g.get("ast", 0)
            elif market_upper == "PRA":
                v = g.get("pts", 0) + g.get("reb", 0) + g.get("ast", 0)
            elif market_upper == "PR":
                v = g.get("pts", 0) + g.get("reb", 0)
            elif market_upper == "PA":
                v = g.get("pts", 0) + g.get("ast", 0)
            else:
                v = g.get("pts", 0)
            vals.append(float(v) if v is not None else 0.0)
        return vals
    except Exception:
        return []

def balldontlie_settle_prop(
    player: str,
    market: str,
    line: float,
    pick: str,
    game_date: str,
) -> Tuple[str, float]:
    try:
        players_data = balldontlie_request("/players", params={"search": player})
        if not players_data or not players_data.get("data"):
            return "PENDING", 0.0
        player_id = players_data["data"][0]["id"]

        stats_data = balldontlie_request(
            "/stats",
            params={"player_ids[]": player_id, "dates[]": game_date},
        )
        if not stats_data or not stats_data.get("data"):
            return "PENDING", 0.0

        stats = stats_data["data"][0]
        market_upper = market.upper()
        if market_upper == "PRA":
            actual_val = stats.get("pts", 0) + stats.get("reb", 0) + stats.get("ast", 0)
        elif market_upper == "PR":
            actual_val = stats.get("pts", 0) + stats.get("reb", 0)
        elif market_upper == "PA":
            actual_val = stats.get("pts", 0) + stats.get("ast", 0)
        elif market_upper == "PTS":
            actual_val = stats.get("pts", 0)
        elif market_upper == "REB":
            actual_val = stats.get("reb", 0)
        elif market_upper == "AST":
            actual_val = stats.get("ast", 0)
        else:
            actual_val = stats.get("pts", 0)

        actual_val = float(actual_val)
        won = (actual_val > line) if pick.upper() == "OVER" else (actual_val < line)
        return ("WIN" if won else "LOSS"), actual_val
    except Exception:
        return "PENDING", 0.0

# =============================================================================
# SIMPLE EDGE ESTIMATION (for manual tab)
# =============================================================================
def estimate_edge_from_history(
    values: List[float],
    line: float,
    pick: str,
) -> Tuple[float, float]:
    if not values:
        return 0.0, 0.5

    mu = float(np.mean(values))
    sigma = float(np.std(values) + 1e-6)

    if pick.upper() == "OVER":
        prob = 1.0 - norm.cdf(line, loc=mu, scale=sigma)
    else:
        prob = norm.cdf(line, loc=mu, scale=sigma)

    edge = prob - 0.5
    return edge, prob

# =============================================================================
# ODDS FETCHER (GAME MARKETS)
# =============================================================================
def fetch_game_markets(sport_key: str = "basketball_nba") -> List[Dict[str, Any]]:
    try:
        url = f"{ODDS_API_IO_BASE}/sports/{sport_key}/odds"
        params = {
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "apiKey": ODDS_API_IO_KEY,
        }
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        return data
    except Exception:
        return []

def summarize_best_odds(odds_data: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for game in odds_data:
        home = game.get("home_team")
        away = game.get("away_team")
        commence = game.get("commence_time", "")
        markets = game.get("bookmakers", [])

        best_home_ml = None
        best_away_ml = None
        best_home_book = ""
        best_away_book = ""

        for book in markets:
            key = book.get("key", "")
            for m in book.get("markets", []):
                if m.get("key") == "h2h":
                    outcomes = m.get("outcomes", [])
                    for o in outcomes:
                        if o.get("name") == home:
                            price = o.get("price")
                            if price is not None and (best_home_ml is None or price > best_home_ml):
                                best_home_ml = price
                                best_home_book = key
                        elif o.get("name") == away:
                            price = o.get("price")
                            if price is not None and (best_away_ml is None or price > best_away_ml):
                                best_away_ml = price
                                best_away_book = key

        rows.append(
            {
                "Game": f"{away} @ {home}",
                "Commence": commence,
                "Best Home ML": best_home_ml,
                "Home Book": best_home_book,
                "Best Away ML": best_away_ml,
                "Away Book": best_away_book,
            }
        )
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# =============================================================================
# AUTO-SETTLE WRAPPER
# =============================================================================
def auto_settle_prop(
    player: str,
    market: str,
    line: float,
    pick: str,
    sport: str,
    opponent: str,
    game_date: Optional[str] = None,
) -> Tuple[str, float]:
    if not game_date:
        game_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    if sport == "NBA":
        return balldontlie_settle_prop(player, market, line, pick, game_date)

    return "PENDING", 0.0

# =============================================================================
# PASTEBOARD PARSERS – GAME SLIPS + PLAYER PROPS (incl. alt lines)
# =============================================================================

TEAM_NAMES = [
    "Toronto Raptors", "Cleveland Cavaliers", "Minnesota Timberwolves", "Denver Nuggets",
    "St. Louis Cardinals", "Houston Astros", "Los Angeles Dodgers", "Colorado Rockies",
    "San Diego Padres", "Los Angeles Angels", "Texas Rangers", "Seattle Mariners",
]

TEAM_TOKEN_PATTERN = re.compile(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+")

PROP_PATTERN = re.compile(
    r"(?P<player>[A-Za-z .'-]+)\s+(?P<line>\d+\.?\d*)\s*(Points|Rebounds|Assists|PRA|PR|PA|PTS|REB|AST)?",
    re.IGNORECASE,
)

SPREAD_PATTERN = re.compile(
    r"(?P<sign>[+-])(?P<num>\d+\.?\d*)\s*\((?P<price>-?\d+)\)",
    re.IGNORECASE,
)

TOTAL_PATTERN = re.compile(
    r"[OU]\s*?(?P<num>\d+\.?\d*)\s*\((?P<price>-?\d+)\)",
    re.IGNORECASE,
)

TOTAL_PATTERN_ALT = re.compile(
    r"(O|U)\s+(?P<num>\d+\.?\d*)\s+(?P<price>-?\d+)",
    re.IGNORECASE,
)

MONEYLINE_PATTERN = re.compile(
    r"(?P<ml>[+-]\d{2,4})",
    re.IGNORECASE,
)

def detect_teams_in_block(block: str) -> List[str]:
    found = []
    for t in TEAM_NAMES:
        if t.lower() in block.lower():
            found.append(t)
    if not found:
        for m in TEAM_TOKEN_PATTERN.findall(block):
            if m not in found:
                found.append(m)
    return found[:2]

def parse_game_slips(text: str, default_sport: str) -> List[Dict[str, Any]]:
    blocks = re.split(r"\n\s*\n", text)
    results: List[Dict[str, Any]] = []

    for block in blocks:
        b = block.strip()
        if not b:
            continue

        teams = detect_teams_in_block(b)
        if len(teams) < 2:
            continue
        team_a, team_b = teams[0], teams[1]

        spreads = SPREAD_PATTERN.findall(b)
        totals = TOTAL_PATTERN.findall(b) + TOTAL_PATTERN_ALT.findall(b)
        mls = MONEYLINE_PATTERN.findall(b)

        # Treat first spread as main, others as alt
        for i, sp in enumerate(spreads):
            sign, num, price = sp
            line = float(num) if sign == "+" else -float(num)
            team = team_a if i % 2 == 0 else team_b
            market_type = "SPREAD" if i < 2 else "ALT_SPREAD"
            results.append(
                {
                    "type": "GAME",
                    "sport": default_sport,
                    "team": team,
                    "opponent": team_b if team == team_a else team_a,
                    "market_type": market_type,
                    "line": line,
                    "price": int(price),
                    "raw_block": b,
                }
            )

        # First total as main, others as alt
        for j, t in enumerate(totals):
            if len(t) == 2:
                num, price = t
                ou = "O"
            else:
                ou, num, price = t
            line = float(num)
            base_type = "TOTAL_OVER" if ou.upper() == "O" else "TOTAL_UNDER"
            market_type = base_type if j < 2 else "ALT_" + base_type
            results.append(
                {
                    "type": "GAME",
                    "sport": default_sport,
                    "team": "",
                    "opponent": "",
                    "market_type": market_type,
                    "line": line,
                    "price": int(price),
                    "raw_block": b,
                }
            )

        # First two ML as main, others as alt
        for k, ml in enumerate(mls):
            team = team_a if k % 2 == 0 else team_b
            market_type = "ML" if k < 2 else "ALT_ML"
            results.append(
                {
                    "type": "GAME",
                    "sport": default_sport,
                    "team": team,
                    "opponent": team_b if team == team_a else team_a,
                    "market_type": market_type,
                    "line": 0.0,
                    "price": int(ml),
                    "raw_block": b,
                }
            )

    return results

def parse_player_props(text: str, default_sport: str, source: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    results: List[Dict[str, Any]] = []

    for i, line in enumerate(lines):
        m = PROP_PATTERN.search(line)
        if not m:
            continue
        d = m.groupdict()
        player = d["player"].strip()
        line_val = float(d["line"])

        market = "PTS"
        if "rebound" in line.lower():
            market = "REB"
        elif "assist" in line.lower():
            market = "AST"

        results.append(
            {
                "type": "PROP",
                "source": source,
                "sport": default_sport,
                "player": player,
                "market": market,
                "line": line_val,
                "opponent": "",
                "game_date": "",
            }
        )

    return results

def parse_pasteboard_unified(text: str, default_sport: str, source: str) -> List[Dict[str, Any]]:
    props = parse_player_props(text, default_sport, source)
    games = parse_game_slips(text, default_sport)
    return props + games

# =============================================================================
# CLARITY DECISION ENGINE
# =============================================================================
def clarity_decision_for_prop(row: Dict[str, Any]) -> Tuple[str, str, str, float, float, int, float]:
    """
    Returns:
        decision: 'APPROVED' or 'PASS'
        reason: text explanation
        clarity_pick: 'OVER', 'UNDER', or 'PASS'
        edge: float (0–1)
        win_prob: float (0–1)
        games_used: int
        mean_stat: float
    """
    history: List[float] = []
    if row["sport"] == "NBA" and row["market"] in ["PTS", "REB", "AST", "PRA", "PR", "PA"]:
        history = fetch_nba_recent_stat(row["player"], row["market"], num_games=8)

    if not history:
        return (
            "PASS",
            "No recent data available – cannot compute edge.",
            "PASS",
            0.0,
            0.5,
            0,
            0.0,
        )

    mu = float(np.mean(history))
    sigma = float(np.std(history) + 1e-6)
    line = float(row["line"])

    prob_over = 1.0 - norm.cdf(line, loc=mu, scale=sigma)
    prob_under = norm.cdf(line, loc=mu, scale=sigma)

    if prob_over > prob_under:
        clarity_pick = "OVER"
        win_prob = prob_over
    else:
        clarity_pick = "UNDER"
        win_prob = prob_under

    edge = win_prob - 0.5
    tier_info = STAT_CONFIG.get(row["market"], {"tier": "LOW", "buffer": 0.0, "reject": False})

    if tier_info["reject"]:
        return (
            "PASS",
            f"Red-tier market ({row['market']}) – not Clarity approved.",
            "PASS",
            edge,
            win_prob,
            len(history),
            mu,
        )

    if edge * 100 >= 8.0:
        return (
            "APPROVED",
            f"Edge {edge*100:.1f}% with win prob {win_prob*100:.1f}%.",
            clarity_pick,
            edge,
            win_prob,
            len(history),
            mu,
        )
    else:
        return (
            "PASS",
            f"Edge too small ({edge*100:.1f}%).",
            "PASS",
            edge,
            win_prob,
            len(history),
            mu,
        )

def clarity_decision_for_game(row: Dict[str, Any]) -> Tuple[str, str]:
    mt = row["market_type"]
    price = row.get("price", 0)

    if price <= 0:
        implied = abs(price) / (abs(price) + 100)
    else:
        implied = 100 / (price + 100)

    is_alt = mt.startswith("ALT_")

    if mt.endswith("ML"):
        if implied < 0.40 or implied > 0.65:
            tag = "Alternate ML" if is_alt else "ML"
            return "APPROVED", f"{tag} with implied {implied*100:.1f}% – outside typical coinflip zone."
        else:
            return "PASS", f"Moneyline too close to coinflip ({implied*100:.1f}%)."
    elif "TOTAL" in mt:
        return "PASS", "Totals (including alternate) currently not modeled – not Clarity approved."
    elif "SPREAD" in mt:
        return "PASS", "Spreads (including alternate) not modeled yet – defaulting to PASS."
    else:
        return "PASS", "Unknown market type – PASS."

def analyze_and_store_unified(parsed: List[Dict[str, Any]]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    prop_rows = []
    game_rows = []

    for p in parsed:
        if p["type"] == "PROP":
            decision, reason, clarity_pick, edge, prob, games_used, mean_stat = clarity_decision_for_prop(p)
            tier_info = STAT_CONFIG.get(p["market"], {"tier": "LOW", "buffer": 0.0, "reject": False})

            if decision == "APPROVED":
                badge = (
                    f"<span style='color:green;font-weight:bold'>🟢 CLARITY SAYS: {clarity_pick}</span><br>"
                    f"<span style='font-size:12px'>Edge: {edge*100:.1f}% | "
                    f"Win Prob: {prob*100:.1f}% | Last 8 Avg: {mean_stat:.1f}</span>"
                )
            else:
                badge = (
                    f"<span style='color:red;font-weight:bold'>🔴 CLARITY SAYS: PASS</span><br>"
                    f"<span style='font-size:12px'>Edge: {edge*100:.1f}% | "
                    f"Win Prob: {prob*100:.1f}% | Last 8 Avg: {mean_stat:.1f}</span>"
                )

            prop_rows.append(
                {
                    "Player": p["player"],
                    "Market": p["market"],
                    "Line": p["line"],
                    "Sport": p["sport"],
                    "Games Used": games_used,
                    "Last 8 Avg": round(mean_stat, 2),
                    "Edge %": round(edge * 100, 1),
                    "Win Prob %": round(prob * 100, 1),
                    "Tier": tier_info["tier"],
                    "Red Tier": tier_info["reject"],
                    "Clarity Decision": decision,
                    "Clarity Summary": badge,
                    "Reason": reason,
                }
            )

            insert_bet(
                {
                    "source": p.get("source", "PASTE"),
                    "sport": p["sport"],
                    "player": p["player"],
                    "market": p["market"],
                    "line": p["line"],
                    "pick": clarity_pick,
                    "opponent": p.get("opponent", ""),
                    "game_date": p.get("game_date", ""),
                    "result": "",
                    "actual": 0.0,
                }
            )

        elif p["type"] == "GAME":
            decision, reason = clarity_decision_for_game(p)

            if decision == "APPROVED":
                badge = (
                    f"<span style='color:green;font-weight:bold'>🟢 {decision}</span><br>"
                    f"<span style='font-size:12px'>{reason}</span>"
                )
            else:
                badge = (
                    f"<span style='color:red;font-weight:bold'>🔴 {decision}</span><br>"
                    f"<span style='font-size:12px'>{reason}</span>"
                )

            game_rows.append(
                {
                    "Team": p["team"] or "TOTAL",
                    "Opponent": p.get("opponent", ""),
                    "Market Type": p["market_type"],
                    "Line": p["line"],
                    "Price": p.get("price", ""),
                    "Sport": p["sport"],
                    "Clarity Decision": decision,
                    "Clarity Summary": badge,
                }
            )

            insert_bet(
                {
                    "source": "GAME_SLIP",
                    "sport": p["sport"],
                    "player": p["team"] or p["market_type"],
                    "market": p["market_type"],
                    "line": p["line"],
                    "pick": "",
                    "opponent": p.get("opponent", ""),
                    "game_date": "",
                    "result": "",
                    "actual": 0.0,
                }
            )

    df_props = pd.DataFrame(prop_rows) if prop_rows else pd.DataFrame()
    df_games = pd.DataFrame(game_rows) if game_rows else pd.DataFrame()
    return df_props, df_games

# =============================================================================
# STREAMLIT APP
# =============================================================================
def main():
    st.set_page_config(
        page_title="Clarity 18.0 Elite – Unified Quick Scanner",
        layout="wide",
    )

    st.title("CLARITY 18.0 ELITE – Unified Quick Scanner")
    st.caption(f"Version: {VERSION} | Build: {BUILD_DATE}")

    init_db()

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "🎮 GAME MARKETS",
            "📋 PASTE & SCAN",
            "📊 SCANNERS & ACCURACY",
            "🎯 PLAYER PROPS",
            "🔧 SELF EVALUATION",
        ]
    )

    # -------------------------------------------------------------------------
    # TAB 1 – GAME MARKETS
    # -------------------------------------------------------------------------
    with tab1:
        st.subheader("🎮 GAME MARKETS – Live game lines, alternate lines, parlays")
        sport_choice = st.selectbox(
            "Sport feed",
            ["basketball_nba", "americanfootball_nfl", "icehockey_nhl", "baseball_mlb"],
            index=0,
        )
        if st.button("Fetch Live Markets"):
            data = fetch_game_markets(sport_choice)
            if not data:
                st.warning("No market data returned (API may be rate-limited or unavailable).")
            else:
                df = summarize_best_odds(data)
                st.dataframe(df, use_container_width=True)
                st.info("Showing best moneyline prices across books. Use this as a base for parlays / alt lines.")

    # -------------------------------------------------------------------------
    # TAB 2 – PASTE & SCAN (Text + OCR)
    # -------------------------------------------------------------------------
    with tab2:
        st.subheader("📋 PASTE & SCAN – Paste or screenshot anything – props, slips, tickets")

        sport_for_paste = st.selectbox("Default sport for pasted / OCR props & games", list(SPORT_MODELS.keys()), index=0)
        check_scan_timing(sport_for_paste)

        st.markdown("#### Paste Text")
        paste_text = st.text_area(
            "Paste Bovada / MyBookie / PrizePicks text here:",
            height=220,
            placeholder="You can paste mixed content: game lines, live markets, player props, etc.",
        )

        col_p1, col_p2 = st.columns([1, 1])
        with col_p1:
            run_scan = st.button("Scan & Analyze Pasted Text")
        with col_p2:
            st.caption("Clarity will separate PLAYER PROPS and GAME MARKETS, and label each as APPROVED or PASS.")

        if run_scan:
            if not paste_text.strip():
                st.warning("Paste something first.")
            else:
                parsed = parse_pasteboard_unified(paste_text, sport_for_paste, source="PASTE")
                if not parsed:
                    st.warning("No valid props or game slips detected. Check formatting.")
                else:
                    df_props, df_games = analyze_and_store_unified(parsed)

                    if not df_props.empty:
                        st.markdown("### 🟦 PLAYER PROPS – Clarity Recommendations")
                        st.markdown(df_props.to_html(escape=False, index=False), unsafe_allow_html=True)
                    else:
                        st.markdown("### 🟦 PLAYER PROPS – None detected")

                    if not df_games.empty:
                        st.markdown("### 🟥 GAME MARKETS – Clarity Recommendations")
                        st.markdown(df_games.to_html(escape=False, index=False), unsafe_allow_html=True)
                    else:
                        st.markdown("### 🟥 GAME MARKETS – None detected")

                    st.info("All APPROVED/PASS decisions are based on Clarity rules. Bets are stored as pending in the database.")

        st.markdown("---")
        st.markdown("#### Screenshot OCR")
        uploaded_file = st.file_uploader(
            "Upload screenshot (PNG/JPG/JPEG) of slips / props / tickets:",
            type=["png", "jpg", "jpeg"],
        )
        run_ocr = st.button("Run OCR & Scan Screenshot")

        if run_ocr:
            if not uploaded_file:
                st.warning("Upload a screenshot first.")
            else:
                image_bytes = uploaded_file.read()
                with st.spinner("Running OCR on screenshot..."):
                    ocr_text = ocr_space_image(image_bytes)

                if not ocr_text:
                    st.warning("OCR did not return any text. Try a clearer image or different crop.")
                else:
                    with st.expander("OCR Extracted Text"):
                        st.text(ocr_text)

                    parsed_ocr = parse_pasteboard_unified(ocr_text, sport_for_paste, source="OCR")
                    if not parsed_ocr:
                        st.warning("No valid props or game slips detected in OCR text.")
                    else:
                        df_props_ocr, df_games_ocr = analyze_and_store_unified(parsed_ocr)

                        if not df_props_ocr.empty:
                            st.markdown("### 🟦 PLAYER PROPS – Clarity Recommendations (OCR)")
                            st.markdown(df_props_ocr.to_html(escape=False, index=False), unsafe_allow_html=True)
                        else:
                            st.markdown("### 🟦 PLAYER PROPS – None detected (OCR)")

                        if not df_games_ocr.empty:
                            st.markdown("### 🟥 GAME MARKETS – Clarity Recommendations (OCR)")
                            st.markdown(df_games_ocr.to_html(escape=False, index=False), unsafe_allow_html=True)
                        else:
                            st.markdown("### 🟥 GAME MARKETS – None detected (OCR)")

                        st.info("OCR-derived bets have been stored as pending in the database.")

    # -------------------------------------------------------------------------
    # TAB 3 – SCANNERS & ACCURACY
    # -------------------------------------------------------------------------
    with tab3:
        st.subheader("📊 SCANNERS & ACCURACY – Best odds, arbitrage, middles, win rate")

        st.markdown("#### Best Odds Scanner (Moneyline)")
        sport_choice2 = st.selectbox(
            "Sport feed for scanner",
            ["basketball_nba", "americanfootball_nfl", "icehockey_nhl", "baseball_mlb"],
            index=0,
            key="scanner_sport",
        )
        if st.button("Run Best Odds Scanner"):
            data = fetch_game_markets(sport_choice2)
            if not data:
                st.warning("No market data returned.")
            else:
                df = summarize_best_odds(data)
                st.dataframe(df, use_container_width=True)
                st.info(
                    "Use this to spot arbitrage/middles by comparing best home vs best away prices "
                    "and cross-referencing with other books."
                )

        st.markdown("#### Historical Win Rate (from settled bets)")
        hist_df = get_recent_bets(limit=500)
        if hist_df.empty:
            st.write("No bets stored yet.")
        else:
            settled = hist_df[hist_df["Result"].isin(["WIN", "LOSS"])]
            if settled.empty:
                st.write("No settled bets yet.")
            else:
                total = len(settled)
                wins = (settled["Result"] == "WIN").sum()
                win_rate = wins / total * 100
                st.metric("Win Rate (all time)", f"{win_rate:.1f}%")
                st.dataframe(settled.head(50), use_container_width=True)

    # -------------------------------------------------------------------------
    # TAB 4 – PLAYER PROPS (Manual Analyzer)
    # -------------------------------------------------------------------------
    with tab4:
        st.subheader("🎯 PLAYER PROPS – Manual dropdown analyzer")

        sport_pp = st.selectbox("Sport", ["NBA"], index=0)
        player_name = st.text_input("Player name", value="LeBron James")
        market_pp = st.selectbox("Market", ["PTS", "REB", "AST", "PRA", "PR", "PA"], index=0)
        line_pp = st.number_input("Line", min_value=0.0, max_value=100.0, value=25.5, step=0.5)
        pick_pp = st.selectbox("Pick (for manual edge calc only)", ["OVER", "UNDER"], index=0)

        if st.button("Analyze Player Prop"):
            history = fetch_nba_recent_stat(player_name, market_pp, num_games=8)
            edge, prob = estimate_edge_from_history(history, line_pp, pick_pp)
            tier_info = STAT_CONFIG.get(market_pp, {"tier": "LOW", "buffer": 0.0, "reject": False})

            st.markdown("#### Analysis")
            st.write(f"Games used: **{len(history)}**")
            st.write(f"Mean stat: **{round(np.mean(history), 2) if history else 0.0}**")
            st.write(f"Estimated win probability (vs your pick): **{prob*100:.1f}%**")
            st.write(f"Edge vs 50/50: **{edge*100:.1f}%**")
            st.write(f"Tier: **{tier_info['tier']}**, Red Tier: **{tier_info['reject']}**")

            if st.checkbox("Store this as a pending bet", value=True):
                insert_bet(
                    {
                        "source": "MANUAL",
                        "sport": sport_pp,
                        "player": player_name,
                        "market": market_pp,
                        "line": line_pp,
                        "pick": pick_pp,
                        "opponent": "",
                        "game_date": "",
                        "result": "",
                        "actual": 0.0,
                    }
                )
                st.success("Bet stored as pending.")

    # -------------------------------------------------------------------------
    # TAB 5 – SELF EVALUATION (Auto-settle, pending bets, history)
    # -------------------------------------------------------------------------
    with tab5:
        st.subheader("🔧 SELF EVALUATION – Auto-settle, pending bets, tuning history")

        st.markdown("#### Pending Bets")
        pending = get_pending_bets()
        if not pending:
            st.write("No pending bets.")
        else:
            df_p = pd.DataFrame(pending)
            st.dataframe(df_p, use_container_width=True)

            if st.button("Auto-settle all pending (NBA only)"):
                settled_rows = []
                for b in pending:
                    result, actual = auto_settle_prop(
                        b["player"],
                        b["market"],
                        float(b["line"]),
                        b["pick"],
                        b["sport"],
                        b["opponent"],
                        b["game_date"] or None,
                    )
                    if result != "PENDING":
                        update_bet_result(b["id"], result, actual)
                        settled_rows.append(
                            {
                                "ID": b["id"],
                                "Player": b["player"],
                                "Market": b["market"],
                                "Line": b["line"],
                                "Pick": b["pick"],
                                "Sport": b["sport"],
                                "Result": result,
                                "Actual": actual,
                            }
                        )
                if settled_rows:
                    st.success(f"Auto-settled {len(settled_rows)} bets.")
                    st.dataframe(pd.DataFrame(settled_rows), use_container_width=True)
                else:
                    st.info("No bets could be auto-settled (likely non-NBA or no stats yet).")

        st.markdown("#### Recent Bets (All)")
        hist_df2 = get_recent_bets(limit=200)
        if hist_df2.empty:
            st.write("No bets stored yet.")
        else:
            st.dataframe(hist_df2, use_container_width=True)
            st.caption("Use this to visually inspect performance, volatility, and calibration over time.")

    st.markdown("---")
    st.caption(
        "Final 5-tab Clarity 18.0 Elite: game markets, paste & scan (with OCR), scanners & accuracy, "
        "player props, and self evaluation. Pasted Bovada/MyBookie/PrizePicks text is split into PLAYER PROPS "
        "and GAME MARKETS, with bold, color-coded Clarity recommendations."
    )

if __name__ == "__main__":
    main()
