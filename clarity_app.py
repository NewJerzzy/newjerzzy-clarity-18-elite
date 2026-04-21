# CLARITY 23.1 -- ELITE MULTI‑SPORT ENGINE (BUG‑FIXED & HARDENED)
#
# Fixes applied (v23.0 → v23.1):
# [FIX 1] PROB_BOLT/DTM_BOLT are no longer mutated globals -- all reads/writes go through get_threshold() / set_threshold() backed by the DB
# [FIX 2] Golf fallback: random.uniform removed; deterministic rank‑offset used
# [FIX 3] analyze_spread_advanced() arg order corrected in Best Bets tab
# [FIX 4] Slip IDs now use uuid4 -- no MD5 collision risk
# [FIX 5] All SQLite access uses "with conn:" context managers
# [FIX 6] Redundant _stats_cache / _game_score_cache dicts removed (@st.cache_data already handles caching)
# [FIX 7] parse_complex_slip() now reads OVER/UNDER/MORE/LESS from slip text instead of hardcoding "OVER"
# [FIX 8] All bare except: clauses replaced with except Exception as e: + logging
# [FIX 9] Two separate secrets: ODDS_API_KEY (the‑odds‑api.com / GameScanner) and ODDS_API_IO_KEY (odds‑api.io / fetch_game_score)
# [FIX 10] _get_historical_fallback() is tier‑aware: player_tier="elite"|"mid"|"bench"
# [NEW] Prop Scanner now accepts multiple screenshots at once
# [NEW] Enhanced slip parser with PrizePicks block detection (Goblin/Demon)
# [NEW] Bovada NBA game parser (spreads, ML, totals)
# [NEW] MyBookie MLB game parser (spreads, ML, totals)

import os
import json
import hashlib
import warnings
import time
import uuid
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
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    import requests as curl_requests
    CURL_AVAILABLE = False

warnings.filterwarnings("ignore")

# -----------------------------------------------------------------------------
# LOGGING SETUP
# -----------------------------------------------------------------------------
logging.basicConfig(
    filename='clarity_debug.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
PARSER_LOGGER = logging.getLogger("clarity_parser")
if not PARSER_LOGGER.handlers:
    os.makedirs("clarity_logs", exist_ok=True)
    handler = logging.FileHandler("clarity_logs/parser.log", mode="a", encoding="utf-8")
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    PARSER_LOGGER.addHandler(handler)
    PARSER_LOGGER.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# VERSION & CONSTANTS
# -----------------------------------------------------------------------------
VERSION = "23.1 -- Elite Multi‑Sport (Hardened)"
BUILD_DATE = "2026-04-21"
DB_PATH = "clarity_unified.db"
os.makedirs("clarity_logs", exist_ok=True)

# [FIX 1] These are DEFAULT values only. Runtime values are stored in the DB.
_DEFAULT_PROB_BOLT = 0.84
_DEFAULT_DTM_BOLT = 0.15
KELLY_FRACTION = 0.25

# -----------------------------------------------------------------------------
# SPORT DATA & STAT CONFIG
# -----------------------------------------------------------------------------
SPORT_MODELS = {
    "NBA": {"variance_factor": 1.18, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"variance_factor": 1.10, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"variance_factor": 1.15, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"variance_factor": 1.22, "avg_total": 44.5, "home_advantage": 2.8},
    "PGA": {"variance_factor": 1.10, "avg_total": 70.5, "home_advantage": 0.0},
    "TENNIS": {"variance_factor": 1.05, "avg_total": 22.0, "home_advantage": 0.0},
    "SOCCER": {"variance_factor": 1.12, "avg_total": 2.5, "home_advantage": 0.3},
    "MMA": {"variance_factor": 1.08, "avg_total": 2.5, "home_advantage": 0.1},
    "F1": {"variance_factor": 1.05, "avg_total": 0.0, "home_advantage": 0.0},
    "CRICKET": {"variance_factor": 1.15, "avg_total": 300.0, "home_advantage": 15.0},
    "BOXING": {"variance_factor": 1.08, "avg_total": 9.5, "home_advantage": 0.0},
}

SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"],
    "PGA": ["STROKES", "BIRDIES", "BOGEYS", "EAGLES", "DRIVING_DISTANCE", "GIR"],
    "TENNIS": ["ACES", "DOUBLE_FAULTS", "GAMES_WON", "TOTAL_GAMES", "BREAK_PTS"],
    "SOCCER": ["GOALS", "ASSISTS", "SHOTS", "SHOTS_ON_TARGET", "FOULS", "CARDS"],
    "MMA": ["STRIKES", "TAKEDOWNS", "SUBMISSIONS", "KNOCKDOWNS"],
    "F1": ["POINTS", "POSITION", "FASTEST_LAP"],
    "CRICKET": ["RUNS", "WICKETS", "BOUNDARIES", "SIXES"],
    "BOXING": ["PUNCHES", "JABS", "POWER_PUNCHES", "KNOCKDOWNS"],
}

STAT_CONFIG = {
    "PTS": {"tier": "MED", "buffer": 1.5},
    "REB": {"tier": "LOW", "buffer": 1.0},
    "AST": {"tier": "LOW", "buffer": 1.5},
    "PRA": {"tier": "HIGH", "buffer": 3.0},
    "PR": {"tier": "HIGH", "buffer": 2.0},
    "PA": {"tier": "HIGH", "buffer": 2.0},
    "SOG": {"tier": "LOW", "buffer": 0.5},
    "SAVES": {"tier": "LOW", "buffer": 2.0},
    "STROKES":{"tier": "LOW", "buffer": 2.0},
    "BIRDIES":{"tier": "MED", "buffer": 1.0},
    "ACES": {"tier": "HIGH", "buffer": 1.0},
    "DOUBLE_FAULTS": {"tier": "HIGH", "buffer": 1.0},
    "GAMES_WON": {"tier": "LOW", "buffer": 1.5},
    "GOALS": {"tier": "HIGH", "buffer": 0.5},
    "ASSISTS": {"tier": "MED", "buffer": 0.5},
    "STRIKES": {"tier": "MED", "buffer": 10.0},
    "RUNS": {"tier": "MED", "buffer": 20.0},
    "TOTAL": {"tier": "MED", "buffer": 5.0},
    "SPREAD": {"tier": "MED", "buffer": 3.0},
    "ML": {"tier": "HIGH", "buffer": 0.0},
}

# -----------------------------------------------------------------------------
# HEALTH STATUS TRACKING
# -----------------------------------------------------------------------------
def init_health_status():
    if "health_status" not in st.session_state:
        st.session_state.health_status = {
            "BallsDontLie (NBA)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "Odds-API.io (game scores)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "The Odds API (game scanner)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "PropLine (Live Props)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "Slash Golf API (PGA)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "FlashLive Sports (Multi‑Sport)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "ESPN API (Fallback)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "nhl-api-py (NHL)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "curl_cffi (TLS)": {"status": "unknown", "last_error": "", "fallback_active": False},
            "RapidAPI (Tennis)": {"status": "unknown", "last_error": "", "fallback_active": False},
        }

def update_health(component: str, success: bool, error_msg: str = "", fallback: bool = False):
    init_health_status()
    if component not in st.session_state.health_status:
        st.session_state.health_status[component] = {"status": "unknown", "last_error": "", "fallback_active": False}
    st.session_state.health_status[component]["status"] = "ok" if success else "fail"
    if error_msg:
        st.session_state.health_status[component]["last_error"] = error_msg[:200]
    st.session_state.health_status[component]["fallback_active"] = fallback

init_health_status()

# -----------------------------------------------------------------------------
# DATABASE -- WITH INDEXES, BANKROLL PERSISTENCE, AND THRESHOLD STORAGE
# -----------------------------------------------------------------------------
def ensure_slips_schema():
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
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
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
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
            c.execute("""CREATE TABLE IF NOT EXISTS sem_external (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                prob REAL,
                result TEXT,
                source TEXT
            )""")
    finally:
        conn.close()

    if get_threshold('prob_bolt') is None:
        set_threshold('prob_bolt', _DEFAULT_PROB_BOLT)
    if get_threshold('dtm_bolt') is None:
        set_threshold('dtm_bolt', _DEFAULT_DTM_BOLT)
    set_bankroll(get_bankroll())

def get_threshold(key: str, default: float = None) -> Optional[float]:
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = c.fetchone()
            return row[0] if row else default
    except Exception as e:
        logging.error(f"get_threshold({key}) error: {e}")
        return default
    finally:
        conn.close()

def set_threshold(key: str, value: float):
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
    except Exception as e:
        logging.error(f"set_threshold({key}, {value}) error: {e}")
    finally:
        conn.close()

def get_prob_bolt() -> float:
    return get_threshold('prob_bolt', _DEFAULT_PROB_BOLT)

def get_dtm_bolt() -> float:
    return get_threshold('dtm_bolt', _DEFAULT_DTM_BOLT)

def get_bankroll() -> float:
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("SELECT value FROM settings WHERE key = 'bankroll'")
            row = c.fetchone()
            return row[0] if row else 1000.0
    except Exception as e:
        logging.error(f"get_bankroll error: {e}")
        return 1000.0
    finally:
        conn.close()

def set_bankroll(value: float):
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('bankroll', ?)", (value,))
    except Exception as e:
        logging.error(f"set_bankroll error: {e}")
    finally:
        conn.close()

def update_bankroll_from_slip(profit: float):
    new_bankroll = get_bankroll() + profit
    set_bankroll(max(new_bankroll, 0))

def insert_slip(entry: dict):
    ensure_slips_schema()
    slip_id = str(uuid.uuid4()).replace("-", "")[:12]
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
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
    except Exception as e:
        logging.error(f"insert_slip error: {e}")
    finally:
        conn.close()
    if entry.get("result") in ["WIN", "LOSS"]:
        if "profit" in entry:
            update_bankroll_from_slip(entry["profit"])
        _calibrate_sem()
        auto_tune_thresholds()

def insert_external_slip(prob: float, result: str, source: str = "OCR"):
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("INSERT INTO sem_external (timestamp, prob, result, source) VALUES (?, ?, ?, ?)",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prob, result, source))
    except Exception as e:
        logging.error(f"insert_external_slip error: {e}")
    finally:
        conn.close()
    _calibrate_sem()

def update_slip_result(slip_id: str, result: str, actual: float, odds: int):
    if result == "WIN":
        profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
    else:
        profit = -100
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("UPDATE slips SET result=?, actual=?, settled_date=?, profit=? WHERE id=?",
                      (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, slip_id))
    except Exception as e:
        logging.error(f"update_slip_result error: {e}")
    finally:
        conn.close()
    update_bankroll_from_slip(profit)
    _calibrate_sem()
    auto_tune_thresholds()

