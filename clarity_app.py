# =============================================================================
# CLARITY PRIME 24.1 — MERGED ELITE SPORTS BETTING ENGINE (FIXED)
# =============================================================================
# Merges:
#   • CLARITY 23.1   (tier-aware fallback, Monte Carlo in props, full parsers)
#   • CLARITY PRIME 24.0 (clean UI, auto-scan once, bankroll graph, badges)
#
# New in PRIME 24.1:
#   [M-1]  Tier‑aware historical fallback (elite/mid/bench) from 23.1
#   [M-2]  Monte Carlo toggle integrated directly into Player Props tab
#   [M-3]  Manual PROB_BOLT / DTM_BOLT override in Tools tab
#   [M-4]  Separate parser log (clarity_logs/parser.log) from 23.1
#   [M-5]  Multi‑sport fallback for PGA, NHL, etc. using 23.1's _FALLBACK_TIERS
#   [M-6]  All parsers unified (PrizePicks, Bovada, MyBookie, legacy)
#   [M-7]  Prime's UI + badges + bankroll graph + Slip Lab
#   [FIX]  AttributeError in Best Bets tab when last_update is None
# =============================================================================

import os
import re
import io
import uuid
import time
import json
import base64
import logging
import warnings
import sqlite3
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import norm
import requests
import streamlit as st
from PIL import Image
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
# LOGGING (separate parser log from 23.1)
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
VERSION    = "PRIME 24.1"
BUILD_DATE = "2026-04-21"
DB_PATH    = "clarity_prime.db"

# =============================================================================
# SPORT & STAT CONFIGURATION  (from 23.1)
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

# Threshold defaults — stored in DB at runtime
_DEFAULT_PROB_BOLT  = 0.84
_DEFAULT_DTM_BOLT   = 0.15
KELLY_FRACTION      = 0.25

# =============================================================================
# TIER‑AWARE HISTORICAL FALLBACK (from 23.1, [FIX-10])
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
    """Tier‑aware fallback stats (elite/mid/bench)."""
    key = (sport.upper(), market.upper())
    # Try exact tier, then mid, then elite, then bench, then default
    for t in (tier, "mid", "elite", "bench"):
        d = _FALLBACK_TIERS.get(t, {})
        if key in d:
            return d[key]
    return _FB_DEFAULT

# =============================================================================
# DATABASE (context‑manager, uuid4 IDs)
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

    # Seed threshold defaults only if absent
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
# API HEALTH TRACKER (Prime's compact style)
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
# HTTP SESSION FACTORY (curl_cffi → requests)
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
# DRAFTKINGS LINE FETCHER (from 23.1)
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
# PLAYER PROJECTIONS ENGINE (with role-based minutes from 23.1)
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
# ANALYTICAL DISTRIBUTION ENGINE (pure-Python erf)
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
# MONTE CARLO SIMULATION ENGINE (from 23.1)
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

def simulate_player(proj: PlayerProjection, n: int = 10000, seed: int = None) -> MCResult:
    if seed is not None: np.random.seed(seed)
    rates  = proj.raw_payload.get("rates", {}) if proj.raw_payload else {}
    mins   = proj.minutes
    means  = np.array([
        proj.pts, proj.rebs, proj.asts,
        rates.get("stl", 0.08) * mins,
        rates.get("blk", 0.05) * mins,
        rates.get("to",  0.12) * mins,
    ])
    base_var = means * 0.9
    mv = max(0.1, min(1.5, 1.0 + (36 - mins) / 60))
    uv = max(0.8, min(1.4, 1.0 + (proj.usage - 0.22)))
    pv = max(0.9, min(1.3, proj.pace_adj / 98.0))
    std = np.sqrt(base_var * mv * uv * pv)
    cov = np.outer(std, std) * _NBA_CORR
    raw = np.random.multivariate_normal(means, cov, n)
    raw = np.clip(raw, 0, None)
    keys = ["pts","rebs","asts","stl","blk","to"]
    return MCResult({k: raw[:,i] for i, k in enumerate(keys)})

def mc_price_market(proj: PlayerProjection, market: str, sb_line: float, n: int = 10000) -> Dict:
    mc = simulate_player(proj, n)
    _MAP = {"points":"pts","rebounds":"rebs","assists":"asts",
            "steals":"stl","blocks":"blk","turnovers":"to"}
    key  = _MAP.get(market.lower())
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
    fair   = float(np.percentile(sims, 50))
    p_over = float(np.mean(sims > sb_line))
    p_under = 1 - p_over
    odds   = 1.91
    edge   = (p_over * odds) - (1 - p_over)
    kelly  = max(0.0, (p_over * (odds + 1) - 1) / odds)
    return {"fair_line": fair, "prob_over": p_over, "prob_under": p_under,
            "edge": edge, "kelly": kelly}

# =============================================================================
# ANALYTICAL PRICING ENGINE (PricedBet + evaluate_all_bets)
# =============================================================================
@dataclass
class PricedBet:
    player_or_team:   str
    market_type:      str
    sportsbook_line:  float
    sportsbook_price: int
    fair_line:        float
    prob_over:        float
    prob_under:       float
    edge:             float
    kelly:            float
    distribution:     Any
    raw_payload:      Optional[Dict] = None

    def to_dict(self) -> Dict:
        return {
            "player_or_team":   self.player_or_team,
            "market_type":      self.market_type,
            "sportsbook_line":  self.sportsbook_line,
            "sportsbook_price": self.sportsbook_price,
            "fair_line":        self.fair_line,
            "prob_over":        self.prob_over,
            "prob_under":       self.prob_under,
            "edge":             self.edge,
            "kelly":            self.kelly,
        }

def _american_to_prob_raw(odds: int) -> float:
    o = float(odds)
    return 100/(o+100) if o > 0 else -o/(-o+100)

def _price_stat_market_inner(
    player_name: str, market_type: str,
    sb_line: float, sb_price: int, proj: PlayerProjection,
) -> PricedBet:
    stat_val = getattr(proj, market_type, 0.0)
    dist     = StatDist.from_projection(stat_val, proj.minutes, proj.usage, proj.pace_adj)
    fl       = dist.mean
    po       = dist.prob_over(sb_line)
    pu       = dist.prob_under(sb_line)
    imp      = _american_to_prob_raw(sb_price) if sb_price != 0 else 0.5
    b        = abs(sb_price)/100 if sb_price > 0 else 100/max(abs(sb_price),1)
    edge_val = (po - imp) if sb_line >= fl else (pu - imp)
    k_raw    = (((po if sb_line>=fl else pu)*(b+1)-1)/b) if b > 0 else 0
    k        = max(0.0, min(k_raw, 0.25)) * KELLY_FRACTION
    return PricedBet(
        player_or_team=player_name, market_type=market_type,
        sportsbook_line=sb_line, sportsbook_price=sb_price,
        fair_line=fl, prob_over=po, prob_under=pu,
        edge=edge_val, kelly=k, distribution=dist,
        raw_payload={"projection": proj.to_dict(), "implied_prob": imp},
    )

_MMAP = {"player_points":"pts","player_rebounds":"rebs","player_assists":"asts"}

def price_bet(line_obj: Dict, projections: Dict[str, PlayerProjection]) -> Optional[PricedBet]:
    player = line_obj.get("team_or_player","")
    market = line_obj.get("market_type","")
    line   = float(line_obj.get("line",0))
    price  = int(line_obj.get("price",0))
    if player not in projections or market not in _MMAP: return None
    return _price_stat_market_inner(player, _MMAP[market], line, price, projections[player])

def evaluate_all_bets(dk_df: pd.DataFrame, projections: Dict[str, PlayerProjection]) -> List[PricedBet]:
    if dk_df.empty: return []
    return [pb for pb in (price_bet(r.to_dict(), projections) for _, r in dk_df.iterrows()) if pb is not None]

