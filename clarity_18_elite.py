import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
from scipy.special import iv
from openai import OpenAI
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import json
import sqlite3
import re
import time
import requests
import hashlib
import statistics
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
VERSION = "18.0 Elite (Sport-Specific Dropdowns)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

try:
    from pybaseball import statcast_batter, playerid_lookup
    STATCAST_AVAILABLE = True
except ImportError:
    STATCAST_AVAILABLE = False

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20},
    "SOCCER": {"distribution": "poisson", "variance_factor": 1.10},
    "TENNIS": {"distribution": "poisson", "variance_factor": 1.05}
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES (ORGANIZED BY SPORT)
# =============================================================================
SPORT_CATEGORIES = {
    "NBA": [
        "PTS", "REB", "AST", "STL", "BLK", "THREES", "FGM", "FGA", "FTM", "FTA",
        "OREB", "DREB", "TO", "FOULS", "DUNKS", "THREE_ATT", "TWO_MADE", "TWO_ATT",
        "PTS_1ST_3", "AST_1ST_3", "REB_1ST_3",
        "PRA", "PR", "PA", "RA", "BLK_STL", "NBA_FS", "THREES_COMBO", "AST_COMBO",
        "REB_COMBO", "PTS_COMBO", "DOUBLE_DOUBLE", "TRIPLE_DOUBLE", "3PTM"
    ],
    "MLB": [
        "OUTS", "KS", "HITS_ALLOWED", "ER", "BB_ALLOWED", "PITCHES", "1ST_INN_RA",
        "HITS", "TB", "HR", "RUNS", "RBI", "BB", "SB", "BATTER_KS", "SINGLES", "DOUBLES",
        "H+R+RBI", "HITTER_FS", "PITCHER_FS", "KS_COMBO"
    ],
    "NHL": [
        "SOG", "NHL_PTS", "SAVES", "NHL_AST", "GOALS", "GA", "TOI", "FACEOFFS",
        "PLUS_MINUS", "PP_PTS", "HITS", "BLK_SHOTS"
    ],
    "SOCCER": [
        "SHOTS", "SOC_SAVES", "PASSES", "SOT", "CROSSES", "SOC_AST", "SOC_GOALS",
        "SOC_GA", "SHOTS_AST", "CLEARANCES", "TACKLES", "DRIBBLES", "SOCCER_FOULS",
        "SOC_SAVES_COMBO", "PASSES_COMBO", "SOT_COMBO", "GOAL_AST", "SOC_GA_COMBO"
    ],
    "TENNIS": [
        "TOTAL_GAMES", "GAMES_WON", "TOTAL_SETS", "ACES", "BREAK_PTS", "TIEBREAKS",
        "DOUBLE_FAULTS", "TENNIS_FS"
    ],
    "NFL": []
}

