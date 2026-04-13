# =============================================================================
# CLARITY 18.0 ELITE - COMPLETE WITH CLARITY APPROVED GAME ODDS
# =============================================================================
# VERSION: 18.0 Elite (Game Odds with CLARITY Approval)
# DATE: April 13, 2026
# API KEY: 96241c1a5ba686f34a9e4c3463b61661 ✅ UNIFIED (Perplexity + Odds)
# API-Sports: 8c20c34c3b0a6314e04c4997bf0922d2 ✅ INTEGRATED
# =============================================================================
# FEATURES:
# ✅ Smart Analysis (Manual Props)
# ✅ Bet Tracker & ROI Dashboard
# ✅ Parlay Builder with Correlation Warnings
# ✅ Game Odds Analyzer WITH CLARITY APPROVAL (ML/Spread/Total)
# =============================================================================

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
import statistics
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
VERSION = "18.0 Elite (Game Odds CLARITY Approved)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# STAT CONFIG
# =============================================================================
STAT_CONFIG = {
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "STL": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "BLK": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "THREES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "3PTM": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "OUTS": {"tier": "LOW", "buffer": 0.0, "reject": False},
    "KS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "SOG": {"tier": "LOW", "buffer": 0.5, "reject": False},
}

RED_TIER_PROPS = ["PRA", "PR", "PA", "3PTM", "1H", "MILESTONE", "COMBO", "TD", 
                  "UNDER 1.5", "UNDER 2.5", "OVER 1.5", "OVER 2.5"]

SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "odds_key": "basketball_nba", "home_advantage": 1.08, "std": 12.5},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "odds_key": "baseball_mlb", "home_advantage": 1.05, "std": 4.2},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "odds_key": "icehockey_nhl", "home_advantage": 1.07, "std": 2.1},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "odds_key": "americanfootball_nfl", "home_advantage": 1.06, "std": 14.0}
}

# =============================================================================
# INITIAL TEAM RATINGS (Elo)
# =============================================================================
TEAM_RATINGS = {
    "NBA": {
        "BOS": 1650, "DEN": 1620, "OKC": 1680, "SAS": 1580, "LAL": 1550, "MIA": 1520, "ATL": 1500, "NYK": 1540,
        "PHI": 1510, "MIL": 1530, "GSW": 1520, "LAC": 1550, "DAL": 1570, "PHX": 1530, "CLE": 1560, "MEM": 1500,
        "SAC": 1510, "MIN": 1580, "NOP": 1490, "HOU": 1520, "IND": 1500, "ORL": 1480, "CHI": 1450, "TOR": 1460,
        "BKN": 1440, "CHA": 1430, "DET": 1450, "POR": 1470, "UTA": 1480, "WAS": 1420
    },
    "MLB": {
        "LAD": 1620, "NYY": 1580, "BAL": 1520, "DET": 1450, "ATL": 1550, "HOU": 1500, "PHI": 1530, "TEX": 1480,
        "SEA": 1490, "MIN": 1470, "CLE": 1480, "MIL": 1460, "CHC": 1470, "STL": 1450, "NYM": 1500, "SD": 1520,
        "ARI": 1480, "TOR": 1490, "TB": 1510, "BOS": 1500, "KC": 1460, "SF": 1440, "CIN": 1430, "PIT": 1420,
        "LAA": 1440, "COL": 1400, "MIA": 1390, "CWS": 1380, "WSH": 1410, "OAK": 1370
    },
    "NHL": {
        "BOS": 1580, "FLA": 1600, "TOR": 1520, "TB": 1550, "NYR": 1540, "CAR": 1560, "NJ": 1500, "NYI": 1480,
        "PIT": 1490, "WSH": 1510, "OTT": 1530, "DET": 1460, "BUF": 1450, "MTL": 1440, "CBJ": 1470, "PHI": 1430,
        "COL": 1590, "DAL": 1570, "WPG": 1550, "EDM": 1600, "VGK": 1560, "LA": 1520, "SEA": 1480, "CGY": 1490,
        "VAN": 1500, "STL": 1470, "MIN": 1530, "NSH": 1510, "UTA": 1460, "ANA": 1440, "SJ": 1420, "CHI": 1430
    },
    "NFL": {
        "KC": 1680, "SF": 1650, "BUF": 1620, "PHI": 1600, "DET": 1580, "BAL": 1590, "DAL": 1560, "MIA": 1550,
        "CIN": 1570, "GB": 1540, "LAR": 1530, "HOU": 1520, "PIT": 1510, "LAC": 1500, "TB": 1490, "JAX": 1480,
        "SEA": 1470, "MIN": 1500, "CLE": 1490, "ATL": 1480, "NO": 1470, "CHI": 1460, "IND": 1450, "ARI": 1440,
        "DEN": 1480, "LV": 1430, "WAS": 1420, "TEN": 1410, "NYJ": 1440, "NYG": 1400, "CAR": 1390, "NE": 1420
    }
}

