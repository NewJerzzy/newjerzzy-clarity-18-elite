"""
Clarity 19 Elite – Configuration Module
Contains:
- Global constants
- Tier thresholds
- Weighting parameters
- API keys
- Model paths
- Sport definitions
"""

# ============================
# API KEYS (replace with yours)
# ============================
UNIFIED_API_KEY = "YOUR_UNIFIED_KEY"
API_SPORTS_KEY = "YOUR_API_SPORTS_KEY"
BALLDONTLIE_API_KEY = "YOUR_BDL_KEY"
OCR_SPACE_API_KEY = "YOUR_OCR_KEY"
ODDS_API_IO_KEY = "YOUR_ODDS_API_IO_KEY"

# ============================
# MODEL PATHS
# ============================
LIGHTGBM_MODEL_PATH = "models/lightgbm_model.txt"
SCALER_PATH = "models/scaler.pkl"
ENCODER_PATH = "models/encoder.pkl"

# ============================
# TIER THRESHOLDS
# ============================
TIER_THRESHOLDS = {
    "SOVEREIGN_BOLT": 0.18,   # 18% edge
    "ELITE_LOCK": 0.12,       # 12% edge
    "APPROVED": 0.08,         # 8% edge
    "PASS": 0.00
}

# ============================
# WEIGHTING PARAMETERS
# ============================
WEIGHTS_LAST_8 = {
    "recent_games": [2, 2, 2, 1, 1, 1, 1, 1],  # last 3 weighted heavier
}

# L42 volatility buffer
L42_BUFFER = 0.15  # 15% volatility adjustment

# ============================
# OPPONENT STRENGTH WEIGHTS
# ============================
OPPONENT_WEIGHTS = {
    "DEFENSE_RANK_WEIGHT": 0.25,
    "PACE_WEIGHT": 0.15,
    "HOME_AWAY_WEIGHT": 0.10,
}

# ============================
# REST / INJURY WEIGHTS
# ============================
REST_WEIGHTS = {
    "BACK_TO_BACK": -0.07,
    "THREE_IN_FOUR": -0.05,
    "FIVE_IN_SEVEN": -0.03,
}

INJURY_WEIGHTS = {
    "QUESTIONABLE": -0.10,
    "PROBABLE": -0.03,
    "OUT": -1.00,
}

# ============================
# SEASON CONTEXT
# ============================
SEASON_CONTEXT_WEIGHTS = {
    "PLAYOFFS": 0.12,
    "TANKING": -0.15,
    "SEEDING_MOTIVATION": 0.08,
    "BLOWOUT_RISK": -0.10,
}

# ============================
# SUPPORTED SPORTS
# ============================
SUPPORTED_SPORTS = ["NBA", "NFL", "MLB", "NHL"]
"""
Clarity 19 Elite – Database Module
Expanded schema includes:
- bets
- tuning_log
- ml_retrain_log
- correlations
- bankroll
"""

import sqlite3
from datetime import datetime

DB_PATH = "clarity_elite.db"

# ============================================
# INITIALIZE DATABASE
# ============================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # -------------------------
    # BETS TABLE
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            source TEXT,
            sport TEXT,
            player TEXT,
            market TEXT,
            line REAL,
            pick TEXT,
            opponent TEXT,
            game_date TEXT,
            result TEXT,
            actual REAL
        )
    """)

    # -------------------------
    # TUNING LOG
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tuning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            sport TEXT,
            market TEXT,
            old_threshold REAL,
            new_threshold REAL,
            reason TEXT
        )
    """)

    # -------------------------
    # ML RETRAIN LOG
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_retrain_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model_version TEXT,
            accuracy REAL,
            loss REAL,
            notes TEXT
        )
    """)

    # -------------------------
    # CORRELATIONS
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS correlations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            sport TEXT,
            market_a TEXT,
            market_b TEXT,
            correlation REAL
        )
    """)

    # -------------------------
    # BANKROLL
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bankroll (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            bankroll REAL,
            daily_limit REAL,
            kelly_fraction REAL
        )
    """)

    conn.commit()
    conn.close()

# ============================================
# BASIC HELPERS
# ============================================
def insert_bet(row):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO bets (
            created_at, source, sport, player, market, line, pick,
            opponent, game_date, result, actual
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        row.get("source", ""),
        row.get("sport", ""),
        row.get("player", ""),
        row.get("market", ""),
        row.get("line", 0.0),
        row.get("pick", ""),
        row.get("opponent", ""),
        row.get("game_date", ""),
        row.get("result", ""),
        row.get("actual", 0.0),
    ))
    conn.commit()
    conn.close()

def get_pending_bets():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, sport, player, market, line, pick, opponent, game_date
        FROM bets
        WHERE result = '' OR result IS NULL
    """)
    rows = cur.fetchall()
    conn.close()

    out = []
    for r in rows:
        out.append({
            "id": r[0],
            "sport": r[1],
            "player": r[2],
            "market": r[3],
            "line": r[4],
            "pick": r[5],
            "opponent": r[6],
            "game_date": r[7],
        })
    return out