# =============================================================================
# STAT CONFIG - 80+ CATEGORIES ACROSS ALL SPORTS
# =============================================================================
STAT_CONFIG = {
    # ===== NBA =====
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "STL": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "BLK": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "THREES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "FGM": {"tier": "MED", "buffer": 1.0, "reject": False},
    "FGA": {"tier": "HIGH", "buffer": 2.0, "reject": False},
    "FTM": {"tier": "MED", "buffer": 1.0, "reject": False},
    "FTA": {"tier": "MED", "buffer": 1.0, "reject": False},
    "OREB": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "DREB": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "TO": {"tier": "MED", "buffer": 1.0, "reject": False},
    "FOULS": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "DUNKS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "THREE_ATT": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "TWO_MADE": {"tier": "MED", "buffer": 1.0, "reject": False},
    "TWO_ATT": {"tier": "MED", "buffer": 1.0, "reject": False},
    "PTS_1ST_3": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "AST_1ST_3": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "REB_1ST_3": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    # NBA Combo (RED TIER)
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "RA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "BLK_STL": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    "NBA_FS": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "THREES_COMBO": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    "AST_COMBO": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "REB_COMBO": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    "PTS_COMBO": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "DOUBLE_DOUBLE": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "TRIPLE_DOUBLE": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "3PTM": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    
    # ===== MLB =====
    "OUTS": {"tier": "LOW", "buffer": 0.0, "reject": False},
    "KS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "HITS_ALLOWED": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "ER": {"tier": "MED", "buffer": 0.5, "reject": False},
    "BB_ALLOWED": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "PITCHES": {"tier": "MED", "buffer": 10.0, "reject": False},
    "1ST_INN_RA": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "HITS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TB": {"tier": "MED", "buffer": 1.0, "reject": False},
    "HR": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "RUNS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "RBI": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "BB": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "SB": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "BATTER_KS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "SINGLES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "DOUBLES": {"tier": "MED", "buffer": 0.5, "reject": False},
    # MLB Combo (RED TIER)
    "H+R+RBI": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "HITTER_FS": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PITCHER_FS": {"tier": "HIGH", "buffer": 5.0, "reject": True},
    "KS_COMBO": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    
    # ===== NHL =====
    "SOG": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "NHL_PTS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "SAVES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "NHL_AST": {"tier": "MED", "buffer": 0.5, "reject": False},
    "GOALS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "GA": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TOI": {"tier": "MED", "buffer": 2.0, "reject": False},
    "FACEOFFS": {"tier": "HIGH", "buffer": 2.0, "reject": False},
    "PLUS_MINUS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "PP_PTS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "HITS": {"tier": "MED", "buffer": 1.0, "reject": False},
    "BLK_SHOTS": {"tier": "MED", "buffer": 1.0, "reject": False},
    
    # ===== SOCCER =====
    "SHOTS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "SOC_SAVES": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "PASSES": {"tier": "MED", "buffer": 5.0, "reject": False},
    "SOT": {"tier": "MED", "buffer": 0.5, "reject": False},
    "CROSSES": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "SOC_AST": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "SOC_GOALS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "SOC_GA": {"tier": "MED", "buffer": 0.5, "reject": False},
    "SHOTS_AST": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "CLEARANCES": {"tier": "MED", "buffer": 2.0, "reject": False},
    "TACKLES": {"tier": "MED", "buffer": 1.0, "reject": False},
    "DRIBBLES": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "SOCCER_FOULS": {"tier": "MED", "buffer": 1.0, "reject": False},
    # Soccer Combo (RED TIER)
    "SOC_SAVES_COMBO": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    "PASSES_COMBO": {"tier": "HIGH", "buffer": 5.0, "reject": True},
    "SOT_COMBO": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    "GOAL_AST": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "SOC_GA_COMBO": {"tier": "HIGH", "buffer": 1.0, "reject": True},
    
    # ===== TENNIS =====
    "TOTAL_GAMES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "GAMES_WON": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "TOTAL_SETS": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "ACES": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "BREAK_PTS": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "TIEBREAKS": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "DOUBLE_FAULTS": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    # Tennis Combo (RED TIER)
    "TENNIS_FS": {"tier": "HIGH", "buffer": 5.0, "reject": True},
}

RED_TIER_PROPS = [
    "PRA", "PR", "PA", "RA", "BLK_STL", "NBA_FS", "THREES_COMBO", "AST_COMBO",
    "REB_COMBO", "PTS_COMBO", "DOUBLE_DOUBLE", "TRIPLE_DOUBLE", "3PTM",
    "H+R+RBI", "HITTER_FS", "PITCHER_FS", "KS_COMBO",
    "SOC_SAVES_COMBO", "PASSES_COMBO", "SOT_COMBO", "GOAL_AST", "SOC_GA_COMBO",
    "TENNIS_FS",
    "UNDER 1.5", "UNDER 2.5", "OVER 1.5", "OVER 2.5"
]

