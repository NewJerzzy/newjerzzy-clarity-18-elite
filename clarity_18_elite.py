# =============================================================================
# CLARITY 18.0 ELITE - WITH PARLAY BUILDER
# =============================================================================
import streamlit as st
import numpy as np
import pandas as pd
from scipy.stats import poisson
from datetime import datetime
import sqlite3
import warnings
warnings.filterwarnings('ignore')

VERSION = "18.0 Elite"

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
                profit REAL,
                edge REAL,
                tier TEXT
            )
        """)
        conn.commit()
        conn.close()
    
    def add_bet(self, player, market, line, pick, odds, stake, sport, edge=0, tier=""):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        date = datetime.now().strftime("%Y-%m-%d")
        c.execute("""
            INSERT INTO bets (date, sport, player, market, line, pick, odds, stake, edge, tier, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """, (date, sport, player, market, line, pick, odds, stake, edge, tier))
        conn.commit()
        conn.close()
        return c.lastrowid
    
    def settle_bet(self, bet_id, result):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT stake, odds FROM bets WHERE id = ?", (bet_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return
        stake, odds = row
        decimal_odds = 1 + odds/100 if odds > 0 else 1 + 100/abs(odds)
        profit = stake * (decimal_odds - 1) if result == "WIN" else (-stake if result == "LOSS" else 0)
        c.execute("UPDATE bets SET result = ?, profit = ? WHERE id = ?", (result, round(profit, 2), bet_id))
        conn.commit()
        conn.close()
    
    def get_pending_bets(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result = 'PENDING' ORDER BY id DESC", conn)
        conn.close()
        return df
    
    def get_all_bets(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets ORDER BY date DESC, id DESC", conn)
        conn.close()
        return df
    
    def get_summary(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN', 'LOSS', 'PUSH')", conn)
        conn.close()
        if df.empty:
            return {"total_bets": 0, "wins": 0, "losses": 0, "win_rate": 0, "profit": 0, "roi": 0}
        wins = len(df[df['result'] == 'WIN'])
        losses = len(df[df['result'] == 'LOSS'])
        total = wins + losses
        return {
            "total_bets": len(df),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "profit": round(df['profit'].sum(), 2),
            "roi": round(df['profit'].sum() / df['stake'].sum() * 100, 2) if df['stake'].sum() > 0 else 0
        }

# =============================================================================
# PARLAY BUILDER
# =============================================================================
class ParlayBuilder:
    def __init__(self):
        self.legs = []
        self.correlation_matrix = {
            ("POINTS", "ASSISTS"): 0.65, ("POINTS", "PRA"): 0.85,
            ("ASSISTS", "PRA"): 0.70, ("REBOUNDS", "BLOCKS"): 0.45,
            ("KS", "OUTS"): 0.70, ("SOG", "GOALS"): 0.55,
        }
    
    def add_leg(self, player, market, line, pick, odds, edge):
        leg = {
            "player": player, "market": market.upper(), "line": line,
            "pick": pick, "odds": odds, "edge": edge,
            "decimal_odds": 1 + odds/100 if odds > 0 else 1 + 100/abs(odds)
        }
        self.legs.append(leg)
    
    def remove_leg(self, index):
        if 0 <= index < len(self.legs):
            self.legs.pop(index)
    
    def clear_legs(self):
        self.legs = []
    
    def check_correlation(self):
        if len(self.legs) < 2:
            return {"level": "NONE", "issues": [], "warnings": []}
        issues, warnings = [], []
        for i in range(len(self.legs)):
            for j in range(i+1, len(self.legs)):
                l1, l2 = self.legs[i], self.legs[j]
                if l1['player'] == l2['player']:
                    issues.append(f"Same player: {l1['player']}")
                pair = tuple(sorted([l1['market'], l2['market']]))
                if pair in self.correlation_matrix:
                    corr = self.correlation_matrix[pair]
                    if corr > 0.6:
                        warnings.append(f"{l1['market']} + {l2['market']} are {corr:.0%} correlated")
        if issues:
            return {"level": "HIGH", "issues": issues, "warnings": warnings}
        elif warnings:
            return {"level": "MODERATE", "issues": issues, "warnings": warnings}
        return {"level": "SAFE", "issues": [], "warnings": []}
    
    def calculate(self):
        if not self.legs:
            return {"legs": 0, "total_odds": 0, "avg_edge": 0, "payout": 0, "units": 0}
        total_decimal = 1.0
        total_edge = 0
        for leg in self.legs:
            total_decimal *= leg['decimal_odds']
            total_edge += leg['edge']
        corr = self.check_correlation()
        safe_anchor = any(leg['edge'] >= 8.0 for leg in self.legs)
        units = 2.0 if safe_anchor and corr['level'] == 'SAFE' else (1.0 if corr['level'] == 'SAFE' else 0.5)
        return {
            "legs": len(self.legs), "total_odds": round((total_decimal - 1) * 100, 0),
            "avg_edge": round(total_edge / len(self.legs), 1), "payout": round(100 * total_decimal, 2),
            "units": units, "correlation": corr, "safe_anchor": safe_anchor
        }

# =============================================================================
# CLARITY ENGINE
# =============================================================================
class ClarityEngine:
    def __init__(self):
        self.tracker = BetTracker()
        self.parlay = ParlayBuilder()
    
    def analyze_prop(self, player, market, line, pick, data, sport):
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        lam = np.average(data, weights=w)
        sims = poisson.rvs(lam, size=10000)
        proj = np.mean(sims)
        prob = np.mean(sims >= line) if pick == "OVER" else np.mean(sims <= line)
        raw_edge = (prob - 0.524) * 2
        n = len(data)
        penalty = 0.50 if n < 5 else 0.25 if n < 10 else 0.10 if n < 20 else 0.00
        adj_edge = raw_edge * (1 - penalty)
        tier = "SAFE" if adj_edge >= 0.08 else "BALANCED+" if adj_edge >= 0.05 else "RISKY" if adj_edge >= 0.03 else "PASS"
        return {
            "player": player, "market": market, "line": line, "pick": pick,
            "projection": round(proj, 1), "probability": round(prob, 3),
            "raw_edge": round(raw_edge, 3), "adjusted_edge": round(adj_edge, 3),
            "tier": tier
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = ClarityEngine()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE")
    st.markdown(f"**{VERSION}**")
    
    tracker = engine.tracker
    parlay = engine.parlay
    
    with st.sidebar:
        st.header("📊 PORTFOLIO")
        summary = tracker.get_summary()
        st.metric("Total Bets", summary['total_bets'])
        st.metric("Win Rate", f"{summary['win_rate']}%")
        st.metric("ROI", f"{summary['roi']}%")
        st.metric("Profit", f"${summary['profit']}")
    
    tab1, tab2, tab3 = st.tabs(["🎯 ANALYZE PROP", "🔗 PARLAY BUILDER", "📊 BET TRACKER"])
    
    with tab1:
        st.header("Analyze Player Prop")
        col1, col2 = st.columns(2)
        with col1:
            player = st.text_input("Player", "Paul Skenes")
            market = st.text_input("Market", "Ks")
            line = st.number_input("Line", 0.5, 50.0, 6.5)
            pick = st.selectbox("Pick", ["OVER", "UNDER"])
            sport = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"])
        with col2:
            data_str = st.text_area("Recent Games", "6, 7, 5, 8, 6, 5, 7, 6")
            odds = st.number_input("Odds", -500, 500, -110)
        
        if st.button("🚀 ANALYZE", type="primary"):
            data = [float(x.strip()) for x in data_str.split(",")]
            result = engine.analyze_prop(player, market, line, pick, data, sport)
            st.markdown(f"### Tier: {result['tier']}")
            col1, col2, col3 = st.columns(3)
            with col1: st.metric("Projection", result['projection'])
            with col2: st.metric("Probability", f"{result['probability']:.1%}")
            with col3: st.metric("Edge", f"{result['adjusted_edge']:+.1%}")
            
            if result['tier'] in ['SAFE', 'BALANCED+']:
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("📝 Add to Tracker"):
                        tracker.add_bet(player, market, line, pick, odds, 50.0, sport, result['adjusted_edge'], result['tier'])
                        st.success("✅ Bet logged!")
                        st.rerun()
                with c2:
                    if st.button("🔗 Add to Parlay"):
                        parlay.add_leg(player, market, line, pick, odds, result['adjusted_edge'])
                        st.success("✅ Added to parlay!")
                        st.rerun()
    
    with tab2:
        st.header("Parlay Builder")
        col1, col2 = st.columns([2, 1])
        with col1:
            with st.expander("➕ Add Leg Manually"):
                p_player = st.text_input("Player", key="p_player")
                p_market = st.text_input("Market", key="p_market")
                p_line = st.number_input("Line", 0.5, 50.0, 22.5, key="p_line")
                p_pick = st.selectbox("Pick", ["OVER", "UNDER"], key="p_pick")
                p_odds = st.number_input("Odds", -500, 500, -110, key="p_odds")
                p_edge = st.number_input("Edge %", 0.0, 20.0, 5.0, key="p_edge")
                if st.button("Add to Parlay"):
                    parlay.add_leg(p_player, p_market, p_line, p_pick, p_odds, p_edge)
                    st.rerun()
        with col2:
            result = parlay.calculate()
            st.metric("Legs", result['legs'])
            st.metric("Total Odds", f"+{result['total_odds']}")
            st.metric("Avg Edge", f"{result['avg_edge']}%")
            st.metric("$100 Payout", f"${result['payout']}")
            st.metric("Units", result['units'])
            corr = result['correlation']
            if corr['level'] != 'SAFE':
                st.warning(f"⚠️ Correlation: {corr['level']}")
                for issue in corr['issues']:
                    st.error(issue)
                for warn in corr['warnings']:
                    st.warning(warn)
            if result['safe_anchor']:
                st.success("✅ SAFE anchor present")
        
        st.divider()
        st.subheader("Current Parlay Legs")
        for i, leg in enumerate(parlay.legs):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.write(f"**{leg['player']} {leg['market']} {leg['pick']} {leg['line']}** | Odds: {leg['odds']} | Edge: {leg['edge']}%")
            with c2:
                if st.button("❌", key=f"remove_{i}"):
                    parlay.remove_leg(i)
                    st.rerun()
        if parlay.legs:
            if st.button("🗑️ Clear All"):
                parlay.clear_legs()
                st.rerun()
    
    with tab3:
        st.header("Bet Tracker")
        summary = tracker.get_summary()
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("Total Bets", summary['total_bets'])
        with col2: st.metric("Wins", summary['wins'])
        with col3: st.metric("Losses", summary['losses'])
        with col4: st.metric("Win Rate", f"{summary['win_rate']}%")
        st.divider()
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
        st.subheader("📋 Bet History")
        all_bets = tracker.get_all_bets()
        if not all_bets.empty:
            st.dataframe(all_bets[['date', 'sport', 'player', 'market', 'pick', 'line', 'result', 'profit']], use_container_width=True, hide_index=True)

if __name__ == "__main__":
    run_dashboard()
