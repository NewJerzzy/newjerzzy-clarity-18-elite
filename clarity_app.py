# clarity_app.py
# CLARITY 18.2 ELITE – Full Feature Set + Slip Settlement + Clear Pending
# - Multi-sport manual analyzer (NBA, MLB, NFL, NHL)
# - Full auto-settle for NBA (BallsDontLie), scaffold for others
# - Slip-based settlement (Option B)
# - Clear Pending Bets button for testing
# - All unique keys – no Streamlit duplicate ID errors

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

warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION – YOUR API KEYS
# =============================================================================
# >>> REPLACE THESE WITH YOUR REAL KEYS <<<
UNIFIED_API_KEY = "YOUR_UNIFIED_API_KEY"
API_SPORTS_KEY = "YOUR_API_SPORTS_KEY"
ODDS_API_KEY = "YOUR_ODDS_API_KEY"
OCR_SPACE_API_KEY = "YOUR_OCR_SPACE_API_KEY"
ODDS_API_IO_KEY = "YOUR_ODDS_API_IO_KEY"
BALLSDONTLIE_API_KEY = "YOUR_BALLSDONTLIE_API_KEY"

VERSION = "18.2 Elite (Full + Slip Settlement)"
BUILD_DATE = "2026-04-17"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"
BALLSDONTLIE_BASE = "https://api.balldontlie.io"

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
                except Exception:
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
    elif sport in ["SOCCER_EPL", "SOCCER_LALIGA"]:
        if hour not in [14, 15]:
            st.info("⚽ For soccer, lines are often most efficient when scanned in the afternoon (2-3 PM) the day before matches.")

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