# =============================================================================
# SEASON CONTEXT ENGINE
# =============================================================================
class SeasonContextEngine:
    def __init__(self, api_client):
        self.api = api_client
        self.cache = {}
        self.cache_ttl = 3600
        
        self.season_calendars = {
            "NBA": {"regular_season_end": "2026-04-13", "playoffs_start": "2026-04-19"},
            "MLB": {"regular_season_end": "2026-09-28", "playoffs_start": "2026-10-03"},
            "NHL": {"regular_season_end": "2026-04-17", "playoffs_start": "2026-04-20"},
            "NFL": {"regular_season_end": "2026-01-04", "playoffs_start": "2026-01-10"}
        }
        
        self.motivation_multipliers = {
            "MUST_WIN": 1.12, "PLAYOFF_SEEDING": 1.08, "NEUTRAL": 1.00,
            "LOCKED_SEED": 0.92, "ELIMINATED": 0.85, "TANKING": 0.78, "PLAYOFFS": 1.05
        }
    
    def get_season_phase(self, sport: str) -> dict:
        date_obj = datetime.now()
        calendar = self.season_calendars.get(sport, {})
        if not calendar:
            return {"phase": "UNKNOWN", "is_playoffs": False}
        
        if "playoffs_start" in calendar:
            playoffs_start = datetime.strptime(calendar["playoffs_start"], "%Y-%m-%d")
            if date_obj >= playoffs_start:
                return {"phase": "PLAYOFFS", "is_playoffs": True}
        
        season_end = datetime.strptime(calendar.get("regular_season_end", "2026-12-31"), "%Y-%m-%d")
        days_remaining = (season_end - date_obj).days
        
        if days_remaining <= 0:
            phase = "FINAL_DAY"
        elif days_remaining <= 7:
            phase = "FINAL_WEEK"
        else:
            phase = "REGULAR_SEASON"
        
        return {"phase": phase, "is_playoffs": False, "days_remaining": days_remaining,
                "is_final_week": days_remaining <= 7, "is_final_day": days_remaining == 0}
    
    def should_fade_team(self, sport: str, team: str) -> dict:
        phase = self.get_season_phase(sport)
        prompt = f"Is {team} eliminated from {sport} playoffs or locked into their seed? Answer briefly."
        response = self.api.perplexity_call(prompt)
        
        eliminated = "eliminated" in response.lower()
        locked = "locked" in response.lower()
        tanking = "tanking" in response.lower()
        
        fade = False
        reasons = []
        if tanking:
            fade = True
            reasons.append("Team tanking")
        elif eliminated and not phase["is_playoffs"]:
            fade = True
            reasons.append("Team eliminated")
        elif locked and phase["is_final_week"]:
            fade = True
            reasons.append("Seed locked - resting starters")
        
        return {"team": team, "fade": fade, "reasons": reasons}

# =============================================================================
# API-SPORTS CLIENT (Lineups)
# =============================================================================
class APISportsClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-apisports-key": api_key}
        self.sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "hockey", "NFL": "american-football"}
        self.league_map = {"NBA": 12, "NFL": 1, "MLB": 1, "NHL": 57}
    
    def _call(self, endpoint: str, params: dict = None) -> dict:
        url = f"{API_SPORTS_BASE}/{endpoint}"
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            return response.json() if response.status_code == 200 else {}
        except:
            return {}
    
    def is_player_starting(self, sport: str, team: str, player: str) -> dict:
        api_sport = self.sport_map.get(sport, "basketball")
        league_id = self.league_map.get(sport, 12)
        
        data = self._call(f"{api_sport}/teams", {"league": league_id})
        team_id = None
        for t in data.get("response", []):
            if team.lower() in t["name"].lower():
                team_id = t["id"]
                break
        
        if not team_id:
            return {"starting": False, "status": "TEAM_NOT_FOUND", "confidence": "LOW"}
        
        data = self._call(f"{api_sport}/fixtures", {"league": league_id, "team": team_id, "season": "2025-2026"})
        if not data.get("response"):
            return {"starting": False, "status": "NO_FIXTURE", "confidence": "LOW"}
        
        fixture_id = data["response"][0]["id"]
        data = self._call(f"{api_sport}/fixtures/lineups", {"fixture": fixture_id})
        
        for team_data in data.get("response", []):
            if team_data["team"]["id"] == team_id:
                starters = [p["player"]["name"].lower() for p in team_data.get("startXI", [])]
                if player.lower() in starters:
                    return {"starting": True, "status": "STARTER", "confidence": "HIGH"}
                bench = [p["player"]["name"].lower() for p in team_data.get("substitutes", [])]
                if player.lower() in bench:
                    return {"starting": False, "status": "BENCH", "confidence": "HIGH"}
        
        return {"starting": False, "status": "NOT_IN_LINEUP", "confidence": "MEDIUM"}

