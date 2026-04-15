"""
CLARITY 18.0 ELITE – COMPLETE FIXED VERSION (PrizePicks Scanner Working)
All original tabs restored. PrizePicks scanner now shows real player names.
LightGBM optional (fallback to weighted average). Auto arbitrage scanner included.
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import json
import sqlite3
import re
import time
import requests
import hashlib
import threading
import warnings
from collections import defaultdict
import statistics

# Optional LightGBM
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
VERSION = "18.0 Elite (PrizePicks Fixed)"
BUILD_DATE = "2026-04-15"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# SPORT MODELS, CATEGORIES, STAT CONFIG
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 44.5, "home_advantage": 2.8},
    "PGA": {"distribution": "nbinom", "variance_factor": 1.10, "avg_total": 70.5, "home_advantage": 0.0},
    "TENNIS": {"distribution": "poisson", "variance_factor": 1.05, "avg_total": 22.0, "home_advantage": 0.0},
    "UFC": {"distribution": "poisson", "variance_factor": 1.20, "avg_total": 2.5, "home_advantage": 0.0}
}

SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"],
    "PGA": ["STROKES", "BIRDIES", "BOGEYS", "EAGLES", "DRIVING_DISTANCE", "GIR"],
    "TENNIS": ["ACES", "DOUBLE_FAULTS", "GAMES_WON", "TOTAL_GAMES", "BREAK_PTS"],
    "UFC": ["SIGNIFICANT_STRIKES", "TAKEDOWNS", "FIGHT_TIME", "SUB_ATTEMPTS"]
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
    "UFC": ["UFC"]
}

NBA_ROSTERS = {}
MLB_ROSTERS = {}
NHL_ROSTERS = {}

# =============================================================================
# REAL-TIME DATA FETCHERS
# =============================================================================
@st.cache_data(ttl=3600)
def fetch_player_stats_and_injury(player_name: str, sport: str, market: str, num_games: int = 8) -> Tuple[List[float], str]:
    league_map = {"NBA": 12, "MLB": 1, "NHL": 5, "NFL": 1}
    season_map = {"NBA": "2025-2026", "MLB": "2025", "NHL": "2025-2026", "NFL": "2025"}
    stat_map = {"PTS": "points", "REB": "rebounds", "AST": "assists", "STL": "steals", "BLK": "blocks"}
    if sport not in league_map:
        return [], "HEALTHY"
    headers = {"x-apisports-key": API_SPORTS_KEY}
    injury_status = "HEALTHY"
    stats = []
    try:
        url = "https://v1.api-sports.io/players"
        params = {"search": player_name, "league": league_map[sport], "season": season_map[sport]}
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code != 200:
            return [], "HEALTHY"
        players = r.json().get("response", [])
        if not players:
            return [], "HEALTHY"
        player_id = players[0]["player"]["id"]
        injury_url = "https://v1.api-sports.io/injuries"
        injury_params = {"player": player_id, "league": league_map[sport], "season": season_map[sport]}
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
        stats_params = {"player": player_id, "league": league_map[sport], "season": season_map[sport]}
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
        self.cache[cache_key] = result
        return result

# =============================================================================
# GAME SCANNER
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
    def fetch_todays_games(self, sports: List[str] = None) -> List[Dict]:
        if sports is None:
            sports = ["NBA","MLB","NHL","NFL"]
        all_games = []
        sport_keys = {"NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl"}
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
# PROP SCANNER (PRIZEPICKS) – FIXED VERSION
# =============================================================================
class PropScanner:
    BASE_URL = "https://api.prizepicks.com/projections"
    CORS_PROXY = "https://api.allorigins.win/raw?url="
    
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://app.prizepicks.com/',
    }
    LEAGUE_IDS = {
        "NBA": 7, "MLB": 8, "NHL": 9, "NFL": 6,
        "PGA": 12, "TENNIS": 14, "UFC": 16
    }
    MARKET_MAP = {
        "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
        "Strikeouts": "KS", "Hits Allowed": "HITS_ALLOWED",
        "Pass Yards": "PASS_YDS", "Rushing Yards": "RUSH_YDS",
        "Receiving Yards": "REC_YDS", "Hits": "HITS",
        "Total Bases": "TB", "Home Runs": "HR", "Runs": "RUNS",
        "RBI": "RBI", "Walks": "BB", "Stolen Bases": "SB",
        "Pitcher Strikeouts": "KS", "Pitching Outs": "OUTS",
        "Earned Runs": "ER", "Hitter Fantasy Score": "HITTER_FS",
        "Pitcher Fantasy Score": "PITCHER_FS", "Fantasy Score": "HITTER_FS",
        "Pts+Rebs+Asts": "PRA", "Pts+Rebs": "PR", "Pts+Asts": "PA",
        "Rebs+Asts": "RA", "Blks+Stls": "BLK_STL",
        "Strokes": "STROKES", "Birdies": "BIRDIES", "Bogeys": "BOGEYS",
        "Eagles": "EAGLES", "Driving Distance": "DRIVING_DISTANCE",
        "Greens in Regulation": "GIR",
        "Aces": "ACES", "Double Faults": "DOUBLE_FAULTS",
        "Games Won": "GAMES_WON", "Total Games": "TOTAL_GAMES",
        "Break Points": "BREAK_PTS",
        "Significant Strikes": "SIGNIFICANT_STRIKES", "Takedowns": "TAKEDOWNS",
        "Fight Time": "FIGHT_TIME", "Submission Attempts": "SUB_ATTEMPTS"
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    def fetch_prizepicks_props(self, sport: str = None, stop_event: threading.Event = None) -> List[Dict]:
        try:
            props = self._fetch_direct(sport, use_proxy=False, stop_event=stop_event)
            if props:
                st.success(f"✅ Direct API: {len(props)} props fetched")
                return props
        except Exception as e:
            st.warning(f"Direct API failed: {str(e)[:100]}")

        try:
            props = self._fetch_direct(sport, use_proxy=True, stop_event=stop_event)
            if props:
                st.info(f"🔄 AllOrigins Proxy: {len(props)} props fetched")
                return props
        except Exception as e:
            st.warning(f"Proxy failed: {str(e)[:100]}")

        st.warning("All sources failed. Using sample data.")
        return self._fallback_prizepicks_props(sport)

    def _fetch_direct(self, sport: str = None, use_proxy: bool = False, stop_event: threading.Event = None) -> List[Dict]:
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
                url = f"{self.CORS_PROXY}{url}"
            response = self.session.get(url, params=params, timeout=25)
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
            props.append({
                "source": "PrizePicks",
                "sport": sport,
                "player": player_name,
                "market": market,
                "line": float(line),
                "pick": "OVER",
                "odds": -110
            })
        return props

    def _fallback_prizepicks_props(self, sport: str = None) -> List[Dict]:
        props = []
        if sport in ["NBA", None]:
            sample_players = [
                ("LeBron James", "PTS", 25.5),
                ("Stephen Curry", "PTS", 28.5),
                ("Kevin Durant", "PTS", 26.5),
                ("Giannis Antetokounmpo", "PTS", 31.5),
                ("Luka Doncic", "PTS", 30.5),
            ]
            for player, market, line in sample_players:
                props.append({
                    "source": "Fallback",
                    "sport": "NBA",
                    "player": player,
                    "market": market,
                    "line": line,
                    "pick": "OVER",
                    "odds": -110
                })
        if sport in ["MLB", None]:
            sample_players = [
                ("Shohei Ohtani", "HR", 0.5),
                ("Aaron Judge", "HR", 0.5),
                ("Ronald Acuna Jr", "HITS", 1.5),
            ]
            for player, market, line in sample_players:
                props.append({
                    "source": "Fallback",
                    "sport": "MLB",
                    "player": player,
                    "market": market,
                    "line": line,
                    "pick": "OVER",
                    "odds": -110
                })
        return props

# =============================================================================
# ARBITRAGE & MIDDLE FUNCTIONS
# =============================================================================
def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return odds / 100 + 1
    return 100 / abs(odds) + 1

def find_arbitrage_2way(odds_a: Dict[str, float], odds_b: Dict[str, float], bankroll: float = 100.0) -> Dict:
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
        result.update({"stake_a": stake_a, "stake_b": stake_b, "profit": profit, "roi_pct": round(profit / bankroll * 100, 4)})
    return result

def find_middle(line_a: float, odds_a: float, line_b: float, odds_b: float, historical: List[float] = None) -> Dict:
    gap = abs(line_b - line_a)
    if gap < 0.5:
        return {"is_middle": False, "gap": gap}
    dec_a = american_to_decimal(odds_a)
    dec_b = american_to_decimal(odds_b)
    if historical:
        lo, hi = min(line_a, line_b), max(line_a, line_b)
        hits = sum(1 for m in historical if lo < m <= hi)
        mid_prob = hits / len(historical)
    else:
        mid_prob = min(gap * 0.03, 0.25)
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
    soft_dec = american_to_decimal(soft_odds)
    sharp_dec = american_to_decimal(sharp_odds)
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

# =============================================================================
# LIGHTGBM MODEL AND ENSEMBLE
# =============================================================================
class LightGBMPropModel:
    def __init__(self):
        self.model = None
        self.trained = False
    def train(self, X, y):
        if not LGB_AVAILABLE:
            return
        params = {"objective": "regression", "metric": "rmse", "num_leaves": 31, "learning_rate": 0.05, "verbose": -1}
        train_data = lgb.Dataset(X, label=y)
        self.model = lgb.train(params, train_data, num_boost_round=100, valid_sets=[train_data], callbacks=[lgb.early_stopping(10), lgb.log_evaluation(-1)])
        self.trained = True
    def predict(self, X):
        if self.trained and self.model:
            return self.model.predict(X)
        return None

class EnsemblePredictor:
    def __init__(self):
        self.ml_model = LightGBMPropModel()
        self.weight_ml = 0.6
        self.weight_wa = 0.4
        self.recent_ml_accuracy = 0.55
        self.recent_wa_accuracy = 0.55
    def update_weights(self, ml_correct: bool, wa_correct: bool):
        self.recent_ml_accuracy = self.recent_ml_accuracy * 0.95 + (1 if ml_correct else 0) * 0.05
        self.recent_wa_accuracy = self.recent_wa_accuracy * 0.95 + (1 if wa_correct else 0) * 0.05
        total = self.recent_ml_accuracy + self.recent_wa_accuracy
        if total > 0:
            self.weight_ml = self.recent_ml_accuracy / total
            self.weight_wa = self.recent_wa_accuracy / total
    def predict(self, ml_proba: float, wa_proba: float) -> float:
        if ml_proba is None:
            return wa_proba
        return self.weight_ml * ml_proba + self.weight_wa * wa_proba

ensemble = EnsemblePredictor()

# =============================================================================
# CLARITY ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = None
        self.game_scanner = GameScanner(ODDS_API_KEY)
        self.prop_scanner = PropScanner()
        self.season_context = SeasonContextEngine()
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
        self.daily_loss_limit = 200.0
        self.max_unit_size = 0.05
        self.correlation_threshold = 0.12
       
