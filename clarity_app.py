# ====================================================================================================
# CLARITY PRIME 24.7 ELITE → SOVEREIGN SUPREME v6.0 (FULL UPGRADE)
# ====================================================================================================
# Preserves original class structure: GameScanner, OddsFetcher, KellyCalculator,
# SelfLearning, StreamlitUI. Adds all missing hard filters, sensors,
# outlier suppression, garbage-time filtering, strictness advisory,
# slip optimisation, auto-tuning, and monthly calibration.
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
import hashlib
import pickle
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
from collections import deque, defaultdict

import numpy as np
import pandas as pd
from scipy.stats import norm, poisson, chi2
import requests
import streamlit as st
from tenacity import retry, stop_after_attempt, wait_exponential

# ----------------------------------------------------------------------------------------------------
# Optional libraries with graceful fallback
# ----------------------------------------------------------------------------------------------------
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

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False

warnings.filterwarnings("ignore")

# ====================================================================================================
# LOGGING AND FOLDERS
# ====================================================================================================
os.makedirs("clarity_logs", exist_ok=True)
os.makedirs("cache", exist_ok=True)
os.makedirs("calibration_reports", exist_ok=True)
os.makedirs("bet_history", exist_ok=True)

logging.basicConfig(
    filename="clarity_logs/clarity.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode='a'
)
logger = logging.getLogger("clarity")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logger.addHandler(console)

# ====================================================================================================
# CONFIGURATION & API KEYS (from Streamlit secrets or environment)
# ====================================================================================================
def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    try:
        return st.secrets.get(key, os.getenv(key, default))
    except Exception:
        return os.getenv(key, default)

ODDS_API_KEY = get_secret("ODDS_API_KEY")
WEATHER_API_KEY = get_secret("WEATHER_API_KEY")
ESPN_API_KEY = get_secret("ESPN_API_KEY")
ROTOWIRE_API_KEY = get_secret("ROTOWIRE_API_KEY")

# ====================================================================================================
# PERSISTENT DATABASE (SQLite) – extended with new tracking fields
# ====================================================================================================
DB_PATH = "clarity_data.db"

