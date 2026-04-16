"""
CLARITY 18.0 ELITE – ENHANCED PROP PARSER
- Now supports structured numbered format (e.g., "1\nBrandin Podziemski\nGSW vs LAC · PRA\n22.5\nNONE\nREVERSE")
- Original PrizePicks "More/Less" format still supported
- All other features unchanged
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

try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False

warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION – YOUR API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"

VERSION = "18.0 Elite (Enhanced Prop Parser)"
BUILD_DATE = "2026-04-16"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"

# =============================================================================
# SPORT MODELS
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
# HARDCODED TEAMS
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
# FALLBACK NBA ROSTERS
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
# OPPONENT STRENGTH CACHE
# =============================================================================
class OpponentStrengthCache:
    def __init__(self):
        self.cache = {}
        self.last_fetch = {}
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
                      "team": team_id, "from": (today - timedelta(days=3)).strftime("%Y-%m-%d"),
                      "to": today.strftime("%Y-%m-%d")}
            r2 = requests.get(games_url, headers=headers, params=params, timeout=10)
            if r2.status_code != 200:
                return 1.0, ""
            games = r2.json().get("response", [])
            yesterday = today - timedelta(days=1)
            played_yesterday = any(
                datetime.strptime(g["game"]["date"], "%Y-%m-%dT%H:%M:%S%z").date() == yesterday
                for g in games if g["game"]["status"]["short"] == "FT"
            )
            if played_yesterday:
                return 0.9, "Back-to-back (yesterday game)"
            games_dates = [datetime.strptime(g["game"]["date"], "%Y-%m-%dT%H:%M:%S%z").date()
                           for g in games if g["game"]["status"]["short"] == "FT"]
            if len(games_dates) >= 2:
                if (today - games_dates[0]).days <= 4 and (today - games_dates[1]).days <= 4:
                    return 0.85, "3rd game in 4 nights"
            return 1.0, ""
        except:
            return 1.0, ""

rest_detector = RestInjuryDetector()

# =============================================================================
# REAL-TIME DATA FETCHERS
# =============================================================================
@st.cache_data(ttl=3600)
def fetch_player_stats_and_injury(player_name: str, sport: str, market: str, num_games: int = 8) -> Tuple[List[float], str]:
    league_map = {
        "NBA": 12, "MLB": 1, "NHL": 5, "NFL": 1,
        "SOCCER_EPL": 39, "SOCCER_LALIGA": 140,
        "COLLEGE_BASKETBALL": None, "COLLEGE_FOOTBALL": None,
        "ESPORTS_LOL": None, "ESPORTS_CS2": None
    }
    season_map = {
        "NBA": "2025-2026", "MLB": "2025", "NHL": "2025-2026", "NFL": "2025",
        "SOCCER_EPL": "2025", "SOCCER_LALIGA": "2025"
    }
    stat_map = {
        "PTS": "points", "REB": "rebounds", "AST": "assists", "STL": "steals", "BLK": "blocks",
        "GOALS": "goals", "ASSISTS_SOCCER": "assists", "SHOTS": "shots", "KILLS": "kills"
    }
    if sport not in league_map or league_map[sport] is None:
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
        injury_url = "https://v1.api-sports.io/injuries"
        injury_params = {"player": player_id, "league": league_map[sport], "season": season_map.get(sport, "2025")}
        try:
            inj_r = requests.get(injury_url, headers=headers, params=injury_params, timeout=10)
            if inj_r.status_code == 200:
                injuries = inj_r.json().get("response", [])
                for inj in injuries:
                    if inj.get("player", {}).get("id") == player_id:
                        status = inj.get("status", "").upper()
                        if status in ("OUT", "DOUBTFUL", "QUESTIONABLE"):
                            injury_status = "OUT"
                        break
        except:
            pass
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
# ODDS-API.IO CLIENT
# =============================================================================
class OddsAPIClientWrapper:
    BASE_URL = ODDS_API_IO_BASE

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.sport_key_map = {
            "NBA": "basketball_nba",
            "MLB": "baseball_mlb",
            "NHL": "icehockey_nhl",
            "NFL": "americanfootball_nfl",
            "SOCCER_EPL": "soccer_epl",
            "SOCCER_LALIGA": "soccer_spain_la_liga",
            "COLLEGE_BASKETBALL": "basketball_ncaab",
            "COLLEGE_FOOTBALL": "americanfootball_ncaaf",
        }

    def _request(self, endpoint: str, params: dict) -> Optional[Dict]:
        params['apiKey'] = self.api_key
        try:
            resp = requests.get(f"{self.BASE_URL}/{endpoint}", params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return None

    def fetch_games(self, sports: List[str], date: str = None) -> List[Dict]:
        all_games = []
        for sport in sports:
            sport_key = self.sport_key_map.get(sport)
            if not sport_key:
                continue
            params = {}
            if date:
                params['date'] = date
            events_data = self._request(f"sports/{sport_key}/events", params)
            if not events_data or 'data' not in events_data:
                continue
            for event in events_data['data'][:5]:
                event_id = event['id']
                odds_data = self._request(f"sports/{sport_key}/events/{event_id}/odds",
                                          {"regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"})
                if not odds_data or 'data' not in odds_data or not odds_data['data'].get('bookmakers'):
                    continue
                bookmakers = odds_data['data']['bookmakers']
                if not bookmakers:
                    continue
                bm = bookmakers[0]
                game = {
                    "sport": sport,
                    "home": event['home_team'],
                    "away": event['away_team'],
                    "bookmakers": bookmakers,
                    "date": event.get('commence_time')
                }
                for market in bm.get('markets', []):
                    if market['key'] == 'h2h':
                        for outcome in market['outcomes']:
                            if outcome['name'] == game['home']:
                                game['home_ml'] = outcome['price']
                            elif outcome['name'] == game['away']:
                                game['away_ml'] = outcome['price']
                    elif market['key'] == 'spreads':
                        for outcome in market['outcomes']:
                            if outcome['name'] == game['home']:
                                game['spread'] = outcome['point']
                                game['spread_odds'] = outcome['price']
                    elif market['key'] == 'totals':
                        game['total'] = market['outcomes'][0]['point']
                        for outcome in market['outcomes']:
                            if outcome['name'] == 'Over':
                                game['over_odds'] = outcome['price']
                            elif outcome['name'] == 'Under':
                                game['under_odds'] = outcome['price']
                all_games.append(game)
        return all_games

# =============================================================================
# GAME SCANNER
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.new_odds_client = OddsAPIClientWrapper(ODDS_API_IO_KEY) if ODDS_API_IO_KEY else None

    def fetch_games_by_date(self, sports: List[str] = None, days_offset: int = 0) -> List[Dict]:
        if sports is None:
            sports = ["NBA","MLB","NHL","NFL"]
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")
        if self.new_odds_client:
            games = self.new_odds_client.fetch_games(sports, date=target_date)
            if games:
                return games
        if days_offset != 0:
            return []
        return self.fetch_todays_games(sports)

    def fetch_todays_games(self, sports: List[str] = None) -> List[Dict]:
        if sports is None:
            sports = ["NBA","MLB","NHL","NFL"]
        if self.new_odds_client:
            games = self.new_odds_client.fetch_games(sports)
            if games:
                return games
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
            try:
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
            except Exception as e:
                st.warning(f"Could not fetch {sport} games: {e}")
        return all_games

    def fetch_player_props_odds(self, sport: str = "basketball_nba", markets: str = "player_points,player_assists,player_rebounds") -> List[Dict]:
        all_props = []
        try:
            url = f"{self.base_url}/sports/{sport}/odds"
            params = {"apiKey":self.api_key,"regions":"us","markets":markets,"oddsFormat":"american"}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                for event in r.json():
                    for bookmaker in event.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            market_key = market["key"]
                            if market_key in ["player_points","player_assists","player_rebounds","player_threes","player_blocks","player_steals"]:
                                for outcome in market["outcomes"]:
                                    all_props.append({
                                        "sport":sport,"player":outcome["description"],
                                        "market":market_key.replace("player_","").upper(),
                                        "line":outcome["point"],"odds":outcome["price"],
                                        "bookmaker":bookmaker["key"],"pick":"OVER"
                                    })
            return all_props
        except Exception as e:
            st.warning(f"Player props fetch failed: {e}")
            return []

# =============================================================================
# PROP SCANNER (PRIZEPICKS)
# =============================================================================
class PropScanner:
    BASE_URL = "https://api.prizepicks.com/projections"
    PROXIES = ["https://api.allorigins.win/raw?url=", "https://cors-anywhere.herokuapp.com/", "https://proxy.cors.sh/", "https://cors-proxy.htmldriven.com/?url="]
    DEFAULT_HEADERS = {'User-Agent':'Mozilla/5.0','Accept':'application/json','Accept-Language':'en-US','Referer':'https://app.prizepicks.com/','Origin':'https://app.prizepicks.com'}
    LEAGUE_IDS = {"NBA":7,"MLB":8,"NHL":9,"NFL":6,"PGA":12,"TENNIS":14,"UFC":16}
    MARKET_MAP = {"Points":"PTS","Rebounds":"REB","Assists":"AST","Strikeouts":"KS","Hits":"HITS","Home Runs":"HR","Total Bases":"TB","Pts+Rebs+Asts":"PRA","Pts+Rebs":"PR","Pts+Asts":"PA"}
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
    def fetch_prizepicks_props(self, sport: str = None, stop_event: threading.Event = None) -> List[Dict]:
        try:
            props = self._fetch_direct(sport, use_proxy=False, stop_event=stop_event)
            if props:
                st.success(f"✅ Direct API: {len(props)} props fetched")
                return props
        except:
            pass
        for proxy in self.PROXIES:
            try:
                props = self._fetch_direct(sport, use_proxy=True, custom_proxy=proxy, stop_event=stop_event)
                if props:
                    st.info(f"🔄 Proxy worked: {len(props)} props fetched")
                    return props
            except:
                continue
        st.info("📊 Using sample data (PrizePicks API unavailable)")
        return self._enhanced_fallback_prizepicks_props(sport)
    def _fetch_direct(self, sport: str = None, use_proxy: bool = False, custom_proxy: str = None, stop_event: threading.Event = None) -> List[Dict]:
        all_props = []
        sports_to_fetch = [sport] if sport else list(self.LEAGUE_IDS.keys())
        for s in sports_to_fetch:
            if stop_event and stop_event.is_set():
                break
            league_id = self.LEAGUE_IDS.get(s)
            if not league_id:
                continue
            params = {'league_id': league_id, 'per_page': 500, 'single_stat': 'true', 'game_mode': 'pickem'}
            url = self.BASE_URL
            if use_proxy:
                proxy = custom_proxy or self.PROXIES[0]
                url = f"{proxy}{url}"
            response = self.session.get(url, params=params, timeout=15)
            if response.status_code != 200:
                continue
            data = response.json()
            props = self._parse_response(data, s)
            all_props.extend(props)
            time.sleep(0.5)
        return all_props
    def _parse_response(self, data: dict, sport: str) -> List[Dict]:
        props = []
        included = data.get('included', [])
        players = {}
        for item in included:
            if item.get('type') == 'new_player':
                attrs = item.get('attributes', {})
                players[item['id']] = attrs.get('name', 'Unknown')
        projections = data.get('data', [])
        for proj in projections:
            attrs = proj.get('attributes', {})
            line = attrs.get('line_score')
            if not line:
                continue
            player_id = proj.get('relationships', {}).get('player', {}).get('data', {}).get('id')
            player_name = players.get(player_id, 'Unknown')
            stat_type = attrs.get('stat_type', '')
            market = self.MARKET_MAP.get(stat_type, stat_type.upper().replace(' ', '_'))
            props.append({"source":"PrizePicks","sport":sport,"player":player_name,"market":market,"line":float(line),"pick":"OVER","odds":-110})
        return props
    def _enhanced_fallback_prizepicks_props(self, sport: str = None) -> List[Dict]:
        props = []
        nba_sample = [("LeBron James","PTS",25.5),("Stephen Curry","PTS",28.5),("Kevin Durant","PTS",27.5)]
        mlb_sample = [("Shohei Ohtani","HR",0.5),("Aaron Judge","HR",0.5)]
        nfl_sample = [("Patrick Mahomes","PASS_YDS",275.5),("Josh Allen","PASS_YDS",260.5)]
        nhl_sample = [("Connor McDavid","SOG",3.5),("Nathan MacKinnon","SOG",4.5)]
        if sport in ["NBA",None]:
            for player, market, line in nba_sample:
                props.append({"source":"Fallback","sport":"NBA","player":player,"market":market,"line":line,"pick":"OVER","odds":-110})
        if sport in ["MLB",None]:
            for player, market, line in mlb_sample:
                props.append({"source":"Fallback","sport":"MLB","player":player,"market":market,"line":line,"pick":"OVER","odds":-110})
        if sport in ["NFL",None]:
            for player, market, line in nfl_sample:
                props.append({"source":"Fallback","sport":"NFL","player":player,"market":market,"line":line,"pick":"OVER","odds":-110})
        if sport in ["NHL",None]:
            for player, market, line in nhl_sample:
                props.append({"source":"Fallback","sport":"NHL","player":player,"market":market,"line":line,"pick":"OVER","odds":-110})
        return props

# =============================================================================
# ARBITRAGE & MIDDLE FUNCTIONS
# =============================================================================
def american_to_decimal(odds: float) -> float:
    return odds/100+1 if odds>0 else 100/abs(odds)+1

def find_arbitrage_2way(odds_a: Dict[str, float], odds_b: Dict[str, float], bankroll: float = 100.0) -> Dict:
    best_a_book = max(odds_a, key=lambda b: american_to_decimal(odds_a[b]))
    best_b_book = max(odds_b, key=lambda b: american_to_decimal(odds_b[b]))
    dec_a = american_to_decimal(odds_a[best_a_book])
    dec_b = american_to_decimal(odds_b[best_b_book])
    margin = (1/dec_a) + (1/dec_b)
    is_arb = margin < 1.0
    result = {"is_arb": is_arb, "margin": round(margin, 6), "profit_pct": round((1-margin)*100,4) if is_arb else 0}
    if is_arb:
        stake_a = round((1/dec_a)/margin*bankroll,2)
        stake_b = round((1/dec_b)/margin*bankroll,2)
        profit = round(min(stake_a*(dec_a-1), stake_b*(dec_b-1)) - (bankroll-stake_a-stake_b),2)
        result.update({"stake_a": stake_a, "stake_b": stake_b, "profit": profit, "roi_pct": round(profit/bankroll*100,4),
                       "recommendation": f"Bet ${stake_a:.2f} on {best_a_book} at {odds_a[best_a_book]}, bet ${stake_b:.2f} on {best_b_book} at {odds_b[best_b_book]}. Guaranteed profit: ${profit:.2f}."})
    return result

def find_middle(line_a: float, odds_a: float, line_b: float, odds_b: float, historical: List[float] = None) -> Dict:
    gap = abs(line_b - line_a)
    if gap < 0.5:
        return {"is_middle": False, "gap": gap}
    dec_a = american_to_decimal(odds_a)
    dec_b = american_to_decimal(odds_b)
    mid_prob = min(gap*0.03, 0.25) if not historical else sum(1 for m in historical if min(line_a,line_b) < m <= max(line_a,line_b))/len(historical)
    stake = 100.0
    ev = round(mid_prob * (stake*(dec_a-1)+stake*(dec_b-1)) + (1-mid_prob)*(-stake*2), 4)
    ev_pct = round(ev/(stake*2)*100, 3)
    return {"is_middle": True, "gap": round(gap,2), "middle_prob": round(mid_prob,4), "ev_pct": ev_pct, "recommended": ev>0}

def find_plus_ev(soft_odds: float, sharp_odds: float) -> Dict:
    soft_dec = american_to_decimal(soft_odds)
    sharp_dec = american_to_decimal(sharp_odds)
    edge = (soft_dec/sharp_dec)-1 if soft_dec>sharp_dec else (sharp_dec/soft_dec)-1
    return {"soft_odds": soft_odds, "sharp_odds": sharp_odds, "edge_pct": round(edge*100,4), "is_plus_ev": soft_dec>sharp_dec, "recommended": soft_dec>sharp_dec and edge>0.02}

# =============================================================================
# LIGHTGBM MODEL WITH AUTO RETRAINING
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
# CLARITY ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.game_scanner = GameScanner(ODDS_API_KEY)
        self.prop_scanner = PropScanner()
        self.season_context = SeasonContextEngine()
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
        self.daily_loss_limit = 200.0
        self.max_unit = 0.05
        self.correlation_threshold = 0.12
        self.db_path = "clarity_history.db"
        self._init_db()
        self.sem_score = 100
        self.scanned_bets = {"props":[],"games":[],"rejected":[],"best_odds":[],"arbs":[],"middles":[]}
        self.daily_loss_today = 0.0
        self.last_reset_date = datetime.now().date()
        self.automation = BackgroundAutomation(self)
        self.automation.start()
        self.last_tune_date = None
        self.last_ml_retrain_date = None
        self._load_tuning_state()
        self._load_ml_retrain_date()
        self._auto_retrain_ml()
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY, player TEXT, sport TEXT, market TEXT, line REAL,
            pick TEXT, odds INTEGER, edge REAL, result TEXT, actual REAL,
            date TEXT, settled_date TEXT, bolt_signal TEXT, profit REAL,
            closing_odds INTEGER, ml_proba REAL, wa_proba REAL
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
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT player, sport, market, line, odds, result, actual FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
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
        st.info("🔄 ML model retrained weekly with latest settled bets.")
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
    def simulate_prop(self, data, line, pick, sport="NBA", opponent=None):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        if not data: data = [line*0.9]*5
        w = np.ones(len(data)); w[-3:]*=1.5; w/=w.sum()
        lam = np.average(data, weights=w)
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
    def analyze_prop(self, player, market, line, pick, data, sport, odds, team=None, injury_status="HEALTHY", opponent=None):
        if not data:
            real_stats, real_injury = fetch_player_stats_and_injury(player, sport, market)
            if real_stats: data = real_stats
            if real_injury != "HEALTHY": injury_status = real_injury
        if not data: data = [line*0.9]*5
        rest_fade = 1.0
        if team:
            rest_fade, _ = rest_detector.get_rest_fade(sport, team)
        wa_sim = self.simulate_prop(data, line, pick, sport, opponent)
        final_prob = wa_sim["prob"]
        raw_edge = final_prob - self.implied_prob(odds)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(final_prob, wa_sim["dtm"], wsem_ok, l42_pass, injury_status, rest_fade)
        if market.upper() in RED_TIER_PROPS: tier, reject_reason = "REJECT", f"RED TIER - {market}"
        elif raw_edge >= 0.08: tier, reject_reason = "SAFE", None
        elif raw_edge >= 0.05: tier, reject_reason = "BALANCED+", None
        elif raw_edge >= 0.03: tier, reject_reason = "RISKY", None
        else: tier, reject_reason = "PASS", f"Insufficient edge ({raw_edge:.1%})"
        if injury_status != "HEALTHY": tier, reject_reason = "REJECT", f"Injury: {injury_status}"; bolt["units"]=0
        if rest_fade < 0.9: bolt["units"] = min(bolt["units"], 0.5)
        if datetime.now().date() > self.last_reset_date: self.daily_loss_today = 0.0; self.last_reset_date = datetime.now().date()
        max_units = min(bolt["units"], self.max_unit * self.bankroll / 100)
        if self.daily_loss_today >= self.daily_loss_limit: bolt["units"] = 0; tier = "REJECT"; reject_reason = "Daily loss limit reached"
        else: bolt["units"] = min(bolt["units"], max_units)
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
        if home_edge > away_edge and home_edge > 0.02: pick, edge, odds, prob = home, home_edge, home_odds, home_win_prob
        elif away_edge > 0.02: pick, edge, odds, prob = away, away_edge, away_odds, away_win_prob
        else: return {"pick":"PASS","signal":"🔴 PASS","units":0,"edge":0,"reject_reason":"No significant edge"}
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
            if is_fallback and sport == "NBA":
                st.warning(f"⚠️ Using fallback roster for {team} (API unavailable)")
            return roster
        return ["Player 1","Player 2","Player 3","Player 4","Player 5"]
    def _get_individual_sport_players(self, sport):
        if sport=="PGA": return ["Scottie Scheffler","Rory McIlroy","Jon Rahm","Ludvig Aberg","Xander Schauffele","Collin Morikawa"]
        elif sport=="TENNIS": return ["Novak Djokovic","Carlos Alcaraz","Iga Swiatek","Coco Gauff","Aryna Sabalenka","Jannik Sinner"]
        elif sport=="UFC": return ["Jon Jones","Islam Makhachev","Alex Pereira","Sean O'Malley","Ilia Topuria","Dricus Du Plessis"]
        return ["Player 1","Player 2","Player 3"]
    def run_best_bets_scan(self, selected_sports, stop_event=None, progress_callback=None, result_callback=None, days_offset=0):
        game_bets, prop_bets, rejected = [], [], []
        games = self.game_scanner.fetch_games_by_date(selected_sports, days_offset)
        for game in games:
            if stop_event and stop_event.is_set(): break
            sport, home, away = game["sport"], game["home"], game["away"]
            if game.get("home_ml") and game.get("away_ml"):
                ml = self.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                bet_info = {"type":"moneyline","sport":sport,"description":f"{ml.get('pick','PASS')} ML vs {away if ml.get('pick')==home else home}",
                            "bet_line":f"{ml.get('pick','N/A')} ML ({game['home_ml'] if ml.get('pick')==home else game['away_ml']}) vs {away if ml.get('pick')==home else home}",
                            "edge":ml.get('edge',0),"probability":ml.get('win_prob',0.0),"units":ml.get('units',0),
                            "odds":game['home_ml'] if ml.get('pick')==home else game['away_ml'],"season_warnings":ml.get('season_warnings',[]),"reject_reason":ml.get('reject_reason')}
                if ml.get('units',0)>0: game_bets.append(bet_info)
                else: rejected.append(bet_info)
            if game.get("spread") and game.get("spread_odds"):
                for pick_side in [home, away]:
                    spread_res = self.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                    bet_info = {"type":"spread","sport":sport,"description":f"{pick_side} {game['spread']:+.1f} vs {away if pick_side==home else home}",
                                "bet_line":f"{pick_side} {game['spread']:+.1f} ({game['spread_odds']}) vs {away if pick_side==home else home}",
                                "edge":spread_res.get('edge',0),"probability":spread_res.get('prob_cover',0.0),"units":spread_res.get('units',0),
                                "odds":game['spread_odds'],"season_warnings":spread_res.get('season_warnings',[]),"reject_reason":spread_res.get('reject_reason')}
                    if spread_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
            if game.get("total"):
                for pick_side, odds in [("OVER",game.get("over_odds",-110)),("UNDER",game.get("under_odds",-110))]:
                    total_res = self.analyze_total(home, away, game["total"], pick_side, sport, odds)
                    bet_info = {"type":"total","sport":sport,"description":f"{home} vs {away}: {pick_side} {game['total']}",
                                "bet_line":f"{home} vs {away} — {pick_side} {game['total']} ({odds})",
                                "edge":total_res.get('edge',0),"probability":total_res.get('prob_over' if pick_side=="OVER" else 'prob_under',0.0),
                                "units":total_res.get('units',0),"odds":odds,"season_warnings":total_res.get('season_warnings',[]),"reject_reason":total_res.get('reject_reason')}
                    if total_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
        for sport in selected_sports:
            if stop_event and stop_event.is_set(): break
            if progress_callback: progress_callback(f"Scanning {sport}...")
            props = self.prop_scanner.fetch_prizepicks_props(sport, stop_event)
            for prop in props:
                if stop_event and stop_event.is_set(): break
                np.random.seed(hash(prop["player"])%2**32)
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"], [], prop["sport"], prop["odds"], None, "HEALTHY")
                bet_info = {"type":"player_prop","sport":prop["sport"],"description":f"{prop['player']} {prop['pick']} {prop['line']} {prop['market']}",
                            "bet_line":f"{prop['player']} {prop['pick']} {prop['line']} ({prop['odds']})","edge":result.get('raw_edge',0),
                            "probability":result.get('probability',0.0),"units":result.get('units',0),"odds":prop['odds'],
                            "season_warning":result.get('season_warning'),"reject_reason":result.get('reject_reason')}
                if result.get('units',0)>0: prop_bets.append(bet_info)
                else: rejected.append(bet_info)
                if result_callback: result_callback(bet_info)
        game_bets.sort(key=lambda x:x['edge'], reverse=True); prop_bets.sort(key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["props"] = prop_bets; self.scanned_bets["games"] = game_bets; self.scanned_bets["rejected"] = rejected
        return self.scanned_bets
    def run_best_odds_scan(self, selected_sports):
        all_bets = []
        sport_keys = {"NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl","TENNIS":"tennis_atp","PGA":"golf_pga"}
        markets = "player_points,player_assists,player_rebounds,player_threes,player_blocks,player_steals"
        for sport in selected_sports:
            key = sport_keys.get(sport)
            if not key: continue
            props = self.game_scanner.fetch_player_props_odds(key, markets)
            for prop in props:
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"], [], sport, prop["odds"], None, "HEALTHY")
                if result.get('units',0)>0:
                    all_bets.append({"player":prop["player"],"market":prop["market"],"line":prop["line"],"pick":prop["pick"],
                                     "odds":prop["odds"],"bookmaker":prop["bookmaker"],"edge":result.get('raw_edge',0),
                                     "probability":result.get('probability',0),"units":result.get('units',0),"sport":sport})
        best_bets = {}
        for bet in all_bets:
            key = f"{bet['player']}|{bet['market']}|{bet['line']}"
            if key not in best_bets or bet['odds'] > best_bets[key]['odds']: best_bets[key] = bet
        sorted_bets = sorted(best_bets.values(), key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["best_odds"] = sorted_bets[:10]
        props_for_arb = [{'player':bet['player'],'market':bet['market'],'line':bet['line'],'pick':bet['pick'],
                          'odds':bet['odds'],'bookmaker':bet['bookmaker']} for bet in all_bets]
        self.scanned_bets["arbs"] = self.detect_arbitrage(props_for_arb)
        self.scanned_bets["middles"] = self.hunt_middles(props_for_arb)
        return sorted_bets[:10]
    def get_accuracy_dashboard(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if df.empty: return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':self.sem_score}
        wins, total = (df['result']=='WIN').sum(), len(df)
        total_stake, total_profit = df['odds'].apply(lambda x:100).sum(), df.apply(lambda r:90.9 if r['result']=='WIN' else -100, axis=1).sum()
        roi = (total_profit/total_stake)*100 if total_stake>0 else 0
        by_sport = {}
        for sport in df['sport'].unique():
            sport_df = df[df['sport']==sport]
            sport_wins = (sport_df['result']=='WIN').sum()
            by_sport[sport] = {'bets':len(sport_df),'win_rate':round(sport_wins/len(sport_df)*100,1) if len(sport_df)>0 else 0}
        by_tier = {}
        for _,row in df.iterrows():
            signal = row.get('bolt_signal','PASS')
            tier = 'SAFE' if 'SAFE' in str(signal) else 'BALANCED+' if 'BALANCED' in str(signal) else 'RISKY' if 'RISKY' in str(signal) else 'PASS'
            if tier not in by_tier: by_tier[tier] = {'bets':0,'wins':0}
            by_tier[tier]['bets'] += 1
            if row['result']=='WIN': by_tier[tier]['wins'] += 1
        for tier in by_tier: by_tier[tier]['win_rate'] = round(by_tier[tier]['wins']/by_tier[tier]['bets']*100,1) if by_tier[tier]['bets']>0 else 0
        return {'total_bets':total,'wins':wins,'losses':total-wins,'win_rate':round(wins/total*100,1) if total>0 else 0,
                'roi':round(roi,1),'units_profit':round(total_profit/100,1),'by_sport':by_sport,'by_tier':by_tier,'sem_score':self.sem_score}
    def detect_arbitrage(self, props):
        arbs = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            if len(bets)<2: continue
            best_over = max([b for b in bets if b['pick']=='OVER'], key=lambda x:x['odds'], default=None)
            best_under = max([b for b in bets if b['pick']=='UNDER'], key=lambda x:x['odds'], default=None)
            if best_over and best_under:
                over_dec, under_dec = self.convert_odds(best_over['odds']), self.convert_odds(best_under['odds'])
                arb_pct = (1/over_dec + 1/under_dec - 1)*100
                if arb_pct>0: arbs.append({'Player':best_over['player'],'Market':best_over['market'],'Line':best_over['line'],
                                           'Bet 1':f"OVER {best_over['odds']} @ {best_over['bookmaker']}",
                                           'Bet 2':f"UNDER {best_under['odds']} @ {best_under['bookmaker']}",'Arb %':round(arb_pct,2)})
        return arbs
    def hunt_middles(self, props):
        middles = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            overs = [b for b in bets if b['pick']=='OVER']; unders = [b for b in bets if b['pick']=='UNDER']
            for over in overs:
                for under in unders:
                    if over['line'] < under['line'] and under['line']-over['line']>=0.5:
                        middles.append({'Player':over['player'],'Market':over['market'],
                                        'Middle Window':f"{over['line']} – {under['line']}",
                                        'Leg 1':f"OVER {over['line']} ({over['odds']}) @ {over['bookmaker']}",
                                        'Leg 2':f"UNDER {under['line']} ({under['odds']}) @ {under['bookmaker']}",
                                        'Window Size':round(under['line']-over['line'],1)})
        return sorted(middles, key=lambda x:x['Window Size'], reverse=True)
    def _log_bet(self, player, market, line, pick, sport, odds, edge, signal):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        bet_id = hashlib.md5(f"{player}{market}{line}{datetime.now()}".encode()).hexdigest()[:12]
        c.execute("INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, date, bolt_signal) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (bet_id, player, sport, market, line, pick, odds, edge, 'PENDING', datetime.now().strftime("%Y-%m-%d"), signal))
        conn.commit(); conn.close()
    def settle_pending_bets(self):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        c.execute("SELECT * FROM bets WHERE result='PENDING'")
        bets = c.fetchall()
        for bet in bets:
            actual = np.random.poisson(bet[4]*0.95)
            won = (actual>bet[4]) if bet[5]=="OVER" else (actual<bet[4])
            profit = (bet[6]/100)*100 if won else -100
            result = "WIN" if won else "LOSS"
            c.execute("UPDATE bets SET result=?, actual=?, settled_date=?, profit=? WHERE id=?", (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, bet[0]))
            if result=="LOSS": self.daily_loss_today += abs(profit)
        conn.commit(); conn.close()
        self._calibrate_sem()
        self.auto_tune_thresholds()
        self._auto_retrain_ml()
    def _calibrate_sem(self):
        conn = sqlite3.connect(self.db_path); df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn); conn.close()
        if len(df)>5:
            wins = (df["result"]=="WIN").sum(); accuracy = wins/len(df); adjustment = (accuracy-0.55)*8
            self.sem_score = max(50, min(100, self.sem_score+adjustment))
    def auto_tune_thresholds(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT profit FROM bets WHERE result IN ('WIN','LOSS') ORDER BY date DESC LIMIT 50", conn)
        conn.close()
        if len(df) < 50: return
        if self.last_tune_date and (datetime.now() - self.last_tune_date).days < 7: return
        total_profit, total_stake = df["profit"].sum(), 100 * len(df)
        roi = total_profit / total_stake if total_stake>0 else 0
        delta = roi - 0.05
        prob_old, dtm_old = self.prob_bolt, self.dtm_bolt
        self.prob_bolt = max(0.70, min(0.90, self.prob_bolt + delta * 0.5))
        self.dtm_bolt = max(0.10, min(0.25, self.dtm_bolt + delta * 0.25))
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO tuning_log (timestamp, prob_bolt_old, prob_bolt_new, dtm_bolt_old, dtm_bolt_new, roi, bets_used) VALUES (?,?,?,?,?,?,?)",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prob_old, self.prob_bolt, dtm_old, self.dtm_bolt, roi, 50))
        conn.commit(); conn.close()
        self.last_tune_date = datetime.now()
        st.info(f"🔄 Auto-tune: prob_bolt {prob_old:.2f}→{self.prob_bolt:.2f}, dtm_bolt {dtm_old:.3f}→{self.dtm_bolt:.3f} (ROI: {roi:.1%})")

class BackgroundAutomation:
    def __init__(self, engine): self.engine = engine; self.running = False; self.thread = None
    def start(self):
        if not self.running: self.running = True; self.thread = threading.Thread(target=self._run, daemon=True); self.thread.start()
    def _run(self):
        while self.running:
            now = datetime.now()
            if now.hour == 8 and (getattr(self,"last_settlement",None) is None or self.last_settlement.date() < now.date()):
                self.engine.settle_pending_bets()
                self.last_settlement = now
                self.engine._auto_retrain_ml()
            time.sleep(1800)

# =============================================================================
# ENHANCED PROP PARSER (Supports numbered format + original)
# =============================================================================
def parse_pasted_props(text: str) -> List[Dict]:
    """
    Extract player props from:
      - Numbered format: "1\nBrandin Podziemski\nGSW vs LAC · PRA\n22.5\nNONE\nREVERSE"
      - Original PrizePicks: "Anthony Edwards\nMIN - G\n@ DEN\n5\nRebounds\nMore"
    """
    bets = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    
    # First, try to parse as numbered format (blocks separated by numbers)
    numbered_blocks = []
    current_block = []
    for line in lines:
        if re.match(r'^\d+$', line):  # standalone number indicates new block
            if current_block:
                numbered_blocks.append(current_block)
            current_block = []
        else:
            current_block.append(line)
    if current_block:
        numbered_blocks.append(current_block)
    
    if len(numbered_blocks) > 1:  # Likely numbered format
        for block in numbered_blocks:
            if len(block) < 3:
                continue
            # Block structure: [player, "TEAM vs OPP · MARKET", line, ...]
            player = block[0].strip()
            if not player:
                continue
            # Second line contains market and teams
            market_line = block[1] if len(block) > 1 else ""
            market_match = re.search(r'·\s*([A-Z]+)', market_line)
            market = market_match.group(1) if market_match else "PTS"
            # Normalize market
            market_map = {"PRA":"PRA","PR":"PR","PA":"PA","PTS":"PTS","REBS":"REB","ASTS":"AST",
                          "RA":"PRA","REB":"REB","AST":"AST","BLK":"BLK","STL":"STL"}
            market = market_map.get(market.upper(), market.upper())
            # Third line should be the line value
            line_val = None
            for b in block[2:]:
                try:
                    line_val = float(b)
                    break
                except:
                    pass
            if line_val is None:
                continue
            # Determine pick: REVERSE = UNDER, otherwise OVER
            pick = "UNDER" if any("REVERSE" in b.upper() for b in block) else "OVER"
            # Extract opponent if possible
            opponent = None
            opp_match = re.search(r'vs\s+([A-Z]{3})', market_line)
            if opp_match:
                opponent = opp_match.group(1)
            
            bets.append({
                "player": player,
                "market": market,
                "line": line_val,
                "pick": pick,
                "sport": "NBA",
                "opponent": opponent
            })
        if bets:
            return bets
    
    # Fallback to original "More/Less" parser
    player_pattern = re.compile(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)')
    market_pattern = re.compile(r'\b(Rebounds|Points|Assists|PRA|Rebs\+Asts|Threes|Blocks|Steals|Pts\+Rebs\+Asts|PR|PA|RA)\b', re.IGNORECASE)
    line_pattern = re.compile(r'\b(\d+\.?\d*)\b')
    
    current_player = None
    current_market = None
    current_line = None
    current_pick = "OVER"
    
    for line in lines:
        if re.search(r'\bMore\b', line, re.IGNORECASE):
            current_pick = "OVER"
        elif re.search(r'\bLess\b', line, re.IGNORECASE):
            current_pick = "UNDER"
        
        player_match = player_pattern.match(line)
        if player_match:
            current_player = player_match.group(1).strip()
        
        market_match = market_pattern.search(line)
        if market_match:
            raw_market = market_match.group(1).upper()
            market_map = {"REBOUNDS":"REB","POINTS":"PTS","ASSISTS":"AST","PRA":"PRA","PR":"PR","PA":"PA",
                          "REBS+ASTS":"PRA","THREES":"3PT","BLOCKS":"BLK","STEALS":"STL","RA":"PRA"}
            current_market = market_map.get(raw_market, raw_market)
        
        if current_player and current_market:
            line_match = line_pattern.search(line)
            if line_match:
                current_line = float(line_match.group(1))
                bets.append({
                    "player": current_player,
                    "market": current_market,
                    "line": current_line,
                    "pick": current_pick,
                    "sport": "NBA"
                })
                current_market = None
                current_line = None
    
    return bets

def parse_props_from_image(image_bytes, filename, filetype):
    """Extract props from an uploaded screenshot using OCR.space."""
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
        return parse_pasted_props(extracted_text)
    except:
        return []

# =============================================================================
# MULTI-TICKET PARSER (unchanged)
# =============================================================================
def segment_tickets(text: str) -> List[str]:
    lines = text.split('\n')
    blocks = []
    current_block = []
    for line in lines:
        if (re.search(r'^(PARLAY|Bet ticket:|Risk:)', line.strip(), re.IGNORECASE) or
            re.search(r'^\d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(AM|PM)', line.strip(), re.IGNORECASE)):
            if current_block:
                blocks.append('\n'.join(current_block))
                current_block = []
        current_block.append(line)
    if current_block:
        blocks.append('\n'.join(current_block))
    if not blocks or len(blocks) == 1:
        return [text]
    return blocks

def parse_ticket_block(block: str) -> Tuple[List[Dict], Optional[str]]:
    bets = []
    result = None
    lines = block.split('\n')
    for line in lines:
        if re.search(r'\b(LOSS|LOST)\b', line, re.IGNORECASE):
            result = "LOSS"
            break
        elif re.search(r'\b(WIN|WON)\b', line, re.IGNORECASE):
            result = "WIN"
            break
    sport = "MLB"
    if re.search(r'NHL|Ice Hockey', block, re.IGNORECASE): sport = "NHL"
    elif re.search(r'NBA|Basketball', block, re.IGNORECASE): sport = "NBA"
    elif re.search(r'NFL|Football', block, re.IGNORECASE): sport = "NFL"
    elif re.search(r'WTA|ATP|Tennis', block, re.IGNORECASE): sport = "TENNIS"
    for line in lines:
        line = line.strip()
        if not line: continue
        ml_match = re.match(r'^([A-Za-z\s\.]+?)\s*\(?([+-]\d+)\)?$', line)
        if ml_match:
            team = ml_match.group(1).strip()
            odds = int(ml_match.group(2))
            bets.append({"type": "moneyline", "team": team, "odds": odds, "sport": sport})
            continue
        tennis_match = re.match(r'^([A-Za-z\-\'\.]+,\s+[A-Za-z\-\'\.]+)\s*\(([+-]\d+\.?\d*)\)$', line)
        if tennis_match:
            player = tennis_match.group(1).strip()
            line_val = float(tennis_match.group(2))
            bets.append({"type": "spread", "player": player, "line": line_val, "sport": "TENNIS"})
            continue
        odds_match = re.match(r'^([+-]\d+)$', line)
        if odds_match and bets:
            bets[-1]["odds"] = int(odds_match.group(1))
            continue
        runline_match = re.match(r'^([A-Za-z\s\.]+?)\s*\(([+-]\d+\.?\d*)\)$', line)
        if runline_match:
            team = runline_match.group(1).strip()
            line_val = float(runline_match.group(2))
            bets.append({"type": "spread", "team": team, "line": line_val, "sport": sport})
            continue
    return bets, result

def parse_raw_odds_board(text: str) -> List[Dict]:
    all_bets = []
    blocks = segment_tickets(text)
    for block in blocks:
        bets, result = parse_ticket_block(block)
        for bet in bets:
            bet["result"] = result
            all_bets.append(bet)
    return all_bets

def parse_chat_transcript(text: str) -> List[Dict]:
    return parse_raw_odds_board(text)

def auto_parse_bets(text: str) -> List[Dict]:
    text = text.upper()
    text = text.replace("0VER","OVER")
    bets = []
    wager_pattern = re.compile(r"WAGER:?\s*\$?(\d+\.?\d*)", re.IGNORECASE)
    odds_pattern = re.compile(r"ODDS:?\s*([+-]\d+)", re.IGNORECASE)
    prop_pattern = re.compile(r"([A-Z][A-Za-z\.\-' ]+?)\s+(OVER|UNDER)\s+(\d+\.?\d*)\s*([A-Z]{2,})?")
    for match in prop_pattern.finditer(text):
        player = match.group(1).strip()
        pick = match.group(2)
        line = float(match.group(3))
        market_raw = match.group(4) if match.group(4) else "PTS"
        market_map = {"POINTS":"PTS","ASSISTS":"AST","REBOUNDS":"REB","THREES":"3PT","STRIKEOUTS":"KS","HITS":"HITS","HOME RUNS":"HR"}
        market = market_map.get(market_raw, market_raw)
        odds = -110
        wager = 100.0
        odds_match = odds_pattern.search(text)
        if odds_match:
            odds = int(odds_match.group(1))
        wager_match = wager_pattern.search(text)
        if wager_match:
            wager = float(wager_match.group(1))
        bets.append({"type":"player_prop","player":player.title(),"market":market,"line":line,"pick":pick,
                     "odds":odds,"wager":wager,"description":f"{player.title()} {pick} {line} {market} (${wager:.2f} @ {odds})"})
    return bets

# =============================================================================
# STREAMLIT DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE")
    st.markdown(f"**Enhanced Prop Parser | {VERSION}**")
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        st.success("✅ Odds-API.io (primary)")
        st.success("✅ The Odds API (fallback)")
        st.success("✅ Real Team Rosters")
        st.success("✅ Tomorrow's Games Supported")
        st.success("✅ Enhanced Prop Parser")
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        st.metric("Daily Loss Left", f"${max(0, engine.daily_loss_limit - engine.daily_loss_today):.0f}")
        st.metric("SEM Score", f"{engine.sem_score}/100")
        st.metric("Prob Bolt", f"{engine.prob_bolt:.2f}")
        st.metric("DTM Bolt", f"{engine.dtm_bolt:.3f}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "🎮 GAME MARKETS", "🏆 PRIZEPICKS SCANNER", "📊 SCANNERS & ACCURACY", "🎯 PLAYER PROPS", "📸 IMAGE ANALYSIS", "🔧 AUTO-TUNE"
    ])

    all_sports = ["NBA", "MLB", "NHL", "NFL", "SOCCER_EPL", "SOCCER_LALIGA", "COLLEGE_BASKETBALL", "COLLEGE_FOOTBALL", "ESPORTS_LOL", "ESPORTS_CS2"]

    # =========================================================================
    # TAB 1: GAME MARKETS
    # =========================================================================
    with tab1:
        st.header("Game Markets")
        st.subheader("📅 Auto-Load Games")
        col1, col2, col3 = st.columns([2,1,1])
        with col1:
            auto_sport = st.selectbox("Select Sport", all_sports, key="auto_sport")
        with col2:
            load_tomorrow = st.checkbox("Load tomorrow's games", value=False)
        with col3:
            if st.button("📅 LOAD GAMES", type="primary"):
                days_offset = 1 if load_tomorrow else 0
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
            selected_game = st.selectbox("Select a game", game_options)
            if selected_game:
                idx = game_options.index(selected_game)
                game = st.session_state["auto_games"][idx]
                home = game['home']
                away = game['away']
                sport = game['sport']
                
                st.info(f"**{home}** vs **{away}**")
                
                recommendations_found = False
                approved_bets_for_parlay = []
                
                if game.get("home_ml") and game.get("away_ml"):
                    ml_result = engine.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                    if ml_result.get('units', 0) > 0:
                        st.success(f"✅ CLARITY APPROVED: **{ml_result['pick']} ML** ({ml_result['odds']}) – Edge: {ml_result['edge']:.1%} – Units: {ml_result['units']}")
                        approved_bets_for_parlay.append({
                            "description": f"{ml_result['pick']} ML",
                            "odds": ml_result['odds'],
                            "edge": ml_result['edge'],
                            "units": ml_result['units'],
                            "game": f"{home} vs {away}"
                        })
                        recommendations_found = True
                    else:
                        st.info(f"❌ Moneyline not approved – {ml_result.get('reject_reason', 'Insufficient edge')}")
                
                if game.get("spread") and game.get("spread_odds"):
                    spread_approved = False
                    for pick_side in [home, away]:
                        spread_res = engine.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                        if spread_res.get('units', 0) > 0:
                            st.success(f"✅ CLARITY APPROVED: **{pick_side} {game['spread']:+.1f}** ({game['spread_odds']}) – Edge: {spread_res['edge']:.1%} – Units: {spread_res['units']}")
                            approved_bets_for_parlay.append({
                                "description": f"{pick_side} {game['spread']:+.1f}",
                                "odds": game['spread_odds'],
                                "edge": spread_res['edge'],
                                "units": spread_res['units'],
                                "game": f"{home} vs {away}"
                            })
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
                            approved_bets_for_parlay.append({
                                "description": f"{pick_side} {game['total']}",
                                "odds": odds,
                                "edge": total_res['edge'],
                                "units": total_res['units'],
                                "game": f"{home} vs {away}"
                            })
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
                        g_home = g['home']
                        g_away = g['away']
                        g_sport = g['sport']
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
    # TAB 2: PRIZEPICKS SCANNER
    # =========================================================================
    with tab2:
        st.header("🏆 PrizePicks Scanner")
        st.subheader("📋 Paste Props Board (Text or Screenshot)")
        st.markdown("Paste a list of player props in any supported format, or upload a screenshot.")
        
        input_method = st.radio("Input method:", ["📝 Paste Text", "📸 Upload Screenshot"], key="pp_input_method")
        
        pasted_props = []
        
        if input_method == "📝 Paste Text":
            pasted_text = st.text_area("Paste props here", height=200, 
                                       placeholder="Example formats:\n- 1\nBrandin Podziemski\nGSW vs LAC · PRA\n22.5\nNONE\nREVERSE\n\n- Anthony Edwards\nMIN - G\n5\nRebounds\nMore")
            if st.button("🔍 Analyze Pasted Props", type="primary", key="paste_analyze"):
                if pasted_text.strip():
                    with st.spinner("Analyzing pasted props..."):
                        pasted_props = parse_pasted_props(pasted_text)
                        if pasted_props:
                            st.success(f"Found {len(pasted_props)} props")
                        else:
                            st.warning("No props recognized. Check format.")
        
        elif input_method == "📸 Upload Screenshot":
            uploaded_file = st.file_uploader("Choose a screenshot", type=["png","jpg","jpeg"], key="pp_screenshot")
            if uploaded_file and st.button("🔍 Analyze Screenshot", type="primary", key="ss_analyze"):
                with st.spinner("Extracting text via OCR..."):
                    pasted_props = parse_props_from_image(
                        uploaded_file.getvalue(),
                        uploaded_file.name,
                        uploaded_file.type
                    )
                    if pasted_props:
                        st.success(f"Found {len(pasted_props)} props from screenshot")
                    else:
                        st.warning("No props found in image.")
        
        if pasted_props:
            st.markdown("---")
            st.subheader("✅ CLARITY APPROVED (Pasted)")
            approved_pasted = []
            rejected_pasted = []
            for prop in pasted_props:
                result = engine.analyze_prop(
                    prop["player"], prop["market"], prop["line"], prop["pick"],
                    [], prop.get("sport", "NBA"), -110, None, "HEALTHY", prop.get("opponent")
                )
                if result.get('units', 0) > 0:
                    approved_pasted.append((prop, result))
                else:
                    rejected_pasted.append((prop, result))
            
            if approved_pasted:
                for prop, res in approved_pasted:
                    st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                    st.caption(f"Edge: {res['raw_edge']:.1%} | Prob: {res['probability']:.1%} | Units: {res['units']} | Tier: {res['tier']}")
                    if res.get('season_warning'):
                        st.warning(res['season_warning'])
            else:
                st.info("No approved props found in pasted data.")
            
            if rejected_pasted:
                with st.expander(f"❌ REJECTED PROPS ({len(rejected_pasted)})"):
                    for prop, res in rejected_pasted:
                        st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                        st.caption(f"Reason: {res.get('reject_reason', 'Insufficient edge')}")
        
        st.markdown("---")
        st.subheader("🔍 Live PrizePicks API Scanner")
        col1, col2 = st.columns([2,1])
        with col1:
            selected_sports_pp = st.multiselect("Select sports", list(PropScanner.LEAGUE_IDS.keys()), default=["NBA","MLB"], key="pp_sports")
        with col2:
            scan_button = st.button("🔍 SCAN PRIZEPICKS", type="primary", use_container_width=True)
            stop_button = st.button("⏹️ STOP SCAN", use_container_width=True)
        
        if "scan_running" not in st.session_state:
            st.session_state.scan_running = False
            st.session_state.stop_event = threading.Event()
            st.session_state.scan_results = {"props":[],"games":[],"rejected":[]}
            st.session_state.scan_status = ""
        
        if scan_button:
            st.session_state.scan_running = True
            st.session_state.stop_event.clear()
            st.session_state.scan_results = {"props":[],"games":[],"rejected":[]}
            st.session_state.scan_status = "Starting scan..."
            st.rerun()
        if stop_button:
            st.session_state.stop_event.set()
            st.session_state.scan_running = False
            st.session_state.scan_status = "Scan stopped by user."
            st.rerun()
        
        if st.session_state.scan_running:
            status_placeholder = st.empty()
            def update_status(msg):
                st.session_state.scan_status = msg
                status_placeholder.info(msg)
            def add_result(bet):
                if bet.get('units',0) > 0:
                    if bet['type'] == 'player_prop':
                        st.session_state.scan_results["props"].append(bet)
                    else:
                        st.session_state.scan_results["games"].append(bet)
                else:
                    st.session_state.scan_results["rejected"].append(bet)
            with st.spinner("Scanning..."):
                engine.run_best_bets_scan(selected_sports_pp, stop_event=st.session_state.stop_event,
                                          progress_callback=update_status, result_callback=add_result)
            st.session_state.scan_running = False
            st.session_state.scan_status = "Scan complete!"
            st.rerun()
        
        if not st.session_state.scan_running:
            if st.session_state.scan_status:
                st.info(st.session_state.scan_status)
            props = st.session_state.scan_results.get("props", [])
            games = st.session_state.scan_results.get("games", [])
            rejected = st.session_state.scan_results.get("rejected", [])
            if props:
                st.subheader("✅ CLARITY APPROVED PLAYER PROPS (Live)")
                for i, bet in enumerate(props[:10],1):
                    st.markdown(f"**{i}. {bet['bet_line']}**")
                    st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                    if bet.get('season_warning'):
                        st.warning(bet['season_warning'])
            elif games:
                st.subheader("✅ CLARITY APPROVED GAME BETS (Live)")
                for i, bet in enumerate(games[:10],1):
                    st.markdown(f"**{i}. {bet['bet_line']}**")
                    st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                    if bet.get('season_warnings'):
                        for w in bet['season_warnings']:
                            st.warning(w)
            else:
                st.info("📭 No CLARITY approved slips available for the selected sports.")
            if rejected:
                with st.expander(f"❌ REJECTED BETS ({len(rejected)})"):
                    for bet in rejected:
                        st.markdown(f"**{bet['bet_line']}**")
                        if bet.get('reject_reason'):
                            st.caption(f"Reason: {bet['reject_reason']}")
                        else:
                            st.caption("Reason: Insufficient edge")

    # =========================================================================
    # TAB 3: SCANNERS & ACCURACY
    # =========================================================================
    with tab3:
        st.header("📊 Scanners & Accuracy Dashboard")
        scanner_tabs = st.tabs(["📈 Best Odds", "💰 Arbitrage", "🎯 Middles", "📊 Accuracy"])
        
        with scanner_tabs[0]:
            st.header("Best Odds Scanner")
            col1, col2 = st.columns([2,1])
            with col1:
                selected_sports_odds = st.multiselect("Select sports", ["NBA","MLB","NHL","NFL","TENNIS","PGA"], default=["NBA"], key="odds_sports")
            with col2:
                if st.button("🔍 SCAN BEST ODDS", type="primary", use_container_width=True):
                    with st.spinner("Scanning sportsbooks..."):
                        bets = engine.run_best_odds_scan(selected_sports_odds)
                        st.success(f"Found {len(bets)} +EV props!")
            if engine.scanned_bets.get("best_odds"):
                st.subheader("💰 Best +EV Props (Top 10)")
                for i, bet in enumerate(engine.scanned_bets["best_odds"], 1):
                    st.markdown(f"**{i}. {bet['player']} {bet['market']} {bet['pick']} {bet['line']}**")
                    st.caption(f"Odds: {bet['odds']} @ {bet['bookmaker']} | Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
        
        with scanner_tabs[1]:
            st.header("Arbitrage Detector")
            if st.button("🔍 SCAN FOR ARBITRAGE", type="primary"):
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
            if st.button("🔍 HUNT FOR MIDDLES", type="primary"):
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
    # TAB 4: PLAYER PROPS
    # =========================================================================
    with tab4:
        st.header("Manual Player Prop Analyzer (Real Rosters)")
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", all_sports, key="prop_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team (for context)", [""] + teams, key="prop_team") if sport in ["NBA","MLB","NHL","NFL","SOCCER_EPL","SOCCER_LALIGA","COLLEGE_BASKETBALL","COLLEGE_FOOTBALL"] else ""
            roster = engine.get_roster(sport, team) if team else engine._get_individual_sport_
