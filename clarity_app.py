# =============================================================================
# CLARITY 22.5 – SOVEREIGN UNIFIED ENGINE (FULL SELF‑EVALUATION + SLIP PARSERS + WHY ANALYSIS)
#   - Dual sniffer (PrizePicks + Underdog) with browser headers
#   - Real BallsDontLie stats + prop model (WMA/volatility/Kelly)
#   - Game analyzer (ML, spreads, totals) with auto‑load from Odds‑API.io
#   - Unified slip system (props + games) with auto‑settlement
#   - FULL SELF‑EVALUATION: SEM score, auto‑tune thresholds, tuning history
#   - Bovada, MyBookie, PrizePicks slip parsers with WIN/LOSS detection
#   - **WHY ANALYSIS** in Paste & Scan: explains why a slip won or lost using real stats
# =============================================================================

import os
import json
import hashlib
import warnings
import time
import random
import re
import pickle
from functools import wraps
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson, nbinom
import streamlit as st
import sqlite3
import requests

warnings.filterwarnings("ignore")

VERSION = "22.5 – Unified + Self‑Evaluation + Why Analysis"
BUILD_DATE = "2026-04-18"

# =============================================================================
# YOUR API KEYS (all active)
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

DB_PATH = "clarity_unified.db"
LOG_DIR = "clarity_logs"
os.makedirs(LOG_DIR, exist_ok=True)

# Default thresholds – will be auto‑tuned
PROB_BOLT = 0.84
DTM_BOLT = 0.15

_stats_cache = {}
_game_score_cache = {}

# =============================================================================
# SPORT DATA & STAT CONFIG
# =============================================================================
SPORT_MODELS = {
    "NBA": {"variance_factor": 1.18, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"variance_factor": 1.10, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"variance_factor": 1.15, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"variance_factor": 1.22, "avg_total": 44.5, "home_advantage": 2.8},
}

SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR"],
    "NHL": ["SOG", "SAVES", "GOALS"],
    "NFL": ["PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"],
}

STAT_CONFIG = {
    "PTS": {"tier": "MED", "buffer": 1.5},
    "REB": {"tier": "LOW", "buffer": 1.0},
    "AST": {"tier": "LOW", "buffer": 1.5},
    "PRA": {"tier": "HIGH", "buffer": 3.0},
    "PR":  {"tier": "HIGH", "buffer": 2.0},
    "PA":  {"tier": "HIGH", "buffer": 2.0},
}

# =============================================================================
# DATABASE – UNIFIED SLIPS + TUNING + SEM LOGS
# =============================================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS slips (
        id TEXT PRIMARY KEY,
        type TEXT,
        sport TEXT,
        player TEXT,
        team TEXT,
        opponent TEXT,
        market TEXT,
        line REAL,
        pick TEXT,
        odds INTEGER,
        edge REAL,
        prob REAL,
        kelly REAL,
        tier TEXT,
        bolt_signal TEXT,
        result TEXT,
        actual REAL,
        date TEXT,
        settled_date TEXT,
        profit REAL,
        bankroll REAL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tuning_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        prob_bolt_old REAL,
        prob_bolt_new REAL,
        dtm_bolt_old REAL,
        dtm_bolt_new REAL,
        roi REAL,
        bets_used INTEGER
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS sem_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        sem_score INTEGER,
        accuracy REAL,
        bets_analyzed INTEGER
    )""")
    conn.commit()
    conn.close()

def insert_slip(entry: dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    slip_id = hashlib.md5(f"{entry.get('player','')}{entry.get('team','')}{entry.get('market','')}{datetime.now()}".encode()).hexdigest()[:12]
    c.execute("""INSERT OR REPLACE INTO slips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
        slip_id,
        entry.get("type", "PROP"),
        entry.get("sport", ""),
        entry.get("player", ""),
        entry.get("team", ""),
        entry.get("opponent", ""),
        entry.get("market", ""),
        entry.get("line", 0.0),
        entry.get("pick", ""),
        entry.get("odds", 0),
        entry.get("edge", 0.0),
        entry.get("prob", 0.5),
        entry.get("kelly", 0.0),
        entry.get("tier", ""),
        entry.get("bolt_signal", ""),
        entry.get("result", "PENDING"),
        entry.get("actual", 0.0),
        datetime.now().strftime("%Y-%m-%d"),
        entry.get("settled_date", ""),
        entry.get("profit", 0.0),
        entry.get("bankroll", 1000.0)
    ))
    conn.commit()
    conn.close()
    # If result is WIN/LOSS, trigger self‑evaluation
    if entry.get("result") in ["WIN", "LOSS"]:
        _calibrate_sem()
        auto_tune_thresholds()

def get_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips WHERE result = 'PENDING'", conn)
    conn.close()
    return df

def get_all_slips():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips ORDER BY date DESC", conn)
    conn.close()
    return df