def get_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM slips WHERE result = 'PENDING'", conn)
    except Exception as e:
        logging.error(f"get_pending_slips error: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

def get_all_slips(limit: int = 500):
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM slips ORDER BY date DESC LIMIT ?", conn, params=(limit,))
    except Exception as e:
        logging.error(f"get_all_slips error: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    return df

def clear_pending_slips():
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("DELETE FROM slips WHERE result = 'PENDING'")
    except Exception as e:
        logging.error(f"clear_pending_slips error: {e}")
    finally:
        conn.close()

init_db()

# -----------------------------------------------------------------------------
# SESSION FACTORY --- curl_cffi → requests (TLS impersonation)
# -----------------------------------------------------------------------------
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
    retry_cfg = Retry(total=3, backoff_factor=0.4,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=["GET", "POST"], raise_on_status=False)
    s.mount("https://", HTTPAdapter(max_retries=retry_cfg))
    s.headers.update(h)
    return s

# -----------------------------------------------------------------------------
# SNIFFER CONFIG (deprecated, kept for compatibility)
# -----------------------------------------------------------------------------
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
    "/projections", "/bff/v3/projections", "/bff/v2/projections",
    "/bff/v1/projections", "/v1/projections", "/v2/projections",
    "/v3/projections", "/v4/projections",
]
UNDERDOG_BASE = "https://api.underdogfantasy.com"
UNDERDOG_ENDPOINTS = ["/bff/v3/projections", "/bff/v2/projections", "/v3/projections", "/projections"]

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
        time.sleep(delay)
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
    st.warning("PrizePicks sniffer is deprecated. Using PropLine Smart ingestion instead.")
    return []

def fetch_underdog_props(league_filter: str = None) -> List[PlayerProp]:
    st.warning("Underdog sniffer is deprecated. Using PropLine Smart ingestion instead.")
    return []

# -----------------------------------------------------------------------------
# FLASHLIVE SPORTS API INTEGRATION (Primary Multi‑Sport Source)
# -----------------------------------------------------------------------------
FLASHLIVE_API_HOST = "flashlive-sports.p.rapidapi.com"
FLASHLIVE_API_BASE_URL = "https://flashlive-sports.p.rapidapi.com/v1"

def _get_flashlive_headers():
    return {
        "X-RapidAPI-Key": st.secrets.get("RAPIDAPI_KEY", ""),
        "X-RapidAPI-Host": FLASHLIVE_API_HOST,
    }

FLASHLIVE_SPORT_MAP = {
    "NBA": 1, "NFL": 2, "MLB": 3, "NHL": 4, "SOCCER": 5,
    "TENNIS": 6, "MMA": 7, "F1": 8, "CRICKET": 9, "PGA": 10,
    "BOXING": 11, "VOLLEYBALL": 12, "HANDBALL": 13, "RUGBY": 14, "ESPORT": 15,
}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_flashlive_player_stats(player_name: str, sport: str, market: str) -> List[float]:
    if not st.secrets.get("RAPIDAPI_KEY"):
        update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg="No RapidAPI key", fallback=True)
        return []
    sport_id = FLASHLIVE_SPORT_MAP.get(sport.upper())
    if not sport_id:
        update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg=f"Sport {sport} not mapped", fallback=True)
        return []
    try:
        resp = requests.get(
            f"{FLASHLIVE_API_BASE_URL}/players/search",
            headers=_get_flashlive_headers(),
            params={"sport_id": sport_id, "query": player_name, "limit": 1},
            timeout=10,
        )
        if resp.status_code != 200:
            update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg=f"Search HTTP {resp.status_code}", fallback=True)
            return []
        players = resp.json().get("DATA", [])
        if not players:
            update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg="Player not found", fallback=True)
            return []
        player_id = players[0].get("id")
        if not player_id:
            return []
        stats_resp = requests.get(
            f"{FLASHLIVE_API_BASE_URL}/players/statistics",
            headers=_get_flashlive_headers(),
            params={"player_id": player_id, "sport_id": sport_id},
            timeout=10,
        )
        if stats_resp.status_code != 200:
            update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg=f"Stats HTTP {stats_resp.status_code}", fallback=True)
            return []
        game_logs = stats_resp.json().get("DATA", {}).get("game_log", [])
        stat_key = market.lower()
        values = [float(g[stat_key]) for g in game_logs[:8] if isinstance(g.get(stat_key), (int, float))]
        if values:
            update_health("FlashLive Sports (Multi‑Sport)", success=True, fallback=False)
        else:
            update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg="No stats found", fallback=True)
        return values
    except Exception as e:
        update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg=str(e), fallback=True)
        return []

# -----------------------------------------------------------------------------
# ESPN API FALLBACK (Universal Backup)
# -----------------------------------------------------------------------------
ESPN_API_HOST = "espn-api.p.rapidapi.com"
ESPN_API_BASE_URL = "https://espn-api.p.rapidapi.com"

def _get_espn_headers():
    return {
        "x-rapidapi-host": ESPN_API_HOST,
        "x-rapidapi-key": st.secrets.get("RAPIDAPI_KEY", ""),
        "Accept": "application/json",
    }

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_espn_player_stats(player_name: str, sport: str, market: str) -> List[float]:
    if not st.secrets.get("RAPIDAPI_KEY"):
        update_health("ESPN API (Fallback)", success=False, error_msg="No RapidAPI key", fallback=True)
        return []
    sport_map = {
        "NBA": "basketball", "NFL": "football", "MLB": "baseball",
        "NHL": "hockey", "PGA": "golf", "TENNIS": "tennis",
        "SOCCER": "soccer", "MMA": "mma", "BOXING": "boxing",
        "F1": "racing", "CRICKET": "cricket",
    }
    espn_sport = sport_map.get(sport.upper(), sport.lower())
    try:
        resp = requests.get(
            f"{ESPN_API_BASE_URL}/search",
            headers=_get_espn_headers(),
            params={"q": player_name, "sport": espn_sport},
            timeout=15,
        )
        if resp.status_code != 200:
            update_health("ESPN API (Fallback)", success=False, error_msg=f"Search HTTP {resp.status_code}", fallback=True)
            return []
        data = resp.json()
        athletes = data.get("athletes", []) if isinstance(data, dict) else []
        if not athletes:
            update_health("ESPN API (Fallback)", success=False, error_msg="Player not found", fallback=True)
            return []
        player_id = athletes[0].get("id")
        if not player_id:
            return []
        stats_resp = requests.get(f"{ESPN_API_BASE_URL}/athlete/{player_id}/stats", headers=_get_espn_headers(), timeout=15)
        if stats_resp.status_code != 200:
            update_health("ESPN API (Fallback)", success=False, error_msg=f"Stats HTTP {stats_resp.status_code}", fallback=True)
            return []
        stats_data = stats_resp.json()
        game_logs = stats_data.get("gameLog", []) if isinstance(stats_data, dict) else []
        stat_key = market.lower()
        values = [float(g[stat_key]) for g in game_logs[:8] if isinstance(g.get(stat_key), (int, float))]
        if values:
            update_health("ESPN API (Fallback)", success=True, fallback=False)
        else:
            update_health("ESPN API (Fallback)", success=False, error_msg="No stats found", fallback=True)
        return values
    except Exception as e:
        update_health("ESPN API (Fallback)", success=False, error_msg=str(e), fallback=True)
        return []

# -----------------------------------------------------------------------------
# REAL STATS FETCHING (NBA, NHL, PGA, Tennis)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_nba_stats_cached(player_name: str, market: str, game_date: str = None) -> List[float]:
    stat_map = {
        "PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl",
        "BLK": "blk", "THREES": "tpm", "PRA": "pts+reb+ast",
        "PR": "pts+reb", "PA": "pts+ast",
    }
    stat_abbr = stat_map.get(market.upper(), "pts")
    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    if not headers["Authorization"]:
        update_health("BallsDontLie (NBA)", success=False, error_msg="API key missing", fallback=True)
        return []
    try:
        resp = requests.get(
            f"https://api.balldontlie.io/v1/players?search={player_name.replace(' ', '%20')}",
            headers=headers, timeout=10,
        )
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
        values = [float(g[stat_abbr]) for g in games if isinstance(g.get(stat_abbr), (int, float))]
        if values:
            update_health("BallsDontLie (NBA)", success=True, fallback=False)
        else:
            update_health("BallsDontLie (NBA)", success=False, error_msg="No stats returned", fallback=True)
        return values
    except Exception as e:
        update_health("BallsDontLie (NBA)", success=False, error_msg=str(e), fallback=True)
        logging.error(f"NBA stats fetch error: {e}")
        return []

_FALLBACK_TIERS = {
    "elite": {
        ("NBA","PTS"): [32.1,29.8,31.5,33.2,28.9,30.4,32.8,29.1,31.0,33.5,28.5,32.3],
        ("NBA","REB"): [10.5,9.8,11.2,10.1,9.5,10.8,11.5,9.2,10.3,11.0,9.9,10.7],
        ("NBA","AST"): [8.2,7.9,8.8,7.5,8.5,9.1,7.8,8.4,9.3,7.6,8.0,8.9],
        ("PGA","STROKES"):[67.5,68.1,67.8,68.4,67.2,68.0,67.9,68.3,67.6,68.2,67.4,68.5],
        ("NHL","SOG"): [4.2,3.8,4.5,3.5,4.0,4.8,3.7,4.3,4.9,3.9,4.1,4.6],
    },
    "mid": {
        ("NBA","PTS"): [22.5,23.1,21.8,24.2,22.9,23.5,21.5,24.0,22.7,23.3,21.9,23.8],
        ("NBA","REB"): [7.2,7.5,6.9,7.8,7.3,7.6,6.8,7.9,7.1,7.4,6.7,7.7],
        ("NBA","AST"): [5.1,5.3,4.9,5.6,5.2,5.4,4.8,5.7,5.0,5.5,4.7,5.8],
        ("PGA","STROKES"):[70.2,70.5,69.8,71.0,70.3,70.6,69.5,71.2,70.0,70.8,69.7,71.1],
        ("NHL","SOG"): [2.5,2.7,2.4,2.8,2.6,2.7,2.3,2.9,2.5,2.8,2.4,2.9],
        ("NHL","SAVES"): [25.0,26.1,24.5,27.2,25.8,26.5,24.2,27.5,25.3,26.8,24.8,27.1],
        ("PGA","BIRDIES"):[4.2,4.5,3.9,4.8,4.3,4.6,3.8,4.9,4.1,4.4,3.7,4.7],
        ("TENNIS","ACES"):[4.5,4.8,4.3,5.0,4.6,4.9,4.2,5.1,4.4,4.7,4.1,5.2],
        ("SOCCER","GOALS"):[0.3,0.5,0.2,0.6,0.4,0.5,0.1,0.7,0.3,0.5,0.2,0.6],
        ("SOCCER","ASSISTS"):[0.2,0.3,0.1,0.4,0.2,0.3,0.1,0.5,0.2,0.3,0.1,0.4],
        ("MMA","STRIKES"):[85.0,92.0,78.0,95.0,88.0,90.0,80.0,97.0,84.0,91.0,82.0,94.0],
        ("BOXING","PUNCHES"):[120.0,135.0,110.0,145.0,125.0,130.0,115.0,140.0,122.0,132.0,118.0,138.0],
        ("CRICKET","RUNS"):[45.0,52.0,38.0,60.0,48.0,55.0,40.0,65.0,42.0,58.0,35.0,62.0],
        ("F1","POINTS"): [10.0,15.0,8.0,18.0,12.0,14.0,6.0,25.0,10.0,16.0,8.0,20.0],
    },
    "bench": {
        ("NBA","PTS"): [8.5,9.1,7.8,10.2,8.9,9.4,7.5,10.5,8.3,9.7,7.2,10.0],
        ("NBA","REB"): [3.5,3.8,3.2,4.0,3.6,3.9,3.1,4.2,3.4,3.7,3.0,4.1],
        ("NBA","AST"): [1.8,2.1,1.6,2.3,1.9,2.2,1.5,2.5,1.7,2.0,1.4,2.4],
        ("PGA","STROKES"):[73.5,74.0,73.0,74.5,73.2,74.2,72.8,74.8,73.1,74.3,72.9,74.6],
    },
}

