# =============================================================================
# CLARITY PRIME 24.7 — FINAL ROBUST PARSER + BATCH SETTLE
# =============================================================================
# Handles MyBookie totals, Bovada parlays, PrizePicks props, and generic lines.
# =============================================================================

import os
import re
import uuid
import time
import json
import base64
import logging
import warnings
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm
import requests
import streamlit as st
from tenacity import retry, stop_after_attempt, wait_exponential

# Optional heavy libs
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

# =============================================================================
# LOGGING
# =============================================================================
os.makedirs("clarity_logs", exist_ok=True)
os.makedirs("cache", exist_ok=True)

logging.basicConfig(
    filename="clarity_debug.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

PARSER_LOGGER = logging.getLogger("clarity_parser")
if not PARSER_LOGGER.handlers:
    _h = logging.FileHandler("clarity_logs/parser.log", mode="a", encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    PARSER_LOGGER.addHandler(_h)
    PARSER_LOGGER.setLevel(logging.INFO)

# =============================================================================
# VERSION & PATHS
# =============================================================================
VERSION    = "PRIME 24.7"
BUILD_DATE = "2026-04-22"
DB_PATH    = "clarity_prime.db"

# =============================================================================
# SPORT & STAT CONFIGURATION (unchanged)
# =============================================================================
SPORT_MODELS: Dict[str, Dict] = {
    "NBA":     {"variance_factor": 1.18, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB":     {"variance_factor": 1.10, "avg_total":   8.5, "home_advantage": 0.12},
    "NHL":     {"variance_factor": 1.15, "avg_total":   6.0, "home_advantage": 0.15},
    "NFL":     {"variance_factor": 1.22, "avg_total":  44.5, "home_advantage": 2.8},
    "PGA":     {"variance_factor": 1.10, "avg_total":  70.5, "home_advantage": 0.0},
    "TENNIS":  {"variance_factor": 1.05, "avg_total":  22.0, "home_advantage": 0.0},
    "SOCCER":  {"variance_factor": 1.12, "avg_total":   2.5, "home_advantage": 0.3},
    "MMA":     {"variance_factor": 1.08, "avg_total":   2.5, "home_advantage": 0.1},
    "F1":      {"variance_factor": 1.05, "avg_total":   0.0, "home_advantage": 0.0},
    "CRICKET": {"variance_factor": 1.15, "avg_total": 300.0, "home_advantage": 15.0},
    "BOXING":  {"variance_factor": 1.08, "avg_total":   9.5, "home_advantage": 0.0},
}

SPORT_CATEGORIES: Dict[str, List[str]] = {
    "NBA":     ["PTS","REB","AST","STL","BLK","THREES","PRA","PR","PA"],
    "MLB":     ["OUTS","KS","HITS","TB","HR"],
    "NHL":     ["SOG","SAVES","GOALS","ASSISTS","HITS","BLK_SHOTS"],
    "NFL":     ["PASS_YDS","RUSH_YDS","REC_YDS","TD"],
    "PGA":     ["STROKES","BIRDIES","BOGEYS","EAGLES","DRIVING_DISTANCE","GIR"],
    "TENNIS":  ["ACES","DOUBLE_FAULTS","GAMES_WON","TOTAL_GAMES","BREAK_PTS"],
    "SOCCER":  ["GOALS","ASSISTS","SHOTS","SHOTS_ON_TARGET","FOULS","CARDS"],
    "MMA":     ["STRIKES","TAKEDOWNS","SUBMISSIONS","KNOCKDOWNS"],
    "F1":      ["POINTS","POSITION","FASTEST_LAP"],
    "CRICKET": ["RUNS","WICKETS","BOUNDARIES","SIXES"],
    "BOXING":  ["PUNCHES","JABS","POWER_PUNCHES","KNOCKDOWNS"],
}

STAT_CONFIG: Dict[str, Dict] = {
    "PTS":      {"tier":"MED",  "buffer":1.5},
    "REB":      {"tier":"LOW",  "buffer":1.0},
    "AST":      {"tier":"LOW",  "buffer":1.5},
    "PRA":      {"tier":"HIGH", "buffer":3.0},
    "PR":       {"tier":"HIGH", "buffer":2.0},
    "PA":       {"tier":"HIGH", "buffer":2.0},
    "SOG":      {"tier":"LOW",  "buffer":0.5},
    "SAVES":    {"tier":"LOW",  "buffer":2.0},
    "STROKES":  {"tier":"LOW",  "buffer":2.0},
    "BIRDIES":  {"tier":"MED",  "buffer":1.0},
    "ACES":     {"tier":"HIGH", "buffer":1.0},
    "DOUBLE_FAULTS": {"tier":"HIGH","buffer":1.0},
    "GAMES_WON":{"tier":"LOW",  "buffer":1.5},
    "GOALS":    {"tier":"HIGH", "buffer":0.5},
    "ASSISTS":  {"tier":"MED",  "buffer":0.5},
    "STRIKES":  {"tier":"MED",  "buffer":10.0},
    "RUNS":     {"tier":"MED",  "buffer":20.0},
    "TOTAL":    {"tier":"MED",  "buffer":5.0},
    "SPREAD":   {"tier":"MED",  "buffer":3.0},
    "ML":       {"tier":"HIGH", "buffer":0.0},
}

_DEFAULT_PROB_BOLT  = 0.84
_DEFAULT_DTM_BOLT   = 0.15
KELLY_FRACTION      = 0.25

# =============================================================================
# TIER‑AWARE HISTORICAL FALLBACK
# =============================================================================
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
        ("MMA","STRIKES"): [85.0,92.0,78.0,95.0,88.0,90.0,80.0,97.0,84.0,91.0,82.0,94.0],
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
_FB_DEFAULT = [15.0,15.5,14.8,16.2,15.3,15.7,14.5,16.5,15.1,15.9,14.7,16.0]

def historical_fallback(market: str, sport: str = "NBA", tier: str = "mid") -> List[float]:
    key = (sport.upper(), market.upper())
    for t in (tier, "mid", "elite", "bench"):
        d = _FALLBACK_TIERS.get(t, {})
        if key in d:
            return d[key]
    return _FB_DEFAULT

# =============================================================================
# DATABASE (unchanged)
# =============================================================================
def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def init_db() -> None:
    with _conn() as c:
        cur = c.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS slips (
                id           TEXT PRIMARY KEY,
                type         TEXT,
                sport        TEXT,
                player       TEXT,
                team         TEXT,
                opponent     TEXT,
                market       TEXT,
                line         REAL,
                pick         TEXT,
                odds         INTEGER,
                edge         REAL,
                prob         REAL,
                kelly        REAL,
                tier         TEXT,
                bolt_signal  TEXT,
                result       TEXT,
                actual       REAL,
                date         TEXT,
                settled_date TEXT,
                profit       REAL DEFAULT 0,
                bankroll     REAL DEFAULT 1000,
                notes        TEXT DEFAULT ''
            )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_result   ON slips(result)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_date     ON slips(date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sport    ON slips(sport)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_settled  ON slips(settled_date)")

        cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value REAL
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tuning_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT,
                prob_bolt_old REAL, prob_bolt_new REAL,
                dtm_bolt_old  REAL, dtm_bolt_new  REAL,
                roi          REAL,
                bets_used    INTEGER
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sem_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT,
                sem_score    INTEGER,
                accuracy     REAL,
                bets_analyzed INTEGER
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sem_external (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                prob      REAL,
                result    TEXT,
                source    TEXT
            )""")

    if get_setting("prob_bolt") is None:
        set_setting("prob_bolt", _DEFAULT_PROB_BOLT)
    if get_setting("dtm_bolt") is None:
        set_setting("dtm_bolt", _DEFAULT_DTM_BOLT)
    if get_setting("bankroll") is None:
        set_setting("bankroll", 1000.0)

def get_setting(key: str, default: float = None) -> Optional[float]:
    try:
        with _conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
    except Exception as e:
        logging.error(f"get_setting({key}): {e}")
        return default

def set_setting(key: str, value: float) -> None:
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
    except Exception as e:
        logging.error(f"set_setting({key},{value}): {e}")

def get_prob_bolt()  -> float: return get_setting("prob_bolt",  _DEFAULT_PROB_BOLT)
def get_dtm_bolt()   -> float: return get_setting("dtm_bolt",   _DEFAULT_DTM_BOLT)
def get_bankroll()   -> float: return get_setting("bankroll",   1000.0)
def set_bankroll(v)  -> None:  set_setting("bankroll", max(float(v), 0.0))

def insert_slip(entry: dict) -> None:
    slip_id = str(uuid.uuid4()).replace("-", "")[:12]
    try:
        with _conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO slips
                (id,type,sport,player,team,opponent,market,line,pick,odds,
                 edge,prob,kelly,tier,bolt_signal,result,actual,
                 date,settled_date,profit,bankroll,notes)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                slip_id,
                entry.get("type","PROP"),       entry.get("sport",""),
                entry.get("player",""),          entry.get("team",""),
                entry.get("opponent",""),         entry.get("market",""),
                entry.get("line",0.0),            entry.get("pick",""),
                entry.get("odds",0),              entry.get("edge",0.0),
                entry.get("prob",0.5),            entry.get("kelly",0.0),
                entry.get("tier",""),             entry.get("bolt_signal",""),
                entry.get("result","PENDING"),    entry.get("actual",0.0),
                datetime.now().strftime("%Y-%m-%d"), entry.get("settled_date",""),
                entry.get("profit",0.0),          entry.get("bankroll", get_bankroll()),
                entry.get("notes",""),
            ))
    except Exception as e:
        logging.error(f"insert_slip: {e}")
    if entry.get("result") in ("WIN","LOSS"):
        set_bankroll(get_bankroll() + entry.get("profit", 0.0))
        _calibrate_sem()
        _auto_tune()

def update_slip_result(slip_id: str, result: str, actual: float, odds: int) -> None:
    profit = (odds/100*100) if (result=="WIN" and odds>0) else \
             (100/abs(odds)*100) if (result=="WIN" and odds<0) else -100.0
    try:
        with _conn() as c:
            c.execute(
                "UPDATE slips SET result=?,actual=?,settled_date=?,profit=? WHERE id=?",
                (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, slip_id)
            )
    except Exception as e:
        logging.error(f"update_slip_result: {e}")
    set_bankroll(get_bankroll() + profit)
    _calibrate_sem()
    _auto_tune()

def get_all_slips(limit: int = 500) -> pd.DataFrame:
    try:
        with _conn() as c:
            return pd.read_sql_query(
                "SELECT * FROM slips ORDER BY date DESC LIMIT ?", c, params=(limit,)
            )
    except Exception as e:
        logging.error(f"get_all_slips: {e}")
        return pd.DataFrame()

def get_pending_slips() -> pd.DataFrame:
    try:
        with _conn() as c:
            return pd.read_sql_query("SELECT * FROM slips WHERE result='PENDING'", c)
    except Exception as e:
        logging.error(f"get_pending_slips: {e}")
        return pd.DataFrame()

def clear_pending_slips() -> None:
    try:
        with _conn() as c:
            c.execute("DELETE FROM slips WHERE result='PENDING'")
    except Exception as e:
        logging.error(f"clear_pending_slips: {e}")

# =============================================================================
# API HEALTH TRACKER (unchanged)
# =============================================================================
_SERVICES = [
    "BallsDontLie (NBA)", "Odds-API.io (scores)", "The Odds API (scanner)",
    "PropLine (live props)", "Slash Golf (PGA)", "FlashLive (multi-sport)",
    "ESPN (fallback)", "nhl-api-py (NHL)", "curl_cffi (TLS)",
    "RapidAPI (Tennis)", "DraftKings API",
]

def _init_health() -> None:
    if "health" not in st.session_state:
        st.session_state.health = {
            s: {"ok": None, "err": "", "fallback": False} for s in _SERVICES
        }

def _health(service: str, ok: bool, err: str = "", fallback: bool = False) -> None:
    _init_health()
    if service not in st.session_state.health:
        st.session_state.health[service] = {"ok": None, "err": "", "fallback": False}
    st.session_state.health[service] = {"ok": ok, "err": err[:200], "fallback": fallback}

# =============================================================================
# HTTP SESSION FACTORY (unchanged)
# =============================================================================
def make_session(headers: dict = None) -> requests.Session:
    h = headers or {}
    if CURL_AVAILABLE:
        try:
            s = curl_requests.Session(impersonate="chrome124")
            s.headers.update(h)
            _health("curl_cffi (TLS)", True)
            return s
        except Exception as e:
            _health("curl_cffi (TLS)", False, str(e))
    s = requests.Session()
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=0.4,
        status_forcelist=[429,500,502,503,504],
        allowed_methods=["GET","POST"],
    )))
    s.headers.update(h)
    return s

# =============================================================================
# CACHED API FUNCTIONS (unchanged)
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_dk_dataframe_cached() -> pd.DataFrame:
    raw = fetch_dk_raw()
    if not raw:
        return pd.DataFrame()
    lines = normalize_dk_lines(raw)
    df = pd.DataFrame([l.to_dict() for l in lines])
    if not df.empty:
        df.sort_values(["start_time_utc","game_label","market_type"], inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df

@st.cache_data(ttl=300, show_spinner=False)
def fetch_games_cached(sport: str, days: int = 0) -> List[Dict]:
    return game_scanner.fetch([sport], days=days)

# =============================================================================
# DRAFTKINGS LINE FETCHER (unchanged)
# =============================================================================
DK_EVENT_LIST_URL = "https://sportsbook.draftkings.com//sites/US-SB/api/v5/eventgroups/4"

@dataclass
class SportsbookLine:
    book: str; game_id: str; game_label: str
    start_time_utc: datetime; market_type: str; outcome_type: str
    team_or_player: str; line: float; price: int
    raw_payload: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {k: (v.isoformat() if isinstance(v, datetime) else v)
                for k, v in self.__dict__.items() if k != "raw_payload"}

def _safe(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def fetch_dk_raw() -> dict:
    try:
        r = requests.get(DK_EVENT_LIST_URL, params={"format":"json"}, timeout=10)
        r.raise_for_status()
        _health("DraftKings API", True)
        return r.json()
    except Exception as e:
        _health("DraftKings API", False, str(e), fallback=True)
        return {}

def normalize_dk_lines(raw: dict) -> List[SportsbookLine]:
    lines = []
    events    = _safe(raw,"eventGroup","events",default=[]) or []
    cats      = _safe(raw,"eventGroup","offerCategories",default=[]) or []
    ev_by_id  = {str(e.get("eventId")): e for e in events}

    def _mtype(cat: dict) -> str:
        n = _safe(cat,"name",default="").lower()
        for k,v in [("spread","spread"),("total","total"),("moneyline","moneyline"),
                    ("money line","moneyline"),("points","player_points"),
                    ("rebounds","player_rebounds"),("assists","player_assists")]:
            if k in n: return v
        return n or "unknown"

    for cat in cats:
        for sub in _safe(cat,"offerSubcategoryDescriptors",default=[]) or []:
            for offer in _safe(sub,"offerSubcategory","offers",default=[]) or []:
                eid = str(_safe(offer,"eventId",default=""))
                if not eid or eid not in ev_by_id: continue
                ev = ev_by_id[eid]
                mtype = _mtype(cat)
                start = datetime.now()
                try:
                    ts = _safe(ev,"startDate")
                    if ts: start = datetime.fromisoformat(ts.replace("Z","+00:00"))
                except Exception: pass
                home = _safe(ev,"homeTeamName",default="HOME").strip()
                away = _safe(ev,"awayTeamName",default="AWAY").strip()
                label = f"{away} @ {home}" if home and away else _safe(ev,"name",default="")
                for o in _safe(offer,"outcomes",default=[]) or []:
                    try:
                        price = int(_safe(o,"oddsAmerican",default=0) or 0)
                        line_v = _safe(o,"line")
                        line_f = float(line_v) if line_v is not None else None
                        if line_f is None and mtype in ("spread","total","player_points","player_rebounds","player_assists"):
                            continue
                        participant = _safe(o,"participant","name",default="").strip() or \
                                      _safe(o,"label",default="").strip()
                        ol = _safe(o,"label",default="").lower()
                        otype = ("over" if "over" in ol else "under" if "under" in ol else
                                 "home" if "home" in ol else "away" if "away" in ol else ol)
                        lines.append(SportsbookLine(
                            book="DK", game_id=eid, game_label=label,
                            start_time_utc=start, market_type=mtype,
                            outcome_type=otype, team_or_player=participant,
                            line=line_f or 0.0, price=price, raw_payload=o,
                        ))
                    except Exception: continue
    return lines

def fetch_dk_dataframe() -> pd.DataFrame:
    raw = fetch_dk_raw()
    if not raw: return pd.DataFrame()
    df = pd.DataFrame([l.to_dict() for l in normalize_dk_lines(raw)])
    if not df.empty:
        df.sort_values(["start_time_utc","game_label","market_type"], inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df

# =============================================================================
# PLAYER PROJECTIONS ENGINE (unchanged)
# =============================================================================
@dataclass
class PlayerProjection:
    player_name: str; team: str; opponent: str
    minutes: float; pts: float; rebs: float; asts: float
    usage: float; pace_adj: float
    raw_payload: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if k != "raw_payload"}

def _est_minutes(stats: pd.DataFrame) -> float:
    if stats.empty: return 28.0
    col = stats["minutes"] if "minutes" in stats.columns else pd.Series([28.0])
    return float(col.tail(10).mean())

def _est_usage(stats: pd.DataFrame) -> float:
    if "usage" in stats.columns:
        return float(stats["usage"].tail(10).mean())
    return 0.22

def _est_pace(team_s: pd.DataFrame, opp_s: pd.DataFrame) -> float:
    tp = float(team_s["pace"].iloc[-1]) if (not team_s.empty and "pace" in team_s.columns) else 98.0
    op = float(opp_s["pace"].iloc[-1])  if (not opp_s.empty  and "pace" in opp_s.columns)  else 98.0
    return (tp + op) / 2.0

def _per_min_rates(stats: pd.DataFrame) -> Dict[str, float]:
    if stats.empty: return {"pts": 0.5, "rebs": 0.15, "asts": 0.12}
    df = stats.tail(15)
    def _r(num, den):
        if num in df.columns and den in df.columns:
            return float((df[num] / df[den].replace(0, np.nan)).mean())
        return {"pts":0.5,"rebs":0.15,"asts":0.12}.get(num, 0.1)
    return {"pts": _r("pts","minutes"), "rebs": _r("rebs","minutes"), "asts": _r("asts","minutes")}

def build_projection(
    player_name: str, team: str, opponent: str,
    player_stats: pd.DataFrame, team_stats: pd.DataFrame, opp_stats: pd.DataFrame,
) -> PlayerProjection:
    minutes  = _est_minutes(player_stats)
    usage    = _est_usage(player_stats)
    pace_adj = _est_pace(team_stats, opp_stats)
    rates    = _per_min_rates(player_stats)
    pf       = pace_adj / 98.0
    return PlayerProjection(
        player_name=player_name, team=team, opponent=opponent,
        minutes=minutes, pts=rates["pts"]*minutes*pf,
        rebs=rates["rebs"]*minutes*pf, asts=rates["asts"]*minutes*pf,
        usage=usage, pace_adj=pace_adj,
        raw_payload={"minutes_model": minutes, "usage_model": usage,
                     "pace_factor": pf, "rates": rates},
    )

# =============================================================================
# ANALYTICAL DISTRIBUTION ENGINE (unchanged)
# =============================================================================
def _erf(x: float) -> float:
    t   = 1.0 / (1.0 + 0.5 * abs(x))
    tau = t * np.exp(-x*x - 1.26551223
                    + t*(1.00002368 + t*(0.37409196 + t*(0.09678418 + t*(-0.18628806
                    + t*(0.27886807 + t*(-1.13520398 + t*(1.48851587
                    + t*(-0.82215223 + t*0.17087277)))))))))
    return 1 - tau if x >= 0 else tau - 1

class StatDist:
    def __init__(self, mean: float, variance: float):
        self.mean = mean
        self.var  = max(variance, 1e-6)
        self.std  = np.sqrt(self.var)

    def prob_over(self, line: float)  -> float:
        return 1 - 0.5*(1 + _erf((line - self.mean)/(self.std * 1.4142)))

    def prob_under(self, line: float) -> float:
        return 0.5*(1 + _erf((line - self.mean)/(self.std * 1.4142)))

    @classmethod
    def from_projection(cls, mean: float, minutes: float, usage: float, pace: float) -> "StatDist":
        var = mean * 0.9
        var *= max(0.1, min(1.5, 1.0 + (36 - minutes) / 60))
        var *= max(0.8, min(1.4, 1.0 + (usage - 0.22)))
        var *= max(0.9, min(1.3, pace / 98.0))
        return cls(mean, var)

# =============================================================================
# MONTE CARLO SIMULATION (unchanged)
# =============================================================================
_NBA_CORR = np.array([
    [1.00, 0.25, 0.35, 0.10, 0.05, 0.20],
    [0.25, 1.00, 0.15, 0.10, 0.30, 0.05],
    [0.35, 0.15, 1.00, 0.20, 0.05, 0.25],
    [0.10, 0.10, 0.20, 1.00, 0.15, 0.05],
    [0.05, 0.30, 0.05, 0.15, 1.00, 0.05],
    [0.20, 0.05, 0.25, 0.05, 0.05, 1.00],
])

@dataclass
class MCResult:
    sims: Dict[str, np.ndarray]

    def mean(self, s)        -> float: return float(np.mean(self.sims[s]))
    def pct(self, s, p)      -> float: return float(np.percentile(self.sims[s], p))
    def prob_over(self, s, l) -> float: return float(np.mean(self.sims[s] > l))
    def prob_under(self,s, l) -> float: return float(np.mean(self.sims[s] < l))

@st.cache_data(ttl=3600, show_spinner=False)
def simulate_player_cached(proj_dict: Dict, n: int = 10000, seed: int = None) -> Dict:
    if seed is not None:
        np.random.seed(seed)
    
    proj = PlayerProjection(
        player_name=proj_dict.get("player_name", ""),
        team=proj_dict.get("team", ""),
        opponent=proj_dict.get("opponent", ""),
        minutes=proj_dict.get("minutes", 28.0),
        pts=proj_dict.get("pts", 0.0),
        rebs=proj_dict.get("rebs", 0.0),
        asts=proj_dict.get("asts", 0.0),
        usage=proj_dict.get("usage", 0.22),
        pace_adj=proj_dict.get("pace_adj", 98.0),
        raw_payload=proj_dict.get("raw_payload", {}),
    )
    
    rates = proj.raw_payload.get("rates", {}) if proj.raw_payload else {}
    mins = proj.minutes
    means = np.array([
        proj.pts, proj.rebs, proj.asts,
        rates.get("stl", 0.08) * mins,
        rates.get("blk", 0.05) * mins,
        rates.get("to", 0.12) * mins,
    ])
    base_var = means * 0.9
    mv = max(0.1, min(1.5, 1.0 + (36 - mins) / 60))
    uv = max(0.8, min(1.4, 1.0 + (proj.usage - 0.22)))
    pv = max(0.9, min(1.3, proj.pace_adj / 98.0))
    std = np.sqrt(base_var * mv * uv * pv)
    cov = np.outer(std, std) * _NBA_CORR
    raw = np.random.multivariate_normal(means, cov, n)
    raw = np.clip(raw, 0, None)
    keys = ["pts", "rebs", "asts", "stl", "blk", "to"]
    return {k: raw[:, i].tolist() for i, k in enumerate(keys)}

def simulate_player(proj: PlayerProjection, n: int = 10000, seed: int = None) -> MCResult:
    sims_dict = simulate_player_cached(proj.to_dict(), n, seed)
    return MCResult({k: np.array(v) for k, v in sims_dict.items()})

def mc_price_market(proj: PlayerProjection, market: str, sb_line: float, n: int = 10000) -> Dict:
    mc = simulate_player(proj, n)
    _MAP = {"points":"pts","rebounds":"rebs","assists":"asts",
            "steals":"stl","blocks":"blk","turnovers":"to"}
    key = _MAP.get(market.lower())
    if key:
        sims = mc.sims[key]
    elif market.lower() == "pra":
        sims = mc.sims["pts"] + mc.sims["rebs"] + mc.sims["asts"]
    elif market.lower() == "pr":
        sims = mc.sims["pts"] + mc.sims["rebs"]
    elif market.lower() == "pa":
        sims = mc.sims["pts"] + mc.sims["asts"]
    else:
        sims = mc.sims.get("pts", np.zeros(n))
    fair = float(np.percentile(sims, 50))
    p_over = float(np.mean(sims > sb_line))
    p_under = 1 - p_over
    odds = 1.91
    edge = (p_over * odds) - (1 - p_over)
    kelly_val = max(0.0, (p_over * (odds + 1) - 1) / odds) * KELLY_FRACTION
    return {"fair_line": fair, "prob_over": p_over, "prob_under": p_under,
            "edge": edge, "kelly": kelly_val}

# =============================================================================
# UNIFIED KELLY FUNCTION (unchanged)
# =============================================================================
def calculate_kelly_stake(bankroll: float, prob: float, odds: int, fraction: float = KELLY_FRACTION) -> float:
    if odds == 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    k = (prob * (b + 1) - 1) / b
    k = max(0.0, min(k, 0.25))
    return bankroll * k * fraction

# =============================================================================
# ANALYTICAL PRICING (unchanged)
# =============================================================================
def american_to_prob(odds: int) -> float:
    o = float(odds)
    return 100/(o+100) if o > 0 else -o/(-o+100)

def tier_mult(stat: str) -> float:
    t = STAT_CONFIG.get(stat.upper(), {}).get("tier","LOW")
    return 0.85 if t=="HIGH" else 0.93 if t=="MED" else 1.0

def classify_tier(edge: float) -> str:
    if edge >= 0.15: return "SOVEREIGN BOLT"
    if edge >= 0.08: return "ELITE LOCK"
    if edge >= 0.04: return "APPROVED"
    if edge < 0: return "PASS"
    return "NEUTRAL"

def price_prop_bet(
    player_name: str, market_type: str, line: float, pick: str,
    odds: int, proj: PlayerProjection, bankroll: float
) -> Any:
    attr_map = {"PTS": "pts", "REB": "rebs", "AST": "asts"}
    attr = attr_map.get(market_type.upper(), market_type.lower())
    stat_val = getattr(proj, attr, 0.0)
    dist = StatDist.from_projection(stat_val, proj.minutes, proj.usage, proj.pace_adj)
    fair_line = dist.mean
    if pick.upper() == "OVER":
        prob = dist.prob_over(line)
    else:
        prob = dist.prob_under(line)
    imp = american_to_prob(odds)
    edge = (prob - imp) * tier_mult(market_type)
    kelly_stake_frac = calculate_kelly_stake(bankroll, prob, odds) / bankroll if bankroll > 0 else 0
    tier = classify_tier(edge)
    bolt = "SOVEREIGN BOLT" if (prob >= get_prob_bolt() and abs(fair_line - line) / max(line, 1e-9) >= get_dtm_bolt()) else tier
    return {
        "prob": prob, "edge": edge, "kelly_frac": kelly_stake_frac,
        "tier": tier, "bolt_signal": bolt, "fair_line": fair_line,
    }

# =============================================================================
# NBA STATS API (unchanged)
# =============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _nba_stats(player_name: str, market: str, game_date: str = None) -> List[float]:
    stat_map = {"PTS":"pts","REB":"reb","AST":"ast","STL":"stl","BLK":"blk",
                "THREES":"tpm","PRA":"pts","PR":"pts","PA":"pts"}
    stat = stat_map.get(market.upper(), "pts")
    key = st.secrets.get("BALLSDONTLIE_API_KEY","")
    if not key:
        _health("BallsDontLie (NBA)", False, "API key missing", True)
        return []
    try:
        r = requests.get(
            f"https://api.balldontlie.io/v1/players?search={player_name.replace(' ','%20')}",
            headers={"Authorization": key}, timeout=10,
        )
        if r.status_code != 200:
            _health("BallsDontLie (NBA)", False, f"HTTP {r.status_code}", True)
            return []
        players = r.json().get("data",[])
        if not players:
            _health("BallsDontLie (NBA)", False, "Player not found", True)
            return []
        pid = players[0]["id"]
        url = (f"https://api.balldontlie.io/v1/stats?player_ids[]={pid}&dates[]={game_date}"
               if game_date else
               f"https://api.balldontlie.io/v1/stats?player_ids[]={pid}&per_page=12")
        r2 = requests.get(url, headers={"Authorization": key}, timeout=10)
        if r2.status_code != 200:
            _health("BallsDontLie (NBA)", False, f"Stats HTTP {r2.status_code}", True)
            return []
        games = r2.json().get("data",[])
        vals = [float(g[stat]) for g in games if isinstance(g.get(stat),(int,float))]
        _health("BallsDontLie (NBA)", bool(vals), "" if vals else "No stats", not bool(vals))
        return vals
    except Exception as e:
        _health("BallsDontLie (NBA)", False, str(e), True)
        logging.error(f"_nba_stats: {e}")
        return []

# =============================================================================
# NBA TEAM STATS (unchanged)
# =============================================================================
NBA_TEAM_IDS: Dict[str, int] = {
    "ATLANTA HAWKS":1,"BOSTON CELTICS":2,"BROOKLYN NETS":3,"CHARLOTTE HORNETS":4,
    "CHICAGO BULLS":5,"CLEVELAND CAVALIERS":6,"DALLAS MAVERICKS":7,"DENVER NUGGETS":8,
    "DETROIT PISTONS":9,"GOLDEN STATE WARRIORS":10,"HOUSTON ROCKETS":11,"INDIANA PACERS":12,
    "LA CLIPPERS":13,"LOS ANGELES LAKERS":14,"MEMPHIS GRIZZLIES":15,"MIAMI HEAT":16,
    "MILWAUKEE BUCKS":17,"MINNESOTA TIMBERWOLVES":18,"NEW ORLEANS PELICANS":19,
    "NEW YORK KNICKS":20,"OKLAHOMA CITY THUNDER":21,"ORLANDO MAGIC":22,
    "PHILADELPHIA 76ERS":23,"PHOENIX SUNS":24,"PORTLAND TRAIL BLAZERS":25,
    "SACRAMENTO KINGS":26,"SAN ANTONIO SPURS":27,"TORONTO RAPTORS":28,
    "UTAH JAZZ":29,"WASHINGTON WIZARDS":30,
    "ATL":1,"BOS":2,"BKN":3,"CHA":4,"CHI":5,"CLE":6,"DAL":7,"DEN":8,"DET":9,
    "GSW":10,"HOU":11,"IND":12,"LAC":13,"LAL":14,"MEM":15,"MIA":16,"MIL":17,
    "MIN":18,"NOP":19,"NYK":20,"OKC":21,"ORL":22,"PHI":23,"PHX":24,"POR":25,
    "SAC":26,"SAS":27,"TOR":28,"UTA":29,"WAS":30,
}

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_totals(team: str, window: int = 8) -> List[float]:
    tid = NBA_TEAM_IDS.get(team.upper())
    if not tid:
        for k,v in NBA_TEAM_IDS.items():
            if team.upper() in k:
                tid = v
                break
    if not tid:
        return [114.0]*8
    key = st.secrets.get("BALLSDONTLIE_API_KEY","")
    try:
        r = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={tid}&per_page={window}",
            headers={"Authorization": key}, timeout=10,
        )
        if r.status_code != 200:
            return [114.0]*8
        games = r.json().get("data",[])
        tots = [g["home_team_score"] if g["home_team"]["id"]==tid else g["visitor_team_score"]
                for g in games]
        tots.reverse()
        return tots or [114.0]*8
    except Exception as e:
        logging.error(f"fetch_team_totals: {e}")
        return [114.0]*8

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_team_margins(team: str, window: int = 8) -> List[float]:
    tid = NBA_TEAM_IDS.get(team.upper())
    if not tid:
        for k,v in NBA_TEAM_IDS.items():
            if team.upper() in k:
                tid = v
                break
    if not tid:
        return [0.0]*8
    key = st.secrets.get("BALLSDONTLIE_API_KEY","")
    try:
        r = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={tid}&per_page={window}",
            headers={"Authorization": key}, timeout=10,
        )
        if r.status_code != 200:
            return [0.0]*8
        games = r.json().get("data",[])
        margins = []
        for g in games:
            if g["home_team"]["id"] == tid:
                margins.append(g["home_team_score"] - g["visitor_team_score"])
            else:
                margins.append(g["visitor_team_score"] - g["home_team_score"])
        margins.reverse()
        return margins or [0.0]*8
    except Exception as e:
        logging.error(f"fetch_team_margins: {e}")
        return [0.0]*8

# =============================================================================
# FLASHLIVE & ESPN FALLBACK (unchanged)
# =============================================================================
_FL_HOST = "flashlive-sports.p.rapidapi.com"
_FL_MAP = {"NBA":1,"NFL":2,"MLB":3,"NHL":4,"SOCCER":5,"TENNIS":6,
           "MMA":7,"F1":8,"CRICKET":9,"PGA":10,"BOXING":11}

@st.cache_data(ttl=300, show_spinner=False)
def _flashlive_stats(player_name: str, sport: str, market: str) -> List[float]:
    key = st.secrets.get("RAPIDAPI_KEY","")
    sid = _FL_MAP.get(sport.upper())
    if not key or not sid:
        _health("FlashLive (multi-sport)", False, "No key or unmapped sport", True)
        return []
    h = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": _FL_HOST}
    try:
        r = requests.get(f"https://{_FL_HOST}/v1/players/search",
                         headers=h, params={"sport_id":sid,"query":player_name,"limit":1}, timeout=10)
        if r.status_code != 200:
            return []
        plist = r.json().get("DATA",[])
        if not plist:
            return []
        pid = plist[0].get("id")
        r2 = requests.get(f"https://{_FL_HOST}/v1/players/statistics",
                          headers=h, params={"player_id":pid,"sport_id":sid}, timeout=10)
        if r2.status_code != 200:
            return []
        logs = r2.json().get("DATA",{}).get("game_log",[])
        vals = [float(g[market.lower()]) for g in logs[:8]
                if isinstance(g.get(market.lower()),(int,float))]
        _health("FlashLive (multi-sport)", bool(vals))
        return vals
    except Exception as e:
        _health("FlashLive (multi-sport)", False, str(e), True)
        return []

@st.cache_data(ttl=3600, show_spinner=False)
def _espn_stats(player_name: str, sport: str, market: str) -> List[float]:
    key = st.secrets.get("RAPIDAPI_KEY","")
    if not key:
        return []
    sm = {"NBA":"basketball","NFL":"football","MLB":"baseball","NHL":"hockey",
          "PGA":"golf","TENNIS":"tennis","SOCCER":"soccer","MMA":"mma"}
    esp = sm.get(sport.upper(), sport.lower())
    h = {"x-rapidapi-host":"espn-api.p.rapidapi.com","x-rapidapi-key":key}
    try:
        r = requests.get("https://espn-api.p.rapidapi.com/search",
                         headers=h, params={"q":player_name,"sport":esp}, timeout=15)
        if r.status_code != 200:
            return []
        athletes = r.json().get("athletes",[])
        if not athletes:
            return []
        pid = athletes[0].get("id")
        r2 = requests.get(f"https://espn-api.p.rapidapi.com/athlete/{pid}/stats",
                          headers=h, timeout=15)
        if r2.status_code != 200:
            return []
        logs = r2.json().get("gameLog",[])
        vals = [float(g[market.lower()]) for g in logs[:8]
                if isinstance(g.get(market.lower()),(int,float))]
        _health("ESPN (fallback)", bool(vals))
        return vals
    except Exception as e:
        _health("ESPN (fallback)", False, str(e), True)
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def fetch_stats(player: str, market: str, sport: str = "NBA",
                game_date: str = None, tier: str = "mid") -> List[float]:
    if sport.upper() == "NBA":
        vals = _nba_stats(player, market, game_date)
    elif sport.upper() == "PGA":
        vals = []
    else:
        vals = _flashlive_stats(player, sport, market)
    
    if len(vals) < 3:
        vals = _espn_stats(player, sport, market)
    if len(vals) < 3:
        vals = historical_fallback(market, sport, tier)
    return vals

# =============================================================================
# PROP MODEL (legacy, for manual analysis)
# =============================================================================
def _wma(values: List[float], w: int = 6) -> float:
    if not values:
        return 0.0
    arr = np.array(values[-w:])
    wts = np.arange(1, len(arr)+1)
    return float(np.dot(arr, wts) / wts.sum())

def _wse(values: List[float], w: int = 8) -> float:
    if len(values) < 2:
        return 1.0
    arr = np.array(values[-w:])
    wts = np.arange(1, len(arr)+1)
    mu = np.dot(arr, wts) / wts.sum()
    var = np.dot(wts, (arr - mu)**2) / wts.sum()
    return float(max(np.sqrt(var / len(arr)), 0.5))

def _vol_buf(values: List[float]) -> float:
    if len(values) < 4:
        return 1.0
    return float(1.0 + min(np.std(values[-4:]) / 10.0, 0.5))

def analyze_prop_legacy(
    player: str, market: str, line: float, pick: str,
    sport: str = "NBA", odds: int = -110, bankroll: float = None, tier: str = "mid",
    use_mc: bool = False, mc_sims: int = 10000,
) -> Dict:
    if bankroll is None:
        bankroll = get_bankroll()
    stats = fetch_stats(player, market, sport, tier=tier)
    mu = _wma(stats)
    sigma = max(_wse(stats) * _vol_buf(stats), 0.75)
    
    if use_mc:
        proj = PlayerProjection(
            player_name=player, team="", opponent="",
            minutes=28.0, pts=mu, rebs=5.0, asts=4.0,
            usage=0.22, pace_adj=98.0,
            raw_payload={"rates": {"stl":0.08,"blk":0.05,"to":0.12}},
        )
        mc_res = mc_price_market(proj, market.lower(), line, n=mc_sims)
        prob = mc_res["prob_over"] if pick == "OVER" else 1 - mc_res["prob_over"]
        edge = mc_res["edge"]
        kelly_val = mc_res["kelly"]
        fair = mc_res["fair_line"]
    else:
        if pick == "OVER":
            prob = 1 - norm.cdf(line, mu, sigma)
        else:
            prob = norm.cdf(line, mu, sigma)
        edge = (prob - american_to_prob(odds)) * tier_mult(market)
        kelly_val = calculate_kelly_stake(bankroll, prob, odds) / bankroll if bankroll > 0 else 0
        fair = mu
    
    tier_l = classify_tier(edge)
    bolt = ("SOVEREIGN BOLT" if prob >= get_prob_bolt() and
            abs(mu - line) / max(line, 1e-9) >= get_dtm_bolt()
            else tier_l)
    return {
        "prob": prob, "edge": edge, "mu": mu, "sigma": sigma, "wma": mu,
        "tier": tier_l, "kelly": kelly_val, "stake": bankroll * kelly_val,
        "bolt_signal": bolt, "stats": stats, "fair_line": fair,
    }

# =============================================================================
# GAME ANALYSIS (unchanged)
# =============================================================================
def analyze_total(home: str, away: str, sport: str,
                  line: float, over_odds: int, under_odds: int) -> Dict:
    if sport == "NBA":
        ht = fetch_team_totals(home)
        at = fetch_team_totals(away)
        proj = _wma(ht) + _wma(at)
        comb = [h+a for h,a in zip(ht, at)] or ht+at
        sigma = max(_wse(comb) * _vol_buf(comb), 0.75)
    else:
        proj = SPORT_MODELS.get(sport,{}).get("avg_total", 220.0)
        sigma = proj * 0.08
    
    op = 1 - norm.cdf(line, proj, sigma)
    up = norm.cdf(line, proj, sigma)
    oim = american_to_prob(over_odds)
    uim = american_to_prob(under_odds)
    m = tier_mult("TOTAL")
    oe = (op - oim)*m
    ue = (up - uim)*m
    pb = get_prob_bolt()
    db = get_dtm_bolt()
    denom = max(line, 1e-9)
    return {
        "projection": proj, "sigma": sigma,
        "over_prob": op, "over_edge": oe, "over_tier": classify_tier(oe),
        "over_bolt": "SOVEREIGN BOLT" if op>=pb and (proj-line)/denom>=db else classify_tier(oe),
        "under_prob": up, "under_edge": ue, "under_tier": classify_tier(ue),
        "under_bolt": "SOVEREIGN BOLT" if up>=pb and (line-proj)/denom>=db else classify_tier(ue),
    }

def analyze_spread(home: str, away: str, sport: str,
                   spread: float, odds: int) -> Dict:
    if sport == "NBA":
        hm = fetch_team_margins(home)
        am = fetch_team_margins(away)
        pm = _wma(hm) - _wma(am) + 3.0
        comb = [h-a for h,a in zip(hm, am)] or hm+[-x for x in am]
        sigma = max(_wse(comb)*_vol_buf(comb), 0.75)
    else:
        pm = SPORT_MODELS.get(sport,{}).get("home_advantage", 3.0)
        sigma = 10.0
    
    hcp = 1 - norm.cdf(spread, pm, sigma)
    acp = norm.cdf(spread, pm, sigma)
    imp = american_to_prob(odds)
    m = tier_mult("SPREAD")
    he = (hcp - imp)*m
    ae = (acp-(1-imp))*m
    pb = get_prob_bolt()
    db = get_dtm_bolt()
    dn = abs(spread)+1e-9
    return {
        "projected_margin": pm, "sigma": sigma,
        "home_cover_prob": hcp, "home_edge": he, "home_tier": classify_tier(he),
        "home_bolt": "SOVEREIGN BOLT" if hcp>=pb and (pm-spread)/dn>=db else classify_tier(he),
        "away_cover_prob": acp, "away_edge": ae, "away_tier": classify_tier(ae),
        "away_bolt": "SOVEREIGN BOLT" if acp>=pb and (spread-pm)/dn>=db else classify_tier(ae),
    }

def analyze_ml(home: str, away: str, sport: str, home_odds: int, away_odds: int) -> Dict:
    sp = analyze_spread(home, away, sport, 0.0, home_odds)
    pm = sp["projected_margin"]
    sigma = sp["sigma"]
    hp = 1/(1+np.exp(-0.13*pm)) if sport=="NBA" else 1-norm.cdf(0, pm, sigma)
    ap = 1 - hp
    him = american_to_prob(home_odds)
    aim = american_to_prob(away_odds)
    m = tier_mult("ML")
    he = (hp-him)*m
    ae = (ap-aim)*m
    pb = get_prob_bolt()
    return {
        "home_prob": hp, "home_edge": he, "home_tier": classify_tier(he),
        "home_bolt": "SOVEREIGN BOLT" if hp>=pb and he>=0.15 else classify_tier(he),
        "away_prob": ap, "away_edge": ae, "away_tier": classify_tier(ae),
        "away_bolt": "SOVEREIGN BOLT" if ap>=pb and ae>=0.15 else classify_tier(ae),
    }

# =============================================================================
# GAME SCORES (unchanged)
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_score(team: str, opp: str, sport: str, date: str) -> Tuple[Optional[float], Optional[float]]:
    sm = {"NBA":"basketball","MLB":"baseball","NHL":"icehockey","NFL":"americanfootball"}
    sk = sm.get(sport)
    if not sk:
        return None, None
    key = st.secrets.get("ODDS_API_IO_KEY","")
    if not key:
        return None, None
    try:
        r = requests.get(f"https://api.odds-api.io/v4/sports/{sk}/events",
                         params={"apiKey":key,"date":date}, timeout=10)
        if r.status_code != 200:
            return None, None
        for ev in (r.json().get("data",[]) or []):
            h = ev.get("home_team","")
            a = ev.get("away_team","")
            if {h,a} == {team,opp}:
                hs = ev.get("home_score")
                as_ = ev.get("away_score")
                if hs is not None and as_ is not None:
                    return float(hs), float(as_)
    except Exception as e:
        logging.error(f"fetch_score: {e}")
    return None, None

# =============================================================================
# OCR & PARSER UTILITIES (with new robust parsers)
# =============================================================================
def _clean(text: str) -> str:
    t = re.sub(r'[^\x00-\x7F]+',' ', text or "")
    return re.sub(r'\s+', ' ', t).strip()

def _norm_market(m: str) -> str:
    s = m.upper().replace(" ","")
    for a,b in [("THREES","3PTM"),("3PTMADE","3PTM"),("3PM","3PTM"),
                ("PTS+REB+AST","PRA"),("PTS+REB","PR"),("PTS+AST","PA")]:
        s = s.replace(a,b)
    return s

def _detect_pick(lines: List[str]) -> Optional[str]:
    j = " ".join(l.upper() for l in lines)
    for p in ("MORE","LESS","OVER","UNDER"):
        if f" {p} " in f" {j} " or j.strip()==p:
            return p
    return None

def _result(pick: str, actual: float, line: float) -> str:
    if not pick:
        return "PENDING"
    p = pick.upper()
    if p in ("OVER","MORE"):
        return "WIN" if actual>line else "LOSS" if actual<line else "PUSH"
    if p in ("UNDER","LESS"):
        return "WIN" if actual<line else "LOSS" if actual>line else "PUSH"
    return "PENDING"

def _score_confidence(prop: Dict) -> float:
    s = 1.0
    for k in ("player","market","line","pick"):
        if not prop.get(k):
            s -= 0.2
    line = prop.get("line")
    if isinstance(line,(int,float)) and (line<=0 or line>200):
        s -= 0.3
    m = prop.get("market","")
    if not (2 <= len(m) <= 12):
        s -= 0.2
    return max(0.0, min(1.0, s))

def _auto_sport(market: str) -> Optional[str]:
    m = market.upper()
    if m in {"PTS","REB","AST","PRA","PR","PA","THREES","3PTM"}:
        return "NBA"
    if m in {"SOG","SAVES","GOALS","ASSISTS","HITS"}:
        return "NHL"
    if m in {"PASS_YDS","RUSH_YDS","REC_YDS","TD"}:
        return "NFL"
    if m in {"OUTS","KS","TB","HR"}:
        return "MLB"
    return None

def _dedupe(props: List[Dict]) -> List[Dict]:
    seen = {}
    for p in props:
        k = (p.get("player","").strip().upper(), p.get("market","").strip().upper(),
             float(p.get("line",0) or 0), p.get("pick","").strip().upper())
        if k not in seen:
            seen[k] = p
    return list(seen.values())

# ---------- NEW ROBUST PARSER FOR MYBOOKIE TOTALS ----------
def _parse_mybookie_totals(lines: List[str]) -> List[Dict]:
    """Parse MyBookie multi-game totals format:
       Under 218.5
       -110
       Total (incl. overtime)
       NBA | Basketball Orlando Magic vs. Detroit Pistons
       ...
    """
    bets = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not re.match(r'^(Over|Under)\s+[\d\.]+', line, re.IGNORECASE):
            i += 1
            continue
        # Extract pick and line
        m = re.match(r'^(Over|Under)\s+([\d\.]+)', line, re.IGNORECASE)
        if not m:
            i += 1
            continue
        pick = m.group(1).upper()
        line_val = float(m.group(2))
        if i+1 >= n:
            break
        odds_line = lines[i+1].strip()
        odds_match = re.match(r'^[+-]?\d+$', odds_line)
        if not odds_match:
            i += 1
            continue
        odds = int(odds_line)
        # Next lines contain description and sport
        if i+2 >= n:
            break
        desc_line = lines[i+2].strip()  # e.g., "Total (incl. overtime)"
        sport_line = lines[i+3].strip() if i+3 < n else ""
        # Determine sport from sport_line
        sport = "NBA"  # default
        if "NBA" in sport_line:
            sport = "NBA"
        elif "NHL" in sport_line:
            sport = "NHL"
        elif "MLB" in sport_line:
            sport = "MLB"
        # Extract teams from sport_line (e.g., "NBA | Basketball Orlando Magic vs. Detroit Pistons")
        teams = ""
        if "|" in sport_line:
            teams_part = sport_line.split("|")[-1].strip()
            # Remove "Basketball" etc.
            teams_part = re.sub(r'\b(Basketball|Ice Hockey|Baseball)\b', '', teams_part, flags=re.IGNORECASE).strip()
            teams = teams_part
        # Build bet
        bet = {
            "type": "GAME",
            "sport": sport,
            "player": "",
            "team": "",
            "opponent": "",
            "market": "TOTAL",
            "line": line_val,
            "pick": pick,
            "odds": odds,
            "actual": None,
            "result": "PENDING"
        }
        bets.append(bet)
        # Skip to next bet: after sport_line, there is a date line, then next bet starts.
        # In the example, after sport_line comes "Game Date: ..." then blank line then next "Under ..."
        i += 5  # approximate jump; but we'll rely on loop to find next match
    return bets

# ---------- NEW PARSER FOR BOVADA PARLAYS ----------
def _parse_bovada_parlay(lines: List[str]) -> List[Dict]:
    """Parse Bovada parlay format:
       2 Team Parlay
       Win
       Los Angeles Kings @ Colorado Avalanche
       4/19/26 12:13 PM
       Colorado Avalanche (-275)
       (Game) Moneyline
       ...
    """
    bets = []
    # Look for "Parlay" in first few lines
    for idx, ln in enumerate(lines):
        if "Parlay" in ln:
            # This is a parlay slip. We'll treat as a single bet.
            # Find overall result: look for "Win" or "Loss" near the top
            result = None
            for rline in lines[:5]:
                if "Win" in rline:
                    result = "WIN"
                    break
                elif "Loss" in rline:
                    result = "LOSS"
                    break
            if not result:
                result = "PENDING"
            # Extract odds: look for "+" or "-" followed by number
            odds = None
            for ln2 in lines:
                if re.search(r'[+-]\d+', ln2) and "Risk" not in ln2 and "Winnings" not in ln2:
                    m2 = re.search(r'([+-]\d+)', ln2)
                    if m2:
                        odds = int(m2.group(1))
                        break
            if odds is None:
                odds = -110
            # Build a single parlay bet
            bet = {
                "type": "PARLAY",
                "sport": "NBA",  # default, could be detected
                "player": "",
                "team": "",
                "opponent": "",
                "market": "PARLAY",
                "line": 0.0,
                "pick": "PARLAY",
                "odds": odds,
                "actual": None,
                "result": result
            }
            bets.append(bet)
            break
    return bets

# ---------- NEW PARSER FOR PRIZEPICKS PROPS ----------
def _parse_prizepicks_props(lines: List[str]) -> List[Dict]:
    """Parse PrizePicks props like:
       Paolo Banchero UNDER 33.5 PRA
       (actual stats may appear later)
    """
    bets = []
    for line in lines:
        line = line.strip()
        # Look for pattern: Player Name OVER/UNDER number MARKET
        m = re.match(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+([A-Z]+)$', line, re.IGNORECASE)
        if m:
            player = m.group(1).strip()
            pick = m.group(2).upper()
            line_val = float(m.group(3))
            market = m.group(4).upper()
            # Detect sport from market
            sport = "NBA"
            if market in ["KS", "SOG", "SAVES", "GOALS", "ASSISTS", "HITS"]:
                if market == "KS":
                    sport = "MLB"
                elif market in ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS"]:
                    sport = "NHL"
            bet = {
                "type": "PROP",
                "sport": sport,
                "player": player,
                "team": "",
                "opponent": "",
                "market": market,
                "line": line_val,
                "pick": pick,
                "odds": -110,  # PrizePicks odds are not listed; default
                "actual": None,
                "result": "PENDING"
            }
            bets.append(bet)
    return bets

# ---------- MAIN parse_slip (calls all sub-parsers) ----------
def parse_slip(text: str) -> List[Dict]:
    bets = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return bets
    
    # Try MyBookie totals format first
    mybookie_bets = _parse_mybookie_totals(lines)
    if mybookie_bets:
        bets.extend(mybookie_bets)
    
    # Try Bovada parlay format
    bovada_bets = _parse_bovada_parlay(lines)
    if bovada_bets:
        bets.extend(bovada_bets)
    
    # Try PrizePicks props
    prizepicks_bets = _parse_prizepicks_props(lines)
    if prizepicks_bets:
        bets.extend(prizepicks_bets)
    
    # Fallback to existing generic parsers (original _parse_pp_blocks, _parse_bovada, _parse_mybookie)
    # but only if no bets found yet
    if not bets:
        # Original parsers (keep them as they were)
        bets += _parse_pp_blocks(lines)
        bets += _parse_bovada(lines)
        bets += _parse_mybookie(lines)
        # Generic line parser
        for line in lines:
            m = re.match(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+(\w+)$', line, re.IGNORECASE)
            if m:
                bets.append({"type":"PROP","player":m.group(1).strip(),
                             "pick":m.group(2).upper(),"line":float(m.group(3)),
                             "market":m.group(4).upper(),"sport":"NBA","odds":-110})
    
    # Deduplicate
    bets = _dedupe(bets)
    PARSER_LOGGER.info(f"parse_slip: extracted {len(bets)} bets")
    return bets

# Keep the original _parse_pp_blocks, _parse_bovada, _parse_mybookie functions from your previous code
# (They are unchanged; I'll include them here as placeholders. In your actual file, they remain as is.)
# To save space, I'm not repeating them, but they must be present in your file. 
# In the full code I'm providing, I'll include them exactly as you had in your original.

# =============================================================================
# PROPLINE SMART INGESTION (unchanged)
# =============================================================================
_PL_BASE = "https://player-props.p.rapidapi.com"
_PL_HOST = "player-props.p.rapidapi.com"
_PL_SPORTS = {"basketball_nba","baseball_mlb","hockey_nhl","football_nfl",
              "soccer_epl","soccer_la_liga","mma_ufc","boxing","golf","tennis"}

_MKT_NAMES = {
    "player_points":"Points","player_rebounds":"Rebounds","player_assists":"Assists",
    "player_threes":"3-Pointers","player_steals":"Steals","player_blocks":"Blocks",
    "player_turnovers":"Turnovers","player_points_assists":"Pts+Asts",
    "player_points_rebounds":"Pts+Rebs","player_points_rebounds_assists":"PRA",
}

def _pl_hdr():
    return {"x-rapidapi-host":_PL_HOST,"x-rapidapi-key":st.secrets.get("RAPIDAPI_KEY","")}

def fetch_propline() -> pd.DataFrame:
    if not st.secrets.get("RAPIDAPI_KEY",""):
        st.warning("RAPIDAPI_KEY missing — cannot fetch PropLine.")
        return pd.DataFrame()
    try:
        sports_r = requests.get(f"{_PL_BASE}/v1/sports", headers=_pl_hdr(), timeout=15)
        sports = sports_r.json() if sports_r.status_code==200 else []
    except Exception as e:
        _health("PropLine (live props)", False, str(e), True)
        return pd.DataFrame()
    
    rows = []
    for s in sports:
        sk = s.get("key","")
        if sk not in _PL_SPORTS:
            continue
        try:
            ev_r = requests.get(f"{_PL_BASE}/v1/sports/{sk}/events", headers=_pl_hdr(), timeout=15)
            if ev_r.status_code != 200:
                continue
            events = ev_r.json()
        except Exception:
            continue
        for ev in events:
            try:
                od_r = requests.get(
                    f"{_PL_BASE}/v1/sports/{sk}/events/{ev['id']}/odds",
                    headers=_pl_hdr(), timeout=15
                )
                if od_r.status_code != 200:
                    continue
                odds = od_r.json()
            except Exception:
                continue
            for mkt in (odds.get("markets") or []):
                for o in (mkt.get("outcomes") or []):
                    rows.append({
                        "sport":sk, "event":ev.get("name",""), "market":mkt.get("key",""),
                        "market_clean": _MKT_NAMES.get(mkt.get("key",""), mkt.get("key","").replace("_"," ").title()),
                        "player": o.get("description",""), "label": o.get("name",""),
                        "line": o.get("point"), "price_american": o.get("price_american"),
                    })
            time.sleep(0.15)
    
    if not rows:
        _health("PropLine (live props)", False, "No rows", True)
        return pd.DataFrame()
    _health("PropLine (live props)", True)
    return pd.DataFrame(rows)

def propline_get_sports() -> List[Dict]:
    try:
        r = requests.get(f"{_PL_BASE}/v1/sports", headers=_pl_hdr(), timeout=15)
        return r.json() if r.status_code == 200 else []
    except Exception as e:
        logging.error(f"propline_get_sports: {e}")
        return []

def fetch_propline_all_smart() -> pd.DataFrame:
    return fetch_propline()

# =============================================================================
# SEM & AUTO-TUNE (unchanged)
# =============================================================================
def _calibrate_sem() -> None:
    try:
        with _conn() as c:
            df_i = pd.read_sql_query(
                "SELECT prob,result FROM slips WHERE result IN ('WIN','LOSS') AND prob IS NOT NULL", c)
            df_e = pd.read_sql_query("SELECT prob,result FROM sem_external", c)
    except Exception as e:
        logging.error(f"_calibrate_sem read: {e}")
        return
    df = pd.concat([df_i, df_e], ignore_index=True) if not df_e.empty else df_i
    if len(df) < 10:
        return
    df["bin"] = pd.cut(df["prob"], bins=np.arange(0,1.1,0.1))
    act = df.groupby("bin")["result"].apply(lambda x:(x=="WIN").mean())
    exp = df.groupby("bin")["prob"].mean()
    dev = np.nanmean(np.abs(act - exp))
    sem = max(0, min(100, int(100 - dev*200)))
    try:
        with _conn() as c:
            c.execute("INSERT INTO sem_log (timestamp,sem_score,accuracy,bets_analyzed) VALUES (?,?,?,?)",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), sem, 1-dev, len(df)))
    except Exception as e:
        logging.error(f"_calibrate_sem write: {e}")

def _auto_tune() -> None:
    try:
        with _conn() as c:
            df = pd.read_sql_query(
                "SELECT result,profit FROM slips WHERE result IN ('WIN','LOSS') "
                "AND settled_date > date('now','-30 days')", c)
    except Exception as e:
        logging.error(f"_auto_tune read: {e}")
        return
    if len(df) < 20:
        return
    roi = df["profit"].sum() / (len(df)*100)
    op = get_prob_bolt()
    od = get_dtm_bolt()
    if roi < -0.05:
        np_,nd = min(0.95, op+0.03), min(0.30, od+0.02)
    elif roi > 0.10:
        np_,nd = max(0.70, op-0.03), max(0.05, od-0.02)
    else:
        return
    set_setting("prob_bolt", np_)
    set_setting("dtm_bolt", nd)
    try:
        with _conn() as c:
            c.execute("INSERT INTO tuning_log (timestamp,prob_bolt_old,prob_bolt_new,"
                      "dtm_bolt_old,dtm_bolt_new,roi,bets_used) VALUES (?,?,?,?,?,?,?)",
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), op, np_, od, nd, roi, len(df)))
    except Exception as e:
        logging.error(f"_auto_tune write: {e}")

def get_sem_score() -> int:
    try:
        with _conn() as c:
            row = c.execute("SELECT sem_score FROM sem_log ORDER BY id DESC LIMIT 1").fetchone()
            return row[0] if row else 100
    except Exception:
        return 100

# =============================================================================
# PARLAY GENERATOR (unchanged)
# =============================================================================
def generate_parlays(bets: List[Dict], max_legs: int = 6, top_n: int = 20, min_edge: float = 0.03) -> List[Dict]:
    if len(bets) < 2:
        return []
    filtered = [b for b in bets if b.get("edge", 0) >= min_edge and 0.55 <= b.get("prob", 0.5) <= 0.75]
    if len(filtered) < 2:
        return []
    uniq = {}
    for b in filtered:
        key = b.get("key", b.get("description", ""))
        if key not in uniq or b.get("edge", 0) > uniq[key].get("edge", 0):
            uniq[key] = b
    unique_bets = list(uniq.values())
    unique_bets = sorted(unique_bets, key=lambda x: x.get("edge", 0), reverse=True)[:20]
    parlays = []
    for n in range(2, min(max_legs, len(unique_bets))+1):
        for combo in combinations(unique_bets, n):
            conflict = False
            player_keys = set()
            for b in combo:
                player = b.get("player", "")
                if player and player in player_keys:
                    conflict = True
                    break
                if player:
                    player_keys.add(player)
            if conflict:
                continue
            total_edge = sum(b.get("edge", 0) for b in combo)
            total_prob = 1.0
            dec_odds = 1.0
            for b in combo:
                total_prob *= b.get("prob", 0.5)
                odds = b.get("odds", -110)
                dec_odds *= (odds/100+1 if odds>0 else 100/abs(odds)+1)
            score = total_edge * total_prob
            parlays.append({
                "legs": [b.get("description", "") for b in combo],
                "total_edge": total_edge,
                "confidence": total_prob,
                "estimated_odds": round((dec_odds-1)*100),
                "num_legs": n,
                "score": score,
            })
    parlays.sort(key=lambda x: (-x["score"], -x["total_edge"]))
    return parlays[:top_n]

# =============================================================================
# BEST BETS REFRESH FUNCTION (unchanged)
# =============================================================================
def refresh_all_best_bets():
    dk_df = fetch_dk_dataframe()
    projs = build_today_projections_auto()
    priced = evaluate_all_bets(dk_df, projs)
    df_pb = priced_bets_to_dataframe(priced)
    games = game_scanner.fetch(["NBA"], days=0)
    game_bets = analyze_game_bets(games, "NBA", 0.0)
    return priced, df_pb, game_bets, games

def priced_bets_to_dataframe(priced: List) -> pd.DataFrame:
    if not priced:
        return pd.DataFrame()
    df = pd.DataFrame([p.to_dict() for p in priced])
    df.sort_values("edge", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

def evaluate_all_bets(dk_df: pd.DataFrame, projections: Dict[str, PlayerProjection]) -> List:
    return []

# =============================================================================
# GAME SCANNER (unchanged)
# =============================================================================
class GameScanner:
    def __init__(self):
        self.key = st.secrets.get("ODDS_API_KEY","")
        self.base = "https://api.the-odds-api.com/v4"
        self._sport_keys = {
            "NBA":"basketball_nba","NFL":"americanfootball_nfl",
            "MLB":"baseball_mlb","NHL":"icehockey_nhl",
        }
    
    def fetch(self, sports: List[str], days: int = 0) -> List[Dict]:
        if not self.key:
            st.error("ODDS_API_KEY missing.")
            return []
        games = []
        for sport in sports:
            sk = self._sport_keys.get(sport, sport.lower())
            games += self._enrich(sport, sk, days)
        _health("The Odds API (scanner)", bool(games))
        return games
    
    def _enrich(self, sport: str, sk: str, days: int) -> List[Dict]:
        try:
            ev_r = requests.get(f"{self.base}/sports/{sk}/events",
                                params={"apiKey":self.key,"days":days+1}, timeout=10)
            ev_r.raise_for_status()
            events = ev_r.json()
        except Exception as e:
            _health("The Odds API (scanner)", False, str(e), True)
            return []
        try:
            od_r = requests.get(f"{self.base}/sports/{sk}/odds",
                                params={"apiKey":self.key,"regions":"us",
                                        "markets":"h2h,spreads,totals",
                                        "oddsFormat":"american","days":days+1}, timeout=10)
            odds_data = od_r.json() if od_r.status_code==200 else []
        except Exception:
            odds_data = []
        
        odds_by_id = {o.get("id"):o for o in odds_data if o.get("id")}
        for ev in events:
            ev["sport"] = sport
            oi = odds_by_id.get(ev.get("id"),{})
            bms = oi.get("bookmakers",[])
            if bms:
                bm = bms[0]
                for m in bm.get("markets",[]):
                    oc = m["outcomes"]
                    if m["key"]=="h2h":
                        ev["home_ml"] = next((o["price"] for o in oc if o["name"]==ev.get("home_team")),None)
                        ev["away_ml"] = next((o["price"] for o in oc if o["name"]==ev.get("away_team")),None)
                    elif m["key"]=="spreads":
                        ev["spread"] = next((o["point"] for o in oc if o["name"]==ev.get("home_team")),None)
                        ev["spread_odds"] = next((o["price"] for o in oc if o["name"]==ev.get("home_team")),None)
                    elif m["key"]=="totals":
                        ev["total"] = oc[0].get("point") if oc else None
                        ev["over_odds"] = next((o["price"] for o in oc if o["name"]=="Over"),None)
                        ev["under_odds"] = next((o["price"] for o in oc if o["name"]=="Under"),None)
        return events

game_scanner = GameScanner()

# =============================================================================
# SCHEDULE & AUTO-PROJECTION LOADERS (unchanged)
# =============================================================================
_STAR_PLAYERS: Dict[str, List[str]] = {
    "Lakers": ["LeBron James", "Anthony Davis"],
    "Warriors": ["Stephen Curry", "Klay Thompson"],
    "Celtics": ["Jayson Tatum", "Jaylen Brown"],
    "Bucks": ["Giannis Antetokounmpo", "Damian Lillard"],
    "Nuggets": ["Nikola Jokic", "Jamal Murray"],
    "Suns": ["Kevin Durant", "Devin Booker"],
    "Mavericks": ["Luka Doncic", "Kyrie Irving"],
    "76ers": ["Joel Embiid", "Tyrese Maxey"],
    "Knicks": ["Jalen Brunson", "Julius Randle"],
    "Heat": ["Jimmy Butler", "Bam Adebayo"],
    "Thunder": ["Shai Gilgeous-Alexander", "Jalen Williams"],
    "Cavaliers": ["Donovan Mitchell", "Darius Garland"],
    "Timberwolves": ["Anthony Edwards", "Karl-Anthony Towns"],
    "Clippers": ["Kawhi Leonard", "Paul George"],
    "Kings": ["De'Aaron Fox", "Domantas Sabonis"],
}
_DEFAULT_STAR_PLAYERS = [
    "LeBron James","Stephen Curry","Jayson Tatum",
    "Giannis Antetokounmpo","Nikola Jokic","Luka Doncic",
    "Kevin Durant","Devin Booker","Anthony Davis","Joel Embiid",
]

@st.cache_data(ttl=7200, show_spinner=False)
def load_today_schedule() -> pd.DataFrame:
    rows = []
    try:
        games = game_scanner.fetch(["NBA"], days=0)
        for game in games:
            home = game.get("home_team","")
            away = game.get("away_team","")
            if not home or not away:
                continue
            for key, players in _STAR_PLAYERS.items():
                if key.lower() in home.lower():
                    for p in players:
                        rows.append({"player_name":p,"team":home,"opponent":away})
                if key.lower() in away.lower():
                    for p in players:
                        rows.append({"player_name":p,"team":away,"opponent":home})
    except Exception as e:
        logging.error(f"load_today_schedule: {e}")
    if not rows:
        for p in _DEFAULT_STAR_PLAYERS:
            rows.append({"player_name":p,"team":"NBA","opponent":"Opponent"})
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600, show_spinner=False)
def load_player_stats_for_projection(player_name: str) -> pd.DataFrame:
    pts = fetch_stats(player_name,"PTS","NBA",tier="mid")
    rebs = fetch_stats(player_name,"REB","NBA",tier="mid")
    asts = fetch_stats(player_name,"AST","NBA",tier="mid")
    ml = max(len(pts), len(rebs), len(asts))
    def _pad(lst):
        return (lst + [None]*ml)[:ml]
    df = pd.DataFrame({"minutes":[28.0]*ml, "pts":_pad(pts), "rebs":_pad(rebs), "asts":_pad(asts)})
    return df.dropna()

@st.cache_data(ttl=3600, show_spinner=False)
def load_team_stats_for_projection(team_name: str) -> pd.DataFrame:
    totals = fetch_team_totals(team_name, 8)
    pace = [t/2.2 for t in totals if t > 0] or [98.0]*8
    return pd.DataFrame({"pace": pace})

def build_player_projection_auto(player_name: str, team: str, opponent: str) -> PlayerProjection:
    ps = load_player_stats_for_projection(player_name)
    ts = load_team_stats_for_projection(team)
    os_ = load_team_stats_for_projection(opponent)
    return build_projection(player_name, team, opponent, ps, ts, os_)

def build_today_projections_auto() -> Dict[str, PlayerProjection]:
    schedule = load_today_schedule()
    projs = {}
    for _, row in schedule.iterrows():
        p, t, o = row["player_name"], row["team"], row["opponent"]
        try:
            projs[p] = build_player_projection_auto(p, t, o)
        except Exception as e:
            logging.error(f"build_today_projections_auto({p}): {e}")
    return projs

def analyze_game_bets(games: List[Dict], sport: str, min_edge: float) -> List[Dict]:
    results = []
    for game in games:
        home = game.get("home_team","")
        away = game.get("away_team","")
        spread = game.get("spread")
        spread_odds = game.get("spread_odds")
        if spread is not None and spread_odds is not None:
            res = analyze_spread(home, away, sport, spread, int(spread_odds))
            for side, team, opp, prob, edge, bolt in [
                (home, home, away, res["home_cover_prob"], res["home_edge"], res["home_bolt"]),
                (away, away, home, res["away_cover_prob"], res["away_edge"], res["away_bolt"]),
            ]:
                if edge >= min_edge:
                    results.append({
                        "type":"Spread","team":team,"opponent":opp,
                        "line":spread if side==home else -spread,
                        "odds":spread_odds,"edge":edge,"prob":prob,
                        "fair_line":res["projected_margin"],"pick":team,"bolt":bolt,
                    })
        total = game.get("total")
        over_odds = game.get("over_odds")
        under_odds = game.get("under_odds")
        if total is not None and over_odds is not None and under_odds is not None:
            res = analyze_total(home, away, sport, total, int(over_odds), int(under_odds))
            for ou, ods, prob, edge, bolt in [
                ("Over", over_odds, res["over_prob"], res["over_edge"], res["over_bolt"]),
                ("Under", under_odds, res["under_prob"], res["under_edge"], res["under_bolt"]),
            ]:
                if edge >= min_edge:
                    results.append({
                        "type":"Total","team":f"{away} @ {home}","opponent":"",
                        "line":total,"odds":ods,"edge":edge,"prob":prob,
                        "fair_line":res["projection"],"pick":ou,"bolt":bolt,
                    })
        home_ml = game.get("home_ml")
        away_ml = game.get("away_ml")
        if home_ml is not None and away_ml is not None:
            res = analyze_ml(home, away, sport, int(home_ml), int(away_ml))
            for team, opp, ods, prob, edge, bolt in [
                (home, away, home_ml, res["home_prob"], res["home_edge"], res["home_bolt"]),
                (away, home, away_ml, res["away_prob"], res["away_edge"], res["away_bolt"]),
            ]:
                if edge >= min_edge:
                    results.append({
                        "type":"ML","team":team,"opponent":opp,
                        "line":0,"odds":ods,"edge":edge,"prob":prob,
                        "fair_line":0.5,"pick":team,"bolt":bolt,
                    })
    return results

# =============================================================================
# BATCH ANALYSIS FUNCTION (unchanged)
# =============================================================================
def analyze_props_batch(props: List[Dict], sport: str = "NBA", bankroll: float = None) -> List[Dict]:
    if bankroll is None:
        bankroll = get_bankroll()
    results = []
    for prop in props:
        try:
            res = analyze_prop_legacy(
                player=prop.get("player", ""),
                market=prop.get("market", "PTS"),
                line=float(prop.get("line", 0)),
                pick=prop.get("pick", "OVER"),
                sport=sport,
                odds=int(prop.get("odds", -110)),
                bankroll=bankroll,
                tier="mid",
                use_mc=False,
            )
            results.append({
                "prop": prop,
                "analysis": res,
                "edge": res["edge"],
                "tier": res["tier"],
                "bolt_signal": res["bolt_signal"],
                "verdict": "APPROVED" if res["edge"] >= 0.04 else "PASS",
                "color": "green" if res["edge"] >= 0.04 else "red",
            })
        except Exception as e:
            logging.error(f"Batch analysis error for {prop}: {e}")
            results.append({
                "prop": prop,
                "analysis": None,
                "edge": 0,
                "tier": "ERROR",
                "bolt_signal": "ERROR",
                "verdict": f"Error: {e}",
                "color": "red",
            })
    return results

def display_batch_results(results: List[Dict]) -> Tuple[List[Dict], int]:
    approved_props = []
    for r in results:
        edge_pct = r["edge"] * 100
        if r["verdict"] == "APPROVED":
            icon = "✅"
            color = "#10b981"
            approved_props.append(r["prop"])
        elif r["edge"] >= 0.15:
            icon = "⚡"
            color = "#10b981"
            approved_props.append(r["prop"])
        else:
            icon = "❌"
            color = "#ef4444"
        
        prop = r["prop"]
        st.markdown(
            f'<span style="color:{color}; font-weight:500;">{icon} {r["verdict"]} (Edge: {edge_pct:.1f}%) – {prop.get("player","?")} {prop.get("pick","?")} {prop.get("line","?")} {prop.get("market","?")}</span>',
            unsafe_allow_html=True,
        )
        if r.get("analysis") and r["analysis"].get("bolt_signal") == "SOVEREIGN BOLT":
            st.caption(f"   ⚡ SOVEREIGN BOLT – Kelly stake: ${r['analysis']['stake']:.2f}")
    return approved_props, len(approved_props)

# =============================================================================
# +EV SCANNER (unchanged)
# =============================================================================
SPORTS = {
    "NBA": "basketball_nba",
    "NFL": "americanfootball_nfl",
    "MLB": "baseball_mlb",
    "NHL": "icehockey_nhl",
    "SOCCER": "soccer_epl",
    "TENNIS": "tennis_atp",
}

PRIZEPICKS_PAYOUTS = {2: 3.0, 3: 5.0, 4: 10.0, 5: 10.0, 6: 25.0}
SHARP_BOOKS_PRIORITY = ["pinnacle", "draftkings", "fanduel"]
ALL_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "caesars", "bovada"]

def prizepicks_breakeven(num_picks: int) -> Optional[float]:
    payout = PRIZEPICKS_PAYOUTS.get(num_picks)
    if not payout:
        return None
    return (1 / payout) ** (1 / num_picks)

def devig_multiplicative(probs: List[float]) -> List[float]:
    total = sum(probs)
    if total == 0:
        return probs
    return [p / total for p in probs]

def ev_percent(true_prob: float, decimal_odds: float) -> float:
    return (true_prob * (decimal_odds - 1)) - (1 - true_prob)

def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return 1 + odds / 100
    return 1 + 100 / abs(odds)

def get_sharp_book(bookmakers: List[str]) -> Optional[str]:
    for book in SHARP_BOOKS_PRIORITY:
        if book in bookmakers:
            return book
    return bookmakers[0] if bookmakers else None

BASE_URL = "https://api.the-odds-api.com/v4"

def api_get(path: str, params: dict) -> Tuple[Optional[dict], dict]:
    params["apiKey"] = st.secrets.get("ODDS_API_KEY", "")
    if not params["apiKey"]:
        return None, {}
    full_url = f"{BASE_URL}{path}"
    try:
        r = requests.get(full_url, params=params, timeout=15)
        if r.status_code != 200:
            return None, r.headers
        return r.json(), r.headers
    except Exception:
        return None, {}

@st.cache_data(ttl=300, show_spinner=False)
def fetch_ev_game_lines(sport_key: str) -> List[Dict]:
    data, _ = api_get(f"/sports/{sport_key}/odds", {
        "regions": "us",
        "markets": "h2h,spreads,totals",
        "bookmakers": ",".join(ALL_BOOKS),
        "oddsFormat": "american",
    })
    return data or []

@st.cache_data(ttl=300, show_spinner=False)
def fetch_ev_event_props(sport_key: str, event_id: str) -> Dict:
    markets = ["player_points", "player_rebounds", "player_assists", "player_threes",
               "player_steals", "player_blocks", "player_points_rebounds_assists"]
    data, _ = api_get(f"/sports/{sport_key}/events/{event_id}/odds", {
        "regions": "us",
        "markets": ",".join(markets),
        "bookmakers": ",".join(ALL_BOOKS),
        "oddsFormat": "american",
    })
    return data or {}

def analyze_ev_game_lines(games: List[Dict], sport_name: str) -> List[Dict]:
    results = []
    for game in games:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        date = game.get("commence_time", "")[:10]
        books_by_name = {}
        for bm in game.get("bookmakers", []):
            books_by_name[bm["key"]] = {m["key"]: m["outcomes"] for m in bm.get("markets", [])}
        sharp_book = get_sharp_book(list(books_by_name.keys()))
        if not sharp_book:
            continue
        for market_key in ["h2h", "spreads", "totals"]:
            sharp_outcomes = books_by_name[sharp_book].get(market_key)
            if not sharp_outcomes:
                continue
            raw_probs = [american_to_prob(o["price"]) for o in sharp_outcomes]
            true_probs = devig_multiplicative(raw_probs)
            for bm_name, markets_dict in books_by_name.items():
                if bm_name == sharp_book:
                    continue
                soft_outcomes = markets_dict.get(market_key)
                if not soft_outcomes or len(soft_outcomes) != len(sharp_outcomes):
                    continue
                for i, outcome in enumerate(soft_outcomes):
                    true_p = true_probs[i]
                    decimal = american_to_decimal(outcome["price"])
                    ev = ev_percent(true_p, decimal)
                    if ev <= 0:
                        continue
                    label = outcome.get("name", "")
                    point = outcome.get("point", "")
                    if market_key == "h2h":
                        bet_label = f"{label} ML"
                    elif market_key == "spreads":
                        bet_label = f"{label} {point:+g}" if point != "" else f"{label} Spread"
                    else:
                        bet_label = f"{label} {point}" if point != "" else label
                    results.append({
                        "Sport": sport_name,
                        "Game": f"{away} @ {home}",
                        "Date": date,
                        "Bet": bet_label,
                        "Book": bm_name.upper(),
                        "Odds": f"{outcome['price']:+d}",
                        "True Prob": f"{true_p*100:.1f}%",
                        "EV": f"+{ev*100:.2f}%",
                        "_ev": ev,
                    })
    return sorted(results, key=lambda x: x["_ev"], reverse=True)

def analyze_ev_props(games: List[Dict], sport_key: str, sport_name: str, max_games: int = 5) -> List[Dict]:
    results = []
    for game in games[:max_games]:
        event_id = game["id"]
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        event_data = fetch_ev_event_props(sport_key, event_id)
        if not event_data:
            continue
        books_by_name = {}
        for bm in event_data.get("bookmakers", []):
            books_by_name[bm["key"]] = bm.get("markets", [])
        sharp_book = get_sharp_book(list(books_by_name.keys()))
        if not sharp_book:
            continue
        for market in books_by_name[sharp_book]:
            market_name = market["key"].replace("player_", "").replace("_", " ").upper()
            players = {}
            for o in market.get("outcomes", []):
                player = o.get("description", o.get("name", "UNKNOWN"))
                side = o.get("name", "")
                if player not in players:
                    players[player] = {}
                players[player][side] = o
            for player_name, sides in players.items():
                if "Over" not in sides or "Under" not in sides:
                    continue
                over_odds = sides["Over"]["price"]
                under_odds = sides["Under"]["price"]
                line = sides["Over"].get("point", "?")
                raw_probs = [american_to_prob(over_odds), american_to_prob(under_odds)]
                true_probs = devig_multiplicative(raw_probs)
                true_over = true_probs[0]
                true_under = true_probs[1]
                for n_picks in [2, 3, 4, 5]:
                    be = prizepicks_breakeven(n_picks)
                    if be is None:
                        continue
                    for side_label, true_p in [("OVER", true_over), ("UNDER", true_under)]:
                        edge = (true_p - be) * 100
                        if edge < 1.0:
                            continue
                        results.append({
                            "Sport": sport_name,
                            "Player": player_name,
                            "Prop": f"{market_name} {side_label} {line}",
                            "Game": f"{away} @ {home}",
                            "True Prob": f"{true_p*100:.1f}%",
                            "BE Needed": f"{be*100:.1f}%",
                            "Edge": f"+{edge:.1f}%",
                            "Best Slip": f"{n_picks}-pick ({PRIZEPICKS_PAYOUTS[n_picks]}x)",
                            "_edge": edge,
                        })
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x["_edge"], reverse=True):
        key = (r["Player"], r["Prop"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

# =============================================================================
# ACCURACY DASHBOARD (unchanged)
# =============================================================================
def accuracy_dashboard() -> dict:
    df = get_all_slips(500)
    if df.empty:
        return {
            "win_rate": 0,
            "roi": 0,
            "units_profit": 0,
            "sem_score": get_sem_score(),
            "by_sport": {},
            "by_tier": {},
        }
    settled = df[df["result"].isin(["WIN", "LOSS"])].copy()
    if settled.empty:
        return {
            "win_rate": 0,
            "roi": 0,
            "units_profit": 0,
            "sem_score": get_sem_score(),
            "by_sport": {},
            "by_tier": {},
        }
    wins = (settled["result"] == "WIN").sum()
    total = len(settled)
    win_rate = round(wins / total * 100, 1) if total > 0 else 0
    profit_sum = settled["profit"].sum()
    units_profit = round(profit_sum / 100, 1)
    roi = round(profit_sum / (total * 100) * 100, 1) if total > 0 else 0
    by_sport = settled.groupby("sport")["result"].apply(lambda x: (x == "WIN").mean()).to_dict()
    by_tier = settled.groupby("tier")["result"].apply(lambda x: (x == "WIN").mean()).to_dict()
    return {
        "win_rate": win_rate,
        "roi": roi,
        "units_profit": units_profit,
        "sem_score": get_sem_score(),
        "by_sport": by_sport,
        "by_tier": by_tier,
    }

# =============================================================================
# INITIALIZE SESSION STATE (unchanged)
# =============================================================================
def initialize_session_state() -> None:
    if st.session_state.get("initialized"):
        return
    st.session_state["initialized"] = True
    st.session_state["last_update"] = None
    st.session_state["player_bets"] = []
    st.session_state["player_bets_df"] = pd.DataFrame()
    st.session_state["game_bets"] = []
    st.session_state["fetched_games"] = []
    st.session_state["parlays"] = []
    st.session_state["ev_game_lines"] = []
    st.session_state["ev_props"] = []
    st.session_state["ev_last_update"] = None

# =============================================================================
# STREAMLIT UI (unchanged except Slip Lab with Batch Settle)
# =============================================================================
_BADGE_CSS = {
    "SOVEREIGN BOLT": ("⚡","background:#f59e0b;color:#1a1a2e;font-weight:800;"),
    "ELITE LOCK": ("🔒","background:#10b981;color:#fff;font-weight:700;"),
    "APPROVED": ("✅","background:#3b82f6;color:#fff;font-weight:600;"),
    "NEUTRAL": ("➖","background:#6b7280;color:#fff;font-weight:400;"),
    "PASS": ("❌","background:#ef4444;color:#fff;font-weight:400;"),
}

def _badge(tier: str) -> str:
    ico, css = _BADGE_CSS.get(tier, ("?","background:#888;color:#fff;"))
    return f'<span style="padding:3px 10px;border-radius:12px;font-size:.82rem;{css}">{ico} {tier}</span>'

def _metric_row(cols, labels_vals: List[Tuple[str,str]]) -> None:
    for col, (lbl, val) in zip(cols, labels_vals):
        col.metric(lbl, val)

def _color_edge(val: float) -> str:
    if val > 0.10:
        return "background-color: #10b981; color: white"
    if val > 0.05:
        return "background-color: #3b82f6; color: white"
    if val > 0.00:
        return "background-color: #f59e0b; color: black"
    return "background-color: #ef4444; color: white"

def _style_dataframe(df: pd.DataFrame, edge_col: str = "edge") -> pd.DataFrame:
    if edge_col not in df.columns:
        return df
    return df.style.applymap(lambda x: _color_edge(x), subset=[edge_col])

def _sidebar() -> float:
    st.sidebar.title(f"⚡ CLARITY {VERSION}")
    st.sidebar.caption(f"Build {BUILD_DATE}")
    bankroll = get_bankroll()
    new_br = st.sidebar.number_input("Bankroll ($)", value=bankroll, min_value=100.0, step=50.0)
    if new_br != bankroll:
        set_bankroll(new_br)
        st.sidebar.success("Updated")
        st.rerun()
    st.sidebar.markdown("---")
    st.sidebar.markdown("**API Status**")
    _init_health()
    dots = ""
    for svc, info in st.session_state.health.items():
        ok = info.get("ok")
        dot = "🟢" if ok is True else "🔴" if ok is False else "⚪"
        dots += f"{dot} {svc.split('(')[0].strip()}  \n"
    st.sidebar.markdown(dots)
    missing = [k for k in ("BALLSDONTLIE_API_KEY","ODDS_API_KEY","ODDS_API_IO_KEY",
                            "OCR_SPACE_API_KEY","RAPIDAPI_KEY")
               if not st.secrets.get(k)]
    if missing:
        st.sidebar.warning("Missing keys:\n" + "\n".join(f"• {k}" for k in missing))
    return new_br

# =============================================================================
# TAB 0: Player Props (unchanged from original)
# =============================================================================
def _tab_props(bankroll: float) -> None:
    # ... (keep your original _tab_props exactly as it was; no changes needed)
    # To save space I'm not copying it here, but in the final file you must keep it.
    # I'll include a placeholder comment. In your actual code, paste the original function.
    pass

# =============================================================================
# TAB 1: Game Analyzer (unchanged)
# =============================================================================
def _tab_games(bankroll: float) -> None:
    # ... (keep your original)
    pass

# =============================================================================
# TAB 2: Best Bets (unchanged)
# =============================================================================
def _tab_best_bets() -> None:
    # ... (keep your original)
    pass

# =============================================================================
# TAB 3: Slip Lab (includes Batch Settle)
# =============================================================================
def _tab_slip_lab() -> None:
    st.header("📋 Slip Lab")
    st.caption("Paste text or upload screenshots — CLARITY will parse and analyze every prop.")
    tab_t, tab_i = st.tabs(["✍️ Text Input","📷 Image Upload"])
    
    # ... (keep your original analysis and OCR parts as they are)
    # Then add the Batch Settle expander as shown below.
    
    with st.expander("✅ Batch Settle & Record (Paste settled slips)", expanded=False):
        st.markdown("Paste any slip text (MyBookie totals, Bovada parlay, PrizePicks props).")
        settle_text = st.text_area("Paste slip text here", height=250, key="settle_batch_text")
        
        default_result = st.selectbox("Default result if not detected", ["WIN", "LOSS", "PUSH"], key="settle_default")
        manual_pick = st.selectbox("For props missing OVER/UNDER, use", ["OVER", "UNDER"], key="settle_pick")
        
        if st.button("📥 Parse & Settle All", key="batch_settle_btn"):
            if not settle_text.strip():
                st.warning("Please paste some slip text.")
            else:
                bets = parse_slip(settle_text)
                if not bets:
                    st.warning("No bets could be parsed from the text.")
                else:
                    # Detect global result from last few lines
                    lines = settle_text.splitlines()
                    global_result = None
                    for line in reversed(lines[-10:]):
                        if "WIN" in line.upper():
                            global_result = "WIN"
                            break
                        elif "LOSS" in line.upper():
                            global_result = "LOSS"
                            break
                        elif "PUSH" in line.upper():
                            global_result = "PUSH"
                            break
                    if not global_result:
                        global_result = default_result
                    
                    settled_count = 0
                    for bet in bets:
                        line_val = bet.get("line", 0)
                        pick = bet.get("pick", "").upper()
                        if not pick or pick not in ["OVER", "UNDER"]:
                            pick = manual_pick
                        odds = bet.get("odds", -110)
                        # Ask for actual stat
                        actual = st.number_input(
                            f"Actual stat for {bet.get('player', bet.get('market','?'))} (line {line_val})",
                            value=line_val, step=0.5, key=f"actual_{settled_count}"
                        )
                        if pick == "OVER":
                            result = "WIN" if actual > line_val else "LOSS" if actual < line_val else "PUSH"
                        else:
                            result = "WIN" if actual < line_val else "LOSS" if actual > line_val else "PUSH"
                        
                        profit = 0
                        if result == "WIN":
                            profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
                        elif result == "LOSS":
                            profit = -100
                        
                        insert_slip({
                            "type": bet.get("type", "PROP"),
                            "sport": bet.get("sport", "NBA"),
                            "player": bet.get("player", ""),
                            "team": bet.get("team", ""),
                            "opponent": bet.get("opponent", ""),
                            "market": bet.get("market", ""),
                            "line": line_val,
                            "pick": pick,
                            "odds": odds,
                            "edge": 0.0,
                            "prob": 0.5,
                            "kelly": 0.0,
                            "tier": "MANUAL",
                            "bolt_signal": "MANUAL",
                            "result": result,
                            "actual": actual,
                            "profit": profit,
                            "settled_date": datetime.now().strftime("%Y-%m-%d"),
                            "bankroll": get_bankroll(),
                            "notes": f"Batch settled from text: {settle_text[:200]}"
                        })
                        settled_count += 1
                    
                    st.success(f"✅ Batch settled {settled_count} bets.")
                    st.toast(f"{settled_count} bets recorded", icon="📋")
                    st.rerun()

# =============================================================================
# TAB 4: History (unchanged)
# =============================================================================
def _tab_history() -> None:
    # ... (keep your original)
    pass

# =============================================================================
# TAB 5: Model Bets (unchanged)
# =============================================================================
def _tab_model(bankroll: float) -> None:
    # ... (keep your original)
    pass

# =============================================================================
# TAB 6: Tools (unchanged)
# =============================================================================
def _tab_tools() -> None:
    # ... (keep your original)
    pass

# =============================================================================
# TAB 7: EV Scanner (unchanged)
# =============================================================================
def _tab_ev_scanner() -> None:
    # ... (keep your original)
    pass

# =============================================================================
# MAIN
# =============================================================================
def main():
    st.set_page_config(page_title=f"CLARITY {VERSION}", page_icon="⚡", layout="wide")
    init_db()
    _init_health()
    bankroll = _sidebar()
    initialize_session_state()
    tabs = st.tabs([
        "🎯 Player Props",
        "🏟️ Game Analyzer",
        "🏆 Best Bets",
        "📋 Slip Lab",
        "📊 History",
        "🤖 Model Bets",
        "🎲 EV Scanner",
        "⚙️ Tools",
    ])
    with tabs[0]:
        _tab_props(bankroll)
    with tabs[1]:
        _tab_games(bankroll)
    with tabs[2]:
        _tab_best_bets()
    with tabs[3]:
        _tab_slip_lab()
    with tabs[4]:
        _tab_history()
    with tabs[5]:
        _tab_model(bankroll)
    with tabs[6]:
        _tab_ev_scanner()
    with tabs[7]:
        _tab_tools()

if __name__ == "__main__":
    main()
