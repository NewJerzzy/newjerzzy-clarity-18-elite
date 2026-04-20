# =============================================================================
# CLARITY 23.0 – ELITE MULTI‑SPORT ENGINE (FULLY UPGRADED)
#   - All prior features (sniffer, caching, bankroll, auto‑tune, SEM, etc.)
#   - Fixed: Clear buttons in Paste & Scan (text and images)
#   - Fixed: GameScanner now uses The Odds API (the-odds-api.com) with correct endpoints
#   - Fixed: Game Analyzer displays team names (home_team, away_team) and fetches odds
#   - Added: API key warnings in sidebar and Tools tab
#   - Added: OCR support for WEBP images
#   - Enhanced slip parser: PrizePicks Goblin, Bovada parlays, MyBookie slips
#   - **NEW:** Game Analyzer now uses full CLARITY model (WMA, sigma, edge, tiers)
# =============================================================================

import os
import json
import hashlib
import warnings
import time
import random
import re
import logging
from functools import wraps
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Tuple
from itertools import combinations
import base64

import numpy as np
import pandas as pd
from scipy.stats import norm
import streamlit as st
import sqlite3
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from PIL import Image
import io

# Sport‑specific libraries (optional)
try:
    from nhlpy import NHLClient
    NHL_AVAILABLE = True
except ImportError:
    NHL_AVAILABLE = False

try:
    import pgatourpy as pga
    PGA_AVAILABLE = True
except ImportError:
    PGA_AVAILABLE = False

try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    import requests as curl_requests
    CURL_AVAILABLE = False

warnings.filterwarnings("ignore")

# =============================================================================
# LOGGING SETUP
# =============================================================================
logging.basicConfig(
    filename='clarity_debug.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# =============================================================================
# VERSION & CONSTANTS
# =============================================================================
VERSION = "23.0 – Elite Multi‑Sport"
BUILD_DATE = "2026-04-19"

DB_PATH = "clarity_unified.db"
os.makedirs("clarity_logs", exist_ok=True)

PROB_BOLT = 0.84
DTM_BOLT = 0.15
KELLY_FRACTION = 0.25   # Fractional Kelly for conservative staking

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
    "PGA": {"variance_factor": 1.10, "avg_total": 70.5, "home_advantage": 0.0},
    "TENNIS": {"variance_factor": 1.05, "avg_total": 22.0, "home_advantage": 0.0},
}

SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"],
    "PGA": ["STROKES", "BIRDIES", "BOGEYS", "EAGLES", "DRIVING_DISTANCE", "GIR"],
    "TENNIS": ["ACES", "DOUBLE_FAULTS", "GAMES_WON", "TOTAL_GAMES", "BREAK_PTS"],
}

STAT_CONFIG = {
    "PTS": {"tier": "MED", "buffer": 1.5},
    "REB": {"tier": "LOW", "buffer": 1.0},
    "AST": {"tier": "LOW", "buffer": 1.5},
    "PRA": {"tier": "HIGH", "buffer": 3.0},
    "PR":  {"tier": "HIGH", "buffer": 2.0},
    "PA":  {"tier": "HIGH", "buffer": 2.0},
    "SOG": {"tier": "LOW", "buffer": 0.5},
    "SAVES": {"tier": "LOW", "buffer": 2.0},
    "STROKES": {"tier": "LOW", "buffer": 2.0},
    "BIRDIES": {"tier": "MED", "buffer": 1.0},
    "ACES": {"tier": "HIGH", "buffer": 1.0},
    "DOUBLE_FAULTS": {"tier": "HIGH", "buffer": 1.0},
    "GAMES_WON": {"tier": "LOW", "buffer": 1.5},
    # Game markets
    "TOTAL": {"tier": "MED", "buffer": 5.0},
    "SPREAD": {"tier": "MED", "buffer": 3.0},
    "ML": {"tier": "HIGH", "buffer": 0.0},
}

# =============================================================================
# HEALTH STATUS TRACKING (stored in session state)
# =============================================================================
def init_health_status():
    if "health_status" not in st.session_state:
        st.session_state.health_status = {
            "BallsDontLie (NBA)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "Odds-API.io (game scores)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "PrizePicks Sniffer": {"status": "unknown", "last_error": "", "fallback_active": False},
            "Underdog Sniffer": {"status": "unknown", "last_error": "", "fallback_active": False},
            "pgatourpy (PGA)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "nhl-api-py (NHL)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "curl_cffi (TLS)": {"status": "unknown", "last_error": "", "fallback_active": False},
        }

def update_health(component: str, success: bool, error_msg: str = "", fallback: bool = False):
    """Update health status of a component."""
    init_health_status()
    st.session_state.health_status[component]["status"] = "ok" if success else "fail"
    if error_msg:
        st.session_state.health_status[component]["last_error"] = error_msg[:200]
    st.session_state.health_status[component]["fallback_active"] = fallback

init_health_status()

# =============================================================================
# DATABASE – WITH INDEXES AND BANKROLL PERSISTENCE
# =============================================================================
def ensure_slips_schema():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("PRAGMA table_info(slips)")
    cols = [row[1] for row in c.fetchall()]

    required = [
        "id","type","sport","player","team","opponent","market","line","pick","odds",
        "edge","prob","kelly","tier","bolt_signal","result","actual","date",
        "settled_date","profit","bankroll"
    ]

    for col in required:
        if col not in cols:
            if col == "profit":
                c.execute("ALTER TABLE slips ADD COLUMN profit REAL DEFAULT 0")
            elif col == "bankroll":
                c.execute("ALTER TABLE slips ADD COLUMN bankroll REAL DEFAULT 1000")
            else:
                c.execute(f"ALTER TABLE slips ADD COLUMN {col} TEXT DEFAULT ''")
    conn.commit()
    conn.close()

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
    ensure_slips_schema()
    
    c.execute("CREATE INDEX IF NOT EXISTS idx_slips_result ON slips(result)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_slips_date ON slips(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_slips_sport ON slips(sport)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_slips_settled ON slips(settled_date)")
    
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
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value REAL
    )""")
    # New table for external SEM training data (community slips)
    c.execute("""CREATE TABLE IF NOT EXISTS sem_external (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT,
        prob REAL,
        result TEXT,
        source TEXT
    )""")
    conn.commit()
    conn.close()
    set_bankroll(get_bankroll())

def get_bankroll() -> float:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = 'bankroll'")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 1000.0

def set_bankroll(value: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('bankroll', ?)", (value,))
    conn.commit()
    conn.close()

def update_bankroll_from_slip(profit: float):
    new_bankroll = get_bankroll() + profit
    set_bankroll(max(new_bankroll, 0))

def insert_slip(entry: dict):
    ensure_slips_schema()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    slip_id = hashlib.md5(f"{entry.get('player','')}{entry.get('team','')}{entry.get('market','')}{datetime.now()}".encode()).hexdigest()[:12]

    c.execute("""
        INSERT OR REPLACE INTO slips (
            id, type, sport, player, team, opponent, market, line, pick, odds,
            edge, prob, kelly, tier, bolt_signal, result, actual, date, settled_date,
            profit, bankroll
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
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
        entry.get("bankroll", get_bankroll())
    ))
    conn.commit()
    conn.close()
    if entry.get("result") in ["WIN", "LOSS"]:
        if "profit" in entry:
            update_bankroll_from_slip(entry["profit"])
        _calibrate_sem()
        auto_tune_thresholds()