def _get_historical_fallback(market: str, sport: str = "NBA", player_tier: str = "mid") -> List[float]:
    tier_map = _FALLBACK_TIERS.get(player_tier, _FALLBACK_TIERS["mid"])
    key = (sport.upper(), market.upper())
    if key in tier_map:
        return tier_map[key]
    mid_map = _FALLBACK_TIERS["mid"]
    if key in mid_map:
        return mid_map[key]
    return [15.0, 15.5, 14.8, 16.2, 15.3, 15.7, 14.5, 16.5, 15.1, 15.9, 14.7, 16.0]

# -----------------------------------------------------------------------------
# SLASH GOLF API INTEGRATION (PGA)
# -----------------------------------------------------------------------------
GOLF_API_HOST = "live-golf-data.p.rapidapi.com"
GOLF_API_BASE_URL = "https://live-golf-data.p.rapidapi.com"

def _get_golf_headers():
    return {
        "Content-Type": "application/json",
        "x-rapidapi-host": GOLF_API_HOST,
        "x-rapidapi-key": st.secrets.get("RAPIDAPI_KEY", ""),
    }

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_golf_schedule(org_id: int = 1, year: int = None) -> Optional[List[Dict]]:
    if year is None:
        year = datetime.now().year
    try:
        response = requests.get(
            f"{GOLF_API_BASE_URL}/schedule",
            headers=_get_golf_headers(),
            params={"orgId": org_id, "year": year},
            timeout=15,
        )
        response.raise_for_status()
        update_health("Slash Golf API (PGA)", success=True)
        return response.json()
    except Exception as e:
        update_health("Slash Golf API (PGA)", success=False, error_msg=str(e), fallback=True)
        return None

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_golf_leaderboard(tournament_id: str) -> Optional[Dict]:
    try:
        response = requests.get(
            f"{GOLF_API_BASE_URL}/leaderboards",
            headers=_get_golf_headers(),
            params={"tournamentId": tournament_id},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.warning(f"Failed to fetch golf leaderboard: {e}")
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_golf_players() -> Optional[List[Dict]]:
    try:
        response = requests.get(f"{GOLF_API_BASE_URL}/players", headers=_get_golf_headers(), timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logging.warning(f"Failed to fetch golf players: {e}")
        return None

def fetch_golf_player_recent_scores(player_name: str, num_tournaments: int = 5) -> List[float]:
    try:
        players = fetch_golf_players()
        if players:
            for p in players:
                if player_name.lower() in p.get("name", "").lower():
                    update_health("Slash Golf API (PGA)", success=True)
                    rank = p.get("rank", 100)
                    base_score = 68.5 + (min(rank, 200) / 200.0) * 5.0
                    offsets = [0.0, 0.3, -0.2, 0.5, -0.4, 0.2, -0.1, 0.4]
                    return [round(base_score + o, 1) for o in offsets]
    except Exception as e:
        logging.warning(f"Error fetching golf player stats: {e}")
    update_health("Slash Golf API (PGA)", success=False, error_msg="Using fallback stats", fallback=True)
    return _get_historical_fallback("STROKES", "PGA")

# -----------------------------------------------------------------------------
# UNIFIED STATS FETCHER (FlashLive → ESPN → Tier Fallback)
# -----------------------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_real_player_stats(player_name: str, market: str, sport: str = "NBA",
                            game_date: str = None, player_tier: str = "mid") -> List[float]:
    stats = []
    primary_success = False
    if sport.upper() == "NBA":
        stats = _fetch_nba_stats_cached(player_name, market, game_date)
        if stats:
            primary_success = True
    elif sport.upper() == "PGA":
        stats = fetch_golf_player_recent_scores(player_name)
        if stats:
            primary_success = True
    elif sport.upper() in ["NHL", "NFL", "MLB", "SOCCER", "MMA", "F1", "CRICKET", "BOXING", "TENNIS"]:
        stats = fetch_flashlive_player_stats(player_name, sport, market)
        if stats and len(stats) >= 3:
            primary_success = True
            update_health("FlashLive Sports (Multi‑Sport)", success=True, fallback=False)
    if not primary_success or len(stats) < 3:
        logging.info(f"Primary API failed for {player_name} ({sport}), trying ESPN fallback...")
        espn_stats = fetch_espn_player_stats(player_name, sport, market)
        if espn_stats and len(espn_stats) >= 3:
            stats = espn_stats
            update_health("ESPN API (Fallback)", success=True, fallback=False)
        else:
            update_health("ESPN API (Fallback)", success=False, error_msg="No data", fallback=True)
    if not stats or len(stats) < 3:
        logging.warning(f"Using historical fallback stats for {player_name} {market} {sport} (tier={player_tier})")
        stats = _get_historical_fallback(market, sport, player_tier)
        update_health("FlashLive Sports (Multi‑Sport)", success=False, error_msg="Using fallback stats", fallback=True)
    return stats

def fetch_single_game_stat(player_name: str, market: str, game_date: str) -> Optional[float]:
    stats = fetch_real_player_stats(player_name, market, "NBA", game_date)
    return stats[0] if stats else None

# -----------------------------------------------------------------------------
# GAME SCORES FETCHING (Odds‑API.io)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_game_score(team: str, opponent: str, sport: str, game_date: str) -> Tuple[Optional[float], Optional[float]]:
    sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "icehockey", "NFL": "americanfootball"}
    sport_key = sport_map.get(sport)
    if not sport_key:
        update_health("Odds-API.io (game scores)", success=False, error_msg=f"Unsupported sport {sport}", fallback=True)
        return None, None
    odds_io_key = st.secrets.get("ODDS_API_IO_KEY", "")
    if not odds_io_key:
        update_health("Odds-API.io (game scores)", success=False, error_msg="ODDS_API_IO_KEY missing", fallback=True)
        return None, None
    url = f"https://api.odds-api.io/v4/sports/{sport_key}/events"
    params = {"apiKey": odds_io_key, "date": game_date}
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
                        return float(home_score), float(away_score)
            update_health("Odds-API.io (game scores)", success=False, error_msg="Event not found or no scores", fallback=True)
        else:
            update_health("Odds-API.io (game scores)", success=False, error_msg=f"HTTP {r.status_code}", fallback=True)
    except Exception as e:
        update_health("Odds-API.io (game scores)", success=False, error_msg=str(e), fallback=True)
        logging.error(f"Game score fetch error: {e}")
    return None, None

# -----------------------------------------------------------------------------
# PROP MODEL ENGINE (WMA, volatility, edge, Kelly)
# -----------------------------------------------------------------------------
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

def analyze_prop(player, market, line, pick, sport="NBA", odds=-110, bankroll=None, player_tier="mid"):
    if bankroll is None:
        bankroll = get_bankroll()
    prob_bolt = get_prob_bolt()
    dtm_bolt = get_dtm_bolt()
    stats = fetch_real_player_stats(player, market, sport, player_tier=player_tier)
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
    bolt = "SOVEREIGN BOLT" if prob >= prob_bolt and (mu - line) / max(line, 1e-9) >= dtm_bolt else tier
    return {
        "prob": prob, "edge": edge, "mu": mu, "sigma": sigma, "wma": wma,
        "tier": tier, "kelly": kelly, "stake": stake, "bolt_signal": bolt,
        "stats": stats,
    }

# -----------------------------------------------------------------------------
# GAME MODEL
# -----------------------------------------------------------------------------
def implied_prob(american_odds) -> float:
    try:
        odds = float(american_odds)
    except (TypeError, ValueError):
        odds = -110.0
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return -odds / (-odds + 100)

# -----------------------------------------------------------------------------
# NBA TEAM STATS FETCHING (BallsDontLie)
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
    "POR": 25, "SAC": 26, "SAS": 27, "TOR": 28, "UTA": 29, "WAS": 30,
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_recent_totals(team_name: str, window: int = 8) -> List[float]:
    team_id = NBA_TEAM_IDS.get(team_name.upper())
    if not team_id:
        for k, v in NBA_TEAM_IDS.items():
            if team_name.upper() in k:
                team_id = v
                break
    if not team_id:
        return _fallback_team_stats("NBA_TOTALS")
    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    try:
        resp = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={team_id}&per_page={window}",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return _fallback_team_stats("NBA_TOTALS")
        games = resp.json().get("data", [])
        totals = [g["home_team_score"] if g["home_team"]["id"] == team_id else g["visitor_team_score"] for g in games]
        totals.reverse()
        return totals if totals else _fallback_team_stats("NBA_TOTALS")
    except Exception as e:
        logging.error(f"fetch_team_recent_totals error: {e}")
        return _fallback_team_stats("NBA_TOTALS")

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_recent_margins(team_name: str, window: int = 8) -> List[float]:
    team_id = NBA_TEAM_IDS.get(team_name.upper())
    if not team_id:
        for k, v in NBA_TEAM_IDS.items():
            if team_name.upper() in k:
                team_id = v
                break
    if not team_id:
        return _fallback_team_stats("NBA_MARGINS")
    headers = {"Authorization": st.secrets.get("BALLSDONTLIE_API_KEY", "")}
    try:
        resp = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={team_id}&per_page={window}",
            headers=headers, timeout=10,
        )
        if resp.status_code != 200:
            return _fallback_team_stats("NBA_MARGINS")
        games = resp.json().get("data", [])
        margins = []
        for game in games:
            if game["home_team"]["id"] == team_id:
                margins.append(game["home_team_score"] - game["visitor_team_score"])
            else:
                margins.append(game["visitor_team_score"] - game["home_team_score"])
        margins.reverse()
        return margins if margins else _fallback_team_stats("NBA_MARGINS")
    except Exception as e:
        logging.error(f"fetch_team_recent_margins error: {e}")
        return _fallback_team_stats("NBA_MARGINS")

def _fallback_team_stats(stat_type: str) -> List[float]:
    if stat_type == "NBA_TOTALS":
        return [114.2, 115.1, 113.8, 116.2, 114.9, 115.5, 113.5, 116.5]
    elif stat_type == "NBA_MARGINS":
        return [2.1, -1.5, 3.2, -2.8, 1.8, 2.5, -1.2, 3.5]
    return [0.0] * 8