# =============================================================================
# NO-VIG FAIR ODDS
# =============================================================================
def novig_probabilities(odds_list: list) -> list:
    raw_probs = [1 / o for o in odds_list]
    total = sum(raw_probs)
    return [p / total for p in raw_probs]

def true_edge(model_prob: float, bookmaker_odds: float, all_odds: list) -> float:
    fair_probs = novig_probabilities(all_odds)
    fair_prob = fair_probs[0]
    return model_prob - fair_prob

def kelly_criterion_full(prob: float, decimal_odds: float, fraction: float = 0.25) -> float:
    b = decimal_odds - 1
    kelly = (b * prob - (1 - prob)) / b if b > 0 else 0
    return max(0.0, kelly * fraction)

# =============================================================================
# SKELLAM DISTRIBUTION (Spreads)
# =============================================================================
def skellam_pmf(k: int, mu1: float, mu2: float) -> float:
    return np.exp(-(mu1 + mu2)) * (mu1 / mu2) ** (k / 2) * iv(abs(k), 2 * np.sqrt(mu1 * mu2))

def skellam_cdf(k: int, mu1: float, mu2: float, max_diff: int = 50) -> float:
    return sum(skellam_pmf(i, mu1, mu2) for i in range(-max_diff, k + 1))

def spread_probability(spread: float, mu1: float, mu2: float, pick: str = "HOME") -> float:
    if pick == "HOME":
        return 1 - skellam_cdf(int(-spread), mu1, mu2)
    else:
        return skellam_cdf(int(-spread) - 1, mu1, mu2)

def total_probability(total: float, mu1: float, mu2: float, pick: str = "OVER") -> float:
    lambda_total = mu1 + mu2
    if pick == "OVER":
        return 1 - poisson.cdf(int(total), lambda_total)
    else:
        return poisson.cdf(int(total) - 1, lambda_total)