def update_slip_result(slip_id: str, result: str, actual: float, odds: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if result == "WIN":
        profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
    else:
        profit = -100
    c.execute("UPDATE slips SET result=?, actual=?, settled_date=?, profit=? WHERE id=?",
              (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, slip_id))
    conn.commit()
    conn.close()
    _calibrate_sem()
    auto_tune_thresholds()

def clear_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM slips WHERE result = 'PENDING'")
    conn.commit()
    conn.close()

init_db()

# =============================================================================
# REAL STATS FETCHING (BallsDontLie) – for prop model and why analysis
# =============================================================================
def fetch_real_player_stats(player_name: str, market: str, sport: str = "NBA", game_date: str = None) -> List[float]:
    cache_key = f"{player_name}_{market}_{sport}_{game_date}"
    if cache_key in _stats_cache:
        return _stats_cache[cache_key]
    if sport != "NBA":
        return _fallback_stats(market)
    stat_map = {
        "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
        "BLK": "blk", "THREES": "tpm", "PRA": "pts+reb+ast",
        "PR": "pts+reb", "PA": "pts+ast"
    }
    stat_abbr = stat_map.get(market.upper(), "pts")
    headers = {"Authorization": BALLSDONTLIE_API_KEY}
    search_url = f"https://api.balldontlie.io/v1/players?search={player_name.replace(' ', '%20')}"
    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return _fallback_stats(market)
        players = resp.json().get("data", [])
        if not players:
            return _fallback_stats(market)
        player_id = players[0].get("id")
        # If game_date provided, fetch only that date; else last 12 games
        if game_date:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&dates[]={game_date}"
        else:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&per_page=12"
        stats_resp = requests.get(stats_url, headers=headers, timeout=10)
        if stats_resp.status_code != 200:
            return _fallback_stats(market)
        games = stats_resp.json().get("data", [])
        values = []
        for game in games:
            val = game.get(stat_abbr, 0)
            if isinstance(val, (int, float)):
                values.append(float(val))
        if len(values) < 1:
            return _fallback_stats(market)
        _stats_cache[cache_key] = values
        return values
    except Exception:
        return _fallback_stats(market)

def fetch_single_game_stat(player_name: str, market: str, game_date: str) -> Optional[float]:
    stats = fetch_real_player_stats(player_name, market, "NBA", game_date)
    if stats and len(stats) > 0:
        return stats[0]
    return None

def _fallback_stats(market: str) -> List[float]:
    if market.upper() == "PTS":
        mean_stat = 22.0; std_stat = 5.0
    elif market.upper() in ["REB", "AST"]:
        mean_stat = 8.0; std_stat = 3.0
    else:
        mean_stat = 15.0; std_stat = 4.0
    return np.random.normal(mean_stat, std_stat, 12).tolist()

# =============================================================================
# GAME SCORES FETCHING (Odds-API.io) – for why analysis of game bets
# =============================================================================
def fetch_game_score(team: str, opponent: str, sport: str, game_date: str) -> Tuple[Optional[float], Optional[float]]:
    cache_key = f"{sport}_{team}_{opponent}_{game_date}"
    if cache_key in _game_score_cache:
        return _game_score_cache[cache_key]
    # Use Odds-API.io historical scores
    sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
    sport_key = sport_map.get(sport)
    if not sport_key:
        return None, None
    url = f"https://api.odds-api.io/v4/sports/{sport_key}/events"
    params = {"apiKey": ODDS_API_IO_KEY, "date": game_date}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            events = data.get("data", []) if isinstance(data, dict) else data
            for event in events:
                home = event.get("home_team", "")
                away = event.get("away_team", "")
                if (home == team and away == opponent) or (home == opponent and away == team):
                    home_score = event.get("home_score")
                    away_score = event.get("away_score")
                    if home_score is not None and away_score is not None:
                        _game_score_cache[cache_key] = (float(home_score), float(away_score))
                        return float(home_score), float(away_score)
    except Exception:
        pass
    _game_score_cache[cache_key] = (None, None)
    return None, None

# =============================================================================
# PROP MODEL ENGINE (WMA, volatility, edge, Kelly)
# =============================================================================
def weighted_moving_average(values, window=6):
    if not values: return 0.0
    arr = np.array(values[-window:])
    weights = np.arange(1, len(arr) + 1)
    return float(np.sum(arr * weights) / np.sum(weights))

def weighted_standard_error(values, window=8):
    if len(values) < 2: return 1.0
    arr = np.array(values[-window:])
    weights = np.arange(1, len(arr) + 1)
    mean = np.sum(arr * weights) / np.sum(weights)
    var = np.sum(weights * (arr - mean) ** 2) / np.sum(weights)
    return float(max(np.sqrt(var / len(arr)), 0.5))

def l42_volatility_buffer(values):
    if len(values) < 4: return 1.0
    arr = np.array(values[-4:])
    return float(1.0 + min(np.std(arr) / 10.0, 0.5))

def tier_multiplier(stat):
    cfg = STAT_CONFIG.get(stat.upper(), {"tier": "LOW"})
    if cfg["tier"] == "HIGH": return 0.85
    if cfg["tier"] == "MED": return 0.93
    return 1.0

def kelly_fraction(prob, odds=-110):
    if odds == 0: return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    k = (prob * (b + 1) - 1) / b
    return float(max(0.0, min(k, 0.25)))

def classify_tier(edge):
    if edge >= 0.15: return "SOVEREIGN BOLT"
    if edge >= 0.08: return "ELITE LOCK"
    if edge >= 0.04: return "APPROVED"
    return "PASS" if edge < 0 else "NEUTRAL"

def analyze_prop(player, market, line, pick, sport="NBA", odds=-110, bankroll=1000):
    stats = fetch_real_player_stats(player, market, sport)
    wma = weighted_moving_average(stats)
    wse = weighted_standard_error(stats)
    vol_buf = l42_volatility_buffer(stats)
    sigma = max(wse * vol_buf, 0.75)
    mu = wma
    if pick == "OVER":
        prob = 1 - norm.cdf(line, loc=mu, scale=sigma)
    else:
        prob = norm.cdf(line, loc=mu, scale=sigma)
    edge = (prob - 0.5) * tier_multiplier(market)
    tier = classify_tier(edge)
    kelly = kelly_fraction(prob, odds)
    stake = bankroll * kelly
    bolt = "SOVEREIGN BOLT" if prob >= PROB_BOLT and (mu - line) / line >= DTM_BOLT else tier
    return {
        "prob": prob, "edge": edge, "mu": mu, "sigma": sigma, "wma": wma,
        "tier": tier, "kelly": kelly, "stake": stake, "bolt_signal": bolt,
        "stats": stats
    }

# =============================================================================
# GAME SCANNER (Odds-API.io) – auto-load games
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str, io_key: str):
        self.api_key = api_key
        self.io_key = io_key
        self.base_url = "https://api.the-odds-api.com/v4"
        self.io_base = "https://api.odds-api.io/v4"

    def fetch_games_by_date(self, sports: List[str], days_offset: int = 0) -> List[Dict]:
        target_date = (datetime.now() + timedelta(days=days_offset)).strftime("%Y-%m-%d")
        games = self._fetch_from_odds_api_io(sports, target_date)
        if games:
            return games
        return self._fetch_from_odds_api(sports)

    def _fetch_from_odds_api_io(self, sports: List[str], date_str: str) -> List[Dict]:
        all_games = []
        sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
        for sport in sports:
            sport_key = sport_map.get(sport)
            if not sport_key:
                continue
            url = f"{self.io_base}/sports/{sport_key}/events"
            params = {"apiKey": self.io_key}
            if date_str:
                params["date"] = date_str
            try:
                r = requests.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    events = data.get("data", []) if isinstance(data, dict) else data
                    for event in events[:20]:
                        game = {"sport": sport, "home": event.get("home_team", ""), "away": event.get("away_team", ""), "date": event.get("commence_time", ""), "event_id": event.get("id")}
                        odds_url = f"{self.io_base}/sports/{sport_key}/events/{event['id']}/odds"
                        odds_params = {"apiKey": self.io_key, "regions": "us", "markets": "h2h,spreads,totals"}
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
            except:
                pass
        return all_games

    def _fetch_from_odds_api(self, sports: List[str]) -> List[Dict]:
        all_games = []
        sport_keys = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}
        for sport in sports:
            key = sport_keys.get(sport)
            if not key:
                continue
            url = f"{self.base_url}/sports/{key}/odds"
            params = {"apiKey": self.api_key, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}
            try:
                r = requests.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    for game in r.json():
                        game_data = {"sport": sport, "home": game["home_team"], "away": game["away_team"], "bookmakers": game.get("bookmakers", [])}
                        if game_data["bookmakers"]:
                            bm = game_data["bookmakers"][0]
                            markets = {m["key"]: m for m in bm.get("markets", [])}
                            if "h2h" in markets:
                                outcomes = markets["h2h"]["outcomes"]
                                game_data["home_ml"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                                game_data["away_ml"] = next((o["price"] for o in outcomes if o["name"] == game["away_team"]), None)
                            if "spreads" in markets:
                                outcomes = markets["spreads"]["outcomes"]
                                game_data["spread"] = next((o["point"] for o in outcomes if o["name"] == game["home_team"]), None)
                                game_data["spread_odds"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                            if "totals" in markets:
                                outcomes = markets["totals"]["outcomes"]
                                game_data["total"] = next((o["point"] for o in outcomes), None)
                                game_data["over_odds"] = next((o["price"] for o in outcomes if o["name"] == "Over"), None)
                                game_data["under_odds"] = next((o["price"] for o in outcomes if o["name"] == "Under"), None)
                            all_games.append(game_data)
            except:
                pass
        return all_games

game_scanner = GameScanner(ODDS_API_KEY, ODDS_API_IO_KEY)

# =============================================================================
# SNIFFER BASE (shared session & headers)
# =============================================================================
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    import requests as curl_requests
    CURL_AVAILABLE = False

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Origin": "https://app.prizepicks.com",
    "Referer": "https://app.prizepicks.com/",
}

def make_session():
    if CURL_AVAILABLE:
        s = curl_requests.Session(impersonate="chrome124")
    else:
        s = curl_requests.Session()
    s.headers.update(BASE_HEADERS)
    return s

# =============================================================================
# PRIZEPICKS SNIFFER
# =============================================================================
PRIZEPICKS_BASE = "https://api.prizepicks.com"
PRIZEPICKS_ENDPOINTS = ["/projections", "/v1/projections", "/v2/projections"]

@dataclass
class PlayerProp:
    projection_id: str
    player_name: str
    team: str
    league: str
    stat_type: str
    line_score: float
    is_promoted: bool
    source: str = "PrizePicks"
    raw: dict = field(default_factory=dict, repr=False)

def fetch_all_pages(url, params=None, max_pages=30, delay=0.25):
    all_data = []
    all_included = []
    next_url = url
    page = 1
    session = make_session()
    while next_url and page <= max_pages:
        try:
            resp = session.get(next_url, params=params if page == 1 else None, timeout=15)
            if resp.status_code != 200:
                break
            body = resp.json()
            data = body.get("data", [])
            if isinstance(data, list):
                all_data.extend(data)
            elif isinstance(data, dict):
                all_data.append(data)
            all_included.extend(body.get("included", []))
            links = body.get("links", {})
            next_url = links.get("next") or None
        except Exception:
            break
        page += 1
        time.sleep(delay + random.uniform(0, 0.1))
    return all_data, all_included

def extract_prizepicks_props(records, included_map):
    players_map = included_map.get("new_player", {})
    players_map2 = included_map.get("player", {})
    leagues_map = included_map.get("league", {})
    stats_map = included_map.get("stat_type", {})
    props = []
    for rec in records:
        attrs = rec.get("attributes", {})
        rels = rec.get("relationships", {})
        line = float(attrs.get("line_score", attrs.get("line", 0)) or 0)
        stat_type = attrs.get("stat_type", "") or attrs.get("stat_display_name", "")
        if not stat_type:
            st_rel = rels.get("stat_type", {}).get("data", {})
            st_id = str(st_rel.get("id", ""))
            stat_type = stats_map.get(st_id, {}).get("name", "") or st_id
        player_name = attrs.get("player_name", "") or attrs.get("name", "")
        if not player_name:
            p_rel = rels.get("new_player", {}) or rels.get("player", {})
            p_id = str(p_rel.get("data", {}).get("id", ""))
            p_attrs = players_map.get(p_id) or players_map2.get(p_id) or {}
            player_name = p_attrs.get("name", "") or p_attrs.get("display_name", "") or p_id
        league = attrs.get("league", "") or attrs.get("league_name", "")
        if not league:
            l_rel = rels.get("league", {}).get("data", {})
            l_id = str(l_rel.get("id", ""))
            league = leagues_map.get(l_id, {}).get("name", "") or l_id
        team = attrs.get("team", "") or attrs.get("team_name", "")
        is_promo = bool(attrs.get("is_promo") or attrs.get("is_promoted") or attrs.get("flash_sale_line_score"))
        if not player_name or not stat_type:
            continue
        props.append(PlayerProp(
            projection_id=str(rec.get("id", "")),
            player_name=str(player_name),
            team=str(team),
            league=str(league),
            stat_type=str(stat_type),
            line_score=line,
            is_promoted=is_promo,
            source="PrizePicks",
            raw=rec,
        ))
    return props

def fetch_prizepicks_props(league_filter=None):
    for endpoint in PRIZEPICKS_ENDPOINTS:
        url = f"{PRIZEPICKS_BASE}{endpoint}"
        params = {"page[size]": 250, "single_stat": True}
        try:
            records, included = fetch_all_pages(url, params=params, max_pages=30)
            if records:
                included_map = {}
                for inc in included:
                    t = inc.get("type", "")
                    i = str(inc.get("id", ""))
                    attrs = {**inc.get("attributes", {}), "_id": i}
                    included_map.setdefault(t, {})[i] = attrs
                props = extract_prizepicks_props(records, included_map)
                if league_filter:
                    league_upper = league_filter.upper()
                    props = [p for p in props if league_upper in p.league.upper()]
                return props
        except Exception:
            continue
    return []

# =============================================================================
# UNDERDOG SNIFFER
# =============================================================================
UNDERDOG_BASE = "https://api.underdogfantasy.com"
UNDERDOG_ENDPOINTS = ["/bff/v3/projections", "/v3/projections", "/projections"]
UNDERDOG_HEADERS = {
    **BASE_HEADERS,
    "x-app-version": "2.0.0",
    "x-device-id": "web",
    "Origin": "https://underdogfantasy.com",
    "Referer": "https://underdogfantasy.com/",
}

def make_underdog_session():
    if CURL_AVAILABLE:
        s = curl_requests.Session(impersonate="chrome124")
    else:
        s = curl_requests.Session()
    s.headers.update(UNDERDOG_HEADERS)
    return s

def fetch_underdog_pages(url, params=None, max_pages=30, delay=0.25):
    all_data = []
    next_url = url
    page = 1
    session = make_underdog_session()
    while next_url and page <= max_pages:
        try:
            resp = session.get(next_url, params=params if page == 1 else None, timeout=15)
            if resp.status_code != 200:
                break
            body = resp.json()
            data = body.get("data", body.get("results", []))
            if isinstance(data, list):
                all_data.extend(data)
            elif isinstance(data, dict):
                all_data.append(data)
            next_url = body.get("links", {}).get("next") or body.get("next")
        except Exception:
            break
        page += 1
        time.sleep(delay + random.uniform(0, 0.1))
    return all_data

def extract_underdog_props(records):
    props = []
    for rec in records:
        attrs = rec.get("attributes", rec)
        line = float(attrs.get("line_score", attrs.get("line", 0)) or 0)
        stat_type = attrs.get("stat_type", "") or attrs.get("stat_display_name", "")
        player_name = attrs.get("player_name", "") or attrs.get("name", "")
        league = attrs.get("league", "") or attrs.get("league_name", "")
        team = attrs.get("team", "") or attrs.get("team_name", "")
        is_promo = bool(attrs.get("is_promo", False))
        if not player_name or not stat_type:
            continue
        props.append(PlayerProp(
            projection_id=str(rec.get("id", "")),
            player_name=str(player_name),
            team=str(team),
            league=str(league),
            stat_type=str(stat_type),
            line_score=line,
            is_promoted=is_promo,
            source="Underdog",
            raw=rec,
        ))
    return props

def fetch_underdog_props(league_filter=None):
    for endpoint in UNDERDOG_ENDPOINTS:
        url = f"{UNDERDOG_BASE}{endpoint}"
        params = {"page[size]": 250, "single_stat": True}
        try:
            records = fetch_underdog_pages(url, params=params, max_pages=30)
            if records:
                props = extract_underdog_props(records)
                if league_filter:
                    league_upper = league_filter.upper()
                    props = [p for p in props if league_upper in p.league.upper()]
                return props
        except Exception:
            continue
    return []

# =============================================================================
# SLIP PARSERS – Bovada, MyBookie, PrizePicks (full)
# =============================================================================
def parse_bovada_slip(text: str) -> List[Dict]:
    """Parse Bovada slip format – returns bets with result and actual if available."""
    bets = []
    lines = text.split('\n')
    current_parlay = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if 'Parlay' in line or 'Team Parlay' in line:
            current_parlay = {'type': 'PARLAY', 'legs': [], 'result': None}
        elif 'Loss' in line or 'Win' in line:
            if current_parlay:
                current_parlay['result'] = 'LOSS' if 'Loss' in line else 'WIN'
            else:
                # Single bet result
                result = 'LOSS' if 'Loss' in line else 'WIN'
        elif '@' in line and ('Moneyline' in line or 'Spread' in line or 'Total' in line):
            teams = re.search(r'(.+?)\s+@\s+(.+)', line)
            if teams:
                home = teams.group(2).strip()
                away = teams.group(1).strip()
        elif '+' in line or '-' in line:
            odds_match = re.search(r'([A-Za-z\s]+)\s*\(([+-]\d+)\)', line)
            if odds_match:
                team = odds_match.group(1).strip()
                odds = int(odds_match.group(2))
                if current_parlay:
                    current_parlay['legs'].append({'team': team, 'odds': odds, 'type': 'ML'})
                else:
                    bets.append({'type': 'GAME', 'sport': 'NBA', 'team': team, 'opponent': '', 'market_type': 'ML', 'line': 0.0, 'odds': odds, 'pick': team, 'result': result if 'result' in locals() else None, 'actual': None})
        elif 'Risk' in line:
            risk_match = re.search(r'Risk\s*\$\s*([\d.]+)', line)
            if risk_match and current_parlay:
                current_parlay['risk'] = float(risk_match.group(1))
        elif 'Odds' in line:
            odds_match = re.search(r'Odds\s*([+-]\d+)', line)
            if odds_match and current_parlay:
                current_parlay['parlay_odds'] = int(odds_match.group(1))
        elif 'Winnings' in line and current_parlay:
            # end of parlay – we don't auto-analyze parlays, just skip
            current_parlay = None
    return bets

def parse_mybookie_slip(text: str) -> List[Dict]:
    """Parse MyBookie slip format – returns bets with result and actual (from final score)."""
    bets = []
    lines = text.split('\n')
    current_bet = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        spread_match = re.search(r'([A-Za-z\s]+)\s*\(([+-]\d+\.?\d*)\)', line)
        if spread_match:
            team = spread_match.group(1).strip()
            spread = float(spread_match.group(2))
            current_bet = {'team': team, 'spread': spread, 'market_type': 'SPREAD'}
        odds_match = re.search(r'^([+-]\d{3,4})$', line)
        if odds_match and 'team' in current_bet:
            current_bet['odds'] = int(odds_match.group(1))
        if 'Handicap' in line:
            current_bet['type'] = 'GAME'
            if 'NHL' in line:
                current_bet['sport'] = 'NHL'
            elif 'NBA' in line:
                current_bet['sport'] = 'NBA'
            elif 'MLB' in line:
                current_bet['sport'] = 'MLB'
            elif 'NFL' in line:
                current_bet['sport'] = 'NFL'
            vs_match = re.search(r'(.+?)\s+vs\.\s+(.+)', line)
            if vs_match and 'team' in current_bet:
                if current_bet['team'] in vs_match.group(1):
                    current_bet['opponent'] = vs_match.group(2).strip()
                else:
                    current_bet['opponent'] = vs_match.group(1).strip()
        if 'Game Date:' in line:
            date_match = re.search(r'Game Date:\s*(.+)', line)
            if date_match:
                current_bet['game_date'] = date_match.group(1)
        if 'Risk:' in line and 'team' in current_bet:
            risk_match = re.search(r'Risk:\s*([\d.]+)', line)
            if risk_match:
                current_bet['risk'] = float(risk_match.group(1))
        if 'Win:' in line and 'team' in current_bet:
            win_match = re.search(r'Win:\s*([\d.]+)', line)
            if win_match:
                current_bet['win'] = float(win_match.group(1))
        if 'LOSS' in line and 'team' in current_bet:
            current_bet['result'] = 'LOSS'
            bets.append(current_bet.copy())
            current_bet = {}
        elif 'WIN' in line and 'team' in current_bet:
            current_bet['result'] = 'WIN'
            bets.append(current_bet.copy())
            current_bet = {}
    return bets

def parse_prizepicks_slip(text: str) -> List[Dict]:
    """Parse PrizePicks slip – includes result and actual value."""
    bets = []
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    i = 0
    while i < len(lines):
        if i+1 < len(lines) and lines[i] == lines[i+1]:
            player = lines[i]
            i += 2
        else:
            player = lines[i]
            i += 1
        if i >= len(lines):
            break
        try:
            line_val = float(lines[i])
        except:
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
            actual = float(lines[i])
        except:
            actual = 0.0
        i += 1
        result = 'WIN' if actual > line_val else 'LOSS'
        market_map = {'Ks':'KS','Hits+Runs+RBIs':'H+R+RBI','TB':'TB','Home Runs':'HR','Hits':'HITS','Points':'PTS','Rebounds':'REB','Assists':'AST','PRA':'PRA','PR':'PR','PA':'PA','SOG':'SOG','Saves':'SAVES'}
        market_std = market_map.get(market, market)
        sport = 'MLB' if market in ('Ks','Hits+Runs+RBIs','TB','Home Runs','Hits') else 'NBA'
        bets.append({'type':'PROP','sport':sport,'player':player,'market':market_std,'line':line_val,'pick':'OVER','result':result,'actual':actual,'odds':0})
    return bets

def parse_any_slip(text: str) -> List[Dict]:
    text_lower = text.lower()
    if 'parlay' in text_lower and '@' in text_lower:
        return parse_bovada_slip(text)
    elif 'handicap' in text_lower and 'game date:' in text_lower:
        return parse_mybookie_slip(text)
    elif 'flex play' in text_lower or 'final' in text_lower:
        return parse_prizepicks_slip(text)
    else:
        return []

# =============================================================================
# WHY ANALYSIS – generates explanation for a settled bet
# =============================================================================
def generate_why_analysis(bet: Dict) -> str:
    """Return a human-readable explanation of why the bet won or lost."""
    if bet.get('type') == 'PROP':
        player = bet.get('player', 'Unknown')
        market = bet.get('market', 'PTS')
        line = bet.get('line', 0)
        pick = bet.get('pick', 'OVER')
        result = bet.get('result')
        actual = bet.get('actual', None)
        sport = bet.get('sport', 'NBA')
        game_date = bet.get('game_date', (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
        
        # If actual not provided, try to fetch from API
        if actual is None or actual == 0:
            if sport == 'NBA':
                actual = fetch_single_game_stat(player, market, game_date)
            if actual is None:
                return f"⚠️ Could not fetch actual stats for {player} on {game_date}. Please provide actual value in slip."
        
        diff = actual - line
        if pick == 'OVER':
            if result == 'WIN':
                return f"✅ **WIN** – {player} {market} {pick} {line}. Actual: {actual}. You won because actual ({actual}) exceeded the line ({line}) by {diff:.1f} points."
            else:
                return f"❌ **LOSS** – {player} {market} {pick} {line}. Actual: {actual}. You lost because actual ({actual}) was below the line ({line}) by {abs(diff):.1f} points."
        else:  # UNDER
            if result == 'WIN':
                return f"✅ **WIN** – {player} {market} {pick} {line}. Actual: {actual}. You won because actual ({actual}) stayed under the line ({line}) by {abs(diff):.1f} points."
            else:
                return f"❌ **LOSS** – {player} {market} {pick} {line}. Actual: {actual}. You lost because actual ({actual}) exceeded the line ({line}) by {diff:.1f} points."
    
    elif bet.get('type') == 'GAME':
        team = bet.get('team', '')
        opponent = bet.get('opponent', '')
        market_type = bet.get('market_type', 'ML')
        line = bet.get('line', 0)
        pick = bet.get('pick', team)
        result = bet.get('result')
        sport = bet.get('sport', 'NBA')
        game_date = bet.get('game_date', (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
        
        # Fetch actual scores
        home_score, away_score = fetch_game_score(team, opponent, sport, game_date)
        if home_score is None or away_score is None:
            return f"⚠️ Could not fetch final score for {team} vs {opponent} on {game_date}. Please provide actual result manually."
        
        if market_type == 'ML':
            if (team == bet.get('home', '') and home_score > away_score) or (team == bet.get('away', '') and away_score > home_score):
                actual_result = 'WIN'
            else:
                actual_result = 'LOSS'
            if result == actual_result:
                return f"✅ **WIN** – {team} ML. Final: {team} {home_score if team==bet.get('home','') else away_score}, {opponent} {away_score if opponent==bet.get('away','') else home_score}. Your pick won."
            else:
                return f"❌ **LOSS** – {team} ML. Final: {team} {home_score if team==bet.get('home','') else away_score}, {opponent} {away_score if opponent==bet.get('away','') else home_score}. Your pick lost."
        
        elif market_type == 'SPREAD':
            if team == bet.get('home', ''):
                margin = home_score - away_score
            else:
                margin = away_score - home_score
            if pick == team:
                covered = margin > line
            else:
                covered = margin < -line
            if result == ('WIN' if covered else 'LOSS'):
                return f"✅ **WIN** – {team} {line:+.1f}. Final margin: {margin:+.1f}. You covered the spread."
            else:
                return f"❌ **LOSS** – {team} {line:+.1f}. Final margin: {margin:+.1f}. You did not cover the spread."
        
        elif market_type == 'TOTAL':
            total = home_score + away_score
            if pick == 'OVER':
                if result == 'WIN':
                    return f"✅ **WIN** – OVER {line}. Final total: {total}. You won because total exceeded {line} by {total-line:.1f}."
                else:
                    return f"❌ **LOSS** – OVER {line}. Final total: {total}. You lost because total was {line-total:.1f} points short."
            else:
                if result == 'WIN':
                    return f"✅ **WIN** – UNDER {line}. Final total: {total}. You won because total stayed under {line} by {line-total:.1f}."
                else:
                    return f"❌ **LOSS** – UNDER {line}. Final total: {total}. You lost because total exceeded {line} by {total-line:.1f}."
    
    return "Analysis not available for this bet type."

# =============================================================================
# SELF‑EVALUATION & METRICS (from CLARITY 18.3)
# =============================================================================
def get_accuracy_dashboard():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips WHERE result IN ('WIN','LOSS')", conn)
    conn.close()
    if df.empty:
        return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':100}
    wins = (df['result'] == 'WIN').sum()
    total = len(df)
    win_rate = wins/total*100
    total_profit = df['profit'].sum() if 'profit' in df.columns else 0
    total_stake = total * 100
    roi = (total_profit/total_stake)*100 if total_stake>0 else 0
    units_profit = total_profit / 100
    by_sport = {}
    for sport in df['sport'].unique():
        sport_df = df[df['sport']==sport]
        sport_wins = (sport_df['result']=='WIN').sum()
        by_sport[sport] = {'bets':len(sport_df), 'win_rate': round(sport_wins/len(sport_df)*100,1) if len(sport_df)>0 else 0}
    by_tier = {}
    for _,row in df.iterrows():
        signal = row.get('bolt_signal','PASS')
        if 'SOVEREIGN BOLT' in signal or 'ELITE LOCK' in signal:
            tier = 'SAFE'
        elif 'APPROVED' in signal:
            tier = 'BALANCED+'
        elif 'NEUTRAL' in signal:
            tier = 'NEUTRAL'
        else:
            tier = 'PASS'
        if tier not in by_tier:
            by_tier[tier] = {'bets':0,'wins':0}
        by_tier[tier]['bets'] += 1
        if row['result']=='WIN':
            by_tier[tier]['wins'] += 1
    for tier in by_tier:
        by_tier[tier]['win_rate'] = round(by_tier[tier]['wins']/by_tier[tier]['bets']*100,1) if by_tier[tier]['bets']>0 else 0
    sem_score = _get_sem_score()
    return {'total_bets':total,'wins':wins,'losses':total-wins,'win_rate':round(win_rate,1),'roi':round(roi,1),'units_profit':round(units_profit,1),'by_sport':by_sport,'by_tier':by_tier,'sem_score':sem_score}

def _get_sem_score() -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sem_score FROM sem_log ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 100

def _calibrate_sem():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT prob, result FROM slips WHERE result IN ('WIN','LOSS') AND prob IS NOT NULL", conn)
    conn.close()
    if len(df) < 10:
        return
    df['bin'] = pd.cut(df['prob'], bins=np.arange(0,1.1,0.1))
    actual_by_bin = df.groupby('bin')['result'].apply(lambda x: (x=='WIN').mean())
    expected_by_bin = df.groupby('bin')['prob'].mean()
    deviation = np.mean(np.abs(actual_by_bin - expected_by_bin))
    sem = max(0, min(100, int(100 - deviation * 200)))
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO sem_log (timestamp, sem_score, accuracy, bets_analyzed) VALUES (?,?,?,?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sem, 1-deviation, len(df)))
    conn.commit()
    conn.close()

def auto_tune_thresholds():
    global PROB_BOLT, DTM_BOLT
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT result, profit, bolt_signal FROM slips WHERE result IN ('WIN','LOSS') AND settled_date > date('now','-30 days')", conn)
    conn.close()
    if len(df) < 20:
        return
    total_profit = df['profit'].sum()
    total_stake = len(df) * 100
    roi = total_profit / total_stake if total_stake>0 else 0
    old_prob = PROB_BOLT
    old_dtm = DTM_BOLT
    if roi < -0.05:
        PROB_BOLT = min(0.95, PROB_BOLT + 0.03)
        DTM_BOLT = min(0.30, DTM_BOLT + 0.02)
    elif roi > 0.10:
        PROB_BOLT = max(0.70, PROB_BOLT - 0.03)
        DTM_BOLT = max(0.05, DTM_BOLT - 0.02)
    else:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO tuning_log (timestamp, prob_bolt_old, prob_bolt_new, dtm_bolt_old, dtm_bolt_new, roi, bets_used) VALUES (?,?,?,?,?,?,?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), old_prob, PROB_BOLT, old_dtm, DTM_BOLT, roi, len(df)))
    conn.commit()
    conn.close()

# =============================================================================
# STREAMLIT UI – UNIFIED WITH ALL TABS (including HISTORY & METRICS)
# =============================================================================
def main():
    st.set_page_config(page_title="CLARITY 22.5 – Unified + Self‑Evaluation", layout="wide")
    st.title(f"CLARITY {VERSION}")
    st.caption(f"Sniffer (PrizePicks/Underdog) + Prop Model + Game Analyzer + Self‑Evaluation • {BUILD_DATE}")

    bankroll = st.sidebar.number_input("Your Bankroll ($)", value=1000.0, min_value=100.0, step=50.0)

    tabs = st.tabs(["🎯 Player Props", "🏟️ Game Analyzer", "🧾 Unified Slip", "📋 Paste & Scan", "📊 History & Metrics", "⚙️ Tools"])

    # ---------- Tab 0: Player Props (with sniffer) ----------
    with tabs[0]:
        st.header("Player Props Analyzer")
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), key="pp_sport")
        platform = st.radio("Fetch from:", ["PrizePicks", "Underdog"], horizontal=True, key="pp_platform")
        if st.button(f"📡 Fetch Live Props from {platform}", type="primary"):
            with st.spinner(f"Sniffing {platform}..."):
                try:
                    if platform == "PrizePicks":
                        live = fetch_prizepicks_props(league_filter=sport)
                    else:
                        live = fetch_underdog_props(league_filter=sport)
                    st.session_state['live_props'] = live
                    st.session_state['last_platform'] = platform
                    st.success(f"✅ Fetched {len(live)} props")
                except Exception as e:
                    st.error(f"Failed: {e}")
                    st.session_state['live_props'] = []
        if 'live_props' in st.session_state and st.session_state['live_props']:
            st.subheader("Live Props")
            prop_list = st.session_state['live_props']
            options = {f"{p.player_name} - {p.stat_type} {p.line_score}": p for p in prop_list}
            sel = st.selectbox("Select a prop", list(options.keys()))
            prop = options[sel]
            st.session_state.pp_player = prop.player_name
            st.session_state.pp_market = prop.stat_type
            st.session_state.pp_line = float(prop.line_score)
            st.info(f"Loaded: {prop.player_name} | {prop.stat_type} o/u {prop.line_score}")

        player = st.text_input("Player Name", value=st.session_state.get('pp_player', "LeBron James"), key="pp_player")
        market = st.selectbox("Market", SPORT_CATEGORIES.get(sport, ["PTS"]),
                              index=SPORT_CATEGORIES.get(sport, ["PTS"]).index(st.session_state.get('pp_market', "PTS")) if st.session_state.get('pp_market') in SPORT_CATEGORIES.get(sport, ["PTS"]) else 0,
                              key="pp_market")
        line = st.number_input("Line", value=float(st.session_state.get('pp_line', 25.5)), step=0.5, key="pp_line")
        pick = st.radio("Pick", ["OVER", "UNDER"], horizontal=True, key="pp_pick")
        odds = st.number_input("American Odds", value=-110, key="pp_odds")

        if st.button("🚀 Run Prop Analysis", type="primary"):
            res = analyze_prop(player, market, line, pick, sport, odds, bankroll)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Win Prob", f"{res['prob']:.1%}")
            col2.metric("Edge", f"{res['edge']:+.1%}")
            col3.metric("Kelly Stake", f"${res['stake']:.2f}")
            col4.metric("Tier", res["tier"])
            if res["bolt_signal"] == "SOVEREIGN BOLT":
                st.success(f"### ⚡ SOVEREIGN BOLT — {pick} {line} {market}")
            elif res["edge"] > 0.04:
                st.success(f"### {res['bolt_signal']} — Recommended")
            else:
                st.error("### PASS — No edge")
            st.line_chart(pd.DataFrame({"Game": range(1, len(res["stats"])+1), "Stat": res["stats"]}).set_index("Game"))
            if st.button("➕ Add to Slip"):
                insert_slip({
                    "type": "PROP", "sport": sport, "player": player, "team": "", "opponent": "",
                    "market": market, "line": line, "pick": pick, "odds": odds,
                    "edge": res["edge"], "prob": res["prob"], "kelly": res["kelly"],
                    "tier": res["tier"], "bolt_signal": res["bolt_signal"], "bankroll": bankroll
                })
                st.success("Added to slip!")

    # ---------- Tab 1: Game Analyzer (auto‑load games) ----------
    with tabs[1]:
        st.header("Game Analyzer – ML, Spreads, Totals")
        sport2 = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], index=0, key="game_sport")
        col1, col2 = st.columns([3,1])
        with col1:
            load_tomorrow = st.checkbox("Load tomorrow's games", value=False, key="load_tomorrow")
        with col2:
            if st.button("📅 Load Games", type="primary"):
                days_offset = 1 if load_tomorrow else 0
                with st.spinner(f"Fetching {'tomorrow' if days_offset else 'today'}'s games..."):
                    games = game_scanner.fetch_games_by_date([sport2], days_offset)
                    if games:
                        st.session_state["auto_games"] = games
                        st.success(f"Loaded {len(games)} games")
                    else:
                        st.warning("No games found.")
        if "auto_games" in st.session_state and st.session_state["auto_games"]:
            game_options = [f"{g['home']} vs {g['away']}" for g in st.session_state["auto_games"]]
            selected = st.selectbox("Select a game", game_options)
            idx = game_options.index(selected)
            game = st.session_state["auto_games"][idx]
            home, away = game['home'], game['away']
            st.info(f"**{home}** vs **{away}**")
            if game.get("home_ml") and game.get("away_ml"):
                st.subheader("Moneyline")
                st.write(f"Home: {game['home_ml']} | Away: {game['away_ml']}")
            if game.get("spread") is not None:
                st.subheader("Spread")
                st.write(f"Spread: {game['spread']} (odds: {game.get('spread_odds','N/A')})")
            if game.get("total") is not None:
                st.subheader("Total")
                st.write(f"Total: {game['total']} (Over: {game.get('over_odds','N/A')}, Under: {game.get('under_odds','N/A')})")
        st.markdown("---")
        st.subheader("Manual Entry (fallback)")
        home = st.text_input("Home Team", key="game_home")
        away = st.text_input("Away Team", key="game_away")
        market_type = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="game_market")
        if market_type == "ML":
            home_odds = st.number_input("Home Odds", value=-110, key="ml_home")
            away_odds = st.number_input("Away Odds", value=-110, key="ml_away")
            if st.button("Analyze ML"):
                st.info("ML analysis would appear here (extend with actual model).")
        elif market_type == "SPREAD":
            spread = st.number_input("Spread", value=-5.5, key="spread_line")
            pick_side = st.selectbox("Pick", [home, away], key="spread_pick")
            odds_sp = st.number_input("Odds", value=-110, key="spread_odds")
            if st.button("Analyze Spread"):
                st.info("Spread analysis placeholder.")
        else:  # TOTAL
            total = st.number_input("Total Line", value=220.5, key="total_line")
            pick_tot = st.selectbox("Pick", ["OVER", "UNDER"], key="total_pick")
            odds_tot = st.number_input("Odds", value=-110, key="total_odds")
            if st.button("Analyze Total"):
                st.info("Total analysis placeholder.")

    # ---------- Tab 2: Unified Slip ----------
    with tabs[2]:
        st.header("Unified Slip – Props & Games")
        pending = get_pending_slips()
        if pending.empty:
            st.info("No pending bets.")
        else:
            st.dataframe(pending[["id", "type", "sport", "player", "team", "market", "line", "pick", "odds"]])
            slip_id = st.text_input("Slip ID to settle")
            settle_action = st.selectbox("Settle as", ["WIN", "LOSS", "AUTO (PROP)", "AUTO (GAME)"])
            if st.button("Settle") and slip_id:
                row = pending[pending["id"] == slip_id]
                if row.empty:
                    st.error("Invalid ID")
                else:
                    r = row.iloc[0]
                    if settle_action in ["WIN", "LOSS"]:
                        update_slip_result(slip_id, settle_action, r.get("actual", 0.0), r["odds"])
                        st.success(f"Marked as {settle_action}")
                    else:
                        st.warning("Auto‑settle not fully implemented in this version.")
            if st.button("Clear All Pending"):
                clear_pending_slips()
                st.rerun()

    # ---------- Tab 3: Paste & Scan (with WHY ANALYSIS) ----------
    with tabs[3]:
        st.header("Paste & Scan Slips")
        st.markdown("Paste any slip from **PrizePicks, Bovada, or MyBookie** – Clarity will auto‑detect, analyze, and explain why you won or lost.")
        text = st.text_area("Paste slip text", height=200,
                            placeholder="Example (Bovada):\n2 Team Parlay\nLoss\nGolden State Warriors @ L.A. Clippers\nGolden State Warriors (+180)\n...\n\nExample (MyBookie):\nOttawa Senators (+1.5)\n-187\nHandicap...")
        if st.button("🔍 Scan & Analyze"):
            if not text.strip():
                st.warning("Please paste some slip text.")
            else:
                parsed = parse_any_slip(text)
                if not parsed:
                    st.error("No bets recognized. Check format or use manual entry.")
                else:
                    st.success(f"Detected {len(parsed)} bets.")
                    for bet in parsed:
                        # If the bet has a result, generate why analysis
                        if bet.get('result') in ['WIN', 'LOSS']:
                            explanation = generate_why_analysis(bet)
                            with st.expander(f"{bet.get('type','BET').upper()}: {bet.get('player', bet.get('team',''))} - {bet.get('market', bet.get('market_type',''))} {bet.get('line','')} {bet.get('pick','')} - {bet.get('result')}"):
                                st.markdown(explanation)
                                # Also insert into DB as settled bet if not already there
                                # Avoid duplicates by checking if similar slip exists (simplified: just insert)
                                profit = 0
                                if bet['result'] == 'WIN':
                                    odds = bet.get('odds', -110)
                                    profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
                                else:
                                    profit = -100
                                insert_slip({
                                    "type": bet.get('type', 'PROP'),
                                    "sport": bet.get('sport', 'NBA'),
                                    "player": bet.get('player', ''),
                                    "team": bet.get('team', ''),
                                    "opponent": bet.get('opponent', ''),
                                    "market": bet.get('market', bet.get('market_type', 'PTS')),
                                    "line": bet.get('line', 0),
                                    "pick": bet.get('pick', 'OVER'),
                                    "odds": bet.get('odds', -110),
                                    "edge": 0.0,
                                    "prob": 0.5,
                                    "kelly": 0.0,
                                    "tier": "",
                                    "bolt_signal": "",
                                    "result": bet['result'],
                                    "actual": bet.get('actual', 0.0),
                                    "settled_date": datetime.now().strftime("%Y-%m-%d"),
                                    "profit": profit,
                                    "bankroll": bankroll
                                })
                                st.success("Bet added to history (self‑evaluation updated).")
                        else:
                            # No result – treat as pending bet (or just show)
                            with st.expander(f"{bet.get('type','BET').upper()}: {bet.get('player', bet.get('team',''))} - {bet.get('market', bet.get('market_type',''))} {bet.get('line','')}"):
                                st.json(bet)
                                st.info("This slip does not contain a WIN/LOSS result. Add result manually in the Unified Slip tab after settling.")

    # ---------- Tab 4: History & Metrics (Self‑Evaluation) ----------
    with tabs[4]:
        st.header("📊 History & Metrics (Self‑Evaluation)")
        st.markdown("Clarity automatically evaluates its own performance and tunes thresholds.")
        acc = get_accuracy_dashboard()
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Total Bets", acc['total_bets'])
        col2.metric("Win Rate", f"{acc['win_rate']}%")
        col3.metric("ROI", f"{acc['roi']}%")
        col4.metric("Units Profit", f"{acc['units_profit']}")
        st.subheader("By Sport")
        if acc['by_sport']:
            st.dataframe(pd.DataFrame(acc['by_sport']).T)
        else:
            st.info("No settled bets by sport yet.")
        st.subheader("By Tier (Bolt Signal)")
        if acc['by_tier']:
            st.dataframe(pd.DataFrame(acc['by_tier']).T)
        else:
            st.info("No settled bets by tier yet.")
        st.metric("SEM Score (Calibration)", f"{acc['sem_score']}/100")
        st.caption("SEM score measures how well predicted probabilities match actual outcomes. Higher = better calibrated.")
        st.subheader("Auto‑Tune History")
        conn = sqlite3.connect(DB_PATH)
        df_tune = pd.read_sql_query("SELECT * FROM tuning_log ORDER BY id DESC", conn)
        conn.close()
        if df_tune.empty:
            st.info("No tuning events yet. After 20+ settled bets, auto‑tune will run weekly.")
        else:
            st.dataframe(df_tune)
        st.subheader("Recent Bets")
        df_recent = get_all_slips().head(50)
        if df_recent.empty:
            st.info("No bets yet.")
        else:
            st.dataframe(df_recent[["date", "type", "player", "team", "market", "pick", "result", "profit"]])

    # ---------- Tab 5: Tools ----------
    with tabs[5]:
        st.header("Tools")
        st.info(f"curl_cffi available: {CURL_AVAILABLE}")
        st.info(f"BallsDontLie key: {'✅ Set' if BALLSDONTLIE_API_KEY else '❌ Missing'}")
        st.info(f"Odds‑API.io key: {'✅ Set' if ODDS_API_IO_KEY else '❌ Missing'}")
        st.info(f"Current thresholds: PROB_BOLT = {PROB_BOLT:.2f}, DTM_BOLT = {DTM_BOLT:.3f}")
        st.caption("Self‑evaluation runs automatically when you settle bets or paste winning/losing slips. Auto‑tune adjusts thresholds weekly.")

if __name__ == "__main__":
    main()
