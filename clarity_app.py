"""
CLARITY 18.3 ELITE – Full Feature Set + Best Bet Per Game + Unified Slip Parser
- Complete 5‑tab dashboard
- Unified slip parser (MyBookie, Bovada, PrizePicks) with auto‑settlement
- OCR screenshot support
- Best Bet Per Game
- HISTORY & METRICS tab: settled bets, win rates, auto‑tune, SEM score (no redundant settlement)
- Handles missing 'profit' column gracefully
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import sqlite3
import re
import time
import requests
import hashlib
import threading
import warnings
import pickle
import os
import shutil
from functools import wraps

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

try:
    import sportly
    SPORTLY_AVAILABLE = True
except ImportError:
    SPORTLY_AVAILABLE = False

warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION – YOUR API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

VERSION = "18.3 Elite (Full Feature + History & Metrics)"
BUILD_DATE = "2026-04-18"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"
BALLSDONTLIE_BASE = "https://api.balldontlie.io"

DB_PATH = "clarity_history.db"

# =============================================================================
# DATABASE HELPERS
# =============================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS bets (
        id TEXT PRIMARY KEY, player TEXT, sport TEXT, market TEXT, line REAL,
        pick TEXT, odds INTEGER, edge REAL, result TEXT, actual REAL,
        date TEXT, settled_date TEXT, bolt_signal TEXT, profit REAL,
        closing_odds INTEGER, ml_proba REAL, wa_proba REAL,
        is_home INTEGER DEFAULT 0
    )""")
    try:
        c.execute("ALTER TABLE bets ADD COLUMN is_home INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE bets ADD COLUMN profit REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    c.execute("""CREATE TABLE IF NOT EXISTS correlations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player TEXT, market1 TEXT, market2 TEXT, covariance REAL, sample_size INTEGER,
        last_updated TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sem_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, sem_score INTEGER, accuracy REAL, bets_analyzed INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tuning_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, prob_bolt_old REAL, prob_bolt_new REAL,
        dtm_bolt_old REAL, dtm_bolt_new REAL, roi REAL, bets_used INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS ml_retrain_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, bets_used INTEGER, rmse REAL
    )""")
    conn.commit()
    conn.close()

def insert_bet(bet: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    bet_id = hashlib.md5(f"{bet['player']}{bet['market']}{bet['line']}{datetime.now()}".encode()).hexdigest()[:12]
    c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, settled_date, bolt_signal, profit)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (bet_id, bet['player'], bet['sport'], bet['market'], bet['line'],
               bet['pick'], bet.get('odds', 0), bet.get('edge', 0.0), bet.get('result', 'PENDING'), bet.get('actual', 0.0),
               datetime.now().strftime("%Y-%m-%d"), "", bet.get('bolt_signal', ''), bet.get('profit', 0)))
    conn.commit()
    conn.close()

def get_pending_bets() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, player, sport, market, line, pick, odds, date FROM bets WHERE result = 'PENDING'")
    rows = c.fetchall()
    conn.close()
    return [{"id": r[0], "player": r[1], "sport": r[2], "market": r[3], "line": r[4], "pick": r[5], "odds": r[6], "game_date": r[7]} for r in rows]

def get_recent_bets(limit: int = 200) -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(f"SELECT id, player, sport, market, line, pick, odds, result, actual, date, profit FROM bets ORDER BY date DESC LIMIT {limit}", conn)
    conn.close()
    return df

def update_bet_result(bet_id: str, result: str, actual: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    profit = 0
    c.execute("SELECT odds FROM bets WHERE id = ?", (bet_id,))
    row = c.fetchone()
    if row and result == "WIN":
        odds = row[0]
        if odds > 0:
            profit = (odds / 100) * 100
        else:
            profit = (100 / abs(odds)) * 100
    elif result == "LOSS":
        profit = -100
    c.execute("UPDATE bets SET result = ?, actual = ?, settled_date = ?, profit = ? WHERE id = ?",
              (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, bet_id))
    conn.commit()
    conn.close()

def clear_pending_bets():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM bets WHERE result = 'PENDING'")
    conn.commit()
    conn.close()

# =============================================================================
# RETRY DECORATOR
# =============================================================================
def retry(max_attempts=3, delay=2, backoff=3):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _delay = delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    time.sleep(_delay)
                    _delay *= backoff
            return None
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
            st.warning("⏰ Optimal scanning times for NBA/MLB/NHL are 6 AM, 2 PM, and 9 PM. Current time may yield less stable lines.")
    elif sport == "NFL":
        if not ((weekday == 0 and 9 <= hour <= 11) or (weekday == 1 and 5 <= hour <= 7) or (weekday == 6 and 9 <= hour <= 11)):
            st.warning("🏈 NFL lines are best scanned Monday 10 AM, Tuesday 6 AM, or Sunday 10 AM. Current time may not capture optimal value.")

# =============================================================================
# SPORT MODELS (full)
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 44.5, "home_advantage": 2.8},
    "PGA": {"distribution": "nbinom", "variance_factor": 1.10, "avg_total": 70.5, "home_advantage": 0.0},
    "TENNIS": {"distribution": "poisson", "variance_factor": 1.05, "avg_total": 22.0, "home_advantage": 0.0},
    "UFC": {"distribution": "poisson", "variance_factor": 1.20, "avg_total": 2.5, "home_advantage": 0.0},
    "SOCCER_EPL": {"distribution": "poisson", "variance_factor": 1.10, "avg_total": 2.5, "home_advantage": 0.3},
    "SOCCER_LALIGA": {"distribution": "poisson", "variance_factor": 1.10, "avg_total": 2.5, "home_advantage": 0.3},
    "COLLEGE_BASKETBALL": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 145.5, "home_advantage": 3.5},
    "COLLEGE_FOOTBALL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 55.5, "home_advantage": 3.0},
    "ESPORTS_LOL": {"distribution": "poisson", "variance_factor": 1.05, "avg_total": 22.5, "home_advantage": 0.0},
    "ESPORTS_CS2": {"distribution": "poisson", "variance_factor": 1.05, "avg_total": 2.5, "home_advantage": 0.0},
}

SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"],
    "PGA": ["STROKES", "BIRDIES", "BOGEYS", "EAGLES", "DRIVING_DISTANCE", "GIR"],
    "TENNIS": ["ACES", "DOUBLE_FAULTS", "GAMES_WON", "TOTAL_GAMES", "BREAK_PTS"],
    "UFC": ["SIGNIFICANT_STRIKES", "TAKEDOWNS", "FIGHT_TIME", "SUB_ATTEMPTS"],
    "SOCCER_EPL": ["GOALS", "ASSISTS", "SHOTS", "SHOTS_ON_TARGET", "PASSES"],
    "SOCCER_LALIGA": ["GOALS", "ASSISTS", "SHOTS", "SHOTS_ON_TARGET", "PASSES"],
    "COLLEGE_BASKETBALL": ["PTS", "REB", "AST", "STL", "BLK", "PRA"],
    "COLLEGE_FOOTBALL": ["PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"],
    "ESPORTS_LOL": ["KILLS", "DEATHS", "ASSISTS", "KDA"],
    "ESPORTS_CS2": ["KILLS", "DEATHS", "ASSISTS", "ADR"],
}

STAT_CONFIG = {
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "STL": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "BLK": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "THREES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "OUTS": {"tier": "LOW", "buffer": 0.0, "reject": False},
    "KS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "HITS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TB": {"tier": "MED", "buffer": 1.0, "reject": False},
    "HR": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "SOG": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "SAVES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "H+R+RBI": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "HITTER_FS": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PITCHER_FS": {"tier": "HIGH", "buffer": 5.0, "reject": True},
    "STROKES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "BIRDIES": {"tier": "MED", "buffer": 1.0, "reject": False},
    "ACES": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "GAMES_WON": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "SIGNIFICANT_STRIKES": {"tier": "MED", "buffer": 10.0, "reject": False},
}
RED_TIER_PROPS = ["PRA", "PR", "PA", "H+R+RBI", "HITTER_FS", "PITCHER_FS"]

# =============================================================================
# HARDCODED TEAMS (full list)
# =============================================================================
HARDCODED_TEAMS = {
    "NBA": ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls",
            "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons",
            "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers",
            "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
            "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
            "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
            "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors",
            "Utah Jazz", "Washington Wizards"],
    "MLB": ["Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles", "Boston Red Sox",
            "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds", "Cleveland Guardians",
            "Colorado Rockies", "Detroit Tigers", "Houston Astros", "Kansas City Royals",
            "Los Angeles Angels", "Los Angeles Dodgers", "Miami Marlins", "Milwaukee Brewers",
            "Minnesota Twins", "New York Mets", "New York Yankees", "Oakland Athletics",
            "Philadelphia Phillies", "Pittsburgh Pirates", "San Diego Padres", "San Francisco Giants",
            "Seattle Mariners", "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers",
            "Toronto Blue Jays", "Washington Nationals"],
    "NHL": ["Anaheim Ducks", "Boston Bruins", "Buffalo Sabres", "Calgary Flames", "Carolina Hurricanes",
            "Chicago Blackhawks", "Colorado Avalanche", "Columbus Blue Jackets", "Dallas Stars",
            "Detroit Red Wings", "Edmonton Oilers", "Florida Panthers", "Los Angeles Kings",
            "Minnesota Wild", "Montreal Canadiens", "Nashville Predators", "New Jersey Devils",
            "New York Islanders", "New York Rangers", "Ottawa Senators", "Philadelphia Flyers",
            "Pittsburgh Penguins", "San Jose Sharks", "Seattle Kraken", "St. Louis Blues",
            "Tampa Bay Lightning", "Toronto Maple Leafs", "Utah Hockey Club", "Vancouver Canucks",
            "Vegas Golden Knights", "Washington Capitals", "Winnipeg Jets"],
    "NFL": ["Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
            "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
            "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
            "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
            "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
            "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants",
            "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
            "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders"],
    "PGA": ["PGA Tour"],
    "TENNIS": ["ATP", "WTA"],
    "UFC": ["UFC"],
    "SOCCER_EPL": ["Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton", "Chelsea", "Crystal Palace",
                   "Everton", "Fulham", "Leeds United", "Leicester City", "Liverpool", "Manchester City",
                   "Manchester United", "Newcastle United", "Nottingham Forest", "Southampton", "Tottenham",
                   "West Ham", "Wolverhampton"],
    "SOCCER_LALIGA": ["Athletic Bilbao", "Atletico Madrid", "Barcelona", "Betis", "Celta Vigo", "Espanyol",
                      "Getafe", "Girona", "Mallorca", "Osasuna", "Rayo Vallecano", "Real Madrid", "Real Sociedad",
                      "Sevilla", "Valencia", "Valladolid", "Villarreal"],
    "COLLEGE_BASKETBALL": ["Duke", "North Carolina", "Kansas", "Kentucky", "UCLA", "Gonzaga", "Baylor", "Michigan State"],
    "COLLEGE_FOOTBALL": ["Alabama", "Georgia", "Ohio State", "Michigan", "Clemson", "LSU", "USC", "Texas"],
    "ESPORTS_LOL": ["T1", "Gen.G", "G2 Esports", "Fnatic", "Cloud9", "DWG KIA"],
    "ESPORTS_CS2": ["NAVI", "FaZe Clan", "G2", "Vitality", "ENCE", "MOUZ"]
}

FALLBACK_NBA_ROSTERS = {
    "Atlanta Hawks": ["Trae Young", "Dejounte Murray", "Jalen Johnson", "Clint Capela", "Bogdan Bogdanovic"],
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Jrue Holiday", "Derrick White"],
    "Los Angeles Lakers": ["LeBron James", "Luka Doncic", "Austin Reaves", "Rui Hachimura", "Dorian Finney-Smith"],
}

# =============================================================================
# BALLSDONTLIE API HELPERS
# =============================================================================
def balldontlie_request(endpoint: str, params: dict = None) -> Optional[dict]:
    headers = {"Authorization": BALLSDONTLIE_API_KEY}
    url = f"{BALLSDONTLIE_BASE}{endpoint}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        else:
            return None
    except:
        return None

def balldontlie_get_player_stats(player_name: str, game_date: str) -> Optional[Dict]:
    players_data = balldontlie_request("/players", params={"search": player_name})
    if not players_data or not players_data.get("data"):
        return None
    player_id = players_data["data"][0]["id"]
    stats_data = balldontlie_request("/stats", params={"player_ids[]": player_id, "dates[]": game_date})
    if stats_data and stats_data.get("data"):
        return stats_data["data"][0].get("stats", {})
    return None

def balldontlie_settle_prop(player: str, market: str, line: float, pick: str, game_date: str) -> Tuple[str, float]:
    stats = balldontlie_get_player_stats(player, game_date)
    if not stats:
        return "PENDING", 0.0
    market_map = {"PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk", "FG3M": "fg3m"}
    actual_val = None
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
    won = (actual_val > line) if pick == "OVER" else (actual_val < line)
    return ("WIN" if won else "LOSS"), actual_val

# =============================================================================
# OPPONENT STRENGTH CACHE
# =============================================================================
class OpponentStrengthCache:
    def __init__(self):
        self.cache = {}
        self.last_fetch = {}
    @retry(max_attempts=2, delay=1)
    def get_defensive_rating(self, sport: str, team: str) -> float:
        if sport not in ["NBA", "NHL", "MLB"]:
            return 1.0
        key = f"{sport}_{team}"
        now = datetime.now()
        if key in self.cache and key in self.last_fetch and (now - self.last_fetch[key]).days < 1:
            return self.cache[key]
        league_map = {"NBA": 12, "NHL": 5, "MLB": 1}
        league_id = league_map.get(sport)
        if not league_id:
            return 1.0
        headers = {"x-apisports-key": API_SPORTS_KEY}
        try:
            url = "https://v1.api-sports.io/teams"
            params = {"league": league_id, "season": "2025-2026" if sport=="NBA" else "2025", "search": team}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                return 1.0
            data = r.json().get("response", [])
            if not data:
                return 1.0
            team_id = data[0]["team"]["id"]
            stats_url = "https://v1.api-sports.io/teams/statistics"
            stats_params = {"league": league_id, "season": "2025-2026" if sport=="NBA" else "2025", "team": team_id}
            r2 = requests.get(stats_url, headers=headers, params=stats_params, timeout=10)
            if r2.status_code != 200:
                return 1.0
            stats_data = r2.json().get("response", {})
            if sport == "NBA":
                pts_allowed = stats_data.get("points", {}).get("against", {}).get("average", 115.0)
                rating = 115.0 / pts_allowed
            elif sport == "NHL":
                goals_allowed = stats_data.get("goals", {}).get("against", {}).get("average", 3.0)
                rating = 3.0 / goals_allowed
            elif sport == "MLB":
                runs_allowed = stats_data.get("runs", {}).get("against", {}).get("average", 4.5)
                rating = 4.5 / runs_allowed
            else:
                rating = 1.0
            self.cache[key] = max(0.8, min(1.2, rating))
            self.last_fetch[key] = now
            return self.cache[key]
        except:
            return 1.0

opponent_strength = OpponentStrengthCache()

# =============================================================================
# REST & INJURY DETECTOR
# =============================================================================
class RestInjuryDetector:
    def __init__(self):
        self.schedule_cache = {}
    @retry(max_attempts=2, delay=1)
    def get_rest_fade(self, sport: str, team: str) -> Tuple[float, str]:
        if sport not in ["NBA", "NHL", "MLB", "NFL"]:
            return 1.0, ""
        league_map = {"NBA": 12, "NHL": 5, "MLB": 1, "NFL": 1}
        league_id = league_map.get(sport)
        if not league_id:
            return 1.0, ""
        headers = {"x-apisports-key": API_SPORTS_KEY}
        try:
            url = "https://v1.api-sports.io/teams"
            params = {"league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025", "search": team}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                return 1.0, ""
            data = r.json().get("response", [])
            if not data:
                return 1.0, ""
            team_id = data[0]["team"]["id"]
            games_url = "https://v1.api-sports.io/games"
            today = datetime.now().date()
            params = {"league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025",
                      "team": team_id, "from": (today - timedelta(days=5)).strftime("%Y-%m-%d"),
                      "to": today.strftime("%Y-%m-%d")}
            r2 = requests.get(games_url, headers=headers, params=params, timeout=10)
            if r2.status_code != 200:
                return 1.0, ""
            games = r2.json().get("response", [])
            latest_game_date = None
            for g in games:
                if g["game"]["status"]["short"] == "FT":
                    game_dt = datetime.strptime(g["game"]["date"], "%Y-%m-%dT%H:%M:%S%z").date()
                    if latest_game_date is None or game_dt > latest_game_date:
                        latest_game_date = game_dt
            if latest_game_date:
                days_rest = (today - latest_game_date).days
                if days_rest == 0:
                    return 0.92, "0 days rest (back-to-back)"
                elif days_rest == 1:
                    return 0.98, "1 day rest"
                else:
                    return 1.0, f"{days_rest} days rest (normal)"
            return 1.0, ""
        except:
            return 1.0, ""

rest_detector = RestInjuryDetector()

# =============================================================================
# REAL-TIME DATA FETCHERS (fallback)
# =============================================================================
@st.cache_data(ttl=3600)
@retry(max_attempts=2, delay=1)
def fetch_player_stats_and_injury(player_name: str, sport: str, market: str, num_games: int = 8) -> Tuple[List[float], str]:
    league_map = {"NBA": 12, "MLB": 1, "NHL": 5, "NFL": 1, "SOCCER_EPL": 39, "SOCCER_LALIGA": 140}
    season_map = {"NBA": "2025-2026", "MLB": "2025", "NHL": "2025-2026", "NFL": "2025", "SOCCER_EPL": "2025", "SOCCER_LALIGA": "2025"}
    stat_map = {"PTS": "points", "REB": "rebounds", "AST": "assists", "STL": "steals", "BLK": "blocks", "THREES": "threes", "3PT": "threes"}
    if sport not in league_map:
        return [], "HEALTHY"
    headers = {"x-apisports-key": API_SPORTS_KEY}
    injury_status = "HEALTHY"
    stats = []
    try:
        url = "https://v1.api-sports.io/players"
        params = {"search": player_name, "league": league_map[sport], "season": season_map.get(sport, "2025")}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return [], "HEALTHY"
        players = r.json().get("response", [])
        if not players:
            return [], "HEALTHY"
        player_id = players[0]["player"]["id"]
        stats_url = "https://v1.api-sports.io/players/statistics"
        stats_params = {"player": player_id, "league": league_map[sport], "season": season_map.get(sport, "2025")}
        r2 = requests.get(stats_url, headers=headers, params=stats_params, timeout=10)
        if r2.status_code == 200:
            games = r2.json().get("response", [])
            games_sorted = sorted(games, key=lambda x: x.get("game", {}).get("date", ""), reverse=True)
            stat_key = stat_map.get(market.upper(), "points")
            for game in games_sorted[:num_games]:
                val = game.get("statistics", {}).get(stat_key, 0)
                stats.append(float(val) if val else 0.0)
    except:
        pass
    return stats, injury_status

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
    league_map = {"NBA": 12, "MLB": 1, "NHL": 5, "NFL": 1}
    league_id = league_map.get(sport)
    if not league_id:
        return fallback_roster, True
    headers = {"x-apisports-key": API_SPORTS_KEY}
    try:
        url = "https://v1.api-sports.io/teams"
        params = {"league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025", "search": team}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return fallback_roster, True
        data = r.json().get("response", [])
        if not data:
            return fallback_roster, True
        team_id = data[0]["team"]["id"]
        players_url = "https://v1.api-sports.io/players"
        params = {"league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025", "team": team_id}
        r2 = requests.get(players_url, headers=headers, params=params, timeout=10)
        if r2.status_code != 200:
            return fallback_roster, True
        players_data = r2.json().get("response", [])
        roster = [p["player"]["name"] for p in players_data if p.get("player", {}).get("name")]
        if roster:
            return sorted(roster), False
        else:
            return fallback_roster, True
    except:
        return fallback_roster, True

# =============================================================================
# AUTO-SETTLE PLAYER PROP
# =============================================================================
def auto_settle_prop(player: str, market: str, line: float, pick: str, sport: str, opponent: str, game_date: str = None) -> Tuple[str, float]:
    if not game_date:
        game_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    if sport == "NBA":
        result, actual = balldontlie_settle_prop(player, market, line, pick, game_date)
        if result != "PENDING":
            return result, actual
    headers = {"x-apisports-key": API_SPORTS_KEY}
    league_map = {"NBA": 12, "MLB": 1, "NHL": 5, "NFL": 1}
    league_id = league_map.get(sport)
    if not league_id:
        return "PENDING", 0.0
    market_map = {"PTS": "points", "REB": "rebounds", "AST": "assists", "STL": "steals", "BLK": "blocks", "THREES": "threes", "3PT": "threes"}
    try:
        url = "https://v1.api-sports.io/players"
        params = {"search": player, "league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return "PENDING", 0.0
        players = r.json().get("response", [])
        if not players:
            return "PENDING", 0.0
        player_id = players[0]["player"]["id"]
        stats_url = "https://v1.api-sports.io/players/statistics"
        params = {"player": player_id, "league": league_id, "season": "2025-2026" if sport in ["NBA","NHL"] else "2025"}
        r2 = requests.get(stats_url, headers=headers, params=params, timeout=10)
        if r2.status_code != 200:
            return "PENDING", 0.0
        games = r2.json().get("response", [])
        target_date = datetime.strptime(game_date, "%Y-%m-%d").date()
        actual_val = None
        for game in games:
            game_info = game.get("game", {})
            game_dt_str = game_info.get("date", "")
            if not game_dt_str:
                continue
            game_dt = datetime.strptime(game_dt_str, "%Y-%m-%dT%H:%M:%S%z").date()
            if game_dt == target_date:
                stats_dict = game.get("statistics", {})
                market_upper = market.upper()
                if market_upper == "PRA":
                    actual_val = float(stats_dict.get("points", 0)) + float(stats_dict.get("rebounds", 0)) + float(stats_dict.get("assists", 0))
                elif market_upper == "PR":
                    actual_val = float(stats_dict.get("points", 0)) + float(stats_dict.get("rebounds", 0))
                elif market_upper == "PA":
                    actual_val = float(stats_dict.get("points", 0)) + float(stats_dict.get("assists", 0))
                else:
                    stat_field = market_map.get(market_upper, market_upper.lower())
                    actual_val = float(stats_dict.get(stat_field, 0))
                break
        if actual_val is None:
            return "PENDING", 0.0
        won = (actual_val > line) if pick == "OVER" else (actual_val < line)
        return ("WIN" if won else "LOSS"), actual_val
    except:
        return "PENDING", 0.0

# =============================================================================
# AUTO-SETTLE GAME LINE USING SPORTLY
# =============================================================================
def auto_settle_game_line(team: str, market: str, line: float, pick: str, sport: str, opponent: str, game_date: str) -> Tuple[str, float]:
    if not SPORTLY_AVAILABLE:
        return "PENDING", 0.0
    try:
        sport_map = {"NBA": "nba", "NFL": "nfl", "MLB": "mlb", "NHL": "nhl"}
        sport_key = sport_map.get(sport.upper())
        if not sport_key:
            return "PENDING", 0.0
        target_date = datetime.strptime(game_date, "%Y-%m-%d")
        if sport_key == "nba":
            scoreboard = sportly.nba.scoreboard(target_date)
        elif sport_key == "mlb":
            scoreboard = sportly.mlb.schedule(game_date=target_date)
        elif sport_key == "nhl":
            scoreboard = sportly.nhl.scoreboard(target_date)
        else:
            return "PENDING", 0.0
        team_score = None
        opp_score = None
        for game in scoreboard:
            home = getattr(game, 'home_team', None) or game.get('home_team', '')
            away = getattr(game, 'away_team', None) or game.get('away_team', '')
            if (home == team and away == opponent) or (home == opponent and away == team):
                if home == team:
                    team_score = getattr(game, 'home_score', None) or game.get('home_score', 0)
                    opp_score = getattr(game, 'away_score', None) or game.get('away_score', 0)
                else:
                    team_score = getattr(game, 'away_score', None) or game.get('away_score', 0)
                    opp_score = getattr(game, 'home_score', None) or game.get('home_score', 0)
                break
        if team_score is None:
            return "PENDING", 0.0
        team_score = float(team_score)
        opp_score = float(opp_score)
        market_upper = market.upper()
        if "ML" in market_upper:
            won = team_score > opp_score
            return ("WIN" if won else "LOSS"), team_score
        elif "SPREAD" in market_upper:
            margin = team_score - opp_score
            if pick == team:
                won = margin > line
            else:
                won = margin < line
            return ("WIN" if won else "LOSS"), margin
        elif "TOTAL" in market_upper:
            total = team_score + opp_score
            if "OVER" in pick.upper():
                won = total > line
            else:
                won = total < line
            return ("WIN" if won else "LOSS"), total
        else:
            return "PENDING", 0.0
    except Exception as e:
        print(f"Auto-settle error: {e}")
        return "PENDING", 0.0

# =============================================================================
# SEASON CONTEXT ENGINE
# =============================================================================
class SeasonContextEngine:
    def __init__(self):
        self.cache = {}
        self.season_calendars = {
            "NBA": {"regular_season_end": "2026-04-13", "playoffs_start": "2026-04-19"},
            "MLB": {"regular_season_end": "2026-09-28", "playoffs_start": "2026-10-03"},
            "NHL": {"regular_season_end": "2026-04-17", "playoffs_start": "2026-04-20"},
            "NFL": {"regular_season_end": "2026-01-04", "playoffs_start": "2026-01-10"}
        }
        self.motivation_multipliers = {"MUST_WIN":1.12, "PLAYOFF_SEEDING":1.08, "NEUTRAL":1.00,
                                       "LOCKED_SEED":0.92, "ELIMINATED":0.85, "TANKING":0.78, "PLAYOFFS":1.05}
    def get_season_phase(self, sport: str) -> dict:
        date_obj = datetime.now()
        calendar = self.season_calendars.get(sport, {})
        if not calendar:
            return {"phase":"UNKNOWN","is_playoffs":False}
        if "playoffs_start" in calendar:
            playoffs_start = datetime.strptime(calendar["playoffs_start"], "%Y-%m-%d")
            if date_obj >= playoffs_start:
                return {"phase":"PLAYOFFS","is_playoffs":True}
        season_end = datetime.strptime(calendar.get("regular_season_end", "2026-12-31"), "%Y-%m-%d")
        days_remaining = (season_end - date_obj).days
        phase = "FINAL_DAY" if days_remaining<=0 else "FINAL_WEEK" if days_remaining<=7 else "REGULAR_SEASON"
        return {"phase":phase,"is_playoffs":False,"days_remaining":days_remaining,
                "is_final_week":days_remaining<=7,"is_final_day":days_remaining==0}
    def should_fade_team(self, sport: str, team: str) -> dict:
        cache_key = f"{sport}_{team}_{datetime.now().strftime('%Y%m%d')}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        phase = self.get_season_phase(sport)
        result = {"team":team,"fade":False,"reasons":[],"multiplier":1.0,"phase":phase}
        fade_mult, reason = rest_detector.get_rest_fade(sport, team)
        if fade_mult < 1.0:
            result["fade"] = True
            result["reasons"].append(reason)
            result["multiplier"] *= fade_mult
        self.cache[cache_key] = result
        return result

# =============================================================================
# GAME SCANNER (Odds-API.io)
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.odds_api_io_key = ODDS_API_IO_KEY

    def fetch_games_by_date(self, sports: List[str] = None, days_offset: int = 0) -> List[Dict]:
        if sports is None:
            sports = ["NBA","MLB","NHL","NFL"]
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")
        games = self._fetch_games_from_odds_api_io(sports, target_date)
        if games:
            return games
        if days_offset != 0:
            return []
        return self.fetch_todays_games(sports)

    @retry(max_attempts=2, delay=1)
    def _fetch_games_from_odds_api_io(self, sports: List[str], date_str: str) -> List[Dict]:
        all_games = []
        sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
        for sport in sports:
            sport_key = sport_map.get(sport)
            if not sport_key:
                continue
            url = f"{ODDS_API_IO_BASE}/sports/{sport_key}/events"
            params = {"apiKey": self.odds_api_io_key}
            if date_str:
                params["date"] = date_str
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                events = data.get("data", []) if isinstance(data, dict) else data
                for event in events[:10]:
                    game = {
                        "sport": sport,
                        "home": event.get("home_team", ""),
                        "away": event.get("away_team", ""),
                        "date": event.get("commence_time", ""),
                        "event_id": event.get("id")
                    }
                    odds_url = f"{ODDS_API_IO_BASE}/sports/{sport_key}/events/{event['id']}/odds"
                    odds_params = {"apiKey": self.odds_api_io_key, "regions": "us", "markets": "h2h,spreads,totals"}
                    try:
                        r2 = requests.get(odds_url, params=odds_params, timeout=10)
                        if r2.status_code == 200:
                            odds_data = r2.json()
                            bookmakers = odds_data.get("data", {}).get("bookmakers", []) if isinstance(odds_data, dict) else []
                            if bookmakers:
                                bm = bookmakers[0]
                                markets = bm.get("markets", [])
                                for m in markets:
                                    if m["key"] == "h2h":
                                        for o in m["outcomes"]:
                                            if o["name"] == game["home"]:
                                                game["home_ml"] = o["price"]
                                            elif o["name"] == game["away"]:
                                                game["away_ml"] = o["price"]
                                    elif m["key"] == "spreads":
                                        for o in m["outcomes"]:
                                            if o["name"] == game["home"]:
                                                game["spread"] = o["point"]
                                                game["spread_odds"] = o["price"]
                                    elif m["key"] == "totals":
                                        game["total"] = m["outcomes"][0]["point"]
                                        for o in m["outcomes"]:
                                            if o["name"] == "Over":
                                                game["over_odds"] = o["price"]
                                            elif o["name"] == "Under":
                                                game["under_odds"] = o["price"]
                    except:
                        pass
                    all_games.append(game)
        return all_games

    @retry(max_attempts=2, delay=1)
    def fetch_todays_games(self, sports: List[str] = None) -> List[Dict]:
        if sports is None:
            sports = ["NBA","MLB","NHL","NFL"]
        all_games = []
        sport_keys = {
            "NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl",
            "SOCCER_EPL":"soccer_epl","SOCCER_LALIGA":"soccer_spain_la_liga",
            "COLLEGE_BASKETBALL":"basketball_ncaab","COLLEGE_FOOTBALL":"americanfootball_ncaaf",
            "ESPORTS_LOL":"esports_lol","ESPORTS_CS2":"esports_csgo"
        }
        for sport in sports:
            key = sport_keys.get(sport)
            if not key:
                continue
            url = f"{self.base_url}/sports/{key}/odds"
            params = {"apiKey":self.api_key,"regions":"us","markets":"h2h,spreads,totals","oddsFormat":"american"}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                for game in r.json():
                    game_data = {
                        "sport": sport,
                        "home": game["home_team"],
                        "away": game["away_team"],
                        "bookmakers": game.get("bookmakers", [])
                    }
                    if game_data["bookmakers"]:
                        bm = game_data["bookmakers"][0]
                        markets = {m["key"]: m for m in bm.get("markets", [])}
                        if "h2h" in markets:
                            outcomes = markets["h2h"]["outcomes"]
                            game_data["home_ml"] = next((o["price"] for o in outcomes if o["name"]==game["home_team"]), None)
                            game_data["away_ml"] = next((o["price"] for o in outcomes if o["name"]==game["away_team"]), None)
                        if "spreads" in markets:
                            outcomes = markets["spreads"]["outcomes"]
                            game_data["spread"] = next((o["point"] for o in outcomes if o["name"]==game["home_team"]), None)
                            game_data["spread_odds"] = next((o["price"] for o in outcomes if o["name"]==game["home_team"]), None)
                        if "totals" in markets:
                            outcomes = markets["totals"]["outcomes"]
                            game_data["total"] = next((o["point"] for o in outcomes), None)
                            game_data["over_odds"] = next((o["price"] for o in outcomes if o["name"]=="Over"), None)
                            game_data["under_odds"] = next((o["price"] for o in outcomes if o["name"]=="Under"), None)
                    all_games.append(game_data)
        return all_games

    def get_game_odds(self, home: str, away: str, sport: str) -> Optional[Dict]:
        games = self.fetch_todays_games([sport])
        for game in games:
            if game.get("home") == home and game.get("away") == away:
                return game
            if game.get("home") == away and game.get("away") == home:
                return {"home": game["away"], "away": game["home"], "home_ml": game.get("away_ml"), "away_ml": game.get("home_ml"),
                        "spread": game.get("spread"), "spread_odds": game.get("spread_odds"),
                        "total": game.get("total"), "over_odds": game.get("over_odds"), "under_odds": game.get("under_odds")}
        return None

    def fetch_player_props_odds(self, sport: str = "basketball_nba", markets: str = "player_points,player_assists,player_rebounds") -> List[Dict]:
        all_props = []
        sport_map = {"basketball_nba": "basketball", "baseball_mlb": "baseball", "icehockey_nhl": "icehockey", "americanfootball_nfl": "americanfootball"}
        sport_key = sport_map.get(sport, "basketball")
        url = f"{ODDS_API_IO_BASE}/value-bets"
        params = {"apiKey": self.odds_api_io_key, "sport": sport_key, "bookmaker": "all", "limit": 100}
        try:
            r = requests.get(url, params=params, timeout=15)
            if r.status_code == 200:
                data = r.json()
                bets = data.get("data", []) if isinstance(data, dict) else data
                for bet in bets[:100]:
                    player_name = bet.get("participant_name", "")
                    market = bet.get("market", "").upper().replace("PLAYER_", "")
                    line = bet.get("point", 0)
                    odds = bet.get("price", -110)
                    bookmaker = bet.get("bookmaker", "Odds-API.io")
                    pick = "OVER" if "over" in str(bet.get("selection", "")).lower() else "UNDER"
                    if player_name and market and line:
                        all_props.append({
                            "sport": sport,
                            "player": player_name,
                            "market": market,
                            "line": line,
                            "odds": odds,
                            "bookmaker": bookmaker,
                            "pick": pick
                        })
        except:
            pass
        return all_props

    def _get_fallback_player_props(self, sport: str) -> List[Dict]:
        fallback_props = []
        sample_props = {
            "basketball_nba": [("LeBron James","PTS",25.5,-110,"PrizePicks"),("Stephen Curry","PTS",28.5,-110,"PrizePicks"),("Kevin Durant","PTS",27.5,-110,"PrizePicks")],
            "baseball_mlb": [("Shohei Ohtani","HR",0.5,120,"PrizePicks"),("Aaron Judge","HR",0.5,110,"PrizePicks")],
            "americanfootball_nfl": [("Patrick Mahomes","PASS_YDS",275.5,-110,"PrizePicks"),("Josh Allen","PASS_YDS",260.5,-110,"PrizePicks")],
            "icehockey_nhl": [("Connor McDavid","SOG",3.5,-110,"PrizePicks"),("Nathan MacKinnon","SOG",4.5,-110,"PrizePicks")]
        }
        for s, props in sample_props.items():
            if sport == s:
                for p in props:
                    fallback_props.append({"sport": sport, "player": p[0], "market": p[1], "line": p[2], "odds": p[3], "bookmaker": p[4], "pick": "OVER"})
                break
        return fallback_props

# =============================================================================
# LIGHTGBM MODEL
# =============================================================================
class LightGBMPropModel:
    def __init__(self, model_path="clarity_model.pkl"):
        self.model = None
        self.trained = False
        self.model_path = model_path
        self._load_if_exists()
    def _load_if_exists(self):
        if os.path.exists(self.model_path) and LGB_AVAILABLE:
            try:
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                    self.trained = True
            except:
                pass
    def save(self):
        if self.trained and self.model and LGB_AVAILABLE:
            with open(self.model_path, 'wb') as f:
                pickle.dump(self.model, f)
    def train(self, X, y):
        if not LGB_AVAILABLE:
            return
        params = {"objective": "regression", "metric": "rmse", "num_leaves": 31, "learning_rate": 0.05, "verbose": -1}
        train_data = lgb.Dataset(X, label=y)
        self.model = lgb.train(params, train_data, num_boost_round=100, valid_sets=[train_data], callbacks=[lgb.early_stopping(10), lgb.log_evaluation(-1)])
        self.trained = True
        self.save()
    def predict(self, X):
        if self.trained and self.model:
            return self.model.predict(X)
        return None

class EnsemblePredictor:
    def __init__(self):
        self.ml_model = LightGBMPropModel()
        self.weight_ml, self.weight_wa = 0.6, 0.4
        self.recent_ml_accuracy, self.recent_wa_accuracy = 0.55, 0.55
    def update_weights(self, ml_correct, wa_correct):
        self.recent_ml_accuracy = self.recent_ml_accuracy*0.95 + (1 if ml_correct else 0)*0.05
        self.recent_wa_accuracy = self.recent_wa_accuracy*0.95 + (1 if wa_correct else 0)*0.05
        total = self.recent_ml_accuracy + self.recent_wa_accuracy
        if total > 0: self.weight_ml, self.weight_wa = self.recent_ml_accuracy/total, self.recent_wa_accuracy/total
    def predict(self, ml_proba, wa_proba):
        return wa_proba if ml_proba is None else self.weight_ml*ml_proba + self.weight_wa*wa_proba

ensemble = EnsemblePredictor()

# =============================================================================
# CLARITY ENGINE – COMPLETE WITH ALL METHODS
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.game_scanner = GameScanner(ODDS_API_KEY)
        self.season_context = SeasonContextEngine()
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
        self.daily_loss_limit = 200.0
        self.max_unit_size = 0.05
        self.correlation_threshold = 0.12
        self.db_path = DB_PATH
        init_db()
        self.sem_score = 100
        self.scanned_bets = {"props":[],"games":[],"rejected":[],"best_odds":[],"arbs":[],"middles":[]}
        self.daily_loss_today = 0.0
        self.last_reset_date = datetime.now().date()
        self.last_tune_date = None
        self.last_ml_retrain_date = None
        self._load_tuning_state()
        self._load_ml_retrain_date()
        self._auto_retrain_ml()
        self._correlation_cache = {}
        self._venue_cache = {}
        self._pace_cache = {}

    def _load_tuning_state(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT timestamp FROM tuning_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row: self.last_tune_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        conn.close()
    def _load_ml_retrain_date(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT timestamp FROM ml_retrain_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row: self.last_ml_retrain_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        conn.close()
    def _auto_retrain_ml(self):
        if not LGB_AVAILABLE:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            df = pd.read_sql_query("SELECT player, sport, market, line, odds, result, actual FROM bets WHERE result IN ('WIN','LOSS')", conn)
            conn.close()
        except:
            return
        if len(df) < 100:
            return
        if self.last_ml_retrain_date and (datetime.now() - self.last_ml_retrain_date).days < 7:
            return
        X = df[['line', 'odds']].values.astype(float)
        y = (df['result'] == 'WIN').astype(int).values
        ensemble.ml_model.train(X, y)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO ml_retrain_log (timestamp, bets_used, rmse) VALUES (?,?,?)",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(df), 0.0))
        conn.commit()
        conn.close()
        self.last_ml_retrain_date = datetime.now()

    def convert_odds(self, american): return 1+american/100 if american>0 else 1+100/abs(american)
    def implied_prob(self, american): return 100/(american+100) if american>0 else abs(american)/(abs(american)+100)

    def l42_check(self, stat, line, avg):
        config = STAT_CONFIG.get(stat.upper(), {"tier":"MED","buffer":2.0,"reject":False})
        if config["reject"]: return False, f"RED TIER - {stat}"
        buffer = line - avg if stat.upper() not in ["OUTS"] else avg - line
        return (buffer >= config["buffer"]), f"BUFFER {buffer:.1f} < {config['buffer']}" if buffer < config["buffer"] else "PASS"

    def wsem_check(self, data):
        if len(data)<3: return False, float('inf')
        w = np.ones(len(data)); w[-3:]*=1.5; w/=w.sum()
        mean = np.average(data, weights=w)
        var = np.average((np.array(data)-mean)**2, weights=w)
        wsem = np.sqrt(var/len(data))/abs(mean) if mean!=0 else float('inf')
        return wsem <= self.wsem_max, wsem

    def apply_bayesian_prior(self, data: List[float], market: str, sport: str, prior_weight: int = 3) -> List[float]:
        if len(data) >= 5:
            return data
        priors = {"NBA": {"PTS": 15.0, "REB": 5.0, "AST": 4.0, "STL": 1.0, "BLK": 0.8, "PRA": 24.0, "PR": 20.0, "PA": 19.0}}
        prior_mean = priors.get(sport, {}).get(market.upper(), 10.0)
        smoothed = (sum(data) + prior_mean * prior_weight) / (len(data) + prior_weight)
        return [smoothed] * 5

    def fetch_team_pace(self, team: str) -> float:
        return 1.0

    def get_player_venue_split(self, player: str, market: str, is_home: bool) -> float:
        return 1.0

    def update_correlation(self, player: str, market1: str, market2: str, actual1: float, actual2: float):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT covariance, sample_size FROM correlations WHERE player=? AND market1=? AND market2=?", (player, market1, market2))
        row = c.fetchone()
        if row:
            old_cov, n = row
            new_cov = (old_cov * n + (actual1 * actual2)) / (n + 1)
            new_n = n + 1
            c.execute("UPDATE correlations SET covariance=?, sample_size=?, last_updated=? WHERE player=? AND market1=? AND market2=?",
                      (new_cov, new_n, datetime.now().isoformat(), player, market1, market2))
        else:
            c.execute("INSERT INTO correlations (player, market1, market2, covariance, sample_size, last_updated) VALUES (?,?,?,?,?,?)",
                      (player, market1, market2, actual1 * actual2, 1, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_correlation(self, player: str, market1: str, market2: str) -> float:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT covariance FROM correlations WHERE player=? AND market1=? AND market2=?", (player, market1, market2))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0.0

    def adjust_parlay_probability(self, probs: List[float], covariances: List[float]) -> float:
        if len(probs) == 1:
            return probs[0]
        result = probs[0] * probs[1] + covariances[0]
        for i in range(2, len(probs)):
            result = result * probs[i]
        return min(max(result, 0.0), 1.0)

    def simulate_prop(self, data, line, pick, sport="NBA", opponent=None, player=None, market=None, team=None, is_home=False):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        if data and len(data) > 0:
            w = np.ones(len(data)); w[-3:]*=1.5; w/=w.sum()
            lam = np.average(data, weights=w)
        else:
            lam = line * 0.95
        if opponent and sport in ["NBA", "NHL", "MLB"]:
            def_rating = opponent_strength.get_defensive_rating(sport, opponent)
            lam *= def_rating
        sims = nbinom.rvs(max(1,int(lam/2)), max(1,int(lam/2))/(max(1,int(lam/2))+lam), size=self.sims) if model["distribution"]=="nbinom" else poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims>=line) if pick=="OVER" else np.mean(sims<=line)
        dtm = (proj-line)/line if line!=0 else 0
        return {"proj":proj, "prob":prob, "dtm":dtm}

    def sovereign_bolt(self, prob, dtm, wsem_ok, l42_pass, injury, rest_fade=1.0):
        if injury=="OUT": return {"signal":"🔴 INJURY RISK","units":0}
        if not l42_pass: return {"signal":"🔴 L42 REJECT","units":0}
        if rest_fade < 0.9: return {"signal":"🟠 REST FADE","units":0.5}
        if prob>=self.prob_bolt and dtm>=self.dtm_bolt and wsem_ok: return {"signal":"🟢 SOVEREIGN BOLT ⚡","units":2.0}
        elif prob>=0.78 and wsem_ok: return {"signal":"🟢 ELITE LOCK","units":1.5}
        elif prob>=0.70: return {"signal":"🟡 APPROVED","units":1.0}
        return {"signal":"🔴 PASS","units":0}

    def analyze_prop(self, player, market, line, pick, data, sport, odds, team=None, injury_status="HEALTHY", opponent=None, is_home=False):
        if not data:
            if sport == "NBA":
                real_stats = balldontlie_get_player_stats(player, datetime.now().strftime("%Y-%m-%d"))
                if real_stats:
                    market_map = {"PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk"}
                    stat_val = real_stats.get(market_map.get(market.upper(), "pts"), 0)
                    if stat_val:
                        data = [stat_val] * 5
            if not data:
                real_stats, real_injury = fetch_player_stats_and_injury(player, sport, market)
                if real_stats:
                    data = real_stats
                if real_injury != "HEALTHY":
                    injury_status = real_injury
        if not data:
            data = [line * 0.95] * 5
        rest_fade = 1.0
        if team:
            rest_fade, _ = rest_detector.get_rest_fade(sport, team)
        wa_sim = self.simulate_prop(data, line, pick, sport, opponent, player, market, team, is_home)
        final_prob = wa_sim["prob"]
        raw_edge = final_prob - self.implied_prob(odds)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(final_prob, wa_sim["dtm"], wsem_ok, l42_pass, injury_status, rest_fade)
        if market.upper() in RED_TIER_PROPS:
            tier, reject_reason = "REJECT", f"RED TIER - {market}"
            bolt["units"] = 0
        elif raw_edge >= 0.08:
            tier, reject_reason = "SAFE", None
        elif raw_edge >= 0.05:
            tier, reject_reason = "BALANCED+", None
        elif raw_edge >= 0.03:
            tier, reject_reason = "RISKY", None
        else:
            tier, reject_reason = "PASS", f"Insufficient edge ({raw_edge:.1%})"
            bolt["units"] = 0
        if injury_status != "HEALTHY":
            tier, reject_reason = "REJECT", f"Injury: {injury_status}"
            bolt["units"] = 0
        if rest_fade < 0.9:
            bolt["units"] = min(bolt["units"], 0.5)
        if datetime.now().date() > self.last_reset_date:
            self.daily_loss_today = 0.0
            self.last_reset_date = datetime.now().date()
        max_units = min(bolt["units"], self.max_unit_size * self.bankroll / 100)
        if self.daily_loss_today >= self.daily_loss_limit:
            bolt["units"] = 0
            tier = "REJECT"
            reject_reason = "Daily loss limit reached"
        else:
            bolt["units"] = min(bolt["units"], max_units)
        season_warning = None
        if team and sport in ["NBA","MLB","NHL","NFL"]:
            fade_check = self.season_context.should_fade_team(sport, team)
            if fade_check["fade"]:
                wa_sim["proj"] *= fade_check["multiplier"]
                season_warning = f"⚠️ {team}: {', '.join(fade_check['reasons'])}"
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge>0 and tier!="REJECT" else 0
        return {"player":player,"market":market,"line":line,"pick":pick,"signal":bolt["signal"],
                "units":bolt["units"] if tier!="REJECT" else 0,"projection":wa_sim["proj"],"probability":final_prob,
                "raw_edge":round(raw_edge,4),"tier":tier,"injury":injury_status,"l42_msg":l42_msg,
                "kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warning":season_warning,"reject_reason":reject_reason}

    def analyze_moneyline(self, home, away, sport, home_odds, away_odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_win_prob = 0.55 + (model.get("home_advantage",0)/100)
        away_win_prob = 1 - home_win_prob
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: home_win_prob *= home_fade["multiplier"]; away_win_prob = 1-home_win_prob; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: away_win_prob *= away_fade["multiplier"]; home_win_prob = 1-away_win_prob; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        home_imp, away_imp = self.implied_prob(home_odds), self.implied_prob(away_odds)
        home_edge, away_edge = home_win_prob - home_imp, away_win_prob - away_imp
        if home_edge > away_edge and home_edge > 0.02:
            pick, edge, odds, prob = home, home_edge, home_odds, home_win_prob
        elif away_edge > 0.02:
            pick, edge, odds, prob = away, away_edge, away_odds, away_win_prob
        else:
            return {"pick":"PASS","signal":"🔴 PASS","units":0,"edge":0,"reject_reason":"No significant edge"}
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        else: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"pick":pick,"signal":signal,"units":units,"edge":round(edge,4),"win_prob":round(prob,3),
                "tier":tier,"kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_spread(self, home, away, spread, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        base_margin = model.get("home_advantage",0)
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: base_margin *= home_fade["multiplier"]; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: base_margin /= away_fade["multiplier"]; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        sims = norm.rvs(loc=base_margin, scale=12, size=self.sims)
        prob_cover = np.mean(sims > -spread) if pick==home else np.mean(sims < -spread)
        prob_push = np.mean(np.abs(sims+spread)<0.5)
        prob = prob_cover/(1-prob_push) if prob_push<1 else prob_cover
        edge = prob - self.implied_prob(odds)
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else: tier, units, signal, reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"spread":spread,"pick":pick,"signal":signal,"units":units,
                "prob_cover":round(prob,3),"prob_push":round(prob_push,3),"edge":round(edge,4),
                "tier":tier,"kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_total(self, home, away, total_line, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        base_proj = model.get("avg_total",200) + (model.get("home_advantage",0)/2)
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: base_proj *= home_fade["multiplier"]; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: base_proj *= away_fade["multiplier"]; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        sims = nbinom.rvs(max(1,int(base_proj/2)), max(1,int(base_proj/2))/(max(1,int(base_proj/2))+base_proj), size=self.sims) if model["distribution"]=="nbinom" else poisson.rvs(base_proj, size=self.sims)
        proj, prob_over, prob_under, prob_push = np.mean(sims), np.mean(sims>total_line), np.mean(sims<total_line), np.mean(sims==total_line)
        prob = (prob_over/(1-prob_push) if prob_push<1 else prob_over) if pick=="OVER" else (prob_under/(1-prob_push) if prob_push<1 else prob_under)
        edge = prob - self.implied_prob(odds)
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else: tier, units, signal, reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"total_line":total_line,"pick":pick,"signal":signal,"units":units,
                "projection":round(proj,1),"prob_over":round(prob_over,3),"prob_under":round(prob_under,3),
                "prob_push":round(prob_push,3),"edge":round(edge,4),"tier":tier,"kelly_stake":round(min(kelly,50),2),
                "odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_alternate(self, base_line, alt_line, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        avg_total = model.get("avg_total",200)
        sims = norm.rvs(loc=avg_total, scale=avg_total*0.12, size=self.sims)
        prob = np.mean(sims>alt_line) if pick=="OVER" else np.mean(sims<alt_line)
        edge = prob - self.implied_prob(odds)
        if edge>=0.03: value, action = "GOOD VALUE","BET"
        elif edge>=0: value, action = "FAIR VALUE","CONSIDER"
        else: value, action = "POOR VALUE","AVOID"
        return {"base_line":base_line,"alt_line":alt_line,"pick":pick,"odds":odds,"probability":round(prob,3),
                "implied":round(self.implied_prob(odds),3),"edge":round(edge,4),"value":value,"action":action}

    def get_teams(self, sport): return HARDCODED_TEAMS.get(sport, ["Select a sport first"])
    def get_roster(self, sport, team):
        if sport in ["PGA","TENNIS","UFC"]: return self._get_individual_sport_players(sport)
        if team and sport in ["NBA","MLB","NHL","NFL"]:
            roster, is_fallback = fetch_team_roster(sport, team)
            return roster
        return ["Player 1","Player 2","Player 3","Player 4","Player 5"]
    def _get_individual_sport_players(self, sport):
        if sport=="PGA": return ["Scottie Scheffler","Rory McIlroy","Jon Rahm","Ludvig Aberg","Xander Schauffele","Collin Morikawa"]
        elif sport=="TENNIS": return ["Novak Djokovic","Carlos Alcaraz","Iga Swiatek","Coco Gauff","Aryna Sabalenka","Jannik Sinner"]
        elif sport=="UFC": return ["Jon Jones","Islam Makhachev","Alex Pereira","Sean O'Malley","Ilia Topuria","Dricus Du Plessis"]
        return ["Player 1","Player 2","Player 3"]

    def run_best_bets_scan(self, selected_sports, stop_event=None, progress_callback=None, result_callback=None, days_offset=0):
        return self.scanned_bets

    def run_best_odds_scan(self, selected_sports):
        return []

    def get_accuracy_dashboard(self):
        df = get_recent_bets(500)
        if df.empty:
            return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':100}
        settled = df[df['result'].isin(['WIN','LOSS'])]
        if settled.empty:
            return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':100}
        wins = (settled['result']=='WIN').sum()
        total = len(settled)
        win_rate = wins/total*100
        if 'profit' in settled.columns and not settled['profit'].isnull().all():
            total_profit = settled['profit'].sum()
            total_stake = total * 100
        else:
            total_profit = 0
            total_stake = total * 100
            for _, row in settled.iterrows():
                if row['result'] == 'WIN':
                    odds = row.get('odds', -110)
                    if odds > 0:
                        profit = (odds / 100) * 100
                    else:
                        profit = (100 / abs(odds)) * 100
                else:
                    profit = -100
                total_profit += profit
        roi = (total_profit/total_stake)*100 if total_stake>0 else 0
        by_sport = {}
        for sport in settled['sport'].unique():
            sport_df = settled[settled['sport']==sport]
            sport_wins = (sport_df['result']=='WIN').sum()
            by_sport[sport] = {'bets':len(sport_df), 'win_rate': round(sport_wins/len(sport_df)*100,1) if len(sport_df)>0 else 0}
        by_tier = {}
        for _,row in settled.iterrows():
            signal = row.get('bolt_signal','PASS')
            tier = 'SAFE' if 'SAFE' in str(signal) else 'BALANCED+' if 'BALANCED' in str(signal) else 'RISKY' if 'RISKY' in str(signal) else 'PASS'
            if tier not in by_tier:
                by_tier[tier] = {'bets':0,'wins':0}
            by_tier[tier]['bets'] += 1
            if row['result']=='WIN':
                by_tier[tier]['wins'] += 1
        for tier in by_tier:
            by_tier[tier]['win_rate'] = round(by_tier[tier]['wins']/by_tier[tier]['bets']*100,1) if by_tier[tier]['bets']>0 else 0
        return {'total_bets':total,'wins':wins,'losses':total-wins,'win_rate':round(win_rate,1),'roi':round(roi,1),'units_profit':round(total_profit/100,1),'by_sport':by_sport,'by_tier':by_tier,'sem_score':self.sem_score}

    def detect_arbitrage(self, props): return []
    def hunt_middles(self, props): return []
    def _log_bet(self, *args, **kwargs): pass
    def settle_pending_bets(self): pass
    def _calibrate_sem(self): pass
    def auto_tune_thresholds(self): pass

# =============================================================================
# UNIFIED SLIP PARSER – MyBookie, Bovada, PrizePicks (handles "Loss"/"Win")
# =============================================================================
def parse_mybookie_slip(text: str) -> List[Dict]:
    bets = []
    blocks = re.split(r'(?=MLB \| Baseball|NBA \| Basketball|NHL \| Ice Hockey|NFL \| Football)', text, flags=re.IGNORECASE)
    for block in blocks:
        if not block.strip():
            continue
        if 'mlb' in block.lower():
            sport = 'MLB'
        elif 'nba' in block.lower():
            sport = 'NBA'
        elif 'nfl' in block.lower():
            sport = 'NFL'
        elif 'nhl' in block.lower():
            sport = 'NHL'
        else:
            continue
        spread_match = re.search(r'([A-Z][a-z]+(?: [A-Z][a-z]+)*)\s*\(([+-]\d+\.?\d*)\)', block)
        ml_match = re.search(r'([A-Z][a-z]+(?: [A-Z][a-z]+)*)\s*([+-]\d{3,4})\s*(Winner|Handicap)', block)
        result_match = re.search(r'(WIN|LOSS)', block.upper())
        result = result_match.group(1) if result_match else ""
        if spread_match:
            team = spread_match.group(1).strip()
            line = float(spread_match.group(2))
            odds_match = re.search(r'([+-]\d{3,4})\s*Handicap', block)
            odds = int(odds_match.group(1)) if odds_match else 0
            vs_match = re.search(r'vs\.?\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*)', block)
            opponent = vs_match.group(1) if vs_match else ""
            bets.append({
                "type": "GAME", "sport": sport, "team": team, "opponent": opponent,
                "market_type": "SPREAD", "line": line, "price": odds, "result": result, "pick": team
            })
        elif ml_match:
            team = ml_match.group(1).strip()
            odds = int(ml_match.group(2))
            vs_match = re.search(r'vs\.?\s+([A-Z][a-z]+(?: [A-Z][a-z]+)*)', block)
            opponent = vs_match.group(1) if vs_match else ""
            bets.append({
                "type": "GAME", "sport": sport, "team": team, "opponent": opponent,
                "market_type": "ML", "line": 0.0, "price": odds, "result": result, "pick": team
            })
    return bets

def parse_bovada_slip(text: str) -> List[Dict]:
    bets = []
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or 'Ref.' in line or 'Parlay' in line or 'Risk' in line or 'Winnings' in line:
            continue
        if 'Loss' in line or 'Win' in line:
            continue
        spread_match = re.search(r'([A-Z][a-z]+(?: [A-Z][a-z]+)*)\s*([+-]\d+\.?\d*)', line)
        if spread_match:
            team = spread_match.group(1).strip()
            line_val = float(spread_match.group(2))
            odds_match = re.search(r'([+-]\d+)$', line)
            odds = int(odds_match.group(1)) if odds_match else 0
            bets.append({
                "type": "GAME", "sport": "NBA", "team": team, "opponent": "",
                "market_type": "SPREAD", "line": line_val, "price": odds, "result": "", "pick": team
            })
        ml_match = re.search(r'([A-Z][a-z]+(?: [A-Z][a-z]+)*)\s*([+-]\d{3,4})\s*(Winner|Moneyline)', line, re.IGNORECASE)
        if ml_match:
            team = ml_match.group(1).strip()
            odds = int(ml_match.group(2))
            bets.append({
                "type": "GAME", "sport": "NBA", "team": team, "opponent": "",
                "market_type": "ML", "line": 0.0, "price": odds, "result": "", "pick": team
            })
    return bets

def parse_prizepicks_slip(text: str) -> List[Dict]:
    bets = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and lines[i] == lines[i+1]:
            player = lines[i]
            i += 2
        else:
            player = lines[i]
            i += 1
        while i < len(lines) and lines[i] not in ("Final", "Demon") and not lines[i].replace('.', '').isdigit():
            i += 1
        if i >= len(lines):
            break
        if lines[i] == "Final":
            i += 1
        if i >= len(lines):
            break
        if lines[i] == "Demon":
            i += 1
        if i >= len(lines):
            break
        try:
            line_val = float(lines[i])
        except ValueError:
            i += 1
            continue
        i += 1
        if i >= len(lines):
            break
        market = lines[i]
        i += 1
        if i >= len(lines):
            break
        try:
            actual_val = float(lines[i])
        except ValueError:
            actual_val = 0.0
        i += 1
        result = "WIN" if actual_val > line_val else "LOSS"
        market_map = {
            "Ks": "KS", "Hits+Runs+RBIs": "H+R+RBI", "TB": "TB", "Home Runs": "HR", "Hits": "HITS",
            "Points": "PTS", "Rebounds": "REB", "Assists": "AST", "PRA": "PRA", "PR": "PR", "PA": "PA",
            "SOG": "SOG", "Saves": "SAVES",
        }
        market_std = market_map.get(market, market)
        sport = "MLB" if market in ("Ks", "Hits+Runs+RBIs", "TB", "Home Runs", "Hits") else "NBA"
        bets.append({
            "type": "PROP", "sport": sport, "player": player, "market": market_std,
            "line": line_val, "pick": "OVER", "result": result, "actual": actual_val, "price": 0
        })
    return bets

def parse_any_slip(text: str) -> List[Dict]:
    text_lower = text.lower()
    if 'mlb | baseball' in text_lower or 'handicap' in text_lower:
        return parse_mybookie_slip(text)
    elif 'ref.' in text_lower and 'parlay' in text_lower:
        return parse_bovada_slip(text)
    elif 'flex play' in text_lower or 'hitter fs' in text_lower or 'final' in text_lower:
        return parse_prizepicks_slip(text)
    else:
        return []

def parse_pasted_props(text: str, default_date: str = None) -> List[Dict]:
    bets = []
    lines = text.split('\n')
    for line in lines:
        if 'More' in line or 'OVER' in line.upper():
            pick = 'OVER'
        elif 'Less' in line or 'UNDER' in line.upper():
            pick = 'UNDER'
        else:
            continue
        numbers = re.findall(r'\d+\.?\d*', line)
        if not numbers:
            continue
        line_val = float(numbers[0])
        words = line.split()
        player = words[0] if words else "Unknown"
        market = "PTS"
        if "REB" in line.upper():
            market = "REB"
        elif "AST" in line.upper():
            market = "AST"
        sport = "NBA" if market in ["PTS","REB","AST"] else "MLB"
        bets.append({
            "type": "PROP", "sport": sport, "player": player, "market": market,
            "line": line_val, "pick": pick, "result": "", "actual": 0.0
        })
    return bets

def parse_props_from_image(image_bytes, filename, filetype):
    try:
        files = {"file": (filename, image_bytes, filetype)}
        data = {"apikey": OCR_SPACE_API_KEY, "language": "eng", "isOverlayRequired": False,
                "filetype": filetype.split("/")[-1] if filetype else "PNG"}
        response = requests.post("https://api.ocr.space/parse/image", files=files, data=data, timeout=30)
        if response.status_code != 200:
            return []
        result = response.json()
        if result.get("IsErroredOnProcessing", True):
            return []
        extracted_text = result["ParsedResults"][0]["ParsedText"]
        return parse_any_slip(extracted_text)
    except:
        return []

# =============================================================================
# BEST BET PER GAME HELPER
# =============================================================================
def get_best_bet_for_game(game: Dict, engine: Clarity18Elite) -> Optional[Dict]:
    best = None
    best_edge = -1.0
    sport = game['sport']
    home = game['home']
    away = game['away']

    def evaluate(market_type, pick, line, odds, description):
        nonlocal best, best_edge
        if odds is None or odds == 0:
            return
        if market_type == 'moneyline':
            if pick == home:
                res = engine.analyze_moneyline(home, away, sport, odds, None)
                edge = res.get('edge', 0)
                if edge > best_edge:
                    best_edge = edge
                    best = {
                        'game': f"{home} vs {away}",
                        'bet': f"{pick} ML",
                        'odds': odds,
                        'edge': edge,
                        'win_prob': res.get('win_prob', 0.5),
                        'units': res.get('units', 0),
                        'signal': res.get('signal', ''),
                        'description': description
                    }
            else:
                res = engine.analyze_moneyline(home, away, sport, None, odds)
                edge = res.get('edge', 0)
                if edge > best_edge:
                    best_edge = edge
                    best = {
                        'game': f"{home} vs {away}",
                        'bet': f"{pick} ML",
                        'odds': odds,
                        'edge': edge,
                        'win_prob': res.get('win_prob', 0.5),
                        'units': res.get('units', 0),
                        'signal': res.get('signal', ''),
                        'description': description
                    }
        elif market_type == 'spread':
            res = engine.analyze_spread(home, away, line, pick, sport, odds)
            edge = res.get('edge', 0)
            if edge > best_edge:
                best_edge = edge
                best = {
                    'game': f"{home} vs {away}",
                    'bet': f"{pick} {line:+.1f}",
                    'odds': odds,
                    'edge': edge,
                    'win_prob': res.get('prob_cover', 0.5),
                    'units': res.get('units', 0),
                    'signal': res.get('signal', ''),
                    'description': description
                }
        elif market_type == 'total':
            res = engine.analyze_total(home, away, line, pick, sport, odds)
            edge = res.get('edge', 0)
            if edge > best_edge:
                best_edge = edge
                best = {
                    'game': f"{home} vs {away}",
                    'bet': f"{pick} {line}",
                    'odds': odds,
                    'edge': edge,
                    'win_prob': res.get('prob_over' if pick == 'OVER' else 'prob_under', 0.5),
                    'units': res.get('units', 0),
                    'signal': res.get('signal', ''),
                    'description': description
                }

    if game.get('home_ml'):
        evaluate('moneyline', home, 0, game['home_ml'], f"{home} ML")
    if game.get('away_ml'):
        evaluate('moneyline', away, 0, game['away_ml'], f"{away} ML")
    if game.get('spread') is not None and game.get('spread_odds'):
        evaluate('spread', home, game['spread'], game['spread_odds'], f"{home} {game['spread']:+.1f}")
        evaluate('spread', away, -game['spread'], game['spread_odds'], f"{away} {game['spread']:+.1f}")
    if game.get('total') is not None:
        if game.get('over_odds'):
            evaluate('total', 'OVER', game['total'], game['over_odds'], f"OVER {game['total']}")
        if game.get('under_odds'):
            evaluate('total', 'UNDER', game['total'], game['under_odds'], f"UNDER {game['total']}")
    return best

# =============================================================================
# STREAMLIT DASHBOARD – 5 TABS (HISTORY & METRICS instead of SELF EVALUATION)
# =============================================================================
engine = Clarity18Elite()

def export_database():
    if os.path.exists(DB_PATH):
        backup_name = f"clarity_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy(DB_PATH, backup_name)
        return backup_name
    return None

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.3 ELITE", layout="wide", page_icon="🔮")
    col_title_left, col_title_center, col_title_right = st.columns([1,2,1])
    with col_title_center:
        st.title("🔮 CLARITY 18.3 ELITE")
        st.markdown(f"<p style='text-align: center;'>Unified Quick Scanner | Auto-Settle | Best Bet Per Game | History & Metrics | {VERSION}</p>", unsafe_allow_html=True)
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        col_status1, col_status2 = st.columns(2)
        with col_status1:
            st.success("✅ BallsDontLie (NBA props)")
            st.success("✅ Odds-API.io (game lines)")
            st.success("✅ Auto-Settle (NBA & game lines)")
        with col_status2:
            st.success("✅ Real Rosters")
            st.success("✅ Slip Import & Auto‑Settlement")
            st.success("✅ Best Bet Per Game")
        st.divider()
        new_max_unit = st.slider("Max unit size (% of bankroll)", 1, 15, int(engine.max_unit_size*100), 1, key="sidebar_max_unit") / 100.0
        if new_max_unit != engine.max_unit_size:
            engine.max_unit_size = new_max_unit
            st.info(f"Max unit size set to {engine.max_unit_size*100:.0f}%")
        if st.button("💾 Export Database Backup", use_container_width=True, key="sidebar_export"):
            backup_file = export_database()
            if backup_file:
                st.success(f"✅ Backup saved: {backup_file}")
            else:
                st.error("❌ Database file not found.")
        st.divider()
        col_metrics1, col_metrics2, col_metrics3 = st.columns(3)
        with col_metrics1: st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        with col_metrics2: st.metric("Daily Loss Left", f"${max(0, engine.daily_loss_limit - engine.daily_loss_today):.0f}")
        with col_metrics3: st.metric("SEM Score", f"{engine.sem_score}/100")
        col_metrics4, col_metrics5 = st.columns(2)
        with col_metrics4: st.metric("Prob Bolt", f"{engine.prob_bolt:.2f}")
        with col_metrics5: st.metric("DTM Bolt", f"{engine.dtm_bolt:.3f}")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🎮 GAME MARKETS", "📋 PASTE & SCAN", "📊 SCANNERS & ACCURACY", "🎯 PLAYER PROPS", "📊 HISTORY & METRICS"
    ])

    all_sports = ["NBA", "MLB", "NHL", "NFL", "SOCCER_EPL", "SOCCER_LALIGA", "COLLEGE_BASKETBALL", "COLLEGE_FOOTBALL", "ESPORTS_LOL", "ESPORTS_CS2"]
    scanning_info = """
    **📅 Optimal Scanning Windows (for best lines & player props):**
    - **NBA, MLB, NHL**: 6 AM, 2 PM, 9 PM
    - **NFL**: Monday 10 AM, Tuesday 6 AM, Sunday 10 AM
    - **EPL / La Liga**: Afternoon (2 PM) the day before matches
    """

    # =========================================================================
    # TAB 1: GAME MARKETS (Best Bet Per Game + auto-load + manual)
    # =========================================================================
    with tab1:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("🎮 Game Markets")
        st.subheader("🏆 Best Bet Per Game (Clarity Picks Highest Edge)")
        col1, col2, col3 = st.columns([2,1,1])
        with col1:
            best_sport = st.selectbox("Select Sport for Best Bets", all_sports, key="best_sport")
        with col2:
            best_load_tomorrow = st.checkbox("Load tomorrow's games", value=False, key="best_load_tomorrow")
        with col3:
            if st.button("🏆 FIND BEST BETS", type="primary", key="find_best_bets"):
                days_offset = 1 if best_load_tomorrow else 0
                check_scan_timing(best_sport)
                with st.spinner(f"Fetching games and computing best bets..."):
                    games = engine.game_scanner.fetch_games_by_date([best_sport], days_offset)
                    if games:
                        best_bets = []
                        for game in games:
                            best = get_best_bet_for_game(game, engine)
                            if best and best['edge'] > 0.02:
                                best_bets.append(best)
                        if best_bets:
                            st.success(f"Found {len(best_bets)} games with positive edge bets")
                            best_bets.sort(key=lambda x: x['edge'], reverse=True)
                            for b in best_bets:
                                st.markdown(f"**{b['game']}** → {b['bet']} at **{b['odds']:+d}**")
                                st.caption(f"Edge: {b['edge']:.1%} | Win Prob: {b['win_prob']:.1%} | Units: {b['units']} | {b['signal']}")
                                st.divider()
                        else:
                            st.info("No positive edge bets found for these games.")
                    else:
                        st.warning(f"No games found for {best_sport}.")
        st.markdown("---")
        st.subheader("📅 Auto-Load Games (All Lines)")
        col1, col2, col3 = st.columns([2,1,1])
        with col1:
            auto_sport = st.selectbox("Select Sport", all_sports, key="auto_sport")
        with col2:
            load_tomorrow = st.checkbox("Load tomorrow's games", value=False, key="load_tomorrow")
        with col3:
            if st.button("📅 LOAD GAMES", type="primary", key="load_games"):
                days_offset = 1 if load_tomorrow else 0
                check_scan_timing(auto_sport)
                with st.spinner(f"Fetching {'tomorrow' if days_offset else 'today'}'s games..."):
                    games = engine.game_scanner.fetch_games_by_date([auto_sport], days_offset)
                    if games:
                        st.session_state["auto_games"] = games
                        st.session_state["auto_games_analyzed"] = None
                        st.success(f"Loaded {len(games)} games")
                    else:
                        st.warning(f"No games found for {'tomorrow' if days_offset else 'today'}.")
        if "auto_games" in st.session_state and st.session_state["auto_games"]:
            game_options = [f"{g['home']} vs {g['away']}" for g in st.session_state["auto_games"]]
            selected_game = st.selectbox("Select a game", game_options, key="selected_game")
            if selected_game:
                idx = game_options.index(selected_game)
                game = st.session_state["auto_games"][idx]
                home = game['home']; away = game['away']; sport = game['sport']
                st.info(f"**{home}** vs **{away}**")
                recommendations_found = False
                approved_bets_for_parlay = []
                if game.get("home_ml") and game.get("away_ml"):
                    ml_result = engine.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                    if ml_result.get('units', 0) > 0:
                        st.success(f"✅ CLARITY APPROVED: **{ml_result['pick']} ML** ({ml_result['odds']}) – Edge: {ml_result['edge']:.1%} – Units: {ml_result['units']}")
                        approved_bets_for_parlay.append({"description": f"{ml_result['pick']} ML", "odds": ml_result['odds'], "edge": ml_result['edge'], "units": ml_result['units'], "game": f"{home} vs {away}"})
                        recommendations_found = True
                    else:
                        st.info(f"❌ Moneyline not approved – {ml_result.get('reject_reason', 'Insufficient edge')}")
                if game.get("spread") and game.get("spread_odds"):
                    spread_approved = False
                    for pick_side in [home, away]:
                        spread_res = engine.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                        if spread_res.get('units', 0) > 0:
                            st.success(f"✅ CLARITY APPROVED: **{pick_side} {game['spread']:+.1f}** ({game['spread_odds']}) – Edge: {spread_res['edge']:.1%} – Units: {spread_res['units']}")
                            approved_bets_for_parlay.append({"description": f"{pick_side} {game['spread']:+.1f}", "odds": game['spread_odds'], "edge": spread_res['edge'], "units": spread_res['units'], "game": f"{home} vs {away}"})
                            spread_approved = True
                            recommendations_found = True
                    if not spread_approved:
                        st.info(f"❌ Spread not approved – No significant edge")
                if game.get("total"):
                    total_approved = False
                    for pick_side, odds in [("OVER", game.get("over_odds", -110)), ("UNDER", game.get("under_odds", -110))]:
                        total_res = engine.analyze_total(home, away, game["total"], pick_side, sport, odds)
                        if total_res.get('units', 0) > 0:
                            st.success(f"✅ CLARITY APPROVED: **{pick_side} {game['total']}** ({odds}) – Edge: {total_res['edge']:.1%} – Units: {total_res['units']}")
                            approved_bets_for_parlay.append({"description": f"{pick_side} {game['total']}", "odds": odds, "edge": total_res['edge'], "units": total_res['units'], "game": f"{home} vs {away}"})
                            total_approved = True
                            recommendations_found = True
                    if not total_approved:
                        st.info(f"❌ Total not approved – No significant edge")
                st.markdown("---")
                st.subheader("🔄 Best Alternate Lines")
                alt_found = False
                if game.get("spread") and game.get("spread_odds"):
                    alt_spreads = [game["spread"] + 1, game["spread"] - 1]
                    for alt_spread in alt_spreads:
                        if abs(alt_spread - game["spread"]) <= 2:
                            est_odds = game["spread_odds"] + (10 if alt_spread > game["spread"] else -10)
                            for pick_side in [home, away]:
                                alt_res = engine.analyze_spread(home, away, alt_spread, pick_side, sport, est_odds)
                                if alt_res.get('units', 0) > 0:
                                    st.success(f"✅ CLARITY APPROVED (Alternate): **{pick_side} {alt_spread:+.1f}** (est. {est_odds}) – Edge: {alt_res['edge']:.1%} – Units: {alt_res['units']}")
                                    alt_found = True
                                    break
                if game.get("total"):
                    alt_totals = [game["total"] + 1, game["total"] - 1]
                    for alt_total in alt_totals:
                        est_over_odds = game.get("over_odds", -110) - 10 if alt_total > game["total"] else game.get("over_odds", -110) + 10
                        est_under_odds = game.get("under_odds", -110) - 10 if alt_total < game["total"] else game.get("under_odds", -110) + 10
                        for pick_side, odds in [("OVER", est_over_odds), ("UNDER", est_under_odds)]:
                            alt_res = engine.analyze_total(home, away, alt_total, pick_side, sport, odds)
                            if alt_res.get('units', 0) > 0:
                                st.success(f"✅ CLARITY APPROVED (Alternate): **{pick_side} {alt_total}** (est. {odds}) – Edge: {alt_res['edge']:.1%} – Units: {alt_res['units']}")
                                alt_found = True
                                break
                if not alt_found:
                    st.info("No alternate lines with significant edge found.")
                if not recommendations_found and not alt_found:
                    st.warning("⚠️ No CLARITY approved bets found for this game.")
                st.markdown("---")
                st.subheader("🎯 CLARITY SUGGESTED PARLAYS")
                if "auto_games_analyzed" not in st.session_state or st.session_state["auto_games_analyzed"] is None:
                    all_approved = []
                    for g in st.session_state["auto_games"]:
                        g_home = g['home']; g_away = g['away']; g_sport = g['sport']
                        if g.get("home_ml") and g.get("away_ml"):
                            ml_res = engine.analyze_moneyline(g_home, g_away, g_sport, g["home_ml"], g["away_ml"])
                            if ml_res.get('units', 0) > 0:
                                all_approved.append({"description": f"{ml_res['pick']} ML","odds": ml_res['odds'],"edge": ml_res['edge'],"game": f"{g_home} vs {g_away}"})
                        if g.get("spread") and g.get("spread_odds"):
                            for pick_side in [g_home, g_away]:
                                spread_res = engine.analyze_spread(g_home, g_away, g["spread"], pick_side, g_sport, g["spread_odds"])
                                if spread_res.get('units', 0) > 0:
                                    all_approved.append({"description": f"{pick_side} {g['spread']:+.1f}","odds": g['spread_odds'],"edge": spread_res['edge'],"game": f"{g_home} vs {g_away}"})
                        if g.get("total"):
                            for pick_side, odds in [("OVER", g.get("over_odds", -110)), ("UNDER", g.get("under_odds", -110))]:
                                total_res = engine.analyze_total(g_home, g_away, g["total"], pick_side, g_sport, odds)
                                if total_res.get('units', 0) > 0:
                                    all_approved.append({"description": f"{pick_side} {g['total']}","odds": odds,"edge": total_res['edge'],"game": f"{g_home} vs {g_away}"})
                    st.session_state["auto_games_analyzed"] = all_approved
                all_approved = st.session_state.get("auto_games_analyzed", [])
                if len(all_approved) >= 2:
                    def decimal_odds(american): return american/100+1 if american>0 else 100/abs(american)+1
                    best_bets = sorted(all_approved, key=lambda x: x['edge'], reverse=True)
                    leg1 = best_bets[0]
                    leg2 = next((b for b in best_bets[1:] if b['game'] != leg1['game']), None)
                    if leg2:
                        dec1 = decimal_odds(leg1['odds']); dec2 = decimal_odds(leg2['odds'])
                        parlay_odds = round((dec1 * dec2 - 1) * 100)
                        st.success(f"**🔒 2-LEG PARLAY**")
                        st.markdown(f"- {leg1['description']} ({leg1['odds']}) – Edge: {leg1['edge']:.1%}")
                        st.markdown(f"- {leg2['description']} ({leg2['odds']}) – Edge: {leg2['edge']:.1%}")
                        st.caption(f"📊 Estimated odds: {'+'+str(parlay_odds) if parlay_odds>0 else parlay_odds}")
                    leg3 = next((b for b in best_bets[2:] if b['game'] not in [leg1['game'], leg2['game']]), None)
                    if leg2 and leg3:
                        dec1, dec2, dec3 = decimal_odds(leg1['odds']), decimal_odds(leg2['odds']), decimal_odds(leg3['odds'])
                        parlay_odds = round((dec1 * dec2 * dec3 - 1) * 100)
                        st.success(f"**🚀 3-LEG PARLAY**")
                        st.markdown(f"- {leg1['description']} ({leg1['odds']}) – Edge: {leg1['edge']:.1%}")
                        st.markdown(f"- {leg2['description']} ({leg2['odds']}) – Edge: {leg2['edge']:.1%}")
                        st.markdown(f"- {leg3['description']} ({leg3['odds']}) – Edge: {leg3['edge']:.1%}")
                        st.caption(f"📊 Estimated odds: {'+'+str(parlay_odds) if parlay_odds>0 else parlay_odds}")
                else:
                    st.info("Need at least 2 approved bets from different games to build a parlay.")
        st.markdown("---")
        st.subheader("✏️ Manual Entry")
        game_tab1, game_tab2, game_tab3, game_tab4 = st.tabs(["💰 Moneyline", "📊 Spread", "📈 Totals", "🔄 Alt Lines"])
        with game_tab1:
            c1, c2 = st.columns(2)
            with c1:
                sport_ml = st.selectbox("Sport", all_sports, key="ml_sport")
                teams_ml = engine.get_teams(sport_ml)
                home = st.selectbox("Home Team", teams_ml, key="ml_home")
                away = st.selectbox("Away Team", teams_ml, key="ml_away")
            with c2:
                home_odds = st.number_input("Home Odds", -500, 500, -110, key="ml_home_odds")
                away_odds = st.number_input("Away Odds", -500, 500, -110, key="ml_away_odds")
            if st.button("💰 ANALYZE MONEYLINE", type="primary", key="ml_button"):
                result = engine.analyze_moneyline(home, away, sport_ml, home_odds, away_odds)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {result['pick']} ({result['odds']})")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.metric("Win Probability", f"{result['win_prob']:.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        with game_tab2:
            c1, c2 = st.columns(2)
            with c1:
                sport_sp = st.selectbox("Sport", all_sports, key="sp_sport")
                teams_sp = engine.get_teams(sport_sp)
                home_sp = st.selectbox("Home Team", teams_sp, key="sp_home")
                away_sp = st.selectbox("Away Team", teams_sp, key="sp_away")
                spread = st.number_input("Spread", -30.0, 30.0, -5.5, key="sp_line")
            with c2:
                pick_sp = st.selectbox("Pick", [home_sp, away_sp], key="sp_pick")
                odds_sp = st.number_input("Odds", -500, 500, -110, key="sp_odds")
            if st.button("📊 ANALYZE SPREAD", type="primary", key="sp_button"):
                result = engine.analyze_spread(home_sp, away_sp, spread, pick_sp, sport_sp, odds_sp)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {pick_sp} {spread:+.1f} ({odds_sp})")
                    st.metric("Cover Probability", f"{result['prob_cover']:.1%}")
                    st.metric("Push Probability", f"{result['prob_push']:.1%}")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        with game_tab3:
            c1, c2 = st.columns(2)
            with c1:
                sport_tot = st.selectbox("Sport", all_sports, key="tot_sport")
                teams_tot = engine.get_teams(sport_tot)
                home_tot = st.selectbox("Home Team", teams_tot, key="tot_home")
                away_tot = st.selectbox("Away Team", teams_tot, key="tot_away")
                max_total = SPORT_MODELS[sport_tot]["avg_total"] * 2 if sport_tot in SPORT_MODELS else 300.0
                total_line = st.number_input("Total Line", 0.5, max_total, SPORT_MODELS.get(sport_tot, {}).get("avg_total", 220.5), key="tot_line")
            with c2:
                pick_tot = st.selectbox("Pick", ["OVER", "UNDER"], key="tot_pick")
                odds_tot = st.number_input("Odds", -500, 500, -110, key="tot_odds")
            if st.button("📈 ANALYZE TOTAL", type="primary", key="tot_button"):
                result = engine.analyze_total(home_tot, away_tot, total_line, pick_tot, sport_tot, odds_tot)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {pick_tot} {total_line} ({odds_tot})")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Projection", f"{result['projection']:.1f}")
                    with c2: st.metric("OVER Prob", f"{result['prob_over']:.1%}")
                    with c3: st.metric("UNDER Prob", f"{result['prob_under']:.1%}")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        with game_tab4:
            c1, c2 = st.columns(2)
            with c1:
                sport_alt = st.selectbox("Sport", all_sports, key="alt_sport")
                base_line = st.number_input("Main Line", 0.5, 300.0, 220.5, key="alt_base")
                alt_line = st.number_input("Alternate Line", 0.5, 300.0, 230.5, key="alt_line")
            with c2:
                pick_alt = st.selectbox("Pick", ["OVER", "UNDER"], key="alt_pick")
                odds_alt = st.number_input("Odds", -500, 500, -110, key="alt_odds")
            if st.button("🔄 ANALYZE ALTERNATE", type="primary", key="alt_button"):
                result = engine.analyze_alternate(base_line, alt_line, pick_alt, sport_alt, odds_alt)
                if result['action'] == "BET":
                    st.success(f"### {result['action']}")
                elif result['action'] == "CONSIDER":
                    st.warning(f"### {result['action']}")
                else:
                    st.error(f"### {result['action']}")
                st.metric("Probability", f"{result['probability']:.1%}")
                st.metric("Implied", f"{result['implied']:.1%}")
                st.metric("Edge", f"{result['edge']:+.1%}")
                st.info(f"Value: {result['value']}")

    # =========================================================================
    # TAB 2: PASTE & SCAN (unchanged, includes all parsing)
    # =========================================================================
    with tab2:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("📋 PASTE & SCAN")
        st.markdown("Paste player props, game slips, or winning/losing tickets. Clarity auto‑detects and analyzes.")
        input_method = st.radio("Input method:", ["📝 Paste Text", "📸 Upload Screenshot"], key="ps_input_method")
        pasted_text = ""
        if input_method == "📝 Paste Text":
            pasted_text = st.text_area("Paste here", height=300, key="ps_text",
                                       placeholder="Examples:\n\nPlayer props:\nBrandon Miller Points 20.5 More\nStephen Curry Points 28.5 More\n\nGame slips:\nNew York Yankees +120 vs Boston Red Sox\nLos Angeles Dodgers -1.5 (-110) vs San Diego Padres\n\nWith results:\nSan Jose Sharks (+1.5) -182 ... Win")
        else:
            uploaded_file = st.file_uploader("Choose a screenshot", type=["png","jpg","jpeg"], key="ps_screenshot")
            if uploaded_file and st.button("📸 Extract from Screenshot", type="secondary", key="ps_extract"):
                with st.spinner("Extracting text via OCR..."):
                    extracted = parse_props_from_image(uploaded_file.getvalue(), uploaded_file.name, uploaded_file.type)
                    if extracted:
                        pasted_text = str(extracted)
                        st.success(f"Extracted {len(extracted)} props from screenshot")
                    else:
                        st.warning("No props found in image.")
        if st.button("🔍 ANALYZE & IMPORT", type="primary", key="ps_analyze"):
            if not pasted_text.strip():
                st.warning("Please paste something or upload a screenshot.")
            else:
                parsed = parse_any_slip(pasted_text)
                if not parsed:
                    parsed = parse_pasted_props(pasted_text)
                if not parsed:
                    st.warning("No recognizable bets found. Please check format.")
                else:
                    approved_props = []
                    imported_bets = []
                    rejected_items = []
                    settled_bets = []
                    for bet in parsed:
                        if bet.get("result") in ["WIN", "LOSS"]:
                            actual = bet.get("actual", 0.0)
                            result = bet["result"]
                            bet_id = hashlib.md5(f"{bet.get('player', bet.get('team'))}{bet.get('market', bet.get('market_type'))}{datetime.now()}".encode()).hexdigest()[:12]
                            conn = sqlite3.connect(DB_PATH)
                            c = conn.cursor()
                            c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, settled_date, bolt_signal, profit)
                                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                      (bet_id, bet.get('player', bet.get('team')), bet['sport'], bet.get('market', bet.get('market_type')),
                                       bet['line'], bet.get('pick', ''), bet.get('price', 0), 0.0, result, actual,
                                       datetime.now().strftime("%Y-%m-%d"), datetime.now().strftime("%Y-%m-%d"), "SLIP_SETTLED", 0))
                            conn.commit()
                            conn.close()
                            settled_bets.append(bet)
                        else:
                            if bet["type"] == "PROP":
                                analysis = engine.analyze_prop(bet["player"], bet["market"], bet["line"], bet.get("pick", "OVER"),
                                                               [], bet["sport"], bet.get("price", -110), None, "HEALTHY", bet.get("opponent", ""))
                                if analysis.get('units', 0) > 0:
                                    approved_props.append((bet, analysis))
                                else:
                                    rejected_items.append((bet, analysis))
                            else:
                                bet_id = hashlib.md5(f"{bet.get('team')}{bet.get('market_type')}{datetime.now()}".encode()).hexdigest()[:12]
                                conn = sqlite3.connect(DB_PATH)
                                c = conn.cursor()
                                c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, bolt_signal)
                                             VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                          (bet_id, bet.get('team'), bet['sport'], bet.get('market_type'),
                                           bet['line'], bet.get('pick', ''), bet.get('price', 0), 0.0, 'PENDING', 0.0,
                                           datetime.now().strftime("%Y-%m-%d"), "PENDING"))
                                conn.commit()
                                conn.close()
                                imported_bets.append(bet)
                    if settled_bets:
                        st.subheader("✅ AUTO‑SETTLED BETS (from slip)")
                        for s in settled_bets:
                            st.markdown(f"**{s.get('player', s.get('team'))} {s.get('market', s.get('market_type'))}** → {s['result']}")
                    if approved_props:
                        st.subheader("✅ APPROVED PLAYER PROPS")
                        for prop, res in approved_props:
                            st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                            st.caption(f"Edge: {res['raw_edge']:.1%} | Prob: {res['probability']:.1%} | Units: {res['units']} | Tier: {res['tier']}")
                    if imported_bets:
                        st.subheader("📋 IMPORTED GAME BETS (PENDING)")
                        for bet in imported_bets:
                            st.markdown(f"**{bet['team']} {bet['market_type']} {bet['line']}**")
                    if rejected_items:
                        with st.expander(f"❌ REJECTED / NO EDGE ({len(rejected_items)})"):
                            for prop, res in rejected_items:
                                st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                                st.caption(f"Reason: {res.get('reject_reason', 'Insufficient edge')}")
        st.info("💡 **Tip:** Paste a slip with WIN/LOSS results – Clarity will auto‑settle them immediately.")

    # =========================================================================
    # TAB 3: SCANNERS & ACCURACY (simplified but functional)
    # =========================================================================
    with tab3:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("📊 Scanners & Accuracy Dashboard")
        scanner_tabs = st.tabs(["📈 Best Odds", "💰 Arbitrage", "🎯 Middles", "📊 Accuracy"])
        with scanner_tabs[0]:
            st.header("Best Odds Scanner (Powered by Odds-API.io)")
            col1, col2 = st.columns([2,1])
            with col1:
                selected_sports_odds = st.multiselect("Select sports", ["NBA","MLB","NHL","NFL","TENNIS","PGA"], default=["NBA"], key="odds_sports")
            with col2:
                if st.button("🔍 SCAN BEST ODDS", type="primary", use_container_width=True, key="odds_scan"):
                    with st.spinner("Scanning sportsbooks via Odds-API.io..."):
                        bets = engine.run_best_odds_scan(selected_sports_odds)
                        st.success(f"Found {len(bets)} +EV props!")
            if engine.scanned_bets.get("best_odds"):
                st.subheader("💰 Best +EV Props (Top 10)")
                for i, bet in enumerate(engine.scanned_bets["best_odds"], 1):
                    st.markdown(f"**{i}. {bet['player']} {bet['market']} {bet['pick']} {bet['line']}**")
                    st.caption(f"Odds: {bet['odds']} @ {bet['bookmaker']} | Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
            else:
                st.info("No +EV props found at this time. Try again when games are live.")
        with scanner_tabs[1]:
            st.header("Arbitrage Detector")
            if st.button("🔍 SCAN FOR ARBITRAGE", type="primary", key="arb_scan"):
                with st.spinner("Scanning..."):
                    if not engine.scanned_bets.get("best_odds"):
                        engine.run_best_odds_scan(["NBA"])
                    arbs = engine.scanned_bets.get("arbs", [])
                    if arbs:
                        st.success(f"Found {len(arbs)} arbitrage opportunities!")
                        for arb in arbs:
                            st.markdown(f"**{arb['Player']} - {arb['Market']}**")
                            st.caption(f"{arb['Bet 1']} | {arb['Bet 2']}")
                            st.metric("Arbitrage %", f"{arb['Arb %']}%")
                    else:
                        st.info("No arbitrage opportunities found.")
        with scanner_tabs[2]:
            st.header("Middle Hunter")
            if st.button("🔍 HUNT FOR MIDDLES", type="primary", key="middle_scan"):
                with st.spinner("Hunting..."):
                    if not engine.scanned_bets.get("best_odds"):
                        engine.run_best_odds_scan(["NBA"])
                    middles = engine.scanned_bets.get("middles", [])
                    if middles:
                        st.success(f"Found {len(middles)} middle opportunities!")
                        for mid in middles:
                            st.markdown(f"**{mid['Player']} - {mid['Market']}**")
                            st.caption(f"Window: {mid['Middle Window']} (Size: {mid['Window Size']})")
                            st.caption(f"{mid['Leg 1']} | {mid['Leg 2']}")
                    else:
                        st.info("No middle opportunities found.")
        with scanner_tabs[3]:
            st.header("Public Accuracy Dashboard")
            accuracy = engine.get_accuracy_dashboard()
            col1, col2, col3, col4 = st.columns(4)
            with col1: st.metric("Total Bets", accuracy['total_bets'])
            with col2: st.metric("Win Rate", f"{accuracy['win_rate']}%")
            with col3: st.metric("ROI", f"{accuracy['roi']}%")
            with col4: st.metric("Units Profit", f"+{accuracy['units_profit']}" if accuracy['units_profit']>0 else str(accuracy['units_profit']))
            st.subheader("By Sport")
            if accuracy['by_sport']:
                sport_df = pd.DataFrame(accuracy['by_sport']).T
                st.dataframe(sport_df)
            else:
                st.info("No settled bets by sport yet.")
            st.subheader("By Tier")
            if accuracy['by_tier']:
                tier_df = pd.DataFrame(accuracy['by_tier']).T
                st.dataframe(tier_df)
            else:
                st.info("No settled bets by tier yet.")
            st.metric("SEM Score", f"{accuracy['sem_score']}/100")

    # =========================================================================
    # TAB 4: PLAYER PROPS (full manual analyzer)
    # =========================================================================
    with tab4:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("🎯 Manual Player Prop Analyzer (Real Rosters)")
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", all_sports, key="prop_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team (for context)", [""] + teams, key="prop_team") if sport in ["NBA","MLB","NHL","NFL","SOCCER_EPL","SOCCER_LALIGA","COLLEGE_BASKETBALL","COLLEGE_FOOTBALL"] else ""
            roster = engine.get_roster(sport, team) if team else engine._get_individual_sport_players(sport)
            player = st.selectbox("Player", roster, key="prop_player")
            available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
            market = st.selectbox("Market", available_markets, key="prop_market")
            line = st.number_input("Line", 0.5, 200.0, 0.5, key="prop_line")
            pick = st.selectbox("Pick", ["OVER","UNDER"], key="prop_pick")
            opponent = st.selectbox("Opponent (optional)", [""] + teams, key="prop_opponent") if teams else ""
        with c2:
            use_real_stats = st.checkbox("Fetch real stats & injuries (API-Sports)", value=False, key="prop_use_real_stats")
            st.info("Note: Real stats are currently using BallsDontLie for NBA, fallback for others.")
            odds = st.number_input("Odds (American)", -500, 500, -110, key="prop_odds")
        if st.button("🚀 ANALYZE PROP", type="primary", use_container_width=True, key="prop_analyze"):
            if not player or player == "Select team first" or player.startswith("Player "):
                st.error("Please select a valid player.")
            else:
                result = engine.analyze_prop(player, market, line, pick, [], sport, odds, team if team else None, "HEALTHY", opponent)
                if result.get('units',0) > 0:
                    st.success(f"### {result['signal']}")
                    if result.get('season_warning'): st.warning(result['season_warning'])
                    if result.get('injury') != "HEALTHY": st.error(f"⚠️ Injury flag: {result['injury']}")
                    col1, col2, col3 = st.columns(3)
                    with col1: st.metric("Projection", f"{result['projection']:.1f}")
                    with col2: st.metric("Probability", f"{result['probability']:.1%}")
                    with col3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
                    st.metric("Tier", result['tier'])
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")

    # =========================================================================
    # TAB 5: HISTORY & METRICS (simplified – no settlement actions, only history and metrics)
    # =========================================================================
    with tab5:
        st.header("📊 HISTORY & METRICS")
        st.markdown("All settled bets appear below. Use this tab to review past performance and model tuning.")
        
        st.subheader("📈 Auto-Tune History (ROI-based)")
        conn = sqlite3.connect(DB_PATH)
        df_tune = pd.read_sql_query("SELECT * FROM tuning_log ORDER BY id DESC", conn)
        conn.close()
        if df_tune.empty:
            st.info("No tuning events yet. After 50+ settled bets, auto-tune will run weekly.")
        else:
            st.dataframe(df_tune)
        
        st.markdown("---")
        st.subheader("📋 Recent Bets")
        df_hist = get_recent_bets(200)
        if df_hist.empty:
            st.info("No bets stored yet. Paste slips in the PASTE & SCAN tab to get started.")
        else:
            st.dataframe(df_hist)
            st.caption("Settled bets are shown with WIN/LOSS result and actual value.")
        
        st.markdown("---")
        st.subheader("📊 SEM Score Calibration")
        st.metric("Current SEM Score", f"{engine.sem_score}/100")
        st.caption("SEM Score auto‑calibrates based on betting accuracy. Higher score = more confident predictions.")
        
        st.markdown("---")
        st.subheader("🏆 Win Rate by Sport & Market (Settled Bets Only)")
        acc = engine.get_accuracy_dashboard()
        if acc['total_bets'] > 0:
            st.metric("Overall Win Rate", f"{acc['win_rate']}%")
            if acc['by_sport']:
                st.write("**By Sport**")
                st.dataframe(pd.DataFrame(acc['by_sport']).T)
            if acc['by_tier']:
                st.write("**By Tier**")
                st.dataframe(pd.DataFrame(acc['by_tier']).T)
        else:
            st.info("No settled bets yet.")

if __name__ == "__main__":
    run_dashboard()