def priced_bets_to_dataframe(priced: List[PricedBet]) -> pd.DataFrame:
    if not priced: return pd.DataFrame()
    df = pd.DataFrame([p.to_dict() for p in priced])
    df.sort_values("edge", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df

# =============================================================================
# KELLY & TIER CLASSIFICATION (unified)
# =============================================================================
def american_to_prob(odds: int) -> float:
    o = float(odds)
    return 100/(o+100) if o > 0 else -o/(-o+100)

def kelly(prob: float, odds: int) -> float:
    b = abs(odds)/100 if odds > 0 else 100/abs(odds)
    k = (prob*(b+1)-1)/b
    return max(0.0, min(k, 0.25)) * KELLY_FRACTION

def tier_mult(stat: str) -> float:
    t = STAT_CONFIG.get(stat.upper(), {}).get("tier","LOW")
    return 0.85 if t=="HIGH" else 0.93 if t=="MED" else 1.0

def classify_tier(edge: float) -> str:
    if edge >= 0.15: return "SOVEREIGN BOLT"
    if edge >= 0.08: return "ELITE LOCK"
    if edge >= 0.04: return "APPROVED"
    if edge < 0:     return "PASS"
    return "NEUTRAL"

# =============================================================================
# NBA STATS API (BallsDontLie)
# =============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def _nba_stats(player_name: str, market: str, game_date: str = None) -> List[float]:
    stat_map = {"PTS":"pts","REB":"reb","AST":"ast","STL":"stl","BLK":"blk",
                "THREES":"tpm","PRA":"pts","PR":"pts","PA":"pts"}
    stat = stat_map.get(market.upper(), "pts")
    key  = st.secrets.get("BALLSDONTLIE_API_KEY","")
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
        vals  = [float(g[stat]) for g in games if isinstance(g.get(stat),(int,float))]
        _health("BallsDontLie (NBA)", bool(vals), "" if vals else "No stats", not bool(vals))
        return vals
    except Exception as e:
        _health("BallsDontLie (NBA)", False, str(e), True)
        logging.error(f"_nba_stats: {e}")
        return []

# =============================================================================
# NBA TEAM STATS (BallsDontLie)
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
            if team.upper() in k: tid = v; break
    if not tid: return [114.0]*8
    key = st.secrets.get("BALLSDONTLIE_API_KEY","")
    try:
        r = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={tid}&per_page={window}",
            headers={"Authorization": key}, timeout=10,
        )
        if r.status_code != 200: return [114.0]*8
        games = r.json().get("data",[])
        tots  = [g["home_team_score"] if g["home_team"]["id"]==tid else g["visitor_team_score"]
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
            if team.upper() in k: tid = v; break
    if not tid: return [0.0]*8
    key = st.secrets.get("BALLSDONTLIE_API_KEY","")
    try:
        r = requests.get(
            f"https://api.balldontlie.io/v1/games?team_ids[]={tid}&per_page={window}",
            headers={"Authorization": key}, timeout=10,
        )
        if r.status_code != 200: return [0.0]*8
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
# FLASHLIVE & ESPN FALLBACK (multi-sport)
# =============================================================================
_FL_HOST = "flashlive-sports.p.rapidapi.com"
_FL_MAP  = {"NBA":1,"NFL":2,"MLB":3,"NHL":4,"SOCCER":5,"TENNIS":6,
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
        if r.status_code != 200: return []
        plist = r.json().get("DATA",[])
        if not plist: return []
        pid = plist[0].get("id")
        r2  = requests.get(f"https://{_FL_HOST}/v1/players/statistics",
                           headers=h, params={"player_id":pid,"sport_id":sid}, timeout=10)
        if r2.status_code != 200: return []
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
    if not key: return []
    sm  = {"NBA":"basketball","NFL":"football","MLB":"baseball","NHL":"hockey",
           "PGA":"golf","TENNIS":"tennis","SOCCER":"soccer","MMA":"mma"}
    esp = sm.get(sport.upper(), sport.lower())
    h   = {"x-rapidapi-host":"espn-api.p.rapidapi.com","x-rapidapi-key":key}
    try:
        r = requests.get("https://espn-api.p.rapidapi.com/search",
                         headers=h, params={"q":player_name,"sport":esp}, timeout=15)
        if r.status_code != 200: return []
        athletes = r.json().get("athletes",[])
        if not athletes: return []
        pid = athletes[0].get("id")
        r2  = requests.get(f"https://espn-api.p.rapidapi.com/athlete/{pid}/stats",
                           headers=h, timeout=15)
        if r2.status_code != 200: return []
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
    """Unified stats fetcher: primary → FlashLive → ESPN → tier‑aware fallback."""
    if sport.upper() == "NBA":
        vals = _nba_stats(player, market, game_date)
    elif sport.upper() == "PGA":
        # For PGA we don't have a live API in this merge; use fallback
        vals = []
    else:
        vals = _flashlive_stats(player, sport, market)

    if len(vals) < 3:
        vals = _espn_stats(player, sport, market)
    if len(vals) < 3:
        vals = historical_fallback(market, sport, tier)
    return vals

# =============================================================================
# PROP MODEL (WMA + volatility + edge + Kelly)
# =============================================================================
def _wma(values: List[float], w: int = 6) -> float:
    if not values: return 0.0
    arr = np.array(values[-w:])
    wts = np.arange(1, len(arr)+1)
    return float(np.dot(arr, wts) / wts.sum())

def _wse(values: List[float], w: int = 8) -> float:
    if len(values) < 2: return 1.0
    arr = np.array(values[-w:])
    wts = np.arange(1, len(arr)+1)
    mu  = np.dot(arr, wts) / wts.sum()
    var = np.dot(wts, (arr - mu)**2) / wts.sum()
    return float(max(np.sqrt(var / len(arr)), 0.5))

def _vol_buf(values: List[float]) -> float:
    if len(values) < 4: return 1.0
    return float(1.0 + min(np.std(values[-4:]) / 10.0, 0.5))

def analyze_prop(
    player: str, market: str, line: float, pick: str,
    sport: str = "NBA", odds: int = -110, bankroll: float = None, tier: str = "mid",
    use_mc: bool = False, mc_sims: int = 10000,
) -> Dict:
    if bankroll is None: bankroll = get_bankroll()
    stats  = fetch_stats(player, market, sport, tier=tier)
    mu     = _wma(stats)
    sigma  = max(_wse(stats) * _vol_buf(stats), 0.75)

    if use_mc:
        # Build a minimal projection for Monte Carlo
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
        edge = (prob - 0.5) * tier_mult(market)
        kelly_val = kelly(prob, odds)
        fair = mu

    tier_l = classify_tier(edge)
    bolt   = ("SOVEREIGN BOLT" if prob >= get_prob_bolt() and
              abs(mu - line) / max(line, 1e-9) >= get_dtm_bolt()
              else tier_l)
    return {
        "prob": prob, "edge": edge, "mu": mu, "sigma": sigma, "wma": mu,
        "tier": tier_l, "kelly": kelly_val, "stake": bankroll * kelly_val,
        "bolt_signal": bolt, "stats": stats, "fair_line": fair,
    }

# =============================================================================
# GAME ANALYSIS (spread / total / moneyline)
# =============================================================================
def analyze_total(home: str, away: str, sport: str,
                  line: float, over_odds: int, under_odds: int) -> Dict:
    if sport == "NBA":
        ht = fetch_team_totals(home); at = fetch_team_totals(away)
        proj  = _wma(ht) + _wma(at)
        comb  = [h+a for h,a in zip(ht, at)] or ht+at
        sigma = max(_wse(comb) * _vol_buf(comb), 0.75)
    else:
        proj  = SPORT_MODELS.get(sport,{}).get("avg_total", 220.0)
        sigma = proj * 0.08

    op  = 1 - norm.cdf(line, proj, sigma)
    up  = norm.cdf(line, proj, sigma)
    oim = american_to_prob(over_odds)
    uim = american_to_prob(under_odds)
    m   = tier_mult("TOTAL")
    oe  = (op - oim)*m;  ue = (up - uim)*m
    pb  = get_prob_bolt(); db = get_dtm_bolt()
    denom = max(line, 1e-9)
    return {
        "projection": proj, "sigma": sigma,
        "over_prob": op,  "over_edge": oe,  "over_tier":  classify_tier(oe),
        "over_bolt": "SOVEREIGN BOLT" if op>=pb and (proj-line)/denom>=db else classify_tier(oe),
        "under_prob": up, "under_edge": ue, "under_tier": classify_tier(ue),
        "under_bolt":"SOVEREIGN BOLT" if up>=pb and (line-proj)/denom>=db else classify_tier(ue),
    }

def analyze_spread(home: str, away: str, sport: str,
                   spread: float, odds: int) -> Dict:
    if sport == "NBA":
        hm = fetch_team_margins(home); am = fetch_team_margins(away)
        pm = _wma(hm) - _wma(am) + 3.0
        comb = [h-a for h,a in zip(hm, am)] or hm+[-x for x in am]
        sigma = max(_wse(comb)*_vol_buf(comb), 0.75)
    else:
        pm    = SPORT_MODELS.get(sport,{}).get("home_advantage", 3.0)
        sigma = 10.0

    hcp = 1 - norm.cdf(spread, pm, sigma)
    acp = norm.cdf(spread, pm, sigma)
    imp = american_to_prob(odds)
    m   = tier_mult("SPREAD")
    he  = (hcp - imp)*m;  ae = (acp-(1-imp))*m
    pb  = get_prob_bolt(); db = get_dtm_bolt()
    dn  = abs(spread)+1e-9
    return {
        "projected_margin": pm, "sigma": sigma,
        "home_cover_prob": hcp, "home_edge": he, "home_tier": classify_tier(he),
        "home_bolt": "SOVEREIGN BOLT" if hcp>=pb and (pm-spread)/dn>=db else classify_tier(he),
        "away_cover_prob": acp, "away_edge": ae, "away_tier": classify_tier(ae),
        "away_bolt": "SOVEREIGN BOLT" if acp>=pb and (spread-pm)/dn>=db else classify_tier(ae),
    }

def analyze_ml(home: str, away: str, sport: str, home_odds: int, away_odds: int) -> Dict:
    sp  = analyze_spread(home, away, sport, 0.0, home_odds)
    pm  = sp["projected_margin"]; sigma = sp["sigma"]
    hp  = 1/(1+np.exp(-0.13*pm)) if sport=="NBA" else 1-norm.cdf(0, pm, sigma)
    ap  = 1 - hp
    him = american_to_prob(home_odds); aim = american_to_prob(away_odds)
    m   = tier_mult("ML")
    he  = (hp-him)*m;  ae = (ap-aim)*m
    pb  = get_prob_bolt()
    return {
        "home_prob": hp, "home_edge": he, "home_tier": classify_tier(he),
        "home_bolt": "SOVEREIGN BOLT" if hp>=pb and he>=0.15 else classify_tier(he),
        "away_prob": ap, "away_edge": ae, "away_tier": classify_tier(ae),
        "away_bolt": "SOVEREIGN BOLT" if ap>=pb and ae>=0.15 else classify_tier(ae),
    }

# =============================================================================
# GAME SCORES (Odds-API.io)
# =============================================================================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_score(team: str, opp: str, sport: str, date: str) -> Tuple[Optional[float], Optional[float]]:
    sm = {"NBA":"basketball","MLB":"baseball","NHL":"icehockey","NFL":"americanfootball"}
    sk = sm.get(sport)
    if not sk: return None, None
    key = st.secrets.get("ODDS_API_IO_KEY","")
    if not key: return None, None
    try:
        r = requests.get(f"https://api.odds-api.io/v4/sports/{sk}/events",
                         params={"apiKey":key,"date":date}, timeout=10)
        if r.status_code != 200: return None, None
        for ev in (r.json().get("data",[]) or []):
            h = ev.get("home_team",""); a = ev.get("away_team","")
            if {h,a} == {team,opp}:
                hs = ev.get("home_score"); as_ = ev.get("away_score")
                if hs is not None and as_ is not None:
                    return float(hs), float(as_)
    except Exception as e:
        logging.error(f"fetch_score: {e}")
    return None, None

# =============================================================================
# OCR (OCR.space)
# =============================================================================
def ocr_image(image_bytes: bytes, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        enc  = base64.b64encode(image_bytes).decode()
        resp = requests.post("https://api.ocr.space/parse/image", data={
            "base64Image": f"data:image/png;base64,{enc}",
            "apikey": api_key, "language": "eng", "OCREngine": 2,
        }, timeout=30)
        res = resp.json()
        if res.get("IsErroredOnProcessing"):
            return None, res.get("ErrorMessage",["OCR error"])[0]
        return res["ParsedResults"][0]["ParsedText"], None
    except Exception as e:
        return None, str(e)

# =============================================================================
# PARSER UTILITIES (unified, with logging)
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
        if f" {p} " in f" {j} " or j.strip()==p: return p
    return None

def _result(pick: str, actual: float, line: float) -> str:
    if not pick: return "PENDING"
    p = pick.upper()
    if p in ("OVER","MORE"):
        return "WIN" if actual>line else "LOSS" if actual<line else "PUSH"
    if p in ("UNDER","LESS"):
        return "WIN" if actual<line else "LOSS" if actual>line else "PUSH"
    return "PENDING"

def _score_confidence(prop: Dict) -> float:
    s = 1.0
    for k in ("player","market","line","pick"):
        if not prop.get(k): s -= 0.2
    line = prop.get("line")
    if isinstance(line,(int,float)) and (line<=0 or line>200): s -= 0.3
    m = prop.get("market","")
    if not (2 <= len(m) <= 12): s -= 0.2
    return max(0.0, min(1.0, s))

def _auto_sport(market: str) -> Optional[str]:
    m = market.upper()
    if m in {"PTS","REB","AST","PRA","PR","PA","THREES","3PTM"}: return "NBA"
    if m in {"SOG","SAVES","GOALS","ASSISTS","HITS"}: return "NHL"
    if m in {"PASS_YDS","RUSH_YDS","REC_YDS","TD"}: return "NFL"
    if m in {"OUTS","KS","TB","HR"}: return "MLB"
    return None

def _dedupe(props: List[Dict]) -> List[Dict]:
    seen = {}
    for p in props:
        k = (p.get("player","").strip().upper(), p.get("market","").strip().upper(),
             float(p.get("line",0) or 0), p.get("pick","").strip().upper())
        if k not in seen: seen[k] = p
    return list(seen.values())

def _parse_pp_blocks(lines: List[str]) -> List[Dict]:
    bets = []; i = 0; n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line: i+=1; continue
        if i+5 >= n: i+=1; continue
        team_pos = lines[i+1].strip() if i+1<n else ""
        matchup  = lines[i+3].strip() if i+3<n else ""
        line_str = lines[i+4].strip() if i+4<n else ""
        market_r = lines[i+5].strip() if i+5<n else ""
        try:
            line_val = float(line_str)
        except Exception: i+=1; continue
        if line_val<=0 or line_val>200: i+=1; continue
        market = _norm_market(market_r)
        if len(market)<2: i+=1; continue
        window = lines[i:i+10]
        pick   = _detect_pick(window) or "MORE"
        try: team_abbr = team_pos.split("-")[0].strip().upper()
        except Exception: team_abbr = ""
        opp_m = re.search(r'(vs|@)\s+([A-Z]{2,3})\b', matchup)
        opp   = opp_m.group(2).upper() if opp_m else ""
        tag   = ""
        if "goblin" in line.lower(): tag = "GOBLIN"
        elif "demon" in line.lower(): tag = "DEMON"
        player = re.sub(r'(Goblin|Demon)', '', line, flags=re.IGNORECASE).strip()
        bets.append({
            "type":"PROP","player":player,"sport":"NBA","team":team_abbr,
            "opponent":opp,"market":market,"line":line_val,"pick":pick,
            "result":"PENDING","actual":0.0,"odds":-110,"tag":tag,
        })
        i += 8
    return bets

def _parse_bovada(lines: List[str]) -> List[Dict]:
    bets = []; i = 0; n = len(lines)
    while i+10 < n:
        if not re.match(r'\d{1,2}/\d{1,2}/\d{2}', lines[i]): i+=1; continue
        away=lines[i+2]; home=lines[i+3]
        def _sp(l):
            m=re.search(r'([+-]\d+\.?\d*)\s*\(([+-]?\d+)\)',l)
            return (float(m.group(1)),int(m.group(2))) if m else None
        def _tot(l):
            m=re.search(r'([OU])(\d+\.?\d*)\s*\(([+-]?\d+)\)',l,re.IGNORECASE)
            return ("OVER" if m.group(1).upper()=="O" else "UNDER",float(m.group(2)),int(m.group(3))) if m else None
        def _ml(l):
            m=re.match(r'^([+-]\d+)$',l.strip())
            return int(m.group(1)) if m else None
        for side,team,opp,sl in [(away,away,home,lines[i+5]),(home,home,away,lines[i+6])]:
            sp=_sp(sl)
            if sp:
                bets.append({"type":"GAME","sport":"NBA","team":team,"opponent":opp,
                             "market":"SPREAD","line":sp[0],"pick":team,"odds":sp[1],"is_alt":False})
        for ml_line,team,opp in [(lines[i+7],away,home),(lines[i+8],home,away)]:
            ml=_ml(ml_line)
            if ml: bets.append({"type":"GAME","sport":"NBA","team":team,"opponent":opp,
                                "market":"ML","line":0.0,"pick":team,"odds":ml,"is_alt":False})
        for tl in (lines[i+9],lines[i+10]):
            tot=_tot(tl)
            if tot:
                pick,lv,ov=tot
                bets.append({"type":"GAME","sport":"NBA","team":home,"opponent":away,
                             "market":"TOTAL","line":lv,"pick":pick,"odds":ov,"is_alt":False})
        i+=11
    return bets

def _parse_mybookie(lines: List[str]) -> List[Dict]:
    bets=[]; i=0; n=len(lines)
    while i+8<n:
        dl=lines[i+2] if i+2<n else ""
        if not re.search(r'\b[A-Za-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s+[AP]M\b',dl):
            i+=1; continue
        away=lines[i].split("-")[0].strip(); home=lines[i+1].split("-")[0].strip()
        block=lines[i+4:i+13]; j=0
        while j<len(block)-1:
            l=block[j].strip(); nxt=block[j+1].strip()
            if re.match(r'^[+-]\d+(\.\d+)?$',l) and re.match(r'^[+-]\d+$',nxt):
                side="AWAY" if not any(b.get("market")=="SPREAD" and b.get("team")==home for b in bets) else "HOME"
                t,o=( away,home) if side=="AWAY" else (home,away)
                bets.append({"type":"GAME","sport":"MLB","team":t,"opponent":o,
                             "market":"SPREAD","line":float(l),"pick":t,"odds":int(nxt),"is_alt":False})
                j+=2; continue
            m=re.match(r'^([OU])\s+(\d+(\.\d+)?)$',l,re.IGNORECASE)
            if m and re.match(r'^[+-]\d+$',nxt):
                ou="OVER" if m.group(1).upper()=="O" else "UNDER"
                bets.append({"type":"GAME","sport":"MLB","team":home,"opponent":away,
                             "market":"TOTAL","line":float(m.group(2)),"pick":ou,"odds":int(nxt),"is_alt":False})
                j+=2; continue
            if re.match(r'^[+-]\d+$',l):
                side="AWAY" if not any(b.get("market")=="ML" and b.get("team")==home for b in bets) else "HOME"
                t,o=(away,home) if side=="AWAY" else (home,away)
                bets.append({"type":"GAME","sport":"MLB","team":t,"opponent":o,
                             "market":"ML","line":0.0,"pick":t,"odds":int(l),"is_alt":False})
                j+=1; continue
            j+=1
        i+=10
    return bets

def parse_slip(text: str) -> List[Dict]:
    """Master slip parser: PrizePicks blocks, Bovada, MyBookie, legacy."""
    bets = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines:
        return bets
    bets += _parse_pp_blocks(lines)
    bets += _parse_bovada(lines)
    bets += _parse_mybookie(lines)
    # Legacy: simple "Player OVER/UNDER line market"
    for line in lines:
        m = re.match(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+(\w+)$', line, re.IGNORECASE)
        if m:
            bets.append({"type":"PROP","player":m.group(1).strip(),
                         "pick":m.group(2).upper(),"line":float(m.group(3)),
                         "market":m.group(4).upper(),"sport":"NBA","odds":-110})
    PARSER_LOGGER.info(f"parse_slip: extracted {len(bets)} bets")
    return _dedupe(bets)

def parse_prop_line(text: str) -> Optional[Dict]:
    t = _clean(text)
    for pat in [
        r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+([A-Za-z+]+)\s*([+-]?\d+)?$',
        r'^(.+?)\s+([A-Za-z+]+)\s+(OVER|UNDER)\s+([\d\.]+)\s*([+-]?\d+)?$',
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            g = m.groups()
            player,pick,line,market = (g[0].strip(), g[1].upper() if len(g)<5 else g[2].upper(),
                                       float(g[2] if len(g)<5 else g[3]),
                                       _norm_market(g[3] if len(g)<5 else g[1]))
            odds = int(g[4]) if (g[4] if len(g)>4 else None) else -110
            prop = {"player":player,"pick":pick,"line":line,"market":market,"odds":odds}
            prop["confidence"] = _score_confidence(prop)
            prop["sport"] = _auto_sport(market) or ""
            return prop
    blocks = parse_slip(t)
    return blocks[0] if blocks else None

def parse_image_props(image_bytes: bytes) -> List[Dict]:
    key = st.secrets.get("OCR_SPACE_API_KEY","")
    if not key: return []
    text, err = ocr_image(image_bytes, key)
    if err or not text: return []
    return parse_slip(text)

# =============================================================================
# PROPLINE SMART INGESTION
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
        sports   = sports_r.json() if sports_r.status_code==200 else []
    except Exception as e:
        _health("PropLine (live props)", False, str(e), True)
        return pd.DataFrame()

    rows = []
    for s in sports:
        sk = s.get("key","")
        if sk not in _PL_SPORTS: continue
        try:
            ev_r = requests.get(f"{_PL_BASE}/v1/sports/{sk}/events", headers=_pl_hdr(), timeout=15)
            if ev_r.status_code != 200: continue
            events = ev_r.json()
        except Exception: continue
        for ev in events:
            try:
                od_r = requests.get(
                    f"{_PL_BASE}/v1/sports/{sk}/events/{ev['id']}/odds",
                    headers=_pl_hdr(), timeout=15
                )
                if od_r.status_code != 200: continue
                odds = od_r.json()
            except Exception: continue
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
# SEM (Self-Evaluation Metrics)
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
    if len(df) < 10: return
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
    if len(df) < 20: return
    roi = df["profit"].sum() / (len(df)*100)
    op  = get_prob_bolt(); od = get_dtm_bolt()
    if   roi < -0.05: np_,nd = min(0.95, op+0.03), min(0.30, od+0.02)
    elif roi >  0.10: np_,nd = max(0.70, op-0.03), max(0.05, od-0.02)
    else: return
    set_setting("prob_bolt", np_); set_setting("dtm_bolt", nd)
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
    except Exception: return 100

# =============================================================================
# PARLAY GENERATOR (conflict-check extended)
# =============================================================================
def generate_parlays(bets: List[Dict], max_legs: int = 6, top_n: int = 20) -> List[Dict]:
    if len(bets) < 2: return []
    uniq = {}
    for b in bets:
        k = b.get("unique_key", b.get("description",""))
        if k not in uniq or b.get("edge",0) > uniq[k].get("edge",0):
            uniq[k] = b
    bets_u = list(uniq.values())
    parlays = []
    for n in range(2, min(max_legs, len(bets_u))+1):
        for combo in combinations(bets_u, n):
            conflict = False
            game_keys = set(); player_market_keys = set()
            for b in combo:
                gk = f"{b.get('sport','')}_{b.get('team','')}_{b.get('opponent','')}"
                pmk = f"{b.get('player','')}_{b.get('market','')}"
                if gk in game_keys or pmk in player_market_keys:
                    conflict = True; break
                game_keys.add(gk); player_market_keys.add(pmk)
            if conflict: continue
            tot_edge = sum(b.get("edge",0) for b in combo)
            tot_prob  = 1.0; dec_odds = 1.0
            for b in combo:
                tot_prob *= b.get("prob",0.5)
                o = b.get("odds",-110)
                dec_odds *= (o/100+1 if o>0 else 100/abs(o)+1)
            parlays.append({
                "legs":     [b.get("description","") for b in combo],
                "total_edge": tot_edge, "confidence": tot_prob,
                "estimated_odds": round((dec_odds-1)*100),
                "num_legs": n,
            })
    parlays.sort(key=lambda x: (-x["total_edge"], -x["confidence"]))
    return parlays[:top_n]

# =============================================================================
# ACCURACY DASHBOARD
# =============================================================================
def accuracy_dashboard() -> Dict:
    df = get_all_slips(2000)
    df = df[df["result"].isin(["WIN","LOSS"])] if not df.empty else df
    if df.empty:
        return {"total_bets":0,"wins":0,"losses":0,"win_rate":0,
                "roi":0,"units_profit":0,"by_sport":{},"by_tier":{},"sem_score":100}
    wins    = (df["result"]=="WIN").sum(); total = len(df)
    t_profit = df["profit"].sum() if "profit" in df.columns else 0.0
    roi     = t_profit / (total*100) * 100
    by_sport = {}
    for sp in df["sport"].unique():
        sdf = df[df["sport"]==sp]
        by_sport[sp] = {"bets":len(sdf),
                        "win_rate":round((sdf["result"]=="WIN").sum()/len(sdf)*100,1)}
    by_tier = {}
    for _,row in df.iterrows():
        sig = row.get("bolt_signal","PASS")
        t = ("SAFE" if "SOVEREIGN BOLT" in sig or "ELITE LOCK" in sig
             else "BALANCED+" if "APPROVED" in sig
             else "NEUTRAL" if "NEUTRAL" in sig else "PASS")
        by_tier.setdefault(t,{"bets":0,"wins":0})
        by_tier[t]["bets"] += 1
        if row["result"]=="WIN": by_tier[t]["wins"] += 1
    for t in by_tier:
        by_tier[t]["win_rate"] = round(by_tier[t]["wins"]/by_tier[t]["bets"]*100,1)
    return {"total_bets":total,"wins":wins,"losses":total-wins,
            "win_rate":round(wins/total*100,1),"roi":round(roi,1),
            "units_profit":round(t_profit/100,1),"by_sport":by_sport,
            "by_tier":by_tier,"sem_score":get_sem_score()}

# =============================================================================
# GAME SCANNER (The Odds API)
# =============================================================================
class GameScanner:
    def __init__(self):
        self.key  = st.secrets.get("ODDS_API_KEY","")
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
        except Exception: odds_data = []

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
                        ev["spread"]      = next((o["point"] for o in oc if o["name"]==ev.get("home_team")),None)
                        ev["spread_odds"] = next((o["price"] for o in oc if o["name"]==ev.get("home_team")),None)
                    elif m["key"]=="totals":
                        ev["total"]      = oc[0].get("point") if oc else None
                        ev["over_odds"]  = next((o["price"] for o in oc if o["name"]=="Over"),None)
                        ev["under_odds"] = next((o["price"] for o in oc if o["name"]=="Under"),None)
        return events

game_scanner = GameScanner()

# =============================================================================
# SCHEDULE & AUTO-PROJECTION LOADERS
# =============================================================================
_STAR_PLAYERS: Dict[str, List[str]] = {
    "Lakers":    ["LeBron James",          "Anthony Davis"],
    "Warriors":  ["Stephen Curry",         "Klay Thompson"],
    "Celtics":   ["Jayson Tatum",          "Jaylen Brown"],
    "Bucks":     ["Giannis Antetokounmpo", "Damian Lillard"],
    "Nuggets":   ["Nikola Jokic",          "Jamal Murray"],
    "Suns":      ["Kevin Durant",          "Devin Booker"],
    "Mavericks": ["Luka Doncic",           "Kyrie Irving"],
    "76ers":     ["Joel Embiid",           "Tyrese Maxey"],
    "Knicks":    ["Jalen Brunson",         "Julius Randle"],
    "Heat":      ["Jimmy Butler",          "Bam Adebayo"],
    "Thunder":   ["Shai Gilgeous-Alexander","Jalen Williams"],
    "Cavaliers": ["Donovan Mitchell",      "Darius Garland"],
    "Timberwolves": ["Anthony Edwards",   "Karl-Anthony Towns"],
    "Clippers":  ["Kawhi Leonard",         "Paul George"],
    "Kings":     ["De'Aaron Fox",          "Domantas Sabonis"],
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
            home = game.get("home_team",""); away = game.get("away_team","")
            if not home or not away: continue
            for key, players in _STAR_PLAYERS.items():
                if key.lower() in home.lower():
                    for p in players: rows.append({"player_name":p,"team":home,"opponent":away})
                if key.lower() in away.lower():
                    for p in players: rows.append({"player_name":p,"team":away,"opponent":home})
    except Exception as e:
        logging.error(f"load_today_schedule: {e}")
    if not rows:
        for p in _DEFAULT_STAR_PLAYERS:
            rows.append({"player_name":p,"team":"NBA","opponent":"Opponent"})
    return pd.DataFrame(rows)

@st.cache_data(ttl=3600, show_spinner=False)
def load_player_stats_for_projection(player_name: str) -> pd.DataFrame:
    pts  = fetch_stats(player_name,"PTS","NBA",tier="mid")
    rebs = fetch_stats(player_name,"REB","NBA",tier="mid")
    asts = fetch_stats(player_name,"AST","NBA",tier="mid")
    ml   = max(len(pts), len(rebs), len(asts))
    def _pad(lst): return (lst + [None]*ml)[:ml]
    df = pd.DataFrame({"minutes":[28.0]*ml, "pts":_pad(pts), "rebs":_pad(rebs), "asts":_pad(asts)})
    return df.dropna()

@st.cache_data(ttl=3600, show_spinner=False)
def load_team_stats_for_projection(team_name: str) -> pd.DataFrame:
    totals = fetch_team_totals(team_name, 8)
    pace   = [t/2.2 for t in totals if t > 0] or [98.0]*8
    return pd.DataFrame({"pace": pace})

def build_player_projection_auto(player_name: str, team: str, opponent: str) -> PlayerProjection:
    ps   = load_player_stats_for_projection(player_name)
    ts   = load_team_stats_for_projection(team)
    os_  = load_team_stats_for_projection(opponent)
    return build_projection(player_name, team, opponent, ps, ts, os_)

def build_today_projections_auto() -> Dict[str, PlayerProjection]:
    schedule = load_today_schedule()
    projs    = {}
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
        home = game.get("home_team",""); away = game.get("away_team","")
        # Spread
        spread      = game.get("spread")
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
        # Total
        total      = game.get("total")
        over_odds  = game.get("over_odds")
        under_odds = game.get("under_odds")
        if total is not None and over_odds is not None and under_odds is not None:
            res = analyze_total(home, away, sport, total, int(over_odds), int(under_odds))
            for ou, ods, prob, edge, bolt in [
                ("Over",  over_odds,  res["over_prob"],  res["over_edge"],  res["over_bolt"]),
                ("Under", under_odds, res["under_prob"], res["under_edge"], res["under_bolt"]),
            ]:
                if edge >= min_edge:
                    results.append({
                        "type":"Total","team":f"{away} @ {home}","opponent":"",
                        "line":total,"odds":ods,"edge":edge,"prob":prob,
                        "fair_line":res["projection"],"pick":ou,"bolt":bolt,
                    })
        # Moneyline
        home_ml = game.get("home_ml"); away_ml = game.get("away_ml")
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
# AUTO-INIT BEST BETS (runs once on app load)
# =============================================================================
def initialize_best_bets() -> None:
    if st.session_state.get("best_bets_initialized"):
        return
    # Initialize last_update to None
    st.session_state["last_update"] = None
    with st.spinner("⚡ CLARITY is scanning today's best bets…"):
        try:
            dk_df = fetch_dk_dataframe()
            projs = build_today_projections_auto()
            priced = evaluate_all_bets(dk_df, projs)
            st.session_state["player_bets"]    = priced
            st.session_state["player_bets_df"] = priced_bets_to_dataframe(priced)
            games     = game_scanner.fetch(["NBA"], days=0)
            game_bets = analyze_game_bets(games, "NBA", 0.0)
            st.session_state["game_bets"]      = game_bets
            st.session_state["fetched_games"]  = games
            st.session_state["best_bets_initialized"] = True
            st.session_state["last_update"] = datetime.now()
        except Exception as e:
            logging.error(f"initialize_best_bets: {e}")
            st.session_state["best_bets_initialized"] = False

# =============================================================================
# STREAMLIT UI — CLARITY PRIME 24.1
# =============================================================================
_BADGE_CSS = {
    "SOVEREIGN BOLT": ("⚡","background:#f59e0b;color:#1a1a2e;font-weight:800;"),
    "ELITE LOCK":     ("🔒","background:#10b981;color:#fff;font-weight:700;"),
    "APPROVED":       ("✅","background:#3b82f6;color:#fff;font-weight:600;"),
    "NEUTRAL":        ("➖","background:#6b7280;color:#fff;font-weight:400;"),
    "PASS":           ("❌","background:#ef4444;color:#fff;font-weight:400;"),
}

def _badge(tier: str) -> str:
    ico, css = _BADGE_CSS.get(tier, ("?","background:#888;color:#fff;"))
    return f'<span style="padding:3px 10px;border-radius:12px;font-size:.82rem;{css}">{ico} {tier}</span>'

def _metric_row(cols, labels_vals: List[Tuple[str,str]]) -> None:
    for col, (lbl, val) in zip(cols, labels_vals):
        col.metric(lbl, val)

def _sidebar() -> float:
    st.sidebar.title(f"⚡ CLARITY {VERSION}")
    st.sidebar.caption(f"Build {BUILD_DATE}")
    bankroll = get_bankroll()
    new_br   = st.sidebar.number_input("Bankroll ($)", value=bankroll, min_value=100.0, step=50.0)
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

def _tab_props(bankroll: float) -> None:
    st.header("🎯 Player Props")
    for k,v in [("p_sport","NBA"),("p_player","LeBron James"),("p_market","PTS"),
                ("p_line",25.5),("p_pick","OVER"),("p_odds",-110),("p_tier","mid")]:
        if k not in st.session_state: st.session_state[k] = v

    sport  = st.selectbox("Sport", list(SPORT_MODELS.keys()), index=0, key="p_sport")
    player = st.text_input("Player", value=st.session_state.p_player)
    if player != st.session_state.p_player: st.session_state.p_player = player
    mkts   = SPORT_CATEGORIES.get(sport,["PTS"])
    midx   = mkts.index(st.session_state.p_market) if st.session_state.p_market in mkts else 0
    market = st.selectbox("Market", mkts, index=midx)
    if market != st.session_state.p_market: st.session_state.p_market = market
    line   = st.number_input("Line", value=st.session_state.p_line, step=0.5)
    if line != st.session_state.p_line: st.session_state.p_line = line
    c1,c2,c3 = st.columns(3)
    pick   = c1.radio("Pick", ["OVER","UNDER"], horizontal=True,
                      index=0 if st.session_state.p_pick=="OVER" else 1)
    if pick != st.session_state.p_pick: st.session_state.p_pick = pick
    odds   = c2.number_input("Odds", value=st.session_state.p_odds)
    if odds != st.session_state.p_odds: st.session_state.p_odds = odds
    tier   = c3.selectbox("Player Tier", ["elite","mid","bench"], index=1)
    use_mc = st.checkbox("Use Monte Carlo (10,000 sims)", value=False)

    if st.button("🚀 Analyze Prop", type="primary"):
        with st.spinner("Running model..."):
            res = analyze_prop(player, market, line, pick, sport, int(odds), bankroll, tier, use_mc)
        c1,c2,c3,c4 = st.columns(4)
        _metric_row([c1,c2,c3,c4],[
            ("Win Prob", f"{res['prob']:.1%}"),
            ("Edge",     f"{res['edge']:+.1%}"),
            ("Kelly ($)",f"${res['stake']:.2f}"),
            ("Fair Line",f"{res['fair_line']:.1f}"),
        ])
        st.markdown(_badge(res["bolt_signal"]), unsafe_allow_html=True)
        if res["bolt_signal"] in ("SOVEREIGN BOLT","ELITE LOCK","APPROVED"):
            st.success(f"{res['bolt_signal']}  —  {pick} {line} {market}  @  {odds}")
        else:
            st.error("PASS — Insufficient edge for this bet.")
        st.line_chart(pd.DataFrame({"Game":range(1,len(res["stats"])+1),
                                    market: res["stats"]}).set_index("Game"))
        if st.button("➕ Add to Slip Tracker"):
            insert_slip({
                "type":"PROP","sport":sport,"player":player,"team":"","opponent":"",
                "market":market,"line":line,"pick":pick,"odds":int(odds),
                "edge":res["edge"],"prob":res["prob"],"kelly":res["kelly"],
                "tier":res["tier"],"bolt_signal":res["bolt_signal"],"bankroll":bankroll,
            })
            st.success("Added!"); st.toast("Slip logged", icon="➕")

    st.markdown("---")
    st.subheader("📡 Live Props (PropLine)")
    if st.button("Fetch Live Props"):
        with st.spinner("Fetching from PropLine..."):
            df = fetch_propline()
        if df.empty:
            st.warning("No live props returned. Check RAPIDAPI_KEY.")
        else:
            st.success(f"{len(df)} outcomes loaded.")
            sport_filt = st.multiselect("Filter sport", df["sport"].unique().tolist() if "sport" in df.columns else [])
            show_df = df[df["sport"].isin(sport_filt)] if sport_filt else df
            st.dataframe(show_df, use_container_width=True)

def _tab_games(bankroll: float) -> None:
    st.header("🏟️ Game Analyzer — Spreads, Totals & Moneylines")
    st.caption("Powered by The Odds API • Supports NBA, MLB, NHL, NFL and more")
    sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), key="ga_sport")
    if st.button("📡 Fetch Today's Games", type="primary"):
        with st.spinner(f"Fetching {sport} games…"):
            games = game_scanner.fetch([sport], days=0)
            if games:
                st.session_state["fetched_games"] = games
                st.success(f"Found {len(games)} games")
            else:
                st.warning(f"No games found for {sport}. Check ODDS_API_KEY.")
    games = st.session_state.get("fetched_games", [])
    if not games:
        st.info("Click 'Fetch Today's Games' to load matchups.")
        return
    labels = [f"{g.get('away_team','?')} @ {g.get('home_team','?')}  ({g.get('commence_time','')[:10]})"
              for g in games]
    idx = st.selectbox("Select Game", range(len(labels)), format_func=lambda i: labels[i])
    g   = games[idx]
    home, away = g.get("home_team",""), g.get("away_team","")
    st.subheader(f"{away} @ {home}")
    c1, c2, c3 = st.columns(3)
    spread = g.get("spread"); spread_odds = g.get("spread_odds")
    home_ml = g.get("home_ml"); away_ml = g.get("away_ml")
    total = g.get("total"); over_odds = g.get("over_odds"); under_odds = g.get("under_odds")
    with c1:
        st.markdown("**Spread**")
        st.write(f"Line: {spread:+.1f}" if spread is not None else "—")
        st.write(f"Odds: {spread_odds}" if spread_odds is not None else "")
    with c2:
        st.markdown("**Moneyline**")
        st.write(f"{home}: {home_ml}" if home_ml is not None else "—")
        st.write(f"{away}: {away_ml}" if away_ml is not None else "")
    with c3:
        st.markdown("**Total**")
        st.write(f"O/U: {total}" if total is not None else "—")
        st.write(f"Over {over_odds} / Under {under_odds}" if over_odds else "")
    st.divider()
    b1, b2, b3 = st.columns(3)
    if b1.button("🔍 Analyze Spread", use_container_width=True):
        if spread is not None and spread_odds is not None:
            with st.spinner("Analyzing…"):
                res = analyze_spread(home, away, sport, spread, int(spread_odds))
            st.subheader("Spread")
            c1, c2 = st.columns(2)
            c1.metric(f"{home} Cover", f"{res['home_cover_prob']:.1%}")
            c1.metric("Edge", f"{res['home_edge']:+.1%}")
            c1.markdown(_badge(res["home_bolt"]), unsafe_allow_html=True)
            c2.metric(f"{away} Cover", f"{res['away_cover_prob']:.1%}")
            c2.metric("Edge", f"{res['away_edge']:+.1%}")
            c2.markdown(_badge(res["away_bolt"]), unsafe_allow_html=True)
            st.caption(f"Proj margin: {res['projected_margin']:+.1f}  σ={res['sigma']:.1f}")
            if res["home_bolt"] == "SOVEREIGN BOLT":
                st.success(f"⚡ SOVEREIGN BOLT — {home} {spread:+.1f}")
            if res["away_bolt"] == "SOVEREIGN BOLT":
                st.success(f"⚡ SOVEREIGN BOLT — {away} {-spread:+.1f}")
        else:
            st.error("No spread data for this game.")
    if b2.button("🔍 Analyze Total", use_container_width=True):
        if total is not None and over_odds is not None and under_odds is not None:
            with st.spinner("Analyzing…"):
                res = analyze_total(home, away, sport, total, int(over_odds), int(under_odds))
            st.subheader("Total")
            c1, c2 = st.columns(2)
            c1.metric("Over Prob",   f"{res['over_prob']:.1%}")
            c1.metric("Over Edge",   f"{res['over_edge']:+.1%}")
            c1.markdown(_badge(res["over_bolt"]), unsafe_allow_html=True)
            c2.metric("Under Prob",  f"{res['under_prob']:.1%}")
            c2.metric("Under Edge",  f"{res['under_edge']:+.1%}")
            c2.markdown(_badge(res["under_bolt"]), unsafe_allow_html=True)
            st.caption(f"Projected total: {res['projection']:.1f}")
            if res["over_bolt"]  == "SOVEREIGN BOLT": st.success(f"⚡ SOVEREIGN BOLT — OVER {total}")
            if res["under_bolt"] == "SOVEREIGN BOLT": st.success(f"⚡ SOVEREIGN BOLT — UNDER {total}")
        else:
            st.error("No total data for this game.")
    if b3.button("🔍 Analyze Moneyline", use_container_width=True):
        if home_ml is not None and away_ml is not None:
            with st.spinner("Analyzing…"):
                res = analyze_ml(home, away, sport, int(home_ml), int(away_ml))
            st.subheader("Moneyline")
            c1, c2 = st.columns(2)
            c1.metric(f"{home} Win",  f"{res['home_prob']:.1%}")
            c1.metric("Edge",         f"{res['home_edge']:+.1%}")
            c1.markdown(_badge(res["home_bolt"]), unsafe_allow_html=True)
            c2.metric(f"{away} Win",  f"{res['away_prob']:.1%}")
            c2.metric("Edge",         f"{res['away_edge']:+.1%}")
            c2.markdown(_badge(res["away_bolt"]), unsafe_allow_html=True)
            if res["home_bolt"] == "SOVEREIGN BOLT": st.success(f"⚡ SOVEREIGN BOLT — {home} ML")
            if res["away_bolt"] == "SOVEREIGN BOLT": st.success(f"⚡ SOVEREIGN BOLT — {away} ML")
        else:
            st.error("No moneyline data for this game.")

def _tab_best_bets() -> None:
    st.header("🏆 Best Bets — Automated Recommendations")
    st.caption("Top player props and game bets ranked by CLARITY edge model")
    with st.expander("⚙️ Filter Settings", expanded=False):
        fc1, fc2 = st.columns(2)
        min_edge      = fc1.slider("Min Edge (%)", 0.0, 15.0, 2.0, 0.5) / 100.0
        max_props     = fc1.slider("Max Player Props", 3, 15, 6)
        max_games     = fc2.slider("Max Game Bets",    3, 15, 6)
        use_kelly     = fc2.checkbox("Kelly Sizing", value=True)
        kelly_cap_pct = fc2.slider("Kelly Cap (% bankroll)", 1, 25, 10) / 100.0 if use_kelly else 1.0

    if st.button("🔄 Refresh All Data", type="primary"):
        with st.spinner("Refreshing lines and projections…"):
            try:
                dk_df  = fetch_dk_dataframe()
                projs  = build_today_projections_auto()
                priced = evaluate_all_bets(dk_df, projs)
                st.session_state["player_bets"]    = priced
                st.session_state["player_bets_df"] = priced_bets_to_dataframe(priced)
                games  = game_scanner.fetch(["NBA"], days=0)
                st.session_state["game_bets"]      = analyze_game_bets(games, "NBA", 0.0)
                st.session_state["last_update"]    = datetime.now()
                st.success("Data refreshed ✅")
                st.rerun()
            except Exception as e:
                st.error(f"Refresh error: {e}")

    last_update = st.session_state.get("last_update")
    if last_update and isinstance(last_update, datetime):
        st.caption(f"Last scan: {last_update.strftime('%H:%M:%S')}")
    else:
        st.caption("No data loaded yet. Click 'Refresh All Data'.")

    df_pb = st.session_state.get("player_bets_df", pd.DataFrame())
    if not df_pb.empty:
        filtered = df_pb[df_pb["edge"] >= min_edge].head(max_props).copy()
        if not filtered.empty:
            st.subheader(f"🏀 Top {len(filtered)} Player Props")
            def _tier_badge(e):
                if e >= 0.15: return "⚡ SOVEREIGN BOLT"
                if e >= 0.08: return "🔒 ELITE LOCK"
                if e >= 0.04: return "✅ APPROVED"
                return "ℹ️ NEUTRAL"
            filtered["Tier"] = filtered["edge"].apply(_tier_badge)
            br = get_bankroll()
            filtered["Stake $"] = filtered.apply(
                lambda r: f"${min(r['kelly']*br, br*kelly_cap_pct):.0f}" if use_kelly else "$100", axis=1
            )
            disp_cols = ["player_or_team","market_type","sportsbook_line","fair_line",
                         "prob_over","edge","kelly","Stake $","Tier"]
            st.dataframe(filtered[disp_cols], use_container_width=True)
            sel = st.multiselect(
                "Select props to add to Slip Tracker", filtered.index,
                format_func=lambda i: (
                    f"{filtered.loc[i,'player_or_team']}  "
                    f"{filtered.loc[i,'market_type']}  "
                    f"O/U {filtered.loc[i,'sportsbook_line']}  "
                    f"(edge {filtered.loc[i,'edge']:.1%})"
                )
            )
            if st.button("➕ Add Selected Props to Slip"):
                for i in sel:
                    row = filtered.loc[i]
                    pick = "OVER" if row["prob_over"] >= 0.5 else "UNDER"
                    prob = row["prob_over"] if pick == "OVER" else row["prob_under"]
                    insert_slip({
                        "type":"PROP","sport":"NBA",
                        "player":row["player_or_team"],"team":"","opponent":"",
                        "market":row["market_type"],"line":row["sportsbook_line"],
                        "pick":pick,"odds":int(row.get("sportsbook_price",-110)),
                        "edge":row["edge"],"prob":prob,"kelly":row["kelly"],
                        "tier":row["Tier"],"bolt_signal":row["Tier"],"bankroll":get_bankroll(),
                    })
                st.success(f"Added {len(sel)} props."); st.rerun()
        else:
            st.info(f"No player props above {min_edge*100:.1f}% edge threshold.")
    else:
        st.info("No player prop data yet — click Refresh or wait for auto-scan.")
    st.divider()
    game_bets = st.session_state.get("game_bets", [])
    filtered_g = sorted([b for b in game_bets if b["edge"] >= min_edge],
                        key=lambda x: x["edge"], reverse=True)[:max_games]
    if filtered_g:
        st.subheader(f"🏟️ Top {len(filtered_g)} Game Bets (Spread / Total / ML)")
        df_g = pd.DataFrame(filtered_g)
        br   = get_bankroll()
        df_g["Stake $"] = df_g["edge"].apply(
            lambda e: f"${min(e*0.25*br, br*kelly_cap_pct):.0f}" if use_kelly else "$100"
        )
        st.dataframe(df_g[["type","team","opponent","line","odds","edge","prob",
                            "fair_line","pick","Stake $"]], use_container_width=True)
        sel_g = st.multiselect(
            "Select game bets to add", df_g.index,
            format_func=lambda i: (
                f"{df_g.loc[i,'team']}  {df_g.loc[i,'type']}  "
                f"{df_g.loc[i,'pick']}  {df_g.loc[i,'line']}  "
                f"(edge {df_g.loc[i,'edge']:.1%})"
            )
        )
        if st.button("➕ Add Selected Game Bets"):
            for i in sel_g:
                row = df_g.loc[i]
                insert_slip({
                    "type":"GAME","sport":"NBA",
                    "team":row["team"],"opponent":row["opponent"],
                    "market":row["type"],"line":row["line"],"pick":row["pick"],
                    "odds":int(row["odds"]),"edge":row["edge"],"prob":row["prob"],
                    "kelly":row["edge"]*0.25,"tier":"BEST BET",
                    "bolt_signal":row.get("bolt",""),"bankroll":get_bankroll(),
                })
            st.success(f"Added {len(sel_g)} game bets."); st.rerun()
    else:
        st.info(f"No game bets above {min_edge*100:.1f}% edge threshold.")
    st.divider()
    st.subheader("🎲 Auto Parlay Generator")
    max_legs_par = st.slider("Max legs", 2, 6, 4, key="par_legs")
    if st.button("⚡ Generate Parlays from Top Props"):
        raw_bets = st.session_state.get("player_bets", [])
        bet_dicts = []
        for b in raw_bets:
            if b.edge >= 0.02:
                pick    = "OVER" if b.prob_over >= 0.5 else "UNDER"
                bet_dicts.append({
                    "description": f"{b.player_or_team}  {b.market_type}  {pick}  {b.sportsbook_line}",
                    "edge":    b.edge,
                    "prob":    b.prob_over if pick=="OVER" else b.prob_under,
                    "odds":    b.sportsbook_price,
                    "sport":   "NBA",
                    "player":  b.player_or_team,
                    "market":  b.market_type,
                    "team":    b.player_or_team,
                    "opponent":"",
                    "unique_key": f"{b.player_or_team}_{b.market_type}_{b.sportsbook_line}",
                })
        parlays = generate_parlays(bet_dicts, max_legs=max_legs_par, top_n=5)
        if parlays:
            st.session_state["parlays"] = parlays
            st.success(f"Generated {len(parlays)} parlays.")
        else:
            st.warning("Not enough qualifying bets for parlays. Lower edge threshold or refresh data.")
    for i, p in enumerate(st.session_state.get("parlays",[])):
        with st.expander(
            f"Parlay #{i+1} — {p['num_legs']} legs | "
            f"Edge: {p['total_edge']:.2%} | "
            f"Confidence: {p['confidence']:.1%} | "
            f"Est. odds: +{p['estimated_odds']}"
        ):
            for leg in p["legs"]: st.markdown(f"• {leg}")
            if st.button(f"➕ Add Parlay #{i+1} to Slip", key=f"padd_{i}"):
                insert_slip({
                    "type":"PARLAY","sport":"NBA",
                    "edge":p["total_edge"],"prob":p["confidence"],
                    "odds":p["estimated_odds"],"tier":"PARLAY",
                    "bolt_signal":"PARLAY","bankroll":get_bankroll(),
                    "notes":"\n".join(p["legs"]),
                })
                st.success(f"Parlay #{i+1} logged."); st.rerun()

def _tab_slip_lab() -> None:
    st.header("📋 Slip Lab")
    st.caption("Paste text or upload screenshots — CLARITY will parse and analyze every prop.")
    tab_t, tab_i = st.tabs(["✍️ Text Input","📷 Image Upload"])
    with tab_t:
        text = st.text_area("Paste slip text", height=250,
                            placeholder="e.g., LeBron James OVER 25.5 PTS  or full PrizePicks block")
        if text:
            with st.spinner("Parsing..."):
                props = parse_slip(text)
            if props:
                st.success(f"{len(props)} props detected.")
                for p in props:
                    c1,c2,c3 = st.columns([3,1,1])
                    c1.write(f"**{p.get('player','')}** — {p.get('market','')} {p.get('pick','')} {p.get('line','')}")
                    c2.write(p.get("sport",""))
                    if c3.button("Analyze", key=f"at_{id(p)}"):
                        res = analyze_prop(p["player"], p["market"], p["line"],
                                           p["pick"], p.get("sport","NBA"))
                        st.markdown(_badge(res["bolt_signal"]), unsafe_allow_html=True)
                        st.write(f"Prob: {res['prob']:.1%}  Edge: {res['edge']:+.2%}  Stake: ${res['stake']:.2f}")
            else:
                st.info("No props detected. Try a different format.")
    with tab_i:
        files = st.file_uploader("Upload screenshots", type=["png","jpg","jpeg"],
                                 accept_multiple_files=True)
        if files:
            for f in files:
                st.write(f"**{f.name}**")
                with st.spinner(f"OCR {f.name}..."):
                    props = parse_image_props(f.getvalue())
                if props:
                    for p in props:
                        st.write(f"  • {p.get('player','')} {p.get('pick','')} "
                                 f"{p.get('line','')} {p.get('market','')}")
                else:
                    st.caption("No props extracted — check OCR_SPACE_API_KEY.")

def _tab_history() -> None:
    st.header("📊 History & Accuracy Metrics")
    df = get_all_slips(500)
    dash = accuracy_dashboard()
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Win Rate",     f"{dash['win_rate']}%")
    c2.metric("ROI",          f"{dash['roi']}%")
    c3.metric("Units Profit", str(dash['units_profit']))
    c4.metric("SEM Score",    str(dash['sem_score']))
    if not df.empty and "profit" in df.columns:
        settled = df[df["result"].isin(["WIN","LOSS"])].copy()
        if not settled.empty:
            settled = settled.sort_values("settled_date")
            settled["cum_profit"] = settled["profit"].cumsum()
            st.subheader("Cumulative P&L Curve")
            st.line_chart(settled[["settled_date","cum_profit"]].set_index("settled_date"))
    st.subheader("By Sport")
    st.json(dash["by_sport"])
    st.subheader("By Tier")
    st.json(dash["by_tier"])
    st.markdown("---")
    st.subheader("All Bets")
    if not df.empty:
        st.dataframe(df, use_container_width=True)
        pending = df[df["result"]=="PENDING"]
        if not pending.empty:
            st.subheader("Settle Pending Bets")
            slip_id  = st.selectbox("Slip ID", pending["id"].tolist())
            sel_row  = pending[pending["id"]==slip_id].iloc[0]
            actual   = st.number_input("Actual Result", value=0.0, step=0.1)
            res_pick = st.radio("Outcome", ["WIN","LOSS","PUSH"], horizontal=True)
            if st.button("Settle Bet"):
                update_slip_result(slip_id, res_pick, actual, int(sel_row.get("odds",-110)))
                st.success("Settled!"); st.rerun()
    else:
        st.info("No bets recorded yet.")

def _tab_model(bankroll: float) -> None:
    st.header("🤖 Model-Priced Bets (DraftKings)")
    use_mc = st.toggle("Monte Carlo mode (10 000 sims/player)", value=False)
    if st.button("Fetch DraftKings Lines", type="primary"):
        with st.spinner("Fetching DK lines..."):
            dk_df = fetch_dk_dataframe()
        if dk_df.empty:
            st.warning("No DraftKings lines fetched."); return
        st.success(f"{len(dk_df)} lines fetched.")
        st.dataframe(dk_df.head(20), use_container_width=True)
        player_cols = dk_df[dk_df["market_type"].str.startswith("player")]
        players = player_cols["team_or_player"].unique().tolist() if not player_cols.empty else []
        if players:
            st.subheader("Priced Bets")
            results = []
            with st.spinner(f"Pricing {len(players)} players..."):
                for _, row in player_cols.iterrows():
                    pname  = row.get("team_or_player","")
                    mtype  = row.get("market_type","")
                    sb_line = float(row.get("line",0))
                    if not pname or not mtype or sb_line<=0: continue
                    proj = PlayerProjection(
                        player_name=pname, team="", opponent="",
                        minutes=28.0, pts=sb_line*1.02, rebs=5.0, asts=4.0,
                        usage=0.22, pace_adj=98.0,
                        raw_payload={"rates":{"stl":0.08,"blk":0.05,"to":0.12}},
                    )
                    mkt_key = mtype.replace("player_","")
                    if use_mc:
                        r = mc_price_market(proj, mkt_key, sb_line)
                    else:
                        dist   = StatDist.from_projection(sb_line*1.02, 28.0, 0.22, 98.0)
                        p_over = dist.prob_over(sb_line)
                        imp    = american_to_prob(int(row.get("price",-110)))
                        edge   = (p_over - imp)
                        k      = kelly(p_over, int(row.get("price",-110)))
                        r = {"fair_line": sb_line*1.02, "prob_over": p_over, "edge": edge, "kelly": k}
                    results.append({
                        "Player":     pname,
                        "Market":     mtype,
                        "Line":       sb_line,
                        "Fair Line":  round(r.get("fair_line",0),2),
                        "P(over)":    round(r.get("prob_over",0),3),
                        "Edge":       round(r.get("edge",0),3),
                        "Kelly":      round(r.get("kelly",0),3),
                        "Tier":       classify_tier(r.get("edge",0)),
                    })
            if results:
                rdf = pd.DataFrame(results).sort_values("Edge", ascending=False)
                st.dataframe(rdf, use_container_width=True)
                good = rdf[rdf["Tier"].isin(["SOVEREIGN BOLT","ELITE LOCK","APPROVED"])]
                if not good.empty:
                    st.success(f"{len(good)} edges found worth watching.")
            else:
                st.info("No priceable bets in the current DK data.")
        else:
            st.info("No player prop lines found in the DK feed. Check DK endpoint or try later.")

def _tab_tools() -> None:
    st.header("⚙️ Tools & Diagnostics")
    st.subheader("🔌 API Health Detail")
    _init_health()
    cols = st.columns(2)
    for i, (svc, info) in enumerate(st.session_state.health.items()):
        ok  = info.get("ok")
        ico = "🟢" if ok else "🔴" if ok is False else "⚪"
        msg = f"{ico} **{svc}**"
        if info.get("fallback"): msg += " (fallback)"
        if info.get("err"):      msg += f"\n   ⚠️ {info['err'][:80]}"
        cols[i%2].markdown(msg)
    st.subheader("🔍 On-Demand Tests")
    c1,c2,c3 = st.columns(3)
    if c1.button("Test NBA API"):
        vals = _nba_stats("LeBron James","PTS")
        st.success(f"NBA OK: {vals[:3]}") if vals else st.error("NBA failed.")
    if c2.button("Test PropLine"):
        sports = propline_get_sports()
        st.success(f"PropLine OK: {len(sports)} sports") if sports else st.error("PropLine failed.")
    if c3.button("Test DraftKings"):
        df = fetch_dk_dataframe()
        st.success(f"DK OK: {len(df)} lines") if not df.empty else st.error("DK fetch failed.")
    st.subheader("📜 Recent Error Log")
    try:
        if os.path.exists("clarity_debug.log"):
            with open("clarity_debug.log") as f:
                errs = [l for l in f.readlines() if "ERROR" in l][-5:]
            if errs:
                for e in errs: st.code(e.strip())
            else:
                st.success("No errors in log.")
        else:
            st.info("Log not found yet.")
    except Exception as e:
        st.warning(f"Could not read log: {e}")
    st.subheader("🧹 Maintenance")
    c1,c2,c3 = st.columns(3)
    if c1.button("Clear Pending Slips"):
        clear_pending_slips(); st.success("Cleared.")
    if c2.button("Force SEM Recalibration"):
        _calibrate_sem(); st.success("SEM recalibrated.")
    if c3.button("Force Threshold Tune"):
        _auto_tune(); st.success(f"Thresholds: PROB={get_prob_bolt():.2f} DTM={get_dtm_bolt():.2f}")
    st.subheader("⚖️ Current Thresholds")
    st.metric("PROB_BOLT", f"{get_prob_bolt():.3f}")
    st.metric("DTM_BOLT",  f"{get_dtm_bolt():.3f}")
    with st.expander("Override thresholds manually"):
        np_ = st.number_input("PROB_BOLT", value=get_prob_bolt(), step=0.01, min_value=0.5, max_value=1.0)
        nd  = st.number_input("DTM_BOLT",  value=get_dtm_bolt(),  step=0.01, min_value=0.0, max_value=0.5)
        if st.button("Apply"):
            set_setting("prob_bolt", np_); set_setting("dtm_bolt", nd)
            st.success("Thresholds updated."); st.rerun()

# =============================================================================
# MAIN
# =============================================================================
def main():
    st.set_page_config(page_title=f"CLARITY {VERSION}", page_icon="⚡", layout="wide")
    init_db()
    _init_health()
    bankroll = _sidebar()
    initialize_best_bets()
    tabs = st.tabs([
        "🎯 Player Props",
        "🏟️ Game Analyzer",
        "🏆 Best Bets",
        "📋 Slip Lab",
        "📊 History",
        "🤖 Model Bets",
        "⚙️ Tools",
    ])
    with tabs[0]: _tab_props(bankroll)
    with tabs[1]: _tab_games(bankroll)
    with tabs[2]: _tab_best_bets()
    with tabs[3]: _tab_slip_lab()
    with tabs[4]: _tab_history()
    with tabs[5]: _tab_model(bankroll)
    with tabs[6]: _tab_tools()

if __name__ == "__main__":
    main()
