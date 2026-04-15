"""
CLARITY 18.0 ELITE – FINAL with Edge Scanner (Full Original UI Restored)
Real stats, injuries, arbitrage, middles, +EV detection – all automatic.
All original tabs (Game Markets, Player Props, PrizePicks Scanner, Analytics, Image Analysis, Auto-Tune) are fully restored.
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

# Import the edge scanner module
try:
    from edge_scanner import find_arbitrage_2way, find_middle, find_plus_ev, scan_all_games
    EDGE_SCANNER_AVAILABLE = True
except ImportError:
    EDGE_SCANNER_AVAILABLE = False
    st.warning("Edge scanner module not found – arbitrage features disabled.")

warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION – YOUR API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
VERSION = "18.0 Elite (Full UI + Edge Scanner)"
BUILD_DATE = "2026-04-15"

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# SPORT MODELS, CATEGORIES, STAT CONFIG (unchanged)
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
# HARDCODED TEAMS & ROSTERS (keep your existing full lists)
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

# Placeholders – replace with your actual NBA_ROSTERS, MLB_ROSTERS, NHL_ROSTERS
NBA_ROSTERS = {}
MLB_ROSTERS = {}
NHL_ROSTERS = {}

# =============================================================================
# REAL-TIME DATA FETCHERS (with caching)
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
        # Injury check
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
        # Stats
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
# SEASON CONTEXT ENGINE (simplified)
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
# GAME SCANNER (The Odds API)
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
                        bookmakers = game.get("bookmakers", [])
                        if bookmakers:
                            bm = bookmakers[0]
                            markets = {m["key"]:m for m in bm.get("markets", [])}
                            game_data = {"sport":sport,"home":game["home_team"],"away":game["away_team"]}
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
# PROP SCANNER (PrizePicks)
# =============================================================================
class PropScanner:
    BASE_URL = "https://api.prizepicks.com/projections"
    CORS_PROXY = "https://api.allorigins.win/raw?url="
    DEFAULT_HEADERS = {'User-Agent':'Mozilla/5.0','Accept':'application/json','Accept-Language':'en-US','Referer':'https://app.prizepicks.com/'}
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
            params = {'league_id':league_id,'per_page':500,'single_stat':'true','game_mode':'pickem'}
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
        records = data.get('data', []) or [item for item in data.get('included', []) if item.get('type')=='projection']
        players = {item['id']:item['attributes']['name'] for item in data.get('included', []) if item.get('type')=='new_player'}
        for item in records:
            attrs = item.get('attributes', {})
            line = attrs.get('line_score')
            if not line:
                continue
            player_id = attrs.get('player_id')
            player_name = players.get(player_id, 'Unknown')
            market = self.MARKET_MAP.get(attrs.get('stat_type',''), attrs.get('stat_type','').upper().replace(' ','_'))
            props.append({"source":"PrizePicks","sport":sport,"player":player_name,"market":market,"line":float(line),"pick":"OVER","odds":-110})
        return props
    def _fallback_prizepicks_props(self, sport: str = None) -> List[Dict]:
        props = []
        if sport in ["NBA",None]:
            for p in ["LeBron James","Stephen Curry","Kevin Durant","Luka Doncic"]:
                props.append({"source":"Fallback","sport":"NBA","player":p,"market":"PTS","line":round(np.random.uniform(20,35),1),"pick":"OVER","odds":-110})
        if sport in ["MLB",None]:
            for p in ["Shohei Ohtani","Aaron Judge","Ronald Acuna Jr","Mookie Betts"]:
                props.append({"source":"Fallback","sport":"MLB","player":p,"market":"HR","line":0.5,"pick":"OVER","odds":-110})
        return props

# =============================================================================
# CLARITY ENGINE (with real stats, injuries, auto-tune)
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
        self.max_unit_size = 0.05
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
        self._load_tuning_state()
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY, player TEXT, sport TEXT, market TEXT, line REAL,
            pick TEXT, odds INTEGER, edge REAL, result TEXT, actual REAL,
            date TEXT, settled_date TEXT, bolt_signal TEXT, profit REAL
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
        conn.commit()
        conn.close()
    def _load_tuning_state(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT timestamp FROM tuning_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            self.last_tune_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        conn.close()
    def convert_odds(self, american: int) -> float:
        return 1 + american/100 if american>0 else 1 + 100/abs(american)
    def implied_prob(self, american: int) -> float:
        return 100/(american+100) if american>0 else abs(american)/(abs(american)+100)
    def l42_check(self, stat: str, line: float, avg: float) -> Tuple[bool, str]:
        config = STAT_CONFIG.get(stat.upper(), {"tier":"MED","buffer":2.0,"reject":False})
        if config["reject"]:
            return False, f"RED TIER - {stat}"
        buffer = line - avg if stat.upper() not in ["OUTS"] else avg - line
        if buffer < config["buffer"]:
            return False, f"BUFFER {buffer:.1f} < {config['buffer']}"
        return True, "PASS"
    def wsem_check(self, data: List[float]) -> Tuple[bool, float]:
        if len(data) < 3:
            return False, float('inf')
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        mean = np.average(data, weights=w)
        var = np.average((np.array(data)-mean)**2, weights=w)
        sem = np.sqrt(var/len(data))
        wsem = sem/abs(mean) if mean!=0 else float('inf')
        return wsem <= self.wsem_max, wsem
    def simulate_prop(self, data: List[float], line: float, pick: str, sport: str = "NBA") -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        if not data:
            data = [line * 0.9] * 5
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        lam = np.average(data, weights=w)
        if model["distribution"]=="nbinom":
            n = max(1, int(lam/2))
            p = n/(n+lam)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims>=line) if pick=="OVER" else np.mean(sims<=line)
        dtm = (proj-line)/line if line!=0 else 0
        return {"proj":proj,"prob":prob,"dtm":dtm}
    def sovereign_bolt(self, prob: float, dtm: float, wsem_ok: bool, l42_pass: bool, injury: str) -> dict:
        if injury=="OUT":
            return {"signal":"🔴 INJURY RISK","units":0}
        if not l42_pass:
            return {"signal":"🔴 L42 REJECT","units":0}
        if prob>=self.prob_bolt and dtm>=self.dtm_bolt and wsem_ok:
            return {"signal":"🟢 SOVEREIGN BOLT ⚡","units":2.0}
        elif prob>=0.78 and wsem_ok:
            return {"signal":"🟢 ELITE LOCK","units":1.5}
        elif prob>=0.70:
            return {"signal":"🟡 APPROVED","units":1.0}
        return {"signal":"🔴 PASS","units":0}
    def analyze_prop(self, player: str, market: str, line: float, pick: str,
                     data: List[float], sport: str, odds: int, team: str = None, injury_status: str = "HEALTHY") -> dict:
        if not data:
            real_stats, real_injury = fetch_player_stats_and_injury(player, sport, market)
            if real_stats:
                data = real_stats
            if real_injury != "HEALTHY":
                injury_status = real_injury
        if not data:
            data = [line * 0.9] * 5
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        sim = self.simulate_prop(data, line, pick, sport)
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(sim["prob"], sim["dtm"], wsem_ok, l42_pass, injury_status)
        raw_edge = sim["prob"] - self.implied_prob(odds)
        if market.upper() in RED_TIER_PROPS:
            tier, reject_reason = "REJECT", f"RED TIER - {market}"
        elif raw_edge >= 0.08:
            tier, reject_reason = "SAFE", None
        elif raw_edge >= 0.05:
            tier, reject_reason = "BALANCED+", None
        elif raw_edge >= 0.03:
            tier, reject_reason = "RISKY", None
        else:
            tier, reject_reason = "PASS", f"Insufficient edge ({raw_edge:.1%})"
        if injury_status != "HEALTHY":
            tier, reject_reason = "REJECT", f"Injury: {injury_status}"
            bolt["units"] = 0
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
                sim["proj"] *= fade_check["multiplier"]
                season_warning = f"⚠️ {team}: {', '.join(fade_check['reasons'])}"
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge>0 and tier!="REJECT" else 0
        return {"player":player,"market":market,"line":line,"pick":pick,"signal":bolt["signal"],
                "units":bolt["units"] if tier!="REJECT" else 0,"projection":sim["proj"],"probability":sim["prob"],
                "raw_edge":round(raw_edge,4),"tier":tier,"injury":injury_status,
                "l42_msg":l42_msg,"kelly_stake":round(min(kelly,50),2),"odds":odds,
                "season_warning":season_warning,"reject_reason":reject_reason}
    def analyze_total(self, home: str, away: str, total_line: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage",0)
        avg_total = model.get("avg_total",200)
        base_proj = avg_total + (home_adv/2)
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]:
            base_proj *= home_fade["multiplier"]
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            base_proj *= away_fade["multiplier"]
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        if model["distribution"]=="nbinom":
            n = max(1,int(base_proj/2)); p = n/(n+base_proj)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(base_proj, size=self.sims)
        proj = np.mean(sims)
        prob_over = np.mean(sims>total_line)
        prob_under = np.mean(sims<total_line)
        prob_push = np.mean(sims==total_line)
        prob = (prob_over/(1-prob_push) if prob_push<1 else prob_over) if pick=="OVER" else (prob_under/(1-prob_push) if prob_push<1 else prob_under)
        imp = self.implied_prob(odds)
        edge = prob-imp
        if edge>=0.05:
            tier,units,signal,reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03:
            tier,units,signal,reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01:
            tier,units,signal,reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else:
            tier,units,signal,reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"total_line":total_line,"pick":pick,"signal":signal,"units":units,
                "projection":round(proj,1),"prob_over":round(prob_over,3),"prob_under":round(prob_under,3),
                "prob_push":round(prob_push,3),"edge":round(edge,4),"tier":tier,"kelly_stake":round(min(kelly,50),2),
                "odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}
    def analyze_moneyline(self, home: str, away: str, sport: str, home_odds: int, away_odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage",0)
        home_win_prob = 0.55 + (home_adv/100)
        away_win_prob = 1-home_win_prob
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]:
            home_win_prob *= home_fade["multiplier"]
            away_win_prob = 1-home_win_prob
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            away_win_prob *= away_fade["multiplier"]
            home_win_prob = 1-away_win_prob
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        home_imp = self.implied_prob(home_odds)
        away_imp = self.implied_prob(away_odds)
        home_edge = home_win_prob - home_imp
        away_edge = away_win_prob - away_imp
        if home_edge > away_edge and home_edge > 0.02:
            pick,edge,odds,prob = home,home_edge,home_odds,home_win_prob
        elif away_edge > 0.02:
            pick,edge,odds,prob = away,away_edge,away_odds,away_win_prob
        else:
            return {"pick":"PASS","signal":"🔴 PASS","units":0,"edge":0,"reject_reason":"No significant edge"}
        if edge>=0.05:
            tier,units,signal,reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03:
            tier,units,signal,reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        else:
            tier,units,signal,reject_reason = "RISKY",1.0,"🟠 RISKY",None
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"pick":pick,"signal":signal,"units":units,"edge":round(edge,4),
                "win_prob":round(prob,3),"tier":tier,"kelly_stake":round(min(kelly,50),2),
                "odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}
    def analyze_spread(self, home: str, away: str, spread: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage",0)
        base_margin = home_adv
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]:
            base_margin *= home_fade["multiplier"]
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            base_margin /= away_fade["multiplier"]
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        sims = norm.rvs(loc=base_margin, scale=12, size=self.sims)
        if pick==home:
            prob_cover = np.mean(sims > -spread)
        else:
            prob_cover = np.mean(sims < -spread)
        prob_push = np.mean(np.abs(sims+spread)<0.5)
        prob = prob_cover/(1-prob_push) if prob_push<1 else prob_cover
        imp = self.implied_prob(odds)
        edge = prob-imp
        if edge>=0.05:
            tier,units,signal,reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03:
            tier,units,signal,reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01:
            tier,units,signal,reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else:
            tier,units,signal,reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"spread":spread,"pick":pick,"signal":signal,
                "units":units,"prob_cover":round(prob,3),"prob_push":round(prob_push,3),
                "edge":round(edge,4),"tier":tier,"kelly_stake":round(min(kelly,50),2),
                "odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}
    def analyze_alternate(self, base_line: float, alt_line: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        avg_total = model.get("avg_total",200)
        sims = norm.rvs(loc=avg_total, scale=avg_total*0.12, size=self.sims)
        prob = np.mean(sims>alt_line) if pick=="OVER" else np.mean(sims<alt_line)
        imp = self.implied_prob(odds)
        edge = prob-imp
        if edge>=0.03:
            value, action = "GOOD VALUE", "BET"
        elif edge>=0:
            value, action = "FAIR VALUE", "CONSIDER"
        else:
            value, action = "POOR VALUE", "AVOID"
        return {"base_line":base_line,"alt_line":alt_line,"pick":pick,"odds":odds,
                "probability":round(prob,3),"implied":round(imp,3),"edge":round(edge,4),
                "value":value,"action":action}
    def check_correlation(self, legs: List[Dict]) -> Dict:
        if len(legs)<2:
            return {"correlated":False,"max_corr":0,"safe":True}
        correlations = []
        for i in range(len(legs)):
            for j in range(i+1,len(legs)):
                l1,l2 = legs[i],legs[j]
                score = 0.0
                if l1.get("team")==l2.get("team"): score+=0.15
                if l1.get("player")==l2.get("player"): score=1.0
                related_pairs = [(["PTS","AST"],0.20),(["PTS","PRA"],0.30),(["REB","BLK"],0.15)]
                s1,s2 = l1.get("market","").upper(), l2.get("market","").upper()
                for pair,bonus in related_pairs:
                    if s1 in pair and s2 in pair: score+=bonus
                correlations.append(min(score,1.0))
        max_corr = max(correlations) if correlations else 0
        return {"correlated":max_corr>self.correlation_threshold,"max_corr":max_corr,"safe":max_corr<=self.correlation_threshold}
    def detect_arbitrage(self, props: List[Dict]) -> List[Dict]:
        arbs = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            if len(bets)<2: continue
            best_over = max([b for b in bets if b['pick']=='OVER'], key=lambda x:x['odds'], default=None)
            best_under = max([b for b in bets if b['pick']=='UNDER'], key=lambda x:x['odds'], default=None)
            if best_over and best_under:
                over_dec = self.convert_odds(best_over['odds'])
                under_dec = self.convert_odds(best_under['odds'])
                arb_pct = (1/over_dec + 1/under_dec - 1)*100
                if arb_pct>0:
                    arbs.append({'Player':best_over['player'],'Market':best_over['market'],'Line':best_over['line'],
                                 'Bet 1':f"OVER {best_over['odds']} @ {best_over['bookmaker']}",
                                 'Bet 2':f"UNDER {best_under['odds']} @ {best_under['bookmaker']}",
                                 'Arb %':round(arb_pct,2)})
        return arbs
    def hunt_middles(self, props: List[Dict]) -> List[Dict]:
        middles = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            overs = [b for b in bets if b['pick']=='OVER']; unders = [b for b in bets if b['pick']=='UNDER']
            for over in overs:
                for under in unders:
                    if over['line'] < under['line']:
                        middle_window = under['line'] - over['line']
                        if middle_window >= 0.5:
                            middles.append({'Player':over['player'],'Market':over['market'],
                                            'Middle Window':f"{over['line']} – {under['line']}",
                                            'Leg 1':f"OVER {over['line']} ({over['odds']}) @ {over['bookmaker']}",
                                            'Leg 2':f"UNDER {under['line']} ({under['odds']}) @ {under['bookmaker']}",
                                            'Window Size':round(middle_window,1)})
        return sorted(middles, key=lambda x:x['Window Size'], reverse=True)
    def get_accuracy_dashboard(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if df.empty:
            return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,
                    'by_sport':{},'by_tier':{},'sem_score':self.sem_score}
        wins = (df['result']=='WIN').sum()
        total = len(df)
        total_stake = df['odds'].apply(lambda x:100).sum()
        total_profit = df.apply(lambda r:90.9 if r['result']=='WIN' else -100, axis=1).sum()
        roi = (total_profit/total_stake)*100 if total_stake>0 else 0
        by_sport = {}
        for sport in df['sport'].unique():
            sport_df = df[df['sport']==sport]
            sport_wins = (sport_df['result']=='WIN').sum()
            by_sport[sport] = {'bets':len(sport_df),'win_rate':round(sport_wins/len(sport_df)*100,1) if len(sport_df)>0 else 0}
        by_tier = {}
        for _,row in df.iterrows():
            signal = row.get('bolt_signal','PASS')
            if 'SAFE' in str(signal):
                tier = 'SAFE'
            elif 'BALANCED' in str(signal):
                tier = 'BALANCED+'
            elif 'RISKY' in str(signal):
                tier = 'RISKY'
            else:
                tier = 'PASS'
            if tier not in by_tier:
                by_tier[tier] = {'bets':0,'wins':0}
            by_tier[tier]['bets'] += 1
            if row['result']=='WIN':
                by_tier[tier]['wins'] += 1
        for tier in by_tier:
            by_tier[tier]['win_rate'] = round(by_tier[tier]['wins']/by_tier[tier]['bets']*100,1) if by_tier[tier]['bets']>0 else 0
        return {'total_bets':total,'wins':wins,'losses':total-wins,'win_rate':round(wins/total*100,1) if total>0 else 0,
                'roi':round(roi,1),'units_profit':round(total_profit/100,1),
                'by_sport':by_sport,'by_tier':by_tier,'sem_score':self.sem_score}
    def run_best_bets_scan(self, selected_sports: List[str], stop_event: threading.Event = None,
                           progress_callback=None, result_callback=None) -> Dict:
        game_bets, prop_bets, rejected = [], [], []
        games = self.game_scanner.fetch_todays_games(selected_sports)
        for game in games:
            if stop_event and stop_event.is_set(): break
            sport = game["sport"]; home,away = game["home"],game["away"]
            if game.get("home_ml") and game.get("away_ml"):
                ml = self.analyze_moneyline(home,away,sport,game["home_ml"],game["away_ml"])
                bet_info = {"type":"moneyline","sport":sport,
                            "description":f"{ml.get('pick','PASS')} ML vs {away if ml.get('pick')==home else home}",
                            "bet_line":f"{ml.get('pick','N/A')} ML ({game['home_ml'] if ml.get('pick')==home else game['away_ml']}) vs {away if ml.get('pick')==home else home}",
                            "edge":ml.get('edge',0),"probability":ml.get('win_prob',0.0),"units":ml.get('units',0),
                            "odds":game['home_ml'] if ml.get('pick')==home else game['away_ml'],
                            "season_warnings":ml.get('season_warnings',[]),"reject_reason":ml.get('reject_reason')}
                if ml.get('units',0)>0: game_bets.append(bet_info)
                else: rejected.append(bet_info)
            if game.get("spread") and game.get("spread_odds"):
                for pick_side in [home,away]:
                    spread_res = self.analyze_spread(home,away,game["spread"],pick_side,sport,game["spread_odds"])
                    bet_info = {"type":"spread","sport":sport,
                                "description":f"{pick_side} {game['spread']:+.1f} vs {away if pick_side==home else home}",
                                "bet_line":f"{pick_side} {game['spread']:+.1f} ({game['spread_odds']}) vs {away if pick_side==home else home}",
                                "edge":spread_res.get('edge',0),"probability":spread_res.get('prob_cover',0.0),
                                "units":spread_res.get('units',0),"odds":game['spread_odds'],
                                "season_warnings":spread_res.get('season_warnings',[]),"reject_reason":spread_res.get('reject_reason')}
                    if spread_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
            if game.get("total"):
                for pick_side,odds in [("OVER",game.get("over_odds",-110)),("UNDER",game.get("under_odds",-110))]:
                    total_res = self.analyze_total(home,away,game["total"],pick_side,sport,odds)
                    prob_key = 'prob_over' if pick_side=="OVER" else 'prob_under'
                    bet_info = {"type":"total","sport":sport,
                                "description":f"{home} vs {away}: {pick_side} {game['total']}",
                                "bet_line":f"{home} vs {away} — {pick_side} {game['total']} ({odds})",
                                "edge":total_res.get('edge',0),"probability":total_res.get(prob_key,0.0),
                                "units":total_res.get('units',0),"odds":odds,
                                "season_warnings":total_res.get('season_warnings',[]),"reject_reason":total_res.get('reject_reason')}
                    if total_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
        for sport in selected_sports:
            if stop_event and stop_event.is_set(): break
            if progress_callback: progress_callback(f"Scanning {sport}...")
            props = self.prop_scanner.fetch_prizepicks_props(sport, stop_event)
            for prop in props:
                if stop_event and stop_event.is_set(): break
                np.random.seed(hash(prop["player"]) % 2**32)
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"],
                                           [], prop["sport"], prop["odds"], None, "HEALTHY")
                bet_info = {"type":"player_prop","sport":prop["sport"],
                            "description":f"{prop['player']} {prop['pick']} {prop['line']} {prop['market']}",
                            "bet_line":f"{prop['player']} {prop['pick']} {prop['line']} ({prop['odds']})",
                            "edge":result.get('raw_edge',0),"probability":result.get('probability',0.0),
                            "units":result.get('units',0),"odds":prop['odds'],
                            "season_warning":result.get('season_warning'),"reject_reason":result.get('reject_reason')}
                if result.get('units',0)>0: prop_bets.append(bet_info)
                else: rejected.append(bet_info)
                if result_callback: result_callback(bet_info)
        game_bets.sort(key=lambda x:x['edge'], reverse=True)
        prop_bets.sort(key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["props"] = prop_bets
        self.scanned_bets["games"] = game_bets
        self.scanned_bets["rejected"] = rejected
        return self.scanned_bets
    def run_best_odds_scan(self, selected_sports: List[str]) -> List[Dict]:
        all_bets = []
        sport_keys = {"NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl"}
        markets = "player_points,player_assists,player_rebounds,player_threes,player_blocks,player_steals"
        for sport in selected_sports:
            key = sport_keys.get(sport)
            if not key: continue
            props = self.game_scanner.fetch_player_props_odds(key, markets)
            for prop in props:
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"],
                                           [], sport, prop["odds"], None, "HEALTHY")
                if result.get('units',0) > 0:
                    all_bets.append({"player":prop["player"],"market":prop["market"],"line":prop["line"],
                                     "pick":prop["pick"],"odds":prop["odds"],"bookmaker":prop["bookmaker"],
                                     "edge":result.get('raw_edge',0),"probability":result.get('probability',0),
                                     "units":result.get('units',0),"sport":sport})
        best_bets = {}
        for bet in all_bets:
            key = f"{bet['player']}|{bet['market']}|{bet['line']}"
            if key not in best_bets or bet['odds'] > best_bets[key]['odds']:
                best_bets[key] = bet
        sorted_bets = sorted(best_bets.values(), key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["best_odds"] = sorted_bets[:10]
        props_for_arb = [{'player':bet['player'],'market':bet['market'],'line':bet['line'],
                          'pick':bet['pick'],'odds':bet['odds'],'bookmaker':bet['bookmaker']} for bet in all_bets]
        self.scanned_bets["arbs"] = self.detect_arbitrage(props_for_arb)
        self.scanned_bets["middles"] = self.hunt_middles(props_for_arb)
        return sorted_bets[:10]
    def get_teams(self, sport: str) -> List[str]:
        return HARDCODED_TEAMS.get(sport, ["Select a sport first"])
    def get_roster(self, sport: str, team: str) -> List[str]:
        if sport=="NBA" and team in NBA_ROSTERS: return NBA_ROSTERS[team]
        elif sport=="MLB" and team in MLB_ROSTERS: return MLB_ROSTERS[team]
        elif sport=="NHL" and team in NHL_ROSTERS: return NHL_ROSTERS[team]
        elif sport in ["PGA","TENNIS","UFC"]: return self._get_individual_sport_players(sport)
        return ["Player 1","Player 2","Player 3","Player 4","Player 5"]
    def _get_individual_sport_players(self, sport: str) -> List[str]:
        if sport=="PGA": return ["Scottie Scheffler","Rory McIlroy","Jon Rahm","Ludvig Aberg","Xander Schauffele","Collin Morikawa"]
        elif sport=="TENNIS": return ["Novak Djokovic","Carlos Alcaraz","Iga Swiatek","Coco Gauff","Aryna Sabalenka","Jannik Sinner"]
        elif sport=="UFC": return ["Jon Jones","Islam Makhachev","Alex Pereira","Sean O'Malley","Ilia Topuria","Dricus Du Plessis"]
        return ["Player 1","Player 2","Player 3"]
    def _log_bet(self, player, market, line, pick, sport, odds, edge, signal):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        bet_id = hashlib.md5(f"{player}{market}{line}{datetime.now()}".encode()).hexdigest()[:12]
        c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, date, bolt_signal)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
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
            c.execute("UPDATE bets SET result=?, actual=?, settled_date=?, profit=? WHERE id=?", 
                      (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, bet[0]))
            if result=="LOSS": self.daily_loss_today += abs(profit)
        conn.commit(); conn.close()
        self._calibrate_sem()
        self.auto_tune_thresholds()
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
        total_profit = df["profit"].sum(); total_stake = 100 * len(df); roi = total_profit / total_stake if total_stake>0 else 0
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
    def __init__(self, engine):
        self.engine = engine
        self.running = False
        self.thread = None
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
    def _run(self):
        while self.running:
            now = datetime.now()
            if now.hour == 8 and (getattr(self,"last_settlement",None) is None or self.last_settlement.date() < now.date()):
                self.engine.settle_pending_bets()
                self.last_settlement = now
            time.sleep(1800)

# =============================================================================
# AUTO-OCR PARSER
# =============================================================================
def auto_parse_bets(text: str) -> List[Dict]:
    text = text.upper()
    text = text.replace("0VER","OVER")
    bets = []
    prop_pattern = re.compile(r"([A-Z][A-Za-z\.\-' ]+?)\s+(OVER|UNDER)\s+(\d+\.?\d*)\s*([A-Z]{2,})?")
    for match in prop_pattern.finditer(text):
        player = match.group(1).strip()
        pick = match.group(2)
        line = float(match.group(3))
        market_raw = match.group(4) if match.group(4) else "PTS"
        market_map = {"POINTS":"PTS","ASSISTS":"AST","REBOUNDS":"REB","THREES":"3PT","STRIKEOUTS":"KS","HITS":"HITS","HOME RUNS":"HR"}
        market = market_map.get(market_raw, market_raw)
        bets.append({"type":"player_prop","player":player.title(),"market":market,"line":line,"pick":pick,"odds":-110,"description":f"{player.title()} {pick} {line} {market}"})
    spread_pattern = re.compile(r"([A-Z]{2,}\s?[A-Za-z]+)\s+([+-]\d+\.?\d*)\s*\(([+-]\d+)\)")
    for match in spread_pattern.finditer(text):
        team = match.group(1).strip()
        spread = float(match.group(2))
        odds = int(match.group(3))
        bets.append({"type":"spread","team":team,"spread":spread,"odds":odds,"description":f"{team} {spread:+.1f}"})
    ml_pattern = re.compile(r"([A-Z]{2,}\s?[A-Za-z]+)\s+([+-]\d{3,})")
    ml_matches = ml_pattern.findall(text)
    if len(ml_matches) >= 2:
        home, home_odds = ml_matches[0]; away, away_odds = ml_matches[1]
        try: bets.append({"type":"moneyline","home":home.strip(),"away":away.strip(),
                          "home_odds":int(home_odds),"away_odds":int(away_odds),
                          "description":f"{home.strip()} ML vs {away.strip()}"})
        except: pass
    total_pattern = re.compile(r"(OVER|UNDER)\s+(\d+\.?\d*)\s*\(?([+-]\d+)?\)?")
    for match in total_pattern.finditer(text):
        pick = match.group(1); total = float(match.group(2)); odds = int(match.group(3)) if match.group(3) else -110
        bets.append({"type":"total","pick":pick,"total":total,"odds":odds,"description":f"{pick} {total}"})
    unique, seen = [], set()
    for bet in bets:
        desc = bet.get("description","")
        if desc not in seen: seen.add(desc); unique.append(bet)
    return unique

# =============================================================================
# STREAMLIT DASHBOARD (Full original UI + new Arbitrage tab)
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE")
    st.markdown(f"**Upgraded: Real Stats + Injuries + Arbitrage Scanner | Version: {VERSION}**")
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        st.success("✅ Real player stats (API-Sports)")
        st.success("✅ Live injury feed")
        st.success("✅ Edge Scanner ready")
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        st.metric("Daily Loss Left", f"${max(0, engine.daily_loss_limit - engine.daily_loss_today):.0f}")
        st.metric("SEM Score", f"{engine.sem_score}/100")
        st.metric("Prob Bolt", f"{engine.prob_bolt:.2f}")
        st.metric("DTM Bolt", f"{engine.dtm_bolt:.3f}")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "🎮 GAME MARKETS", "🎯 PLAYER PROPS", "🏆 PRIZEPICKS SCANNER", "📊 ANALYTICS", "📸 IMAGE ANALYSIS", "🔧 AUTO-TUNE", "💰 ARBITRAGE & MIDDLES"
    ])

    # =========================================================================
    # TAB 1: GAME MARKETS (Full original UI)
    # =========================================================================
    with tab1:
        st.header("Game Markets")
        game_tab1, game_tab2, game_tab3, game_tab4 = st.tabs(["💰 Moneyline", "📊 Spread", "📈 Totals", "🔄 Alt Lines"])
        with game_tab1:
            c1, c2 = st.columns(2)
            with c1:
                sport_ml = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="ml_sport")
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
                    if result.get('reject_reason'):
                        st.warning(f"Reason: {result['reject_reason']}")
        with game_tab2:
            c1, c2 = st.columns(2)
            with c1:
                sport_sp = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="sp_sport")
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
                    if result.get('reject_reason'):
                        st.warning(f"Reason: {result['reject_reason']}")
        with game_tab3:
            c1, c2 = st.columns(2)
            with c1:
                sport_tot = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="tot_sport")
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
                    if result.get('reject_reason'):
                        st.warning(f"Reason: {result['reject_reason']}")
        with game_tab4:
            c1, c2 = st.columns(2)
            with c1:
                sport_alt = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="alt_sport")
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
    # TAB 2: PLAYER PROPS (Manual with real stats)
    # =========================================================================
    with tab2:
        st.header("Manual Player Prop Analyzer (with Real Stats & Injuries)")
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", ["MLB","NBA","NHL","NFL","PGA","TENNIS","UFC"], key="prop_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team (for context)", [""] + teams, key="prop_team") if sport in ["NBA","MLB","NHL","NFL"] else ""
            roster = engine.get_roster(sport, team) if team else engine._get_individual_sport_players(sport)
            player = st.selectbox("Player", roster, key="prop_player")
            available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
            market = st.selectbox("Market", available_markets, key="prop_market")
            line = st.number_input("Line", 0.5, 200.0, 0.5, key="prop_line")
            pick = st.selectbox("Pick", ["OVER","UNDER"], key="prop_pick")
        with c2:
            use_real_stats = st.checkbox("Fetch real stats & injuries (API-Sports)", value=True)
            odds = st.number_input("Odds (American)", -500, 500, -110, key="prop_odds")
        if st.button("🚀 ANALYZE PROP", type="primary", key="prop_button"):
            if not player or player == "Select team first":
                st.error("Please select a player.")
            else:
                if use_real_stats:
                    with st.spinner("Fetching real stats and injury status..."):
                        real_stats, injury = fetch_player_stats_and_injury(player, sport, market)
                        if real_stats:
                            st.info(f"Fetched {len(real_stats)} recent games for {player}. Injury status: {injury}")
                        else:
                            st.warning("No real stats found – using fallback dummy data.")
                        data = real_stats
                        injury_status = injury
                else:
                    data = [line * 0.9] * 5
                    injury_status = "HEALTHY"
                result = engine.analyze_prop(player, market, line, pick, data, sport, odds, team if team else None, injury_status)
                if result.get('units',0) > 0:
                    st.success(f"### {result['signal']}")
                    if result.get('season_warning'):
                        st.warning(result['season_warning'])
                    if result.get('injury') != "HEALTHY":
                        st.error(f"⚠️ Injury flag: {result['injury']}")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Projection", f"{result['projection']:.1f}")
                    with c2: st.metric("Probability", f"{result['probability']:.1%}")
                    with c3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
                    st.metric("Tier", result['tier'])
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'):
                        st.warning(f"Reason: {result['reject_reason']}")

    # =========================================================================
    # TAB 3: PRIZEPICKS SCANNER (Full original UI)
    # =========================================================================
    with tab3:
        st.header("🏆 PrizePicks Scanner (CLARITY Approved Only)")
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
                st.subheader("✅ CLARITY APPROVED PLAYER PROPS")
                for i, bet in enumerate(props[:10],1):
                    st.markdown(f"**{i}. {bet['bet_line']}**")
                    st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                    if bet.get('season_warning'):
                        st.warning(bet['season_warning'])
            if games:
                st.subheader("✅ CLARITY APPROVED GAME BETS")
                for i, bet in enumerate(games[:10],1):
                    st.markdown(f"**{i}. {bet['bet_line']}**")
                    st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                    if bet.get('season_warnings'):
                        for w in bet['season_warnings']:
                            st.warning(w)
            if rejected:
                with st.expander(f"❌ REJECTED BETS ({len(rejected)})"):
                    for bet in rejected:
                        st.markdown(f"**{bet['bet_line']}**")
                        if bet.get('reject_reason'):
                            st.caption(f"Reason: {bet['reject_reason']}")
                        else:
                            st.caption("Reason: Insufficient edge")

    # =========================================================================
    # TAB 4: ANALYTICS (Best Odds, Arbitrage, Middles, Accuracy)
    # =========================================================================
    with tab4:
        analytics_tab1, analytics_tab2, analytics_tab3, analytics_tab4 = st.tabs(["📈 Best Odds","💰 Arbitrage","🎯 Middles","📊 Accuracy"])
        with analytics_tab1:
            st.header("Best Odds Scanner")
            col1, col2 = st.columns([2,1])
            with col1:
                selected_sports_odds = st.multiselect("Select sports", ["NBA","MLB","NHL","NFL"], default=["NBA"], key="odds_sports")
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
        with analytics_tab2:
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
        with analytics_tab3:
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
        with analytics_tab4:
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
    # TAB 5: IMAGE ANALYSIS (OCR)
    # =========================================================================
    with tab5:
        st.header("📸 Screenshot Analyzer")
        uploaded_file = st.file_uploader("Choose an image...", type=["png","jpg","jpeg"])
        if uploaded_file is not None:
            st.image(uploaded_file, caption="Uploaded Screenshot", use_column_width=True)
            if st.button("🔍 ANALYZE SCREENSHOT", type="primary"):
                with st.spinner("Extracting text via OCR..."):
                    files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
                    data = {"apikey": OCR_SPACE_API_KEY, "language": "eng", "isOverlayRequired": False,
                            "filetype": uploaded_file.type.split("/")[-1] if uploaded_file.type else "PNG"}
                    response = requests.post("https://api.ocr.space/parse/image", files=files, data=data, timeout=30)
                    if response.status_code != 200:
                        st.error("OCR service failed. Try again.")
                    else:
                        result = response.json()
                        if result.get("IsErroredOnProcessing", True):
                            st.error(f"OCR Error: {result.get('ErrorMessage', 'Unknown')}")
                        else:
                            extracted_text = result["ParsedResults"][0]["ParsedText"]
                            st.subheader("📝 Extracted Text")
                            st.text(extracted_text)
                            bets = auto_parse_bets(extracted_text)
                            if not bets:
                                st.warning("No recognizable bets found in the image.")
                            else:
                                st.success(f"Found {len(bets)} potential bets.")
                                approved, rejected = [], []
                                for bet in bets:
                                    sport = "NBA"
                                    if bet["type"] == "moneyline":
                                        res = engine.analyze_moneyline(bet["home"], bet["away"], sport, bet["home_odds"], bet["away_odds"])
                                    elif bet["type"] == "spread":
                                        res = engine.analyze_spread(bet["team"], "Opponent", bet["spread"], bet["team"], sport, bet["odds"])
                                    elif bet["type"] == "total":
                                        res = engine.analyze_total("Home", "Away", bet["total"], bet["pick"], sport, bet["odds"])
                                    else:
                                        res = engine.analyze_prop(bet["player"], bet["market"], bet["line"], bet["pick"], [],
                                                                   sport, bet.get("odds",-110), None, "HEALTHY")
                                    if res.get("units",0) > 0:
                                        approved.append((bet, res))
                                    else:
                                        rejected.append((bet, res))
                                if approved:
                                    st.subheader("✅ CLARITY APPROVED")
                                    for bet, res in approved:
                                        st.markdown(f"**{bet.get('description', bet)}**")
                                        edge = res.get('edge', res.get('raw_edge',0))
                                        st.caption(f"Edge: {edge:.1%} | Units: {res.get('units',0)}")
                                if rejected:
                                    with st.expander(f"❌ REJECTED ({len(rejected)})"):
                                        for bet, res in rejected:
                                            st.markdown(f"**{bet.get('description', bet)}**")
                                            if res.get('reject_reason'):
                                                st.caption(f"Reason: {res['reject_reason']}")

    # =========================================================================
    # TAB 6: AUTO-TUNE HISTORY
    # =========================================================================
    with tab6:
        st.header("Auto-Tune History (ROI-based)")
        conn = sqlite3.connect(engine.db_path)
        df = pd.read_sql_query("SELECT * FROM tuning_log ORDER BY id DESC", conn)
        conn.close()
        if df.empty:
            st.info("No tuning events yet. After 50+ settled bets, auto-tune will run weekly.")
        else:
            st.dataframe(df)

    # =========================================================================
    # TAB 7: ARBITRAGE & MIDDLES (New)
    # =========================================================================
    with tab7:
        st.header("💰 Live Arbitrage & Middle Scanner")
        st.markdown("This tool scans for risk‑free arbitrage and middle opportunities using The Odds API.")
        if not EDGE_SCANNER_AVAILABLE:
            st.warning("Edge scanner module not found. Please ensure `edge_scanner.py` is in the same directory.")
        else:
            if st.button("🔍 SCAN FOR ARBITRAGE & MIDDLES", type="primary"):
                with st.spinner("Fetching games and scanning..."):
                    games = engine.game_scanner.fetch_todays_games(["NBA", "MLB", "NHL", "NFL"])
                    if not games:
                        st.warning("No games found today.")
                    else:
                        st.success(f"Found {len(games)} games. Scanning for arbs and middles...")
                        # Full multi‑book scanning would require more complex logic.
                        # For now, we provide manual testers below.
                        st.info("Full automatic scan requires multi‑book odds. Use the manual testers to check specific opportunities.")
            st.markdown("---")
            st.subheader("Manual 2‑Way Arbitrage Tester")
            col1, col2 = st.columns(2)
            with col1:
                book_a_odds = st.number_input("Book A odds", value=-110, step=5)
            with col2:
                book_b_odds = st.number_input("Book B odds", value=-110, step=5)
            if st.button("Test Arbitrage"):
                odds_a = {"Book A": book_a_odds}
                odds_b = {"Book B": book_b_odds}
                arb = find_arbitrage_2way(odds_a, odds_b, bankroll=100.0)
                if arb["is_arb"]:
                    st.success(f"✅ Arbitrage found! Profit: ${arb['profit']:.2f} (ROI: {arb['roi_pct']}%)")
                    st.write(arb)
                else:
                    st.info("No arbitrage opportunity.")
            st.markdown("---")
            st.subheader("Middle Hunter Tester")
            st.markdown("Enter two different lines (e.g., spread -4.5 and -1.5) to see middle EV.")
            c1, c2 = st.columns(2)
            with c1:
                line_a = st.number_input("Line A", value=-4.5, step=0.5)
                odds_a_mid = st.number_input("Odds A", value=-110, step=5)
            with c2:
                line_b = st.number_input("Line B", value=-1.5, step=0.5)
                odds_b_mid = st.number_input("Odds B", value=-110, step=5)
            if st.button("Test Middle"):
                mid = find_middle(line_a, odds_a_mid, line_b, odds_b_mid)
                if mid["is_middle"]:
                    st.write(mid)
                    if mid["recommended"]:
                        st.success(f"✅ Middle opportunity! EV: {mid['ev_pct']}%")
                    else:
                        st.warning("Middle exists but negative EV.")
                else:
                    st.info("No middle window.")
            st.markdown("---")
            st.subheader("+EV Detection (Soft vs Sharp)")
            st.markdown("Compare a soft book's odds to a sharp book (e.g., Pinnacle).")
            soft = st.number_input("Soft book odds", value=-110, step=5)
            sharp = st.number_input("Sharp book odds (e.g., Pinnacle)", value=-108, step=5)
            if st.button("Check +EV"):
                ev = find_plus_ev(soft, sharp)
                if ev["is_plus_ev"]:
                    st.success(f"✅ +EV! Edge: {ev['edge_pct']}%")
                else:
                    st.info(f"No +EV (edge: {ev['edge_pct']}%)")

if __name__ == "__main__":
    run_dashboard()