# =============================================================================
# BET TRACKER
# =============================================================================
class BetTracker:
    def __init__(self, db_path: str = "clarity_bets.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                sport TEXT,
                player TEXT,
                market TEXT,
                line REAL,
                pick TEXT,
                odds INTEGER,
                stake REAL,
                result TEXT,
                actual REAL,
                profit REAL,
                edge REAL,
                tier TEXT,
                notes TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def add_bet(self, player: str, market: str, line: float, pick: str, 
                odds: int, stake: float, sport: str, edge: float = 0, 
                tier: str = "", notes: str = "") -> int:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        date = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            INSERT INTO bets (date, sport, player, market, line, pick, odds, stake, edge, tier, notes, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (date, sport, player, market, line, pick, odds, stake, edge, tier, notes))
        bet_id = c.lastrowid
        conn.commit()
        conn.close()
        return bet_id
    
    def settle_bet(self, bet_id: int, result: str, actual: float = 0):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT stake, odds FROM bets WHERE id = ?", (bet_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return
        stake, odds = row
        decimal_odds = 1 + odds/100 if odds > 0 else 1 + 100/abs(odds)
        if result == "WIN":
            profit = stake * (decimal_odds - 1)
        elif result == "LOSS":
            profit = -stake
        else:
            profit = 0
        c.execute("UPDATE bets SET result = ?, actual = ?, profit = ? WHERE id = ?",
                  (result, actual, round(profit, 2), bet_id))
        conn.commit()
        conn.close()
    
    def get_all_bets(self) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets ORDER BY date DESC, id DESC", conn)
        conn.close()
        return df
    
    def get_pending_bets(self) -> pd.DataFrame:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result = 'PENDING' ORDER BY id DESC", conn)
        conn.close()
        return df
    
    def get_performance_summary(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN', 'LOSS', 'PUSH')", conn)
        conn.close()
        if df.empty:
            return {"total_bets": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_profit": 0, "roi": 0, "total_staked": 0, "avg_edge": 0}
        wins = len(df[df['result'] == 'WIN'])
        losses = len(df[df['result'] == 'LOSS'])
        total = wins + losses
        return {
            "total_bets": len(df),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_staked": round(df['stake'].sum(), 2),
            "total_profit": round(df['profit'].sum(), 2),
            "roi": round(df['profit'].sum() / df['stake'].sum() * 100, 2) if df['stake'].sum() > 0 else 0,
            "avg_edge": round(df['edge'].mean(), 2) if 'edge' in df.columns else 0
        }
    
    def get_performance_by_sport(self) -> dict:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN', 'LOSS', 'PUSH')", conn)
        conn.close()
        if df.empty:
            return {}
        by_sport = {}
        for sport in df['sport'].unique():
            sport_df = df[df['sport'] == sport]
            wins = len(sport_df[sport_df['result'] == 'WIN'])
            total = len(sport_df[sport_df['result'].isin(['WIN', 'LOSS'])])
            by_sport[sport] = {
                "bets": len(sport_df),
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "profit": round(sport_df['profit'].sum(), 2),
                "roi": round(sport_df['profit'].sum() / sport_df['stake'].sum() * 100, 2) if sport_df['stake'].sum() > 0 else 0
            }
        return by_sport
    
    def get_equity_curve(self) -> List[float]:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT profit FROM bets WHERE result IN ('WIN', 'LOSS', 'PUSH') ORDER BY date, id", conn)
        conn.close()
        if df.empty:
            return []
        equity = [0]
        for profit in df['profit']:
            equity.append(equity[-1] + profit)
        return equity

# =============================================================================
# PARLAY BUILDER
# =============================================================================
class ParlayBuilder:
    def __init__(self):
        self.correlation_matrix = {
            ("POINTS", "ASSISTS"): 0.65, ("POINTS", "PRA"): 0.85,
            ("ASSISTS", "PRA"): 0.70, ("REBOUNDS", "BLOCKS"): 0.45,
            ("KS", "OUTS"): 0.70, ("SOG", "GOALS"): 0.55,
        }
        self.legs = []
    
    def add_leg(self, player: str, market: str, line: float, pick: str, odds: int, edge: float) -> dict:
        leg = {
            "player": player,
            "market": market.upper(),
            "line": line,
            "pick": pick,
            "odds": odds,
            "edge": edge,
            "decimal_odds": 1 + odds/100 if odds > 0 else 1 + 100/abs(odds)
        }
        self.legs.append(leg)
        return self._check_correlation()
    
    def remove_leg(self, index: int):
        if 0 <= index < len(self.legs):
            self.legs.pop(index)
    
    def clear_legs(self):
        self.legs = []
    
    def _check_correlation(self) -> dict:
        if len(self.legs) < 2:
            return {"correlated": False, "level": "NONE", "issues": [], "warnings": []}
        issues, warnings = [], []
        for i in range(len(self.legs)):
            for j in range(i+1, len(self.legs)):
                l1, l2 = self.legs[i], self.legs[j]
                if l1['player'] == l2['player']:
                    issues.append(f"⚠️ Same player: {l1['player']}")
                pair = tuple(sorted([l1['market'], l2['market']]))
                if pair in self.correlation_matrix:
                    corr = self.correlation_matrix[pair]
                    if corr > 0.6:
                        warnings.append(f"📊 {l1['market']} + {l2['market']} are {corr:.0%} correlated")
        if issues:
            return {"correlated": True, "level": "HIGH", "issues": issues, "warnings": warnings}
        elif warnings:
            return {"correlated": True, "level": "MODERATE", "issues": issues, "warnings": warnings}
        return {"correlated": False, "level": "SAFE", "issues": [], "warnings": []}
    
    def calculate_parlay(self) -> dict:
        if not self.legs:
            return {
                "legs": 0, "total_odds": 0, "total_decimal": 0, "total_edge": 0, "payout": 0,
                "correlation": {"correlated": False, "level": "NONE", "issues": [], "warnings": []},
                "safe_anchor": False, "recommended_units": 0
            }
        total_decimal = 1.0
        total_edge = 0
        for leg in self.legs:
            total_decimal *= leg['decimal_odds']
            total_edge += leg['edge']
        correlation_check = self._check_correlation()
        safe_anchor = any(leg['edge'] >= 8.0 for leg in self.legs)
        if safe_anchor and not correlation_check['correlated']:
            units = 2.0
        elif not correlation_check['correlated']:
            units = 1.0
        else:
            units = 0.5
        return {
            "legs": len(self.legs),
            "total_odds": round((total_decimal - 1) * 100, 0),
            "total_decimal": round(total_decimal, 2),
            "total_edge": round(total_edge / len(self.legs), 1),
            "payout": round(100 * total_decimal, 2),
            "correlation": correlation_check,
            "safe_anchor": safe_anchor,
            "recommended_units": units
        }

# =============================================================================
# GAME ODDS ANALYZER WITH CLARITY APPROVAL
# =============================================================================
class GameOddsAnalyzer:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.sport_keys = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}
    
    def fetch_game_odds(self, sport: str) -> List[Dict]:
        sport_key = self.sport_keys.get(sport)
        if not sport_key:
            return []
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
            params = {"apiKey": self.api_key, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "decimal"}
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
        except:
            pass
        return []
    
    def _get_team_strength(self, team: str, sport: str) -> float:
        ratings = TEAM_RATINGS.get(sport, {})
        return ratings.get(team, 1500)
    
    def _calculate_moneyline_edge(self, home: str, away: str, sport: str, home_odds: float, away_odds: float) -> dict:
        home_elo = self._get_team_strength(home, sport)
        away_elo = self._get_team_strength(away, sport)
        home_adv = SPORT_MODELS.get(sport, {}).get("home_advantage", 1.05)
        
        r_home = home_elo + 50
        r_away = away_elo
        p_home = 1 / (1 + 10 ** ((r_away - r_home) / 400))
        p_away = 1 - p_home
        
        all_odds = [home_odds, away_odds]
        fair_probs = novig_probabilities(all_odds)
        home_edge = p_home - fair_probs[0]
        away_edge = p_away - fair_probs[1]
        
        return {
            "home": {"prob": p_home, "edge": home_edge, "odds": home_odds},
            "away": {"prob": p_away, "edge": away_edge, "odds": away_odds}
        }
    
    def _calculate_spread_edge(self, home: str, away: str, sport: str, spread: float, odds: float) -> dict:
        home_elo = self._get_team_strength(home, sport)
        away_elo = self._get_team_strength(away, sport)
        settings = SPORT_MODELS.get(sport, {})
        
        lambda_home = (home_elo / 1500) * 100
        lambda_away = (away_elo / 1500) * 100
        
        if spread < 0:
            prob = spread_probability(abs(spread), lambda_home, lambda_away, "HOME")
        else:
            prob = spread_probability(spread, lambda_home, lambda_away, "AWAY")
        
        implied = 1 / odds
        edge = prob - implied
        
        return {"prob": prob, "edge": edge, "odds": odds, "spread": spread}
    
    def _calculate_total_edge(self, home: str, away: str, sport: str, total: float, odds: float, pick: str) -> dict:
        home_elo = self._get_team_strength(home, sport)
        away_elo = self._get_team_strength(away, sport)
        
        lambda_home = (home_elo / 1500) * 100
        lambda_away = (away_elo / 1500) * 100
        
        prob = total_probability(total, lambda_home, lambda_away, pick)
        implied = 1 / odds
        edge = prob - implied
        
        return {"prob": prob, "edge": edge, "odds": odds, "total": total}
    
    def _assign_tier(self, edge: float) -> str:
        if edge >= 0.08: return "SAFE"
        elif edge >= 0.05: return "BALANCED+"
        elif edge >= 0.03: return "RISKY"
        else: return "PASS"
    
    def _get_verdict(self, edge: float) -> dict:
        tier = self._assign_tier(edge)
        if tier == "SAFE":
            signal, units = "🟢 SAFE", 2.0
        elif tier == "BALANCED+":
            signal, units = "🟡 BALANCED+", 1.5
        elif tier == "RISKY":
            signal, units = "🟠 RISKY", 0.5
        else:
            signal, units = "🔴 PASS", 0.0
        return {"signal": signal, "tier": tier, "units": units}
    
    def analyze_game(self, game: dict, sport: str) -> dict:
        home = game.get('home_team', '')
        away = game.get('away_team', '')
        
        best_odds = {"h2h": {}, "spreads": {}, "totals": {}}
        for book in game.get('bookmakers', []):
            for market in book.get('markets', []):
                market_key = market.get('key')
                if market_key == 'h2h':
                    for outcome in market.get('outcomes', []):
                        team = outcome['name']
                        if team not in best_odds['h2h'] or outcome['price'] > best_odds['h2h'][team]['odds']:
                            best_odds['h2h'][team] = {"odds": outcome['price'], "book": book['key']}
                elif market_key == 'spreads':
                    for outcome in market.get('outcomes', []):
                        team = outcome['name']
                        point = outcome.get('point', 0)
                        key = f"{team}_{point}"
                        if key not in best_odds['spreads'] or outcome['price'] > best_odds['spreads'][key]['odds']:
                            best_odds['spreads'][key] = {"odds": outcome['price'], "book": book['key'], "point": point, "team": team}
                elif market_key == 'totals':
                    for outcome in market.get('outcomes', []):
                        name = outcome['name']
                        point = outcome.get('point', 0)
                        key = f"{name}_{point}"
                        if key not in best_odds['totals'] or outcome['price'] > best_odds['totals'][key]['odds']:
                            best_odds['totals'][key] = {"odds": outcome['price'], "book": book['key'], "point": point, "name": name}
        
        # Calculate CLARITY edges
        analysis = {"home": home, "away": away, "moneyline": {}, "spreads": [], "totals": []}
        
        if len(best_odds['h2h']) >= 2:
            home_odds = best_odds['h2h'].get(home, {}).get('odds', 2.0)
            away_odds = best_odds['h2h'].get(away, {}).get('odds', 2.0)
            ml_edges = self._calculate_moneyline_edge(home, away, sport, home_odds, away_odds)
            
            analysis['moneyline'] = {
                "home": {**ml_edges['home'], **self._get_verdict(ml_edges['home']['edge']), "book": best_odds['h2h'].get(home, {}).get('book', '')},
                "away": {**ml_edges['away'], **self._get_verdict(ml_edges['away']['edge']), "book": best_odds['h2h'].get(away, {}).get('book', '')}
            }
        
        for key, data in best_odds['spreads'].items():
            spread_edge = self._calculate_spread_edge(home, away, sport, data['point'], data['odds'])
            verdict = self._get_verdict(spread_edge['edge'])
            analysis['spreads'].append({
                "team": data['team'], "spread": data['point'], "odds": data['odds'],
                "book": data['book'], "prob": spread_edge['prob'], "edge": spread_edge['edge'],
                **verdict
            })
        
        for key, data in best_odds['totals'].items():
            total_edge = self._calculate_total_edge(home, away, sport, data['point'], data['odds'], data['name'])
            verdict = self._get_verdict(total_edge['edge'])
            analysis['totals'].append({
                "name": data['name'], "total": data['point'], "odds": data['odds'],
                "book": data['book'], "prob": total_edge['prob'], "edge": total_edge['edge'],
                **verdict
            })
        
        return analysis

# =============================================================================
# UNIFIED API CLIENT
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

# =============================================================================
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.tracker = BetTracker()
        self.parlay_builder = ParlayBuilder()
        self.game_analyzer = GameOddsAnalyzer(UNIFIED_API_KEY)
        self.sims = 10000
    
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
    
    def simulate_prop(self, data: List[float], line: float, pick: str, sport: str = "NBA") -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        w = np.ones(len(data)); w[-3:] *= 1.5; w /= w.sum()
        lam = np.average(data, weights=w)
        if model["distribution"] == "nbinom":
            n = max(1, int(lam / 2)); p = n / (n + lam)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims >= line) if pick == "OVER" else np.mean(sims <= line)
        dtm = (proj - line) / line if line != 0 else 0
        return {"proj": proj, "prob": prob, "dtm": dtm}
    
    def analyze_elite(self, player: str, market: str, line: float, pick: str,
                      data: List[float], sport: str, odds: int) -> dict:
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        sim = self.simulate_prop(data, line, pick, sport)
        raw_edge = (sim["prob"] - 0.524) * 2
        n = len(data)
        penalty = 0.50 if n < 5 else 0.25 if n < 10 else 0.10 if n < 20 else 0.00
        adj_edge = raw_edge * (1 - penalty)
        if market.upper() in RED_TIER_PROPS:
            tier = "REJECT"
        else:
            tier = "SAFE" if adj_edge >= 0.08 else "BALANCED+" if adj_edge >= 0.05 else "RISKY" if adj_edge >= 0.03 else "PASS"
        return {
            "player": player, "market": market, "line": line, "pick": pick,
            "projection": sim["proj"], "probability": sim["prob"], "dtm": sim["dtm"],
            "raw_edge": raw_edge, "adjusted_edge": adj_edge, "tier": tier,
            "l42_msg": l42_msg, "l42_pass": l42_pass
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE - GAME ODDS WITH APPROVAL")
    st.markdown(f"**Smart Analysis | Bet Tracker | Parlay Builder
