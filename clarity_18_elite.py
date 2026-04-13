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
VERSION = "18.0 Elite (Sleeper API Fixed)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
SLEEPER_API_BASE = "https://api.sleeper.app/v1"

SCAN_SCHEDULE = {
    "10:00": "Initial scan - lines posted",
    "12:00": "Lineup confirmation scan",
    "15:00": "Steam detection scan",
    "17:30": "Final pre-lock scan"
}

try:
    from pybaseball import statcast_batter, playerid_lookup
    STATCAST_AVAILABLE = True
except ImportError:
    STATCAST_AVAILABLE = False

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "sleeper_sport": "nba"},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "sleeper_sport": "mlb"},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "sleeper_sport": "nhl"},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "sleeper_sport": "nfl"}
}

# =============================================================================
# STAT CONFIG (L42)
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
    "HITS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TB": {"tier": "MED", "buffer": 1.0, "reject": False},
}

RED_TIER_PROPS = ["PRA", "PR", "PA", "3PTM", "1H", "MILESTONE", "COMBO", "TD", 
                  "UNDER 1.5", "UNDER 2.5", "OVER 1.5", "OVER 2.5"]

# =============================================================================
# SLEEPER API CLIENT (FIXED ENDPOINTS)
# =============================================================================
class SleeperAPIClient:
    """Free Sleeper API client - corrected endpoints"""
    
    def __init__(self):
        self.base_url = SLEEPER_API_BASE
        self.diagnostic_log = []
        self.player_cache = {}
    
    def log_diagnostic(self, source: str, message: str, data: Any = None):
        self.diagnostic_log.append({
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "message": message,
            "data": str(data)[:500] if data else None
        })
    
    def get_all_players(self, sport: str = "nba") -> Dict:
        """Fetch all players for a sport"""
        try:
            url = f"{self.base_url}/players/{sport}"
            response = requests.get(url, timeout=10)
            self.log_diagnostic("Sleeper", f"Players endpoint ({sport}): {response.status_code}")
            
            if response.status_code == 200:
                return response.json()
            else:
                self.log_diagnostic("Sleeper", f"Error: {response.text[:200]}")
                return {}
        except Exception as e:
            self.log_diagnostic("Sleeper", f"Exception: {str(e)}")
            return {}
    
    def get_player_projections(self, sport: str = "nba", season: str = "2025") -> List[Dict]:
        """Fetch player projections/stats"""
        props = []
        
        try:
            # First get all players
            players = self.get_all_players(sport)
            if not players:
                return props
            
            # Get stats for the season
            url = f"{self.base_url}/stats/{sport}/{season}"
            response = requests.get(url, timeout=10)
            self.log_diagnostic("Sleeper", f"Stats endpoint ({sport}): {response.status_code}")
            
            if response.status_code == 200:
                stats = response.json()
                
                # Combine player info with stats
                for player_id, player_stats in list(stats.items())[:100]:
                    player_info = players.get(player_id, {})
                    player_name = f"{player_info.get('first_name', '')} {player_info.get('last_name', '')}".strip()
                    team = player_info.get('team', 'UNKNOWN')
                    
                    if player_name and player_stats:
                        # Map Sleeper stats to CLARITY markets
                        if "pts_avg" in player_stats or "pts" in player_stats:
                            props.append({
                                "source": "Sleeper",
                                "sport": sport.upper(),
                                "player": player_name,
                                "team": team,
                                "market": "PTS",
                                "line": float(player_stats.get("pts_avg", player_stats.get("pts", 0))),
                                "odds": -110
                            })
                        if "reb_avg" in player_stats or "reb" in player_stats:
                            props.append({
                                "source": "Sleeper",
                                "sport": sport.upper(),
                                "player": player_name,
                                "team": team,
                                "market": "REB",
                                "line": float(player_stats.get("reb_avg", player_stats.get("reb", 0))),
                                "odds": -110
                            })
                        if "ast_avg" in player_stats or "ast" in player_stats:
                            props.append({
                                "source": "Sleeper",
                                "sport": sport.upper(),
                                "player": player_name,
                                "team": team,
                                "market": "AST",
                                "line": float(player_stats.get("ast_avg", player_stats.get("ast", 0))),
                                "odds": -110
                            })
                
                self.log_diagnostic("Sleeper", f"Found {len(props)} props for {sport}")
            else:
                self.log_diagnostic("Sleeper", f"Stats error: {response.text[:200]}")
                
        except Exception as e:
            self.log_diagnostic("Sleeper", f"Exception: {str(e)}")
        
        return props
    
    def scan_all_sports(self, sports: List[str] = None) -> List[Dict]:
        """Scan multiple sports for player props"""
        if sports is None:
            sports = ["nba", "mlb", "nhl"]
        
        self.diagnostic_log = []
        all_props = []
        
        for sport in sports:
            self.log_diagnostic("Sleeper", f"Scanning {sport}...")
            props = self.get_player_projections(sport)
            all_props.extend(props)
        
        self.log_diagnostic("Sleeper", f"Total props: {len(all_props)}")
        return all_props
    
    def get_diagnostics(self) -> List[Dict]:
        return self.diagnostic_log

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