def insert_external_slip(prob: float, result: str, source: str = "OCR"):
    """Store an external slip's probability and result for SEM calibration only (no bankroll impact)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO sem_external (timestamp, prob, result, source) VALUES (?, ?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prob, result, source))
    conn.commit()
    conn.close()
    _calibrate_sem()

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
    update_bankroll_from_slip(profit)
    _calibrate_sem()
    auto_tune_thresholds()

def get_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips WHERE result = 'PENDING'", conn)
    conn.close()
    return df

def get_all_slips(limit: int = 500):
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips ORDER BY date DESC LIMIT ?", conn, params=(limit,))
    conn.close()
    return df

def clear_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM slips WHERE result = 'PENDING'")
    conn.commit()
    conn.close()

init_db()

# =============================================================================
# SESSION FACTORY — curl_cffi → requests (TLS impersonation)
# =============================================================================
def make_session(headers: dict = None, impersonate: bool = True):
    h = headers or {}
    if CURL_AVAILABLE and impersonate:
        try:
            s = curl_requests.Session(impersonate="chrome124")
            s.headers.update(h)
            update_health("curl_cffi (TLS)", success=True)
            return s
        except Exception as e:
            update_health("curl_cffi (TLS)", success=False, error_msg=str(e))
    else:
        update_health("curl_cffi (TLS)", success=False, error_msg="curl_cffi not available", fallback=True)
    s = requests.Session()
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    retry = Retry(total=3, backoff_factor=0.4,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET", "POST"], raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(h)
    return s

# =============================================================================
# SNIFFER CONFIG (unchanged, kept for completeness)
# =============================================================================
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

UNDERDOG_HEADERS = {
    **BASE_HEADERS,
    "x-app-version": "2.0.0",
    "x-device-id": "web",
    "Origin": "https://underdogfantasy.com",
    "Referer": "https://underdogfantasy.com/",
}

PRIZEPICKS_BASE_URLS = [
    "https://api.prizepicks.com",
    "https://app.prizepicks.com/api",
    "https://www.prizepicks.com/api",
]

PRIZEPICKS_ENDPOINTS = [
    "/projections",
    "/bff/v3/projections",
    "/bff/v2/projections",
    "/bff/v1/projections",
    "/v1/projections",
    "/v2/projections",
    "/v3/projections",
    "/v4/projections",
]

UNDERDOG_BASE = "https://api.underdogfantasy.com"
UNDERDOG_ENDPOINTS = [
    "/bff/v3/projections",
    "/bff/v2/projections",
    "/v3/projections",
    "/projections",
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

# =============================================================================
# SNIFFER HELPERS (pagination, extraction) – kept as before
# =============================================================================
def _fetch_pages(session, url: str, params: dict = None, max_pages: int = 30, delay: float = 0.25):
    all_data, all_included = [], []
    next_url = url
    page = 1
    while next_url and page <= max_pages:
        try:
            resp = session.get(next_url, params=params if page == 1 else None, timeout=15)
            if resp.status_code != 200:
                logging.warning(f"Fetch page {page} status {resp.status_code}: {url}")
                break
            body = resp.json()
            data = body.get("data", [])
            if isinstance(data, list):
                all_data.extend(data)
            elif isinstance(data, dict):
                all_data.append(data)
            all_included.extend(body.get("included", []))
            next_url = (body.get("links") or {}).get("next") or None
        except Exception as e:
            logging.error(f"Fetch page error: {e}")
            break
        page += 1
        time.sleep(delay + random.uniform(0, 0.1))
    return all_data, all_included

def _build_included_map(included: list) -> dict:
    m = {}
    for inc in included:
        t = inc.get("type", "")
        i = str(inc.get("id", ""))
        attrs = {**inc.get("attributes", {}), "_id": i}
        m.setdefault(t, {})[i] = attrs
    return m

def _safe(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return v
    return default

def _extract_props(records: list, inc_map: dict, source: str = "PrizePicks") -> List[PlayerProp]:
    players1 = inc_map.get("new_player", {})
    players2 = inc_map.get("player", {})
    leagues = inc_map.get("league", {})
    stats_map = inc_map.get("stat_type", {})
    props = []

    for rec in records:
        attrs = rec.get("attributes", {})
        rels = rec.get("relationships", {})

        line = float(_safe(attrs, "line_score", "line", "value") or 0)
        stat_type = _safe(attrs, "stat_type", "stat_display_name", "stat", "description")
        if not stat_type:
            st_rel = ((rels.get("stat_type") or {}).get("data")) or {}
            stat_type = (stats_map.get(str(st_rel.get("id", ""))) or {}).get("name", "")

        player_name = _safe(attrs, "player_name", "name")
        team = _safe(attrs, "team", "team_name", "team_abbreviation")
        if not player_name:
            p_rel = ((rels.get("new_player") or rels.get("player") or {}).get("data")) or {}
            p_id = str(p_rel.get("id", ""))
            p_attrs = players1.get(p_id) or players2.get(p_id) or {}
            player_name = _safe(p_attrs, "name", "display_name", "full_name") or p_id
            team = team or _safe(p_attrs, "team", "team_name")

        league = _safe(attrs, "league", "league_name", "league_display_name")
        if not league:
            l_rel = ((rels.get("league") or {}).get("data")) or {}
            l_id = str(l_rel.get("id", ""))
            league = _safe(leagues.get(l_id) or {}, "name", "display_name", "abbreviation") or l_id

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
            source=source,
            raw=rec,
        ))
    return props

def fetch_prizepicks_props(league_filter: str = None) -> List[PlayerProp]:
    session = make_session(BASE_HEADERS, impersonate=True)
    params = {"page[size]": 250, "single_stat": True}
    any_success = False
    for base in PRIZEPICKS_BASE_URLS:
        for ep in PRIZEPICKS_ENDPOINTS:
            url = base.rstrip("/") + ep
            try:
                logging.info(f"Trying PrizePicks: {url}")
                records, included = _fetch_pages(session, url, params=params)
                if not records:
                    continue
                inc_map = _build_included_map(included)
                props = _extract_props(records, inc_map, source="PrizePicks")
                if not props:
                    continue
                if league_filter:
                    lu = league_filter.upper()
                    props = [p for p in props if lu in p.league.upper()]
                if props:
                    any_success = True
                    update_health("PrizePicks Sniffer", success=True)
                    logging.info(f"PrizePicks OK: {len(props)} props from {url}")
                    return props
            except Exception as e:
                logging.warning(f"PrizePicks endpoint failed {url}: {e}")
                update_health("PrizePicks Sniffer", success=False, error_msg=str(e), fallback=True)
                continue
    if not any_success:
        update_health("PrizePicks Sniffer", success=False, error_msg="All endpoints exhausted", fallback=True)
    st.warning("PrizePicks fetch failed. Falling back to Underdog…")
    return fetch_underdog_props(league_filter)

def fetch_underdog_props(league_filter: str = None) -> List[PlayerProp]:
    session = make_session(UNDERDOG_HEADERS, impersonate=True)
    params = {"page[size]": 250, "single_stat": True}
    any_success = False
    for ep in UNDERDOG_ENDPOINTS:
        url = UNDERDOG_BASE.rstrip("/") + ep
        try:
            logging.info(f"Trying Underdog: {url}")
            records, included = _fetch_pages(session, url, params=params)
            if not records:
                try:
                    r = session.get(url, params=params, timeout=15)
                    body = r.json()
                    records = body.get("data", body.get("results", []))
                    included = []
                except Exception:
                    pass
            if not records:
                continue
            inc_map = _build_included_map(included)
            props = _extract_props(records, inc_map, source="Underdog")
            if not props:
                props = []
                for rec in records:
                    a = rec.get("attributes", rec)
                    line = float(a.get("line_score", a.get("line", 0)) or 0)
                    stat = a.get("stat_type", "") or a.get("stat_display_name", "")
                    name = a.get("player_name", "") or a.get("name", "")
                    lg = a.get("league", "") or a.get("sport", "")
                    team = a.get("team", "") or a.get("team_name", "")
                    if not name or not stat:
                        continue
                    props.append(PlayerProp(
                        projection_id=str(rec.get("id", "")),
                        player_name=str(name),
                        team=str(team),
                        league=str(lg),
                        stat_type=str(stat),
                        line_score=line,
                        is_promoted=bool(a.get("is_promo", False)),
                        source="Underdog",
                        raw=rec,
                    ))
            if not props:
                continue
            if league_filter:
                lu = league_filter.upper()
                props = [p for p in props if lu in p.league.upper()]
            if props:
                any_success = True
                update_health("Underdog Sniffer", success=True)
                logging.info(f"Underdog OK: {len(props)} props from {url}")
                return props
        except Exception as e:
            logging.warning(f"Underdog endpoint failed {url}: {e}")
            update_health("Underdog Sniffer", success=False, error_msg=str(e), fallback=True)
            continue
    if not any_success:
        update_health("Underdog Sniffer", success=False, error_msg="All endpoints exhausted", fallback=True)
    return []

# =============================================================================
# REAL STATS FETCHING (NBA, NHL, PGA, Tennis) – with caching and health
# =============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_nba_stats_cached(player_name: str, market: str, game_date: str = None) -> List[float]:
    stat_map = {
        "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
        "BLK": "blk", "THREES": "tpm", "PRA": "pts+reb+ast",
        "PR": "pts+reb", "PA": "pts+ast"
    }
    stat_abbr = stat_map.get(market.upper(), "pts")
    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    if not headers["Authorization"]:
        update_health("BallsDontLie (NBA)", success=False, error_msg="API key missing", fallback=True)
        return []
    search_url = f"https://api.balldontlie.io/v1/players?search={player_name.replace(' ', '%20')}"
    try:
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            update_health("BallsDontLie (NBA)", success=False, error_msg=f"HTTP {resp.status_code}", fallback=True)
            return []
        players = resp.json().get("data", [])
        if not players:
            update_health("BallsDontLie (NBA)", success=False, error_msg="Player not found", fallback=True)
            return []
        player_id = players[0].get("id")
        if game_date:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&dates[]={game_date}"
        else:
            stats_url = f"https://api.balldontlie.io/v1/stats?player_ids[]={player_id}&per_page=12"
        stats_resp = requests.get(stats_url, headers=headers, timeout=10)
        if stats_resp.status_code != 200:
            update_health("BallsDontLie (NBA)", success=False, error_msg=f"Stats HTTP {stats_resp.status_code}", fallback=True)
            return []
        games = stats_resp.json().get("data", [])
        values = []
        for game in games:
            val = game.get(stat_abbr, 0)
            if isinstance(val, (int, float)):
                values.append(float(val))
        if values:
            update_health("BallsDontLie (NBA)", success=True, fallback=False)
        else:
            update_health("BallsDontLie (NBA)", success=False, error_msg="No stats returned", fallback=True)
        return values
    except Exception as e:
        update_health("BallsDontLie (NBA)", success=False, error_msg=str(e), fallback=True)
        logging.error(f"NBA stats fetch error: {e}")
        return []

def _get_historical_fallback(market: str, sport: str = "NBA") -> List[float]:
    fallback_map = {
        ("NBA", "PTS"): [22.5, 23.1, 21.8, 24.2, 22.9, 23.5, 21.5, 24.0, 22.7, 23.3, 21.9, 23.8],
        ("NBA", "REB"): [7.2, 7.5, 6.9, 7.8, 7.3, 7.6, 6.8, 7.9, 7.1, 7.4, 6.7, 7.7],
        ("NBA", "AST"): [5.1, 5.3, 4.9, 5.6, 5.2, 5.4, 4.8, 5.7, 5.0, 5.5, 4.7, 5.8],
        ("NHL", "SOG"): [2.5, 2.7, 2.4, 2.8, 2.6, 2.7, 2.3, 2.9, 2.5, 2.8, 2.4, 2.9],
        ("NHL", "SAVES"): [25.0, 26.1, 24.5, 27.2, 25.8, 26.5, 24.2, 27.5, 25.3, 26.8, 24.8, 27.1],
        ("PGA", "STROKES"): [70.2, 70.5, 69.8, 71.0, 70.3, 70.6, 69.5, 71.2, 70.0, 70.8, 69.7, 71.1],
        ("TENNIS", "ACES"): [4.5, 4.8, 4.3, 5.0, 4.6, 4.9, 4.2, 5.1, 4.4, 4.7, 4.1, 5.2],
    }
    key = (sport, market.upper())
    if key in fallback_map:
        return fallback_map[key]
    return [15.0, 15.5, 14.8, 16.2, 15.3, 15.7, 14.5, 16.5, 15.1, 15.9, 14.7, 16.0]

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_real_player_stats(player_name: str, market: str, sport: str = "NBA", game_date: str = None) -> List[float]:
    cache_key = f"{player_name}_{market}_{sport}_{game_date}"
    if cache_key in _stats_cache:
        return _stats_cache[cache_key]

    if sport == "NBA":
        stats = _fetch_nba_stats_cached(player_name, market, game_date)
    elif sport == "NHL" and NHL_AVAILABLE:
        stats = []
        update_health("nhl-api-py (NHL)", success=False, error_msg="Not fully integrated", fallback=True)
    elif sport == "PGA" and PGA_AVAILABLE:
        stats = []
        update_health("pgatourpy (PGA)", success=False, error_msg="Import ok but no data fetch", fallback=True)
    elif sport == "TENNIS":
        stats = []
    else:
        stats = []

    if not stats or len(stats) < 3:
        logging.warning(f"Using fallback stats for {player_name} {market} {sport}")
        stats = _get_historical_fallback(market, sport)
        if sport == "NBA":
            update_health("BallsDontLie (NBA)", success=False, error_msg="Using fallback stats", fallback=True)
        elif sport == "NHL":
            update_health("nhl-api-py (NHL)", success=False, error_msg="Using fallback stats", fallback=True)
        elif sport == "PGA":
            update_health("pgatourpy (PGA)", success=False, error_msg="Using fallback stats", fallback=True)

    _stats_cache[cache_key] = stats
    return stats

def fetch_single_game_stat(player_name: str, market: str, game_date: str) -> Optional[float]:
    stats = fetch_real_player_stats(player_name, market, "NBA", game_date)
    return stats[0] if stats else None

# =============================================================================
# GAME SCORES FETCHING (Odds-API.io) with health tracking
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_score(team: str, opponent: str, sport: str, game_date: str) -> Tuple[Optional[float], Optional[float]]:
    cache_key = f"{sport}_{team}_{opponent}_{game_date}"
    if cache_key in _game_score_cache:
        return _game_score_cache[cache_key]
    sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
    sport_key = sport_map.get(sport)
    if not sport_key:
        update_health("Odds-API.io (game scores)", success=False, error_msg=f"Unsupported sport {sport}", fallback=True)
        return None, None
    url = f"https://api.odds-api.io/v4/sports/{sport_key}/events"
    params = {"apiKey": st.secrets.get("ODDS_API_IO_KEY", ""), "date": game_date}
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
                        update_health("Odds-API.io (game scores)", success=True)
                        _game_score_cache[cache_key] = (float(home_score), float(away_score))
                        return float(home_score), float(away_score)
            update_health("Odds-API.io (game scores)", success=False, error_msg="Event not found or no scores", fallback=True)
        else:
            update_health("Odds-API.io (game scores)", success=False, error_msg=f"HTTP {r.status_code}", fallback=True)
    except Exception as e:
        update_health("Odds-API.io (game scores)", success=False, error_msg=str(e), fallback=True)
        logging.error(f"Game score fetch error: {e}")
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
    full_kelly = max(0.0, min(k, 0.25))
    return full_kelly * KELLY_FRACTION

def classify_tier(edge):
    if edge >= 0.15: return "SOVEREIGN BOLT"
    if edge >= 0.08: return "ELITE LOCK"
    if edge >= 0.04: return "APPROVED"
    return "PASS" if edge < 0 else "NEUTRAL"

def analyze_prop(player, market, line, pick, sport="NBA", odds=-110, bankroll=None):
    if bankroll is None:
        bankroll = get_bankroll()
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
# GAME MODEL
# =============================================================================
def implied_prob(american_odds: float) -> float:
    if american_odds > 0:
        return 100 / (american_odds + 100)
    else:
        return -american_odds / (-american_odds + 100)

# -----------------------------------------------------------------------------
# NBA TEAM STATS FETCHING (Balldontlie)
# -----------------------------------------------------------------------------
NBA_TEAM_IDS = {
    "ATLANTA HAWKS": 1, "BOSTON CELTICS": 2, "BROOKLYN NETS": 3,
    "CHARLOTTE HORNETS": 4, "CHICAGO BULLS": 5, "CLEVELAND CAVALIERS": 6,
    "DALLAS MAVERICKS": 7, "DENVER NUGGETS": 8, "DETROIT PISTONS": 9,
    "GOLDEN STATE WARRIORS": 10, "HOUSTON ROCKETS": 11, "INDIANA PACERS": 12,
    "LA CLIPPERS": 13, "LOS ANGELES LAKERS": 14, "MEMPHIS GRIZZLIES": 15,
    "MIAMI HEAT": 16, "MILWAUKEE BUCKS": 17, "MINNESOTA TIMBERWOLVES": 18,
    "NEW ORLEANS PELICANS": 19, "NEW YORK KNICKS": 20, "OKLAHOMA CITY THUNDER": 21,
    "ORLANDO MAGIC": 22, "PHILADELPHIA 76ERS": 23, "PHOENIX SUNS": 24,
    "PORTLAND TRAIL BLAZERS": 25, "SACRAMENTO KINGS": 26, "SAN ANTONIO SPURS": 27,
    "TORONTO RAPTORS": 28, "UTAH JAZZ": 29, "WASHINGTON WIZARDS": 30,
    "ATL": 1, "BOS": 2, "BKN": 3, "CHA": 4, "CHI": 5, "CLE": 6,
    "DAL": 7, "DEN": 8, "DET": 9, "GSW": 10, "HOU": 11, "IND": 12,
    "LAC": 13, "LAL": 14, "MEM": 15, "MIA": 16, "MIL": 17, "MIN": 18,
    "NOP": 19, "NYK": 20, "OKC": 21, "ORL": 22, "PHI": 23, "PHX": 24,
    "POR": 25, "SAC": 26, "SAS": 27, "TOR": 28, "UTA": 29, "WAS": 30
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_recent_totals(team_name: str, window: int = 8) -> List[float]:
    """Fetch total points (team's own score) from last N games for a given NBA team."""
    team_id = NBA_TEAM_IDS.get(team_name.upper())
    if not team_id:
        for k, v in NBA_TEAM_IDS.items():
            if team_name.upper() in k:
                team_id = v
                break
    if not team_id:
        return _fallback_team_stats("NBA_TOTALS")

    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    url = f"https://api.balldontlie.io/v1/games?team_ids[]={team_id}&per_page={window}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return _fallback_team_stats("NBA_TOTALS")
        games = resp.json().get("data", [])
        totals = []
        for game in games:
            if game["home_team"]["id"] == team_id:
                totals.append(game["home_team_score"])
            else:
                totals.append(game["visitor_team_score"])
        totals.reverse()
        return totals if totals else _fallback_team_stats("NBA_TOTALS")
    except Exception:
        return _fallback_team_stats("NBA_TOTALS")

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_recent_margins(team_name: str, window: int = 8) -> List[float]:
    """Fetch point differential (team_score - opponent_score) for a team."""
    team_id = NBA_TEAM_IDS.get(team_name.upper())
    if not team_id:
        for k, v in NBA_TEAM_IDS.items():
            if team_name.upper() in k:
                team_id = v
                break
    if not team_id:
        return _fallback_team_stats("NBA_MARGINS")

    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    url = f"https://api.balldontlie.io/v1/games?team_ids[]={team_id}&per_page={window}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return _fallback_team_stats("NBA_MARGINS")
        games = resp.json().get("data", [])
        margins = []
        for game in games:
            if game["home_team"]["id"] == team_id:
                margin = game["home_team_score"] - game["visitor_team_score"]
            else:
                margin = game["visitor_team_score"] - game["home_team_score"]
            margins.append(margin)
        margins.reverse()
        return margins if margins else _fallback_team_stats("NBA_MARGINS")
    except Exception:
        return _fallback_team_stats("NBA_MARGINS")

def _fallback_team_stats(stat_type: str) -> List[float]:
    if stat_type == "NBA_TOTALS":
        return [114.2, 115.1, 113.8, 116.2, 114.9, 115.5, 113.5, 116.5]
    elif stat_type == "NBA_MARGINS":
        return [2.1, -1.5, 3.2, -2.8, 1.8, 2.5, -1.2, 3.5]
    else:
        return [0.0] * 8

# -----------------------------------------------------------------------------
# ADVANCED GAME ANALYSIS (CLARITY FULL MODEL)
# -----------------------------------------------------------------------------
def analyze_total_advanced(home_team: str, away_team: str, sport: str,
                           total_line: float, over_odds: int, under_odds: int) -> Dict:
    if sport != "NBA":
        proj = SPORT_MODELS.get(sport, {}).get("avg_total", 220.0)
        sigma = 12.0
    else:
        home_totals = fetch_team_recent_totals(home_team, 8)
        away_totals = fetch_team_recent_totals(away_team, 8)
        home_avg = weighted_moving_average(home_totals)
        away_avg = weighted_moving_average(away_totals)
        proj = home_avg + away_avg

        combined = []
        for h, a in zip(home_totals, away_totals):
            combined.append(h + a)
        if len(combined) < 3:
            combined = home_totals + away_totals
        wse = weighted_standard_error(combined)
        vol_buf = l42_volatility_buffer(combined)
        sigma = max(wse * vol_buf, 0.75)

    over_prob = 1 - norm.cdf(total_line, loc=proj, scale=sigma)
    under_prob = norm.cdf(total_line, loc=proj, scale=sigma)

    over_imp = implied_prob(over_odds)
    under_imp = implied_prob(under_odds)

    mult = tier_multiplier("TOTAL")
    over_edge = (over_prob - over_imp) * mult
    under_edge = (under_prob - under_imp) * mult

    over_tier = classify_tier(over_edge)
    under_tier = classify_tier(under_edge)

    over_bolt = "SOVEREIGN BOLT" if (over_prob >= PROB_BOLT and (proj - total_line)/total_line >= DTM_BOLT) else over_tier
    under_bolt = "SOVEREIGN BOLT" if (under_prob >= PROB_BOLT and (total_line - proj)/total_line >= DTM_BOLT) else under_tier

    return {
        "projection": proj, "sigma": sigma,
        "over_prob": over_prob, "over_edge": over_edge, "over_tier": over_tier, "over_bolt": over_bolt,
        "under_prob": under_prob, "under_edge": under_edge, "under_tier": under_tier, "under_bolt": under_bolt
    }

def analyze_spread_advanced(home_team: str, away_team: str, sport: str,
                            spread: float, spread_odds: int) -> Dict:
    if sport != "NBA":
        proj_margin = SPORT_MODELS.get(sport, {}).get("home_advantage", 3.0)
        sigma = 10.0
    else:
        home_margins = fetch_team_recent_margins(home_team, 8)
        away_margins = fetch_team_recent_margins(away_team, 8)
        home_avg = weighted_moving_average(home_margins)
        away_avg = weighted_moving_average(away_margins)
        proj_margin = home_avg - away_avg + 3.0

        combined_margins = [h - a for h, a in zip(home_margins, away_margins)]
        if len(combined_margins) < 3:
            combined_margins = home_margins + [-x for x in away_margins]
        wse = weighted_standard_error(combined_margins)
        vol_buf = l42_volatility_buffer(combined_margins)
        sigma = max(wse * vol_buf, 0.75)

    home_cover_prob = 1 - norm.cdf(spread, loc=proj_margin, scale=sigma)
    away_cover_prob = norm.cdf(spread, loc=proj_margin, scale=sigma)

    imp = implied_prob(spread_odds)
    mult = tier_multiplier("SPREAD")
    home_edge = (home_cover_prob - imp) * mult
    away_edge = (away_cover_prob - (1 - imp)) * mult

    home_tier = classify_tier(home_edge)
    away_tier = classify_tier(away_edge)
    home_bolt = "SOVEREIGN BOLT" if (home_cover_prob >= PROB_BOLT and (proj_margin - spread)/abs(spread+1e-9) >= DTM_BOLT) else home_tier
    away_bolt = "SOVEREIGN BOLT" if (away_cover_prob >= PROB_BOLT and (spread - proj_margin)/abs(spread+1e-9) >= DTM_BOLT) else away_tier

    return {
        "projected_margin": proj_margin, "sigma": sigma,
        "home_cover_prob": home_cover_prob, "home_edge": home_edge, "home_tier": home_tier, "home_bolt": home_bolt,
        "away_cover_prob": away_cover_prob, "away_edge": away_edge, "away_tier": away_tier, "away_bolt": away_bolt
    }

def analyze_moneyline_advanced(home_team: str, away_team: str, sport: str,
                               home_odds: int, away_odds: int) -> Dict:
    spread_res = analyze_spread_advanced(home_team, away_team, sport, 0.0, home_odds)
    proj_margin = spread_res["projected_margin"]
    sigma = spread_res["sigma"]

    if sport == "NBA":
        home_prob = 1 / (1 + np.exp(-0.13 * proj_margin))
    else:
        home_prob = 1 - norm.cdf(0, loc=proj_margin, scale=sigma)
    away_prob = 1 - home_prob

    home_imp = implied_prob(home_odds)
    away_imp = implied_prob(away_odds)

    mult = tier_multiplier("ML")
    home_edge = (home_prob - home_imp) * mult
    away_edge = (away_prob - away_imp) * mult

    home_tier = classify_tier(home_edge)
    away_tier = classify_tier(away_edge)
    home_bolt = "SOVEREIGN BOLT" if (home_prob >= PROB_BOLT and home_edge >= 0.15) else home_tier
    away_bolt = "SOVEREIGN BOLT" if (away_prob >= PROB_BOLT and away_edge >= 0.15) else away_tier

    return {
        "home_prob": home_prob, "home_edge": home_edge, "home_tier": home_tier, "home_bolt": home_bolt,
        "away_prob": away_prob, "away_edge": away_edge, "away_tier": away_tier, "away_bolt": away_bolt
    }

# =============================================================================
# GAME SCANNER – uses The Odds API and fetches both events and odds
# =============================================================================
class GameScanner:
    def __init__(self):
        self.api_key = st.secrets.get("ODDS_API_IO_KEY", "")
        self.base_url = "https://api.the-odds-api.com/v4"

    def fetch_games_by_date(self, sports: List[str], days_offset: int = 0) -> List[Dict]:
        if not self.api_key or self.api_key == "your_key_here":
            st.error("Odds-API.io API key is missing or invalid. Please set ODDS_API_IO_KEY in your Streamlit secrets.")
            return []

        all_games = []
        sport_key_map = {
            "NBA": "basketball_nba",
            "NFL": "americanfootball_nfl",
            "MLB": "baseball_mlb",
            "NHL": "icehockey_nhl",
        }

        for sport in sports:
            sport_key = sport_key_map.get(sport, sport.lower().replace(" ", "_"))
            events = self._fetch_events_with_odds(sport_key, days_offset)
            for event in events:
                event["sport"] = sport
            all_games.extend(events)

        if not all_games:
            st.info(f"No games found for {', '.join(sports)}. Try a different date or check your API key.")
        else:
            update_health("Odds-API.io (game scores)", success=True)

        return all_games

    def _fetch_events_with_odds(self, sport_key: str, days_offset: int) -> List[Dict]:
        events_url = f"{self.base_url}/sports/{sport_key}/events"
        events_params = {
            "apiKey": self.api_key,
            "days": days_offset + 1,
        }
        try:
            response = requests.get(events_url, params=events_params, timeout=10)
            response.raise_for_status()
            events = response.json()
        except Exception as e:
            st.warning(f"Error fetching events for {sport_key}: {e}")
            update_health("Odds-API.io (game scores)", success=False, error_msg=str(e), fallback=True)
            return []

        odds_url = f"{self.base_url}/sports/{sport_key}/odds"
        odds_params = {
            "apiKey": self.api_key,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
            "days": days_offset + 1,
        }
        try:
            odds_response = requests.get(odds_url, params=odds_params, timeout=10)
            odds_response.raise_for_status()
            odds_data = odds_response.json()
        except Exception as e:
            st.warning(f"Error fetching odds for {sport_key}: {e}")
            update_health("Odds-API.io (game scores)", success=False, error_msg=str(e), fallback=True)
            odds_data = []

        odds_by_event = {}
        for odd in odds_data:
            event_id = odd.get("id")
            if event_id:
                odds_by_event[event_id] = odd

        for event in events:
            event_id = event.get("id")
            if event_id in odds_by_event:
                odds_info = odds_by_event[event_id]
                bookmakers = odds_info.get("bookmakers", [])
                if bookmakers:
                    bm = bookmakers[0]
                    markets = bm.get("markets", [])
                    for m in markets:
                        if m["key"] == "h2h":
                            outcomes = m["outcomes"]
                            event["home_ml"] = next((o["price"] for o in outcomes if o["name"] == event.get("home_team")), None)
                            event["away_ml"] = next((o["price"] for o in outcomes if o["name"] == event.get("away_team")), None)
                        elif m["key"] == "spreads":
                            outcomes = m["outcomes"]
                            event["spread"] = next((o["point"] for o in outcomes if o["name"] == event.get("home_team")), None)
                            event["spread_odds"] = next((o["price"] for o in outcomes if o["name"] == event.get("home_team")), None)
                        elif m["key"] == "totals":
                            outcomes = m["outcomes"]
                            event["total"] = outcomes[0].get("point")
                            event["over_odds"] = next((o["price"] for o in outcomes if o["name"] == "Over"), None)
                            event["under_odds"] = next((o["price"] for o in outcomes if o["name"] == "Under"), None)
            else:
                event["home_ml"] = None
                event["away_ml"] = None
                event["spread"] = None
                event["spread_odds"] = None
                event["total"] = None
                event["over_odds"] = None
                event["under_odds"] = None

        return events

game_scanner = GameScanner()

# =============================================================================
# OCR FUNCTION – extract text from uploaded image (supports WEBP)
# =============================================================================
def ocr_image(image_bytes, api_key):
    try:
        encoded = base64.b64encode(image_bytes).decode('utf-8')
        payload = {
            'base64Image': f"data:image/png;base64,{encoded}",
            'apikey': api_key,
            'language': 'eng',
            'OCREngine': 2,
        }
        response = requests.post('https://api.ocr.space/parse/image', data=payload, timeout=30)
        result = response.json()
        if result.get('IsErroredOnProcessing'):
            error_msg = result.get('ErrorMessage', ['Unknown error'])[0]
            return None, f"OCR error: {error_msg}"
        parsed_text = result['ParsedResults'][0]['ParsedText']
        return parsed_text, None
    except Exception as e:
        return None, str(e)

# =============================================================================
# ENHANCED SLIP PARSER – handles PrizePicks Goblin, Bovada, MyBookie, and original block format
# =============================================================================
def parse_complex_slip(text: str) -> List[Dict]:
    bets = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return bets

    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and lines[i] == lines[i+1]:
            player_name = lines[i]
            block_lines = []
            j = i
            while j < len(lines) and not (j > i and lines[j] == ""):
                block_lines.append(lines[j])
                j += 1
                if j+1 < len(lines) and lines[j] == lines[j+1]:
                    break
            if len(block_lines) >= 12:
                try:
                    league = block_lines[3].upper()
                    team = block_lines[4]
                    opponent = block_lines[6]
                    line_val = float(block_lines[9])
                    market_raw = block_lines[10]
                    actual_val = float(block_lines[11])
                    
                    market = "PTS"
                    if "Pitches" in market_raw:
                        market = "PITCHES"
                    elif "Assists" in market_raw:
                        market = "AST"
                    elif "Steals" in market_raw:
                        market = "STL"
                    elif "Pts" in market_raw or "Rebs" in market_raw:
                        market = market_raw.replace("+", "").replace(" ", "")
                    else:
                        market = market_raw.upper()
                    
                    pick = "OVER"
                    result = "WIN" if actual_val > line_val else "LOSS"
                    
                    bet = {
                        "type": "PROP",
                        "player": player_name,
                        "sport": league,
                        "team": team,
                        "opponent": opponent,
                        "market": market,
                        "line": line_val,
                        "pick": pick,
                        "result": result,
                        "actual": actual_val,
                        "odds": -110,
                    }
                    bets.append(bet)
                    i = j
                    continue
                except Exception as e:
                    logging.warning(f"Error parsing player block for {player_name}: {e}")
                    i += 1
                    continue
        else:
            line = lines[i]
            goblin_match = re.search(r'^Goblin\s+([\d\.]+)\s+(\w+)\s+([\d\.]+)$', line, re.IGNORECASE)
            if goblin_match:
                player_name = "Unknown"
                for k in range(max(0, i-10), i):
                    candidate = lines[k]
                    if candidate and not re.match(r'^(Goblin|Final|WIN|LOSS|Risk|Win|Odds|Ref\.|Leaderboard|Show details)', candidate, re.IGNORECASE):
                        if len(candidate) > 2 and not candidate.isdigit():
                            player_name = candidate
                            break
                line_val = float(goblin_match.group(1))
                market_raw = goblin_match.group(2)
                actual_val = float(goblin_match.group(3))
                market = market_raw.upper()
                if market == "ASSISTS":
                    market = "AST"
                elif market == "STEALS":
                    market = "STL"
                result = "WIN" if actual_val > line_val else "LOSS"
                bet = {
                    "type": "PROP",
                    "player": player_name,
                    "sport": "NBA",
                    "team": "",
                    "opponent": "",
                    "market": market,
                    "line": line_val,
                    "pick": "OVER",
                    "result": result,
                    "actual": actual_val,
                    "odds": -110,
                }
                bets.append(bet)
                i += 1
                continue

            if re.search(r'\d+ Team Parlay', line, re.IGNORECASE):
                parlay_result = None
                parlay_risk = None
                parlay_odds = None
                parlay_winnings = None
                j = i
                while j < len(lines) and j < i + 20:
                    l = lines[j]
                    if re.search(r'^(Win|Loss)$', l, re.IGNORECASE):
                        parlay_result = l.upper()
                    elif re.search(r'Risk\s*[\$\d\.]+', l, re.IGNORECASE):
                        risk_match = re.search(r'Risk\s*\$?([\d\.]+)', l, re.IGNORECASE)
                        if risk_match:
                            parlay_risk = float(risk_match.group(1))
                    elif re.search(r'Odds\s*[+-]\d+', l, re.IGNORECASE):
                        odds_match = re.search(r'Odds\s*([+-]\d+)', l, re.IGNORECASE)
                        if odds_match:
                            parlay_odds = int(odds_match.group(1))
                    elif re.search(r'Winnings\s*[+\$]?[\d\.]+', l, re.IGNORECASE):
                        win_match = re.search(r'Winnings\s*[+\$]?([\d\.]+)', l, re.IGNORECASE)
                        if win_match:
                            parlay_winnings = float(win_match.group(1))
                    j += 1
                if parlay_result:
                    bet = {
                        "type": "PARLAY",
                        "result": parlay_result,
                        "raw": "\n".join(lines[i:j]),
                        "odds": parlay_odds if parlay_odds else 0,
                        "risk": parlay_risk if parlay_risk else 0,
                        "winnings": parlay_winnings if parlay_winnings else 0,
                    }
                    bets.append(bet)
                i = j
                continue

            mb_team_match = re.search(r'^([A-Za-z\s\.\-]+?)\s+([+-]\d+)$', line)
            if mb_team_match and i+1 < len(lines):
                team = mb_team_match.group(1).strip()
                odds = int(mb_team_match.group(2))
                next_line = lines[i+1]
                if "Winner" in next_line or "LOSS" in next_line.upper():
                    result = "WIN" if "Winner" in next_line else "LOSS" if "LOSS" in next_line.upper() else "PENDING"
                    sport = "NBA"
                    opponent = ""
                    game_date = ""
                    for k in range(i+2, min(i+10, len(lines))):
                        l = lines[k]
                        if "NBA" in l or "MLB" in l or "NHL" in l or "NFL" in l:
                            vs_match = re.search(r'vs\.\s+([A-Za-z\s\.\-]+)', l)
                            if vs_match:
                                opponent = vs_match.group(1).strip()
                            if "NBA" in l:
                                sport = "NBA"
                            elif "MLB" in l:
                                sport = "MLB"
                            elif "NHL" in l:
                                sport = "NHL"
                            elif "NFL" in l:
                                sport = "NFL"
                        date_match = re.search(r'Game Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})', l)
                        if date_match:
                            try:
                                dt = datetime.strptime(date_match.group(1), "%b %d, %Y")
                                game_date = dt.strftime("%Y-%m-%d")
                            except:
                                game_date = date_match.group(1)
                    risk = None
                    win_amt = None
                    for k in range(i+2, min(i+15, len(lines))):
                        l = lines[k]
                        risk_match = re.search(r'Risk:\s*([\d\.]+)', l, re.IGNORECASE)
                        if risk_match:
                            risk = float(risk_match.group(1))
                        win_match = re.search(r'Win:\s*([\d\.]+)', l, re.IGNORECASE)
                        if win_match:
                            win_amt = float(win_match.group(1))
                        if "LOSS" in l.upper():
                            result = "LOSS"
                    bet = {
                        "type": "GAME",
                        "team": team,
                        "opponent": opponent,
                        "odds": odds,
                        "market_type": "ML",
                        "line": 0.0,
                        "pick": team,
                        "sport": sport,
                        "result": result,
                        "game_date": game_date,
                        "risk": risk,
                        "win_amount": win_amt,
                    }
                    bets.append(bet)
                    i += 2
                    continue

            m = re.search(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+(\w+)$', line, re.IGNORECASE)
            if m:
                bet = {
                    "type": "PROP",
                    "player": m.group(1).strip(),
                    "pick": m.group(2).upper(),
                    "line": float(m.group(3)),
                    "market": m.group(4).upper(),
                    "sport": "NBA",
                    "odds": -110,
                }
                bets.append(bet)
                i += 1
                continue
            
            m = re.search(r'^(.+?)\s+(\w+)\s+(OVER|UNDER)\s+([\d\.]+)$', line, re.IGNORECASE)
            if m:
                bet = {
                    "type": "PROP",
                    "player": m.group(1).strip(),
                    "market": m.group(2).upper(),
                    "pick": m.group(3).upper(),
                    "line": float(m.group(4)),
                    "sport": "NBA",
                    "odds": -110,
                }
                bets.append(bet)
                i += 1
                continue
            
            m = re.search(r'^([A-Za-z\s\.\-]+?)\s+(?:vs\.?\s+([A-Za-z\s\.\-]+?))?\s+ML\s+([+-]\d+)$', line, re.IGNORECASE)
            if m:
                bet = {
                    "type": "GAME",
                    "team": m.group(1).strip(),
                    "opponent": m.group(2).strip() if m.group(2) else "",
                    "odds": int(m.group(3)),
                    "market_type": "ML",
                    "line": 0.0,
                    "pick": m.group(1).strip(),
                    "sport": "NBA",
                }
                bets.append(bet)
                i += 1
                continue
            
            m = re.search(r'^([A-Za-z\s\.\-]+?)\s+([+-][\d\.]+)\s*\(([+-]\d+)\)$', line, re.IGNORECASE)
            if m:
                bet = {
                    "type": "GAME",
                    "team": m.group(1).strip(),
                    "spread": float(m.group(2)),
                    "odds": int(m.group(3)),
                    "market_type": "SPREAD",
                    "line": float(m.group(2)),
                    "pick": m.group(1).strip(),
                    "sport": "NBA",
                }
                bets.append(bet)
                i += 1
                continue
            
            m = re.search(r'^(OVER|UNDER)\s+([\d\.]+)\s*\(([+-]\d+)\)$', line, re.IGNORECASE)
            if m:
                bet = {
                    "type": "GAME",
                    "pick": m.group(1).upper(),
                    "line": float(m.group(2)),
                    "odds": int(m.group(3)),
                    "market_type": "TOTAL",
                    "sport": "NBA",
                }
                bets.append(bet)
                i += 1
                continue
            
            logging.warning(f"Unrecognized line: {line}")
            i += 1
    
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
                return f"✅ WIN – {player} {market} OVER {line}. Actual: {actual:.1f}. Exceeded line by {diff:.1f}."
            else:
                return f"❌ LOSS – {player} {market} OVER {line}. Actual: {actual:.1f}. Fell short by {abs(diff):.1f}."
        else:
            if result == 'WIN':
                return f"✅ WIN – {player} {market} UNDER {line}. Actual: {actual:.1f}. Stayed under by {abs(diff):.1f}."
            else:
                return f"❌ LOSS – {player} {market} UNDER {line}. Actual: {actual:.1f}. Exceeded by {diff:.1f}."
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
            return f"Final: {home_score} – {away_score}. Your bet on {team} was a {result}."
        elif market_type == 'SPREAD':
            return f"Final: {home_score} – {away_score}. Spread {line:+.1f} on {team}. Result: {result}."
        elif market_type == 'TOTAL':
            pick = bet.get('pick', 'OVER')
            if pick == 'OVER':
                if result == 'WIN':
                    return f"✅ WIN – OVER {line}. Final total: {total}. Exceeded by {total-line:.1f}."
                else:
                    return f"❌ LOSS – OVER {line}. Final total: {total}. Fell short by {line-total:.1f}."
            else:
                if result == 'WIN':
                    return f"✅ WIN – UNDER {line}. Final total: {total}. Stayed under by {line-total:.1f}."
                else:
                    return f"❌ LOSS – UNDER {line}. Final total: {total}. Exceeded by {total-line:.1f}."
    return "Analysis not available."

# =============================================================================
# SELF‑EVALUATION & METRICS (now includes external SEM data)
# =============================================================================
def _get_sem_score() -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT sem_score FROM sem_log ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    return row[0] if row else 100

def _calibrate_sem():
    conn = sqlite3.connect(DB_PATH)
    df_internal = pd.read_sql_query("SELECT prob, result FROM slips WHERE result IN ('WIN','LOSS') AND prob IS NOT NULL", conn)
    df_external = pd.read_sql_query("SELECT prob, result FROM sem_external", conn)
    df = pd.concat([df_internal, df_external], ignore_index=True) if not df_external.empty else df_internal
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
    total_profit = df['profit'].sum() if 'profit' in df.columns else 0
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

def get_accuracy_dashboard():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM slips WHERE result IN ('WIN','LOSS')", conn)
    conn.close()
    if df.empty:
        return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':100}
    wins = (df['result'] == 'WIN').sum()
    total = len(df)
    win_rate = wins/total*100
    total_stake = total * 100
    if 'profit' in df.columns:
        total_profit = df['profit'].sum()
    else:
        total_profit = 0
        for _, row in df.iterrows():
            if row['result'] == 'WIN':
                odds = row.get('odds', -110)
                profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
            else:
                profit = -100
            total_profit += profit
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

# =============================================================================
# PARLAY GENERATION (with correlation checks)
# =============================================================================
def generate_parlays(approved_bets: List[Dict], max_legs: int = 4, top_n: int = 20) -> List[Dict]:
    if len(approved_bets) < 2:
        return []
    unique = {}
    for bet in approved_bets:
        key = bet.get('unique_key', bet.get('description', ''))
        if key not in unique or bet.get('edge', 0) > unique[key].get('edge', 0):
            unique[key] = bet
    unique_bets = list(unique.values())
    parlays = []
    for n in range(2, min(max_legs, len(unique_bets)) + 1):
        for combo in combinations(unique_bets, n):
            game_keys = set()
            conflict = False
            for b in combo:
                game_id = f"{b.get('sport', '')}_{b.get('team', '')}_{b.get('opponent', '')}"
                if game_id in game_keys:
                    conflict = True
                    break
                game_keys.add(game_id)
            if conflict:
                continue
            total_edge = sum(b.get('edge', 0) for b in combo)
            total_prob = 1.0
            decimal_odds = 1.0
            for b in combo:
                total_prob *= b.get('prob', 0.5)
                odds = b.get('odds', -110)
                if odds > 0:
                    dec = odds / 100 + 1
                else:
                    dec = 100 / abs(odds) + 1
                decimal_odds *= dec
            estimated_american = round((decimal_odds - 1) * 100)
            parlays.append({
                'legs': [b.get('description', '') for b in combo],
                'total_edge': total_edge,
                'confidence': total_prob,
                'estimated_odds': estimated_american,
                'num_legs': n
            })
    parlays.sort(key=lambda x: (-x['total_edge'], -x['confidence']))
    return parlays[:top_n]

# =============================================================================
# STREAMLIT UI
# =============================================================================
def main():
    st.set_page_config(page_title="CLARITY 23.0 – Elite Multi‑Sport", layout="wide")
    st.title(f"CLARITY {VERSION}")
    st.caption(f"Sniffer (PrizePicks/Underdog) + Prop Model + Game Analyzer + Best Bets (Parlays) • {BUILD_DATE}")

    # API key warnings
    if not st.secrets.get("BALLSDONTLIE_API_KEY"):
        st.sidebar.warning("⚠️ BallsDontLie API key missing. NBA stats will use fallback averages.")
    if not st.secrets.get("ODDS_API_IO_KEY") or st.secrets.get("ODDS_API_IO_KEY") == "your_key_here":
        st.sidebar.warning("⚠️ Odds-API.io key missing or invalid. Game Analyzer will not load games.")
    if not st.secrets.get("OCR_SPACE_API_KEY"):
        st.sidebar.warning("⚠️ OCR.space API key missing. Screenshot OCR will not work.")

    current_bankroll = get_bankroll()
    new_bankroll = st.sidebar.number_input("Your Bankroll ($)", value=current_bankroll, min_value=100.0, step=50.0)
    if new_bankroll != current_bankroll:
        set_bankroll(new_bankroll)
        st.sidebar.success("Bankroll updated")
        st.rerun()

    tabs = st.tabs(["🎯 Player Props", "🏟️ Game Analyzer", "🏆 Best Bets", "📋 Paste & Scan", "📊 History & Metrics", "⚙️ Tools"])

    # ---------- Tab 0: Player Props (with sniffer) ----------
    with tabs[0]:
        st.header("Player Props Analyzer")
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), key="pp_sport")
        platform = st.radio("Fetch from:", ["Auto (PrizePicks → Underdog)", "Underdog only"], horizontal=True, key="pp_platform")
        if st.button(f"📡 Fetch Live Props", type="primary"):
            with st.spinner(f"Fetching props (TLS + brute‑force + fallback)..."):
                try:
                    if platform == "Auto (PrizePicks → Underdog)":
                        live = fetch_prizepicks_props(league_filter=sport)
                    else:
                        live = fetch_underdog_props(league_filter=sport)
                    st.session_state['live_props'] = live
                    if live:
                        st.success(f"✅ Fetched {len(live)} props")
                        st.toast(f"Loaded {len(live)} props", icon="✅")
                    else:
                        st.warning("No props found. Please use manual entry below.")
                except Exception as e:
                    st.error(f"Failed to fetch: {e}")
                    st.session_state['live_props'] = []
        if 'live_props' in st.session_state and st.session_state['live_props']:
            st.subheader("Live Props")
            prop_list = st.session_state['live_props']
            options = {f"{p.player_name} - {p.stat_type} {p.line_score}": p for p in prop_list}
            sel = st.selectbox("Select a prop to analyze", list(options.keys()))
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
            res = analyze_prop(player, market, line, pick, sport, odds, new_bankroll)
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
                    "tier": res["tier"], "bolt_signal": res["bolt_signal"], "bankroll": new_bankroll
                })
                st.success("Added to slip!")
                st.toast("Slip added", icon="➕")

    # ---------- Tab 1: Game Analyzer (FULL CLARITY MODEL) ----------
    with tabs[1]:
        st.header("Game Analyzer – ML, Spreads, Totals with CLARITY Approval")
        st.caption("Fetches real team stats (NBA) and applies the full weighted moving average, volatility, edge, and tier model.")
        sport2 = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], index=0, key="game_sport")
        col1, col2 = st.columns([3, 1])
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
            for idx, game in enumerate(st.session_state["auto_games"]):
                home = game.get('home_team', '')
                away = game.get('away_team', '')
                if not home or not away:
                    continue
                st.subheader(f"{home} vs {away}")

                # ---------- MONEYLINE ----------
                if game.get('home_ml') and game.get('away_ml'):
                    ml_res = analyze_moneyline_advanced(home, away, sport2, game['home_ml'], game['away_ml'])
                    col_ml1, col_ml2 = st.columns(2)
                    with col_ml1:
                        tier = ml_res['home_tier']
                        bolt = ml_res['home_bolt']
                        if tier != "PASS":
                            st.success(f"**{home} ML ({game['home_ml']})**")
                            st.caption(f"{bolt} | Edge: {ml_res['home_edge']:.1%} | Prob: {ml_res['home_prob']:.1%}")
                        else:
                            st.error(f"**{home} ML ({game['home_ml']})** — PASS")
                    with col_ml2:
                        tier = ml_res['away_tier']
                        bolt = ml_res['away_bolt']
                        if tier != "PASS":
                            st.success(f"**{away} ML ({game['away_ml']})**")
                            st.caption(f"{bolt} | Edge: {ml_res['away_edge']:.1%} | Prob: {ml_res['away_prob']:.1%}")
                        else:
                            st.error(f"**{away} ML ({game['away_ml']})** — PASS")

                # ---------- SPREAD ----------
                if game.get('spread') is not None and game.get('spread_odds'):
                    spread_res = analyze_spread_advanced(home, away, sport2, game['spread'], game['spread_odds'])
                    col_sp1, col_sp2 = st.columns(2)
                    with col_sp1:
                        tier = spread_res['home_tier']
                        bolt = spread_res['home_bolt']
                        if tier != "PASS":
                            st.success(f"**{home} {game['spread']:+.1f} ({game['spread_odds']})**")
                            st.caption(f"{bolt} | Edge: {spread_res['home_edge']:.1%} | Cover Prob: {spread_res['home_cover_prob']:.1%}")
                        else:
                            st.error(f"**{home} {game['spread']:+.1f} ({game['spread_odds']})** — PASS")
                    with col_sp2:
                        tier = spread_res['away_tier']
                        bolt = spread_res['away_bolt']
                        if tier != "PASS":
                            st.success(f"**{away} {game['spread']:+.1f} ({game['spread_odds']})**")
                            st.caption(f"{bolt} | Edge: {spread_res['away_edge']:.1%} | Cover Prob: {spread_res['away_cover_prob']:.1%}")
                        else:
                            st.error(f"**{away} {game['spread']:+.1f} ({game['spread_odds']})** — PASS")

                # ---------- TOTAL ----------
                if game.get('total') is not None and game.get('over_odds') and game.get('under_odds'):
                    total_res = analyze_total_advanced(home, away, sport2, game['total'], game['over_odds'], game['under_odds'])
                    col_tot1, col_tot2 = st.columns(2)
                    with col_tot1:
                        tier = total_res['over_tier']
                        bolt = total_res['over_bolt']
                        if tier != "PASS":
                            st.success(f"**OVER {game['total']} ({game['over_odds']})**")
                            st.caption(f"{bolt} | Edge: {total_res['over_edge']:.1%} | Prob: {total_res['over_prob']:.1%} | Proj: {total_res['projection']:.1f}")
                        else:
                            st.error(f"**OVER {game['total']} ({game['over_odds']})** — PASS")
                    with col_tot2:
                        tier = total_res['under_tier']
                        bolt = total_res['under_bolt']
                        if tier != "PASS":
                            st.success(f"**UNDER {game['total']} ({game['under_odds']})**")
                            st.caption(f"{bolt} | Edge: {total_res['under_edge']:.1%} | Prob: {total_res['under_prob']:.1%} | Proj: {total_res['projection']:.1f}")
                        else:
                            st.error(f"**UNDER {game['total']} ({game['under_odds']})** — PASS")
                st.markdown("---")

        st.markdown("---")
        st.subheader("Manual Entry (fallback)")
        with st.expander("Click to enter a game manually"):
            home_man = st.text_input("Home Team", key="game_home")
            away_man = st.text_input("Away Team", key="game_away")
            market_man = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="game_market")
            if market_man == "ML":
                home_odds = st.number_input("Home Odds", value=-110, key="ml_home")
                away_odds = st.number_input("Away Odds", value=-110, key="ml_away")
                if st.button("Analyze ML (Manual)"):
                    res = analyze_moneyline_advanced(home_man, away_man, sport2, home_odds, away_odds)
                    st.markdown(f"{home_man}: {'✅ '+res['home_tier'] if res['home_tier']!='PASS' else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                    st.markdown(f"{away_man}: {'✅ '+res['away_tier'] if res['away_tier']!='PASS' else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
            elif market_man == "SPREAD":
                spread = st.number_input("Spread (home margin)", value=-5.5, key="spread_line")
                odds_sp = st.number_input("Odds", value=-110, key="spread_odds")
                if st.button("Analyze Spread (Manual)"):
                    res = analyze_spread_advanced(home_man, away_man, sport2, spread, odds_sp)
                    st.markdown(f"{home_man} {spread:+.1f}: {'✅ '+res['home_tier'] if res['home_tier']!='PASS' else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                    st.markdown(f"{away_man} {spread:+.1f}: {'✅ '+res['away_tier'] if res['away_tier']!='PASS' else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
            else:
                total = st.number_input("Total Line", value=220.5, key="total_line")
                over_odds = st.number_input("Over Odds", value=-110, key="over_odds")
                under_odds = st.number_input("Under Odds", value=-110, key="under_odds")
                if st.button("Analyze Total (Manual)"):
                    res = analyze_total_advanced(home_man, away_man, sport2, total, over_odds, under_odds)
                    st.markdown(f"OVER {total}: {'✅ '+res['over_tier'] if res['over_tier']!='PASS' else '❌ PASS'} (Edge: {res['over_edge']:.1%})")
                    st.markdown(f"UNDER {total}: {'✅ '+res['under_tier'] if res['under_tier']!='PASS' else '❌ PASS'} (Edge: {res['under_edge']:.1%})")

    # ---------- Tab 2: BEST BETS (parlays from approved bets) ----------
    with tabs[2]:
        st.header("🏆 Best Bets – Parlays (2-4 legs) from Clarity Approved")
        st.markdown("Automatically generated from fetched props (edge > 4%) and loaded game lines (edge > 2%). Minimum 2 legs required. Same‑game parlays are automatically filtered to avoid conflicts.")
        if st.button("🔄 Refresh Best Bets"):
            st.session_state['generate_best_bets'] = True
        approved_bets = []
        plus_ev_bets = []
        if 'live_props' in st.session_state and st.session_state['live_props']:
            for prop in st.session_state['live_props']:
                res_over = analyze_prop(prop.player_name, prop.stat_type, prop.line_score, "OVER", sport2, -110, new_bankroll)
                res_under = analyze_prop(prop.player_name, prop.stat_type, prop.line_score, "UNDER", sport2, -110, new_bankroll)
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
                        "unique_key": prop.player_name + prop.stat_type + best_pick,
                        "sport": sport2,
                        "team": prop.team,
                        "opponent": ""
                    })
                elif best_edge > 0:
                    plus_ev_bets.append({
                        "description": f"{prop.player_name} {prop.stat_type} {best_pick} {prop.line_score}",
                        "edge": best_edge,
                        "prob": res['prob'],
                        "odds": -110,
                        "unique_key": prop.player_name + prop.stat_type + best_pick
                    })
        if 'auto_games' in st.session_state and st.session_state['auto_games']:
            for game in st.session_state['auto_games']:
                sport_g = game.get('sport', 'NBA')
                home = game.get('home_team', '')
                away = game.get('away_team', '')
                if game.get('home_ml') and game.get('away_ml'):
                    ml_res = analyze_moneyline_advanced(home, away, sport_g, game['home_ml'], game['away_ml'])
                    if ml_res['home_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{home} ML ({game['home_ml']})",
                            "edge": ml_res['home_edge'],
                            "prob": ml_res['home_prob'],
                            "odds": game['home_ml'],
                            "unique_key": f"{home}_ML",
                            "sport": sport_g,
                            "team": home,
                            "opponent": away,
                            "market": "ML"
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
                            "unique_key": f"{away}_ML",
                            "sport": sport_g,
                            "team": away,
                            "opponent": home,
                            "market": "ML"
                        })
                    elif ml_res['away_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{away} ML ({game['away_ml']})",
                            "edge": ml_res['away_edge'],
                            "prob": ml_res['away_prob'],
                            "odds": game['away_ml'],
                            "unique_key": f"{away}_ML"
                        })
                if game.get('spread') is not None and game.get('spread_odds'):
                    spread_res = analyze_spread_advanced(home, away, game['spread'], game['spread_odds'], sport_g)
                    if spread_res['home_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{home} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['home_edge'],
                            "prob": spread_res['home_cover_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{home}_spread",
                            "sport": sport_g,
                            "team": home,
                            "opponent": away,
                            "market": "SPREAD"
                        })
                    elif spread_res['home_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{home} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['home_edge'],
                            "prob": spread_res['home_cover_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{home}_spread"
                        })
                    if spread_res['away_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"{away} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['away_edge'],
                            "prob": spread_res['away_cover_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{away}_spread",
                            "sport": sport_g,
                            "team": away,
                            "opponent": home,
                            "market": "SPREAD"
                        })
                    elif spread_res['away_edge'] > 0:
                        plus_ev_bets.append({
                            "description": f"{away} {game['spread']:+.1f} ({game['spread_odds']})",
                            "edge": spread_res['away_edge'],
                            "prob": spread_res['away_cover_prob'],
                            "odds": game['spread_odds'],
                            "unique_key": f"{away}_spread"
                        })
                if game.get('total') is not None and game.get('over_odds') and game.get('under_odds'):
                    total_res = analyze_total_advanced(home, away, sport_g, game['total'], game['over_odds'], game['under_odds'])
                    if total_res['over_edge'] > 0.02:
                        approved_bets.append({
                            "description": f"OVER {game['total']} ({game['over_odds']})",
                            "edge": total_res['over_edge'],
                            "prob": total_res['over_prob'],
                            "odds": game['over_odds'],
                            "unique_key": f"OVER_{game['total']}",
                            "sport": sport_g,
                            "team": "",
                            "opponent": "",
                            "market": "TOTAL"
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
                            "unique_key": f"UNDER_{game['total']}",
                            "sport": sport_g,
                            "team": "",
                            "opponent": "",
                            "market": "TOTAL"
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

    # ---------- Tab 3: Paste & Scan (with OCR and enhanced parser) ----------
    with tabs[3]:
        st.header("Paste & Scan Slips")
        st.markdown("Paste any slip (single game, parlay, multiple sports) – Clarity will extract individual bets and explain why you won or lost.")
        
        text_key = "slip_text_input"
        text = st.text_area("Paste slip text", height=200, key=text_key)
        col_clear, col_scan = st.columns([1, 4])
        with col_clear:
            if st.button("🗑️ Clear Text", use_container_width=True):
                if text_key in st.session_state:
                    del st.session_state[text_key]
                st.rerun()
        with col_scan:
            scan_clicked = st.button("🔍 Scan & Analyze (Text)", type="primary", use_container_width=True)
        
        st.markdown("---")
        st.subheader("📸 Or upload screenshots (multiple)")
        
        uploader_key = "screenshot_uploader"
        uploaded_images = st.file_uploader("Choose images (JPG, PNG, WEBP)", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True, key=uploader_key)
        use_for_sem = st.checkbox("Use these external slips for SEM calibration (improves model, does NOT affect your bankroll)", value=True)
        
        col_img_clear, col_img_scan = st.columns([1, 4])
        with col_img_clear:
            if st.button("🗑️ Clear Images", use_container_width=True):
                if uploader_key in st.session_state:
                    del st.session_state[uploader_key]
                if "ocr_results" in st.session_state:
                    del st.session_state["ocr_results"]
                st.rerun()
        with col_img_scan:
            ocr_clicked = st.button("📷 Extract & Analyze Images", use_container_width=True)
        
        if ocr_clicked and uploaded_images:
            ocr_api_key = st.secrets.get("OCR_SPACE_API_KEY", "K89641020988957")
            if not ocr_api_key:
                st.error("OCR API key missing. Please add OCR_SPACE_API_KEY to your secrets.")
            else:
                all_text = ""
                progress_bar = st.progress(0)
                status_text = st.empty()
                for i, img_file in enumerate(uploaded_images):
                    status_text.text(f"Processing image {i+1} of {len(uploaded_images)}...")
                    img_bytes = img_file.read()
                    extracted_text, error = ocr_image(img_bytes, ocr_api_key)
                    if error:
                        st.error(f"OCR failed for {img_file.name}: {error}")
                    else:
                        all_text += extracted_text + "\n\n"
                    progress_bar.progress((i+1)/len(uploaded_images))
                status_text.empty()
                if all_text.strip():
                    st.success("OCR complete. Parsing bets...")
                    parsed_bets = parse_complex_slip(all_text)
                    if not parsed_bets:
                        st.error("No bets recognized in the extracted text. Check image quality or try pasting manually.")
                    else:
                        st.success(f"Detected {len(parsed_bets)} bets from images.")
                        st.session_state["ocr_results"] = parsed_bets
                        for bet in parsed_bets:
                            explanation = generate_why_analysis(bet)
                            with st.expander(f"{bet.get('sport', 'UNK')} – {bet.get('team', '')} {bet.get('market_type', 'ML')} at {bet.get('odds', '?')}"):
                                st.markdown(explanation)
                                if use_for_sem and bet.get('result') in ['WIN', 'LOSS'] and bet.get('prob') is not None:
                                    insert_external_slip(bet.get('prob', 0.5), bet.get('result'), source="OCR")
                                    st.caption("✅ This slip was used for SEM calibration (model improvement). Your bankroll and personal history unchanged.")
                                elif bet.get('result') in ['WIN', 'LOSS']:
                                    st.caption("ℹ️ Slip result recorded but not used for SEM calibration (toggle off).")
                                else:
                                    st.caption("⚠️ Could not determine result or probability – not used for SEM.")
                else:
                    st.warning("No text extracted from the uploaded images.")
        elif ocr_clicked and not uploaded_images:
            st.warning("Please upload at least one image.")
        
        if scan_clicked:
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
                                    "odds": bet.get('odds', 0),
                                    "edge": 0,
                                    "prob": 0.5,
                                    "kelly": 0,
                                    "tier": "",
                                    "bolt_signal": "",
                                    "result": bet.get('result'),
                                    "actual": 0,
                                    "settled_date": datetime.now().strftime("%Y-%m-%d"),
                                    "profit": profit,
                                    "bankroll": new_bankroll
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
                                    "prob": bet.get('prob', 0.5),
                                    "kelly": 0,
                                    "tier": "",
                                    "bolt_signal": "",
                                    "result": bet.get('result'),
                                    "actual": bet.get('actual', 0),
                                    "settled_date": datetime.now().strftime("%Y-%m-%d"),
                                    "profit": profit,
                                    "bankroll": new_bankroll
                                })
                                st.success("Bet added to history (self‑evaluation updated).")

    # ---------- Tab 4: History & Metrics ----------
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
        df_recent = get_all_slips(limit=50)
        if df_recent.empty:
            st.info("No bets yet.")
        else:
            st.dataframe(df_recent[["date", "type", "player", "team", "market", "pick", "result", "profit"]])

    # ---------- Tab 5: Tools (with Health Dashboard) ----------
    with tabs[5]:
        st.header("Tools")
        st.subheader("📡 System Health Dashboard")
        st.markdown("This dashboard shows the real‑time status of all data sources and modules. Red ❌ means the component failed and a fallback was used. Yellow ⚠️ means a fallback is active. Green ✅ means the component is working normally.")
        if st.button("🔄 Refresh Health Status"):
            st.rerun()
        health_data = []
        for component, info in st.session_state.health_status.items():
            status_icon = "✅" if info["status"] == "ok" else ("⚠️" if info["fallback_active"] else "❌")
            health_data.append({
                "Component": component,
                "Status": status_icon,
                "Fallback Active": "Yes" if info["fallback_active"] else "No",
                "Last Error": info["last_error"][:80] + "..." if len(info["last_error"]) > 80 else info["last_error"]
            })
        health_df = pd.DataFrame(health_data)
        st.dataframe(health_df, use_container_width=True)
        st.subheader("⚙️ System Information")
        st.info(f"curl_cffi (TLS impersonation): {'✅ Available' if CURL_AVAILABLE else '❌ Not installed'}")
        st.info(f"BallsDontLie (NBA): {'✅ Set' if st.secrets.get('BALLSDONTLIE_API_KEY') else '❌ Missing'}")
        st.info(f"Odds‑API.io (game lines): {'✅ Set' if st.secrets.get('ODDS_API_IO_KEY') and st.secrets.get('ODDS_API_IO_KEY') != 'your_key_here' else '❌ Missing'}")
        st.info(f"RapidAPI (Tennis): {'✅ Set' if st.secrets.get('RAPIDAPI_KEY') and st.secrets.get('RAPIDAPI_KEY') != 'YOUR_RAPIDAPI_KEY_HERE' else '❌ Missing'}")
        st.info(f"nhl-api-py: {'✅ Available' if NHL_AVAILABLE else '❌ Not installed'}")
        st.info(f"pgatourPY: {'✅ Available' if PGA_AVAILABLE else '❌ Not installed'}")
        st.info(f"Current thresholds: PROB_BOLT = {PROB_BOLT:.2f}, DTM_BOLT = {DTM_BOLT:.3f}")
        st.info(f"Fractional Kelly multiplier: {KELLY_FRACTION:.0%}")
        st.caption("Self‑evaluation runs automatically when you settle bets or paste winning/losing slips. Auto‑tune adjusts thresholds weekly.")
        if os.path.exists("clarity_debug.log"):
            with open("clarity_debug.log", "r") as f:
                log_content = f.read()
            st.download_button("📥 Download Debug Log", data=log_content, file_name="clarity_debug.log", mime="text/plain")
        else:
            st.info("No log file yet. Logging will start after this deployment.")

if __name__ == "__main__":
    main()