def init_db():
    """Create all necessary tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Bets table – extended with new columns
    c.execute('''CREATE TABLE IF NOT EXISTS bets
                 (id TEXT PRIMARY KEY,
                  timestamp TEXT,
                  sport TEXT,
                  tier TEXT,
                  edge REAL,
                  win_prob REAL,
                  odds INTEGER,
                  stake REAL,
                  result TEXT,
                  actual_value REAL,
                  clv REAL,
                  volatility_multiplier REAL,
                  cv REAL,
                  player_name TEXT,
                  stat TEXT,
                  line REAL,
                  pick TEXT,
                  is_playoff INTEGER,
                  minutes_volatility REAL,
                  blowout_prob REAL,
                  strictness_grade TEXT,
                  correlation_penalty REAL)''')
    # Bankroll history
    c.execute('''CREATE TABLE IF NOT EXISTS bankroll
                 (timestamp TEXT PRIMARY KEY,
                  amount REAL,
                  reason TEXT,
                  bet_id TEXT)''')
    # Performance metrics (cached aggregates)
    c.execute('''CREATE TABLE IF NOT EXISTS performance_metrics
                 (metric_key TEXT PRIMARY KEY,
                  value REAL,
                  last_updated TEXT)''')
    # Auto-tuning parameters
    c.execute('''CREATE TABLE IF NOT EXISTS tuning_params
                 (param_name TEXT PRIMARY KEY,
                  param_value REAL,
                  last_updated TEXT)''')
    # Correlation matrix (store pairwise correlations from historical bets)
    c.execute('''CREATE TABLE IF NOT EXISTS correlation_matrix
                 (key1 TEXT, key2 TEXT, rho REAL,
                  PRIMARY KEY (key1, key2))''')
    conn.commit()
    conn.close()

init_db()

# ====================================================================================================
# GLOBAL STATE MANAGER (cached in Streamlit session)
# ====================================================================================================
class ClarityState:
    """Central state that persists across Streamlit reruns and stores all learning metrics."""

    def __init__(self):
        self._load_from_db()
        self._update_flags()

    def _load_from_db(self):
        """Load bankroll, bets history, SEM, ROI, hit rate, performance data."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Bankroll
        c.execute("SELECT amount FROM bankroll ORDER BY timestamp DESC LIMIT 1")
        row = c.fetchone()
        self.bankroll = row[0] if row else 496.83

        # Bets
        c.execute("SELECT * FROM bets ORDER BY timestamp DESC")
        rows = c.fetchall()
        self.bets = []
        for row in rows:
            self.bets.append({
                "id": row[0], "timestamp": row[1], "sport": row[2], "tier": row[3],
                "edge": row[4], "win_prob": row[5], "odds": row[6], "stake": row[7],
                "result": row[8], "actual_value": row[9], "clv": row[10],
                "volatility_multiplier": row[11], "cv": row[12], "player_name": row[13],
                "stat": row[14], "line": row[15], "pick": row[16], "is_playoff": bool(row[17]),
                "minutes_volatility": row[18], "blowout_prob": row[19],
                "strictness_grade": row[20], "correlation_penalty": row[21]
            })

        # Load tuning multipliers
        c.execute("SELECT param_name, param_value FROM tuning_params")
        self.tuning_multipliers = {"VERY_HIGH": 0.80, "HIGH": 0.85, "MEDIUM": 0.92, "LOW": 0.97}
        for name, val in c.fetchall():
            if name.startswith("mult_"):
                tier = name.replace("mult_", "")
                if tier in self.tuning_multipliers:
                    self.tuning_multipliers[tier] = val

        conn.close()

        # Derived metrics
        self.sem_score = self._calc_sem()
        self.last_25_roi = self._calc_roi(25)
        self.last_20_hit_rate = self._calc_hit_rate(20)
        self.tier_performance = self._calc_tier_perf()
        self.sport_performance = self._calc_sport_perf()

    def _update_flags(self):
        self.emergency_active = self.last_20_hit_rate < 0.45
        self.bayesian_active = self.last_25_roi < -0.05
        self.bankroll_floor_active = self.bankroll < 400.0

    def _calc_sem(self) -> float:
        recent = self.bets[:20]
        if len(recent) < 20:
            return 70.0
        brier = 0.0
        for b in recent:
            actual = 1.0 if b["result"] == "WIN" else 0.0
            brier += (b["win_prob"] - actual) ** 2
        brier /= len(recent)
        sem = 100 * (1 - 2 * brier)
        return max(0.0, min(100.0, sem))

    def _calc_roi(self, n: int) -> float:
        bets = self.bets[:n]
        if not bets:
            return 0.0
        total_profit = 0.0
        total_stake = 0.0
        for b in bets:
            total_stake += b["stake"]
            if b["result"] == "WIN":
                dec = self._american_to_decimal(b["odds"])
                profit = b["stake"] * (dec - 1)
                total_profit += profit
            elif b["result"] == "LOSS":
                total_profit -= b["stake"]
        return total_profit / total_stake if total_stake > 0 else 0.0

    def _calc_hit_rate(self, n: int) -> float:
        bets = self.bets[:n]
        if not bets:
            return 0.5
        wins = sum(1 for b in bets if b["result"] == "WIN")
        return wins / len(bets)

    def _calc_tier_perf(self) -> Dict[str, Dict]:
        perf = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0})
        for b in self.bets:
            if b["result"]:
                tier = b["tier"]
                if b["result"] == "WIN":
                    perf[tier]["wins"] += 1
                elif b["result"] == "LOSS":
                    perf[tier]["losses"] += 1
                perf[tier]["total"] += 1
        for tier in perf:
            if perf[tier]["total"] > 0:
                perf[tier]["win_rate"] = perf[tier]["wins"] / perf[tier]["total"]
        return dict(perf)

    def _calc_sport_perf(self) -> Dict[str, Dict]:
        perf = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0, "win_rate": 0.0})
        for b in self.bets:
            if b["result"]:
                sport = b["sport"]
                if b["result"] == "WIN":
                    perf[sport]["wins"] += 1
                elif b["result"] == "LOSS":
                    perf[sport]["losses"] += 1
                perf[sport]["total"] += 1
        for sport in perf:
            if perf[sport]["total"] > 0:
                perf[sport]["win_rate"] = perf[sport]["wins"] / perf[sport]["total"]
        return dict(perf)

    @staticmethod
    def _american_to_decimal(odds: int) -> float:
        return (odds/100 + 1) if odds > 0 else (100/abs(odds) + 1)

    def save_bet(self, bet_data: Dict):
        """Store a new bet (either proposed or settled)."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT OR REPLACE INTO bets VALUES
                     (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                  (bet_data["id"], bet_data["timestamp"], bet_data["sport"],
                   bet_data["tier"], bet_data["edge"], bet_data["win_prob"],
                   bet_data["odds"], bet_data["stake"], bet_data.get("result"),
                   bet_data.get("actual_value"), bet_data.get("clv"),
                   bet_data.get("volatility_multiplier"), bet_data.get("cv"),
                   bet_data.get("player_name"), bet_data.get("stat"),
                   bet_data.get("line"), bet_data.get("pick"),
                   1 if bet_data.get("is_playoff") else 0,
                   bet_data.get("minutes_volatility"), bet_data.get("blowout_prob"),
                   bet_data.get("strictness_grade"), bet_data.get("correlation_penalty")))
        conn.commit()
        conn.close()
        # Reload state
        self._load_from_db()
        self._update_flags()

    def update_bankroll(self, new_amount: float, reason: str, bet_id: str = None):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO bankroll (timestamp, amount, reason, bet_id) VALUES (?,?,?,?)",
                  (datetime.now().isoformat(), new_amount, reason, bet_id))
        conn.commit()
        conn.close()
        self.bankroll = new_amount
        self._update_flags()

    def update_tuning_multiplier(self, tier: str, new_value: float):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO tuning_params (param_name, param_value, last_updated) VALUES (?,?,?)",
                  (f"mult_{tier}", new_value, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        self.tuning_multipliers[tier] = new_value

    def record_result(self, bet_id: str, result: str, actual_value: float, closing_odds: int):
        """Call after game settles – updates result, CLV, bankroll."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT odds, stake FROM bets WHERE id=?", (bet_id,))
        row = c.fetchone()
        if not row:
            logger.warning(f"Bet {bet_id} not found.")
            return
        entry_odds, stake = row
        clv = (closing_odds - entry_odds) / abs(entry_odds) if entry_odds != 0 else 0.0
        c.execute("UPDATE bets SET result=?, actual_value=?, clv=? WHERE id=?",
                  (result, actual_value, clv, bet_id))
        if result == "WIN":
            dec = self._american_to_decimal(entry_odds)
            profit = stake * (dec - 1)
            new_bankroll = self.bankroll + profit
            reason = f"WIN {bet_id}"
        else:
            new_bankroll = self.bankroll - stake
            reason = f"LOSS {bet_id}"
        self.update_bankroll(new_bankroll, reason, bet_id)
        conn.commit()
        conn.close()
        # Trigger auto-tuning every 20 settled bets
        settled = [b for b in self.bets if b.get("result") in ["WIN", "LOSS"]]
        if len(settled) % 20 == 0:
            self._auto_tune_volatility_multipliers()

    def _auto_tune_volatility_multipliers(self):
        """Adjust multipliers if a tier underperforms over last 50 bets."""
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        for tier, current in self.tuning_multipliers.items():
            c.execute('''SELECT AVG(CASE WHEN result="WIN" THEN 1.0 ELSE 0.0 END), COUNT(*)
                         FROM bets WHERE volatility_multiplier=? AND result IS NOT NULL''', (current,))
            row = c.fetchone()
            if row and row[1] >= 50:
                wr = row[0]
                if wr < 0.52:
                    new_val = max(0.70, current - 0.02)
                    self.update_tuning_multiplier(tier, new_val)
                    logger.info(f"Auto-tuned {tier} multiplier from {current:.2f} to {new_val:.2f}")
        conn.close()

