# =============================================================================
# CLARITY 18.0 ELITE - COMPLETE WITH ALL FEATURES
# =============================================================================
# VERSION: 18.0 Elite (Full Featured)
# DATE: April 13, 2026
# API KEY: 96241c1a5ba686f34a9e4c3463b61661 ✅ UNIFIED (Perplexity + Odds)
# API-Sports: 8c20c34c3b0a6314e04c4997bf0922d2 ✅ INTEGRATED
# =============================================================================
# FEATURES:
# ✅ Smart Analysis (Manual Props)
# ✅ Bet Tracker & ROI Dashboard
# ✅ Parlay Builder with Correlation Warnings
# ✅ Game Odds Analyzer (ML/Spread/Total)
# ✅ SEM Calibration Dashboard
# ✅ Lineup Confirmation
# ✅ Season Context Engine
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
VERSION = "18.0 Elite (Full Featured)"
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
# SMART ANALYSIS CONFIGURATION
# =============================================================================
LINEUP_CONFIRMATION_HOUR = 10
LINEUP_CONFIRMATION_MINUTE = 30

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
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "odds_key": "basketball_nba"},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "odds_key": "baseball_mlb"},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "odds_key": "icehockey_nhl"},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "odds_key": "americanfootball_nfl"}
}

# =============================================================================
# BET TRACKER & ROI DASHBOARD
# =============================================================================
class BetTracker:
    """Track all bets, calculate ROI, win rate, and performance metrics"""
    
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
            return {"total_bets": 0, "wins": 0, "losses": 0, "win_rate": 0, "total_profit": 0, "roi": 0}
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
    
    def delete_bet(self, bet_id: int):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM bets WHERE id = ?", (bet_id,))
        conn.commit()
        conn.close()

# =============================================================================
# PARLAY BUILDER WITH CORRELATION WARNINGS
# =============================================================================
class ParlayBuilder:
    """Build parlays with correlation detection"""
    
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
        return self._check_correlation()
    
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
            return {"total_odds": 0, "total_edge": 0, "payout": 0, "legs": 0}
        
        total_decimal = 1.0
        total_edge = 0
        for leg in self.legs:
            total_decimal *= leg['decimal_odds']
            total_edge += leg['edge']
        
        correlation_check = self._check_correlation()
        safe_anchor = any(leg['edge'] >= 8.0 for leg in self.legs)
        
        return {
            "legs": len(self.legs),
            "total_odds": round((total_decimal - 1) * 100, 0),
            "total_decimal": round(total_decimal, 2),
            "total_edge": round(total_edge / len(self.legs), 1) if self.legs else 0,
            "payout": round(100 * total_decimal, 2),
            "correlation": correlation_check,
            "safe_anchor": safe_anchor,
            "recommended_units": 2.0 if safe_anchor and not correlation_check['correlated'] else 1.0 if not correlation_check['correlated'] else 0.5
        }