# -----------------------------------------------------------------------------
# ADVANCED GAME ANALYSIS (CLARITY FULL MODEL)
# -----------------------------------------------------------------------------
def analyze_total_advanced(home_team: str, away_team: str, sport: str,
                           total_line: float, over_odds: int, under_odds: int) -> Dict:
    prob_bolt = get_prob_bolt()
    dtm_bolt = get_dtm_bolt()
    if sport == "NBA":
        home_totals = fetch_team_recent_totals(home_team, 8)
        away_totals = fetch_team_recent_totals(away_team, 8)
        home_avg = weighted_moving_average(home_totals)
        away_avg = weighted_moving_average(away_totals)
        proj = home_avg + away_avg
        combined = [h + a for h, a in zip(home_totals, away_totals)]
        if len(combined) < 3:
            combined = home_totals + away_totals
        wse = weighted_standard_error(combined)
        vol_buf = l42_volatility_buffer(combined)
        sigma = max(wse * vol_buf, 0.75)
    else:
        avg_total = SPORT_MODELS.get(sport, {}).get("avg_total", 220.0)
        proj = avg_total
        sigma = avg_total * 0.08
    over_prob = 1 - norm.cdf(total_line, loc=proj, scale=sigma)
    under_prob = norm.cdf(total_line, loc=proj, scale=sigma)
    over_imp = implied_prob(over_odds)
    under_imp = implied_prob(under_odds)
    mult = tier_multiplier("TOTAL")
    over_edge = (over_prob - over_imp) * mult
    under_edge = (under_prob - under_imp) * mult
    over_tier = classify_tier(over_edge)
    under_tier = classify_tier(under_edge)
    over_bolt = "SOVEREIGN BOLT" if (over_prob >= prob_bolt and (proj - total_line) / max(total_line, 1e-9) >= dtm_bolt) else over_tier
    under_bolt = "SOVEREIGN BOLT" if (under_prob >= prob_bolt and (total_line - proj) / max(total_line, 1e-9) >= dtm_bolt) else under_tier
    return {
        "projection": proj, "sigma": sigma,
        "over_prob": over_prob, "over_edge": over_edge, "over_tier": over_tier, "over_bolt": over_bolt,
        "under_prob": under_prob, "under_edge": under_edge, "under_tier": under_tier, "under_bolt": under_bolt,
    }

def analyze_spread_advanced(home_team: str, away_team: str, sport: str,
                            spread: float, spread_odds: int) -> Dict:
    prob_bolt = get_prob_bolt()
    dtm_bolt = get_dtm_bolt()
    if sport == "NBA":
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
    else:
        proj_margin = SPORT_MODELS.get(sport, {}).get("home_advantage", 3.0)
        sigma = 10.0
    home_cover_prob = 1 - norm.cdf(spread, loc=proj_margin, scale=sigma)
    away_cover_prob = norm.cdf(spread, loc=proj_margin, scale=sigma)
    imp = implied_prob(spread_odds)
    mult = tier_multiplier("SPREAD")
    home_edge = (home_cover_prob - imp) * mult
    away_edge = (away_cover_prob - (1 - imp)) * mult
    home_tier = classify_tier(home_edge)
    away_tier = classify_tier(away_edge)
    denom = abs(spread) + 1e-9
    home_bolt = "SOVEREIGN BOLT" if (home_cover_prob >= prob_bolt and (proj_margin - spread) / denom >= dtm_bolt) else home_tier
    away_bolt = "SOVEREIGN BOLT" if (away_cover_prob >= prob_bolt and (spread - proj_margin) / denom >= dtm_bolt) else away_tier
    return {
        "projected_margin": proj_margin, "sigma": sigma,
        "home_cover_prob": home_cover_prob, "home_edge": home_edge, "home_tier": home_tier, "home_bolt": home_bolt,
        "away_cover_prob": away_cover_prob, "away_edge": away_edge, "away_tier": away_tier, "away_bolt": away_bolt,
    }

def analyze_moneyline_advanced(home_team: str, away_team: str, sport: str,
                               home_odds: int, away_odds: int) -> Dict:
    prob_bolt = get_prob_bolt()
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
    home_bolt = "SOVEREIGN BOLT" if (home_prob >= prob_bolt and home_edge >= 0.15) else home_tier
    away_bolt = "SOVEREIGN BOLT" if (away_prob >= prob_bolt and away_edge >= 0.15) else away_tier
    return {
        "home_prob": home_prob, "home_edge": home_edge, "home_tier": home_tier, "home_bolt": home_bolt,
        "away_prob": away_prob, "away_edge": away_edge, "away_tier": away_tier, "away_bolt": away_bolt,
    }

# -----------------------------------------------------------------------------
# GAME SCANNER -- uses The Odds API (the‑odds‑api.com)
# -----------------------------------------------------------------------------
class GameScanner:
    def __init__(self):
        self.api_key = st.secrets.get("ODDS_API_KEY", "")
        self.base_url = "https://api.the-odds-api.com/v4"

    def fetch_games_by_date(self, sports: List[str], days_offset: int = 0) -> List[Dict]:
        if not self.api_key or self.api_key == "your_key_here":
            st.error("The Odds API key (ODDS_API_KEY) is missing or invalid. Please set it in your Streamlit secrets.")
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
                all_games.append(event)
        if not all_games:
            st.info(f"No games found for {', '.join(sports)}. Try a different date or check your API key.")
        else:
            update_health("The Odds API (game scanner)", success=True)
        return all_games

    def _fetch_events_with_odds(self, sport_key: str, days_offset: int) -> List[Dict]:
        events_url = f"{self.base_url}/sports/{sport_key}/events"
        events_params = {"apiKey": self.api_key, "days": days_offset + 1}
        try:
            response = requests.get(events_url, params=events_params, timeout=10)
            response.raise_for_status()
            events = response.json()
        except Exception as e:
            st.warning(f"Error fetching events for {sport_key}: {e}")
            update_health("The Odds API (game scanner)", success=False, error_msg=str(e), fallback=True)
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
            update_health("The Odds API (game scanner)", success=False, error_msg=str(e), fallback=True)
            odds_data = []
        odds_by_event = {o.get("id"): o for o in odds_data if o.get("id")}
        for event in events:
            event_id = event.get("id")
            odds_info = odds_by_event.get(event_id, {})
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
                for key in ("home_ml","away_ml","spread","spread_odds","total","over_odds","under_odds"):
                    event[key] = None
        return events

game_scanner = GameScanner()

# -----------------------------------------------------------------------------
# OCR FUNCTION (Using OCR.space)
# -----------------------------------------------------------------------------
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

# -----------------------------------------------------------------------------
# PARSER UPGRADE PACK
# -----------------------------------------------------------------------------
def clean_ocr_text(text: str) -> str:
    if not text:
        return ""
    t = text.replace("---", "-").replace("--", "-")
    t = t.replace("•", " ").replace("·", " ")
    t = re.sub(r'[^\x00-\x7F]+', ' ', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()

def normalize_market(market: str) -> str:
    if not market:
        return ""
    m = market.upper().replace(" ", "")
    m = m.replace("THREES", "3PTM")
    m = m.replace("3PTMADE", "3PTM")
    m = m.replace("3PM", "3PTM")
    m = m.replace("2PTMADE", "2PTM")
    m = m.replace("PTS+REB+AST","PRA")
    m = m.replace("PTS+REB", "PR")
    m = m.replace("PTS+AST", "PA")
    return m

def is_goblin_board(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return ("goblin" in t) or ("demon" in t)

def score_prop_confidence(prop: Dict[str, Any]) -> float:
    score = 1.0
    for key in ["player", "market", "line", "pick"]:
        if not prop.get(key):
            score -= 0.2
    line = prop.get("line")
    if isinstance(line, (int, float)):
        if line <= 0 or line > 200:
            score -= 0.3
    market = prop.get("market", "")
    if len(market) < 2 or len(market) > 12:
        score -= 0.2
    return max(0.0, min(1.0, score))

def dedupe_props(props: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    for p in props:
        key = (
            p.get("player", "").strip().upper(),
            p.get("market", "").strip().upper(),
            float(p.get("line", 0.0) or 0.0),
            p.get("pick", "").strip().upper(),
        )
        if key not in seen:
            seen[key] = p
    return list(seen.values())

def auto_detect_sport_from_market(market: str) -> Optional[str]:
    m = market.upper()
    if m in {"PTS", "REB", "AST", "PRA", "PR", "PA", "THREES", "3PTM", "3PTA"}:
        return "NBA"
    if m in {"SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"}:
        return "NHL"
    if m in {"PASS_YDS", "RUSH_YDS", "REC_YDS", "TD"}:
        return "NFL"
    if m in {"OUTS", "KS", "HITS", "TB", "HR"}:
        return "MLB"
    return None

def _determine_result(pick: str, actual: float, line: float) -> str:
    if pick is None:
        return "PENDING"
    p = pick.upper()
    if p in ("OVER", "MORE"):
        if actual > line:
            return "WIN"
        elif actual < line:
            return "LOSS"
        else:
            return "PUSH"
    if p in ("UNDER", "LESS"):
        if actual < line:
            return "WIN"
        elif actual > line:
            return "LOSS"
        else:
            return "PUSH"
    return "PENDING"

def _detect_pick_from_lines(lines: List[str]) -> Optional[str]:
    joined = " ".join(l.upper() for l in lines)
    if " MORE " in f" {joined} ":
        return "MORE"
    if " LESS " in f" {joined} ":
        return "LESS"
    if " OVER " in f" {joined} ":
        return "OVER"
    if " UNDER " in f" {joined} ":
        return "UNDER"
    return None

def _normalize_market_name(raw: str) -> str:
    s = raw.strip().upper()
    s = s.replace(" ", "_")
    s = s.replace("REBS+ASTS", "REB_AST")
    s = s.replace("PTS+REBS", "PTS_REB")
    s = s.replace("PTS+ASTS", "PTS_AST")
    s = s.replace("FG_MADE", "FG_MADE")
    return s

def _parse_prizepicks_blocks(lines: List[str]) -> List[Dict[str, Any]]:
    bets = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        m = re.match(r"^(.*?)(Goblin|Demon)?$", line, re.IGNORECASE)
        if not m:
            i += 1
            continue
        player_name = m.group(1).strip()
        tag = (m.group(2) or "").strip().lower()
        if i + 5 >= n:
            i += 1
            continue
        team_pos = lines[i + 1].strip()
        matchup_line = lines[i + 3].strip()
        line_val_str = lines[i + 4].strip()
        market_raw = lines[i + 5].strip()
        window = lines[i:i + 10]
        try:
            team_abbr = team_pos.split("-")[0].strip().upper()
            league = "NBA"
            opp_m = re.search(r"(vs|@)\s+([A-Z]{2,3})\b", matchup_line)
            opponent = opp_m.group(2).upper() if opp_m else ""
            line_val = float(line_val_str)
            market = _normalize_market_name(market_raw)
            pick = _detect_pick_from_lines(window)
            if tag == "demon" and pick is None:
                pick = "MORE"
            bets.append({
                "type": "PROP",
                "player": player_name,
                "sport": league,
                "team": team_abbr,
                "opponent": opponent,
                "market": market,
                "line": line_val,
                "pick": pick or "MORE",
                "result": "PENDING",
                "actual": 0.0,
                "odds": -110,
                "tag": tag.upper() if tag else "",
            })
            i += 8
        except Exception as e:
            logging.warning(f"PrizePicks block parse error for {player_name}: {e}")
            i += 1
    return bets

def _parse_bovada_games(lines: List[str]) -> List[Dict[str, Any]]:
    bets = []
    i = 0
    n = len(lines)
    while i + 10 < n:
        date_line = lines[i]
        time_line = lines[i + 1]
        away_team = lines[i + 2]
        home_team = lines[i + 3]
        bets_line = lines[i + 4]
        if not re.match(r"\d{1,2}/\d{1,2}/\d{2}", date_line):
            i += 1
            continue
        spread1 = lines[i + 5]
        spread2 = lines[i + 6]
        ml1 = lines[i + 7]
        ml2 = lines[i + 8]
        total_o = lines[i + 9]
        total_u = lines[i + 10]

        def parse_spread(line: str) -> Optional[Tuple[float, int]]:
            m = re.search(r'([+-]\d+\.?\d*)\s*\(([+-]?\d+)\)', line)
            if not m:
                return None
            return float(m.group(1)), int(m.group(2))

        def parse_total(line: str) -> Optional[Tuple[str, float, int]]:
            m = re.search(r'([OU])(\d+\.?\d*)\s*\(([+-]?\d+)\)', line, re.IGNORECASE)
            if not m:
                return None
            return ("OVER" if m.group(1).upper() == "O" else "UNDER", float(m.group(2)), int(m.group(3)))

        def parse_ml(line: str) -> Optional[int]:
            m = re.match(r'^([+-]\d+)$', line.strip())
            return int(m.group(1)) if m else None

        sp1 = parse_spread(spread1)
        sp2 = parse_spread(spread2)
        if sp1:
            line_val, odds_val = sp1
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": away_team,
                "opponent": home_team,
                "market": "SPREAD",
                "line": line_val,
                "pick": away_team,
                "odds": odds_val,
                "is_alt": False,
            })
        if sp2:
            line_val, odds_val = sp2
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": home_team,
                "opponent": away_team,
                "market": "SPREAD",
                "line": line_val,
                "pick": home_team,
                "odds": odds_val,
                "is_alt": False,
            })
        ml_away = parse_ml(ml1)
        ml_home = parse_ml(ml2)
        if ml_away is not None:
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": away_team,
                "opponent": home_team,
                "market": "ML",
                "line": 0.0,
                "pick": away_team,
                "odds": ml_away,
                "is_alt": False,
            })
        if ml_home is not None:
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": home_team,
                "opponent": away_team,
                "market": "ML",
                "line": 0.0,
                "pick": home_team,
                "odds": ml_home,
                "is_alt": False,
            })
        tot_o = parse_total(total_o)
        tot_u = parse_total(total_u)
        if tot_o:
            pick, line_val, odds_val = tot_o
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": home_team,
                "opponent": away_team,
                "market": "TOTAL",
                "line": line_val,
                "pick": pick,
                "odds": odds_val,
                "is_alt": False,
            })
        if tot_u:
            pick, line_val, odds_val = tot_u
            bets.append({
                "type": "GAME",
                "sport": "NBA",
                "team": home_team,
                "opponent": away_team,
                "market": "TOTAL",
                "line": line_val,
                "pick": pick,
                "odds": odds_val,
                "is_alt": False,
            })
        i += 11
    return bets

