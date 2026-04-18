"""
CLARITY 18.1 ELITE – Unified Quick Scanner
- Multi-sport manual analyzer (NBA, MLB, NFL, NHL)
- Auto-settle framework for all sports (NBA fully wired via BallDontLie, others scaffolded)
- Slip-based settlement (Option B)
- Manual settle as fallback
- Clear Pending Bets button (testing cleanup only)
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
# CONFIGURATION – YOUR API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

VERSION = "18.1 Elite (Unified Quick Scanner – Multi-Sport + Clear Pending)"
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
    # MLB
    "HITS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TB": {"tier": "MED", "buffer": 0.5, "reject": False},
    "K": {"tier": "MED", "buffer": 0.5, "reject": False},
    "OUTS": {"tier": "LOW", "buffer": 1.5, "reject": False},
    # NFL
    "PASS_YDS": {"tier": "MED", "buffer": 10.0, "reject": False},
    "RUSH_YDS": {"tier": "MED", "buffer": 5.0, "reject": False},
    "REC_YDS": {"tier": "MED", "buffer": 5.0, "reject": False},
    "REC": {"tier": "LOW", "buffer": 0.5, "reject": False},
    # NHL
    "SOG": {"tier": "MED", "buffer": 0.5, "reject": False},
    "SAVES": {"tier": "MED", "buffer": 1.5, "reject": False},
    "POINTS": {"tier": "MED", "buffer": 0.5, "reject": False},
}

RED_TIER_PROPS = ["PRA", "PR", "PA"]

# =============================================================================
# DB HELPERS
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

def get_recent_bets(limit: int = 200) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, created_at, sport, player, market, line, pick, opponent, game_date, result, actual "
        "FROM bets ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    cols = ["ID", "Created", "Sport", "Player/Team", "Market", "Line", "Pick", "Opponent", "Game Date", "Result", "Actual"]
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

def clear_pending_bets() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM bets WHERE result = '' OR result IS NULL")
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
# OTHER SPORTS – RECENT STAT FETCH (SCAFFOLD)
# =============================================================================
def api_sports_request(endpoint: str, params: Optional[dict] = None, sport: str = "baseball") -> Optional[dict]:
    try:
        headers = {"x-apisports-key": API_SPORTS_KEY}
        url = f"{API_SPORTS_BASE}/{sport}{endpoint}"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def fetch_mlb_recent_stat(player_name: str, market: str, num_games: int = 8) -> List[float]:
    # Placeholder scaffold – wire to MLB stats provider of your choice
    return []

def fetch_nfl_recent_stat(player_name: str, market: str, num_games: int = 8) -> List[float]:
    # Placeholder scaffold – wire to NFL stats provider of your choice
    return []

def fetch_nhl_recent_stat(player_name: str, market: str, num_games: int = 8) -> List[float]:
    # Placeholder scaffold – wire to NHL stats provider of your choice
    return []

def fetch_recent_stat_multi(
    sport: str,
    player_name: str,
    market: str,
    num_games: int = 8,
) -> List[float]:
    sport = sport.upper()
    if sport == "NBA":
        return fetch_nba_recent_stat(player_name, market, num_games)
    if sport == "MLB":
        return fetch_mlb_recent_stat(player_name, market, num_games)
    if sport == "NFL":
        return fetch_nfl_recent_stat(player_name, market, num_games)
    if sport == "NHL":
        return fetch_nhl_recent_stat(player_name, market, num_games)
    return []

# =============================================================================
# SIMPLE EDGE ESTIMATION
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
# AUTO-SETTLE WRAPPERS
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
    """
    Automatic prop settlement.
    - NBA: fully wired via BallDontLie.
    - Other sports: scaffolded, returns PENDING by default.
    """
    if not game_date:
        game_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    if sport.upper() == "NBA":
        return balldontlie_settle_prop(player, market, line, pick, game_date)

    # Future: wire MLB/NFL/NHL prop settlement here.
    return "PENDING", 0.0

def fetch_game_final_score_api(
    sport: str,
    team: str,
    opponent: str,
    game_date: Optional[str],
) -> Tuple[Optional[int], Optional[int]]:
    """
    Framework for automatic game-line settlement.
    Currently returns (None, None) as a placeholder.
    Wire this to API-Sports / ESPN / MLB / NHL stats APIs.
    """
    return None, None

def auto_settle_game(
    team: str,
    market: str,
    line: float,
    pick: str,
    sport: str,
    opponent: str,
    game_date: Optional[str] = None,
) -> Tuple[str, float]:
    """
    Automatic game-line settlement framework.
    - Uses final scores to settle ML / SPREAD / TOTAL / ALT_* markets.
    - Currently returns PENDING until fetch_game_final_score_api is wired.
    """
    if not game_date:
        game_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    team_score, opp_score = fetch_game_final_score_api(sport, team, opponent, game_date)
    if team_score is None or opp_score is None:
        return "PENDING", 0.0

    mt = market.upper()
    if "ML" in mt:
        won = team_score > opp_score
        return ("WIN" if won else "LOSS"), float(team_score)
    elif "SPREAD" in mt:
        margin = team_score - opp_score
        if pick.upper() in ["FAV", "OVER"]:
            won = margin > line
        else:
            won = margin < line
        return ("WIN" if won else "LOSS"), float(margin)
    elif "TOTAL" in mt:
        total = team_score + opp_score
        if "OVER" in mt:
            won = total > line
        else:
            won = total < line
        return ("WIN" if won else "LOSS"), float(total)

    return "PENDING", 0.0

# =============================================================================
# TEAM / SPORT DETECTION + OCR CLEANING
# =============================================================================

NBA_TEAMS = [
    "Toronto Raptors", "Cleveland Cavaliers", "Minnesota Timberwolves", "Denver Nuggets",
    "Los Angeles Lakers", "Boston Celtics", "Golden State Warriors", "Miami Heat",
    "Philadelphia 76ers", "New York Knicks", "Brooklyn Nets", "Chicago Bulls",
]

NFL_TEAMS = [
    "Dallas Cowboys", "San Francisco 49ers", "Kansas City Chiefs", "Philadelphia Eagles",
    "Buffalo Bills", "Miami Dolphins", "New York Jets", "New England Patriots",
]

MLB_TEAMS = [
    "Cincinnati Reds", "Minnesota Twins", "New York Mets", "Chicago Cubs",
    "Tampa Bay Rays", "Pittsburgh Pirates", "Chicago White Sox", "Oakland Athletics",
    "St. Louis Cardinals", "Houston Astros", "Los Angeles Dodgers", "Colorado Rockies",
    "San Diego Padres", "Los Angeles Angels", "Texas Rangers", "Seattle Mariners",
    "New York Yankees", "Boston Red Sox",
]

NHL_TEAMS = [
    "Toronto Maple Leafs", "Montreal Canadiens", "Boston Bruins", "New York Rangers",
    "Chicago Blackhawks", "Detroit Red Wings",
]

ALL_TEAMS = NBA_TEAMS + NFL_TEAMS + MLB_TEAMS + NHL_TEAMS

TEAM_TOKEN_PATTERN = re.compile(r"[A-Z][a-z]+(?: [A-Z][a-z]+)+")

MONTH_WORDS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}

def detect_sport_from_text(text: str) -> str:
    t = text.lower()
    score = {"NBA": 0, "NFL": 0, "MLB": 0, "NHL": 0}

    for name in NBA_TEAMS:
        if name.lower() in t:
            score["NBA"] += 1
    for name in NFL_TEAMS:
        if name.lower() in t:
            score["NFL"] += 1
    for name in MLB_TEAMS:
        if name.lower() in t:
            score["MLB"] += 1
    for name in NHL_TEAMS:
        if name.lower() in t:
            score["NHL"] += 1

    if re.search(r"-[LR]\b", t):
        score["MLB"] += 3

    best = max(score, key=score.get)
    if score[best] == 0:
        return "NBA"
    return best

def is_garbage_line(line: str) -> bool:
    l = line.strip()
    if not l:
        return True
    if l.lower() in {"lht", "thu"}:
        return True
    if l in {"₺", "•", "V"}:
        return True
    if len(l) <= 2 and not any(ch.isdigit() for ch in l):
        return True
    return False

def clean_ocr_text(raw: str) -> str:
    lines = [l.strip() for l in raw.splitlines()]
    lines = [l for l in lines if l]

    filtered: List[str] = []
    for l in lines:
        low = l.lower()
        if is_garbage_line(l):
            continue
        if "more" in low or "less" in low:
            continue
        if "more wagers" in low:
            continue
        filtered.append(l)

    lines = filtered
    merged: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        if (
            i + 1 < len(lines)
            and len(line.split()) == 1
            and len(lines[i + 1].split()) == 1
            and line[0].isupper()
            and lines[i + 1][0].isupper()
        ):
            merged.append(f"{line} {lines[i+1]}")
            i += 2
            continue

        if i + 1 < len(lines):
            next_line = lines[i + 1]

            if re.match(r"^[+-]?\d+(\.\d+)?$", line) and re.match(r"^[+-]?\d{2,4}$", next_line):
                merged.append(f"{line} ({next_line})")
                i += 2
                continue

            if re.match(r"^[OU]\s*\d+(\.\d+)?$", line, re.IGNORECASE) and re.match(r"^[+-]?\d{2,4}$", next_line):
                merged.append(f"{line} ({next_line})")
                i += 2
                continue

            if re.match(r"^[+-]?\d+(\.\d+)?$", line) and re.match(r"^\(-?\d+\)$", next_line):
                merged.append(f"{line} {next_line}")
                i += 2
                continue
            if re.match(r"^[OU]\d+(\.\d+)?$", line, re.IGNORECASE) and re.match(r"^\(-?\d+\)$", next_line):
                merged.append(f"{line} {next_line}")
                i += 2
                continue

        if (
            i + 1 < len(lines)
            and re.match(r"^\d+(\.\d+)?$", line)
            and any(word in lines[i + 1].lower() for word in ["points", "rebounds", "assists", "pra", "pr ", "pa "])
        ):
            merged.append(f"{line} {lines[i+1]}")
            i += 2
            continue

        m_ge = re.match(r"^[≥=]\s*(\d+(\.\d+)?)\s+(.*)$", line)
        if m_ge:
            num = m_ge.group(1)
            rest = m_ge.group(3)
            merged.append(f"{num} {rest}")
            i += 1
            continue

        merged.append(line)
        i += 1

    cleaned = []
    for l in merged:
        if len(l) <= 1:
            continue
        cleaned.append(l)

    return "\n".join(cleaned)

# =============================================================================
# PASTEBOARD PARSERS – GAME SLIPS + PLAYER PROPS
# =============================================================================

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
    lower_block = block.lower()
    for t in ALL_TEAMS:
        if t.lower() in lower_block:
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

def looks_like_pitcher_line(line: str) -> bool:
    if "-" in line and "," in line and re.search(r"-[LR]\b", line):
        return True
    return False

def parse_player_props(text: str, default_sport: str, source: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    results: List[Dict[str, Any]] = []

    pitcher_count = sum(1 for l in lines if looks_like_pitcher_line(l))
    if pitcher_count >= 2:
        return results

    i = 0
    while i < len(lines):
        line = lines[i]

        low = line.lower()
        if re.match(r"^[A-Z]{2,4}\s*-\s*[A-Z\-]+$", line) or re.match(r"^[A-Z]{2,4}-\s*[A-Z]$", line):
            i += 1
            continue

        if i + 1 < len(lines) and not any(ch.isdigit() for ch in line) and any(ch.isdigit() for ch in lines[i + 1]):
            combined = f"{line} {lines[i+1]}"
            m = PROP_PATTERN.search(combined)
            if m:
                d = m.groupdict()
                player = d["player"].strip()
                if any(tok.lower() in MONTH_WORDS for tok in player.split()):
                    i += 2
                    continue

                line_val = float(d["line"])
                market = "PTS"
                lower_combined = combined.lower()
                if "rebound" in lower_combined:
                    market = "REB"
                elif "assist" in lower_combined:
                    market = "AST"
                elif "pra" in lower_combined:
                    market = "PRA"
                elif "pr " in lower_combined or "points + rebounds" in lower_combined:
                    market = "PR"
                elif "pa " in lower_combined or "points + assists" in lower_combined:
                    market = "PA"

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
                i += 2
                continue

        m = PROP_PATTERN.search(line)
        if not m:
            i += 1
            continue

        d = m.groupdict()
        player = d["player"].strip()
        if any(tok.lower() in MONTH_WORDS for tok in player.split()):
            i += 1
            continue

        line_val = float(d["line"])

        market = "PTS"
        lower_line = line.lower()
        if "rebound" in lower_line:
            market = "REB"
        elif "assist" in lower_line:
            market = "AST"
        elif "pra" in lower_line:
            market = "PRA"
        elif "pr " in lower_line or "points + rebounds" in lower_line:
            market = "PR"
        elif "pa " in lower_line or "points + assists" in lower_line:
            market = "PA"

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
        i += 1

    return results

def parse_pasteboard_unified(text: str, default_sport: str, source: str) -> List[Dict[str, Any]]:
    if default_sport == "AUTO":
        detected = detect_sport_from_text(text)
        default_sport = detected

    if source in ["OCR", "SLIP"]:
        text = clean_ocr_text(text)

    games = parse_game_slips(text, default_sport)
    props = parse_player_props(text, default_sport, source)

    return props + games

# =============================================================================
# CLARITY DECISION ENGINE
# =============================================================================
def clarity_decision_for_prop(row: Dict[str, Any]) -> Tuple[str, str, str, float, float, int, float]:
    history: List[float] = fetch_recent_stat_multi(
        row["sport"], row["player"], row["market"], num_games=8
    )

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

    if not df_games.empty:
        df_games = df_games.drop_duplicates(
            subset=["Team", "Opponent", "Market Type", "Line", "Price"]
        )

    return df_props, df_games

# =============================================================================
# SLIP-BASED SETTLEMENT (Option B)
# =============================================================================
def match_slip_to_pending(
    slip_rows: List[Dict[str, Any]],
    pending: List[Dict[str, Any]],
) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """
    For each slip row (PROP or GAME), find matching pending bets.
    Matching logic:
    - Props: sport + player + market + line
    - Games: sport + team + market_type + line
    """
    matches: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []

    for s in slip_rows:
        matched: List[Dict[str, Any]] = []
        if s["type"] == "PROP":
            for p in pending:
                if (
                    p["sport"].upper() == s["sport"].upper()
                    and p["player"].lower() == s["player"].lower()
                    and p["market"].upper() == s["market"].upper()
                    and abs(float(p["line"]) - float(s["line"])) < 0.01
                ):
                    matched.append(p)
        elif s["type"] == "GAME":
            for p in pending:
                if (
                    p["sport"].upper() == s["sport"].upper()
                    and p["market"].upper() == s["market_type"].upper()
                    and abs(float(p["line"]) - float(s["line"])) < 0.01
                ):
                    if s["team"] and s["team"].lower() not in p["player"].lower():
                        continue
                    matched.append(p)

        if matched:
            matches.append((s, matched))

    return matches

# =============================================================================
# STREAMLIT APP
# =============================================================================
def main():
    st.set_page_config(
        page_title="Clarity 18.1 Elite – Unified Quick Scanner",
        layout="wide",
    )

    st.title("CLARITY 18.1 ELITE – Unified Quick Scanner")
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

        sport_for_paste = st.selectbox(
            "Sport for pasted / OCR props & games",
            ["AUTO", "NBA", "NFL", "MLB", "NHL"],
            index=0,
        )
        if sport_for_paste != "AUTO":
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
    # TAB 4 – PLAYER PROPS (Manual Analyzer – Multi-Sport)
    # -------------------------------------------------------------------------
    with tab4:
        st.subheader("🎯 PLAYER PROPS – Manual dropdown analyzer (Multi-Sport)")

        sport_pp = st.selectbox("Sport", ["NBA", "MLB", "NFL", "NHL"], index=0)

        if sport_pp == "NBA":
            markets_pp = ["PTS", "REB", "AST", "PRA", "PR", "PA"]
        elif sport_pp == "MLB":
            markets_pp = ["HITS", "TB", "K", "OUTS"]
        elif sport_pp == "NFL":
            markets_pp = ["PASS_YDS", "RUSH_YDS", "REC_YDS", "REC"]
        else:  # NHL
            markets_pp = ["SOG", "SAVES", "POINTS"]

        player_name = st.text_input("Player name", value="LeBron James" if sport_pp == "NBA" else "")
        market_pp = st.selectbox("Market", markets_pp, index=0)
        line_pp = st.number_input("Line", min_value=0.0, max_value=500.0, value=25.5, step=0.5)
        pick_pp = st.selectbox("Pick (for manual edge calc only)", ["OVER", "UNDER"], index=0)

        if st.button("Analyze Player Prop"):
            history = fetch_recent_stat_multi(sport_pp, player_name, market_pp, num_games=8)
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
    # TAB 5 – SELF EVALUATION (Auto-settle, slip-settle, pending bets, history)
    # -------------------------------------------------------------------------
    with tab5:
        st.subheader("🔧 SELF EVALUATION – Auto-settle, slip-settle, tuning history")

        st.markdown("#### Pending Bets")
        pending = get_pending_bets()
        if not pending:
            st.write("No pending bets.")
        else:
            df_p = pd.DataFrame(pending)
            st.dataframe(df_p, use_container_width=True)

            col_auto, col_slip, col_manual = st.columns([1, 1, 1])

            # A) FULLY AUTOMATIC SETTLE (Option A)
            with col_auto:
                if st.button("Auto-settle all pending (API-based)"):
                    settled_rows = []
                    for b in pending:
                        sport = b["sport"]
                        market = b["market"]
                        line = float(b["line"])
                        pick = b["pick"]
                        player_or_team = b["player"]
                        opponent = b["opponent"]
                        game_date = b["game_date"] or None

                        result = "PENDING"
                        actual = 0.0

                        if market.upper() in STAT_CONFIG.keys():
                            result, actual = auto_settle_prop(
                                player_or_team,
                                market,
                                line,
                                pick,
                                sport,
                                opponent,
                                game_date,
                            )
                        elif any(k in market.upper() for k in ["ML", "SPREAD", "TOTAL"]):
                            result, actual = auto_settle_game(
                                player_or_team,
                                market,
                                line,
                                pick,
                                sport,
                                opponent,
                                game_date,
                            )

                        if result != "PENDING":
                            update_bet_result(b["id"], result, actual)
                            settled_rows.append(
                                {
                                    "ID": b["id"],
                                    "Player/Team": player_or_team,
                                    "Market": market,
                                    "Line": line,
                                    "Pick": pick,
                                    "Sport": sport,
                                    "Result": result,
                                    "Actual": actual,
                                }
                            )

                    if settled_rows:
                        st.success(f"Auto-settled {len(settled_rows)} bets.")
                        st.dataframe(pd.DataFrame(settled_rows), use_container_width=True)
                    else:
                        st.info("No bets could be auto-settled (likely unsupported stats or missing game data).")

            # B) SLIP-BASED SETTLE (Option B)
            with col_slip:
                st.markdown("**Settle from slip (OCR)**")
                slip_file = st.file_uploader(
                    "Upload winning/losing slip",
                    type=["png", "jpg", "jpeg"],
                    key="slip_uploader",
                )
                slip_result_choice = st.radio(
                    "This slip is:",
                    ["WINNING slip (all bets won)", "LOSING slip (all bets lost)"],
                    index=0,
                    key="slip_result_choice",
                )
                if st.button("Settle from slip"):
                    if not slip_file:
                        st.warning("Upload a slip first.")
                    else:
                        image_bytes = slip_file.read()
                        with st.spinner("Running OCR on slip..."):
                            slip_text = ocr_space_image(image_bytes)

                        if not slip_text:
                            st.warning("OCR did not return any text from slip.")
                        else:
                            with st.expander("Slip OCR Text"):
                                st.text(slip_text)

                            slip_parsed = parse_pasteboard_unified(slip_text, "AUTO", source="SLIP")
                            if not slip_parsed:
                                st.warning("No recognizable props or game markets found in slip.")
                            else:
                                matches = match_slip_to_pending(slip_parsed, pending)
                                if not matches:
                                    st.info("No pending bets matched this slip.")
                                else:
                                    final_result = "WIN" if "WINNING" in slip_result_choice else "LOSS"
                                    updated = []
                                    for slip_row, matched_bets in matches:
                                        for mb in matched_bets:
                                            update_bet_result(mb["id"], final_result, 0.0)
                                            updated.append(
                                                {
                                                    "ID": mb["id"],
                                                    "Player/Team": mb["player"],
                                                    "Market": mb["market"],
                                                    "Line": mb["line"],
                                                    "Pick": mb["pick"],
                                                    "Sport": mb["sport"],
                                                    "Result": final_result,
                                                    "Actual": 0.0,
                                                }
                                            )
                                    if updated:
                                        st.success(f"Settled {len(updated)} bets from slip.")
                                        st.dataframe(pd.DataFrame(updated), use_container_width=True)

            # C) MANUAL SETTLE (fallback only)
            with col_manual:
                st.markdown("**Manual settle (fallback)**")
                st.caption("Use only if auto-settle and slip-settle cannot determine the outcome.")
                manual_id = st.number_input("Bet ID to settle", min_value=0, step=1, value=0)
                manual_result = st.selectbox("Result", ["WIN", "LOSS"], index=0)
                manual_actual = st.number_input("Actual stat / score (optional)", value=0.0, step=0.5)
                if st.button("Settle manually"):
                    if manual_id <= 0:
                        st.warning("Enter a valid Bet ID.")
                    else:
                        update_bet_result(int(manual_id), manual_result, manual_actual)
                        st.success(f"Bet {manual_id} settled as {manual_result}.")

        st.markdown("---")
        st.markdown("#### Testing Cleanup – Clear Pending Bets Only")
        st.caption(
            "This will delete ONLY pending bets (test data). Settled bets (WIN/LOSS) remain for self-evaluation."
        )
        if st.button("Clear Pending Bets (Testing Only)"):
            clear_pending_bets()
            st.success("All pending bets have been cleared. Settled history remains intact.")

        st.markdown("#### Recent Bets (All)")
        hist_df2 = get_recent_bets(limit=200)
        if hist_df2.empty:
            st.write("No bets stored yet.")
        else:
            st.dataframe(hist_df2, use_container_width=True)

            settled = hist_df2[hist_df2["Result"].isin(["WIN", "LOSS"])]
            if not settled.empty:
                total = len(settled)
                wins = (settled["Result"] == "WIN").sum()
                win_rate = wins / total * 100

                st.markdown("#### Performance Summary")
                st.metric("Overall Win Rate", f"{win_rate:.1f}%")

                by_sport = settled.groupby("Sport")["Result"].apply(
                    lambda s: (s == "WIN").mean() * 100
                )
                st.write("Win Rate by Sport:")
                st.dataframe(by_sport.reset_index().rename(columns={"Result": "Win Rate %"}))

                by_market = settled.groupby("Market")["Result"].apply(
                    lambda s: (s == "WIN").mean() * 100
                )
                st.write("Win Rate by Market:")
                st.dataframe(by_market.reset_index().rename(columns={"Result": "Win Rate %"}))

                st.caption(
                    "Use these win rates to judge if Clarity is too strict (passing too many winners) "
                    "or too loose (approving too many losers). Thresholds can be tuned based on this."
                )

    st.markdown("---")
    st.caption(
        "Clarity 18.1 Elite now supports: unified scanning, OCR, pending bet tracking, "
        "automatic NBA prop settlement, multi-sport manual analysis, slip-based settlement, "
        "and a safe Clear Pending Bets button so your real evaluation starts clean."
    )

if __name__ == "__main__":
    main()