def update_bet_result(bet_id, result, actual):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        UPDATE bets
        SET result = ?, actual = ?
        WHERE id = ?
    """, (result, actual, bet_id))
    conn.commit()
    conn.close()

def clear_pending_bets():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM bets WHERE result = '' OR result IS NULL")
    conn.commit()
    conn.close()
"""
Clarity 19 Elite – Statistical Engine
Includes:
- Weighted moving averages
- WSEM
- L42 volatility buffer
- Opponent strength adjustment
- Rest & injury adjustment
- Season context adjustment
- Multi-sport stat ingestion
"""

import numpy as np
from clarity_config import (
    WEIGHTS_LAST_8,
    L42_BUFFER,
    OPPONENT_WEIGHTS,
    REST_WEIGHTS,
    INJURY_WEIGHTS,
    SEASON_CONTEXT_WEIGHTS,
)

# ============================================
# WEIGHTED MOVING AVERAGE
# ============================================
def weighted_moving_average(values):
    if len(values) < 8:
        return np.mean(values)

    weights = np.array(WEIGHTS_LAST_8["recent_games"])
    values = np.array(values[-8:])
    return float(np.average(values, weights=weights))

# ============================================
# WSEM – Weighted Standard Error of Mean
# ============================================
def weighted_sem(values):
    if len(values) < 2:
        return 1.0
    wma = weighted_moving_average(values)
    diffs = [(v - wma) ** 2 for v in values]
    return float(np.sqrt(np.mean(diffs)))

# ============================================
# L42 VOLATILITY BUFFER
# ============================================
def l42_adjustment(values):
    if len(values) < 4:
        return 0.0
    last4 = values[-4:]
    last2 = values[-2:]
    vol = abs(np.mean(last2) - np.mean(last4))
    return vol * L42_BUFFER

# ============================================
# OPPONENT STRENGTH ADJUSTMENT
# ============================================
def opponent_strength_adjustment(def_rank, pace, home):
    adj = 0.0
    adj += (1 - def_rank / 30) * OPPONENT_WEIGHTS["DEFENSE_RANK_WEIGHT"]
    adj += pace * OPPONENT_WEIGHTS["PACE_WEIGHT"]
    adj += (0.05 if home else -0.05) * OPPONENT_WEIGHTS["HOME_AWAY_WEIGHT"]
    return adj

# ============================================
# REST & INJURY ADJUSTMENT
# ============================================
def rest_injury_adjustment(rest_days, injury_status):
    adj = 0.0
    if rest_days == 0:
        adj += REST_WEIGHTS["BACK_TO_BACK"]
    elif rest_days == 1:
        adj += REST_WEIGHTS["THREE_IN_FOUR"]
    elif rest_days == 2:
        adj += REST_WEIGHTS["FIVE_IN_SEVEN"]

    adj += INJURY_WEIGHTS.get(injury_status.upper(), 0.0)
    return adj

# ============================================
# SEASON CONTEXT ADJUSTMENT
# ============================================
def season_context_adjustment(context):
    return SEASON_CONTEXT_WEIGHTS.get(context.upper(), 0.0)

# ============================================
# MULTI-SPORT STAT INGESTION (STUBS)
# ============================================
def fetch_stats_nba(player, market):
    return []

def fetch_stats_mlb(player, market):
    return []

def fetch_stats_nfl(player, market):
    return []

def fetch_stats_nhl(player, market):
    return []

def fetch_stats(sport, player, market):
    sport = sport.upper()
    if sport == "NBA":
        return fetch_stats_nba(player, market)
    if sport == "MLB":
        return fetch_stats_mlb(player, market)
    if sport == "NFL":
        return fetch_stats_nfl(player, market)
    if sport == "NHL":
        return fetch_stats_nhl(player, market)
    return []

"""
Clarity 19 Elite – Modeling Engine
This is the core decision engine that powers:
- Tier classification (Bolt / Lock / Approved / Pass / Reject)
- Probability modeling
- Expected value modeling
- Spread & total simulation
- Parlay correlation engine
- Kelly staking engine
- ML model scaffolding
- Auto-tuning logs
"""

import numpy as np
from scipy.stats import norm
from datetime import datetime

from clarity_config import (
    TIER_THRESHOLDS,
    OPPONENT_WEIGHTS,
    REST_WEIGHTS,
    SEASON_CONTEXT_WEIGHTS,
    LIGHTGBM_MODEL_PATH,
)
from clarity_stats import (
    weighted_moving_average,
    weighted_sem,
    l42_adjustment,
    opponent_strength_adjustment,
    rest_injury_adjustment,
    season_context_adjustment,
    fetch_stats,
)
from clarity_database import (
    insert_bet,
)

# ============================================================
# ML MODEL (SCALABLE – LOADED ONLY IF FILE EXISTS)
# ============================================================
class MLModel:
    def __init__(self):
        self.model = None
        self.loaded = False
        self._try_load()

    def _try_load(self):
        try:
            import lightgbm as lgb
            self.model = lgb.Booster(model_file=LIGHTGBM_MODEL_PATH)
            self.loaded = True
        except Exception:
            self.loaded = False

    def predict(self, features):
        if not self.loaded:
            return None
        try:
            return float(self.model.predict([features])[0])
        except Exception:
            return None


ml_model = MLModel()

# ============================================================
# TIER CLASSIFICATION
# ============================================================
def classify_tier(edge):
    if edge >= TIER_THRESHOLDS["SOVEREIGN_BOLT"]:
        return "SOVEREIGN BOLT"
    if edge >= TIER_THRESHOLDS["ELITE_LOCK"]:
        return "ELITE LOCK"
    if edge >= TIER_THRESHOLDS["APPROVED"]:
        return "APPROVED"
    if edge > 0:
        return "PASS"
    return "REJECT"


# ============================================================
# PROP PROBABILITY ENGINE
# ============================================================
def compute_prop_probability(values, line, adjustments):
    """
    values: last 8 games
    line: betting line
    adjustments: combined adjustments from:
        - opponent strength
        - rest/injury
        - season context
        - L42 volatility
    """

    if not values:
        return 0.5, 0.0, 0.0

    wma = weighted_moving_average(values)
    sem = weighted_sem(values)
    l42 = l42_adjustment(values)

    adjusted_mean = wma + adjustments + l42

    prob_over = 1 - norm.cdf(line, loc=adjusted_mean, scale=sem)
    prob_under = norm.cdf(line, loc=adjusted_mean, scale=sem)

    return prob_over, prob_under, adjusted_mean


# ============================================================
# GAME LINE SIMULATION (SPREAD & TOTAL)
# ============================================================
def simulate_spread(team_strength, opp_strength, line, n=5000):
    """
    Monte Carlo simulation for spread outcomes.
    """
    diffs = np.random.normal(
        loc=team_strength - opp_strength,
        scale=12.0,  # typical NBA spread variance
        size=n,
    )
    prob_cover = np.mean(diffs > line)
    return prob_cover


def simulate_total(team_off, team_def, opp_off, opp_def, line, n=5000):
    """
    Monte Carlo simulation for totals.
    """
    mean_total = team_off + opp_off - (team_def + opp_def) * 0.5
    totals = np.random.normal(loc=mean_total, scale=18.0, size=n)
    prob_over = np.mean(totals > line)
    return prob_over


# ============================================================
# PARLAY CORRELATION ENGINE
# ============================================================
def compute_correlation_adjustment(corr):
    """
    corr: correlation coefficient between props
    """
    if corr > 0.5:
        return -0.10
    if corr > 0.3:
        return -0.05
    if corr < -0.3:
        return 0.05
    return 0.0


# ============================================================
# KELLY STAKING ENGINE
# ============================================================
def kelly_fraction(prob, odds):
    """
    prob: win probability
    odds: American odds
    """
    if odds > 0:
        b = odds / 100
    else:
        b = 100 / abs(odds)

    q = 1 - prob
    k = (b * prob - q) / b
    return max(0.0, min(k, 1.0))


# ============================================================
# UNIFIED PROP DECISION ENGINE
# ============================================================
def analyze_prop(sport, player, market, line, opponent_info, rest_days, injury_status, context):
    """
    Full Clarity 19 Elite prop analysis.
    """

    # 1. Fetch last 8 stats
    values = fetch_stats(sport, player, market)

    if not values:
        return {
            "tier": "REJECT",
            "reason": "No stat data available",
            "edge": 0.0,
            "prob": 0.5,
            "adjusted_mean": 0.0,
        }

    # 2. Compute adjustments
    opp_adj = opponent_strength_adjustment(
        opponent_info.get("def_rank", 15),
        opponent_info.get("pace", 1.0),
        opponent_info.get("home", True),
    )

    rest_adj = rest_injury_adjustment(rest_days, injury_status)
    context_adj = season_context_adjustment(context)

    total_adj = opp_adj + rest_adj + context_adj

    # 3. Probability engine
    prob_over, prob_under, adj_mean = compute_prop_probability(values, line, total_adj)

    # 4. Choose best side
    if prob_over > prob_under:
        prob = prob_over
        pick = "OVER"
    else:
        prob = prob_under
        pick = "UNDER"

    edge = prob - 0.5
    tier = classify_tier(edge)

    return {
        "tier": tier,
        "pick": pick,
        "edge": edge,
        "prob": prob,
        "adjusted_mean": adj_mean,
        "values": values,
        "adjustments": total_adj,
    }


# ============================================================
# UNIFIED GAME-LINE DECISION ENGINE
# ============================================================
def analyze_game_line(sport, team_strength, opp_strength, market_type, line):
    """
    Handles:
    - ML
    - SPREAD
    - TOTAL
    """

    if market_type == "ML":
        prob = 1 / (1 + np.exp(-(team_strength - opp_strength) / 5))
        edge = prob - 0.5
        tier = classify_tier(edge)
        return {"tier": tier, "prob": prob, "edge": edge}

    if "SPREAD" in market_type:
        prob_cover = simulate_spread(team_strength, opp_strength, line)
        edge = prob_cover - 0.5
        tier = classify_tier(edge)
        return {"tier": tier, "prob": prob_cover, "edge": edge}

    if "TOTAL" in market_type:
        prob_over = simulate_total(team_strength, 1, opp_strength, 1, line)
        edge = prob_over - 0.5
        tier = classify_tier(edge)
        return {"tier": tier, "prob": prob_over, "edge": edge}

    return {"tier": "REJECT", "prob": 0.5, "edge": 0.0}


# ============================================================
# PARLAY ENGINE
# ============================================================
def analyze_parlay(legs, correlations):
    """
    legs: list of dicts with {"prob": float}
    correlations: list of correlation coefficients
    """

    base_prob = np.prod([leg["prob"] for leg in legs])

    corr_adj = sum(compute_correlation_adjustment(c) for c in correlations)
    final_prob = max(0.0, min(base_prob + corr_adj, 1.0))

    return {
        "base_prob": base_prob,
        "final_prob": final_prob,
        "edge": final_prob - base_prob,
    }
"""
Clarity 19 Elite – Parsing Engine
Handles:
- MyBookie slips
- Bovada slips
- PrizePicks slips
- DraftKings slips
- FanDuel slips
- Universal fallback parser
- OCR cleanup
- Player prop extraction
- Game line extraction
- Market normalization
"""

import re
from datetime import datetime

# ============================================================
# OCR CLEANUP
# ============================================================
def clean_ocr_text(text):
    lines = [l.strip() for l in text.splitlines()]
    cleaned = []

    for l in lines:
        if not l:
            continue
        if l.lower() in ["more", "less", "more wagers"]:
            continue
        if l in ["•", "₺", "V"]:
            continue
        cleaned.append(l)

    return "\n".join(cleaned)


# ============================================================
# MARKET NORMALIZATION
# ============================================================
def normalize_market(m):
    m = m.lower().strip()

    replacements = {
        "points": "PTS",
        "rebounds": "REB",
        "assists": "AST",
        "pra": "PRA",
        "pr ": "PR",
        "pa ": "PA",
        "hits": "HITS",
        "strikeouts": "K",
        "total bases": "TB",
        "outs": "OUTS",
        "passing yards": "PASS_YDS",
        "rushing yards": "RUSH_YDS",
        "receiving yards": "REC_YDS",
        "receptions": "REC",
        "shots on goal": "SOG",
        "saves": "SAVES",
        "points nhl": "POINTS",
    }

    for k, v in replacements.items():
        if k in m:
            return v

    return m.upper()


# ============================================================
# PLAYER PROP PATTERN
# ============================================================
PROP_PATTERN = re.compile(
    r"(?P<player>[A-Za-z .'-]+)\s+(?P<line>\d+\.?\d*)\s*(?P<market>[A-Za-z +]+)?",
    re.IGNORECASE,
)

# ============================================================
# GAME LINE PATTERNS
# ============================================================
SPREAD_PATTERN = re.compile(
    r"(?P<team>[A-Za-z .'-]+)\s+(?P<sign>[+-])(?P<num>\d+\.?\d*)",
    re.IGNORECASE,
)

TOTAL_PATTERN = re.compile(
    r"(O|U)\s*(?P<num>\d+\.?\d*)",
    re.IGNORECASE,
)

MONEYLINE_PATTERN = re.compile(
    r"(?P<team>[A-Za-z .'-]+)\s+(?P<ml>[+-]\d{2,4})",
    re.IGNORECASE,
)


# ============================================================
# PRIZEPICKS PARSER
# ============================================================
def parse_prizepicks(text):
    rows = []
    for line in text.splitlines():
        if "More" in line or "Less" in line:
            m = PROP_PATTERN.search(line)
            if not m:
                continue
            d = m.groupdict()
            player = d["player"].strip()
            line_val = float(d["line"])
            market = normalize_market(d.get("market", "PTS"))
            rows.append({
                "type": "PROP",
                "source": "PRIZEPICKS",
                "player": player,
                "market": market,
                "line": line_val,
            })
    return rows


# ============================================================
# MYBOOKIE PARSER
# ============================================================
def parse_mybookie(text):
    rows = []
    for block in text.split("\n\n"):
        if "MyBookie" not in text:
            continue

        for line in block.splitlines():
            m = PROP_PATTERN.search(line)
            if m:
                d = m.groupdict()
                player = d["player"].strip()
                line_val = float(d["line"])
                market = normalize_market(d.get("market", "PTS"))
                rows.append({
                    "type": "PROP",
                    "source": "MYBOOKIE",
                    "player": player,
                    "market": market,
                    "line": line_val,
                })

        for m in SPREAD_PATTERN.finditer(block):
            team = m.group("team").strip()
            sign = m.group("sign")
            num = float(m.group("num"))
            rows.append({
                "type": "GAME",
                "source": "MYBOOKIE",
                "team": team,
                "market": "SPREAD",
                "line": num if sign == "+" else -num,
            })

        for m in MONEYLINE_PATTERN.finditer(block):
            team = m.group("team").strip()
            ml = int(m.group("ml"))
            rows.append({
                "type": "GAME",
                "source": "MYBOOKIE",
                "team": team,
                "market": "ML",
                "line": 0.0,
                "price": ml,
            })

    return rows


# ============================================================
# BOVADA PARSER
# ============================================================
def parse_bovada(text):
    rows = []
    for block in text.split("\n\n"):
        if "Bovada" not in text:
            continue

        for m in PROP_PATTERN.finditer(block):
            d = m.groupdict()
            player = d["player"].strip()
            line_val = float(d["line"])
            market = normalize_market(d.get("market", "PTS"))
            rows.append({
                "type": "PROP",
                "source": "BOVADA",
                "player": player,
                "market": market,
                "line": line_val,
            })

        for m in SPREAD_PATTERN.finditer(block):
            team = m.group("team").strip()
            sign = m.group("sign")
            num = float(m.group("num"))
            rows.append({
                "type": "GAME",
                "source": "BOVADA",
                "team": team,
                "market": "SPREAD",
                "line": num if sign == "+" else -num,
            })

        for m in MONEYLINE_PATTERN.finditer(block):
            team = m.group("team").strip()
            ml = int(m.group("ml"))
            rows.append({
                "type": "GAME",
                "source": "BOVADA",
                "team": team,
                "market": "ML",
                "line": 0.0,
                "price": ml,
            })

    return rows


# ============================================================
# DRAFTKINGS PARSER
# ============================================================
def parse_draftkings(text):
    rows = []
    if "DraftKings" not in text:
        return rows

    for m in PROP_PATTERN.finditer(text):
        d = m.groupdict()
        player = d["player"].strip()
        line_val = float(d["line"])
        market = normalize_market(d.get("market", "PTS"))
        rows.append({
            "type": "PROP",
            "source": "DRAFTKINGS",
            "player": player,
            "market": market,
            "line": line_val,
        })

    for m in MONEYLINE_PATTERN.finditer(text):
        team = m.group("team").strip()
        ml = int(m.group("ml"))
        rows.append({
            "type": "GAME",
            "source": "DRAFTKINGS",
            "team": team,
            "market": "ML",
            "line": 0.0,
            "price": ml,
        })

    return rows


# ============================================================
# FANDUEL PARSER
# ============================================================
def parse_fanduel(text):
    rows = []
    if "FanDuel" not in text:
        return rows

    for m in PROP_PATTERN.finditer(text):
        d = m.groupdict()
        player = d["player"].strip()
        line_val = float(d["line"])
        market = normalize_market(d.get("market", "PTS"))
        rows.append({
            "type": "PROP",
            "source": "FANDUEL",
            "player": player,
            "market": market,
            "line": line_val,
        })

    for m in MONEYLINE_PATTERN.finditer(text):
        team = m.group("team").strip()
        ml = int(m.group("ml"))
        rows.append({
            "type": "GAME",
            "source": "FANDUEL",
            "team": team,
            "market": "ML",
            "line": 0.0,
            "price": ml,
        })

    return rows


# ============================================================
# UNIVERSAL FALLBACK PARSER
# ============================================================
def parse_universal(text):
    rows = []

    for m in PROP_PATTERN.finditer(text):
        d = m.groupdict()
        player = d["player"].strip()
        line_val = float(d["line"])
        market = normalize_market(d.get("market", "PTS"))
        rows.append({
            "type": "PROP",
            "source": "UNIVERSAL",
            "player": player,
            "market": market,
            "line": line_val,
        })

    for m in SPREAD_PATTERN.finditer(text):
        team = m.group("team").strip()
        sign = m.group("sign")
        num = float(m.group("num"))
        rows.append({
            "type": "GAME",
            "source": "UNIVERSAL",
            "team": team,
            "market": "SPREAD",
            "line": num if sign == "+" else -num,
        })

    for m in MONEYLINE_PATTERN.finditer(text):
        team = m.group("team").strip()
        ml = int(m.group("ml"))
        rows.append({
            "type": "GAME",
            "source": "UNIVERSAL",
            "team": team,
            "market": "ML",
            "line": 0.0,
            "price": ml,
        })

    return rows


# ============================================================
# MASTER PARSER
# ============================================================
def parse_slip(text):
    text = clean_ocr_text(text)

    parsers = [
        parse_prizepicks,
        parse_mybookie,
        parse_bovada,
        parse_draftkings,
        parse_fanduel,
        parse_universal,
    ]

    rows = []
    for p in parsers:
        parsed = p(text)
        if parsed:
            rows.extend(parsed)

    return rows
"""
Clarity 19 Elite – Settlement Engine
Handles:
- Multi-sport auto-settle
- Multi-sport slip-settle
- Prop settlement
- Game-line settlement
- OCR-based matching
- Result extraction
"""

import requests
from datetime import datetime, timedelta

from clarity_database import update_bet_result
from clarity_parsers import parse_slip
from clarity_config import (
    API_SPORTS_KEY,
    BALLDONTLIE_API_KEY,
)

# ============================================================
# API HELPERS
# ============================================================
def api_sports_request(sport, endpoint, params=None):
    """
    Generic API-Sports request for NFL/MLB/NHL.
    """
    try:
        headers = {"x-apisports-key": API_SPORTS_KEY}
        url = f"https://v1.api-sports.io/{sport}{endpoint}"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


def balldontlie_request(endpoint, params=None):
    """
    NBA stats via BallDontLie.
    """
    try:
        headers = {"Authorization": BALLDONTLIE_API_KEY}
        url = f"https://api.balldontlie.io/v1{endpoint}"
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


# ============================================================
# NBA PROP SETTLEMENT
# ============================================================
def settle_nba_prop(player, market, line, pick, game_date):
    """
    Uses BallDontLie to settle NBA props.
    """
    try:
        players = balldontlie_request("/players", params={"search": player})
        if not players or not players.get("data"):
            return "PENDING", 0.0

        player_id = players["data"][0]["id"]

        stats = balldontlie_request(
            "/stats",
            params={"player_ids[]": player_id, "dates[]": game_date},
        )
        if not stats or not stats.get("data"):
            return "PENDING", 0.0

        s = stats["data"][0]

        if market == "PTS":
            actual = s.get("pts", 0)
        elif market == "REB":
            actual = s.get("reb", 0)
        elif market == "AST":
            actual = s.get("ast", 0)
        elif market == "PRA":
            actual = s.get("pts", 0) + s.get("reb", 0) + s.get("ast", 0)
        elif market == "PR":
            actual = s.get("pts", 0) + s.get("reb", 0)
        elif market == "PA":
            actual = s.get("pts", 0) + s.get("ast", 0)
        else:
            actual = s.get("pts", 0)

        actual = float(actual)
        won = (actual > line) if pick == "OVER" else (actual < line)
        return ("WIN" if won else "LOSS"), actual

    except Exception:
        return "PENDING", 0.0


# ============================================================
# MLB PROP SETTLEMENT
# ============================================================
def settle_mlb_prop(player, market, line, pick, game_date):
    """
    MLB settlement via API-Sports.
    """
    try:
        players = api_sports_request("baseball", "/players", params={"search": player})
        if not players or not players.get("response"):
            return "PENDING", 0.0

        player_id = players["response"][0]["id"]

        stats = api_sports_request(
            "baseball",
            "/games",
            params={"date": game_date, "player": player_id},
        )
        if not stats or not stats.get("response"):
            return "PENDING", 0.0

        s = stats["response"][0]["statistics"][0]

        if market == "HITS":
            actual = s.get("hits", 0)
        elif market == "TB":
            actual = s.get("total_bases", 0)
        elif market == "K":
            actual = s.get("strikeouts", 0)
        elif market == "OUTS":
            actual = s.get("outs", 0)
        else:
            actual = 0

        actual = float(actual)
        won = (actual > line) if pick == "OVER" else (actual < line)
        return ("WIN" if won else "LOSS"), actual

    except Exception:
        return "PENDING", 0.0


# ============================================================
# NFL PROP SETTLEMENT
# ============================================================
def settle_nfl_prop(player, market, line, pick, game_date):
    """
    NFL settlement via API-Sports.
    """
    try:
        players = api_sports_request("american-football", "/players", params={"search": player})
        if not players or not players.get("response"):
            return "PENDING", 0.0

        player_id = players["response"][0]["id"]

        stats = api_sports_request(
            "american-football",
            "/games",
            params={"date": game_date, "player": player_id},
        )
        if not stats or not stats.get("response"):
            return "PENDING", 0.0

        s = stats["response"][0]["statistics"][0]

        if market == "PASS_YDS":
            actual = s.get("passing", {}).get("yards", 0)
        elif market == "RUSH_YDS":
            actual = s.get("rushing", {}).get("yards", 0)
        elif market == "REC_YDS":
            actual = s.get("receiving", {}).get("yards", 0)
        elif market == "REC":
            actual = s.get("receiving", {}).get("receptions", 0)
        else:
            actual = 0

        actual = float(actual)
        won = (actual > line) if pick == "OVER" else (actual < line)
        return ("WIN" if won else "LOSS"), actual

    except Exception:
        return "PENDING", 0.0


# ============================================================
# NHL PROP SETTLEMENT
# ============================================================
def settle_nhl_prop(player, market, line, pick, game_date):
    """
    NHL settlement via API-Sports.
    """
    try:
        players = api_sports_request("hockey", "/players", params={"search": player})
        if not players or not players.get("response"):
            return "PENDING", 0.0

        player_id = players["response"][0]["id"]

        stats = api_sports_request(
            "hockey",
            "/games",
            params={"date": game_date, "player": player_id},
        )
        if not stats or not stats.get("response"):
            return "PENDING", 0.0

        s = stats["response"][0]["statistics"][0]

        if market == "SOG":
            actual = s.get("shots", 0)
        elif market == "SAVES":
            actual = s.get("saves", 0)
        elif market == "POINTS":
            actual = s.get("points", 0)
        else:
            actual = 0

        actual = float(actual)
        won = (actual > line) if pick == "OVER" else (actual < line)
        return ("WIN" if won else "LOSS"), actual

    except Exception:
        return "PENDING", 0.0


# ============================================================
# GAME-LINE SETTLEMENT (ALL SPORTS)
# ============================================================
def settle_game_line(team, opponent, market, line, pick, sport, game_date):
    """
    Settles ML, SPREAD, TOTAL using API-Sports.
    """
    try:
        sport_map = {
            "NBA": "basketball",
            "NFL": "american-football",
            "MLB": "baseball",
            "NHL": "hockey",
        }

        s = sport_map.get(sport.upper(), "basketball")

        games = api_sports_request(
            s,
            "/games",
            params={"date": game_date, "team": team},
        )
        if not games or not games.get("response"):
            return "PENDING", 0.0

        g = games["response"][0]
        team_score = g.get("scores", {}).get("team", 0)
        opp_score = g.get("scores", {}).get("opponent", 0)

        if market == "ML":
            won = team_score > opp_score
            return ("WIN" if won else "LOSS"), float(team_score)

        if "SPREAD" in market:
            margin = team_score - opp_score
            won = margin > line if pick == "OVER" else margin < line
            return ("WIN" if won else "LOSS"), float(margin)

        if "TOTAL" in market:
            total = team_score + opp_score
            won = total > line if pick == "OVER" else total < line
            return ("WIN" if won else "LOSS"), float(total)

        return "PENDING", 0.0

    except Exception:
        return "PENDING", 0.0


# ============================================================
# MASTER PROP SETTLEMENT
# ============================================================
def settle_prop(bet):
    sport = bet["sport"].upper()
    player = bet["player"]
    market = bet["market"]
    line = float(bet["line"])
    pick = bet["pick"]
    game_date = bet["game_date"] or (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

    if sport == "NBA":
        return settle_nba_prop(player, market, line, pick, game_date)
    if sport == "MLB":
        return settle_mlb_prop(player, market, line, pick, game_date)
    if sport == "NFL":
        return settle_nfl_prop(player, market, line, pick, game_date)
    if sport == "NHL":
        return settle_nhl_prop(player, market, line, pick, game_date)

    return "PENDING", 0.0


# ============================================================
# SLIP-BASED SETTLEMENT
# ============================================================
def match_slip_to_pending(slip_rows, pending):
    matches = []

    for s in slip_rows:
        matched = []
        if s["type"] == "PROP":
            for p in pending:
                if (
                    p["sport"].upper() == s.get("sport", p["sport"]).upper()
                    and p["player"].lower() == s["player"].lower()
                    and p["market"].upper() == s["market"].upper()
                    and abs(float(p["line"]) - float(s["line"])) < 0.01
                ):
                    matched.append(p)

        elif s["type"] == "GAME":
            for p in pending:
                if (
                    p["sport"].upper() == s.get("sport", p["sport"]).upper()
                    and p["market"].upper() == s["market"].upper()
                    and abs(float(p["line"]) - float(s["line"])) < 0.01
                ):
                    matched.append(p)

        if matched:
            matches.append((s, matched))

    return matches


def settle_from_slip(slip_text, pending, slip_result):
    slip_rows = parse_slip(slip_text)
    matches = match_slip_to_pending(slip_rows, pending)

    updated = []
    for slip_row, matched_bets in matches:
        for mb in matched_bets:
            update_bet_result(mb["id"], slip_result, 0.0)
            updated.append(mb)

    return updated
import streamlit as st
from datetime import datetime

from clarity_database import (
    init_db,
    insert_bet,
    get_pending_bets,
    update_bet_result,
    clear_pending_bets,
)
from clarity_parsers import parse_slip
from clarity_engine import (
    analyze_prop,
    analyze_game_line,
    analyze_parlay,
    kelly_fraction,
)
from clarity_settlement import (
    settle_prop,
    settle_game_line,
    settle_from_slip,
)

# ============================
# INIT
# ============================
init_db()
st.set_page_config(page_title="Clarity 19 Elite", layout="wide")

st.title("Clarity 19 Elite – Sovereign Modeling Suite")

tabs = st.tabs([
    "Slip Upload & Settle",
    "Player Props Analyzer",
    "Game Lines Analyzer",
    "Kelly & Bankroll",
    "Pending & Auto-Settle",
])

# ============================
# TAB 1 – SLIP UPLOAD & SETTLE
# ============================
with tabs[0]:
    st.header("Slip Upload & Parsing")

    slip_text = st.text_area(
        "Paste OCR text or raw slip text here",
        height=200,
        key="slip_text_area",
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Parse Slip", key="parse_slip_btn"):
            if not slip_text.strip():
                st.warning("Paste slip text first.")
            else:
                rows = parse_slip(slip_text)
                if not rows:
                    st.error("No bets detected from slip.")
                else:
                    st.success(f"Detected {len(rows)} bets from slip:")
                    for r in rows:
                        st.json(r)

    with col2:
        slip_result = st.selectbox(
            "Settle all matched bets from this slip as:",
            ["WIN", "LOSS"],
            key="slip_result_select",
        )
        if st.button("Settle From Slip vs Pending", key="settle_from_slip_btn"):
            pending = get_pending_bets()
            if not pending:
                st.info("No pending bets to match.")
            else:
                updated = settle_from_slip(slip_text, pending, slip_result)
                st.success(f"Updated {len(updated)} pending bets from slip match.")

    st.markdown("---")
    st.subheader("Manual Bet Entry (Store in DB)")

    sport = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], key="manual_sport")
    bet_type = st.selectbox("Type", ["PROP", "GAME"], key="manual_type")

    if bet_type == "PROP":
        player = st.text_input("Player", key="manual_player")
        market = st.text_input("Market (e.g., PTS, REB, PRA)", key="manual_market")
        line = st.number_input("Line", value=20.5, key="manual_line")
        pick = st.selectbox("Pick", ["OVER", "UNDER"], key="manual_pick")
        opponent = st.text_input("Opponent", key="manual_opp")
        game_date = st.date_input("Game Date", key="manual_date")

        if st.button("Save Pending Prop Bet", key="save_prop_btn"):
            row = {
                "source": "MANUAL",
                "sport": sport,
                "player": player,
                "market": market.upper(),
                "line": float(line),
                "pick": pick,
                "opponent": opponent,
                "game_date": game_date.strftime("%Y-%m-%d"),
                "result": "",
                "actual": 0.0,
            }
            insert_bet(row)
            st.success("Prop bet saved as pending.")

    else:
        team = st.text_input("Team", key="manual_team")
        opponent = st.text_input("Opponent", key="manual_team_opp")
        market = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="manual_game_market")
        line = st.number_input("Line (0 for ML)", value=0.0, key="manual_game_line")
        pick = st.selectbox("Pick", ["OVER", "UNDER", "TEAM"], key="manual_game_pick")
        game_date = st.date_input("Game Date", key="manual_game_date")

        if st.button("Save Pending Game Bet", key="save_game_btn"):
            row = {
                "source": "MANUAL",
                "sport": sport,
                "player": team,
                "market": market.upper(),
                "line": float(line),
                "pick": pick,
                "opponent": opponent,
                "game_date": game_date.strftime("%Y-%m-%d"),
                "result": "",
                "actual": 0.0,
            }
            insert_bet(row)
            st.success("Game bet saved as pending.")

# ============================
# TAB 2 – PLAYER PROPS ANALYZER
# ============================
with tabs[1]:
    st.header("Elite Player Props Analyzer")

    col1, col2, col3 = st.columns(3)
    with col1:
        sport_pp = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], key="pp_sport")
        player_pp = st.text_input("Player", key="pp_player")
    with col2:
        market_pp = st.text_input("Market (PTS, REB, PRA, etc.)", key="pp_market")
        line_pp = st.number_input("Line", value=20.5, key="pp_line")
    with col3:
        opp_def_rank = st.slider("Opponent Defense Rank (1=best, 30=worst)", 1, 30, 15, key="pp_def_rank")
        opp_pace = st.slider("Opponent Pace Factor", 0.8, 1.2, 1.0, 0.01, key="pp_pace")
        home_game = st.checkbox("Home Game?", value=True, key="pp_home")

    rest_days = st.selectbox("Rest Days", [0, 1, 2, 3], key="pp_rest")
    injury_status = st.selectbox("Injury Status", ["NONE", "PROBABLE", "QUESTIONABLE", "OUT"], key="pp_injury")
    context = st.selectbox("Season Context", ["REGULAR", "PLAYOFFS", "TANKING", "SEEDING_MOTIVATION", "BLOWOUT_RISK"], key="pp_context")

    if st.button("Run Elite Prop Analysis", key="pp_run_btn"):
        opponent_info = {
            "def_rank": opp_def_rank,
            "pace": opp_pace,
            "home": home_game,
        }
        result = analyze_prop(
            sport_pp,
            player_pp,
            market_pp.upper(),
            float(line_pp),
            opponent_info,
            rest_days,
            injury_status,
            context,
        )

        st.subheader("Clarity 19 Elite Decision")
        st.write(f"**Tier:** {result['tier']}")
        st.write(f"**Pick:** {result.get('pick', 'N/A')}")
        st.write(f"**Edge:** {result['edge']:.3f}")
        st.write(f"**Win Probability:** {result['prob']:.3f}")
        st.write(f"**Adjusted Mean:** {result['adjusted_mean']:.2f}")
        st.write(f"**Total Adjustments:** {result['adjustments']:.3f}")

        with st.expander("Underlying Stat Sample (Last Games)"):
            st.write(result["values"])

# ============================
# TAB 3 – GAME LINES ANALYZER
# ============================
with tabs[2]:
    st.header("Game Lines Analyzer (ML / Spread / Total)")

    col1, col2 = st.columns(2)
    with col1:
        sport_gl = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], key="gl_sport")
        team_strength = st.slider("Team Strength Rating", 0.0, 100.0, 55.0, key="gl_team_str")
        opp_strength = st.slider("Opponent Strength Rating", 0.0, 100.0, 50.0, key="gl_opp_str")
    with col2:
        market_gl = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="gl_market")
        line_gl = st.number_input("Line (0 for ML)", value=0.0, key="gl_line")

    if st.button("Run Game Line Analysis", key="gl_run_btn"):
        res = analyze_game_line(
            sport_gl,
            float(team_strength),
            float(opp_strength),
            market_gl,
            float(line_gl),
        )
        st.subheader("Clarity 19 Elite Decision")
        st.write(f"**Tier:** {res['tier']}")
        st.write(f"**Win Probability:** {res['prob']:.3f}")
        st.write(f"**Edge:** {res['edge']:.3f}")

# ============================
# TAB 4 – KELLY & BANKROLL
# ============================
with tabs[3]:
    st.header("Kelly Staking & Bankroll Guidance")

    bankroll = st.number_input("Current Bankroll ($)", value=1000.0, key="kelly_bankroll")
    odds = st.number_input("American Odds (e.g., -110, +150)", value=-110, key="kelly_odds")
    prob = st.slider("Win Probability (from Clarity)", 0.0, 1.0, 0.55, 0.001, key="kelly_prob")

    if st.button("Compute Kelly Stake", key="kelly_btn"):
        k = kelly_fraction(prob, odds)
        stake = bankroll * k
        st.subheader("Kelly Recommendation")
        st.write(f"**Kelly Fraction:** {k:.3f}")
        st.write(f"**Suggested Stake:** ${stake:.2f}")

# ============================
# TAB 5 – PENDING & AUTO-SETTLE
# ============================
with tabs[4]:
    st.header("Pending Bets & Auto-Settle")

    pending = get_pending_bets()
    st.write(f"Pending Bets: {len(pending)}")

    if pending:
        st.table(pending)

    st.markdown("### Auto-Settle All Pending Props (API-Based)")

    if st.button("Auto-Settle All Pending Props", key="auto_settle_btn"):
        updated = 0
        for b in pending:
            if b["market"] in ["ML", "SPREAD", "TOTAL"]:
                continue
            result, actual = settle_prop(b)
            if result != "PENDING":
                update_bet_result(b["id"], result, actual)
                updated += 1
        st.success(f"Auto-settled {updated} props (where data was available).")

    st.markdown("### Auto-Settle All Pending Game Lines (API-Based)")

    if st.button("Auto-Settle All Pending Game Lines", key="auto_settle_games_btn"):
        updated = 0
        for b in pending:
            if b["market"] not in ["ML", "SPREAD", "TOTAL"]:
                continue
            result, actual = settle_game_line(
                b["player"],
                b["opponent"],
                b["market"],
                float(b["line"]),
                b["pick"],
                b["sport"],
                b["game_date"],
            )
            if result != "PENDING":
                update_bet_result(b["id"], result, actual)
                updated += 1
        st.success(f"Auto-settled {updated} game-line bets (where data was available).")

    st.markdown("### Testing Utility")

    if st.button("Clear Pending Bets (Testing Only)", key="clear_pending_btn"):
        clear_pending_bets()
        st.success("All pending bets cleared (testing only). Refresh to see updated state.")