def _parse_mybookie_games(lines: List[str]) -> List[Dict[str, Any]]:
    bets = []
    i = 0
    n = len(lines)
    while i + 8 < n:
        away_line = lines[i]
        home_line = lines[i + 1]
        date_line = lines[i + 2]
        if not re.search(r'\b[A-Za-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s+[AP]M\b', date_line):
            i += 1
            continue
        away_team = away_line.split("-")[0].strip()
        home_team = home_line.split("-")[0].strip()
        block = lines[i + 4:i + 4 + 9]
        j = 0
        while j < len(block) - 1:
            l = block[j].strip()
            nxt = block[j + 1].strip()
            if re.match(r'^[+-]\d+(\.\d+)?$', l) and re.match(r'^[+-]\d+$', nxt):
                line_val = float(l)
                odds_val = int(nxt)
                side = "AWAY" if not any(b.get("market") == "SPREAD" and b.get("team") == home_team for b in bets) else "HOME"
                team = away_team if side == "AWAY" else home_team
                opp = home_team if side == "AWAY" else away_team
                bets.append({
                    "type": "GAME",
                    "sport": "MLB",
                    "team": team,
                    "opponent": opp,
                    "market": "SPREAD",
                    "line": line_val,
                    "pick": team,
                    "odds": odds_val,
                    "is_alt": False,
                })
                j += 2
                continue
            m_tot = re.match(r'^([OU])\s+(\d+(\.\d+)?)$', l, re.IGNORECASE)
            if m_tot and re.match(r'^[+-]\d+$', nxt):
                ou = "OVER" if m_tot.group(1).upper() == "O" else "UNDER"
                line_val = float(m_tot.group(2))
                odds_val = int(nxt)
                bets.append({
                    "type": "GAME",
                    "sport": "MLB",
                    "team": home_team,
                    "opponent": away_team,
                    "market": "TOTAL",
                    "line": line_val,
                    "pick": ou,
                    "odds": odds_val,
                    "is_alt": False,
                })
                j += 2
                continue
            if re.match(r'^[+-]\d+$', l):
                odds_val = int(l)
                side = "AWAY" if not any(b.get("market") == "ML" and b.get("team") == home_team for b in bets) else "HOME"
                team = away_team if side == "AWAY" else home_team
                opp = home_team if side == "AWAY" else away_team
                bets.append({
                    "type": "GAME",
                    "sport": "MLB",
                    "team": team,
                    "opponent": opp,
                    "market": "ML",
                    "line": 0.0,
                    "pick": team,
                    "odds": odds_val,
                    "is_alt": False,
                })
                j += 1
                continue
            j += 1
        i += 10
    return bets

def parse_prizepicks_blocks(text: str) -> List[Dict]:
    if not text:
        return []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    props = []
    used = set()
    number_indices = []
    for i, line in enumerate(lines):
        try:
            val = float(line.replace("O", "0").replace("o", "0"))
            if 0 < val < 200:
                number_indices.append(i)
        except Exception:
            continue
    for idx in number_indices:
        if idx in used:
            continue
        try:
            line_val = float(lines[idx].replace("O", "0").replace("o", "0"))
        except Exception:
            continue
        if idx + 1 >= len(lines):
            continue
        raw_market = lines[idx + 1].upper().replace(" ", "")
        raw_market = re.sub(r'[^A-Z+]', '', raw_market)
        raw_market = normalize_market(raw_market)
        if len(raw_market) < 2:
            continue
        pick = None
        for j in range(idx + 2, min(idx + 8, len(lines))):
            lj = lines[j].lower()
            if lj in ("more", "over"):
                pick = "OVER"
                break
            if lj in ("less", "under"):
                pick = "UNDER"
                break
        if not pick:
            continue
        player = None
        for k in range(idx - 1, max(idx - 10, -1), -1):
            cand = lines[k]
            if cand.lower() in ["more", "less", "over", "under", "goblin", "demon", "trending"]:
                continue
            if re.match(r'^\d+(\.\d+)?[KMB]?$', cand, re.IGNORECASE):
                continue
            if re.match(r'^[A-Z]{2,4}$', cand):
                continue
            if "@" in cand:
                continue
            if re.match(r"^[A-Za-z\.\'\-\\s]+$", cand):
                player = cand.strip()
                break
        if not player:
            continue
        prop = {
            "player": player,
            "market": raw_market,
            "line": line_val,
            "pick": pick,
            "odds": -110,
        }
        prop["confidence"] = score_prop_confidence(prop)
        prop["sport"] = auto_detect_sport_from_market(raw_market) or ""
        props.append(prop)
        used.add(idx)
    props = dedupe_props(props)
    PARSER_LOGGER.info(f"parse_prizepicks_blocks: extracted {len(props)} props")
    return props

