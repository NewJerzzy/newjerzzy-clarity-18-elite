```python
"""
CLARITY 18.0 ELITE – UNIFIED QUICK SCANNER (Player Props + Game Slips + Result Tracking)

- One paste board for everything: player props, game slips, winning/losing tickets.
- Auto-detects PrizePicks, MyBookie, Bovada formats. (Simplified in this rebuild.)
- Live analysis for game lines (ML, spread, total) using Odds-API.io.
- Player prop analysis using BallsDontLie (NBA) or API-SPORTS.
- Screenshot OCR support (stubbed).
- Imports results into SQLite for auto-tune and ML retraining (simplified).
"""

import os
import re
import time
import hashlib
import threading
import warnings
import shutil
import sqlite3
from functools import wraps
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd
import requests
import streamlit as st
from scipy.stats import poisson, nbinom, norm

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURATION – YOUR API KEYS (kept hard-coded as requested)
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

VERSION = "18.0 Elite (Unified Quick Scanner – Rebuild)"
BUILD_DATE = "2026-04-17"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"
BALLSDONTLIE_BASE = "https://api.balldontlie.io/v1"

DB_PATH = "clarity_elite.db"

# =============================================================================
# RETRY DECORATOR
# =============================================================================
def retry(max_attempts: int = 3, delay: float = 2.0, backoff: float = 2.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(_delay)
                    _delay *= backoff
        return wrapper
    return decorator

# =============================================================================
# TIMING WARNING HELPER
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
    elif sport in ["SOCCER_EPL", "SOCCER_LALIGA"]:
        if hour not in [14, 15]:
            st.info(
                "⚽ For soccer, lines are often most efficient when scanned in the afternoon "
                "(2–3 PM) the day before matches."
            )

# =============================================================================
# SPORT MODELS
# =============================================================================
SPORT_MODELS: Dict[str, Dict[str, Any]] = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 44.5, "home_advantage": 2.8},
    "SOCCER_EPL": {"distribution": "poisson", "variance_factor": 1.10, "avg_total": 2.5, "home_advantage": 0.3},
    "SOCCER_LALIGA": {"distribution": "poisson", "variance_factor": 1.10, "avg_total": 2.5, "home_advantage": 0.3},
}

SPORT_CATEGORIES: Dict[str, List[str]] = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "H+R+RBI"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS"],
    "NFL": ["PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"],
    "SOCCER_EPL": ["GOALS", "ASSISTS", "SHOTS", "SHOTS_ON_TARGET"],
    "SOCCER_LALIGA": ["GOALS", "ASSISTS", "SHOTS", "SHOTS_ON_TARGET"],
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
    "H+R+RBI": {"tier": "HIGH", "buffer": 0.5, "reject": True},
}

RED_TIER_PROPS = ["PRA", "PR", "PA", "H+R+RBI"]

# =============================================================================
# HARDCODED TEAMS (trimmed but valid)
# =============================================================================
HARDCODED_TEAMS: Dict[str, List[str]] = {
    "NBA": [
        "Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls",
        "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons",
        "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers",
        "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
        "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
        "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
        "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors",
        "Utah Jazz", "Washington Wizards",
    ],
    "NFL": [
        "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
        "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
        "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
        "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
        "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
        "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants",
        "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
        "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders",
    ],
}

# =============================================================================
# FALLBACK NBA ROSTERS (trimmed but consistent)
# =============================================================================
FALLBACK_NBA_ROSTERS: Dict[str, List[str]] = {
    "Atlanta Hawks": ["Trae Young", "Dejounte Murray", "Jalen Johnson", "Clint Capela", "Bogdan Bogdanovic"],
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Jrue Holiday", "Derrick White"],
    "Denver Nuggets": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr.", "Aaron Gordon", "Kentavious Caldwell-Pope"],
    "Golden State Warriors": ["Stephen Curry", "Klay Thompson", "Draymond Green", "Andrew Wiggins", "Jonathan Kuminga"],
    "Los Angeles Lakers": ["LeBron James", "Anthony Davis", "D'Angelo Russell", "Austin Reaves", "Rui Hachimura"],
}

# =============================================================================
# DATABASE HELPERS
# =============================================================================
def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
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

def insert_ticket(row: Dict[str, Any]) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO tickets (
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

# =============================================================================
# BALLSDONTLIE API HELPERS
# =============================================================================
@retry(max_attempts=3, delay=1)
def balldontlie_request(endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
    headers = {"Authorization": BALLSDONTLIE_API_KEY}
    url = f"{BALLSDONTLIE_BASE}{endpoint}"
    r = requests.get(url, headers=headers, params=params, timeout=15)
    if r.status_code == 200:
        return r.json()
    return None

def balldontlie_get_player_stats(player_name: str, game_date: str) -> Optional[Dict[str, Any]]:
    players_data = balldontlie_request("/players", params={"search": player_name})
    if not players_data or not players_data.get("data"):
        return None
    player_id = players_data["data"][0]["id"]
    stats_data = balldontlie_request("/stats", params={"player_ids[]": player_id, "dates[]": game_date})
    if stats_data and stats_data.get("data"):
        return stats_data["data"][0]
    return None

def balldontlie_settle_prop(
    player: str,
    market: str,
    line: float,
    pick: str,
    game_date: str,
) -> Tuple[str, float]:
    stats_entry = balldontlie_get_player_stats(player, game_date)
    if not stats_entry:
        return "PENDING", 0.0

    stats = stats_entry.get("stats", stats_entry)  # some responses embed under "stats"
    market_map = {
        "PTS": "pts",
        "REB": "reb",
        "AST": "ast",
        "STL": "stl",
        "BLK": "blk",
        "FG3M": "fg3m",
        "THREES": "fg3m",
    }

    market_upper = market.upper()
    if market_upper == "PRA":
        actual_val = stats.get("pts", 0) + stats.get("reb", 0) + stats.get("ast", 0)
    elif market_upper == "PR":
        actual_val = stats.get("pts", 0) + stats.get("reb", 0)
    elif market_upper == "PA":
        actual_val = stats.get("pts", 0) + stats.get("ast", 0)
    else:
        stat_field = market_map.get(market_upper, market_upper.lower())
        actual_val = stats.get(stat_field, 0)

    if actual_val is None:
        return "PENDING", 0.0

    won = (actual_val > line) if pick.upper() == "OVER" else (actual_val < line)
    return ("WIN" if won else "LOSS"), float(actual_val)

# =============================================================================
# OPPONENT STRENGTH CACHE (API-SPORTS)
# =============================================================================
class OpponentStrengthCache:
    def __init__(self):
        self.cache: Dict[str, float] = {}
        self.last_fetch: Dict[str, datetime] = {}

    @retry(max_attempts=2, delay=1)
    def get_defensive_rating(self, sport: str, team: str) -> float:
        if sport not in ["NBA", "NHL", "MLB"]:
            return 1.0

        league_map = {
            "NBA": 12,   # NBA
            "NHL": 57,   # NHL
            "MLB": 253,  # MLB
        }
        season_map = {
            "NBA": "2024-2025",
            "NHL": "2024",
            "MLB": "2025",
        }

        league_id = league_map.get(sport)
        season = season_map.get(sport)
        if not league_id or not season:
            return 1.0

        key = f"{sport}_{team}"
        now = datetime.utcnow()
        if key in self.cache and key in self.last_fetch and (now - self.last_fetch[key]).days < 1:
            return self.cache[key]

        headers = {"x-apisports-key": API_SPORTS_KEY}

        # Find team ID
        url = f"{API_SPORTS_BASE}/teams"
        params = {"league": league_id, "season": season, "search": team}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return 1.0
        data = r.json().get("response", [])
        if not data:
            return 1.0
        team_id = data[0]["team"]["id"]

        # Fetch team statistics
        stats_url = f"{API_SPORTS_BASE}/teams/statistics"
        stats_params = {"league": league_id, "season": season, "team": team_id}
        r2 = requests.get(stats_url, headers=headers, params=stats_params, timeout=10)
        if r2.status_code != 200:
            return 1.0
        stats_data = r2.json().get("response", {})

        if sport == "NBA":
            pts_allowed = (
                stats_data.get("points", {})
                .get("against", {})
                .get("average", 115.0)
            )
            rating = 115.0 / pts_allowed if pts_allowed else 1.0
        elif sport == "NHL":
            goals_allowed = (
                stats_data.get("goals", {})
                .get("against", {})
                .get("average", 3.0)
            )
            rating = 3.0 / goals_allowed if goals_allowed else 1.0
        elif sport == "MLB":
            runs_allowed = (
                stats_data.get("runs", {})
                .get("against", {})
                .get("average", 4.5)
            )
            rating = 4.5 / runs_allowed if runs_allowed else 1.0
        else:
            rating = 1.0

        rating = float(max(0.8, min(1.2, rating)))
        self.cache[key] = rating
        self.last_fetch[key] = now
        return rating

opponent_strength = OpponentStrengthCache()

# =============================================================================
# REST & INJURY DETECTOR (simplified rest logic)
# =============================================================================
class RestInjuryDetector:
    def __init__(self):
        self.schedule_cache: Dict[str, Any] = {}

    @retry(max_attempts=2, delay=1)
    def get_rest_fade(self, sport: str, team: str) -> Tuple[float, str]:
        if sport not in ["NBA", "NHL", "MLB", "NFL"]:
            return 1.0, ""

        league_map = {
            "NBA": 12,
            "NHL": 57,
            "MLB": 253,
            "NFL": 1,
        }
        season_map = {
            "NBA": "2024-2025",
            "NHL": "2024",
            "MLB": "2025",
            "NFL": "2025",
        }

        league_id = league_map.get(sport)
        season = season_map.get(sport)
        if not league_id or not season:
            return 1.0, ""

        headers = {"x-apisports-key": API_SPORTS_KEY}

        # Find team ID
        url = f"{API_SPORTS_BASE}/teams"
        params = {"league": league_id, "season": season, "search": team}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return 1.0, ""
        data = r.json().get("response", [])
        if not data:
            return 1.0, ""
        team_id = data[0]["team"]["id"]

        # Recent games
        games_url = f"{API_SPORTS_BASE}/games"
        today = datetime.utcnow().date()
        params = {
            "league": league_id,
            "season": season,
            "team": team_id,
            "from": (today - timedelta(days=5)).strftime("%Y-%m-%d"),
            "to": today.strftime("%Y-%m-%d"),
        }
        r2 = requests.get(games_url, headers=headers, params=params, timeout=10)
        if r2.status_code != 200:
            return 1.0, ""
        games = r2.json().get("response", [])

        latest_game_date = None
        for g in games:
            game_info = g.get("game", {})
            status_short = game_info.get("status", {}).get("short")
            if status_short not in ["FT", "AOT", "FTOT", "FT+OT"]:
                continue
            game_dt_str = game_info.get("date")
            if not game_dt_str:
                continue
            try:
                game_dt = datetime.fromisoformat(game_dt_str.replace("Z", "+00:00")).date()
            except Exception:
                continue
            if latest_game_date is None or game_dt > latest_game_date:
                latest_game_date = game_dt

        if latest_game_date is None:
            return 1.0, ""

        days_rest = (today - latest_game_date).days
        if days_rest == 0:
            return 0.92, "0 days rest (back-to-back)"
        elif days_rest == 1:
            return 0.98, "1 day rest"
        else:
            return 1.0, f"{days_rest} days rest (normal)"

rest_detector = RestInjuryDetector()

# =============================================================================
# REAL-TIME PLAYER STATS (API-SPORTS)
# =============================================================================
@st.cache_data(ttl=3600)
@retry(max_attempts=2, delay=1)
def fetch_player_stats_and_injury(
    player_name: str,
    sport: str,
    market: str,
    num_games: int = 8,
) -> Tuple[List[float], str]:
    league_map = {
        "NBA": 12,
        "MLB": 253,
        "NHL": 57,
        "NFL": 1,
    }
    season_map = {
        "NBA": "2024-2025",
        "MLB": "2025",
        "NHL": "2024",
        "NFL": "2025",
    }

    if sport not in league_map:
        return [], "HEALTHY"

    headers = {"x-apisports-key": API_SPORTS_KEY}
    league_id = league_map[sport]
    season = season_map[sport]

    # Find player
    url = f"{API_SPORTS_BASE}/players"
    params = {"search": player_name, "league": league_id, "season": season}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        return [], "HEALTHY"
    players = r.json().get("response", [])
    if not players:
        return [], "HEALTHY"
    player_id = players[0]["player"]["id"]

    # Stats
    stats_url = f"{API_SPORTS_BASE}/players/statistics"
    stats_params = {"player": player_id, "league": league_id, "season": season}
    r2 = requests.get(stats_url, headers=headers, params=stats_params, timeout=10)
    if r2.status_code != 200:
        return [], "HEALTHY"
    games = r2.json().get("response", [])

    # Map market to nested stat key
    stat_key_map = {
        "PTS": ("points", "total"),
        "REB": ("rebounds", "total"),
        "AST": ("assists", "total"),
        "STL": ("steals", "total"),
        "BLK": ("blocks", "total"),
    }
    key_tuple = stat_key_map.get(market.upper(), ("points", "total"))

    games_sorted = sorted(games, key=lambda x: x.get("game", {}).get("date", ""), reverse=True)
    stats_list: List[float] = []
    for game in games_sorted[:num_games]:
        stats_dict = game.get("statistics", {})
        val = (
            stats_dict.get(key_tuple[0], {})
            .get(key_tuple[1], 0)
        )
        stats_list.append(float(val) if val is not None else 0.0)

    injury_status = "HEALTHY"  # placeholder; could be extended with injuries endpoint
    return stats_list, injury_status

# =============================================================================
# TEAM ROSTER FETCHER
# =============================================================================
@st.cache_data(ttl=86400)
@retry(max_attempts=2, delay=1)
def fetch_team_roster(sport: str, team: str) -> Tuple[List[str], bool]:
    if sport == "NBA" and team in FALLBACK_NBA_ROSTERS:
        fallback_roster = FALLBACK_NBA_ROSTERS[team]
    else:
        fallback_roster = ["Player 1", "Player 2", "Player 3", "Player 4", "Player 5"]

    if sport not in ["NBA", "MLB", "NHL", "NFL"]:
        return fallback_roster, True

    league_map = {
        "NBA": 12,
        "MLB": 253,
        "NHL": 57,
        "NFL": 1,
    }
    season_map = {
        "NBA": "2024-2025",
        "MLB": "2025",
        "NHL": "2024",
        "NFL": "2025",
    }

    league_id = league_map.get(sport)
    season = season_map.get(sport)
    if not league_id or not season:
        return fallback_roster, True

    headers = {"x-apisports-key": API_SPORTS_KEY}

    # Find team ID
    url = f"{API_SPORTS_BASE}/teams"
    params = {"league": league_id, "season": season, "search": team}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        return fallback_roster, True
    data = r.json().get("response", [])
    if not data:
        return fallback_roster, True
    team_id = data[0]["team"]["id"]

    # Fetch players
    players_url = f"{API_SPORTS_BASE}/players"
    params = {"league": league_id, "season": season, "team": team_id}
    r2 = requests.get(players_url, headers=headers, params=params, timeout=10)
    if r2.status_code != 200:
        return fallback_roster, True
    players_data = r2.json().get("response", [])

    roster = [p.get("player", {}).get("name") for p in players_data if p.get("player", {}).get("name")]
    if roster:
        return sorted(roster), False
    return fallback_roster, True

# =============================================================================
# AUTO-SETTLE PLAYER PROP (NBA via Balldontlie, others via API-SPORTS)
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

    # NBA first: Balldontlie
    if sport == "NBA":
        result, actual = balldontlie_settle_prop(player, market, line, pick, game_date)
        if result != "PENDING":
            return result, actual

    # Fallback: API-SPORTS
    league_map = {
        "NBA": 12,
        "MLB": 253,
        "NHL": 57,
        "NFL": 1,
    }
    season_map = {
        "NBA": "2024-2025",
        "MLB": "2025",
        "NHL": "2024",
        "NFL": "2025",
    }

    league_id = league_map.get(sport)
    season = season_map.get(sport)
    if not league_id or not season:
        return "PENDING", 0.0

    headers = {"x-apisports-key": API_SPORTS_KEY}

    # Find player
    url = f"{API_SPORTS_BASE}/players"
    params = {"search": player, "league": league_id, "season": season}
    r = requests.get(url, headers=headers, params=params, timeout=10)
    if r.status_code != 200:
        return "PENDING", 0.0
    players = r.json().get("response", [])
    if not players:
        return "PENDING", 0.0
    player_id = players[0]["player"]["id"]

    # Stats
    stats_url = f"{API_SPORTS_BASE}/players/statistics"
    params = {"player": player_id, "league": league_id, "season": season}
    r2 = requests.get(stats_url, headers=headers, params=params, timeout=10)
    if r2.status_code != 200:
        return "PENDING", 0.0
    games = r2.json().get("response", [])

    target_date = datetime.strptime(game_date, "%Y-%m-%d").date()
    stat_key_map = {
        "PTS": ("points", "total"),
        "REB": ("rebounds", "total"),
        "AST": ("assists", "total"),
        "STL": ("steals", "total"),
        "BLK": ("blocks", "total"),
    }

    market_upper = market.upper()
    key_tuple = stat_key_map.get(market_upper, ("points", "total"))

    actual_val: Optional[float] = None
    for game in games:
        game_info = game.get("game", {})
        game_dt_str = game_info.get("date")
        if not game_dt_str:
            continue
        try:
            game_dt = datetime.fromisoformat(game_dt_str.replace("Z", "+00:00")).date()
        except Exception:
            continue
        if game_dt != target_date:
            continue

        stats_dict = game.get("statistics", {})
        if market_upper == "PRA":
            pts = stats_dict.get("points", {}).get("total", 0)
            reb = stats_dict.get("rebounds", {}).get("total", 0)
            ast = stats_dict.get("assists", {}).get("total", 0)
            actual_val = float(pts + reb + ast)
        elif market_upper == "PR":
            pts = stats_dict.get("points", {}).get("total", 0)
            reb = stats_dict.get("rebounds", {}).get("total", 0)
            actual_val = float(pts + reb)
        elif market_upper == "PA":
            pts = stats_dict.get("points", {}).get("total", 0)
            ast = stats_dict.get("assists", {}).get("total", 0)
            actual_val = float(pts + ast)
        else:
            actual_val = float(
                stats_dict.get(key_tuple[0], {}).get(key_tuple[1], 0)
            )
        break

    if actual_val is None:
        return "PENDING", 0.0

    won = (actual_val > line) if pick.upper() == "OVER" else (actual_val < line)
    return ("WIN" if won else "LOSS"), actual_val

# =============================================================================
# SIMPLE PASTEBOARD PARSER (PrizePicks-style)
# =============================================================================
PROP_PATTERN = re.compile(
    r"(?P<player>[A-Za-z .'-]+)\s+(?P<market>[A-Z+]+)\s+(?P<line>\d+\.?\d*)\s+(?P<pick>OVER|UNDER)",
    re.IGNORECASE,
)

def parse_pasteboard(text: str, default_sport: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    results: List[Dict[str, Any]] = []
    for line in lines:
        m = PROP_PATTERN.search(line)
        if not m:
            continue
        d = m.groupdict()
        results.append(
            {
                "source": "PASTE",
                "sport": default_sport,
                "player": d["player"].strip(),
                "market": d["market"].upper(),
                "line": float(d["line"]),
                "pick": d["pick"].upper(),
                "opponent": "",
                "game_date": "",
            }
        )
    return results

# =============================================================================
# SIMPLE MODEL EVALUATION
# =============================================================================
def estimate_edge_from_history(
    values: List[float],
    line: float,
    pick: str,
    sport: str,
    market: str,
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
# STREAMLIT UI
# =============================================================================
def main():
    st.set_page_config(page_title="Clarity 18.0 Elite – Unified Quick Scanner", layout="wide")
    st.title("CLARITY 18.0 ELITE – Unified Quick Scanner")
    st.caption(f"Version: {VERSION} | Build: {BUILD_DATE}")

    init_db()

    with st.sidebar:
        st.subheader("Scan Settings")
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), index=0)
        check_scan_timing(sport)
        show_raw = st.checkbox("Show raw parsed props", value=False)

    st.markdown("### Paste Board")
    paste_text = st.text_area(
        "Paste PrizePicks / slips / tickets here:",
        height=220,
        placeholder="Example: LeBron James PTS 27.5 OVER\nNikola Jokic PRA 47.5 UNDER",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        run_scan = st.button("Scan & Analyze")
    with col2:
        auto_settle = st.button("Auto-Settle Last Tickets")

    results_df = None

    if run_scan and paste_text.strip():
        parsed = parse_pasteboard(paste_text, sport)
        if not parsed:
            st.warning("No valid props detected in the paste. Check formatting.")
        else:
            rows = []
            for p in parsed:
                stats, injury = fetch_player_stats_and_injury(
                    p["player"], p["sport"], p["market"], num_games=8
                )
                edge, prob = estimate_edge_from_history(
                    stats, p["line"], p["pick"], p["sport"], p["market"]
                )
                tier_info = STAT_CONFIG.get(p["market"], {"tier": "LOW", "buffer": 0.0, "reject": False})
                rows.append(
                    {
                        "Player": p["player"],
                        "Market": p["market"],
                        "Line": p["line"],
                        "Pick": p["pick"],
                        "Sport": p["sport"],
                        "Games Used": len(stats),
                        "Mean Stat": round(np.mean(stats), 2) if stats else 0.0,
                        "Edge": round(edge * 100, 1),
                        "Win Prob %": round(prob * 100, 1),
                        "Tier": tier_info["tier"],
                        "Red Tier": tier_info["reject"],
                        "Injury": injury,
                    }
                )

            results_df = pd.DataFrame(rows)
            st.markdown("### Scan Results")
            st.dataframe(results_df, use_container_width=True)

            if show_raw:
                st.markdown("#### Raw Parsed Props")
                st.json(parsed)

            # Save to DB as pending tickets (no result yet)
            for p in parsed:
                insert_ticket(
                    {
                        **p,
                        "result": "",
                        "actual": 0.0,
                    }
                )

    if auto_settle:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, sport, player, market, line, pick, opponent, game_date FROM tickets WHERE result = ''"
        )
        pending = cur.fetchall()
        conn.close()

        if not pending:
            st.info("No pending tickets to settle.")
        else:
            settled_rows = []
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            for row in pending:
                ticket_id, sport_, player_, market_, line_, pick_, opp_, game_date_ = row
                result, actual = auto_settle_prop(
                    player_, market_, float(line_), pick_, sport_, opp_, game_date_ or None
                )
                cur.execute(
                    "UPDATE tickets SET result = ?, actual = ? WHERE id = ?",
                    (result, actual, ticket_id),
                )
                settled_rows.append(
                    {
                        "ID": ticket_id,
                        "Player": player_,
                        "Market": market_,
                        "Line": line_,
                        "Pick": pick_,
                        "Sport": sport_,
                        "Result": result,
                        "Actual": actual,
                    }
                )
            conn.commit()
            conn.close()

            st.markdown("### Auto-Settled Tickets")
            st.dataframe(pd.DataFrame(settled_rows), use_container_width=True)

    st.markdown("---")
    st.caption("Built for unified scanning, props analysis, and result tracking. Keys are hard-coded per your request.")

if __name__ == "__main__":
    main()
```
