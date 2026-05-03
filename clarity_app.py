# ====================================================================================================
# CLARITY SOVEREIGN SUPREME v6.0 – FULL PRODUCTION (with Sportmonks xG & Tennis)
# ====================================================================================================
# All sensors, auto‑tuning, Sportmonks xG (soccer), FlashLive tennis stats,
# B2B, travel, altitude, weather, RLM, news friction, motivation, ABS, series state.
# ====================================================================================================

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
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple, Union
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson
import requests
import streamlit as st
from PIL import Image
from tenacity import retry, stop_after_attempt, wait_exponential

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
os.makedirs("calibration_reports", exist_ok=True)

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
VERSION    = "SOVEREIGN SUPREME v6.0 (Sportmonks xG + Tennis)"
BUILD_DATE = "2026-05-02"
DB_PATH    = "clarity_prime.db"

# =============================================================================
# SPORT & STAT CONFIGURATION
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
    "TENNIS":  ["ACES","DOUBLE_FAULTS","GAMES_WON","TOTAL_GAMES","BREAK_PTS","FIRST_SERVE_PCT"],
    "SOCCER":  ["GOALS","ASSISTS","SHOTS","SHOTS_ON_TARGET","FOULS","CARDS","XG"],
    "MMA":     ["STRIKES","TAKEDOWNS","SUBMISSIONS","KNOCKDOWNS"],
    "F1":      ["POINTS","POSITION","FASTEST_LAP"],
    "CRICKET": ["RUNS","WICKETS","BOUNDARIES","SIXES"],
    "BOXING":  ["PUNCHES","JABS","POWER_PUNCHES","KNOCKDOWNS"],
}

STAT_CONFIG: Dict[str, Dict] = {
    "PTS":      {"tier":"VERY_HIGH", "mult":0.80},
    "REB":      {"tier":"LOW",       "mult":0.97},
    "AST":      {"tier":"LOW",       "mult":0.97},
    "PRA":      {"tier":"HIGH",      "mult":0.85},
    "PR":       {"tier":"MEDIUM",    "mult":0.92},
    "PA":       {"tier":"MEDIUM",    "mult":0.92},
    "SOG":      {"tier":"LOW",       "mult":0.97},
    "SAVES":    {"tier":"LOW",       "mult":0.97},
    "STROKES":  {"tier":"LOW",       "mult":0.97},
    "BIRDIES":  {"tier":"MEDIUM",    "mult":0.92},
    "ACES":     {"tier":"HIGH",      "mult":0.85},
    "DOUBLE_FAULTS": {"tier":"HIGH","mult":0.85},
    "GAMES_WON":{"tier":"LOW",       "mult":0.97},
    "GOALS":    {"tier":"HIGH",      "mult":0.85},
    "ASSISTS":  {"tier":"MEDIUM",    "mult":0.92},
    "STRIKES":  {"tier":"MEDIUM",    "mult":0.92},
    "RUNS":     {"tier":"MEDIUM",    "mult":0.92},
    "TOTAL":    {"tier":"MEDIUM",    "mult":0.92},
    "SPREAD":   {"tier":"MEDIUM",    "mult":0.92},
    "ML":       {"tier":"HIGH",      "mult":0.85},
    "XG":       {"tier":"MEDIUM",    "mult":0.92},
    "FIRST_SERVE_PCT": {"tier":"LOW","mult":0.97},
}

_DEFAULT_PROB_BOLT  = 0.84
_DEFAULT_DTM_BOLT   = 0.15
KELLY_FRACTION      = 0.25

VOLATILITY_TIERS = {
    "VERY_HIGH": 0.80,
    "HIGH": 0.85,
    "MEDIUM": 0.92,
    "LOW": 0.97,
}

ARENA_ELEVATIONS = {
    "Denver": 5280, "Salt Lake City": 4226, "Mexico City": 7382,
    "Phoenix": 1100, "Los Angeles": 285, "Boston": 10, "Miami": 8,
    "Chicago": 581, "Atlanta": 1050, "Dallas": 430, "Houston": 80,
    "Philadelphia": 39, "New York": 33, "Washington": 10, "San Francisco": 52,
    "Portland": 50, "Seattle": 175, "Minneapolis": 830, "Cleveland": 653,
}

# =============================================================================
# DATABASE (extended with new columns)
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
                notes        TEXT DEFAULT '',
                strictness   TEXT DEFAULT '',
                cv           REAL,
                minutes_vol  REAL,
                blowout_prob REAL,
                clv          REAL,
                entry_odds   INTEGER,
                closing_odds INTEGER,
                b2b          INTEGER,
                travel_zones INTEGER,
                altitude_ft  INTEGER,
                wind_mph     REAL,
                temp_f       REAL,
                precip       TEXT,
                rlm_detected INTEGER,
                injury_status TEXT
            )""")
        existing = [col[1] for col in cur.execute("PRAGMA table_info(slips)")]
        for col, dtype in [("strictness","TEXT"), ("cv","REAL"), ("minutes_vol","REAL"),
                           ("blowout_prob","REAL"), ("clv","REAL"), ("entry_odds","INTEGER"),
                           ("closing_odds","INTEGER"), ("b2b","INTEGER"), ("travel_zones","INTEGER"),
                           ("altitude_ft","INTEGER"), ("wind_mph","REAL"), ("temp_f","REAL"),
                           ("precip","TEXT"), ("rlm_detected","INTEGER"), ("injury_status","TEXT")]:
            if col not in existing:
                cur.execute(f"ALTER TABLE slips ADD COLUMN {col} {dtype} DEFAULT NULL")

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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tuning_params (
                param_name TEXT PRIMARY KEY,
                param_value REAL,
                last_updated TEXT
            )""")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS correlation_matrix (
                key1 TEXT, key2 TEXT, rho REAL,
                PRIMARY KEY (key1, key2)
            )""")

    if get_setting("prob_bolt") is None:
        set_setting("prob_bolt", _DEFAULT_PROB_BOLT)
    if get_setting("dtm_bolt") is None:
        set_setting("dtm_bolt", _DEFAULT_DTM_BOLT)
    if get_setting("bankroll") is None:
        set_setting("bankroll", 1000.0)
    for tier, default in VOLATILITY_TIERS.items():
        if get_setting(f"mult_{tier}") is None:
            set_setting(f"mult_{tier}", default)

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

def get_volatility_multiplier(market: str) -> float:
    tier = STAT_CONFIG.get(market.upper(), {}).get("tier", "LOW")
    return get_setting(f"mult_{tier}", VOLATILITY_TIERS.get(tier, 0.97))

def update_volatility_multiplier(tier: str, new_value: float):
    set_setting(f"mult_{tier}", new_value)

# =============================================================================
# ENVIRONMENTAL SENSORS
# =============================================================================
def b2b_adjustment(role: str, is_home: bool, back_to_back: bool) -> float:
    if not back_to_back:
        return 1.0
    adjustments = {
        "STARTER": {"home": 0.93, "away": 0.90},
        "ROTATION": {"home": 0.87, "away": 0.82},
        "BENCH": {"home": 0.80, "away": 0.75},
    }
    role_upper = role.upper()
    if role_upper not in adjustments:
        role_upper = "ROTATION"
    return adjustments[role_upper]["home" if is_home else "away"]

def travel_stress_multiplier(zones_crossed: int, rest_days: int, direction: str) -> float:
    rest = min(rest_days, 3)
    base = {
        0: {0: 1.0, 1: 1.0, 2: 1.0, 3: 1.0},
        1: {0: 0.98, 1: 0.98, 2: 0.98, 3: 0.98},
        2: {0: 0.92 if direction == "west_to_east" else 0.95,
            1: 0.95 if direction == "west_to_east" else 0.96,
            2: 0.97, 3: 0.98},
        3: {0: 0.88 if direction == "west_to_east" else 0.92,
            1: 0.92 if direction == "west_to_east" else 0.94,
            2: 0.95, 3: 0.96},
    }
    zones = min(zones_crossed, 3)
    return base.get(zones, {rest: 0.95}).get(rest, 0.95)

def altitude_multiplier(elevation_ft: float, sport: str = "NBA", market: str = "") -> float:
    if sport == "MLB" and market == "HR":
        if elevation_ft >= 5000:
            return 1.15
        elif elevation_ft >= 3000:
            return 1.08
    if elevation_ft >= 5000:
        return 1.12
    elif elevation_ft >= 3000:
        return 1.06
    return 1.0

def weather_multiplier(sport: str, market: str, wind_mph: float, temp_f: float, precip: str) -> float:
    mult = 1.0
    if wind_mph >= 15:
        if sport == "NFL" and market in ["PASS_YDS", "PASS_TDS"]:
            mult *= (0.92 if wind_mph >= 20 else 0.95)
        elif sport == "MLB" and market == "HR":
            if wind_mph >= 20:
                mult *= 0.85
            else:
                mult *= 0.92
    if sport == "MLB" and market == "HR":
        if temp_f < 40:
            mult *= 0.92
        elif temp_f < 50:
            mult *= 0.95
        elif 65 <= temp_f < 75:
            mult *= 1.02
        elif 75 <= temp_f < 85:
            mult *= 1.05
        elif temp_f >= 85:
            mult *= 1.03
    if precip == "rain":
        if market == "HR":
            mult *= 0.97
        elif market in ["KS", "STRIKEOUTS"]:
            mult *= 1.02
        mult *= 0.98
    elif precip == "snow":
        if sport == "NFL" and market == "PASS_YDS":
            mult *= 0.88
        mult *= 0.95
    return mult

def steam_rlm(public_pct: float, line_movement: float) -> Tuple[bool, float, str]:
    if public_pct > 0.65 and line_movement < 0:
        return True, 0.015, "RLM detected: sharp money opposite public"
    elif public_pct > 0.65 and line_movement > 0:
        return True, -0.015, "RLM detected but model disagrees – caution"
    return False, 0.0, ""

def news_friction_multiplier(injury_status: str, injury_type: str = "") -> Tuple[float, int]:
    status_map = {
        "OUT": (0.0, 10),
        "GTD": (0.85, 2),
        "QUESTIONABLE": (0.50, 3),
        "DAY_TO_DAY": (0.85, 2),
        "PROBABLE": (0.85, 1),
        "HEALTHY": (1.0, 0),
    }
    base_mult, base_penalty = status_map.get(injury_status.upper(), (1.0, 0))
    if injury_type.upper() in ["SHOULDER", "KNEE", "BACK", "CONCUSSION"]:
        base_mult *= 0.85
    elif injury_type.upper() in ["ANKLE", "STRAIN"]:
        base_mult *= 0.90
    return base_mult, base_penalty

def motivation_multiplier(is_elimination: bool, contract_incentive: bool, is_playoff: bool) -> float:
    mult = 1.0
    if is_playoff:
        mult *= 1.02
    if is_elimination:
        mult *= 1.05
    if contract_incentive:
        mult *= 1.03
    return mult

def abs_challenge_adj(umpire_overturn_rate: float) -> float:
    if umpire_overturn_rate > 0.55:
        return 0.90
    return 1.0

def series_state_multiplier(series_state: str, is_star: bool) -> float:
    if is_star:
        return 0.94
    if series_state == "tied":
        return 0.94
    elif series_state == "down":
        return 0.90
    elif series_state == "up":
        return 0.95
    return 1.0

def apply_usage_minutes_filters(usage_pct: float, minutes_avg: float) -> Tuple[bool, str]:
    if usage_pct < 0.19:
        return True, f"AUTO-PASS: Usage {usage_pct:.1%} <19% (bench player)"
    if minutes_avg < 26:
        return True, f"AUTO-PASS: Minutes {minutes_avg:.1f} <26 min/game"
    return False, ""

# =============================================================================
# STATISTICAL CORE
# =============================================================================
def outlier_suppressed_weights(values: List[float], threshold_sigma: float = 3.0) -> List[float]:
    if len(values) == 0:
        return []
    mean = np.mean(values)
    std = np.std(values)
    if std == 0:
        return [1.0] * len(values)
    weights = []
    for v in values:
        if abs(v - mean) > threshold_sigma * std:
            weights.append(0.5)
        else:
            weights.append(1.0)
    return weights

def garbage_time_adjust(value: float, blowout_margin: float, usage_pct: float) -> float:
    if blowout_margin > 18 and usage_pct < 0.15:
        return value * 0.80
    return value

def role_change_weighted_wma(values: List[float], role_change: bool = False) -> float:
    if len(values) < 6:
        return np.mean(values) if values else 0.0
    last6 = values[-6:]
    outlier_weights = outlier_suppressed_weights(last6)
    base_weights = [1.0, 1.0, 1.5, 1.5, 1.5, 1.5]
    if role_change:
        base_weights = [1.0, 1.0, 2.0, 2.0, 2.0, 2.0]
    combined = [base_weights[i] * outlier_weights[i] for i in range(6)]
    return float(np.average(last6, weights=combined))

def compute_wsem(values: List[float], window: int = 8) -> float:
    if len(values) < 2:
        return 1.0
    last = values[-window:] if len(values) >= window else values
    linear_weights = list(range(1, len(last)+1))
    outlier_weights = outlier_suppressed_weights(last)
    combined = [linear_weights[i] * outlier_weights[i] for i in range(len(last))]
    mu = np.average(last, weights=combined)
    var = np.average((last - mu)**2, weights=combined)
    return max(np.sqrt(var / len(last)), 0.5)

def l42_buffer(values: List[float]) -> float:
    if len(values) < 4:
        return 1.0
    std4 = np.std(values[-4:])
    return 1.0 + min(std4, 0.5)

def minutes_volatility_risk(minutes_list: List[float]) -> Tuple[float, bool]:
    if len(minutes_list) < 4:
        return 0.0, False
    recent = minutes_list[-4:]
    mean_min = np.mean(recent) or 1.0
    cv = np.std(recent) / mean_min
    drop = (recent[0] - recent[-1]) / recent[0] if recent[0] > 0 else 0
    high_risk = (drop > 0.3) or (cv > 0.18)
    return cv, high_risk

def matchup_delta(player_avg: float, opp_allowed_avg: float, league_avg: float) -> float:
    if league_avg == 0:
        return 0.0
    return (player_avg - opp_allowed_avg) / league_avg

def strictness_advisory(
    blowout_prob: float,
    minutes_cv: float,
    role_stable_games: int,
    injury_status: str,
    cv: float,
    matchup_delta_val: float
) -> Tuple[str, int, float]:
    risk = 0.0
    if blowout_prob > 0.18:
        risk += 0.3
    if minutes_cv > 0.20:
        risk += 0.25
    if role_stable_games < 4:
        risk += 0.35
    if injury_status in ["QUESTIONABLE", "DAY_TO_DAY"]:
        risk += 0.4
    if cv > 0.20:
        risk += 0.3
    if matchup_delta_val < -0.12:
        risk += 0.2
    if risk >= 0.7:
        return "A", 6, 0.02
    elif risk >= 0.3:
        return "C", 7, 0.0
    else:
        return "B", 9, -0.005

def generate_alternatives(main_line: float, mu: float, sigma: float, dist_type: str,
                          pick: str, step: float = 0.5, steps: int = 3) -> List[Dict]:
    alternatives = []
    for i in range(-steps, steps+1):
        if i == 0:
            continue
        alt_line = main_line + i * step
        if dist_type == "NORMAL":
            if pick == "OVER":
                prob = 1 - norm.cdf(alt_line, mu, sigma)
            else:
                prob = norm.cdf(alt_line, mu, sigma)
        else:
            if pick == "OVER":
                prob = 1 - poisson.cdf(alt_line, mu)
            else:
                prob = poisson.cdf(alt_line, mu)
        edge = prob - 0.5
        alternatives.append({"line": alt_line, "prob": prob, "edge": edge})
    alternatives.sort(key=lambda x: x["edge"], reverse=True)
    return alternatives[:3]

def slip_correlation_penalty(legs: List[Dict]) -> Tuple[float, str]:
    if len(legs) < 2:
        return 1.0, "Single leg"
    rhos = []
    for a, b in combinations(legs, 2):
        if a.get("player") == b.get("player"):
            if a.get("market") in ["PTS","PRA","PR","PA"] and b.get("market") in ["PTS","PRA","PR","PA"]:
                rho = 0.85
            else:
                rho = 0.45
        elif a.get("team") == b.get("team") and a.get("opponent") == b.get("opponent"):
            rho = 0.60
        else:
            rho = 0.15
        rhos.append(rho)
    avg_rho = np.mean(rhos)
    if avg_rho > 0.70:
        return 0.0, "AUTO-PASS: high slip correlation"
    if avg_rho > 0.50:
        return 0.80, "Kelly reduced 20% due to moderate correlation"
    return 1.0, "Correlation acceptable"

# =============================================================================
# DATABASE OPERATIONS
# =============================================================================
def insert_slip(entry: dict) -> None:
    slip_id = str(uuid.uuid4()).replace("-", "")[:12]
    try:
        with _conn() as c:
            c.execute("""
                INSERT OR REPLACE INTO slips
                (id,type,sport,player,team,opponent,market,line,pick,odds,
                 edge,prob,kelly,tier,bolt_signal,result,actual,
                 date,settled_date,profit,bankroll,notes,strictness,cv,minutes_vol,blowout_prob,
                 entry_odds,closing_odds,clv,b2b,travel_zones,altitude_ft,wind_mph,temp_f,precip,rlm_detected,injury_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                entry.get("notes",""),            entry.get("strictness",""),
                entry.get("cv"),                  entry.get("minutes_vol"),
                entry.get("blowout_prob"),        entry.get("odds"),
                None, None,
                entry.get("b2b",0), entry.get("travel_zones",0), entry.get("altitude_ft",0),
                entry.get("wind_mph"), entry.get("temp_f"), entry.get("precip",""),
                entry.get("rlm_detected",0), entry.get("injury_status","")
            ))
    except Exception as e:
        logging.error(f"insert_slip: {e}")
    if entry.get("result") in ("WIN","LOSS"):
        set_bankroll(get_bankroll() + entry.get("profit", 0.0))
        _calibrate_sem()
        _auto_tune()
        auto_tune_volatility_multipliers()