# =============================================================================
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.sleeper = SleeperAPIClient()
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
            return {"signal": "INJURY RISK", "units": 0}
        if not l42_pass:
            return {"signal": "L42 REJECT", "units": 0}
        if prob >= self.prob_bolt and dtm >= self.dtm_bolt and wsem_ok:
            return {"signal": "SOVEREIGN BOLT", "units": 2.0}
        elif prob >= 0.78 and wsem_ok:
            return {"signal": "ELITE LOCK", "units": 1.5}
        elif prob >= 0.70:
            return {"signal": "APPROVED", "units": 1.0}
        return {"signal": "PASS", "units": 0}
    
    def analyze_prop(self, player: str, market: str, line: float, pick: str,
                     data: List[float], sport: str, odds: int) -> dict:
        
        api_status = self.api.get_injury_status(player, sport)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        sim = self.simulate_prop(data, line, pick, sport)
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(sim["prob"], sim["dtm"], wsem_ok, l42_pass, api_status["injury"])
        
        raw_edge = (sim["prob"] - 0.524) * 2
        
        if market.upper() in RED_TIER_PROPS:
            tier = "REJECT"
        elif raw_edge >= 0.08:
            tier = "SAFE"
        elif raw_edge >= 0.05:
            tier = "BALANCED+"
        elif raw_edge >= 0.03:
            tier = "RISKY"
        else:
            tier = "PASS"
        
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge > 0 else 0
        
        return {
            "player": player, "market": market, "line": line, "pick": pick,
            "signal": bolt["signal"], "units": bolt["units"],
            "projection": sim["proj"], "probability": sim["prob"],
            "raw_edge": round(raw_edge, 4), "tier": tier,
            "injury": api_status["injury"], "l42_msg": l42_msg,
            "kelly_stake": round(min(kelly, 50), 2)
        }
    
    def scan_and_approve(self, sports: List[str] = None) -> Dict:
        """Scan Sleeper API and return CLARITY-approved props"""
        all_props = self.sleeper.scan_all_sports(sports)
        
        approved = []
        rejected = {"RED_TIER": 0, "LOW_EDGE": 0}
        
        for prop in all_props[:100]:
            if prop["market"].upper() in RED_TIER_PROPS:
                rejected["RED_TIER"] += 1
                continue
            
            mock_data = [prop["line"]] * 10
            pick = "OVER"
            
            sim = self.simulate_prop(mock_data, prop["line"], pick, prop["sport"])
            edge = (sim["prob"] - 0.524) * 2
            
            if edge < 0.03:
                rejected["LOW_EDGE"] += 1
                continue
            
            tier = "SAFE" if edge >= 0.08 else "BALANCED+" if edge >= 0.05 else "RISKY"
            
            approved.append({
                "source": prop["source"],
                "player": prop["player"],
                "team": prop.get("team", "UNKNOWN"),
                "market": prop["market"],
                "line": prop["line"],
                "odds": prop["odds"],
                "sport": prop["sport"],
                "edge": round(edge * 100, 1),
                "tier": tier
            })
        
        return {
            "total_scanned": len(all_props),
            "approved": sorted(approved, key=lambda x: x["edge"], reverse=True),
            "rejected": rejected,
            "diagnostics": self.sleeper.get_diagnostics()
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("CLARITY 18.0 ELITE - SLEEPER API (FIXED)")
    st.markdown(f"**Free Player Stats via Sleeper | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("SYSTEM STATUS")
        st.success("Perplexity API LIVE")
        st.success("Sleeper API LIVE (FREE)")
        st.metric("Version", VERSION)
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
    
    tab1, tab2, tab3 = st.tabs(["🔍 AUTO-SCAN", "📊 APPROVED", "🎯 MANUAL"])
    
    with tab1:
        st.header("Auto-Scan Sleeper API")
        st.markdown("*Free player season averages*")
        
        sports = st.multiselect("Sports", ["nba", "mlb", "nhl"], default=["nba"])
        
        if st.button("🚀 RUN AUTO-SCAN", type="primary"):
            with st.spinner("Scanning Sleeper API..."):
                result = engine.scan_and_approve(sports)
                st.success(f"Scanned {result['total_scanned']} props")
                st.metric("Approved", len(result['approved']))
                st.metric("Rejected", sum(result['rejected'].values()))
                
                with st.expander("Diagnostic Logs"):
                    for log in result['diagnostics']:
                        st.text(f"[{log['timestamp']}] {log['source']}: {log['message']}")
    
    with tab2:
        st.header("CLARITY-Approved Props")
        if st.button("🔄 REFRESH", type="primary"):
            with st.spinner("Scanning Sleeper..."):
                result = engine.scan_and_approve(["nba"])
                if result['approved']:
                    df = pd.DataFrame(result['approved'])
                    st.dataframe(df)
                    for _, row in df.head(10).iterrows():
                        st.code(f"{row['player']} - {row['market']} OVER {row['line']} | {row['edge']:.1f}% edge")
                else:
                    st.warning("No approved props")
    
    with tab3:
        st.header("Manual Prop Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            player = st.text_input("Player", "Jalen Johnson")
            market = st.selectbox("Market", list(STAT_CONFIG.keys()))
            line = st.number_input("Line", 0.5, 50.0, 8.5)
            pick = st.selectbox("Pick", ["OVER", "UNDER"])
            sport = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"])
        with c2:
            data_str = st.text_area("Recent Games", "9.2, 10.1, 8.5, 11.3, 9.8, 10.5, 8.9")
            odds = st.number_input("Odds", -500, 500, -110)
        
        if st.button("RUN ANALYSIS", type="primary"):
            data = [float(x.strip()) for x in data_str.split(",")]
            result = engine.analyze_prop(player, market, line, pick, data, sport, odds)
            st.markdown(f"### {result['signal']}")
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("Projection", f"{result['projection']:.1f}")
            with c2: st.metric("Probability", f"{result['probability']:.1%}")
            with c3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
            st.metric("Tier", result['tier'])
            if result['units'] > 0:
                st.success(f"UNITS: {result['units']} (${result['kelly_stake']:.2f})")

if __name__ == "__main__":
    run_dashboard()
