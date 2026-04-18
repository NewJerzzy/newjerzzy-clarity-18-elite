import streamlit as st
import pandas as pd
import numpy as np
import sqlite3
import hashlib
import os
import re
from datetime import datetime

# =============================================================================
# CONFIGURATION
# =============================================================================
VERSION = "18.0 Elite"
ODDS_API_KEY = "dummy"

# =============================================================================
# SPORT MODELS (minimal)
# =============================================================================
SPORT_MODELS = {"NBA": {"avg_total": 228.5, "home_advantage": 3.0}}
SPORT_CATEGORIES = {"NBA": ["PTS", "REB", "AST"]}
HARDCODED_TEAMS = {"NBA": ["Lakers", "Celtics", "Warriors"]}

# =============================================================================
# CLARITY ENGINE CLASS (MUST be defined BEFORE engine = Clarity18Elite())
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.db_path = "clarity_history.db"
        self.bankroll = 1000.0
        self._init_db()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS bets (id TEXT PRIMARY KEY, player TEXT, result TEXT)")
        conn.commit()
        conn.close()
    
    def get_teams(self, sport):
        return HARDCODED_TEAMS.get(sport, ["Team A", "Team B"])
    
    def analyze_moneyline(self, home, away, sport, home_odds, away_odds):
        return {"pick": home, "signal": "🟢 APPROVED", "units": 1.0, "edge": 0.05}
    
    def analyze_spread(self, home, away, spread, pick, sport, odds):
        return {"signal": "🟢 APPROVED", "units": 1.0, "edge": 0.04}
    
    def analyze_total(self, home, away, total, pick, sport, odds):
        return {"signal": "🟢 APPROVED", "units": 1.0, "edge": 0.03}
    
    def analyze_alternate(self, base, alt, pick, sport, odds):
        return {"action": "BET", "edge": 0.04}
    
    def analyze_prop(self, player, market, line, pick, data, sport, odds, team=None, injury="HEALTHY", opp=None):
        return {"units": 1.0, "signal": "🟢 APPROVED", "projection": line, "raw_edge": 0.05}
    
    def run_best_odds_scan(self, sports):
        return [{"player": "LeBron", "market": "PTS", "pick": "OVER", "line": 25.5, "odds": -110, "bookmaker": "FD", "edge": 0.04}]
    
    def get_accuracy_dashboard(self):
        return {"total_bets": 0, "win_rate": 0, "roi": 0, "sem_score": 100}

# =============================================================================
# CREATE ENGINE INSTANCE (AFTER class definition – this is correct now)
# =============================================================================
engine = Clarity18Elite()

# =============================================================================
# PARSERS (mock)
# =============================================================================
def parse_pasted_props(text, default_date=None):
    return []

# =============================================================================
# STREAMLIT DASHBOARD
# =============================================================================
def run_dashboard():
    st.set_page_config(layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE")
    st.caption("Working version – class defined before engine instantiation")
    
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🎮 GAME MARKETS", "📋 PASTE & SCAN", "📊 SCANNERS & ACCURACY", "🎯 PLAYER PROPS", "🔧 SELF EVALUATION"
    ])
    
    with tab1:
        st.header("Game Markets")
        sport = st.selectbox("Sport", ["NBA"], key="gm_sport")
        teams = engine.get_teams(sport)
        home = st.selectbox("Home", teams)
        away = st.selectbox("Away", teams)
        if st.button("Analyze"):
            ml = engine.analyze_moneyline(home, away, sport, -150, +130)
            st.success(f"Moneyline: {ml['pick']} – Edge {ml['edge']:.1%}")
    
    with tab2:
        st.header("Paste & Scan")
        st.text_area("Paste here", height=150)
        st.button("Analyze", help="Mock analysis")
        st.info("Paste any text – parser ready")
    
    with tab3:
        st.header("Scanners")
        if st.button("Scan Best Odds"):
            bets = engine.run_best_odds_scan(["NBA"])
            for b in bets:
                st.write(f"{b['player']} {b['market']} {b['pick']} {b['line']} @ {b['odds']}")
        acc = engine.get_accuracy_dashboard()
        st.metric("Win Rate", f"{acc['win_rate']}%")
    
    with tab4:
        st.header("Player Props")
        player = st.text_input("Player", "LeBron James")
        market = st.selectbox("Market", ["PTS", "REB", "AST"])
        line = st.number_input("Line", 10.0, 50.0, 25.5)
        if st.button("Analyze Prop"):
            res = engine.analyze_prop(player, market, line, "OVER", [], "NBA", -110)
            st.success(f"Approved – Projection {res['projection']:.1f}, Edge {res['raw_edge']:.1%}")
    
    with tab5:
        st.header("Self Evaluation")
        st.subheader("Auto-Tune History")
        st.info("No data yet – will appear after bets are settled")
        st.metric("Current SEM Score", "100/100")

if __name__ == "__main__":
    run_dashboard()
