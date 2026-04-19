# =============================================================================
# CLARITY 22.5 – SOVEREIGN UNIFIED ENGINE (FIXED SNIFFER + BEST BETS)
#   - Dual sniffer (PrizePicks + Underdog) with robust endpoint fallback
#   - Real BallsDontLie stats + prop model (WMA/volatility/Kelly)
#   - Game analyzer (ML, spreads, totals) with auto‑load from Odds‑API.io
#   - BEST BETS tab: parlays (2-4 legs) from approved bets + +EV suggestions
#   - Paste & Scan: explains wins/losses, stores results, feeds SEM
#   - FULL SELF‑EVALUATION: SEM score, auto‑tune thresholds
# =============================================================================

import os
import json
import hashlib
import warnings
import time
import random
import re
from functools import wraps
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from urllib.parse import urljoin

import numpy as np
import pandas as pd
from scipy.stats import norm
import streamlit as st
import sqlite3
import requests

warnings.filterwarnings("ignore")

VERSION = "22.5 – Robust Sniffer + Best Bets"
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
os.makedirs("clarity_logs", exist_ok=True)

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
# DATABASE – ONLY FOR SETTLED BETS (no pending)
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
    c.execute("""INSERT OR REPLACE INTO slips VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
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
    if entry.get("result") in ["WIN", "LOSS"]:
        _calibrate_sem()
        auto_tune_thresholds()

def get_all_slips():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips ORDER BY date DESC", conn)
    conn.close()
    return df

init_db()

# =============================================================================
# REAL STATS FETCHING (BallsDontLie) – for prop model
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
        if game_date:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&dates[]={game_date}"
        else:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&per_page=12"
        stats_resp = requests.get(stats_url, headers=headers, timeout=10)
        if stats_resp.status_code != 200:
            return _fallback_stats(market)
        games = stats_resp.json().get("data", [])
        values = [float(game.get(stat_abbr, 0)) for game in games]
        if len(values) < 1:
            return _fallback_stats(market)
        _stats_cache[cache_key] = values
        return values
    except Exception:
        return _fallback_stats(market)

def fetch_single_game_stat(player_name: str, market: str, game_date: str) -> Optional[float]:
    stats = fetch_real_player_stats(player_name, market, "NBA", game_date)
    return stats[0] if stats else None

def _fallback_stats(market: str) -> List[float]:
    if market.upper() == "PTS":
        mean_stat = 22.0; std_stat = 5.0
    elif market.upper() in ["REB", "AST"]:
        mean_stat = 8.0; std_stat = 3.0
    else:
        mean_stat = 15.0; std_stat = 4.0
    return np.random.normal(mean_stat, std_stat, 12).tolist()

# =============================================================================
# GAME SCORES FETCHING (Odds-API.io)
# =============================================================================
def fetch_game_score(team: str, opponent: str, sport: str, game_date: str) -> Tuple[Optional[float], Optional[float]]:
    cache_key = f"{sport}_{team}_{opponent}_{game_date}"
    if cache_key in _game_score_cache:
        return _game_score_cache[cache_key]
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
# PROP MODEL ENGINE
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
        "prob": prob, "edge": edge, "mu": mu, "sigma": sigma,
        "tier": tier, "kelly": kelly, "stake": stake, "bolt_signal": bolt,
        "stats": stats
    }

# =============================================================================
# GAME MODEL (simplified edge calculation)
# =============================================================================
def implied_prob(american_odds: float) -> float:
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return -american_odds / (-american_odds + 100)

def analyze_moneyline(home_team: str, away_team: str, sport: str, home_odds: float, away_odds: float) -> Dict:
    home_adv = SPORT_MODELS.get(sport, {}).get("home_advantage", 0)
    home_prob = 0.5 + home_adv / 100
    away_prob = 1 - home_prob
    home_edge = home_prob - implied_prob(home_odds)
    away_edge = away_prob - implied_prob(away_odds)
    return {"home": home_team, "away": away_team, "home_edge": home_edge, "away_edge": away_edge, "home_prob": home_prob, "away_prob": away_prob}

def analyze_spread(home_team: str, away_team: str, spread: float, spread_odds: float, sport: str) -> Dict:
    home_cover_prob = 0.5
    home_edge = home_cover_prob - implied_prob(spread_odds)
    away_edge = -home_edge
    return {"home": home_team, "away": away_team, "spread": spread, "home_edge": home_edge, "away_edge": away_edge, "home_prob": home_cover_prob, "away_prob": 1-home_cover_prob}

def analyze_total(total_line: float, over_odds: float, under_odds: float, sport: str) -> Dict:
    over_prob = 0.5
    over_edge = over_prob - implied_prob(over_odds)
    under_edge = -over_edge
    return {"total": total_line, "over_edge": over_edge, "under_edge": under_edge, "over_prob": over_prob, "under_prob": 1-over_prob}

# =============================================================================
# GAME SCANNER (Odds-API.io)
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
# SNIFFER BASE (shared session & headers) – IMPROVED WITH MORE ENDPOINTS
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

# Additional headers that PrizePicks might require
EXTRA_HEADERS = {
    "x-api-key": "prizepicks-web",  # sometimes needed
    "x-app-version": "1.0.0",
}

def make_session():
    if CURL_AVAILABLE:
        s = curl_requests.Session(impersonate="chrome124")
    else:
        s = curl_requests.Session()
    s.headers.update(BASE_HEADERS)
    s.headers.update(EXTRA_HEADERS)
    return s

# =============================================================================
# PRIZEPICKS SNIFFER – ROBUST WITH MULTIPLE ENDPOINTS & BASE URLS
# =============================================================================
PRIZEPICKS_BASE_URLS = [
    "https://api.prizepicks.com",
    "https://app.prizepicks.com/api",
    "https://www.prizepicks.com/api",
]

PRIZEPICKS_ENDPOINTS = [
    "/projections",
    "/v1/projections",
    "/v2/projections",
    "/v3/projections",
    "/v4/projections",
    "/bff/v1/projections",
    "/bff/v2/projections",
    "/bff/v3/projections",
]

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
    """Try multiple base URLs and endpoints until one works."""
    for base_url in PRIZEPICKS_BASE_URLS:
        for endpoint in PRIZEPICKS_ENDPOINTS:
            url = f"{base_url}{endpoint}"
            params = {"page[size]": 250, "single_stat": True}
            try:
                records, included = fetch_all_pages(url, params=params, max_pages=30)
                if records and len(records) > 0:
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
                    if props:
                        return props  # success
            except Exception as e:
                continue
    # If we reach here, no endpoint worked
    st.error("❌ Could not fetch props from PrizePicks. The API may have changed. Please report this issue.")
    return []

# =============================================================================
# UNDERDOG SNIFFER (unchanged, but also made robust)
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
    st.error("❌ Could not fetch props from Underdog. The API may have changed.")
    return []

# =============================================================================
# SLIP PARSER – complex multi‑sport (for Paste & Scan)
# =============================================================================
def parse_complex_slip(text: str) -> List[Dict]:
    bets = []
    blocks = re.split(r'(?=Bet ticket:|PARLAY)', text)
    for block in blocks:
        if not block.strip():
            continue
        if 'PARLAY' in block:
            result_match = re.search(r'PARLAY.*-\s*(WIN|LOSS)', block, re.IGNORECASE)
            overall_result = result_match.group(1).upper() if result_match else None
            if overall_result:
                bets.append({'type': 'PARLAY', 'result': overall_result, 'raw': block})
            continue
        lines = block.split('\n')
        current_bet = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            team_odds_match = re.search(r'^([A-Za-z\s\.\-]+?)\s+([+-]\d+(?:\.\d+)?)$', line)
            if team_odds_match:
                current_bet['team'] = team_odds_match.group(1).strip()
                current_bet['odds'] = int(team_odds_match.group(2))
                continue
            spread_match = re.search(r'^([A-Za-z\s\.\-]+?)\s+\(([+-]\d+\.?\d*)\)$', line)
            if spread_match:
                current_bet['team'] = spread_match.group(1).strip()
                current_bet['spread'] = float(spread_match.group(2))
                current_bet['market_type'] = 'SPREAD'
                continue
            odds_alone = re.search(r'^([+-]\d{3,4})$', line)
            if odds_alone and 'team' in current_bet and 'odds' not in current_bet:
                current_bet['odds'] = int(odds_alone.group(1))
                continue
            sport_match = re.search(r'(NBA|NHL|MLB)\s*\|\s*\w+\s+([A-Za-z\s\.\-]+?)\s+vs\.\s+([A-Za-z\s\.\-]+)', line)
            if sport_match:
                current_bet['sport'] = sport_match.group(1)
                current_bet['opponent'] = sport_match.group(3).strip()
                continue
            if 'Winner' in line and 'team' in current_bet:
                current_bet['market_type'] = 'ML'
                current_bet['line'] = 0.0
                continue
            if 'Handicap' in line and 'spread' in current_bet:
                current_bet['market_type'] = 'SPREAD'
                continue
            date_match = re.search(r'Game Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})\s*-\s*[\d:]+', line)
            if date_match:
                date_str = date_match.group(1)
                try:
                    dt = datetime.strptime(date_str, "%b %d, %Y")
                    current_bet['game_date'] = dt.strftime("%Y-%m-%d")
                except:
                    current_bet['game_date'] = date_str
                continue
            risk_match = re.search(r'Risk:\s*([\d.]+)', line)
            if risk_match and 'team' in current_bet:
                current_bet['risk'] = float(risk_match.group(1))
            win_match = re.search(r'Win:\s*([\d.]+)', line)
            if win_match and 'team' in current_bet:
                current_bet['win'] = float(win_match.group(1))
            if 'CASHED OUT' in line and 'team' in current_bet:
                current_bet['result'] = 'LOSS'
                current_bet['cashed_out'] = True
            if line.upper() in ['WIN', 'LOSS'] and 'team' in current_bet:
                current_bet['result'] = line.upper()
                if 'market_type' not in current_bet:
                    current_bet['market_type'] = 'ML'
                    current_bet['line'] = 0.0
                current_bet['pick'] = current_bet['team']
                bets.append(current_bet.copy())
                current_bet = {}
        if current_bet and 'team' in current_bet and 'sport' in current_bet:
            if 'win' in current_bet and current_bet.get('win', 0) > 0:
                current_bet['result'] = 'WIN'
            elif 'win' in current_bet and current_bet.get('win', 0) == 0:
                current_bet['result'] = 'LOSS'
            if 'result' in current_bet:
                if 'market_type' not in current_bet:
                    current_bet['market_type'] = 'ML'
                    current_bet['line'] = 0.0
                current_bet['pick'] = current_bet['team']
                bets.append(current_bet)
    return bets

# =============================================================================
# WHY ANALYSIS – generates explanation for a settled bet
# =============================================================================
def generate_why_analysis(bet: Dict) -> str:
    if bet.get('type') == 'PARLAY':
        return f"Parlay: {bet.get('result', 'Unknown')}. Detailed leg analysis not available."
    if bet.get('type') == 'PROP':
        player = bet.get('player', 'Unknown')
        market = bet.get('market', 'PTS')
        line = bet.get('line', 0)
        pick = bet.get('pick', 'OVER')
        result = bet.get('result')
        actual = bet.get('actual', None)
        sport = bet.get('sport', 'NBA')
        game_date = bet.get('game_date', (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
        if actual is None or actual == 0:
            if sport == 'NBA':
                actual = fetch_single_game_stat(player, market, game_date)
            if actual is None:
                return f"⚠️ Could not fetch actual stats for {player} on {game_date}."
        diff = actual - line
        if pick == 'OVER':
            if result == 'WIN':
                return f"✅ **WIN** – {player} {market} {pick} {line}. Actual: {actual}. You won because actual ({actual}) exceeded the line ({line}) by {diff:.1f}."
            else:
                return f"❌ **LOSS** – {player} {market} {pick} {line}. Actual: {actual}. You lost because actual ({actual}) was below the line ({line}) by {abs(diff):.1f}."
        else:
            if result == 'WIN':
                return f"✅ **WIN** – {player} {market} {pick} {line}. Actual: {actual}. You won because actual ({actual}) stayed under the line ({line}) by {abs(diff):.1f}."
            else:
                return f"❌ **LOSS** – {player} {market} {pick} {line}. Actual: {actual}. You lost because actual ({actual}) exceeded the line ({line}) by {diff:.1f}."
    elif bet.get('type') == 'GAME' or ('market_type' in bet):
        team = bet.get('team', '')
        opponent = bet.get('opponent', '')
        market_type = bet.get('market_type', 'ML')
        line = bet.get('line', 0)
        result = bet.get('result')
        sport = bet.get('sport', 'NBA')
        game_date = bet.get('game_date', (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"))
        home_score, away_score = fetch_game_score(team, opponent, sport, game_date)
        if home_score is None or away_score is None:
            return f"⚠️ Could not fetch final score for {team} vs {opponent} on {game_date}."
        total = home_score + away_score
        if market_type == 'ML':
            return f"**Final Score:** {home_score} – {away_score}. Your bet on {team} was a {result}."
        elif market_type == 'SPREAD':
            return f"**Final Score:** {home_score} – {away_score}. Spread was {line:+.1f} on {team}. Result: {result}."
        elif market_type == 'TOTAL':
            pick = bet.get('pick', 'OVER')
            if pick == 'OVER':
                if result == 'WIN':
                    return f"✅ **WIN** – OVER {line}. Final total: {total}. You won because total exceeded {line} by {total-line:.1f}."
                else:
                    return f"❌ **LOSS** – OVER {line}. Final total: {total}. You lost because total was {line-total:.1f} short."
            else:
                if result == 'WIN':
                    return f"✅ **WIN** – UNDER {line}. Final total: {total}. You won because total stayed under {line} by {line-total:.1f}."
                else:
                    return f"❌ **LOSS** – UNDER {line}. Final total: {total}. You lost because total exceeded {line} by {total-line:.1f}."
    return "Analysis not available."

# =============================================================================
# SELF‑EVALUATION & METRICS
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
# PARLAY GENERATION (2-4 legs from approved bets)
# =============================================================================
def generate_parlays(approved_bets: List[Dict], max_legs: int = 4, top_n: int = 20) -> List[Dict]:
    if len(approved_bets) < 2:
        return []
    unique = {}
    for bet in approved_bets:
        key = bet.get('unique_key', bet['description'])
        if key not in unique or bet['edge'] > unique[key]['edge']:
            unique[key] = bet
    unique_bets = list(unique.values())
    parlays = []
    from itertools import combinations
    for n in range(2, min(max_legs, len(unique_bets)) + 1):
        for combo in combinations(unique_bets, n):
            total_edge = sum(b['edge'] for b in combo)
            total_prob = 1.0
            decimal_odds = 1.0
            for b in combo:
                total_prob *= b['prob']
                if b['odds'] > 0:
                    dec = b['odds'] / 100 + 1
                else:
                    dec = 100 / abs(b['odds']) + 1
                decimal_odds *= dec
            estimated_american = round((decimal_odds - 1) * 100)
            parlays.append({
                'legs': [b['description'] for b in combo],
                'total_edge': total_edge,
                'confidence': total_prob,
                'estimated_odds': estimated_american,
                'num_legs': n
            })
    parlays.sort(key=lambda x: (-x['total_edge'], -x['confidence']))
    return parlays[:top_n]

# =============================================================================
# STREAMLIT UI – WITH "🏆 BEST BETS" TAB (replaces old Clarity Suggestions)
# =============================================================================
def main():
    st.set_page_config(page_title="CLARITY 22.5 – Best Bets + Parlays", layout="wide")
    st.title(f"CLARITY {VERSION}")
    st.caption(f"Sniffer (PrizePicks/Underdog) + Prop Model + Game Analyzer + Best Bets (Parlays) • {BUILD_DATE}")

    bankroll = st.sidebar.number_input("Your Bankroll ($)", value=1000.0, min_value=100.0, step=50.0)

    tabs = st.tabs(["🎯 Player Props", "🏟️ Game Analyzer", "🏆 Best Bets", "📋 Paste & Scan", "📊 History & Metrics", "⚙️ Tools"])

    # ---------- Tab 0: Player Props (fetch + manual) ----------
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
                    if live:
                        st.success(f"✅ Fetched {len(live)} props from {platform}")
                    else:
                        st.warning(f"No props found on {platform} for {sport}. API may be down or no games available.")
                except Exception as e:
                    st.error(f"Failed to fetch: {e}")
                    st.session_state['live_props'] = []
        if 'live_props' in st.session_state and st.session_state['live_props']:
            st.subheader("Live Props")
            prop_list = st.session_state['live_props']
            options = {f"{p.player_name} - {p.stat_type} {p.line_score}": p for p in prop_list}
            sel = st.selectbox("Select a prop to analyze manually", list(options.keys()))
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

    # ---------- Tab 1: Game Analyzer (auto‑load + approval) ----------
    with tabs[1]:
        st.header("Game Analyzer – ML, Spreads, Totals with Clarity Approval")
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
                        st.warning("No games found. Try a different sport or date.")
        if "auto_games" in st.session_state and st.session_state["auto_games"]:
            for idx, game in enumerate(st.session_state["auto_games"]):
                home = game.get('home', '')
                away = game.get('away', '')
                st.subheader(f"{home} vs {away}")
                if game.get('home_ml') and game.get('away_ml'):
                    ml_res = analyze_moneyline(home, away, sport2, game['home_ml'], game['away_ml'])
                    st.markdown(f"**Moneyline:** {home} {game['home_ml']} → {'✅ APPROVED' if ml_res['home_edge'] > 0.02 else '❌ PASS'} (Edge: {ml_res['home_edge']:.1%})")
                    st.markdown(f"**Moneyline:** {away} {game['away_ml']} → {'✅ APPROVED' if ml_res['away_edge'] > 0.02 else '❌ PASS'} (Edge: {ml_res['away_edge']:.1%})")
                if game.get('spread') is not None and game.get('spread_odds'):
                    spread_res = analyze_spread(home, away, game['spread'], game['spread_odds'], sport2)
                    st.markdown(f"**Spread {game['spread']:+.1f}:** {home} → {'✅ APPROVED' if spread_res['home_edge'] > 0.02 else '❌ PASS'} (Edge: {spread_res['home_edge']:.1%})")
                    st.markdown(f"**Spread {game['spread']:+.1f}:** {away} → {'✅ APPROVED' if spread_res['away_edge'] > 0.02 else '❌ PASS'} (Edge: {spread_res['away_edge']:.1%})")
                if game.get('total') is not None and game.get('over_odds') and game.get('under_odds'):
                    total_res = analyze_total(game['total'], game['over_odds'], game['under_odds'], sport2)
                    st.markdown(f"**Total {game['total']}:** OVER {game['over_odds']} → {'✅ APPROVED' if total_res['over_edge'] > 0.02 else '❌ PASS'} (Edge: {total_res['over_edge']:.1%})")
                    st.markdown(f"**Total {game['total']}:** UNDER {game['under_odds']} → {'✅ APPROVED' if total_res['under_edge'] > 0.02 else '❌ PASS'} (Edge: {total_res['under_edge']:.1%})")
                st.markdown("---")
        st.markdown("---")
        st.subheader("Manual Entry (fallback)")
        home = st.text_input("Home Team", key="game_home")
        away = st.text_input("Away Team", key="game_away")
        market_type = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="game_market")
        if market_type == "ML":
            home_odds = st.number_input("Home Odds", value=-110, key="ml_home")
            away_odds = st.number_input("Away Odds", value=-110, key="ml_away")
            if st.button("Analyze ML"):
                res = analyze_moneyline(home, away, sport2, home_odds, away_odds)
                st.markdown(f"{home} {home_odds}: {'✅ APPROVED' if res['home_edge'] > 0.02 else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                st.markdown(f"{away} {away_odds}: {'✅ APPROVED' if res['away_edge'] > 0.02 else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
        elif market_type == "SPREAD":
            spread = st.number_input("Spread", value=-5.5, key="spread_line")
            odds_sp = st.number_input("Odds", value=-110, key="spread_odds")
            if st.button("Analyze Spread"):
                res = analyze_spread(home, away, spread, odds_sp, sport2)
                st.markdown(f"{home} {spread:+.1f}: {'✅ APPROVED' if res['home_edge'] > 0.02 else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                st.markdown(f"{away} {spread:+.1f}: {'✅ APPROVED' if res['away_edge'] > 0.02 else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
        else:
            total = st.number_input("Total Line", value=220.5, key="total_line")
            over_odds = st.number_input("Over Odds", value=-110, key="over_odds")
            under_odds = st.number_input("Under Odds", value=-110, key="under_odds")
            if st.button("Analyze Total"):
                res = analyze_total(total, over_odds, under_odds, sport2)
                st.markdown(f"OVER {total}: {'✅ APPROVED' if res['over_edge'] > 0.02 else '❌ PASS'} (Edge: {res['over_edge']:.1%})")
                st.markdown(f"UNDER {total}: {'✅ APPROVED' if res['under_edge'] > 0.02 else '❌ PASS'} (Edge: {res['under_edge']:.1%})")

    # ---------- Tab 2: BEST BETS (parlays from approved bets + +EV suggestions) ----------
    with tabs[2]:
        st.header("🏆 Best Bets – Parlays (2-4 legs) from Clarity Approved")
        st.markdown("Automatically generated from fetched props (edge > 4%) and loaded game lines (edge > 2%). Minimum 2 legs required.")
        if st.button("🔄 Refresh Best Bets"):
            st.session_state['generate_best_bets'] = True
        # Collect approved bets
        approved_bets = []
        plus_ev_bets = []  # edge positive but below threshold
        # Props
        if 'live_props' in st.session_state and st.session_state['live_props']:
            for prop in st.session_state['live_props']:
                res_over = analyze_prop(prop.player_name, prop.stat_type, prop.line_score, "OVER", "NBA", -110, bankroll)
                res_under = analyze_prop(prop.player_name, prop.stat_type, prop.line_score, "UNDER", "NBA", -110, bankroll)
                if res_over['edge'] > res_under['edge']:
                    best_edge = res_over['edge']
                    best_pick = "OVER"
                    res = res_over
                else:
                    best_edge = res_under['edge']
                    best_pick = "UNDER"
                    res = res_under
                if best_edge > 0.04:
                    approved_bets.append({
                        "description": f"{prop.player_name} {prop.stat_type} {best_pick} {prop.line_score}",
                        "edge": best_edge,
                        "prob": res['prob'],
                        "odds": -110,
                        "unique_key": prop.player_name + prop.stat_type + best_pick
                    })
                elif best_edge > 0:
                    plus_ev_bets.append({
                        "description": f"{prop.player_name} {prop.stat_type} {best_pick} {prop.line_score}",
                        "edge": best_edge,
                        "prob": res['prob'],
                        "odds": -110,
                        "unique_key": prop.player_name + prop.stat_type + best_pick
                    })
        # Game lines
        if 'auto_games' in st.session_state and st.session_state['auto_games']:
            for game in st.session_state['auto_games']:
                sport_g = game.get('sport', 'NBA')
                home = game.get('home', '')
                away = game.get('away', '')
                # ML
                if game.get('home_ml') and game.get('away_ml'):
                    ml_res = analyze_moneyline(home, away, sport_g, game['home_ml'], game['away_ml'])
                    if ml_res['home_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{home} ML ({game['home_ml']})",
                            "edge": ml_res['home_edge'],
                            "prob": ml_res['home_prob'],
                            "odds": game['home_ml'],
                            "unique_key": f"{home}_ML"
                        })
                    elif ml_res['home_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{home} ML ({game['home_ml']})",
                            "edge": ml_res['home_edge'],
                            "prob": ml_res['home_prob'],
                            "odds": game['home_ml'],
                            "unique_key": f"{home}_ML"
                        })
                    if ml_res['away_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{away} ML ({game['away_ml']})",
                            "edge": ml_res['away_edge'],
                            "prob": ml_res['away_prob'],
                            "odds": game['away_ml'],
                            "unique_key": f"{away}_ML"
                        })
                    elif ml_res['away_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{away} ML ({game['away_ml']})",
                            "edge": ml_res['away_edge'],
                            "prob": ml_res['away_prob'],
                            "odds": game['away_ml'],
                            "unique_key": f"{away}_ML"
                        })
                # Spread
                if game.get('spread') is not None and game.get('spread_odds'):
                    spread_res = analyze_spread(home, away, game['spread'], game['spread_odds'], sport_g)
                    if spread_res['home_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{home} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['home_edge'],
                            "prob": spread_res['home_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{home}_spread"
                        })
                    elif spread_res['home_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{home} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['home_edge'],
                            "prob": spread_res['home_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{home}_spread"
                        })
                    if spread_res['away_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{away} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['away_edge'],
                            "prob": spread_res['away_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{away}_spread"
                        })
                    elif spread_res['away_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{away} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['away_edge'],
                            "prob": spread_res['away_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{away}_spread"
                        })
                # Total
                if game.get('total') is not None and game.get('over_odds') and game.get('under_odds'):
                    total_res = analyze_total(game['total'], game['over_odds'], game['under_odds'], sport_g)
                    if total_res['over_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"OVER {game['total']} ({game['over_odds']})",
                            "edge": total_res['over_edge'],
                            "prob": total_res['over_prob'],
                            "odds": game['over_odds'],
                            "unique_key": f"OVER_{game['total']}"
                        })
                    elif total_res['over_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"OVER {game['total']} ({game['over_odds']})",
                            "edge": total_res['over_edge'],
                            "prob": total_res['over_prob'],
                            "odds": game['over_odds'],
                            "unique_key": f"OVER_{game['total']}"
                        })
                    if total_res['under_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"UNDER {game['total']} ({game['under_odds']})",
                            "edge": total_res['under_edge'],
                            "prob": total_res['under_prob'],
                            "odds": game['under_odds'],
                            "unique_key": f"UNDER_{game['total']}"
                        })
                    elif total_res['under_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"UNDER {game['total']} ({game['under_odds']})",
                            "edge": total_res['under_edge'],
                            "prob": total_res['under_prob'],
                            "odds": game['under_odds'],
                            "unique_key": f"UNDER_{game['total']}"
                        })
        if not approved_bets:
            st.warning("No approved bets (edge > 4% for props, >2% for games). Load games or fetch props first.")
        else:
            st.subheader(f"📈 Approved Bets ({len(approved_bets)} legs available)")
            approved_df = pd.DataFrame([{
                "Bet": b['description'],
                "Edge": f"{b['edge']:.1%}",
                "Win Prob": f"{b['prob']:.1%}",
                "Odds": b['odds']
            } for b in approved_bets])
            st.dataframe(approved_df, use_container_width=True)
            # Generate parlays
            parlays = generate_parlays(approved_bets, max_legs=4, top_n=20)
            if parlays:
                st.subheader(f"🎲 Top Parlays ({len(parlays)} combinations, 2-4 legs)")
                for i, p in enumerate(parlays):
                    with st.expander(f"#{i+1}: {p['num_legs']}-Leg Parlay – Total Edge: {p['total_edge']:.1%} – Est. Odds: {p['estimated_odds']:+d}"):
                        st.markdown("**Legs:**")
                        for leg in p['legs']:
                            st.markdown(f"- {leg}")
                        st.metric("Confidence (product of win probs)", f"{p['confidence']:.1%}")
                        st.metric("Total Edge", f"{p['total_edge']:.1%}")
                        st.caption(f"Estimated parlay odds: {p['estimated_odds']:+d}")
            else:
                st.info("Not enough approved bets to form a 2‑leg parlay (need at least 2 unique legs).")
        # +EV suggestions (positive edge but below approval threshold)
        if plus_ev_bets:
            st.subheader("💰 +EV Suggestions (edge > 0% but below approval threshold)")
            ev_df = pd.DataFrame([{
                "Bet": b['description'],
                "Edge": f"{b['edge']:.1%}",
                "Win Prob": f"{b['prob']:.1%}",
                "Odds": b['odds']
            } for b in plus_ev_bets])
            st.dataframe(ev_df, use_container_width=True)
            st.caption("These bets have positive edge but did not meet the strict approval threshold. You may manually include them in parlays if desired.")

    # ---------- Tab 3: Paste & Scan (unchanged) ----------
    with tabs[3]:
        st.header("Paste & Scan Slips")
        st.markdown("Paste any slip (single game, parlay, multiple sports) – Clarity will extract individual bets and explain why you won or lost.")
        text = st.text_area("Paste slip text", height=300)
        if st.button("🔍 Scan & Analyze", type="primary"):
            if not text.strip():
                st.warning("Please paste some slip text.")
            else:
                parsed_bets = parse_complex_slip(text)
                if not parsed_bets:
                    st.error("No bets recognized. Check format.")
                else:
                    st.success(f"Detected {len(parsed_bets)} bets.")
                    for bet in parsed_bets:
                        if bet.get('type') == 'PARLAY':
                            with st.expander(f"PARLAY – {bet.get('result', 'Unknown')}"):
                                st.markdown(bet.get('raw', ''))
                                st.info("Parlay legs cannot be auto‑analyzed because individual lines are missing. Overall result recorded.")
                                profit = 0
                                if bet.get('result') == 'WIN':
                                    profit = 0
                                else:
                                    profit = -100
                                insert_slip({
                                    "type": "PARLAY",
                                    "sport": "MULTI",
                                    "player": "",
                                    "team": "",
                                    "opponent": "",
                                    "market": "PARLAY",
                                    "line": 0,
                                    "pick": "",
                                    "odds": 0,
                                    "edge": 0,
                                    "prob": 0.5,
                                    "kelly": 0,
                                    "tier": "",
                                    "bolt_signal": "",
                                    "result": bet.get('result'),
                                    "actual": 0,
                                    "settled_date": datetime.now().strftime("%Y-%m-%d"),
                                    "profit": profit,
                                    "bankroll": bankroll
                                })
                                st.success("Parlay result added to history (self‑evaluation updated).")
                        else:
                            explanation = generate_why_analysis(bet)
                            with st.expander(f"{bet.get('sport', 'UNK')} – {bet.get('team', '')} {bet.get('market_type', 'ML')} at {bet.get('odds', '?')}"):
                                st.markdown(explanation)
                                profit = 0
                                if bet.get('result') == 'WIN':
                                    odds = bet.get('odds', -110)
                                    profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
                                else:
                                    profit = -100
                                insert_slip({
                                    "type": "GAME",
                                    "sport": bet.get('sport', 'NBA'),
                                    "player": "",
                                    "team": bet.get('team', ''),
                                    "opponent": bet.get('opponent', ''),
                                    "market": bet.get('market_type', 'ML'),
                                    "line": bet.get('line', 0),
                                    "pick": bet.get('pick', bet.get('team', '')),
                                    "odds": bet.get('odds', -110),
                                    "edge": 0,
                                    "prob": 0.5,
                                    "kelly": 0,
                                    "tier": "",
                                    "bolt_signal": "",
                                    "result": bet.get('result'),
                                    "actual": 0,
                                    "settled_date": datetime.now().strftime("%Y-%m-%d"),
                                    "profit": profit,
                                    "bankroll": bankroll
                                })
                                st.success("Bet added to history (self‑evaluation updated).")

    # ---------- Tab 4: History & Metrics (unchanged) ----------
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

    # ---------- Tab 5: Tools (unchanged) ----------
    with tabs[5]:
        st.header("Tools")
        st.info(f"curl_cffi available: {CURL_AVAILABLE}")
        st.info(f"BallsDontLie key: {'✅ Set' if BALLSDONTLIE_API_KEY else '❌ Missing'}")
        st.info(f"Odds‑API.io key: {'✅ Set' if ODDS_API_IO_KEY else '❌ Missing'}")
        st.info(f"Current thresholds: PROB_BOLT = {PROB_BOLT:.2f}, DTM_BOLT = {DTM_BOLT:.3f}")
        st.caption("Self‑evaluation runs automatically when you paste winning/losing slips. Auto‑tune adjusts thresholds weekly.")

if __name__ == "__main__":
    main()