# =============================================================================
# STATCAST MLB ENHANCEMENT
# =============================================================================
class StatcastMLBEnhancer:
    def __init__(self):
        self.cache = {}
        self.available = STATCAST_AVAILABLE
        self.league_avg = {'barrel_pct': 0.078, 'hard_hit_pct': 0.352, 'avg_exit_velocity': 88.4,
                           'xba': 0.243, 'xslg': 0.405}
    
    def get_statcast_metrics(self, player_name: str, season: int = 2026) -> dict:
        if not self.available:
            return self._default_metrics()
        
        try:
            last_name = player_name.split()[-1]
            player_ids = playerid_lookup(last_name)
            if player_ids.empty:
                return self._default_metrics()
            
            player_id = player_ids['key_mlbam'].iloc[0]
            data = statcast_batter(f"{season}-03-01", f"{season}-10-15", player_id)
            
            if data.empty:
                return self._default_metrics()
            
            return {
                'avg_exit_velocity': data['launch_speed'].mean() if 'launch_speed' in data.columns else 88.4,
                'barrel_pct': (data['barrel'] == 1).mean() if 'barrel' in data.columns else 0.078,
                'hard_hit_pct': (data['launch_speed'] >= 95).mean() if 'launch_speed' in data.columns else 0.352,
                'xba': data['estimated_ba_using_speedangle'].mean() if 'estimated_ba_using_speedangle' in data.columns else 0.243,
                'xslg': data['estimated_slg_using_speedangle'].mean() if 'estimated_slg_using_speedangle' in data.columns else 0.405,
                'sample_size': len(data)
            }
        except:
            return self._default_metrics()
    
    def _default_metrics(self) -> dict:
        return {'avg_exit_velocity': 88.4, 'barrel_pct': 0.078, 'hard_hit_pct': 0.352,
                'xba': 0.243, 'xslg': 0.405, 'sample_size': 0}

# =============================================================================
# UNIFIED API CLIENT (Perplexity)
# =============================================================================
class UnifiedAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.perplexity_client = OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE)
    
    def perplexity_call(self, prompt: str) -> str:
        try:
            r = self.perplexity_client.chat.completions.create(
                model="llama-3.1-sonar-large-32k-online",
                messages=[{"role": "user", "content": prompt}]
            )
            return r.choices[0].message.content
        except:
            return ""
    
    def get_injury_status(self, player: str, sport: str) -> dict:
        content = self.perplexity_call(f"{player} {sport} injury status today? HEALTHY/OUT/GTD.")
        return {
            "injury": "OUT" if any(x in content.upper() for x in ["OUT", "GTD", "QUESTIONABLE"]) else "HEALTHY",
            "steam": "STEAM" in content.upper()
        }
    
    def fetch_player_result(self, player: str, market: str, sport: str, date: str) -> Optional[float]:
        prompt = f"How many {market} did {player} have in their {sport} game on {date}? Return ONLY the number."
        response = self.perplexity_call(prompt)
        match = re.search(r'(\d+\.?\d*)', response)
        return float(match.group(1)) if match else None