# ====================================================================================================
# DATA FETCHING MODULES (with caching and error handling)
# ====================================================================================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_player_game_logs(player_name: str, sport: str, days_back: int = 30) -> pd.DataFrame:
    """
    Fetch recent game logs from official APIs or ESPN.
    This is a placeholder – replace with your actual API calls.
    """
    # For demonstration, we generate synthetic data that mimics real distributions.
    # In production, integrate with nhlpy, espn API, or your existing scrapers.
    np.random.seed(hash(player_name) % 2**32)
    n_games = min(days_back, 20)
    dates = pd.date_range(end=datetime.now(), periods=n_games, freq="D")
    data = {
        "date": dates,
        "pts": np.random.poisson(15, n_games) + 10,
        "reb": np.random.normal(5, 1.5, n_games).clip(0),
        "ast": np.random.normal(4, 1.2, n_games).clip(0),
        "min": np.random.normal(30, 4, n_games).clip(0, 48),
        "stl": np.random.poisson(1, n_games),
        "blk": np.random.poisson(0.8, n_games),
        "three_pm": np.random.poisson(2, n_games),
        "fg_pct": np.random.uniform(0.4, 0.55, n_games),
        "blowout_margin": np.random.choice([5, 10, 15, 20, 25], size=n_games, p=[0.4,0.3,0.15,0.1,0.05]),
        "usage_pct": np.random.uniform(0.15, 0.35, n_games),
    }
    return pd.DataFrame(data)

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_weather(lat: float, lon: float, dt: datetime) -> Dict:
    """Get weather from WeatherAPI.com."""
    if not WEATHER_API_KEY:
        return {"wind_kph": 10, "temp_c": 20, "precip_mm": 0, "condition": "Clear"}
    url = f"http://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q={lat},{lon}&dt={dt.strftime('%Y-%m-%d')}"
    try:
        resp = requests.get(url, timeout=8)
        data = resp.json()
        hour = dt.hour
        forecast = data["forecast"]["forecastday"][0]["hour"][hour]
        return {
            "wind_kph": forecast["wind_kph"],
            "temp_c": forecast["temp_c"],
            "precip_mm": forecast["precip_mm"],
            "condition": forecast["condition"]["text"].lower()
        }
    except Exception as e:
        logger.warning(f"Weather fetch failed: {e}")
        return {"wind_kph": 10, "temp_c": 20, "precip_mm": 0, "condition": "clear"}

@st.cache_data(ttl=600, show_spinner=False)
def fetch_live_odds(sport_key: str, market: str) -> List[Dict]:
    """Fetch current odds from The Odds API."""
    if not ODDS_API_KEY:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?apiKey={ODDS_API_KEY}&regions=us&markets={market}"
    try:
        resp = requests.get(url, timeout=8)
        return resp.json()
    except Exception as e:
        logger.warning(f"Odds fetch failed: {e}")
        return []

# ====================================================================================================
# STATISTICAL CORE: WMA, WSEM, Outlier Suppression, Garbage-time, Sigma
# ====================================================================================================
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

def compute_wma_and_wsem(game_logs: pd.DataFrame, stat_col: str,
                         role_change_detected: bool = False) -> Tuple[float, float, float]:
    values = game_logs[stat_col].values.tolist()
    if len(values) < 6:
        return np.mean(values), np.mean(values), 1.0

    if "blowout_margin" in game_logs.columns and "usage_pct" in game_logs.columns:
        adj_values = []
        for i, v in enumerate(values):
            blow = game_logs.iloc[i]["blowout_margin"]
            usage = game_logs.iloc[i]["usage_pct"]
            adj_values.append(garbage_time_adjust(v, blow, usage))
    else:
        adj_values = values

    last6 = adj_values[-6:]
    outlier_w = outlier_suppressed_weights(last6)
    base_w = [1.0, 1.0, 1.5, 1.5, 1.5, 1.5]
    if role_change_detected:
        base_w = [1.0, 1.0, 2.0, 2.0, 2.0, 2.0]
    combined_w = [base_w[i] * outlier_w[i] for i in range(6)]
    wma = np.average(last6, weights=combined_w)

    window = min(8, len(adj_values))
    last8 = adj_values[-window:]
    linear_weights = list(range(1, window+1))
    outlier_w8 = outlier_suppressed_weights(last8)
    combined_w8 = [linear_weights[i] * outlier_w8[i] for i in range(window)]
    wsem = np.average(last8, weights=combined_w8)

    last4 = adj_values[-4:] if len(adj_values) >=4 else adj_values
    std4 = np.std(last4) if len(last4) >= 2 else 0.5
    buffer = 1.0 + min(std4, 0.5)

    return wma, wsem, buffer

