"""
CLARITY 18.0 ELITE – AUTO-TUNE ENHANCED FOR PLAYER PROPS
- Imports numbered player prop slips in Auto-Tune tab
- Best Odds Scanner note: limited data, use Paste Props for reliability
- Live PrizePicks API may fail; fallback to paste method
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

VERSION = "18.0 Elite (Auto-Tune Props)"
BUILD_DATE = "2026-04-16"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"

# =============================================================================
# SPORT MODELS (abbreviated for brevity – same as before)
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
# HARDCODED TEAMS (abbreviated – full list in your file)
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
# FALLBACK NBA ROSTERS (abbreviated – full list in your file)
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
# OPPONENT STRENGTH CACHE (unchanged)
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
# REST & INJURY DETECTOR (unchanged)
# =============================================================================
class RestInjuryDetector:
    def __init__(self):
        self.schedule_cache = {}
    def get_rest_fade(self, sport: str, team: str) -> Tuple[float, str]:
        if sport not in ["