# =============================================================================
# AUTO-SETTLEMENT ENGINE
# =============================================================================
class AutoSettlementEngine:
    def __init__(self, api_client, db_path: str = "clarity_history.db"):
        self.api = api_client
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id TEXT PRIMARY KEY,
                player TEXT,
                sport TEXT,
                market TEXT,
                line REAL,
                pick TEXT,
                odds INTEGER,
                edge REAL,
                result TEXT,
                actual REAL,
                date TEXT,
                settled_date TEXT,
                clv REAL
            )
        """)
        conn.commit()
        conn.close()
    
    def log_bet(self, player: str, market: str, line: float, pick: str,
                sport: str, odds: int, edge: float) -> str:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        bet_id = hashlib.md5(f"{player}{market}{line}{datetime.now()}".encode()).hexdigest()[:12]
        c.execute("""
            INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (bet_id, player, sport, market, line, pick, odds, edge, "PENDING", datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        return bet_id
    
    def get_pending_bets(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM bets WHERE result = 'PENDING'")
        rows = c.fetchall()
        conn.close()
        return [{"id": r[0], "player": r[1], "sport": r[2], "market": r[3], "line": r[4], 
                 "pick": r[5], "odds": r[6], "edge": r[7], "result": r[8], "actual": r[9], "date": r[10]} for r in rows]
    
    def settle_bet(self, bet: Dict) -> Dict:
        actual = self.api.fetch_player_result(bet["player"], bet["market"], bet["sport"], bet["date"])
        if actual is None:
            return {"status": "PENDING", "bet": bet}
        won = actual > bet["line"] if bet["pick"] == "OVER" else actual < bet["line"]
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("UPDATE bets SET result = ?, actual = ?, settled_date = ? WHERE id = ?",
                  ("WIN" if won else "LOSS", actual, datetime.now().strftime("%Y-%m-%d"), bet["id"]))
        conn.commit()
        conn.close()
        return {"status": "SETTLED", "player": bet["player"], "market": bet["market"], 
                "line": bet["line"], "pick": bet["pick"], "actual": actual, "result": "WIN" if won else "LOSS"}
    
    def settle_all_pending(self) -> List[Dict]:
        results = []
        for bet in self.get_pending_bets():
            results.append(self.settle_bet(bet))
            time.sleep(0.5)
        return results
    
    def get_settlement_summary(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM bets WHERE result = 'WIN'"); wins = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM bets WHERE result = 'LOSS'"); losses = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM bets WHERE result = 'PENDING'"); pending = c.fetchone()[0]
        conn.close()
        total = wins + losses
        win_rate = (wins / total * 100) if total > 0 else 0
        return {"total_bets": total, "wins": wins, "losses": losses, "pending": pending, "win_rate": round(win_rate, 1)}

# =============================================================================
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.api_sports = APISportsClient(API_SPORTS_KEY)
        self.season_context = SeasonContextEngine(self.api)
        self.statcast = StatcastMLBEnhancer()
        self.settlement = AutoSettlementEngine(self.api)
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
    
    def convert_odds(self, american: int, to: str = "implied") -> float:
        if to == "implied":
            return 100 / (american + 100) if american > 0 else abs(american) / (abs(american) + 100)
        return 1 + american/100 if american > 0 else 1 + 100/abs(american)
    
    def l42_check(self, stat: str, line: float, avg: float) -> Tuple[bool, str]:
        config = STAT_CONFIG.get(stat.upper(), {"tier": "MED", "buffer": 2.0, "reject": False})
        if config["reject"]:
            return False, f"RED TIER - {stat}"
        buffer = line - avg if stat.upper() not in ["OUTS", "HITS_ALLOWED"] else avg - line
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
        var = np.average((np.array(data) - mean)**2, weights=w)
        sem = np.sqrt(var / len(data))
        wsem = sem / abs(mean) if mean != 0 else float('inf')
        return wsem <= self.wsem_max, wsem
    
    def simulate_prop(self, data: List[float], line: float, pick: str, sport: str = "NBA") -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        lam = np.average(data, weights=w)
        if model["distribution"] == "nbinom":
            n = max(1, int(lam / 2))
            p = n / (n + lam)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims >= line) if pick == "OVER" else np.mean(sims <= line)
        dtm = (proj - line) / line if line != 0 else 0
        return {"proj": proj, "prob": prob, "dtm": dtm}
    
    def sovereign_bolt(self, prob: float, dtm: float, wsem_ok: bool, l42_pass: bool, injury: str) -> dict:
        if injury == "OUT":
            return {"signal": "🔴 INJURY RISK", "units": 0}
        if not l42_pass:
            return {"signal": "🔴 L42 REJECT", "units": 0}
        if prob >= self.prob_bolt and dtm >= self.dtm_bolt and wsem_ok:
            return {"signal": "🟢 SOVEREIGN BOLT ⚡", "units": 2.0}
        elif prob >= 0.78 and wsem_ok:
            return {"signal": "🟢 ELITE LOCK", "units": 1.5}
        elif prob >= 0.70:
            return {"signal": "🟡 APPROVED", "units": 1.0}
        return {"signal": "🔴 PASS",