def update_slip_result(slip_id: str, result: str, actual: float, closing_odds: int) -> None:
    try:
        with _conn() as c:
            row = c.execute("SELECT odds, stake FROM slips WHERE id=?", (slip_id,)).fetchone()
            if row is None:
                return
            entry_odds, stake = row
            if result == "WIN":
                if entry_odds > 0:
                    profit = stake * (entry_odds / 100)
                else:
                    profit = stake * (100 / abs(entry_odds))
            elif result == "LOSS":
                profit = -stake
            else:
                profit = 0
            clv = (closing_odds - entry_odds) / abs(entry_odds) if entry_odds != 0 else 0.0
            c.execute(
                "UPDATE slips SET result=?, actual=?, settled_date=?, profit=?, closing_odds=?, clv=? WHERE id=?",
                (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, closing_odds, clv, slip_id)
            )
    except Exception as e:
        logging.error(f"update_slip_result: {e}")
    set_bankroll(get_bankroll() + profit)
    _calibrate_sem()
    _auto_tune()
    auto_tune_volatility_multipliers()

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
# API HEALTH TRACKER
# =============================================================================
_SERVICES = [
    "BallsDontLie (NBA)", "Odds-API.io (scores)", "The Odds API (scanner)",
    "PropLine (live props)", "Slash Golf (PGA)", "FlashLive (multi-sport)",
    "ESPN (fallback)", "nhl-api-py (NHL)", "curl_cffi (TLS)",
    "RapidAPI (Tennis)", "DraftKings API", "Parlay-API", "WeatherAPI",
    "Sportmonks (xG)",
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
# HTTP SESSION
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
# WEATHER FETCH (REAL)
# =============================================================================
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_weather_auto(lat: float, lon: float, dt: datetime) -> Dict:
    key = st.secrets.get("WEATHER_API_KEY", "")
    if not key:
        return {"wind_mph": 0, "temp_f": 70, "precip": "clear"}
    url = f"http://api.weatherapi.com/v1/forecast.json?key={key}&q={lat},{lon}&dt={dt.strftime('%Y-%m-%d')}"
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        hour = dt.hour
        forecast = data["forecast"]["forecastday"][0]["hour"][hour]
        wind_mph = forecast.get("wind_mph", 0)
        temp_f = forecast.get("temp_f", 70)
        precip_mm = forecast.get("precip_mm", 0)
        condition = forecast.get("condition", {}).get("text", "").lower()
        if "rain" in condition:
            precip = "rain"
        elif "snow" in condition:
            precip = "snow"
        else:
            precip = "clear"
        _health("WeatherAPI", True)
        return {"wind_mph": wind_mph, "temp_f": temp_f, "precip": precip}
    except Exception as e:
        _health("WeatherAPI", False, str(e), True)
        return {"wind_mph": 0, "temp_f": 70, "precip": "clear"}

# =============================================================================
# SPORTMONKS XG FETCHER (SOCCER)
# =============================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_sportmonks_xg(player_name: str, league_id: int = None, season_id: int = None) -> List[float]:
    key = st.secrets.get("SPORTMONKS_API_KEY", "")
    if not key:
        _health("Sportmonks (xG)", False, "API key missing", True)
        return []
    try:
        search_url = "https://soccer.sportmonks.com/api/v2.0/players/search"
        params = {"api_token": key, "search": player_name}
        resp = requests.get(search_url, params=params, timeout=10)
        if resp.status_code != 200:
            _health("Sportmonks (xG)", False, f"HTTP {resp.status_code}", True)
            return []
        data = resp.json()
        players = data.get("data", [])
        if not players:
            return []
        player_id = players[0].get("id")
        if not player_id:
            return []
        stats_url = f"https://soccer.sportmonks.com/api/v2.0/players/{player_id}/stats"
        params_stats = {"api_token": key, "include": "statistics", "per_page": 10}
        stats_resp = requests.get(stats_url, params=params_stats, timeout=10)
        if stats_resp.status_code != 200:
            _health("Sportmonks (xG)", False, f"Stats HTTP {stats_resp.status_code}", True)
            return []
        stats_data = stats_resp.json()
        xg_vals = []
        for fixture in stats_data.get("data", []):
            stats = fixture.get("statistics", {})
            xg = stats.get("expected_goals") or stats.get("xg")
            if xg is not None:
                xg_vals.append(float(xg))
        _health("Sportmonks (xG)", bool(xg_vals))
        return xg_vals
    except Exception as e:
        _health("Sportmonks (xG)", False, str(e), True)
        logging.error(f"Sportmonks fetch error: {e}")
        return []

# =============================================================================
# FLASHLIVE & ESPN FALLBACK (extended for tennis)
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
        market_lower = market.lower()
        vals = [float(g[market_lower]) for g in logs[:8]
                if isinstance(g.get(market_lower),(int,float))]
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
    if sport.upper() == "NBA":
        vals = _nba_stats(player, market, game_date)
    elif sport.upper() == "SOCCER":
        if market.upper() == "XG":
            vals = fetch_sportmonks_xg(player)
        else:
            vals = _flashlive_stats(player, sport, market)
    elif sport.upper() == "TENNIS":
        vals = _flashlive_stats(player, sport, market)
    else:
        vals = _flashlive_stats(player, sport, market)

    if len(vals) < 3:
        vals = _espn_stats(player, sport, market)
    if len(vals) < 3:
        vals = historical_fallback(market, sport, tier)
    return vals

# =============================================================================
# TIER‑AWARE HISTORICAL FALLBACK
# =============================================================================
_FALLBACK_TIERS = {
    "elite": {
        ("NBA","PTS"): [32.1,29.8,31.5,33.2,28.9,30.4,32.8,29.1,31.0,33.5,28.5,32.3],
        ("NBA","REB"): [10.5,9.8,11.2,10.1,9.5,10.8,11.5,9.2,10.3,11.0,9.9,10.7],
        ("NBA","AST"): [8.2,7.9,8.8,7.5,8.5,9.1,7.8,8.4,9.3,7.6,8.0,8.9],
        ("SOCCER","XG"): [1.2,1.4,0.9,1.6,1.1,1.3,1.0,1.5,1.2,1.3,0.8,1.4],
        ("TENNIS","ACES"): [6.5,7.2,5.8,8.0,6.8,7.5,5.5,8.5,6.2,7.0,5.2,7.8],
    },
    "mid": {
        ("NBA","PTS"): [22.5,23.1,21.8,24.2,22.9,23.5,21.5,24.0,22.7,23.3,21.9,23.8],
        ("NBA","REB"): [7.2,7.5,6.9,7.8,7.3,7.6,6.8,7.9,7.1,7.4,6.7,7.7],
        ("NBA","AST"): [5.1,5.3,4.9,5.6,5.2,5.4,4.8,5.7,5.0,5.5,4.7,5.8],
        ("SOCCER","XG"): [0.6,0.8,0.5,1.0,0.7,0.9,0.4,1.1,0.6,0.8,0.5,0.9],
        ("TENNIS","ACES"): [3.2,3.8,2.9,4.2,3.5,4.0,2.5,4.5,3.0,3.6,2.7,4.0],
    },
    "bench": {
        ("NBA","PTS"): [8.5,9.1,7.8,10.2,8.9,9.4,7.5,10.5,8.3,9.7,7.2,10.0],
        ("NBA","REB"): [3.5,3.8,3.2,4.0,3.6,3.9,3.1,4.2,3.4,3.7,3.0,4.1],
        ("SOCCER","XG"): [0.2,0.3,0.1,0.4,0.2,0.3,0.1,0.5,0.2,0.3,0.1,0.4],
        ("TENNIS","ACES"): [1.5,2.0,1.2,2.5,1.8,2.2,1.0,2.8,1.6,2.1,1.3,2.4],
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
# DRAFTKINGS LINE FETCHER
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
# NBA STATS API (BallsDontLie)
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
        vals  = [float(g[stat]) for g in games if isinstance(g.get(stat),(int,float))]
        _health("BallsDontLie (NBA)", bool(vals), "" if vals else "No stats", not bool(vals))
        return vals
    except Exception as e:
        _health("BallsDontLie (NBA)", False, str(e), True)
        logging.error(f"_nba_stats: {e}")
        return []

# =============================================================================
# NBA TEAM STATS
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
# PLAYER PROJECTIONS ENGINE
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
# ANALYTICAL DISTRIBUTION ENGINE
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
# MONTE CARLO ENGINE
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
# PRICED BET (for best bets tab)
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
# KELLY & TIER CLASSIFICATION
# =============================================================================
def american_to_prob(odds: int) -> float:
    o = float(odds)
    return 100/(o+100) if o > 0 else -o/(-o+100)

def kelly(prob: float, odds: int) -> float:
    b = abs(odds)/100 if odds > 0 else 100/abs(odds)
    k = (prob*(b+1)-1)/b
    return max(0.0, min(k, 0.25)) * KELLY_FRACTION

def tier_mult(market: str) -> float:
    return STAT_CONFIG.get(market.upper(), {}).get("mult", 0.97)

def classify_tier(edge: float) -> str:
    if edge >= 0.15: return "SOVEREIGN BOLT"
    if edge >= 0.08: return "ELITE LOCK"
    if edge >= 0.04: return "APPROVED"
    if edge < 0:     return "PASS"
    return "NEUTRAL"

def current_edge_floor() -> float:
    try:
        with _conn() as c:
            df = pd.read_sql_query(
                "SELECT result,profit FROM slips WHERE result IN ('WIN','LOSS') "
                "AND settled_date > date('now', '-7 days') "
                "ORDER BY settled_date DESC LIMIT 25", c
            )
            if len(df) >= 10:
                roi = df["profit"].sum() / (len(df) * 100)
                if roi < -0.05:
                    return 0.12
                elif roi < 0.0:
                    return 0.07
    except Exception:
        pass
    bankroll = get_bankroll()
    if bankroll < 400:
        return 0.055
    return 0.045

def confidence_score(num_games: int) -> int:
    if num_games >= 10: return 10
    if num_games >= 8: return 9
    if num_games >= 6: return 7
    if num_games >= 4: return 5
    if num_games >= 2: return 3
    return 1

def calculate_kelly_stake(bankroll: float, prob: float, odds: int, fraction: float = KELLY_FRACTION) -> float:
    if odds == 0:
        return 0.0
    b = odds / 100 if odds > 0 else 100 / abs(odds)
    k = (prob * (b + 1) - 1) / b
    k = max(0.0, min(k, 0.25))
    return bankroll * k * fraction

# =============================================================================
# UPGRADED PROP ANALYSIS FUNCTION (with all sensors + xG)
# =============================================================================
def analyze_prop(
    player: str, market: str, line: float, pick: str,
    sport: str = "NBA", odds: int = -110, bankroll: float = None, tier: str = "mid",
    use_mc: bool = False, mc_sims: int = 10000,
    role_change: bool = False, blowout_margin_list: List[float] = None,
    usage_list: List[float] = None, minutes_list: List[float] = None,
    injury_status: str = "HEALTHY", injury_type: str = "",
    blowout_prob: float = 0.0, is_playoff: bool = False,
    matchup_delta_val: float = None, usage_trend_up: bool = False,
    b2b: bool = False, player_role: str = "ROTATION", is_home: bool = True,
    travel_zones: int = 0, rest_days: int = 2, direction: str = "none",
    altitude_city: str = "", elevation_ft: float = 0,
    wind_mph: float = 0, temp_f: float = 70, precip: str = "clear",
    public_pct: float = 0.5, line_movement: float = 0.0,
    is_elimination: bool = False, contract_incentive: bool = False,
    umpire_overturn: float = 0.0, series_state: str = "tied", is_star: bool = False
) -> Dict:
    if bankroll is None:
        bankroll = get_bankroll()
    stats_raw = fetch_stats(player, market, sport, tier=tier)

    if blowout_margin_list is not None and usage_list is not None and len(stats_raw) == len(blowout_margin_list):
        adj_stats = [garbage_time_adjust(v, bm, up) for v, bm, up in zip(stats_raw, blowout_margin_list, usage_list)]
    else:
        adj_stats = stats_raw

    mu_raw = role_change_weighted_wma(adj_stats, role_change)

    b2b_mult = b2b_adjustment(player_role, is_home, b2b)
    travel_mult = travel_stress_multiplier(travel_zones, rest_days, direction)
    if altitude_city:
        elev = ARENA_ELEVATIONS.get(altitude_city, elevation_ft)
    else:
        elev = elevation_ft
    alt_mult = altitude_multiplier(elev, sport, market)
    weather_mult = weather_multiplier(sport, market, wind_mph, temp_f, precip)
    motivation_mult = motivation_multiplier(is_elimination, contract_incentive, is_playoff)
    series_mult = series_state_multiplier(series_state, is_star) if is_playoff else 1.0
    news_mult, conf_penalty = news_friction_multiplier(injury_status, injury_type)
    if news_mult == 0.0:
        return {"error": f"AUTO-PASS: Injury status {injury_status}", "tier": "PASS"}

    mu = mu_raw * b2b_mult * travel_mult * alt_mult * weather_mult * motivation_mult * series_mult * news_mult

    avg_minutes = np.mean(minutes_list) if minutes_list else 33.0 if sport.upper() in ["SOCCER","TENNIS"] else 28.0
    usage_pct = np.mean(usage_list) if usage_list else 0.22
    should_pass, reason = apply_usage_minutes_filters(usage_pct, avg_minutes)
    if should_pass:
        return {"error": reason, "tier": "PASS"}

    wsem = compute_wsem(adj_stats)
    buffer = l42_buffer(adj_stats)
    sigma_raw = max(wsem * buffer, 0.75)
    if is_playoff:
        sigma = sigma_raw + (wsem * 0.5) + 3.5
    else:
        sigma = sigma_raw

    if minutes_list and len(minutes_list) >= 4:
        mins_cv, mins_risk = minutes_volatility_risk(minutes_list)
        if mins_risk:
            return {"error": "AUTO-PASS: Minutes volatility >18% or drop >30%", "tier": "PASS"}
    if blowout_prob > 0.18 and market.upper() in ["PTS","PRA","PR","PA","GOALS","STRIKES","SHOTS"]:
        return {"error": "AUTO-PASS: Blowout probability >18% on usage prop", "tier": "PASS"}

    if use_mc:
        proj = PlayerProjection(
            player_name=player, team="", opponent="",
            minutes=avg_minutes, pts=mu, rebs=5.0, asts=4.0,
            usage=usage_pct, pace_adj=98.0,
            raw_payload={"rates": {"stl":0.08,"blk":0.05,"to":0.12}},
        )
        mc_res = mc_price_market(proj, market.lower(), line, n=mc_sims)
        prob = mc_res["prob_over"] if pick == "OVER" else 1 - mc_res["prob_over"]
        raw_edge = mc_res["edge"]
        kelly_val = mc_res["kelly"]
        fair = mc_res["fair_line"]
    else:
        if line < 4.5:
            if pick == "OVER":
                prob = 1 - poisson.cdf(line, mu=mu)
            else:
                prob = poisson.cdf(line, mu=mu)
            dist_type = "POISSON"
        else:
            if pick == "OVER":
                prob = 1 - norm.cdf(line, mu, sigma)
            else:
                prob = norm.cdf(line, mu, sigma)
            dist_type = "NORMAL"
        impl = american_to_prob(odds)
        raw_edge = prob - impl
        kelly_val = kelly(prob, odds)
        fair = mu

    if raw_edge > 0.20:
        return {"error": "AUTO-PASS: Raw edge >20% (stale line / injury alert)", "tier": "PASS"}

    vol_mult = tier_mult(market)
    adj_edge = raw_edge * vol_mult

    cv = sigma / mu if mu > 0 else 10.0
    if cv > 0.18:
        adj_edge *= 0.80
    if cv > 0.25:
        return {"error": f"AUTO-PASS: Extreme volatility CV={cv:.2f} >0.25", "tier": "PASS"}

    if sport == "MLB" and market.upper() in ["KS", "STRIKEOUTS"]:
        adj_edge *= abs_challenge_adj(umpire_overturn)

    if matchup_delta_val is not None:
        if matchup_delta_val <= -0.12 and not usage_trend_up:
            return {"error": f"AUTO-PASS: Unfavorable matchup Δ={matchup_delta_val:.2f} and usage trend down", "tier": "PASS"}
        elif matchup_delta_val <= -0.12 and usage_trend_up:
            adj_edge *= 0.60

    rlm_detected, steam_boost, rlm_msg = steam_rlm(public_pct, line_movement)
    if rlm_detected:
        adj_edge += steam_boost

    floor = current_edge_floor()
    lean, lean_conf, floor_adj = strictness_advisory(
        blowout_prob, (sigma/mu) if mu else 0, len(stats_raw), injury_status, cv, matchup_delta_val or 0.0
    )
    floor += floor_adj
    floor = max(0.04, floor)

    sem_score = get_sem_score()
    kelly_frac = 0.25 if sem_score > 65 else (0.20 if sem_score >= 55 else 0.15)
    stake = bankroll * kelly_frac * min(kelly_val, 0.25) if adj_edge >= floor else 0.0

    tier_l = classify_tier(adj_edge)
    market_disc = abs(mu - line) / max(line, 1e-9)
    prob_bolt = get_prob_bolt()
    dtm_bolt = get_dtm_bolt()
    if prob >= prob_bolt and market_disc >= dtm_bolt and adj_edge >= 0.15 and lean != "A":
        bolt_signal = "SOVEREIGN BOLT"
    elif adj_edge >= 0.08 and prob >= 0.75 and lean != "A":
        bolt_signal = "ELITE LOCK"
    elif adj_edge >= floor:
        bolt_signal = "APPROVED"
    else:
        bolt_signal = "PASS"

    alternatives = []
    if bolt_signal != "PASS":
        alternatives = generate_alternatives(line, mu, sigma, dist_type, pick)

    return {
        "error": None,
        "prob": prob, "edge": adj_edge, "raw_edge": raw_edge,
        "mu": mu, "sigma": sigma, "cv": cv,
        "wma": mu, "wsem": wsem, "buffer": buffer,
        "tier": tier_l, "kelly": kelly_val, "stake": stake,
        "bolt_signal": bolt_signal, "stats": adj_stats, "fair_line": fair,
        "strictness": f"Lean {lean} (conf {lean_conf}/10)", "floor_used": floor,
        "alternatives": alternatives, "dist_type": dist_type,
        "vol_mult": vol_mult, "cv_applied": cv > 0.18,
    }

# =============================================================================
# GAME ANALYSIS FUNCTIONS
# =============================================================================
def analyze_total(home: str, away: str, sport: str,
                  line: float, over_odds: int, under_odds: int,
                  is_playoff: bool = False, blowout_prob: float = 0.0,
                  wind_mph: float = 0, temp_f: float = 70, precip: str = "clear") -> Dict:
    if sport == "NBA":
        ht = fetch_team_totals(home); at = fetch_team_totals(away)
        proj = role_change_weighted_wma(ht) + role_change_weighted_wma(at)
        comb = [h+a for h,a in zip(ht, at)] or ht+at
        sigma = max(compute_wsem(comb) * l42_buffer(comb), 0.75)
    else:
        proj = SPORT_MODELS.get(sport,{}).get("avg_total", 220.0)
        sigma = proj * 0.08

    if is_playoff:
        sigma = sigma + (compute_wsem(comb) * 0.5) + 3.5 if sport=="NBA" else sigma + 5.0

    weather_mult = weather_multiplier(sport, "TOTAL", wind_mph, temp_f, precip)
    proj *= weather_mult

    op = 1 - norm.cdf(line, proj, sigma)
    up = norm.cdf(line, proj, sigma)
    oim = american_to_prob(over_odds)
    uim = american_to_prob(under_odds)
    m = tier_mult("TOTAL")
    oe = (op - oim)*m
    ue = (up - uim)*m
    if blowout_prob > 0.18:
        oe *= 0.80
        ue *= 0.80
    pb = get_prob_bolt()
    db = get_dtm_bolt()
    denom = max(line, 1e-9)
    return {
        "projection": proj, "sigma": sigma,
        "over_prob": op, "over_edge": oe, "over_tier": classify_tier(oe),
        "over_bolt": "SOVEREIGN BOLT" if op>=pb and (proj-line)/denom>=db else classify_tier(oe),
        "under_prob": up, "under_edge": ue, "under_tier": classify_tier(ue),
        "under_bolt":"SOVEREIGN BOLT" if up>=pb and (line-proj)/denom>=db else classify_tier(ue),
    }

def analyze_spread(home: str, away: str, sport: str,
                   spread: float, home_odds: int, away_odds: int,
                   is_playoff: bool = False, blowout_prob: float = 0.0) -> Dict:
    if sport == "NBA":
        hm = fetch_team_margins(home); am = fetch_team_margins(away)
        pm = role_change_weighted_wma(hm) - role_change_weighted_wma(am) + 3.0
        comb = [h-a for h,a in zip(hm, am)] or hm+[-x for x in am]
        sigma = max(compute_wsem(comb)*l42_buffer(comb), 0.75)
        if is_playoff:
            sigma = sigma + (compute_wsem(comb)*0.5) + 3.5
    else:
        pm = SPORT_MODELS.get(sport,{}).get("home_advantage", 3.0)
        sigma = 10.0

    hcp = 1 - norm.cdf(spread, pm, sigma)
    acp = norm.cdf(spread, pm, sigma)
    home_imp = american_to_prob(home_odds)
    away_imp = american_to_prob(away_odds)
    m = tier_mult("SPREAD")
    he = (hcp - home_imp)*m
    ae = (acp - away_imp)*m
    if blowout_prob > 0.18:
        he *= 0.80
        ae *= 0.80
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

def analyze_ml(home: str, away: str, sport: str, home_odds: int, away_odds: int, is_playoff: bool = False) -> Dict:
    sp = analyze_spread(home, away, sport, 0.0, home_odds, away_odds, is_playoff)
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
# GAME SCANNER (Parlay-API)
# =============================================================================
class GameScanner:
    def __init__(self):
        self.key = st.secrets.get("PARLAY_API_KEY") or st.secrets.get("ODDS_API_KEY")
        self.base = "https://parlay-api.com/v1"
        self._sport_keys = {
            "NBA": "basketball_nba",
            "NFL": "americanfootball_nfl",
            "MLB": "baseball_mlb",
            "NHL": "icehockey_nhl",
        }

    def fetch(self, sports: List[str], days: int = 0) -> List[Dict]:
        if not self.key:
            st.error("No API key found. Please set PARLAY_API_KEY or ODDS_API_KEY in secrets.")
            return []
        games = []
        for sport in sports:
            sk = self._sport_keys.get(sport, sport.lower())
            games += self._enrich(sport, sk, days)
        _health("The Odds API (scanner)", bool(games))
        return games

    def _enrich(self, sport: str, sk: str, days: int) -> List[Dict]:
        try:
            url = f"{self.base}/sports/{sk}/events"
            ev_r = requests.get(url, params={"apiKey": self.key, "days": days + 1}, timeout=10)
            ev_r.raise_for_status()
            events = ev_r.json()
        except Exception as e:
            _health("The Odds API (scanner)", False, str(e), True)
            return []
        try:
            od_r = requests.get(f"{self.base}/sports/{sk}/odds",
                                params={"apiKey": self.key, "regions": "us",
                                        "markets": "h2h,spreads,totals",
                                        "oddsFormat": "american", "days": days + 1}, timeout=10)
            odds_data = od_r.json() if od_r.status_code == 200 else []
        except Exception:
            odds_data = []

        odds_by_id = {o.get("id"): o for o in odds_data if o.get("id")}
        for ev in events:
            ev["sport"] = sport
            oi = odds_by_id.get(ev.get("id"), {})
            bms = oi.get("bookmakers", [])
            if bms:
                bm = bms[0]
                for m in bm.get("markets", []):
                    oc = m["outcomes"]
                    if m["key"] == "h2h":
                        ev["home_ml"] = next((o["price"] for o in oc if o["name"] == ev.get("home_team")), None)
                        ev["away_ml"] = next((o["price"] for o in oc if o["name"] == ev.get("away_team")), None)
                    elif m["key"] == "spreads":
                        home_out = next((o for o in oc if o["name"] == ev.get("home_team")), None)
                        away_out = next((o for o in oc if o["name"] == ev.get("away_team")), None)
                        if home_out:
                            ev["spread"] = home_out.get("point")
                            ev["home_spread_odds"] = home_out.get("price")
                        if away_out:
                            ev["away_spread_odds"] = away_out.get("price")
                    elif m["key"] == "totals":
                        over_out = next((o for o in oc if o["name"] == "Over"), None)
                        under_out = next((o for o in oc if o["name"] == "Under"), None)
                        if over_out or under_out:
                            ev["total"] = (over_out or under_out).get("point")
                            ev["over_odds"] = over_out.get("price") if over_out else None
                            ev["under_odds"] = under_out.get("price") if under_out else None
        return events

game_scanner = GameScanner()

# =============================================================================
# SCHEDULE & AUTO-PROJECTION LOADERS
# =============================================================================
def _normalize_team_name(t: str) -> str:
    mapping = {
        "lakers": "lakers", "los angeles lakers": "lakers", "lal": "lakers",
        "warriors": "warriors", "golden state warriors": "warriors", "gsw": "warriors",
        "celtics": "celtics", "boston celtics": "celtics", "bos": "celtics",
        "bucks": "bucks", "milwaukee bucks": "bucks", "mil": "bucks",
        "nuggets": "nuggets", "denver nuggets": "nuggets", "den": "nuggets",
        "suns": "suns", "phoenix suns": "suns", "phx": "suns",
        "mavericks": "mavericks", "dallas mavericks": "mavericks", "dal": "mavericks",
        "76ers": "76ers", "philadelphia 76ers": "76ers", "phi": "76ers",
        "knicks": "knicks", "new york knicks": "knicks", "nyk": "knicks",
        "heat": "heat", "miami heat": "heat", "mia": "heat",
        "thunder": "thunder", "oklahoma city thunder": "thunder", "okc": "thunder",
        "cavaliers": "cavaliers", "cleveland cavaliers": "cavaliers", "cle": "cavaliers",
        "timberwolves": "timberwolves", "minnesota timberwolves": "timberwolves", "min": "timberwolves",
        "clippers": "clippers", "la clippers": "clippers", "lac": "clippers",
        "kings": "kings", "sacramento kings": "kings", "sac": "kings",
    }
    t_lower = t.lower().strip()
    return mapping.get(t_lower, t_lower)

_STAR_PLAYERS: Dict[str, List[str]] = {
    "lakers": ["LeBron James", "Anthony Davis"],
    "warriors": ["Stephen Curry", "Klay Thompson"],
    "celtics": ["Jayson Tatum", "Jaylen Brown"],
    "bucks": ["Giannis Antetokounmpo", "Damian Lillard"],
    "nuggets": ["Nikola Jokic", "Jamal Murray"],
    "suns": ["Kevin Durant", "Devin Booker"],
    "mavericks": ["Luka Doncic", "Kyrie Irving"],
    "76ers": ["Joel Embiid", "Tyrese Maxey"],
    "knicks": ["Jalen Brunson", "Julius Randle"],
    "heat": ["Jimmy Butler", "Bam Adebayo"],
    "thunder": ["Shai Gilgeous-Alexander", "Jalen Williams"],
    "cavaliers": ["Donovan Mitchell", "Darius Garland"],
    "timberwolves": ["Anthony Edwards", "Karl-Anthony Towns"],
    "clippers": ["Kawhi Leonard", "Paul George"],
    "kings": ["De'Aaron Fox", "Domantas Sabonis"],
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
            home_norm = _normalize_team_name(home)
            away_norm = _normalize_team_name(away)
            for key, players in _STAR_PLAYERS.items():
                if key == home_norm:
                    for p in players: rows.append({"player_name":p,"team":home,"opponent":away})
                if key == away_norm:
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

def analyze_game_bets(games: List[Dict], sport: str, min_edge: float, is_playoff: bool = False) -> List[Dict]:
    results = []
    for game in games:
        home = game.get("home_team",""); away = game.get("away_team","")
        spread = game.get("spread")
        home_spread_odds = game.get("home_spread_odds")
        away_spread_odds = game.get("away_spread_odds")
        if spread is not None and home_spread_odds is not None and away_spread_odds is not None:
            res = analyze_spread(home, away, sport, spread, int(home_spread_odds), int(away_spread_odds), is_playoff)
            if res["home_edge"] >= min_edge:
                results.append({
                    "type":"Spread","team":home,"opponent":away,
                    "line":spread,"odds":home_spread_odds,"edge":res["home_edge"],
                    "prob":res["home_cover_prob"],"fair_line":res["projected_margin"],
                    "pick":home,"bolt":res["home_bolt"],
                })
            if res["away_edge"] >= min_edge:
                results.append({
                    "type":"Spread","team":away,"opponent":home,
                    "line":-spread,"odds":away_spread_odds,"edge":res["away_edge"],
                    "prob":res["away_cover_prob"],"fair_line":res["projected_margin"],
                    "pick":away,"bolt":res["away_bolt"],
                })
        total = game.get("total")
        over_odds = game.get("over_odds")
        under_odds = game.get("under_odds")
        if total is not None and over_odds is not None and under_odds is not None:
            res = analyze_total(home, away, sport, total, int(over_odds), int(under_odds), is_playoff)
            if res["over_edge"] >= min_edge:
                results.append({
                    "type":"Total","team":f"{away} @ {home}","opponent":"",
                    "line":total,"odds":over_odds,"edge":res["over_edge"],
                    "prob":res["over_prob"],"fair_line":res["projection"],
                    "pick":"Over","bolt":res["over_bolt"],
                })
            if res["under_edge"] >= min_edge:
                results.append({
                    "type":"Total","team":f"{away} @ {home}","opponent":"",
                    "line":total,"odds":under_odds,"edge":res["under_edge"],
                    "prob":res["under_prob"],"fair_line":res["projection"],
                    "pick":"Under","bolt":res["under_bolt"],
                })
        home_ml = game.get("home_ml")
        away_ml = game.get("away_ml")
        if home_ml is not None and away_ml is not None:
            res = analyze_ml(home, away, sport, int(home_ml), int(away_ml), is_playoff)
            if res["home_edge"] >= min_edge:
                results.append({
                    "type":"ML","team":home,"opponent":away,
                    "line":0,"odds":home_ml,"edge":res["home_edge"],
                    "prob":res["home_prob"],"fair_line":0.5,
                    "pick":home,"bolt":res["home_bolt"],
                })
            if res["away_edge"] >= min_edge:
                results.append({
                    "type":"ML","team":away,"opponent":home,
                    "line":0,"odds":away_ml,"edge":res["away_edge"],
                    "prob":res["away_prob"],"fair_line":0.5,
                    "pick":away,"bolt":res["away_bolt"],
                })
    return results

# =============================================================================
# SELF-LEARNING FUNCTIONS
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

def auto_tune_volatility_multipliers():
    with _conn() as c:
        for tier, current in VOLATILITY_TIERS.items():
            markets = [m for m, cfg in STAT_CONFIG.items() if cfg["tier"] == tier]
            if not markets:
                continue
            placeholders = ",".join(["?"] * len(markets))
            df = pd.read_sql_query(
                f"""SELECT result, profit FROM slips
                    WHERE market IN ({placeholders})
                    AND result IN ('WIN','LOSS')
                    AND settled_date > date('now', '-30 days')
                    ORDER BY settled_date DESC LIMIT 50""",
                c, params=markets
            )
            if len(df) < 10:
                continue
            win_rate = (df["result"] == "WIN").sum() / len(df)
            roi = df["profit"].sum() / (len(df) * 100)
            if win_rate < 0.50 and roi < 0.0:
                new_mult = current * 0.95
            elif win_rate > 0.60 and roi > 0.05:
                new_mult = current * 1.02
            else:
                continue
            new_mult = max(0.75, min(1.00, new_mult))
            update_volatility_multiplier(tier, new_mult)
            logging.info(f"Auto-tuned {tier} multiplier from {current:.3f} to {new_mult:.3f}")

def get_sem_score() -> int:
    try:
        with _conn() as c:
            row = c.execute("SELECT sem_score FROM sem_log ORDER BY id DESC LIMIT 1").fetchone()
            return row[0] if row else 100
    except Exception: return 100

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

def generate_parlays(bets: List[Dict], max_legs: int = 6, top_n: int = 20, min_edge: float = 0.03) -> List[Dict]:
    if len(bets) < 2: return []
    filtered = [b for b in bets if b.get("edge", 0) >= min_edge and 0.55 <= b.get("prob", 0.5) <= 0.75]
    if len(filtered) < 2: return []
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
            if conflict: continue
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
# BATCH ANALYSIS FUNCTION
# =============================================================================
APPROVE_EDGE = 0.04
BOLT_EDGE = 0.15

def analyze_props_batch(props: List[Dict], sport: str = "NBA", bankroll: float = None) -> List[Dict]:
    if bankroll is None: bankroll = get_bankroll()
    results = []
    for prop in props:
        try:
            res = analyze_prop(
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
            verdict = "APPROVED" if res["edge"] >= APPROVE_EDGE else "PASS"
            results.append({
                "prop": prop,
                "analysis": res,
                "edge": res["edge"],
                "tier": res["tier"],
                "bolt_signal": res["bolt_signal"],
                "verdict": verdict,
                "color": "green" if res["edge"] >= APPROVE_EDGE else "red",
                "confidence": confidence_score(len(res["stats"])) if "stats" in res else 5,
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
                "confidence": 1,
            })
    return results

def display_batch_results(results: List[Dict]) -> Tuple[List[Dict], int]:
    approved_props = []
    for r in results:
        edge_pct = r["edge"] * 100
        conf = r.get("confidence", 5)
        if r["edge"] >= APPROVE_EDGE:
            icon = "⚡" if r["edge"] >= BOLT_EDGE else "✅"
            color = "#10b981"
            approved_props.append(r["prop"])
        else:
            icon = "❌"
            color = "#ef4444"

        prop = r["prop"]
        st.markdown(
            f'<span style="color:{color}; font-weight:500;">{icon} {r["verdict"]} (Edge: {edge_pct:.1f}%, Confidence: {conf}/10) – {prop.get("player","?")} {prop.get("pick","?")} {prop.get("line","?")} {prop.get("market","?")}</span>',
            unsafe_allow_html=True,
        )
        if r.get("analysis") and r["analysis"].get("bolt_signal") == "SOVEREIGN BOLT":
            st.caption(f"   ⚡ SOVEREIGN BOLT – Kelly stake: ${r['analysis']['stake']:.2f}")
    return approved_props, len(approved_props)

# =============================================================================
# OCR & PARSER UTILITIES
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
    if m in {"XG","GOALS","ASSISTS"}: return "SOCCER"
    if m in {"ACES","DOUBLE_FAULTS","FIRST_SERVE_PCT"}: return "TENNIS"
    return None

def _dedupe(props: List[Dict]) -> List[Dict]:
    seen = {}
    for p in props:
        k = (p.get("player","").strip().upper(), p.get("market","").strip().upper(),
             float(p.get("line",0) or 0), p.get("pick","").strip().upper())
        if k not in seen: seen[k] = p
    return list(seen.values())

def _parse_mybookie_totals(lines: List[str]) -> List[Dict]:
    bets = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not re.match(r'^(Over|Under)\s+[\d\.]+', line, re.IGNORECASE):
            i += 1
            continue
        m = re.match(r'^(Over|Under)\s+([\d\.]+)', line, re.IGNORECASE)
        if not m:
            i += 1
            continue
        pick = m.group(1).upper()
        line_val = float(m.group(2))
        if i+1 >= n: break
        odds_line = lines[i+1].strip()
        odds_match = re.match(r'^[+-]?\d+$', odds_line)
        if not odds_match:
            i += 1
            continue
        odds = int(odds_line)
        if i+2 >= n: break
        desc_line = lines[i+2].strip()
        sport_line = lines[i+3].strip() if i+3 < n else ""
        sport = "NBA"
        if "NBA" in sport_line: sport = "NBA"
        elif "NHL" in sport_line: sport = "NHL"
        elif "MLB" in sport_line: sport = "MLB"
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
        i += 5
    return bets

def _parse_bovada_parlay(lines: List[str]) -> List[Dict]:
    for idx, ln in enumerate(lines):
        if "Parlay" in ln:
            result = None
            for rline in lines[:5]:
                if "Win" in rline: result = "WIN"; break
                elif "Loss" in rline: result = "LOSS"; break
            if not result: result = "PENDING"
            odds = None
            for ln2 in lines:
                if re.search(r'[+-]\d+', ln2) and "Risk" not in ln2 and "Winnings" not in ln2:
                    m2 = re.search(r'([+-]\d+)', ln2)
                    if m2: odds = int(m2.group(1)); break
            if odds is None: odds = -110
            bet = {
                "type": "PARLAY",
                "sport": "NBA",
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
            return [bet]
    return []

def _parse_prizepicks_props(lines: List[str]) -> List[Dict]:
    bets = []
    for line in lines:
        line = line.strip()
        m = re.match(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+([A-Z]+)$', line, re.IGNORECASE)
        if m:
            player = m.group(1).strip()
            pick = m.group(2).upper()
            line_val = float(m.group(3))
            market = m.group(4).upper()
            sport = "NBA"
            if market in ["KS", "SOG", "SAVES", "GOALS", "ASSISTS", "HITS"]:
                if market == "KS": sport = "MLB"
                else: sport = "NHL"
            elif market in ["XG", "GOALS", "ASSISTS"]:
                sport = "SOCCER"
            elif market in ["ACES", "DOUBLE_FAULTS", "FIRST_SERVE_PCT"]:
                sport = "TENNIS"
            bet = {
                "type": "PROP",
                "sport": sport,
                "player": player,
                "team": "",
                "opponent": "",
                "market": market,
                "line": line_val,
                "pick": pick,
                "odds": -110,
                "actual": None,
                "result": "PENDING"
            }
            bets.append(bet)
    return bets

def _parse_pp_blocks(lines: List[str]) -> List[Dict]:
    bets = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if i+5 >= n:
            i += 1
            continue
        team_pos = lines[i+1].strip() if i+1<n else ""
        matchup = lines[i+3].strip() if i+3<n else ""
        line_str = lines[i+4].strip() if i+4<n else ""
        market_r = lines[i+5].strip() if i+5<n else ""
        try:
            line_val = float(line_str)
        except Exception:
            i += 1
            continue
        if line_val <= 0 or line_val > 200:
            i += 1
            continue
        market = _norm_market(market_r)
        if len(market) < 2:
            i += 1
            continue
        window = lines[i:i+10]
        pick = _detect_pick(window) or "MORE"
        try:
            team_abbr = team_pos.split("-")[0].strip().upper()
        except Exception:
            team_abbr = ""
        opp_m = re.search(r'(vs|@)\s+([A-Z]{2,3})\b', matchup)
        opp = opp_m.group(2).upper() if opp_m else ""
        tag = ""
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
    bets = []
    i = 0
    n = len(lines)
    while i+10 < n:
        if not re.match(r'\d{1,2}/\d{1,2}/\d{2}', lines[i]):
            i += 1
            continue
        away = lines[i+2]
        home = lines[i+3]
        def _sp(l):
            m = re.search(r'([+-]\d+\.?\d*)\s*\(([+-]?\d+)\)', l)
            return (float(m.group(1)), int(m.group(2))) if m else None
        def _tot(l):
            m = re.search(r'([OU])(\d+\.?\d*)\s*\(([+-]?\d+)\)', l, re.IGNORECASE)
            return ("OVER" if m.group(1).upper()=="O" else "UNDER", float(m.group(2)), int(m.group(3))) if m else None
        def _ml(l):
            m = re.match(r'^([+-]\d+)$', l.strip())
            return int(m.group(1)) if m else None
        for side,team,opp,sl in [(away,away,home,lines[i+5]),(home,home,away,lines[i+6])]:
            sp = _sp(sl)
            if sp:
                bets.append({"type":"GAME","sport":"NBA","team":team,"opponent":opp,
                             "market":"SPREAD","line":sp[0],"pick":team,"odds":sp[1],"is_alt":False})
        for ml_line,team,opp in [(lines[i+7],away,home),(lines[i+8],home,away)]:
            ml = _ml(ml_line)
            if ml:
                bets.append({"type":"GAME","sport":"NBA","team":team,"opponent":opp,
                             "market":"ML","line":0.0,"pick":team,"odds":ml,"is_alt":False})
        for tl in (lines[i+9], lines[i+10]):
            tot = _tot(tl)
            if tot:
                pick,lv,ov = tot
                bets.append({"type":"GAME","sport":"NBA","team":home,"opponent":away,
                             "market":"TOTAL","line":lv,"pick":pick,"odds":ov,"is_alt":False})
        i += 11
    return bets
def _parse_mybookie(lines: List[str]) -> List[Dict]:
    bets = []
    i = 0
    n = len(lines)
    while i+8 < n:
        dl = lines[i+2] if i+2<n else ""
        if not re.search(r'\b[A-Za-z]{3}\s+\d{1,2}\s+\d{1,2}:\d{2}\s+[AP]M\b', dl):
            i += 1
            continue
        away = lines[i].split("-")[0].strip()
        home = lines[i+1].split("-")[0].strip()
        block = lines[i+4:i+13]
        j = 0
        while j < len(block)-1:
            l = block[j].strip()
            nxt = block[j+1].strip()
            if re.match(r'^[+-]\d+(\.\d+)?$', l) and re.match(r'^[+-]\d+$', nxt):
                side = "AWAY" if not any(b.get("market")=="SPREAD" and b.get("team")==home for b in bets) else "HOME"
                t,o = (away,home) if side=="AWAY" else (home,away)
                bets.append({"type":"GAME","sport":"MLB","team":t,"opponent":o,
                             "market":"SPREAD","line":float(l),"pick":t,"odds":int(nxt),"is_alt":False})
                j += 2
                continue
            m = re.match(r'^([OU])\s+(\d+(\.\d+)?)$', l, re.IGNORECASE)
            if m and re.match(r'^[+-]\d+$', nxt):
                ou = "OVER" if m.group(1).upper()=="O" else "UNDER"
                bets.append({"type":"GAME","sport":"MLB","team":home,"opponent":away,
                             "market":"TOTAL","line":float(m.group(2)),"pick":ou,"odds":int(nxt),"is_alt":False})
                j += 2
                continue
            if re.match(r'^[+-]\d+$', l):
                side = "AWAY" if not any(b.get("market")=="ML" and b.get("team")==home for b in bets) else "HOME"
                t,o = (away,home) if side=="AWAY" else (home,away)
                bets.append({"type":"GAME","sport":"MLB","team":t,"opponent":o,
                             "market":"ML","line":0.0,"pick":t,"odds":int(l),"is_alt":False})
                j += 1
                continue
            j += 1
        i += 10
    return bets

def parse_slip(text: str) -> List[Dict]:
    bets = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines: return bets

    bets.extend(_parse_mybookie_totals(lines))
    bets.extend(_parse_bovada_parlay(lines))
    bets.extend(_parse_prizepicks_props(lines))
    bets.extend(_parse_pp_blocks(lines))
    bets.extend(_parse_bovada(lines))
    bets.extend(_parse_mybookie(lines))

    for line in lines:
        m = re.match(r'^(.+?)\s+(OVER|UNDER)\s+([\d\.]+)\s+(\w+)$', line, re.IGNORECASE)
        if m:
            bets.append({
                "type":"PROP","player":m.group(1).strip(),
                "pick":m.group(2).upper(),"line":float(m.group(3)),
                "market":m.group(4).upper(),"sport":"NBA","odds":-110
            })

    bets = _dedupe(bets)
    PARSER_LOGGER.info(f"parse_slip: extracted {len(bets)} bets")
    return bets

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
# PROPLINE FETCH
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
    if not payout: return None
    return (1 / payout) ** (1 / num_picks)

def devig_multiplicative(probs: List[float]) -> List[float]:
    total = sum(probs)
    if total == 0: return probs
    return [p / total for p in probs]

def ev_percent(true_prob: float, decimal_odds: float) -> float:
    return (true_prob * (decimal_odds - 1)) - (1 - true_prob)

def american_to_decimal(odds: int) -> float:
    if odds > 0: return 1 + odds / 100
    return 1 + 100 / abs(odds)

def get_sharp_book(bookmakers: List[str]) -> Optional[str]:
    for book in SHARP_BOOKS_PRIORITY:
        if book in bookmakers: return book
    return bookmakers[0] if bookmakers else None

BASE_URL = "https://api.the-odds-api.com/v4"

def api_get(path: str, params: dict) -> Tuple[Optional[dict], dict]:
    params["apiKey"] = st.secrets.get("ODDS_API_KEY", "")
    if not params["apiKey"]: return None, {}
    full_url = f"{BASE_URL}{path}"
    try:
        r = requests.get(full_url, params=params, timeout=15)
        if r.status_code != 200: return None, r.headers
        return r.json(), r.headers
    except Exception: return None, {}

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

def analyze_ev_game_lines(games: List[Dict], sport_name: str, min_ev: float = 0.0) -> List[Dict]:
    results = []
    for game in games:
        home = game.get("home_team", ""); away = game.get("away_team", "")
        date = game.get("commence_time", "")[:10]
        books_by_name = {}
        for bm in game.get("bookmakers", []):
            books_by_name[bm["key"]] = {m["key"]: m["outcomes"] for m in bm.get("markets", [])}
        sharp_book = get_sharp_book(list(books_by_name.keys()))
        if not sharp_book: continue
        for market_key in ["h2h", "spreads", "totals"]:
            sharp_outcomes = books_by_name[sharp_book].get(market_key)
            if not sharp_outcomes: continue
            raw_probs = [american_to_prob(o["price"]) for o in sharp_outcomes]
            true_probs = devig_multiplicative(raw_probs)
            sharp_map = {}
            for o, tp in zip(sharp_outcomes, true_probs):
                key = (o.get("name", ""), o.get("point", None))
                sharp_map[key] = tp
            for bm_name, markets_dict in books_by_name.items():
                if bm_name == sharp_book: continue
                soft_outcomes = markets_dict.get(market_key)
                if not soft_outcomes: continue
                for outcome in soft_outcomes:
                    key = (outcome.get("name", ""), outcome.get("point", None))
                    true_p = sharp_map.get(key)
                    if true_p is None: continue
                    decimal = american_to_decimal(outcome["price"])
                    ev = ev_percent(true_p, decimal)
                    if ev < min_ev: continue
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

def analyze_ev_props(games: List[Dict], sport_key: str, sport_name: str, max_games: int = 5, min_ev: float = 0.01) -> List[Dict]:
    results = []
    for game in games[:max_games]:
        event_id = game["id"]
        home = game.get("home_team", ""); away = game.get("away_team", "")
        event_data = fetch_ev_event_props(sport_key, event_id)
        if not event_data: continue
        books_by_name = {}
        for bm in event_data.get("bookmakers", []):
            books_by_name[bm["key"]] = bm.get("markets", [])
        sharp_book = get_sharp_book(list(books_by_name.keys()))
        if not sharp_book: continue
        for market in books_by_name[sharp_book]:
            market_name = market["key"].replace("player_", "").replace("_", " ").upper()
            sharp_outcomes = market.get("outcomes", [])
            raw_probs = [american_to_prob(o["price"]) for o in sharp_outcomes]
            true_probs = devig_multiplicative(raw_probs)
            sharp_map = {}
            for o, tp in zip(sharp_outcomes, true_probs):
                key = (o.get("name", ""), o.get("description", ""), o.get("point", None))
                sharp_map[key] = tp
            for bm_name, markets_list in books_by_name.items():
                if bm_name == sharp_book: continue
                soft_market = next((m for m in markets_list if m["key"] == market["key"]), None)
                if not soft_market: continue
                for outcome in soft_market.get("outcomes", []):
                    key = (outcome.get("name", ""), outcome.get("description", ""), outcome.get("point", None))
                    true_p = sharp_map.get(key)
                    if true_p is None: continue
                    decimal = american_to_decimal(outcome["price"])
                    ev = ev_percent(true_p, decimal)
                    if ev < min_ev: continue
                    player = outcome.get("description", outcome.get("name", "UNKNOWN"))
                    line = outcome.get("point", "?")
                    side = outcome.get("name", "")
                    results.append({
                        "Sport": sport_name,
                        "Player": player,
                        "Prop": f"{market_name} {side} {line}",
                        "Game": f"{away} @ {home}",
                        "True Prob": f"{true_p*100:.1f}%",
                        "Soft Odds": f"{outcome['price']:+d}",
                        "EV": f"+{ev*100:.2f}%",
                        "_ev": ev,
                    })
    seen = set()
    unique = []
    for r in sorted(results, key=lambda x: x["_ev"], reverse=True):
        key = (r["Player"], r["Prop"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

# =============================================================================
# STREAMLIT UI – ALL TABS
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

def _color_edge(val: float) -> str:
    if val > 0.10: return "background-color: #10b981; color: white"
    if val > 0.05: return "background-color: #3b82f6; color: white"
    if val > 0.00: return "background-color: #f59e0b; color: black"
    return "background-color: #ef4444; color: white"

def _style_dataframe(df: pd.DataFrame, edge_col: str = "edge") -> pd.DataFrame:
    if edge_col not in df.columns: return df
    return df.style.map(lambda x: _color_edge(x), subset=[edge_col])

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
                            "OCR_SPACE_API_KEY","RAPIDAPI_KEY","PARLAY_API_KEY",
                            "WEATHER_API_KEY","SPORTMONKS_API_KEY") if not st.secrets.get(k)]
    if missing:
        st.sidebar.warning("Missing keys:\n" + "\n".join(f"• {k}" for k in missing))
    return new_br

def _tab_props(bankroll: float) -> None:
    st.header("🎯 Player Props")
    for k,v in [("p_sport","NBA"),("p_player","LeBron James"),("p_market","PTS"),
                ("p_line",25.5),("p_pick","OVER"),("p_odds",-110),("p_tier","mid")]:
        if k not in st.session_state: st.session_state[k] = v

    sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), index=0, key="p_sport")
    player = st.text_input("Player", value=st.session_state.p_player)
    if player != st.session_state.p_player: st.session_state.p_player = player
    mkts = SPORT_CATEGORIES.get(sport,["PTS"])
    midx = mkts.index(st.session_state.p_market) if st.session_state.p_market in mkts else 0
    market = st.selectbox("Market", mkts, index=midx)
    if market != st.session_state.p_market: st.session_state.p_market = market
    line = st.number_input("Line", value=st.session_state.p_line, step=0.5)
    if line != st.session_state.p_line: st.session_state.p_line = line
    c1,c2,c3 = st.columns(3)
    pick = c1.radio("Pick", ["OVER","UNDER"], horizontal=True,
                    index=0 if st.session_state.p_pick=="OVER" else 1)
    if pick != st.session_state.p_pick: st.session_state.p_pick = pick
    odds = c2.number_input("Odds", value=st.session_state.p_odds)
    if odds != st.session_state.p_odds: st.session_state.p_odds = odds
    tier = c3.selectbox("Player Tier", ["elite","mid","bench"], index=1)
    use_mc = st.checkbox("Use Monte Carlo (10,000 sims)", value=False)

    with st.expander("🌍 Environmental & Advanced Filters", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            role_change = st.checkbox("Role change (recent 3 games weight 2.0x)")
            blowout_prob = st.slider("Expected Blowout Probability", 0.0, 1.0, 0.1)
            is_playoff = st.checkbox("Playoff Game")
            injury_status = st.selectbox("Injury Status", ["HEALTHY","PROBABLE","QUESTIONABLE","DAY_TO_DAY","OUT","GTD"])
            injury_type = st.selectbox("Injury Type", ["", "ANKLE", "KNEE", "SHOULDER", "BACK", "CONCUSSION", "STRAIN"])
            minutes_list_input = st.text_input("Minutes per game (comma separated, e.g., 32,30,28,26)", "")
            if minutes_list_input:
                try: minutes_list = [float(x.strip()) for x in minutes_list_input.split(",")]
                except: minutes_list = None
            else: minutes_list = None
            usage_trend_up = st.checkbox("Usage trend up (>5% last 4 games)")
            matchup_delta_input = st.number_input("Matchup Delta (Δ)", value=0.0, step=0.01)
        with col2:
            b2b = st.checkbox("Back‑to‑Back Game")
            player_role = st.selectbox("Player Role", ["STARTER", "ROTATION", "BENCH"])
            is_home = st.checkbox("Home Game", value=True)
            travel_zones = st.slider("Travel Zones Crossed", 0, 3, 0)
            rest_days = st.slider("Rest Days since last game", 0, 3, 2)
            direction = st.selectbox("Travel Direction", ["none", "west_to_east", "east_to_west"])
            altitude_city = st.selectbox("Arena City (Altitude)", ["", "Denver", "Salt Lake City", "Phoenix", "Los Angeles", "Boston"])
            weather_source = st.radio("Weather Source", ["Auto (API)", "Manual"])
            if weather_source == "Manual":
                wind_mph = st.number_input("Wind (mph)", value=0.0, step=1.0)
                temp_f = st.number_input("Temperature (°F)", value=70)
                precip = st.selectbox("Precipitation", ["clear", "rain", "snow"])
            else:
                wind_mph = 0.0
                temp_f = 70
                precip = "clear"
            is_elimination = st.checkbox("Elimination Game")
            contract_incentive = st.checkbox("Contract Incentive")
            if sport == "MLB" and market.upper() in ["KS", "STRIKEOUTS"]:
                umpire_overturn = st.slider("Umpire Overturn Rate", 0.0, 1.0, 0.5)
            else:
                umpire_overturn = 0.0
            if is_playoff:
                series_state = st.selectbox("Playoff Series State", ["tied", "down", "up"])
                is_star = st.checkbox("Star Player (usage >28%)")
            else:
                series_state = "tied"
                is_star = False
            public_pct = st.slider("Public Betting % (on this side)", 0.0, 1.0, 0.5)
            line_movement = st.number_input("Line Movement (points)", value=0.0, step=0.5)

    if st.button("🚀 Analyze Prop", type="primary"):
        if weather_source == "Auto (API)":
            wind_mph = 0
            temp_f = 70
            precip = "clear"
        with st.spinner("Running upgraded model..."):
            res = analyze_prop(
                player, market, line, pick, sport, int(odds), bankroll, tier,
                use_mc, 10000, role_change, None, None, minutes_list,
                injury_status, injury_type, blowout_prob, is_playoff,
                matchup_delta_input if matchup_delta_input != 0 else None,
                usage_trend_up,
                b2b=b2b, player_role=player_role, is_home=is_home,
                travel_zones=travel_zones, rest_days=rest_days, direction=direction,
                altitude_city=altitude_city, elevation_ft=0,
                wind_mph=wind_mph, temp_f=temp_f, precip=precip,
                public_pct=public_pct, line_movement=line_movement,
                is_elimination=is_elimination, contract_incentive=contract_incentive,
                umpire_overturn=umpire_overturn,
                series_state=series_state, is_star=is_star
            )
        if res.get("error"):
            st.error(f"❌ {res['error']}")
        else:
            c1,c2,c3,c4 = st.columns(4)
            _metric_row([c1,c2,c3,c4],[
                ("Win Prob", f"{res['prob']:.1%}"),
                ("Edge",     f"{res['edge']:+.1%}"),
                ("Kelly ($)",f"${res['stake']:.2f}"),
                ("Fair Line",f"{res['fair_line']:.1f}"),
            ])
            st.markdown(f"Confidence: {confidence_score(len(res['stats']))}/10", unsafe_allow_html=True)
            st.markdown(_badge(res["bolt_signal"]), unsafe_allow_html=True)
            st.info(f"Strictness: {res['strictness']} | CV: {res['cv']:.2f} | Vol Mult: {res['vol_mult']:.2f}")
            if res["bolt_signal"] in ("SOVEREIGN BOLT","ELITE LOCK","APPROVED"):
                st.success(f"{res['bolt_signal']}  —  {pick} {line} {market}  @  {odds}")
            else:
                st.error("PASS — Insufficient edge for this bet.")
            st.line_chart(pd.DataFrame({"Game":range(1,len(res["stats"])+1),
                                        market: res["stats"]}).set_index("Game"))
            if res.get("alternatives"):
                st.subheader("Alternative Lines")
                st.dataframe(pd.DataFrame(res["alternatives"]))
            if st.button("➕ Add to Slip Tracker"):
                insert_slip({
                    "type":"PROP","sport":sport,"player":player,"team":"","opponent":"",
                    "market":market,"line":line,"pick":pick,"odds":int(odds),
                    "edge":res["edge"],"prob":res["prob"],"kelly":res["kelly"],
                    "tier":res["tier"],"bolt_signal":res["bolt_signal"],"bankroll":bankroll,
                    "strictness":res["strictness"],"cv":res["cv"],"minutes_vol":0,"blowout_prob":blowout_prob,
                    "b2b":1 if b2b else 0, "travel_zones":travel_zones,
                    "altitude_ft":ARENA_ELEVATIONS.get(altitude_city,0),
                    "wind_mph":wind_mph, "temp_f":temp_f, "precip":precip,
                    "rlm_detected":1 if abs(line_movement)>1.5 else 0,
                    "injury_status":injury_status
                })
                st.success("Added!")
                st.toast("Slip logged", icon="➕")

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
            with st.expander("Show Live Props Data", expanded=False):
                st.dataframe(show_df, use_container_width=True)

    with st.expander("📋 Scan a Prop Slip (Text or Screenshot) – Pre‑bet Analysis", expanded=False):
        st.markdown("Paste a prop line or upload screenshots -- CLARITY will extract and analyze pending props.")
        scan_text = st.text_area("📋 Paste prop slip text", height=150, placeholder="e.g., LeBron James OVER 25.5 PTS\nor full PrizePicks block")
        parsed_props = []
        if scan_text:
            parsed_props = parse_slip(scan_text)
            if parsed_props:
                st.success(f"Found {len(parsed_props)} props.")
                for prop in parsed_props:
                    st.write(f"**Parsed:** {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                if st.button("🔍 Analyze All Props", key="batch_analyze_text"):
                    with st.spinner(f"Analyzing {len(parsed_props)} props..."):
                        batch_results = analyze_props_batch(parsed_props, sport, bankroll)
                        st.subheader("Analysis Results:")
                        approved, count = display_batch_results(batch_results)
                        if approved and st.button(f"➕ Add {count} Approved Props to Slip", key="batch_add_text"):
                            for prop in approved:
                                res = analyze_prop(
                                    prop.get("player",""), prop.get("market","PTS"),
                                    float(prop.get("line",0)), prop.get("pick","OVER"),
                                    sport, int(prop.get("odds",-110)), bankroll
                                )
                                if res.get("error"): continue
                                insert_slip({
                                    "type":"PROP","sport":sport,
                                    "player":prop.get("player",""),"team":"","opponent":"",
                                    "market":prop.get("market",""),"line":float(prop.get("line",0)),
                                    "pick":prop.get("pick","OVER"),"odds":int(prop.get("odds",-110)),
                                    "edge":res["edge"],"prob":res["prob"],"kelly":res["kelly"],
                                    "tier":res["tier"],"bolt_signal":res["bolt_signal"],"bankroll":bankroll,
                                })
                            st.success(f"Added {count} approved props to slip!")
                            st.toast(f"{count} props added", icon="➕")
                            st.rerun()
            else:
                st.info("No props detected.")

        uploaded_files = st.file_uploader("Or upload screenshot(s)", type=["png","jpg","jpeg","webp"], accept_multiple_files=True)
        if uploaded_files:
            all_img_props = []
            for img_file in uploaded_files:
                props = parse_image_props(img_file.getvalue())
                if props:
                    st.write(f"**{img_file.name}:** {len(props)} props detected")
                    for prop in props:
                        st.write(f"  • {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                        all_img_props.append(prop)
                else:
                    st.write(f"**{img_file.name}:** No props detected")
            if all_img_props and st.button("🔍 Analyze All Props from Images", key="batch_analyze_images"):
                with st.spinner(f"Analyzing {len(all_img_props)} props..."):
                    batch_results = analyze_props_batch(all_img_props, sport, bankroll)
                    st.subheader("Analysis Results:")
                    approved, count = display_batch_results(batch_results)
                    if approved and st.button(f"➕ Add {count} Approved Props to Slip", key="batch_add_images"):
                        for prop in approved:
                            res = analyze_prop(
                                prop.get("player",""), prop.get("market","PTS"),
                                float(prop.get("line",0)), prop.get("pick","OVER"),
                                sport, int(prop.get("odds",-110)), bankroll
                            )
                            if res.get("error"): continue
                            insert_slip({
                                "type":"PROP","sport":sport,
                                "player":prop.get("player",""),"team":"","opponent":"",
                                "market":prop.get("market",""),"line":float(prop.get("line",0)),
                                "pick":prop.get("pick","OVER"),"odds":int(prop.get("odds",-110)),
                                "edge":res["edge"],"prob":res["prob"],"kelly":res["kelly"],
                                "tier":res["tier"],"bolt_signal":res["bolt_signal"],"bankroll":bankroll,
                            })
                        st.success(f"Added {count} approved props to slip!")
                        st.toast(f"{count} props added", icon="➕")
                        st.rerun()

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
                st.toast(f"Loaded {len(games)} games", icon="🎮")
            else:
                st.warning(f"No games found for {sport}. Check ODDS_API_KEY.")
    games = st.session_state.get("fetched_games", [])
    if not games:
        st.info("Click 'Fetch Today's Games' to load matchups.")
    else:
        labels = [f"{g.get('away_team','?')} @ {g.get('home_team','?')}  ({g.get('commence_time','')[:10]})"
                  for g in games]
        idx = st.selectbox("Select Game", range(len(labels)), format_func=lambda i: labels[i])
        g = games[idx]
        home, away = g.get("home_team",""), g.get("away_team","")
        st.subheader(f"{away} @ {home}")
        c1, c2, c3 = st.columns(3)
        spread = g.get("spread")
        home_spread_odds = g.get("home_spread_odds")
        away_spread_odds = g.get("away_spread_odds")
        home_ml = g.get("home_ml")
        away_ml = g.get("away_ml")
        total = g.get("total")
        over_odds = g.get("over_odds")
        under_odds = g.get("under_odds")
        with c1:
            st.markdown("**Spread**")
            st.write(f"Line: {spread:+.1f}" if spread is not None else "—")
            st.write(f"Home odds: {home_spread_odds}" if home_spread_odds else "")
            st.write(f"Away odds: {away_spread_odds}" if away_spread_odds else "")
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
            if spread is not None and home_spread_odds is not None and away_spread_odds is not None:
                with st.spinner("Analyzing…"):
                    res = analyze_spread(home, away, sport, spread, int(home_spread_odds), int(away_spread_odds))
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
                    st.success(f"⚡ SOVEREIGN BOLT — {home} {spread:+.1f} @ {home_spread_odds}")
                if res["away_bolt"] == "SOVEREIGN BOLT":
                    st.success(f"⚡ SOVEREIGN BOLT — {away} {-spread:+.1f} @ {away_spread_odds}")
            else:
                st.error("No spread data for this game.")
        if b2.button("🔍 Analyze Total", use_container_width=True):
            if total is not None and over_odds is not None and under_odds is not None:
                wind_mph = 0
                temp_f = 70
                precip = "clear"
                with st.spinner("Analyzing…"):
                    res = analyze_total(home, away, sport, total, int(over_odds), int(under_odds),
                                        wind_mph=wind_mph, temp_f=temp_f, precip=precip)
                st.subheader("Total")
                c1, c2 = st.columns(2)
                c1.metric("Over Prob", f"{res['over_prob']:.1%}")
                c1.metric("Over Edge", f"{res['over_edge']:+.1%}")
                c1.markdown(_badge(res["over_bolt"]), unsafe_allow_html=True)
                c2.metric("Under Prob", f"{res['under_prob']:.1%}")
                c2.metric("Under Edge", f"{res['under_edge']:+.1%}")
                c2.markdown(_badge(res["under_bolt"]), unsafe_allow_html=True)
                st.caption(f"Projected total: {res['projection']:.1f}")
                if res["over_bolt"] == "SOVEREIGN BOLT":
                    st.success(f"⚡ SOVEREIGN BOLT — OVER {total} @ {over_odds}")
                if res["under_bolt"] == "SOVEREIGN BOLT":
                    st.success(f"⚡ SOVEREIGN BOLT — UNDER {total} @ {under_odds}")
            else:
                st.error("No total data for this game.")
        if b3.button("🔍 Analyze Moneyline", use_container_width=True):
            if home_ml is not None and away_ml is not None:
                with st.spinner("Analyzing…"):
                    res = analyze_ml(home, away, sport, int(home_ml), int(away_ml))
                st.subheader("Moneyline")
                c1, c2 = st.columns(2)
                c1.metric(f"{home} Win", f"{res['home_prob']:.1%}")
                c1.metric("Edge", f"{res['home_edge']:+.1%}")
                c1.markdown(_badge(res["home_bolt"]), unsafe_allow_html=True)
                c2.metric(f"{away} Win", f"{res['away_prob']:.1%}")
                c2.metric("Edge", f"{res['away_edge']:+.1%}")
                c2.markdown(_badge(res["away_bolt"]), unsafe_allow_html=True)
                if res["home_bolt"] == "SOVEREIGN BOLT":
                    st.success(f"⚡ SOVEREIGN BOLT — {home} ML @ {home_ml}")
                if res["away_bolt"] == "SOVEREIGN BOLT":
                    st.success(f"⚡ SOVEREIGN BOLT — {away} ML @ {away_ml}")
            else:
                st.error("No moneyline data for this game.")

    with st.expander("✍️ Manual Game Input", expanded=False):
        st.markdown("Enter your own game lines for analysis (useful for testing or unsupported sports).")
        with st.form("manual_game_form"):
            col1, col2 = st.columns(2)
            with col1:
                home_team_man = st.text_input("Home Team", "Home")
                away_team_man = st.text_input("Away Team", "Away")
                spread_man = st.number_input("Spread (home team)", value=0.0, step=0.5)
                home_spread_odds_man = st.number_input("Home Spread Odds", value=-110)
                away_spread_odds_man = st.number_input("Away Spread Odds", value=-110)
            with col2:
                total_man = st.number_input("Total (O/U)", value=220.0, step=0.5)
                over_odds_man = st.number_input("Over Odds", value=-110)
                under_odds_man = st.number_input("Under Odds", value=-110)
                home_ml_man = st.number_input("Home Moneyline", value=-110)
                away_ml_man = st.number_input("Away Moneyline", value=-110)
            submitted = st.form_submit_button("Analyze Manual Game")
            if submitted:
                st.subheader("Manual Game Analysis")
                if spread_man != 0.0 and home_spread_odds_man and away_spread_odds_man:
                    res_s = analyze_spread(home_team_man, away_team_man, sport, spread_man,
                                           int(home_spread_odds_man), int(away_spread_odds_man))
                    st.markdown(f"**Spread** – {home_team_man} {spread_man:+.1f}")
                    st.write(f"{home_team_man} cover: {res_s['home_cover_prob']:.1%} | Edge: {res_s['home_edge']:+.1%}")
                    st.markdown(_badge(res_s["home_bolt"]), unsafe_allow_html=True)
                    st.write(f"{away_team_man} cover: {res_s['away_cover_prob']:.1%} | Edge: {res_s['away_edge']:+.1%}")
                if total_man > 0:
                    res_t = analyze_total(home_team_man, away_team_man, sport, total_man, int(over_odds_man), int(under_odds_man))
                    st.markdown(f"**Total** – {total_man}")
                    st.write(f"Over: {res_t['over_prob']:.1%} (Edge {res_t['over_edge']:+.1%})")
                    st.write(f"Under: {res_t['under_prob']:.1%} (Edge {res_t['under_edge']:+.1%})")
                if home_ml_man != 0 and away_ml_man != 0:
                    res_m = analyze_ml(home_team_man, away_team_man, sport, int(home_ml_man), int(away_ml_man))
                    st.markdown(f"**Moneyline**")
                    st.write(f"{home_team_man}: {res_m['home_prob']:.1%} (Edge {res_m['home_edge']:+.1%})")
                    st.write(f"{away_team_man}: {res_m['away_prob']:.1%} (Edge {res_m['away_edge']:+.1%})")

def _tab_best_bets() -> None:
    st.header("🏆 Best Bets — Automated Recommendations")
    st.caption("Top player props and game bets ranked by CLARITY edge model")

    if st.session_state.get("last_update") is None:
        st.info("👆 No data loaded. Click 'Refresh All Data' below to fetch the latest lines and projections.")

    with st.expander("⚙️ Filter Settings", expanded=False):
        fc1, fc2 = st.columns(2)
        min_edge = fc1.slider("Min Edge (%)", 0.0, 15.0, 2.0, 0.5) / 100.0
        max_props = fc1.slider("Max Player Props", 3, 15, 6)
        max_games = fc2.slider("Max Game Bets", 3, 15, 6)
        use_kelly = fc2.checkbox("Kelly Sizing", value=True)
        kelly_cap_pct = fc2.slider("Kelly Cap (% bankroll)", 1, 25, 10) / 100.0 if use_kelly else 1.0

    if st.button("🔄 Refresh All Data", type="primary"):
        with st.spinner("Refreshing lines and projections…"):
            try:
                dk_df = fetch_dk_dataframe()
                projs = build_today_projections_auto()
                priced = evaluate_all_bets(dk_df, projs)
                st.session_state["player_bets"] = priced
                st.session_state["player_bets_df"] = priced_bets_to_dataframe(priced)
                games = game_scanner.fetch(["NBA"], days=0)
                st.session_state["game_bets"] = analyze_game_bets(games, "NBA", 0.0)
                st.session_state["last_update"] = datetime.now()
                st.success("Data refreshed ✅")
                st.toast("Best bets refreshed", icon="🔄")
                st.rerun()
            except Exception as e:
                st.error(f"Refresh error: {e}")

    last_update = st.session_state.get("last_update")
    if last_update and isinstance(last_update, datetime):
        st.caption(f"Last scan: {last_update.strftime('%H:%M:%S')}")

    player_bets_df = st.session_state.get("player_bets_df", pd.DataFrame())
    if not player_bets_df.empty:
        filtered_p = player_bets_df[player_bets_df["edge"] >= min_edge].sort_values("edge", ascending=False).head(max_props)
        if not filtered_p.empty:
            st.subheader(f"🏀 Top {len(filtered_p)} Player Props")
            br = get_bankroll()
            filtered_p["Stake $"] = filtered_p["kelly"].apply(lambda k: f"${min(k * br, br * kelly_cap_pct):.0f}" if use_kelly else "$100")
            display_cols = ["player","market","line","odds","edge","prob","confidence","Stake $"]
            styled_df = _style_dataframe(filtered_p[display_cols], "edge")
            st.dataframe(styled_df, use_container_width=True)

            sel_p = st.multiselect(
                "Select player props to add", filtered_p.index,
                format_func=lambda i: f"{filtered_p.loc[i,'player']} {filtered_p.loc[i,'market']} OVER {filtered_p.loc[i,'line']} (edge {filtered_p.loc[i,'edge']:.1%}, conf {filtered_p.loc[i,'confidence']}/10)"
            )
            if st.button("➕ Add Selected Player Props"):
                for i in sel_p:
                    row = filtered_p.loc[i]
                    insert_slip({
                        "type":"PROP","sport":"NBA",
                        "player":row["player"],"team":"","opponent":"",
                        "market":row["market"],"line":row["line"],"pick":"OVER",
                        "odds":int(row["odds"]),"edge":row["edge"],"prob":row["prob"],
                        "kelly":row["kelly"],"tier":"BEST BET",
                        "bolt_signal":"", "bankroll":get_bankroll(),
                    })
                st.success(f"Added {len(sel_p)} player props.")
                st.toast(f"{len(sel_p)} props added", icon="➕")
                st.rerun()
        else:
            st.info(f"No player props above {min_edge*100:.1f}% edge threshold.")
    elif st.session_state.get("last_update") is None:
        st.info("No player prop data yet. Click 'Refresh All Data' to load.")
    else:
        st.info("No player props available.")

    st.divider()

    game_bets = st.session_state.get("game_bets", [])
    if game_bets:
        filtered_g = sorted([b for b in game_bets if b["edge"] >= min_edge],
                            key=lambda x: x["edge"], reverse=True)[:max_games]
        if filtered_g:
            st.subheader(f"🏟️ Top {len(filtered_g)} Game Bets (Spread / Total / ML)")
            df_g = pd.DataFrame(filtered_g)
            br = get_bankroll()
            df_g["Stake $"] = df_g["edge"].apply(
                lambda e: f"${min(e*0.25*br, br*kelly_cap_pct):.0f}" if use_kelly else "$100"
            )
            display_cols = ["type","team","opponent","line","odds","edge","prob",
                           "fair_line","pick","Stake $"]
            styled_df = _style_dataframe(df_g[display_cols], "edge")
            st.dataframe(styled_df, use_container_width=True)

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
                st.success(f"Added {len(sel_g)} game bets.")
                st.toast(f"{len(sel_g)} game bets added", icon="➕")
                st.rerun()
        else:
            st.info(f"No game bets above {min_edge*100:.1f}% edge threshold.")
    elif st.session_state.get("last_update") is None:
        st.info("No game bet data yet. Click 'Refresh All Data' to load.")
    else:
        st.info("No game bets available.")

    st.divider()

    st.subheader("🎲 Auto Parlay Generator")
    max_legs_par = st.slider("Max legs", 2, 6, 4, key="par_legs")
    min_parlay_edge = st.slider("Min edge per leg (%)", 0.0, 10.0, 2.0, 0.5) / 100.0
    if st.button("⚡ Generate Parlays from Top Props"):
        raw_bets = st.session_state.get("player_bets", [])
        if raw_bets:
            parlays = generate_parlays(raw_bets, max_legs=max_legs_par, top_n=5, min_edge=min_parlay_edge)
            if parlays:
                st.session_state["parlays"] = parlays
                st.success(f"Generated {len(parlays)} parlays.")
                st.toast(f"{len(parlays)} parlays generated", icon="🎲")
            else:
                st.warning("Not enough qualifying bets for parlays. Lower edge threshold or refresh data.")
        else:
            st.warning("No player bets available. Refresh data first.")

    for i, p in enumerate(st.session_state.get("parlays",[])):
        with st.expander(
            f"Parlay #{i+1} — {p['num_legs']} legs | "
            f"Edge: {p['total_edge']:.2%} | "
            f"Confidence: {p['confidence']:.1%} | "
            f"Est. odds: +{p['estimated_odds']}"
        ):
            for leg in p["legs"]:
                st.markdown(f"• {leg}")
            if st.button(f"➕ Add Parlay #{i+1} to Slip", key=f"padd_{i}"):
                insert_slip({
                    "type":"PARLAY","sport":"NBA",
                    "edge":p["total_edge"],"prob":p["confidence"],
                    "odds":p["estimated_odds"],"tier":"PARLAY",
                    "bolt_signal":"PARLAY","bankroll":get_bankroll(),
                    "notes":"\n".join(p["legs"]),
                })
                st.success(f"Parlay #{i+1} logged.")
                st.toast(f"Parlay #{i+1} added", icon="🎲")
                st.rerun()

def _tab_slip_lab() -> None:
    st.header("📋 Slip Settlement")
    st.caption("Record already settled bets from text or manually.")

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
                    for bet_idx, bet in enumerate(bets):
                        line_val = bet.get("line", 0)
                        pick = bet.get("pick", "").upper()
                        if not pick or pick not in ["OVER", "UNDER"]:
                            pick = manual_pick
                        odds = bet.get("odds", -110)
                        actual = None
                        if bet.get("type") == "GAME" and bet.get("market") == "TOTAL":
                            home_team = bet.get("team", "")
                            away_team = bet.get("opponent", "")
                            if home_team and away_team:
                                hs, as_ = fetch_final_score_espn(home_team, away_team)
                                if hs is not None and as_ is not None:
                                    actual = hs + as_
                                    st.info(f"Auto‑fetched total for {home_team} vs {away_team}: {actual}")
                        if actual is None:
                            actual = st.number_input(
                                f"Actual stat for {bet.get('player', bet.get('market', '?'))} (line {line_val})",
                                value=line_val, step=0.5, key=f"actual_{bet_idx}_{settled_count}"
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

    with st.expander("➕ Manually Record a Single Bet (already settled)", expanded=False):
        with st.form("manual_bet_form"):
            col1, col2, col3 = st.columns(3)
            with col1:
                player = st.text_input("Player Name", "LeBron James")
                market = st.selectbox("Market", ["PTS", "REB", "AST", "PRA", "PR", "PA"])
                line = st.number_input("Line", value=25.5, step=0.5)
            with col2:
                pick = st.selectbox("Pick", ["OVER", "UNDER"])
                odds = st.number_input("American Odds", value=-110)
                result = st.selectbox("Result", ["WIN", "LOSS", "PUSH"])
            with col3:
                actual = st.number_input("Actual Stat", value=0.0, step=0.1)
                date_settled = st.date_input("Date Settled", value=datetime.now().date())
            submitted = st.form_submit_button("Record Bet")
            if submitted:
                if result == "WIN":
                    profit = (odds / 100) * 100 if odds > 0 else (100 / abs(odds)) * 100
                elif result == "LOSS":
                    profit = -100
                else:
                    profit = 0
                insert_slip({
                    "type": "PROP",
                    "sport": "NBA",
                    "player": player,
                    "market": market,
                    "line": line,
                    "pick": pick,
                    "odds": int(odds),
                    "edge": 0.0,
                    "prob": 0.5,
                    "kelly": 0.0,
                    "tier": "MANUAL",
                    "bolt_signal": "MANUAL",
                    "result": result,
                    "actual": actual,
                    "profit": profit,
                    "settled_date": date_settled.strftime("%Y-%m-%d"),
                    "bankroll": get_bankroll(),
                })
                st.success(f"Bet recorded: {player} {pick} {line} {market} → {result}")
                st.toast("Bet recorded", icon="📝")
                st.rerun()

def _tab_history() -> None:
    st.header("📊 History & Accuracy Metrics")
    df = get_all_slips(500)
    dash = accuracy_dashboard()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Win Rate", f"{dash['win_rate']}%")
    c2.metric("ROI", f"{dash['roi']}%")
    c3.metric("Units Profit", str(dash['units_profit']))
    c4.metric("SEM Score", str(dash['sem_score']))

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
        with st.expander("Show All Bets", expanded=False):
            styled_df = _style_dataframe(df, "edge")
            st.dataframe(styled_df, use_container_width=True)
        pending = df[df["result"]=="PENDING"]
        if not pending.empty:
            st.subheader("Settle Pending Bets")
            slip_id = st.selectbox("Slip ID", pending["id"].tolist())
            sel_row = pending[pending["id"]==slip_id].iloc[0]
            actual = st.number_input("Actual Result", value=0.0, step=0.1)
            res_pick = st.radio("Outcome", ["WIN","LOSS","PUSH"], horizontal=True)
            if st.button("Settle Bet"):
                update_slip_result(slip_id, res_pick, actual, int(sel_row.get("odds",-110)))
                st.success("Settled!")
                st.toast("Bet settled", icon="✅")
                st.rerun()
    else:
        st.info("No bets recorded yet. Use the 'Manually Record a Single Bet' expander in Slip Lab to add your past bets.")

def _tab_model(bankroll: float) -> None:
    st.header("🤖 Model-Priced Bets (DraftKings)")
    use_mc = st.toggle("Monte Carlo mode (10,000 sims/player)", value=False)
    if st.button("Fetch DraftKings Lines", type="primary"):
        with st.spinner("Fetching DK lines..."):
            dk_df = fetch_dk_dataframe()
        if dk_df.empty:
            st.warning("No DraftKings lines fetched.")
            return
        st.success(f"{len(dk_df)} lines fetched.")
        with st.expander("Show DraftKings Lines", expanded=False):
            st.dataframe(dk_df.head(20), use_container_width=True)
        player_cols = dk_df[dk_df["market_type"].str.startswith("player")]
        players = player_cols["team_or_player"].unique().tolist() if not player_cols.empty else []
        if players:
            st.subheader("Priced Bets")
            results = []
            with st.spinner(f"Pricing {len(players)} players..."):
                for _, row in player_cols.iterrows():
                    pname = row.get("team_or_player","")
                    mtype = row.get("market_type","")
                    sb_line = float(row.get("line",0))
                    if not pname or not mtype or sb_line <= 0: continue
                    market = mtype.replace("player_", "").upper()
                    stats = fetch_stats(pname, market, tier="mid")
                    mu = role_change_weighted_wma(stats) if stats else sb_line * 1.02
                    sigma = max(compute_wsem(stats) * l42_buffer(stats), 0.75) if len(stats) >= 4 else max(1.5, mu * 0.25)
                    p_over = 1 - norm.cdf(sb_line, mu, sigma)
                    imp = american_to_prob(int(row.get("price", -110)))
                    edge = p_over - imp
                    kelly_frac = calculate_kelly_stake(bankroll, p_over, int(row.get("price", -110))) / bankroll if bankroll > 0 else 0
                    results.append({
                        "Player": pname,
                        "Market": mtype,
                        "Line": sb_line,
                        "Fair Line": round(mu, 2),
                        "P(over)": round(p_over, 3),
                        "Edge": round(edge, 3),
                        "Kelly": round(kelly_frac, 3),
                        "Tier": classify_tier(edge),
                        "Confidence": confidence_score(len(stats)),
                    })
            if results:
                rdf = pd.DataFrame(results).sort_values("Edge", ascending=False)
                styled_df = _style_dataframe(rdf, "Edge")
                st.dataframe(styled_df, use_container_width=True)
                good = rdf[rdf["Tier"].isin(["SOVEREIGN BOLT","ELITE LOCK","APPROVED"])]
                if not good.empty:
                    st.success(f"{len(good)} edges found worth watching.")
            else:
                st.info("No priceable bets in the current DK data.")
        else:
            st.info("No player prop lines found in the DK feed. Check DK endpoint or try later.")

def _tab_ev_scanner() -> None:
    st.header("🎲 +EV Scanner (Market-Based)")
    st.caption("Finds +EV opportunities by devigging sharp books (Pinnacle → DK → FD) and comparing to soft books or PrizePicks break‑even.")
    col1, col2, col3 = st.columns(3)
    with col1:
        min_ev_percent = st.slider("Minimum EV %", 0.0, 20.0, 1.0, 0.5) / 100.0
    with col2:
        selected_sport = st.selectbox("Sport", list(SPORTS.keys()), index=0)
    with col3:
        scan_props = st.checkbox("Include Props", value=True)
    if st.button("🔄 Scan for +EV Opportunities", type="primary"):
        with st.spinner(f"Scanning {selected_sport} lines and props..."):
            sport_key = SPORTS[selected_sport]
            games_data = fetch_ev_game_lines(sport_key)
            if not games_data:
                st.warning(f"No games data for {selected_sport}. Check ODDS_API_KEY.")
            else:
                ev_games = analyze_ev_game_lines(games_data, selected_sport, min_ev=min_ev_percent)
                st.session_state["ev_game_lines"] = ev_games
                if scan_props:
                    ev_props = analyze_ev_props(games_data, sport_key, selected_sport, max_games=5, min_ev=min_ev_percent)
                    st.session_state["ev_props"] = ev_props
                else:
                    st.session_state["ev_props"] = []
                st.session_state["ev_last_update"] = datetime.now()
                st.success(f"Found {len(ev_games)} +EV game lines and {len(st.session_state['ev_props'])} +EV props.")
                st.toast("EV scan completed", icon="🎲")
    if st.session_state.get("ev_last_update"):
        st.caption(f"Last scan: {st.session_state['ev_last_update'].strftime('%Y-%m-%d %H:%M:%S')}")
    ev_games = st.session_state.get("ev_game_lines", [])
    if ev_games:
        st.subheader(f"📈 +EV Game Lines ({len(ev_games)} found)")
        df_games = pd.DataFrame([{k:v for k,v in g.items() if not k.startswith("_")} for g in ev_games[:20]])
        st.dataframe(df_games, use_container_width=True)
    else:
        st.info("No +EV game lines found. Click the scan button above.")
    st.divider()
    ev_props = st.session_state.get("ev_props", [])
    if ev_props:
        st.subheader(f"🎯 +EV PrizePicks Props ({len(ev_props)} found)")
        df_props = pd.DataFrame([{k:v for k,v in p.items() if not k.startswith("_")} for p in ev_props[:25]])
        st.dataframe(df_props, use_container_width=True)
        st.caption("Look up these props manually on PrizePicks. The 'Best Slip' column suggests the optimal parlay size.")
    else:
        st.info("No +EV props found. Click the scan button above.")

def _tab_tools() -> None:
    st.header("⚙️ Tools & Diagnostics")
    st.subheader("🔌 API Health Detail")
    _init_health()
    cols = st.columns(2)
    for i, (svc, info) in enumerate(st.session_state.health.items()):
        ok = info.get("ok")
        ico = "🟢" if ok else "🔴" if ok is False else "⚪"
        msg = f"{ico} **{svc}**"
        if info.get("fallback"):
            msg += " (fallback)"
        if info.get("err"):
            msg += f"\n   ⚠️ {info['err'][:80]}"
        cols[i%2].markdown(msg)

    st.subheader("🔍 On-Demand Tests")
    c1, c2, c3 = st.columns(3)
    if c1.button("Test NBA API"):
        with st.spinner("Testing NBA API..."):
            vals = _nba_stats("LeBron James","PTS")
        if vals:
            st.success(f"NBA OK: {vals[:3]}")
            st.toast("NBA API is working", icon="✅")
        else:
            st.error("NBA failed.")
            st.toast("NBA API failed", icon="❌")
    if c2.button("Test PropLine"):
        with st.spinner("Testing PropLine..."):
            sports = propline_get_sports()
        if sports:
            st.success(f"PropLine OK: {len(sports)} sports")
            st.toast("PropLine is working", icon="✅")
        else:
            st.error("PropLine failed.")
            st.toast("PropLine failed", icon="❌")
    if c3.button("Test DraftKings"):
        with st.spinner("Testing DraftKings..."):
            df = fetch_dk_dataframe()
        if not df.empty:
            st.success(f"DK OK: {len(df)} lines")
            st.toast("DraftKings is working", icon="✅")
        else:
            st.error("DK fetch failed.")
            st.toast("DraftKings failed", icon="❌")

    st.subheader("📜 Recent Error Log")
    try:
        if os.path.exists("clarity_debug.log"):
            with open("clarity_debug.log") as f:
                errs = [l for l in f.readlines() if "ERROR" in l][-5:]
            if errs:
                for e in errs:
                    st.code(e.strip())
            else:
                st.success("No errors in log.")
        else:
            st.info("Log not found yet.")
    except Exception as e:
        st.warning(f"Could not read log: {e}")

    st.subheader("🧹 Maintenance")
    c1, c2, c3 = st.columns(3)
    if c1.button("Clear Pending Slips"):
        clear_pending_slips()
        st.success("Cleared.")
        st.toast("Pending slips cleared", icon="🧹")
    if c2.button("Force SEM Recalibration"):
        with st.spinner("Recalibrating SEM..."):
            _calibrate_sem()
        st.success("SEM recalibrated.")
        st.toast("SEM recalibrated", icon="📊")
    if c3.button("Force Threshold Tune"):
        with st.spinner("Tuning thresholds..."):
            _auto_tune()
        st.success(f"Thresholds: PROB={get_prob_bolt():.2f} DTM={get_dtm_bolt():.2f}")
        st.toast("Thresholds updated", icon="⚙️")

    st.subheader("⚖️ Current Thresholds")
    st.metric("PROB_BOLT", f"{get_prob_bolt():.3f}")
    st.metric("DTM_BOLT", f"{get_dtm_bolt():.3f}")
    with st.expander("Override thresholds manually"):
        np_ = st.number_input("PROB_BOLT", value=get_prob_bolt(), step=0.01, min_value=0.5, max_value=1.0)
        nd = st.number_input("DTM_BOLT", value=get_dtm_bolt(), step=0.01, min_value=0.0, max_value=0.5)
        if st.button("Apply"):
            set_setting("prob_bolt", np_)
            set_setting("dtm_bolt", nd)
            st.success("Thresholds updated.")
            st.toast("New thresholds applied", icon="⚙️")
            st.rerun()

# =============================================================================
# MAIN
# =============================================================================
def main():
    st.set_page_config(page_title=f"CLARITY {VERSION}", page_icon="⚡", layout="wide")
    init_db()
    _init_health()
    bankroll = _sidebar()
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