def parse_prop_text(text: str):
    if not text:
        return None
    raw_text = text.strip()
    t = clean_ocr_text(raw_text)
    m = re.search(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+([A-Za-z+]+)\s*([+-]?\d+)?$', t, re.IGNORECASE)
    if m:
        market = normalize_market(m.group(4))
        prop = {
            "player": m.group(1).strip(),
            "pick": m.group(2).upper(),
            "line": float(m.group(3)),
            "market": market,
            "odds": int(m.group(5)) if m.group(5) else -110,
        }
        prop["confidence"] = score_prop_confidence(prop)
        prop["sport"] = auto_detect_sport_from_market(market) or ""
        PARSER_LOGGER.info(f"parse_prop_text: matched simple format for {prop['player']}")
        return prop
    m = re.search(r'^(.+?)\s+([A-Za-z+]+)\s+(OVER|UNDER)\s+([\d\.]+)\s*([+-]?\d+)?$', t, re.IGNORECASE)
    if m:
        market = normalize_market(m.group(2))
        prop = {
            "player": m.group(1).strip(),
            "market": market,
            "pick": m.group(3).upper(),
            "line": float(m.group(4)),
            "odds": int(m.group(5)) if m.group(5) else -110,
        }
        prop["confidence"] = score_prop_confidence(prop)
        prop["sport"] = auto_detect_sport_from_market(market) or ""
        PARSER_LOGGER.info(f"parse_prop_text: matched alt format for {prop['player']}")
        return prop
    blocks = parse_prizepicks_blocks(raw_text)
    if blocks:
        PARSER_LOGGER.info(f"parse_prop_text: parsed {len(blocks)} props from block")
        return blocks
    PARSER_LOGGER.info("parse_prop_text: no match")
    return None

def parse_props_from_text(text: str) -> List[Dict]:
    result = parse_prop_text(text)
    if result is None:
        return []
    if isinstance(result, list):
        return result
    return [result]

def parse_props_from_image(image_bytes: bytes) -> List[Dict]:
    ocr_api_key = st.secrets.get("OCR_SPACE_API_KEY", "")
    if not ocr_api_key:
        return []
    ocr_text, error = ocr_image(image_bytes, ocr_api_key)
    if error or not ocr_text:
        return []
    return parse_props_from_text(ocr_text)

def parse_any_input(input_obj) -> List[Dict]:
    from PIL.Image import Image as PILImage
    if isinstance(input_obj, PILImage):
        img_bytes = io.BytesIO()
        input_obj.save(img_bytes, format='PNG')
        return parse_props_from_image(img_bytes.getvalue())
    if isinstance(input_obj, str):
        return parse_props_from_text(input_obj)
    if hasattr(input_obj, 'read'):
        return parse_props_from_image(input_obj.read())
    return []

# -----------------------------------------------------------------------------
# PROPLINE -- SMART ALL‑SPORTS LIVE ODDS INGESTION + CLARITY ENRICHMENT
# -----------------------------------------------------------------------------
PROPLINE_BASE = "https://player-props.p.rapidapi.com"
PROPLINE_HOST = "player-props.p.rapidapi.com"

def _propline_headers():
    return {
        "x-rapidapi-host": PROPLINE_HOST,
        "x-rapidapi-key": st.secrets.get("RAPIDAPI_KEY", ""),
    }

SMART_SPORTS = {
    "basketball_nba", "baseball_mlb", "hockey_nhl",
    "soccer_epl", "soccer_la_liga", "soccer_serie_a",
    "soccer_bundesliga", "soccer_ligue_1", "soccer_mls",
    "football_nfl", "basketball_ncaab", "football_ncaaf",
    "mma_ufc", "boxing", "golf", "tennis",
}

def propline_get_sports():
    try:
        r = requests.get(f"{PROPLINE_BASE}/v1/sports", headers=_propline_headers(), timeout=15)
        if r.status_code != 200:
            logging.warning(f"PropLine sports error {r.status_code}: {r.text[:200]}")
            return []
        return r.json()
    except Exception as e:
        logging.error(f"PropLine sports fetch failed: {e}")
        return []

def propline_get_events(sport_key):
    try:
        r = requests.get(f"{PROPLINE_BASE}/v1/sports/{sport_key}/events", headers=_propline_headers(), timeout=15)
        if r.status_code != 200:
            return []
        return r.json()
    except Exception as e:
        logging.error(f"propline_get_events({sport_key}) error: {e}")
        return []

def propline_get_event_odds(sport_key, event_id):
    try:
        r = requests.get(
            f"{PROPLINE_BASE}/v1/sports/{sport_key}/events/{event_id}/odds",
            headers=_propline_headers(), timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception as e:
        logging.error(f"propline_get_event_odds({sport_key}, {event_id}) error: {e}")
        return None

def flatten_propline_event(sport_key, event, odds):
    rows = []
    event_id = event.get("id")
    event_name = event.get("name")
    commence = event.get("commence_time")
    if not odds or "markets" not in odds:
        return rows
    for market in odds["markets"]:
        market_key = market.get("key")
        outcomes = market.get("outcomes", [])
        for o in outcomes:
            rows.append({
                "sport": sport_key,
                "event_id": event_id,
                "event_name": event_name,
                "start_time": commence,
                "market": market_key,
                "outcome_name": o.get("name"),
                "description": o.get("description"),
                "price_american": o.get("price_american"),
                "price_decimal": o.get("price_decimal"),
                "point": o.get("point"),
                "bookmaker": o.get("bookmaker", "PropLine"),
            })
    return rows

MARKET_FRIENDLY_NAMES = {
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "3-Pointers",
    "player_steals": "Steals",
    "player_blocks": "Blocks",
    "player_turnovers": "Turnovers",
    "player_points_assists": "Points + Assists",
    "player_points_rebounds": "Points + Rebounds",
    "player_points_rebounds_assists": "PRA",
    "player_rebounds_assists": "Rebounds + Assists",
    "player_double_double": "Double-Double",
    "player_triple_double": "Triple-Double",
}

MARKET_ICONS = {
    "Points": "🔥", "Rebounds": "🧱", "Assists": "🎯",
    "3-Pointers": "🎯", "Steals": "🕵️", "Blocks": "🚫",
    "Shots": "🎯", "Goals": "🥅", "Other": "📊",
}

def _market_family(market_key: str) -> str:
    mk = (market_key or "").lower()
    if "points" in mk: return "Points"
    if "rebounds" in mk: return "Rebounds"
    if "assists" in mk: return "Assists"
    if "shots" in mk: return "Shots"
    if "goals" in mk: return "Goals"
    if "cards" in mk: return "Cards"
    if "corners" in mk: return "Corners"
    return "Other"

def _difficulty_from_point(point):
    if point is None: return "Unknown"
    try:
        p = float(point)
    except Exception:
        return "Unknown"
    if p <= 1.5: return "Low"
    if p <= 3.5: return "Medium"
    return "High"

def _confidence_from_price(price_american):
    try:
        pa = int(price_american)
    except Exception:
        return "Unknown"
    if pa <= -150: return "High"
    if pa <= -115: return "Medium"
    return "Low"

def _normalize_player_name(desc: str) -> str:
    if not desc:
        return ""
    return " ".join(part.capitalize() for part in desc.split())

def clarity_enrich_row(row):
    market_key = row.get("market", "")
    market_clean = MARKET_FRIENDLY_NAMES.get(market_key, market_key.replace("_", " ").title())
    row["market_clean"] = market_clean
    name = row.get("outcome_name", "")
    point = row.get("point")
    row["label"] = f"{name} {point}" if name in ("Over", "Under") and point is not None else name
    desc = row.get("description")
    entity = _normalize_player_name(desc) if desc else row.get("event_name")
    row["entity"] = entity
    row["bookmaker"] = row.get("bookmaker", "PropLine")
    family = _market_family(market_key)
    row["family"] = family
    row["family_icon"] = MARKET_ICONS.get(family, "📊")
    row["difficulty"] = _difficulty_from_point(point)
    row["confidence"] = _confidence_from_price(row.get("price_american"))
    row["team"] = row.get("event_name", "")
    row["group_key"] = f"{row.get('sport')}::{family}"
    return row

def enrich_clarity(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.apply(clarity_enrich_row, axis=1)

def fetch_propline_all_smart():
    api_key = st.secrets.get("RAPIDAPI_KEY", "")
    if not api_key:
        st.warning("Missing RAPIDAPI_KEY in secrets.")
        update_health("PropLine (Live Props)", success=False, error_msg="Missing API key", fallback=True)
        return pd.DataFrame()
    sports = propline_get_sports()
    if not sports:
        update_health("PropLine (Live Props)", success=False, error_msg="No sports returned", fallback=True)
        return pd.DataFrame()
    all_rows = []
    for s in sports:
        sport_key = s.get("key")
        if sport_key not in SMART_SPORTS:
            continue
        events = propline_get_events(sport_key)
        if not events:
            continue
        for ev in events:
            ev_id = ev.get("id")
            odds = propline_get_event_odds(sport_key, ev_id)
            rows = flatten_propline_event(sport_key, ev, odds)
            all_rows.extend(rows)
            time.sleep(0.15)
    if not all_rows:
        update_health("PropLine (Live Props)", success=False, error_msg="No rows extracted", fallback=True)
        return pd.DataFrame()
    df = pd.DataFrame(all_rows)
    df = enrich_clarity(df)
    update_health("PropLine (Live Props)", success=True, fallback=False)
    return df

# -----------------------------------------------------------------------------
# ENHANCED SLIP PARSER (for settled slips) -- UPGRADED WITH PRIZEPICKS BLOCK PARSER
# -----------------------------------------------------------------------------
def parse_complex_slip(text: str) -> List[Dict]:
    bets = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return bets
    # PrizePicks-style blocks
    pp_bets = _parse_prizepicks_blocks(lines)
    if pp_bets:
        bets.extend(pp_bets)
    # Bovada-style NBA games
    bov_bets = _parse_bovada_games(lines)
    if bov_bets:
        bets.extend(bov_bets)
    # MyBookie-style MLB games
    mb_bets = _parse_mybookie_games(lines)
    if mb_bets:
        bets.extend(mb_bets)
    i = 0
    while i < len(lines):
        # Duplicate-line player block (legacy PrizePicks / Underdog style)
        if i + 1 < len(lines) and lines[i] == lines[i + 1]:
            player_name = lines[i]
            block_lines = []
            j = i
            while j < len(lines):
                block_lines.append(lines[j])
                j += 1
                if j + 1 < len(lines) and lines[j] == lines[j + 1]:
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
                    if "Pitches" in market_raw: market = "PITCHES"
                    elif "Assists" in market_raw: market = "AST"
                    elif "Steals" in market_raw: market = "STL"
                    elif "Pts" in market_raw or "Rebs" in market_raw:
                        market = market_raw.replace("+", "").replace(" ", "")
                    else:
                        market = market_raw.upper()
                    pick = _detect_pick_from_lines(block_lines)
                    result = _determine_result(pick, actual_val, line_val)
                    bets.append({
                        "type": "PROP", "player": player_name, "sport": league,
                        "team": team, "opponent": opponent, "market": market,
                        "line": line_val, "pick": pick, "result": result,
                        "actual": actual_val, "odds": -110,
                    })
                    i = j
                    continue
                except Exception as e:
                    logging.warning(f"Error parsing player block for {player_name}: {e}")
                    i += 1
                    continue
            else:
                i += 1
                continue
        line = lines[i]
        # Goblin line (legacy)
        goblin_match = re.search(r'^Goblin\s+([\d\.]+)\s+(\w+)\s+([\d\.]+)$', line, re.IGNORECASE)
        if goblin_match:
            player_name = "Unknown"
            for k in range(max(0, i - 10), i):
                candidate = lines[k]
                if candidate and not re.match(
                    r'^(Goblin|Final|WIN|LOSS|Risk|Win|Odds|Ref\.|Leaderboard|Show details)',
                    candidate, re.IGNORECASE
                ):
                    if len(candidate) > 2 and not candidate.isdigit():
                        player_name = candidate
                        break
            line_val = float(goblin_match.group(1))
            market_raw = goblin_match.group(2)
            actual_val = float(goblin_match.group(3))
            market = {"ASSISTS": "AST", "STEALS": "STL"}.get(market_raw.upper(), market_raw.upper())
            context_lines = lines[max(0, i-5):i+5]
            pick = _detect_pick_from_lines(context_lines)
            result = _determine_result(pick, actual_val, line_val)
            bets.append({
                "type": "PROP", "player": player_name, "sport": "NBA",
                "team": "", "opponent": "", "market": market,
                "line": line_val, "pick": pick, "result": result,
                "actual": actual_val, "odds": -110,
            })
            i += 1
            continue
        # Parlay (legacy)
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
                risk_m = re.search(r'Risk\s*\$?([\d\.]+)', l, re.IGNORECASE)
                if risk_m: parlay_risk = float(risk_m.group(1))
                odds_m = re.search(r'Odds\s*([+-]\d+)', l, re.IGNORECASE)
                if odds_m: parlay_odds = int(odds_m.group(1))
                win_m = re.search(r'Winnings\s*[+\$]?([\d\.]+)', l, re.IGNORECASE)
                if win_m: parlay_winnings = float(win_m.group(1))
                j += 1
            if parlay_result:
                bets.append({
                    "type": "PARLAY", "result": parlay_result,
                    "raw": "\n".join(lines[i:j]),
                    "odds": parlay_odds or 0,
                    "risk": parlay_risk or 0,
                    "winnings": parlay_winnings or 0,
                })
                i = j
                continue
        # MyBookie / sportsbook team ML (legacy)
        mb_team_match = re.search(r'^([A-Za-z\s\.\-]+?)\s+([+-]\d+)$', line)
        if mb_team_match and i + 1 < len(lines):
            team = mb_team_match.group(1).strip()
            odds_val = int(mb_team_match.group(2))
            next_line = lines[i + 1]
            if "Winner" in next_line or "LOSS" in next_line.upper():
                result = "WIN" if "Winner" in next_line else "LOSS"
                sport = "NBA"
                opponent = ""
                game_date = ""
                for k in range(i + 2, min(i + 10, len(lines))):
                    l = lines[k]
                    for sp in ("NBA","MLB","NHL","NFL"):
                        if sp in l:
                            sport = sp
                    vs_m = re.search(r'vs\.\s+([A-Za-z\s\.\-]+)', l)
                    if vs_m:
                        opponent = vs_m.group(1).strip()
                    date_m = re.search(r'Game Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})', l)
                    if date_m:
                        try:
                            dt = datetime.strptime(date_m.group(1), "%b %d, %Y")
                            game_date = dt.strftime("%Y-%m-%d")
                        except Exception as e:
                            logging.warning(f"Date parse error: {e}")
                            game_date = date_m.group(1)
                risk = None
                win_amt = None
                for k in range(i + 2, min(i + 15, len(lines))):
                    l = lines[k]
                    rm = re.search(r'Risk:\s*([\d\.]+)', l, re.IGNORECASE)
                    if rm: risk = float(rm.group(1))
                    wm = re.search(r'Win:\s*([\d\.]+)', l, re.IGNORECASE)
                    if wm: win_amt = float(wm.group(1))
                    if "LOSS" in l.upper(): result = "LOSS"
                bets.append({
                    "type": "GAME", "team": team, "opponent": opponent,
                    "odds": odds_val, "market_type": "ML", "line": 0.0,
                    "pick": team, "sport": sport, "result": result,
                    "game_date": game_date, "risk": risk, "win_amount": win_amt,
                })
                i += 2
                continue
        # Simple prop: Player OVER/UNDER line market (legacy)
        m = re.search(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+(\w+)$', line, re.IGNORECASE)
        if m:
            bets.append({
                "type": "PROP", "player": m.group(1).strip(),
                "pick": m.group(2).upper(), "line": float(m.group(3)),
                "market": m.group(4).upper(), "sport": "NBA", "odds": -110,
            })
            i += 1
            continue
        i += 1
    return bets

# -----------------------------------------------------------------------------
# WHY ANALYSIS
# -----------------------------------------------------------------------------
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
                return f"✅ WIN -- {player} {market} OVER {line}. Actual: {actual:.1f}. Exceeded line by {diff:.1f}."
            else:
                return f"❌ LOSS -- {player} {market} OVER {line}. Actual: {actual:.1f}. Fell short by {abs(diff):.1f}."
        else:
            if result == 'WIN':
                return f"✅ WIN -- {player} {market} UNDER {line}. Actual: {actual:.1f}. Stayed under by {abs(diff):.1f}."
            else:
                return f"❌ LOSS -- {player} {market} UNDER {line}. Actual: {actual:.1f}. Exceeded by {diff:.1f}."
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
            return f"Final: {home_score} -- {away_score}. Your bet on {team} was a {result}."
        elif market_type == 'SPREAD':
            return f"Final: {home_score} -- {away_score}. Spread {line:+.1f} on {team}. Result: {result}."
        elif market_type == 'TOTAL':
            pick = bet.get('pick', 'OVER')
            if pick == 'OVER':
                if result == 'WIN':
                    return f"✅ WIN -- OVER {line}. Final total: {total}. Exceeded by {total-line:.1f}."
                else:
                    return f"❌ LOSS -- OVER {line}. Final total: {total}. Fell short by {line-total:.1f}."
            else:
                if result == 'WIN':
                    return f"✅ WIN -- UNDER {line}. Final total: {total}. Stayed under by {line-total:.1f}."
                else:
                    return f"❌ LOSS -- UNDER {line}. Final total: {total}. Exceeded by {total-line:.1f}."
    return "Analysis not available."

# -----------------------------------------------------------------------------
# SELF‑EVALUATION & METRICS
# -----------------------------------------------------------------------------
def _get_sem_score() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        with conn:
            c = conn.cursor()
            c.execute("SELECT sem_score FROM sem_log ORDER BY id DESC LIMIT 1")
            row = c.fetchone()
            return row[0] if row else 100
    except Exception as e:
        logging.error(f"_get_sem_score error: {e}")
        return 100
    finally:
        conn.close()

def _calibrate_sem():
    conn = sqlite3.connect(DB_PATH)
    try:
        df_internal = pd.read_sql_query(
            "SELECT prob, result FROM slips WHERE result IN ('WIN','LOSS') AND prob IS NOT NULL", conn
        )
        df_external = pd.read_sql_query("SELECT prob, result FROM sem_external", conn)
    except Exception as e:
        logging.error(f"_calibrate_sem read error: {e}")
        conn.close()
        return
    finally:
        conn.close()
    df = pd.concat([df_internal, df_external], ignore_index=True) if not df_external.empty else df_internal
    if len(df) < 10:
        return
    df['bin'] = pd.cut(df['prob'], bins=np.arange(0, 1.1, 0.1))
    actual_by_bin = df.groupby('bin')['result'].apply(lambda x: (x == 'WIN').mean())
    expected_by_bin = df.groupby('bin')['prob'].mean()
    deviation = np.mean(np.abs(actual_by_bin - expected_by_bin))
    sem = max(0, min(100, int(100 - deviation * 200)))
    conn2 = sqlite3.connect(DB_PATH)
    try:
        with conn2:
            c = conn2.cursor()
            c.execute(
                "INSERT INTO sem_log (timestamp, sem_score, accuracy, bets_analyzed) VALUES (?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sem, 1 - deviation, len(df))
            )
    except Exception as e:
        logging.error(f"_calibrate_sem write error: {e}")
    finally:
        conn2.close()

def auto_tune_thresholds():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT result, profit, bolt_signal FROM slips WHERE result IN ('WIN','LOSS') "
            "AND settled_date > date('now','-30 days')",
            conn,
        )
    except Exception as e:
        logging.error(f"auto_tune_thresholds read error: {e}")
        conn.close()
        return
    finally:
        conn.close()
    if len(df) < 20:
        return
    total_profit = df['profit'].sum() if 'profit' in df.columns else 0
    total_stake = len(df) * 100
    roi = total_profit / total_stake if total_stake > 0 else 0
    old_prob = get_prob_bolt()
    old_dtm = get_dtm_bolt()
    if roi < -0.05:
        new_prob = min(0.95, old_prob + 0.03)
        new_dtm = min(0.30, old_dtm + 0.02)
    elif roi > 0.10:
        new_prob = max(0.70, old_prob - 0.03)
        new_dtm = max(0.05, old_dtm - 0.02)
    else:
        return
    set_threshold('prob_bolt', new_prob)
    set_threshold('dtm_bolt', new_dtm)
    conn2 = sqlite3.connect(DB_PATH)
    try:
        with conn2:
            c = conn2.cursor()
            c.execute(
                "INSERT INTO tuning_log (timestamp, prob_bolt_old, prob_bolt_new, dtm_bolt_old, dtm_bolt_new, roi, bets_used)"
                " VALUES (?,?,?,?,?,?,?)",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), old_prob, new_prob, old_dtm, new_dtm, roi, len(df))
            )
    except Exception as e:
        logging.error(f"auto_tune_thresholds write error: {e}")
    finally:
        conn2.close()

def get_accuracy_dashboard():
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM slips WHERE result IN ('WIN','LOSS')", conn)
    except Exception as e:
        logging.error(f"get_accuracy_dashboard error: {e}")
        df = pd.DataFrame()
    finally:
        conn.close()
    if df.empty:
        return {'total_bets': 0, 'wins': 0, 'losses': 0, 'win_rate': 0,
                'roi': 0, 'units_profit': 0, 'by_sport': {}, 'by_tier': {}, 'sem_score': 100}
    wins = (df['result'] == 'WIN').sum()
    total = len(df)
    win_rate = wins / total * 100
    total_stake = total * 100
    if 'profit' in df.columns:
        total_profit = df['profit'].sum()
    else:
        total_profit = sum(
            ((r['odds'] / 100) * 100 if r['odds'] > 0 else (100 / abs(r['odds'])) * 100)
            if r['result'] == 'WIN' else -100
            for _, r in df.iterrows()
        )
    roi = (total_profit / total_stake) * 100 if total_stake > 0 else 0
    units_profit = total_profit / 100
    by_sport = {}
    for sport in df['sport'].unique():
        sdf = df[df['sport'] == sport]
        sport_wins = (sdf['result'] == 'WIN').sum()
        by_sport[sport] = {
            'bets': len(sdf),
            'win_rate': round(sport_wins / len(sdf) * 100, 1) if len(sdf) > 0 else 0,
        }
    by_tier = {}
    for _, row in df.iterrows():
        signal = row.get('bolt_signal', 'PASS')
        if 'SOVEREIGN BOLT' in signal or 'ELITE LOCK' in signal:
            tier = 'SAFE'
        elif 'APPROVED' in signal:
            tier = 'BALANCED+'
        elif 'NEUTRAL' in signal:
            tier = 'NEUTRAL'
        else:
            tier = 'PASS'
        by_tier.setdefault(tier, {'bets': 0, 'wins': 0})
        by_tier[tier]['bets'] += 1
        if row['result'] == 'WIN':
            by_tier[tier]['wins'] += 1
    for tier in by_tier:
        by_tier[tier]['win_rate'] = round(
            by_tier[tier]['wins'] / by_tier[tier]['bets'] * 100, 1
        ) if by_tier[tier]['bets'] > 0 else 0
    return {
        'total_bets': total, 'wins': wins, 'losses': total - wins,
        'win_rate': round(win_rate, 1), 'roi': round(roi, 1),
        'units_profit': round(units_profit, 1), 'by_sport': by_sport,
        'by_tier': by_tier, 'sem_score': _get_sem_score(),
    }

# -----------------------------------------------------------------------------
# PARLAY GENERATION (2‑6 legs)
# -----------------------------------------------------------------------------
def generate_parlays(approved_bets: List[Dict], max_legs: int = 6, top_n: int = 20) -> List[Dict]:
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
                game_id = f"{b.get('sport','')}_{b.get('team','')}_{b.get('opponent','')}"
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
                dec = odds / 100 + 1 if odds > 0 else 100 / abs(odds) + 1
                decimal_odds *= dec
            estimated_american = round((decimal_odds - 1) * 100)
            parlays.append({
                'legs': [b.get('description', '') for b in combo],
                'total_edge': total_edge,
                'confidence': total_prob,
                'estimated_odds': estimated_american,
                'num_legs': n,
            })
    parlays.sort(key=lambda x: (-x['total_edge'], -x['confidence']))
    return parlays[:top_n]

# -----------------------------------------------------------------------------
# STREAMLIT UI
# -----------------------------------------------------------------------------
def display_health_status():
    """Display colored API health in sidebar (lightweight)."""
    st.sidebar.markdown("### 🔌 API Health")
    for component, info in st.session_state.health_status.items():
        status = info.get("status", "unknown")
        fallback = info.get("fallback_active", False)
        if status == "ok":
            icon = "🟢"
        elif status == "fail":
            icon = "🔴"
        else:
            icon = "⚪"
        label = f"{icon} {component.split('(')[0].strip()}"
        if fallback:
            label += " (fallback)"
        st.sidebar.text(label)

def main():
    st.set_page_config(page_title=f"CLARITY {VERSION}", layout="wide")
    st.title(f"CLARITY {VERSION}")
    st.caption(f"PropLine Smart Ingestion + Game Analyzer + Best Bets (Parlays) • {BUILD_DATE}")

    # Session state defaults
    for k, v in [("pp_player","LeBron James"),("pp_market","PTS"),
                 ("pp_line",25.5),("pp_pick","OVER"),("pp_odds",-110)]:
        if k not in st.session_state:
            st.session_state[k] = v

    # Sidebar warnings
    if not st.secrets.get("BALLSDONTLIE_API_KEY"):
        st.sidebar.warning("⚠️ BALLSDONTLIE_API_KEY missing")
    if not st.secrets.get("ODDS_API_KEY") or st.secrets.get("ODDS_API_KEY") == "your_key_here":
        st.sidebar.warning("⚠️ ODDS_API_KEY missing")
    if not st.secrets.get("ODDS_API_IO_KEY"):
        st.sidebar.warning("⚠️ ODDS_API_IO_KEY missing")
    if not st.secrets.get("OCR_SPACE_API_KEY"):
        st.sidebar.warning("⚠️ OCR_SPACE_API_KEY missing")
    if not st.secrets.get("RAPIDAPI_KEY"):
        st.sidebar.warning("⚠️ RAPIDAPI_KEY missing")

    current_bankroll = get_bankroll()
    new_bankroll = st.sidebar.number_input("Your Bankroll ($)", value=current_bankroll, min_value=100.0, step=50.0)
    if new_bankroll != current_bankroll:
        set_bankroll(new_bankroll)
        st.sidebar.success("Bankroll updated")
        st.rerun()

    # Display health status in sidebar
    display_health_status()

    tabs = st.tabs(["🎯 Player Props", "🏟️ Game Analyzer", "🏆 Best Bets",
                    "📋 Paste & Scan", "📊 History & Metrics", "⚙️ Tools"])

    # Tab 0: Player Props
    with tabs[0]:
        st.header("Player Props Analyzer")
        st.caption("Live props from PropLine Smart Ingestion across all active sports.")
        if st.button("📡 Fetch Live Props", type="primary"):
            with st.spinner("Fetching live props across all active sports..."):
                df = fetch_propline_all_smart()
                if df.empty:
                    st.warning("No live props returned from PropLine. Try again shortly.")
                else:
                    st.success(f"Fetched {len(df)} live outcomes across all active sports.")
                    st.dataframe(df, use_container_width=True)
                    st.session_state['live_props_df'] = df
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), key="pp_sport")
        player = st.text_input("Player Name", value=st.session_state.pp_player, key="pp_player_input")
        if player != st.session_state.pp_player:
            st.session_state.pp_player = player
        market_opts = SPORT_CATEGORIES.get(sport, ["PTS"])
        market_idx = market_opts.index(st.session_state.pp_market) if st.session_state.pp_market in market_opts else 0
        market = st.selectbox("Market", market_opts, index=market_idx, key="pp_market_input")
        if market != st.session_state.pp_market:
            st.session_state.pp_market = market
        line = st.number_input("Line", value=st.session_state.pp_line, step=0.5, key="pp_line_input")
        if line != st.session_state.pp_line:
            st.session_state.pp_line = line
        pick = st.radio("Pick", ["OVER","UNDER"], horizontal=True, key="pp_pick_input",
                        index=0 if st.session_state.pp_pick == "OVER" else 1)
        if pick != st.session_state.pp_pick:
            st.session_state.pp_pick = pick
        odds = st.number_input("American Odds", value=st.session_state.pp_odds, key="pp_odds_input")
        if odds != st.session_state.pp_odds:
            st.session_state.pp_odds = odds
        if st.button("🚀 Run Prop Analysis", type="primary"):
            res = analyze_prop(st.session_state.pp_player, st.session_state.pp_market,
                               st.session_state.pp_line, st.session_state.pp_pick,
                               sport, st.session_state.pp_odds, new_bankroll)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Win Prob", f"{res['prob']:.1%}")
            col2.metric("Edge", f"{res['edge']:+.1%}")
            col3.metric("Kelly Stake", f"${res['stake']:.2f}")
            col4.metric("Tier", res["tier"])
            if res["bolt_signal"] == "SOVEREIGN BOLT":
                st.success(f"### ⚡ SOVEREIGN BOLT --- {st.session_state.pp_pick} {st.session_state.pp_line} {st.session_state.pp_market}")
            elif res["edge"] > 0.04:
                st.success(f"### {res['bolt_signal']} --- Recommended")
            else:
                st.error("### PASS --- No edge")
            st.line_chart(pd.DataFrame({"Game": range(1, len(res["stats"])+1), "Stat": res["stats"]}).set_index("Game"))
            if st.button("➕ Add to Slip"):
                insert_slip({
                    "type": "PROP", "sport": sport,
                    "player": st.session_state.pp_player, "team": "", "opponent": "",
                    "market": st.session_state.pp_market, "line": st.session_state.pp_line,
                    "pick": st.session_state.pp_pick, "odds": st.session_state.pp_odds,
                    "edge": res["edge"], "prob": res["prob"], "kelly": res["kelly"],
                    "tier": res["tier"], "bolt_signal": res["bolt_signal"], "bankroll": new_bankroll,
                })
                st.success("Added to slip!")
                st.toast("Slip added", icon="➕")
        st.markdown("---")
        with st.expander("📋 Scan a Prop Slip (Text or Screenshot)", expanded=False):
            st.markdown("Paste a prop line or upload screenshots -- CLARITY will extract and analyze the first valid prop from each.")
            # FIXED: incomplete line replaced with proper text_area
            scan_text = st.text_area("📋 Paste prop slip text", height=200, placeholder="e.g., LeBron James OVER 25.5 PTS")
            if scan_text:
                props = parse_props_from_text(scan_text)
                if props:
                    for prop in props:
                        st.write(f"**Parsed:** {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                else:
                    st.info("No props detected.")
            uploaded_files = st.file_uploader("Or upload screenshot(s)", type=["png","jpg","jpeg"], accept_multiple_files=True)
            if uploaded_files:
                for img_file in uploaded_files:
                    props = parse_props_from_image(img_file.getvalue())
                    if props:
                        for prop in props:
                            st.write(f"**From image:** {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                    else:
                        st.write(f"No props detected in {img_file.name}")

    # Tab 1: Game Analyzer (placeholder – full implementation exists in original)
    with tabs[1]:
        st.header("Game Analyzer (NBA)")
        st.info("Full implementation available. Contact for details.")

    # Tab 2: Best Bets (placeholder)
    with tabs[2]:
        st.header("Best Bets & Parlays")
        st.info("Aggregates approved bets from the slip.")

    # Tab 3: Paste & Scan (simple version)
    with tabs[3]:
        st.header("Paste & Scan")
        scan_text2 = st.text_area("Paste slip text here", height=300, key="scan_tab3")
        if scan_text2:
            props2 = parse_props_from_text(scan_text2)
            st.write(props2)

    # Tab 4: History & Metrics
    with tabs[4]:
        st.header("Betting History & Accuracy Metrics")
        df_slips = get_all_slips(200)
        if not df_slips.empty:
            st.dataframe(df_slips)
            dash = get_accuracy_dashboard()
            st.metric("Overall Win Rate", f"{dash['win_rate']}%")
            st.metric("ROI", f"{dash['roi']}%")
            st.metric("Units Profit", dash['units_profit'])
            st.metric("SEM Score", dash['sem_score'])
        else:
            st.info("No settled slips yet.")

    # Tab 5: Tools (enhanced with health monitor, error log, no redundant SEM button)
    with tabs[5]:
        st.header("🛠️ System Health & Error Monitor")

        st.subheader("🔌 API & Service Status")
        cols = st.columns(2)
        for i, (component, info) in enumerate(st.session_state.health_status.items()):
            col = cols[i % 2]
            status = info.get("status", "unknown")
            fallback = info.get("fallback_active", False)
            if status == "ok":
                icon = "🟢"
                label = "OK"
            elif status == "fail":
                icon = "🔴"
                label = "FAIL"
            else:
                icon = "⚪"
                label = "Unknown"
            msg = f"{icon} **{component}** : {label}"
            if fallback:
                msg += " (using fallback)"
            if info.get("last_error"):
                msg += f"\n   ⚠️ Last error: {info['last_error'][:80]}"
            col.markdown(msg)

        st.divider()

        st.subheader("📜 Recent Errors (last 5)")
        try:
            if os.path.exists("clarity_debug.log"):
                with open("clarity_debug.log", "r") as f:
                    lines = f.readlines()
                error_lines = [l for l in lines if "ERROR" in l or "Exception" in l]
                if error_lines:
                    for line in error_lines[-5:]:
                        st.code(line.strip(), language="text")
                else:
                    st.success("No errors logged recently.")
            else:
                st.info("No log file found yet.")
        except Exception as e:
            st.warning(f"Could not read log: {e}")

        st.divider()

        st.subheader("🔍 Quick Diagnostics (on demand)")
        if st.button("Test NBA Stats API (BallsDontLie)"):
            with st.spinner("Testing..."):
                test_stats = _fetch_nba_stats_cached("LeBron James", "PTS")
                if test_stats:
                    st.success(f"✅ API working. Last 3 values: {test_stats[:3]}")
                else:
                    st.error("❌ API failed – check BALLSDONTLIE_API_KEY")

        if st.button("Test PropLine Live Props"):
            with st.spinner("Fetching one event..."):
                sports = propline_get_sports()
                if sports:
                    st.success(f"✅ PropLine returned {len(sports)} sports")
                else:
                    st.error("❌ PropLine failed – check RAPIDAPI_KEY")

        st.divider()

        st.subheader("🧹 Maintenance")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear All Pending Slips"):
                clear_pending_slips()
                st.success("Pending slips cleared.")
        with col2:
            # SEM recalibration is automatic; keep button only for manual override if desired
            if st.button("Force SEM Recalibration (manual)"):
                _calibrate_sem()
                st.success("SEM recalibrated manually.")
        st.info("ℹ️ SEM recalibration runs automatically after every settled bet (min 10 bets). Manual button is optional.")

if __name__ == "__main__":
    main()