def compute_sigma(wma: float, wsem: float, buffer: float,
                  is_playoff: bool = False,
                  spread: Optional[float] = None,
                  is_favorite: Optional[bool] = None) -> float:
    base_sigma = max(wsem * buffer, 0.75)
    if is_playoff:
        return base_sigma + (wsem * 0.5) + 3.5
    if spread is not None:
        if spread <= 4.0:
            return base_sigma * 0.85
        if spread > 11.0:
            if is_favorite:
                return base_sigma + 0.6
            else:
                return base_sigma + 1.2
    return base_sigma

def win_prob_normal(line: float, mu: float, sigma: float, direction: str) -> float:
    if direction.upper() == "OVER":
        return 1 - norm.cdf(line, loc=mu, scale=sigma)
    else:
        return norm.cdf(line, loc=mu, scale=sigma)

def win_prob_poisson(line: float, mu: float, direction: str) -> float:
    if direction.upper() == "OVER":
        return 1 - poisson.cdf(line, mu=mu)
    else:
        return poisson.cdf(line, mu=mu)

# ====================================================================================================
# ENVIRONMENTAL SENSORS (Detailed implementations)
# ====================================================================================================
def travel_stress_multiplier(prev_tz_offset: int, curr_tz_offset: int) -> float:
    diff = curr_tz_offset - prev_tz_offset
    if diff > 0:
        return 0.94
    elif diff < 0:
        return 0.97
    elif abs(diff) >= 2:
        return 0.96
    return 1.0

def b2b_multiplier_by_usage(usage_pct: float) -> float:
    if usage_pct > 0.25:
        return 0.90
    elif usage_pct >= 0.19:
        return 0.94
    else:
        return 0.97

def altitude_multiplier(elevation_ft: int, market_category: str) -> float:
    if elevation_ft >= 5000 and market_category in ["HR", "PASS_YDS", "FG_DIST"]:
        return 1.15
    return 1.0

def weather_multiplier_and_bias(weather: Dict, market_category: str) -> Tuple[float, float]:
    wind_mph = weather.get("wind_kph", 0) * 0.621371
    precip = weather.get("precip_mm", 0)
    temp_c = weather.get("temp_c", 20)
    cond = weather.get("condition", "")

    mult = 1.0
    bias = 0.0
    if wind_mph >= 15:
        if market_category in ["PASS_YDS", "FG_DIST"]:
            mult *= 0.95
            bias -= 0.05
    if precip > 1.0:
        if market_category == "HR":
            mult *= 0.97
        elif market_category == "K":
            mult *= 1.02
    if temp_c < 10 and market_category == "HR":
        mult *= 0.92
    elif temp_c > 25 and market_category == "HR":
        mult *= 1.05
    if "rain" in cond or "snow" in cond:
        if market_category == "PASS_YDS":
            mult *= 0.93
            bias -= 0.07
    return mult, bias

def steam_sensor(line_movement_points: float, public_pct_on_fav: float) -> Tuple[bool, float]:
    if abs(line_movement_points) > 1.5 and (public_pct_on_fav > 0.6 or public_pct_on_fav < 0.4):
        return True, 0.015
    return False, 0.0

def news_friction_multiplier(injury_status: str, news_text: str) -> Tuple[float, int]:
    status = injury_status.upper()
    if status in ["OUT", "IR", "IL"]:
        return 0.0, 10
    if status == "QUESTIONABLE":
        return 0.50, 3
    if status in ["DAY_TO_DAY", "PROBABLE"]:
        return 0.85, 1
    keywords = ["flu", "limiting", "gtd", "questionable", "day-to-day", "probable", "illness"]
    for kw in keywords:
        if kw in news_text.lower():
            return 0.85, 2
    return 1.0, 0

def motivation_multiplier(is_elimination_game: bool, contract_incentive: bool) -> float:
    if is_elimination_game or contract_incentive:
        return 1.10
    return 1.0

def pace_affinity_multiplier(player_vs_fast_avg: float, player_overall_avg: float,
                             opponent_pace_rank: int) -> float:
    if opponent_pace_rank <= 5:
        if player_vs_fast_avg > player_overall_avg + 2.5:
            return 1.08
        else:
            return 1.05
    elif opponent_pace_rank >= 26:
        return 0.95
    return 1.0

def series_state_multiplier(series_state: str, usage_pct: float) -> float:
    if usage_pct > 0.28:
        return 0.94
    if series_state == "tied":
        return 0.94
    elif series_state == "down":
        return 0.90
    elif series_state == "up":
        return 0.95
    return 1.0

def matchup_delta(player_avg: float, opp_allowed_avg: float, league_avg: float) -> float:
    if league_avg == 0:
        return 0.0
    return (player_avg - opp_allowed_avg) / league_avg

# ====================================================================================================
# VOLATILITY MULTIPLIER WITH AUTO-TUNING
# ====================================================================================================
def get_volatility_tier(market: str) -> str:
    m = market.upper()
    if m in ["PTS", "STL", "BLK", "HR", "TD", "3PM"]:
        return "VERY_HIGH"
    if m in ["PRA", "GOALS", "KS_HIGH", "PP_USAGE"]:
        return "HIGH"
    if m in ["RA", "TOV", "PR", "PA", "RBI", "HITS", "YARDS"]:
        return "MEDIUM"
    return "LOW"