# =============================================================================
# FALLBACK NBA ROSTERS (full list)
# =============================================================================
FALLBACK_NBA_ROSTERS = {
    "Atlanta Hawks": ["Trae Young", "Dejounte Murray", "Jalen Johnson", "Clint Capela", "Bogdan Bogdanovic"],
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Jrue Holiday", "Derrick White"],
    "Brooklyn Nets": ["Mikal Bridges", "Cameron Johnson", "Nic Claxton", "Dennis Schroder", "Dorian Finney-Smith"],
    "Charlotte Hornets": ["LaMelo Ball", "Brandon Miller", "Miles Bridges", "Mark Williams", "Grant Williams"],
    "Chicago Bulls": ["Zach LaVine", "DeMar DeRozan", "Nikola Vucevic", "Coby White", "Patrick Williams"],
    "Cleveland Cavaliers": ["Donovan Mitchell", "Darius Garland", "Evan Mobley", "Jarrett Allen", "Caris LeVert"],
    "Dallas Mavericks": ["Luka Doncic", "Kyrie Irving", "Daniel Gafford", "P.J. Washington", "Dereck Lively II"],
    "Denver Nuggets": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr.", "Aaron Gordon", "Christian Braun"],
    "Detroit Pistons": ["Cade Cunningham", "Jaden Ivey", "Ausar Thompson", "Jalen Duren", "Isaiah Stewart"],
    "Golden State Warriors": ["Stephen Curry", "Jimmy Butler", "Draymond Green", "Jonathan Kuminga", "Brandin Podziemski"],
    "Houston Rockets": ["Jalen Green", "Alperen Sengun", "Fred VanVleet", "Amen Thompson", "Dillon Brooks"],
    "Indiana Pacers": ["Tyrese Haliburton", "Pascal Siakam", "Myles Turner", "Bennedict Mathurin", "Andrew Nembhard"],
    "LA Clippers": ["Kawhi Leonard", "James Harden", "Norman Powell", "Ivica Zubac", "Derrick Jones Jr."],
    "Los Angeles Lakers": ["LeBron James", "Luka Doncic", "Austin Reaves", "Rui Hachimura", "Dorian Finney-Smith"],
    "Memphis Grizzlies": ["Ja Morant", "Jaren Jackson Jr.", "Desmond Bane", "Zach Edey", "GG Jackson"],
    "Miami Heat": ["Jimmy Butler", "Bam Adebayo", "Tyler Herro", "Terry Rozier", "Nikola Jovic"],
    "Milwaukee Bucks": ["Giannis Antetokounmpo", "Damian Lillard", "Brook Lopez", "Kyle Kuzma", "Kevin Porter Jr."],
    "Minnesota Timberwolves": ["Anthony Edwards", "Julius Randle", "Rudy Gobert", "Jaden McDaniels", "Naz Reid"],
    "New Orleans Pelicans": ["Zion Williamson", "CJ McCollum", "Trey Murphy III", "Herbert Jones", "Yves Missi"],
    "New York Knicks": ["Jalen Brunson", "Karl-Anthony Towns", "Mikal Bridges", "OG Anunoby", "Josh Hart"],
    "Oklahoma City Thunder": ["Shai Gilgeous-Alexander", "Jalen Williams", "Chet Holmgren", "Luguentz Dort", "Isaiah Hartenstein"],
    "Orlando Magic": ["Paolo Banchero", "Franz Wagner", "Jalen Suggs", "Goga Bitadze", "Kentavious Caldwell-Pope"],
    "Philadelphia 76ers": ["Joel Embiid", "Tyrese Maxey", "Paul George", "Kelly Oubre Jr.", "Quentin Grimes"],
    "Phoenix Suns": ["Devin Booker", "Kevin Durant", "Bradley Beal", "Nick Richards", "Royce O'Neale"],
    "Portland Trail Blazers": ["Anfernee Simons", "Shaedon Sharpe", "Scoot Henderson", "Deandre Ayton", "Deni Avdija"],
    "Sacramento Kings": ["De'Aaron Fox", "Domantas Sabonis", "Zach LaVine", "Malik Monk", "Keegan Murray"],
    "San Antonio Spurs": ["Victor Wembanyama", "De'Aaron Fox", "Devin Vassell", "Jeremy Sochan", "Stephon Castle"],
    "Toronto Raptors": ["Scottie Barnes", "Immanuel Quickley", "RJ Barrett", "Jakob Poeltl", "Gradey Dick"],
    "Utah Jazz": ["Lauri Markkanen", "Collin Sexton", "John Collins", "Walker Kessler", "Keyonte George"],
    "Washington Wizards": ["Jordan Poole", "Kyle Kuzma", "Bilal Coulibaly", "Alex Sarr", "Bub Carrington"]
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
    except Exception:
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
            params = {"league": league_id, "season": "2025-2026" if sport == "NBA" else "2025", "search": team}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                return 1.0
            data = r.json().get("response", [])
            if not data:
                return 1.0
            team_id = data[0]["team"]["id"]
            stats_url = "https://v1.api-sports.io/teams/statistics"
            stats_params = {"league": league_id, "season": "2025-2026" if sport == "NBA" else "2025", "team": team_id}
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
        except Exception:
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
            params = {"league": league_id, "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025", "search": team}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if r.status_code != 200:
                return 1.0, ""
            data = r.json().get("response", [])
            if not data:
                return 1.0, ""
            team_id = data[0]["team"]["id"]
            games_url = "https://v1.api-sports.io/games"
            today = datetime.now().date()
            params = {
                "league": league_id,
                "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025",
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
        except Exception:
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
    except Exception:
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
        params = {"league": league_id, "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025", "search": team}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return fallback_roster, True
        data = r.json().get("response", [])
        if not data:
            return fallback_roster, True
        team_id = data[0]["team"]["id"]
        players_url = "https://v1.api-sports.io/players"
        params = {"league": league_id, "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025", "team": team_id}
        r2 = requests.get(players_url, headers=headers, params=params, timeout=10)
        if r2.status_code != 0 and r2.status_code != 200:
            return fallback_roster, True
        if r2.status_code != 200:
            return fallback_roster, True
        players_data = r2.json().get("response", [])
        roster = [p["player"]["name"] for p in players_data if p.get("player", {}).get("name")]
        if roster:
            return sorted(roster), False
        else:
            return fallback_roster, True
    except Exception:
        return fallback_roster, True

# =============================================================================
# AUTO-SETTLE PLAYER PROP
# =============================================================================
def auto_settle_prop(player: str, market: str, line: float, pick: str, sport: str, opponent: str, game_date: str = None) -> Tuple[str, float]:
    if not game_date:
        game_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    # NBA: try BallsDontLie first
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
        params = {"search": player, "league": league_id, "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025"}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return "PENDING", 0.0
        players = r.json().get("response", [])
        if not players:
            return "PENDING", 0.0
        player_id = players[0]["player"]["id"]
        stats_url = "https://v1.api-sports.io/players/statistics"
        params = {"player": player_id, "league": league_id, "season": "2025-2026" if sport in ["NBA", "NHL"] else "2025"}
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
    except Exception:
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
            "NFL": {"regular_season_end": "2026-01-04", "playoffs_start": "2026-01-10"},
        }
        self.motivation_multipliers = {
            "MUST_WIN": 1.12,
            "PLAYOFF_SEEDING": 1.08,
            "NEUTRAL": 1.00,
            "LOCKED_SEED": 0.92,
            "ELIMINATED": 0.85,
            "TANKING": 0.78,
            "PLAYOFFS": 1.05,
        }

    def get_season_phase(self, sport: str) -> dict:
        date_obj = datetime.now()
        calendar = self.season_calendars.get(sport, {})
        if not calendar:
            return {"phase": "UNKNOWN", "is_playoffs": False}
        if "playoffs_start" in calendar:
            playoffs_start = datetime.strptime(calendar["playoffs_start"], "%Y-%m-%d")
            if date_obj >= playoffs_start:
                return {"phase": "PLAYOFFS", "is_playoffs": True}
        season_end = datetime.strptime(calendar.get("regular_season_end", "2026-12-31"), "%Y-%m-%d")
        days_remaining = (season_end - date_obj).days
        phase = "FINAL_DAY" if days_remaining <= 0 else "FINAL_WEEK" if days_remaining <= 7 else "REGULAR_SEASON"
        return {
            "phase": phase,
            "is_playoffs": False,
            "days_remaining": days_remaining,
            "is_final_week": days_remaining <= 7,
            "is_final_day": days_remaining == 0,
        }

    def should_fade_team(self, sport: str, team: str) -> dict:
        cache_key = f"{sport}_{team}_{datetime.now().strftime('%Y%m%d')}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        phase = self.get_season_phase(sport)
        result = {"team": team, "fade": False, "reasons": [], "multiplier": 1.0, "phase": phase}
        fade_mult, reason = rest_detector.get_rest_fade(sport, team)
        if fade_mult < 1.0:
            result["fade"] = True
            result["reasons"].append(reason)
            result["multiplier"] *= fade_mult
        self.cache[cache_key] = result
        return result

season_context = SeasonContextEngine()

# =============================================================================
# GAME SCANNER (Odds-API.io) – simple scaffold
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.odds_api_io_key = ODDS_API_IO_KEY

    def fetch_todays_games(self, sports: List[str]) -> List[Dict]:
        return self.fetch_games_by_date(sports, days_offset=0)

    def fetch_games_by_date(self, sports: List[str] = None, days_offset: int = 0) -> List[Dict]:
        if sports is None:
            sports = ["NBA", "MLB", "NHL", "NFL"]
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")
        games = self._fetch_games_from_odds_api_io(sports, target_date)
        if games:
            return games
        if days_offset != 0:
            return []
        return []

    @retry(max_attempts=2, delay=1)
    def _fetch_games_from_odds_api_io(self, sports: List[str], date_str: str) -> List[Dict]:
        all_games = []
        sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
        for sport in sports:
            sport_key = sport_map.get(sport)
            if not sport_key:
                continue
            url = f"{ODDS_API_IO_BASE}/sports/{sport_key}/events"
            params = {"date": date_str}
            headers = {"x-api-key": self.odds_api_io_key}
            try:
                r = requests.get(url, headers=headers, params=params, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list):
                        for g in data:
                            g["sport"] = sport
                        all_games.extend(data)
            except Exception:
                continue
        return all_games

game_scanner = GameScanner(ODDS_API_KEY)

# =============================================================================
# SIMPLE PROBABILITY ENGINE FOR PLAYER PROP
# =============================================================================
def estimate_prop_probability(values: List[float], line: float, pick: str) -> float:
    if not values:
        return 0.5
    mu = np.mean(values)
    sigma = np.std(values) if np.std(values) > 0 else 1.0
    if pick == "OVER":
        prob = 1 - norm.cdf(line, loc=mu, scale=sigma)
    else:
        prob = norm.cdf(line, loc=mu, scale=sigma)
    return float(prob)

# =============================================================================
# SLIP STORAGE (SQLite)
# =============================================================================
DB_PATH = "clarity_slips.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS slips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            sport TEXT,
            team TEXT,
            opponent TEXT,
            player TEXT,
            market TEXT,
            line REAL,
            pick TEXT,
            game_date TEXT,
            status TEXT,
            actual REAL
        )
    """)
    conn.commit()
    conn.close()

def add_bet_to_slip(sport: str, team: str, opponent: str, player: str, market: str,
                    line: float, pick: str, game_date: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO slips (created_at, sport, team, opponent, player, market, line, pick, game_date, status, actual)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), sport, team, opponent, player, market, line, pick, game_date, "PENDING", None))
    conn.commit()
    conn.close()

def get_all_slips() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips ORDER BY id DESC", conn)
    conn.close()
    return df

def clear_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM slips WHERE status = 'PENDING'")
    conn.commit()
    conn.close()

def settle_all_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, sport, team, opponent, player, market, line, pick, game_date FROM slips WHERE status = 'PENDING'")
    rows = c.fetchall()
    for row in rows:
        slip_id, sport, team, opponent, player, market, line, pick, game_date = row
        result, actual = auto_settle_prop(player, market, float(line), pick, sport, opponent, game_date)
        if result != "PENDING":
            c.execute("UPDATE slips SET status = ?, actual = ? WHERE id = ?", (result, actual, slip_id))
    conn.commit()
    conn.close()

# =============================================================================
# REDESIGNED UI HELPERS
# =============================================================================
def render_header():
    st.markdown(
        f"""
        <div style="padding: 0.5rem 0 1rem 0;">
            <h1 style="margin-bottom:0;">CLARITY {VERSION}</h1>
            <p style="color:#888;margin-top:0;">Elite Multi-Sport Prop Analyzer • Build Date: {BUILD_DATE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_sidebar_controls():
    st.sidebar.subheader("Global Settings")
    sport = st.sidebar.selectbox("Sport", list(SPORT_MODELS.keys()), index=0, key="sport_select")
    check_scan_timing(sport)
    return sport

def render_prop_input(sport: str):
    st.subheader("Manual Prop Analyzer")

    col1, col2 = st.columns(2)
    with col1:
        team = st.selectbox("Team", HARDCODED_TEAMS.get(sport, ["Team A", "Team B"]), key="team_select")
    with col2:
        opponent = st.text_input("Opponent (optional)", value="", key="opponent_input")

    roster, used_fallback = fetch_team_roster(sport, team)
    player = st.selectbox("Player", roster, key="player_select")

    market = st.selectbox("Market", SPORT_CATEGORIES.get(sport, ["PTS"]), key="market_select")
    line = st.number_input("Line", min_value=0.0, max_value=200.0, value=20.5, step=0.5, key="line_input")
    pick = st.radio("Pick", ["OVER", "UNDER"], horizontal=True, key="pick_radio")
    game_date = st.date_input("Game Date", value=datetime.now().date(), key="game_date_input")

    return {
        "team": team,
        "opponent": opponent,
        "player": player,
        "market": market,
        "line": line,
        "pick": pick,
        "game_date": game_date.strftime("%Y-%m-%d"),
        "used_fallback_roster": used_fallback,
    }

def render_analysis_block(sport: str, prop: dict):
    st.markdown("### Analysis")
    stats, injury_status = fetch_player_stats_and_injury(prop["player"], sport, prop["market"], num_games=8)
    if not stats:
        st.warning("No recent stats found for this player/market. Using neutral baseline.")
    prob = estimate_prop_probability(stats, prop["line"], prop["pick"])
    implied_edge = prob - 0.5

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Model Probability", f"{prob*100:.1f}%")
    with col2:
        st.metric("Edge vs 50/50", f"{implied_edge*100:+.1f}%")
    with col3:
        st.metric("Games Sampled", len(stats))

    if injury_status != "HEALTHY":
        st.info(f"Injury status: {injury_status}")

    if prop["market"] in RED_TIER_PROPS:
        st.error("Red-tier market: higher variance / correlation. Use extra caution.")

    st.write("Recent game log (last 8):")
    if stats:
        df = pd.DataFrame({"Game #": list(range(1, len(stats) + 1)), "Stat": stats})
        st.dataframe(df, hide_index=True, use_container_width=True)
    else:
        st.write("No data available.")

def render_slip_section(sport: str, prop: dict):
    st.markdown("### Slip Actions")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("➕ Add to Slip", key="add_to_slip_btn"):
            add_bet_to_slip(
                sport=sport,
                team=prop["team"],
                opponent=prop["opponent"],
                player=prop["player"],
                market=prop["market"],
                line=prop["line"],
                pick=prop["pick"],
                game_date=prop["game_date"],
            )
            st.success("Bet added to slip.")
    with col2:
        if st.button("✅ Auto-Settle Pending", key="settle_pending_btn"):
            settle_all_pending_slips()
            st.success("Attempted to auto-settle all pending bets.")
    with col3:
        if st.button("🧹 Clear Pending Bets", key="clear_pending_btn"):
            clear_pending_slips()
            st.warning("All pending bets cleared (testing mode).")

def render_slip_table():
    st.subheader("Current Slip / History")
    df = get_all_slips()
    if df.empty:
        st.info("No bets in slip yet.")
        return
    df_display = df.copy()
    st.dataframe(df_display, use_container_width=True, hide_index=True)

# =============================================================================
# MAIN APP
# =============================================================================
def main():
    st.set_page_config(page_title="CLARITY 18.2 ELITE", layout="wide")
    init_db()
    render_header()
    sport = render_sidebar_controls()

    tab_analyzer, tab_slip = st.tabs(["📊 Analyzer", "🧾 Slip & Settlement"])

    with tab_analyzer:
        prop = render_prop_input(sport)
        if st.button("Run Analysis", key="run_analysis_btn"):
            render_analysis_block(sport, prop)
            render_slip_section(sport, prop)

    with tab_slip:
        render_slip_table()
        st.caption("Slip-based settlement (Option B). Use Auto-Settle to grade completed games, or Clear Pending for testing.")

    st.markdown("---")
    st.caption("CLARITY 18.2 ELITE • Multi-sport manual analyzer • Auto-settle NBA via BallsDontLie • Scaffold for others.")

if __name__ == "__main__":
    main()