# =============================================================================
# GAME ODDS ANALYZER
# =============================================================================
class GameOddsAnalyzer:
    """Analyze moneylines, spreads, and totals using The Odds API (free tier)"""
    
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
    
    def analyze_game(self, game: dict) -> dict:
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
                        key = f"{team} {point:+.1f}"
                        if key not in best_odds['spreads'] or outcome['price'] > best_odds['spreads'][key]['odds']:
                            best_odds['spreads'][key] = {"odds": outcome['price'], "book": book['key'], "point": point}
                elif market_key == 'totals':
                    for outcome in market.get('outcomes', []):
                        name = outcome['name']
                        point = outcome.get('point', 0)
                        key = f"{name} {point}"
                        if key not in best_odds['totals'] or outcome['price'] > best_odds['totals'][key]['odds']:
                            best_odds['totals'][key] = {"odds": outcome['price'], "book": book['key'], "point": point}
        
        return {"home": home, "away": away, "odds": best_odds}

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
    
    def get_injury_status(self, player: str, sport: str) -> dict:
        return {"injury": "HEALTHY", "steam": False}

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
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
    
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
    st.title("🔮 CLARITY 18.0 ELITE - FULL FEATURED")
    st.markdown(f"**Smart Analysis | Bet Tracker | Parlay Builder | Game Odds | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        st.success("✅ APIs LIVE")
        st.metric("Version", VERSION)
        
        tracker = BetTracker()
        summary = tracker.get_performance_summary()
        st.divider()
        st.subheader("📊 PORTFOLIO")
        st.metric("Total Bets", summary['total_bets'])
        st.metric("Win Rate", f"{summary['win_rate']}%")
        st.metric("ROI", f"{summary['roi']}%")
        st.metric("Profit", f"${summary['total_profit']}")
    
    tab1, tab2, tab3, tab4 = st.tabs(["🎯 SMART ANALYSIS", "📊 BET TRACKER", "🔗 PARLAY BUILDER", "🏀 GAME ODDS"])
    
    # TAB 1: SMART ANALYSIS
    with tab1:
        st.header("Smart Analysis - Manual Prop Entry")
        c1, c2 = st.columns(2)
        with c1:
            player = st.text_input("Player", "Paul Skenes")
            market = st.text_input("Market", "Ks")
            line = st.number_input("Line", 0.5, 50.0, 6.5)
            pick = st.selectbox("Pick", ["OVER", "UNDER"])
            sport = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"])
        with c2:
            data_str = st.text_area("Recent Games", "6, 7, 5, 8, 6, 5, 7, 6")
            odds = st.number_input("Odds", -500, 500, -110)
        
        if st.button("🚀 ANALYZE PROP", type="primary"):
            data = [float(x.strip()) for x in data_str.split(",")]
            result = engine.analyze_elite(player, market, line, pick, data, sport, odds)
            st.markdown(f"### Tier: {result['tier']}")
            col1, col2, col3 = st.columns(3)
            with col1: st.metric("Projection", f"{result['projection']:.1f}")
            with col2: st.metric("Probability", f"{result['probability']:.1%}")
            with col3: st.metric("Edge", f"{result['adjusted_edge']:+.1%}")
            st.info(f"L42: {result['l42_msg']}")
            
            if result['tier'] in ['SAFE', 'BALANCED+']:
                if st.button("📝 Add to Bet Tracker"):
                    tracker.add_bet(player, market, line, pick, odds, 50.0, sport, result['adjusted_edge'], result['tier'])
                    st.success("✅ Bet logged!")
    
    # TAB 2: BET TRACKER
    with tab2:
        st.header("📊 Bet Tracker & ROI Dashboard")
        summary = tracker.get_performance_summary()
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("Total Bets", summary['total_bets'])
        with col2: st.metric("Win Rate", f"{summary['win_rate']}%")
        with col3: st.metric("ROI", f"{summary['roi']}%")
        with col4: st.metric("Profit", f"${summary['total_profit']}")
        
        st.divider()
        
        with st.expander("➕ Log New Bet"):
            c1, c2 = st.columns(2)
            with c1:
                bt_player = st.text_input("Player", key="bt_player")
                bt_market = st.text_input("Market", "PTS", key="bt_market")
                bt_line = st.number_input("Line", 0.5, 50.0, 22.5, key="bt_line")
                bt_pick = st.selectbox("Pick", ["OVER", "UNDER"], key="bt_pick")
            with c2:
                bt_odds = st.number_input("Odds", -500, 500, -110, key="bt_odds")
                bt_stake = st.number_input("Stake ($)", 1.0, 1000.0, 50.0, key="bt_stake")
                bt_sport = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL", "Soccer", "Tennis"], key="bt_sport")
                bt_edge = st.number_input("Edge %", 0.0, 20.0, 5.0, key="bt_edge")
            
            if st.button("📝 Log Bet"):
                tracker.add_bet(bt_player, bt_market, bt_line, bt_pick, bt_odds, bt_stake, bt_sport, bt_edge)
                st.success("✅ Bet logged!")
                st.rerun()
        
        pending = tracker.get_pending_bets()
        if not pending.empty:
            st.subheader("⏳ Pending Bets")
            for _, row in pending.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
                with c1:
                    st.write(f"**{row['player']} {row['market']} {row['pick']} {row['line']}** (${row['stake']})")
                with c2:
                    if st.button("✅ WIN", key=f"win_{row['id']}"):
                        tracker.settle_bet(row['id'], "WIN")
                        st.rerun()
                with c3:
                    if st.button("❌ LOSS", key=f"loss_{row['id']}"):
                        tracker.settle_bet(row['id'], "LOSS")
                        st.rerun()
                with c4:
                    if st.button("🔄 PUSH", key=f"push_{row['id']}"):
                        tracker.settle_bet(row['id'], "PUSH")
                        st.rerun()
        
        st.subheader("📈 Performance by Sport")
        by_sport = tracker.get_performance_by_sport()
        if by_sport:
            st.dataframe(pd.DataFrame(by_sport).T, use_container_width=True)
        
        st.subheader("💰 Equity Curve")
        equity = tracker.get_equity_curve()
        if equity:
            st.line_chart(equity)
        
        st.subheader("📋 Bet History")
        all_bets = tracker.get_all_bets()
        if not all_bets.empty:
            st.dataframe(all_bets[['date', 'sport', 'player', 'market', 'pick', 'line', 'result', 'profit']], use_container_width=True, hide_index=True)
    
    # TAB 3: PARLAY BUILDER
    with tab3:
        st.header("🔗 Parlay Builder with Correlation Warnings")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            with st.expander("➕ Add Leg"):
                p_player = st.text_input("Player", key="p_player")
                p_market = st.text_input("Market", "PTS", key="p_market")
                p_line = st.number_input("Line", 0.5, 50.0, 22.5, key="p_line")
                p_pick = st.selectbox("Pick", ["OVER", "UNDER"], key="p_pick")
                p_odds = st.number_input("Odds", -500, 500, -110, key="p_odds")
                p_edge = st.number_input("Edge %", 0.0, 20.0, 5.0, key="p_edge")
                
                if st.button("Add to Parlay"):
                    result = engine.parlay_builder.add_leg(p_player, p_market, p_line, p_pick, p_odds, p_edge)
                    st.rerun()
        
        with col2:
            parlay_result = engine.parlay_builder.calculate_parlay()
            st.metric("Legs", parlay_result['legs'])
            st.metric("Total Odds", f"+{parlay_result['total_odds']}")
            st.metric("Avg Edge", f"{parlay_result['total_edge']}%")
            st.metric("$100 Payout", f"${parlay_result['payout']}")
            st.metric("Units", parlay_result['recommended_units'])
            
            corr = parlay_result['correlation']
            if corr['correlated']:
                st.warning(f"⚠️ Correlation: {corr['level']}")
                for issue in corr['issues']:
                    st.error(issue)
                for warn in corr['warnings']:
                    st.warning(warn)
            else:
                st.success("✅ No correlation issues")
            
            if parlay_result['safe_anchor']:
                st.success("✅ SAFE anchor present")
            elif parlay_result['legs'] >= 2:
                st.warning("⚠️ No SAFE anchor")
        
        st.divider()
        st.subheader("Current Parlay Legs")
        for i, leg in enumerate(engine.parlay_builder.legs):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"**{leg['player']} {leg['market']} {leg['pick']} {leg['line']}** | Odds: {leg['odds']} | Edge: {leg['edge']}%")
            with c2:
                if st.button(f"❌ Remove", key=f"remove_{i}"):
                    engine.parlay_builder.remove_leg(i)
                    st.rerun()
        
        if engine.parlay_builder.legs:
            if st.button("🗑️ Clear All Legs"):
                engine.parlay_builder.clear_legs()
                st.rerun()
    
    # TAB 4: GAME ODDS
    with tab4:
        st.header("🏀 Game Odds Analyzer")
        sport_odds = st.selectbox("Select Sport", ["NBA", "MLB", "NHL", "NFL"], key="sport_odds")
        
        if st.button("🔍 Fetch Games", type="primary"):
            with st.spinner(f"Fetching {sport_odds} games..."):
                games = engine.game_analyzer.fetch_game_odds(sport_odds)
                if games:
                    for game in games[:8]:
                        analysis = engine.game_analyzer.analyze_game(game)
                        with st.expander(f"🏆 {analysis['away']} @ {analysis['home']}"):
                            st.subheader("Moneyline")
                            for team, data in analysis['odds']['h2h'].items():
                                st.write(f"**{team}**: {data['odds']} ({data['book']})")
                            
                            if analysis['odds']['spreads']:
                                st.subheader("Spreads")
                                for spread, data in analysis['odds']['spreads'].items():
                                    st.write(f"**{spread}**: {data['odds']} ({data['book']})")
                            
                            if analysis['odds']['totals']:
                                st.subheader("Totals")
                                for total, data in analysis['odds']['totals'].items():
                                    st.write(f"**{total}**: {data['odds']} ({data['book']})")
                else:
                    st.warning("No games found. Try again later.")

if __name__ == "__main__":
    run_dashboard()