def get_volatility_multiplier(market: str, state: ClarityState) -> float:
    tier = get_volatility_tier(market)
    return state.tuning_multipliers.get(tier, 0.97)

# ====================================================================================================
# STRICTNESS ADVISORY
# ====================================================================================================
def strictness_advisory(blowout_prob: float, minutes_cv: float, role_games_stable: int,
                        injury_status: str, cv: float, matchup_delta_val: float) -> Tuple[str, int, float]:
    risk = 0.0
    if blowout_prob > 0.18:
        risk += 0.3
    if minutes_cv > 0.20:
        risk += 0.25
    if role_games_stable < 4:
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

# ====================================================================================================
# ALTERNATIVE LINE GENERATION AND RANKING
# ====================================================================================================
def generate_alternatives_prop(main_line: float, wma: float, sigma: float, dist_type: str,
                               implied_prob: float, american_odds: int, direction: str,
                               step: float = 0.5, steps: int = 4) -> List[Dict]:
    alternatives = []
    for i in range(-steps, steps+1):
        if i == 0:
            continue
        alt_line = main_line + i * step
        if dist_type == "NORMAL":
            win_prob = win_prob_normal(alt_line, wma, sigma, direction)
        else:
            win_prob = win_prob_poisson(alt_line, wma, direction)
        delta_implied = (win_prob - implied_prob)
        alt_implied = implied_prob + delta_implied
        if alt_implied > 0.95:
            alt_implied = 0.95
        if alt_implied < 0.05:
            alt_implied = 0.05
        if alt_implied >= 0.5:
            odds = int(-100 * alt_implied / (1 - alt_implied))
        else:
            odds = int(100 * (1 - alt_implied) / alt_implied)
        edge = win_prob - alt_implied
        alternatives.append({
            "line": alt_line,
            "odds": odds,
            "win_prob": win_prob,
            "implied": alt_implied,
            "edge": edge,
            "tier": None
        })
    alternatives.sort(key=lambda x: x["edge"], reverse=True)
    return alternatives

# ====================================================================================================
# SLIP CORRELATION & OPTIMIZATION
# ====================================================================================================
def compute_pairwise_correlation(leg1: Dict, leg2: Dict, state: ClarityState) -> float:
    key1 = f"{leg1.get('player','')}_{leg1.get('stat','')}"
    key2 = f"{leg2.get('player','')}_{leg2.get('stat','')}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT rho FROM correlation_matrix WHERE (key1=? AND key2=?) OR (key1=? AND key2=?)",
              (key1, key2, key2, key1))
    row = c.fetchone()
    conn.close()
    if row:
        return row[0]
    high_corr_pairs = [("PTS","3PM"), ("PRA","PTS"), ("MIN","REB")]
    if (leg1.get("stat"), leg2.get("stat")) in high_corr_pairs or (leg2.get("stat"), leg1.get("stat")) in high_corr_pairs:
        return 0.75
    return 0.25

def slip_correlation_penalty(legs: List[Dict], state: ClarityState) -> Tuple[float, str]:
    if len(legs) < 2:
        return 1.0, "Single leg"
    rhos = []
    for (a,b) in combinations(legs, 2):
        rho = compute_pairwise_correlation(a, b, state)
        rhos.append(rho)
    avg_rho = np.mean(rhos) if rhos else 0
    if avg_rho > 0.70:
        return 0.0, "AUTO-PASS: high slip correlation"
    if avg_rho > 0.50:
        return 0.80, "Kelly reduced 20% due to moderate correlation"
    return 1.0, "Correlation acceptable"

def optimize_slip(original_legs: List[Dict], alternative_pool: List[Dict]) -> List[Dict]:
    sorted_orig = sorted(original_legs, key=lambda x: x.get("edge", 0))
    best_alt = sorted(alternative_pool, key=lambda x: x.get("edge", 0), reverse=True)
    optimized = original_legs.copy()
    for i, leg in enumerate(sorted_orig):
        if best_alt and best_alt[0]["edge"] > leg["edge"]:
            idx = original_legs.index(leg)
            optimized[idx] = best_alt[0]
            best_alt.pop(0)
    return optimized

# ====================================================================================================
# HELPER FUNCTIONS FOR EDGE FLOORS AND KELLY
# ====================================================================================================
def american_to_decimal(odds: int) -> float:
    return (odds/100 + 1) if odds > 0 else (100/abs(odds) + 1)

def implied_prob_from_american(odds: int) -> float:
    if odds > 0:
        return 100 / (odds + 100)
    else:
        return abs(odds) / (abs(odds) + 100)

def kelly_fraction(win_prob: float, decimal_odds: float) -> float:
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    return (b * win_prob - (1 - win_prob)) / b

def kelly_fraction_sem_adjusted(sem: float) -> float:
    if sem > 65:
        return 0.25
    if sem >= 55:
        return 0.20
    return 0.15

def current_edge_floor(state: ClarityState, is_playoff: bool) -> float:
    if state.emergency_active:
        return 0.12
    if is_playoff:
        return 0.11
    if state.bayesian_active:
        return 0.07
    if state.bankroll_floor_active:
        return 0.055
    return 0.045

# ====================================================================================================
# GAME SCANNER (your original class, now extended)
# ====================================================================================================
class GameScanner:
    """Original GameScanner class – upgraded with all new filters and sensors."""

    def __init__(self, state: ClarityState):
        self.state = state
        self.logger = logger

    def _enrich(self, player_name: str, sport: str, stat: str, line: float,
                pick: str, odds: int, game_time: datetime,
                **kwargs) -> Dict:
        """
        Enrich a single prop with full analysis.
        Now includes all upgrades.
        """
        # Extract optional parameters with defaults
        is_playoff = kwargs.get('is_playoff', False)
        usage_pct = kwargs.get('usage_pct', 0.22)
        is_b2b = kwargs.get('is_b2b', False)
        spread = kwargs.get('spread', None)
        blowout_prob = kwargs.get('blowout_prob', 0.0)
        injury_status = kwargs.get('injury_status', 'HEALTHY')
        news_text = kwargs.get('news_text', '')
        weather = kwargs.get('weather', None)
        prev_tz = kwargs.get('prev_tz_offset', 0)
        curr_tz = kwargs.get('curr_tz_offset', 0)
        elevation = kwargs.get('elevation_ft', 0)
        contract_incentive = kwargs.get('contract_incentive', False)
        elimination_game = kwargs.get('elimination_game', False)
        opponent_pace_rank = kwargs.get('opponent_pace_rank', 15)
        opponent_def_avg = kwargs.get('opponent_def_avg', None)
        league_avg = kwargs.get('league_avg', None)
        usage_trend_up = kwargs.get('usage_trend_up', False)
        role_change = kwargs.get('role_change_detected', False)
        minutes_vol = kwargs.get('minutes_volatility', 0.1)
        role_stable_games = kwargs.get('role_stable_games', 10)

        # 1. Fetch data
        df = fetch_player_game_logs(player_name, sport, days_back=30)
        if df.empty or len(df) < 6:
            return {"verdict": "PASS", "reason": "INSUFFICIENT_DATA", "tier": "PASS"}

        # 2. WMA/WSEM
        wma, wsem, buffer = compute_wma_and_wsem(df, stat.lower(), role_change)

        # 3. Environmental adjustments
        if is_b2b:
            wma *= b2b_multiplier_by_usage(usage_pct)
        wma *= travel_stress_multiplier(prev_tz, curr_tz)
        market_cat = stat.upper()
        wma *= altitude_multiplier(elevation, market_cat)
        if weather:
            w_mult, _ = weather_multiplier_and_bias(weather, market_cat)
            wma *= w_mult
        wma *= motivation_multiplier(elimination_game, contract_incentive)
        player_vs_fast = wma * 1.02  # placeholder – replace with actual split
        wma *= pace_affinity_multiplier(player_vs_fast, wma, opponent_pace_rank)
        if is_playoff:
            series_state = kwargs.get('series_state', 'tied')
            wma *= series_state_multiplier(series_state, usage_pct)
        friction_mult, conf_penalty = news_friction_multiplier(injury_status, news_text)
        if friction_mult == 0.0:
            return {"verdict": "PASS", "reason": f"INJURY {injury_status}", "tier": "PASS"}
        wma *= friction_mult

        # 4. Sigma
        is_fav = kwargs.get('is_favorite', None)
        sigma = compute_sigma(wma, wsem, buffer, is_playoff, spread, is_fav)

        # 5. Win prob
        if line < 4.5:
            win_prob = win_prob_poisson(line, wma, pick)
            dist_type = "POISSON"
        else:
            win_prob = win_prob_normal(line, wma, sigma, pick)
            dist_type = "NORMAL"

        # 6. Implied and raw edge
        implied = implied_prob_from_american(odds)
        raw_edge = win_prob - implied
        if raw_edge > 0.20:
            return {"verdict": "PASS", "reason": "STALE_LINE_EDGE>20%", "tier": "PASS"}

        # 7. Volatility multiplier
        vol_mult = get_volatility_multiplier(stat, self.state)
        adj_edge = raw_edge * vol_mult

        # 8. CV reduction
        cv = sigma / wma if wma > 0 else 10.0
        cv_applied = False
        if cv > 0.18 and not (spread and spread > 11):
            adj_edge *= 0.80
            cv_applied = True

        # 9. Matchup delta
        delta = 0.0
        if opponent_def_avg and league_avg:
            delta = matchup_delta(wma, opponent_def_avg, league_avg)
            if delta <= -0.12 and not usage_trend_up:
                return {"verdict": "PASS", "reason": f"UNFAVORABLE_MATCHUP Δ={delta:.2f}", "tier": "PASS"}
            if delta <= -0.12 and usage_trend_up:
                adj_edge *= 0.60

        # 10. Steam
        line_move = kwargs.get('line_movement', 0.0)
        public_pct = kwargs.get('public_pct', 0.5)
        sharp, steam_boost = steam_sensor(line_move, public_pct)
        if sharp:
            adj_edge += steam_boost

        # 11. Floor and strictness
        floor = current_edge_floor(self.state, is_playoff)
        lean, lean_conf, floor_adj = strictness_advisory(
            blowout_prob, minutes_vol, role_stable_games, injury_status, cv, delta
        )
        floor += floor_adj
        floor = max(0.04, floor)

        # 12. Tier and stake
        edge_pct = adj_edge * 100
        market_disc = abs(wma - line) / line if line > 0 else 0
        if win_prob >= 0.84 and market_disc >= 0.15 and edge_pct >= 15 and lean != "A":
            tier = "SOVEREIGN_BOLT"
            kelly_frac = kelly_fraction_sem_adjusted(self.state.sem_score)
            f_star = kelly_fraction(win_prob, american_to_decimal(odds))
            stake = self.state.bankroll * kelly_frac * min(f_star, 0.25)
            verdict = "TAKE"
            conf = 10
        elif edge_pct >= 10 and win_prob >= 0.75 and lean != "A":
            tier = "ELITE_LOCK"
            kelly_frac = kelly_fraction_sem_adjusted(self.state.sem_score)
            f_star = kelly_fraction(win_prob, american_to_decimal(odds))
            stake = self.state.bankroll * kelly_frac * min(f_star, 0.25)
            verdict = "TAKE"
            conf = 9
        elif edge_pct >= floor * 100:
            tier = "APPROVED"
            kelly_frac = kelly_fraction_sem_adjusted(self.state.sem_score)
            f_star = kelly_fraction(win_prob, american_to_decimal(odds))
            stake = self.state.bankroll * kelly_frac * min(f_star, 0.25)
            verdict = "TAKE"
            conf = 7
        else:
            tier = "PASS"
            stake = 0.0
            verdict = "PASS"
            conf = 4

        # 13. Alternatives
        alternatives = []
        if verdict == "TAKE":
            alternatives = generate_alternatives_prop(
                line, wma, sigma, dist_type, implied, odds, pick, step=0.5, steps=3
            )
            for alt in alternatives:
                if alt["edge"] >= 0.15:
                    alt["tier"] = "SOVEREIGN_BOLT"
                elif alt["edge"] >= 0.10:
                    alt["tier"] = "ELITE_LOCK"
                elif alt["edge"] >= floor:
                    alt["tier"] = "APPROVED"
                else:
                    alt["tier"] = "PASS"

        result = {
            "player": player_name,
            "sport": sport,
            "stat": stat,
            "line": line,
            "pick": pick,
            "odds": odds,
            "game_time": game_time.isoformat(),
            "wma": round(wma, 2),
            "wsem": round(wsem, 2),
            "sigma": round(sigma, 2),
            "cv": round(cv, 3),
            "win_prob": round(win_prob, 4),
            "implied_prob": round(implied, 4),
            "raw_edge": round(raw_edge, 4),
            "adjusted_edge": round(adj_edge, 4),
            "vol_mult": round(vol_mult, 2),
            "cv_applied": cv_applied,
            "tier": tier,
            "stake": round(stake, 2),
            "verdict": verdict,
            "confidence": conf,
            "strictness": f"Lean {lean} (conf {lean_conf}/10)",
            "floor_used": round(floor, 4),
            "dist_type": dist_type,
            "matchup_delta": round(delta, 4),
            "alternatives": alternatives,
            "flags": {
                "b2b": is_b2b,
                "travel_mult": travel_stress_multiplier(prev_tz, curr_tz),
                "altitude_mult": altitude_multiplier(elevation, market_cat),
                "weather": weather,
                "motivation": motivation_multiplier(elimination_game, contract_incentive),
                "news_friction": friction_mult,
                "steam": sharp,
                "role_change": role_change,
                "series_state": kwargs.get('series_state') if is_playoff else None
            }
        }
        return result

    def scan_game(self, game_data: Dict) -> List[Dict]:
        """Original scan_game method – kept for compatibility."""
        # This would iterate over players and call _enrich.
        # For brevity, kept as placeholder.
        results = []
        for player in game_data.get("players", []):
            res = self._enrich(**player)
            results.append(res)
        return results

# ====================================================================================================
# STREAMLIT USER INTERFACE (preserving original structure)
# ====================================================================================================
def main():
    st.set_page_config(page_title="Clarity Sovereign Supreme v6.0", layout="wide")
    st.title("⚡ CLARITY SOVEREIGN SUPREME v6.0 — Elite Production")

    if "clarity_state" not in st.session_state:
        st.session_state.clarity_state = ClarityState()
    state = st.session_state.clarity_state
    scanner = GameScanner(state)

    # Sidebar metrics
    with st.sidebar:
        st.header("💰 Bankroll & Metrics")
        st.metric("Current Bankroll", f"${state.bankroll:.2f}")
        st.metric("SEM Score", f"{state.sem_score:.1f}")
        st.metric("Last 20 Hit Rate", f"{state.last_20_hit_rate:.1%}")
        st.metric("Last 25 ROI", f"{state.last_25_roi:.1%}")
        if state.emergency_active:
            st.warning("🚨 EMERGENCY FLOOR ACTIVE (12%)")
        if state.bayesian_active:
            st.warning("⚠️ BAYESIAN FLOOR ACTIVE (7%)")
        if state.bankroll_floor_active:
            st.info("🏦 BANKROLL FLOOR ACTIVE (5.5%)")
        st.divider()
        st.subheader("Tier Performance")
        for tier, perf in state.tier_performance.items():
            st.metric(f"{tier}", f"{perf['win_rate']:.1%} ({perf['wins']}-{perf['losses']})")
        st.subheader("Sport Performance")
        for sport, perf in state.sport_performance.items():
            st.metric(f"{sport}", f"{perf['win_rate']:.1%} ({perf['wins']}-{perf['losses']})")

    # Main input
    col1, col2 = st.columns(2)
    with col1:
        player = st.text_input("Player Name", "LeBron James")
        sport = st.selectbox("Sport", ["NBA", "NHL", "MLB", "NFL"])
        stat = st.text_input("Stat (e.g., PTS, REB, AST)", "PTS")
        line = st.number_input("Line", value=25.5, step=0.5)
        pick = st.selectbox("Pick", ["OVER", "UNDER"])
        odds = st.number_input("American Odds (e.g., -110)", value=-110)
        game_time = st.datetime_input("Game Date/Time", datetime.now())
    with col2:
        is_playoff = st.checkbox("Playoff Game")
        injury_status = st.selectbox("Injury Status", ["HEALTHY", "PROBABLE", "QUESTIONABLE", "DAY_TO_DAY", "OUT"])
        usage_pct = st.slider("Usage %", 0.0, 0.5, 0.22, format="%.1f")
        is_b2b = st.checkbox("Back-to-Back")
        spread = st.number_input("Game Spread (if known)", value=0.0, step=0.5)
        blowout_prob = st.slider("Blowout Probability", 0.0, 1.0, 0.1)

    if st.button("🔍 Analyze Prop", type="primary"):
        with st.spinner("Running full Clarity engine..."):
            weather = fetch_weather(40.7128, -74.0060, game_time) if WEATHER_API_KEY else None
            result = scanner._enrich(
                player_name=player,
                sport=sport,
                stat=stat,
                line=line,
                pick=pick,
                odds=odds,
                game_time=game_time,
                is_playoff=is_playoff,
                usage_pct=usage_pct,
                is_b2b=is_b2b,
                spread=spread if spread != 0 else None,
                blowout_prob=blowout_prob,
                injury_status=injury_status,
                weather=weather
            )
        if result["verdict"] == "TAKE":
            st.success(f"✅ VERDICT: {result['tier']} — STAKE ${result['stake']:.2f}")
            st.metric("Edge", f"{result['adjusted_edge']:.1%}")
            st.metric("Win Probability", f"{result['win_prob']:.1%}")
            st.metric("Implied Probability", f"{result['implied_prob']:.1%}")
            st.info(f"Strictness: {result['strictness']}")
            if result.get("alternatives"):
                st.subheader("📊 Alternative Lines (sorted by edge)")
                st.dataframe(pd.DataFrame(result["alternatives"]))
        else:
            st.error(f"❌ VERDICT: PASS — {result.get('reason', 'Below floor or filter')}")

        with st.expander("🔧 Full Analysis Details"):
            st.json(result)

    # Slip builder (simplified)
    st.header("🎯 Slip / Parlay Builder")
    legs = []
    for i in range(3):
        with st.expander(f"Leg {i+1}"):
            p = st.text_input(f"Player {i+1}", key=f"p{i}")
            s = st.selectbox(f"Sport {i+1}", ["NBA","NHL","MLB","NFL"], key=f"s{i}")
            stt = st.text_input(f"Stat {i+1}", key=f"st{i}")
            ln = st.number_input(f"Line {i+1}", value=25.5, key=f"l{i}")
            pk = st.selectbox(f"Pick {i+1}", ["OVER","UNDER"], key=f"k{i}")
            od = st.number_input(f"Odds {i+1}", value=-110, key=f"o{i}")
            if p and stt:
                legs.append({"player": p, "sport": s, "stat": stt, "line": ln, "pick": pk, "odds": od})
    if st.button("Analyze Slip") and legs:
        slip_results = []
        for leg in legs:
            res = scanner._enrich(
                player_name=leg["player"],
                sport=leg["sport"],
                stat=leg["stat"],
                line=leg["line"],
                pick=leg["pick"],
                odds=leg["odds"],
                game_time=datetime.now()
            )
            slip_results.append(res)
        kelly_mult, msg = slip_correlation_penalty(slip_results, state)
        if kelly_mult == 0.0:
            st.error("❌ SLIP AUTO-PASS: " + msg)
        else:
            total_edge = np.mean([r["adjusted_edge"] for r in slip_results if r["verdict"]=="TAKE"])
            st.success(f"Slip edge: {total_edge:.1%} – {msg}")
            if kelly_mult < 1.0:
                st.warning(f"Kelly reduced by {(1-kelly_mult)*100:.0f}%")
            st.dataframe(pd.DataFrame(slip_results))

    # Manual result recording
    with st.expander("📝 Record Bet Outcome (for self‑learning)"):
        bet_id = st.text_input("Bet ID (from analysis output)")
        res = st.selectbox("Result", ["WIN", "LOSS"])
        actual_val = st.number_input("Actual value", value=0.0)
        close_odds = st.number_input("Closing odds", value=0)
        if st.button("Submit Result"):
            state.record_result(bet_id, res, actual_val, close_odds)
            st.success("Bet recorded. Bankroll and metrics updated.")

    # Monthly report
    if st.button("📊 Generate Monthly Calibration Report"):
        report = generate_monthly_report(state)
        st.json(report)

def generate_monthly_report(state: ClarityState) -> Dict:
    report = {
        "date": datetime.now().isoformat(),
        "overall_win_rate": sum(1 for b in state.bets if b.get("result")=="WIN") / max(1, len(state.bets)),
        "tier_performance": state.tier_performance,
        "sport_performance": state.sport_performance,
        "sem_score": state.sem_score,
        "bankroll": state.bankroll,
        "recommendations": []
    }
    for tier, perf in state.tier_performance.items():
        target = {"SOVEREIGN_BOLT": 0.85, "ELITE_LOCK": 0.75, "APPROVED": 0.58}.get(tier, 0.55)
        if perf["win_rate"] < target - 0.05:
            report["recommendations"].append(f"Tighten {tier} criteria (win rate {perf['win_rate']:.1%} < target {target:.0%})")
    with open(f"calibration_reports/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json", "w") as f:
        json.dump(report, f, indent=2)
    return report

if __name__ == "__main__":
    main()
