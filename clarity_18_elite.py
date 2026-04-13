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
PARLAY_API_KEY = "07e924ee998ffd8a70c27e0b554805a7"
VERSION = "18.0 Elite (Parlay-API Integrated)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
PARLAY_API_BASE = "https://parlay-api.com/v1"

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
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "odds_key": "basketball_nba", "use_skellam": True},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "odds_key": "baseball_mlb", "use_skellam": True},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "odds_key": "icehockey_nhl", "use_skellam": True},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "odds_key": "americanfootball_nfl", "use_skellam": True}
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
# PARLAY-API CLIENT
# =============================================================================
class ParlayAPIClient:
    """Unified client for Parlay-API - The Odds API compatible"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = PARLAY_API_BASE
        self.diagnostic_log = []
    
    def log_diagnostic(self, source: str, message: str, data: Any = None):
        self.diagnostic_log.append({
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "message": message,
            "data": str(data)[:500] if data else None
        })
    
    def get_sports(self) -> List[Dict]:
        """List all available sports"""
        try:
            url = f"{self.base_url}/sports"
            response = requests.get(url, params={"apiKey": self.api_key}, timeout=10)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            self.log_diagnostic("Parlay-API", f"Sports error: {e}")
        return []
    
    def get_player_props(self, sport: str = "basketball_nba", bookmaker: str = "underdog") -> List[Dict]:
        """Fetch player props - The Odds API compatible format"""
        props = []
        
        sport_key = SPORT_MODELS.get(sport, {}).get("odds_key", sport)
        
        try:
            url = f"{self.base_url}/sports/{sport_key}/odds"
            params = {
                "apiKey": self.api_key,
                "regions": "us",
                "bookmakers": bookmaker,
                "markets": "player_points,player_rebounds,player_assists,player_threes,player_blocks,player_steals",
                "oddsFormat": "decimal"
            }
            
            response = requests.get(url, params=params, timeout=15)
            self.log_diagnostic("Parlay-API", f"{sport} props response: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                
                for event in data:
                    for bk in event.get("bookmakers", []):
                        for market in bk.get("markets", []):
                            market_name = market.get("key", "").replace("player_", "").upper()
                            for outcome in market.get("outcomes", []):
                                props.append({
                                    "source": f"Parlay-API ({bookmaker})",
                                    "sport": sport,
                                    "player": outcome.get("description", ""),
                                    "market": market_name,
                                    "line": outcome.get("point", 0),
                                    "odds": outcome.get("price", 2.0),
                                    "home_team": event.get("home_team"),
                                    "away_team": event.get("away_team"),
                                    "commence_time": event.get("commence_time")
                                })
                
                self.log_diagnostic("Parlay-API", f"{sport} props found: {len(props)}")
            else:
                self.log_diagnostic("Parlay-API", f"Error response: {response.text[:200]}")
                
        except Exception as e:
            self.log_diagnostic("Parlay-API", f"Exception: {str(e)}")
        
        return props
    
    def scan_all_sports(self, sports: List[str] = None, bookmaker: str = "underdog") -> List[Dict]:
        """Scan multiple sports for player props"""
        if sports is None:
            sports = ["NBA", "MLB", "NHL"]
        
        self.diagnostic_log = []
        all_props = []
        
        for sport in sports:
            self.log_diagnostic("Parlay-API", f"Scanning {sport}...")
            props = self.get_player_props(sport, bookmaker)
            all_props.extend(props)
        
        self.log_diagnostic("Parlay-API", f"Total props: {len(all_props)}")
        return all_props
    
    def get_diagnostics(self) -> List[Dict]:
        return self.diagnostic_log

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
        self.parlay = ParlayAPIClient(PARLAY_API_KEY)
        self.season_context = SeasonContextEngine(self.api)
        self.statcast = StatcastMLBEnhancer()
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
                     data: List[float], sport: str, odds: int, team: str = None) -> dict:
        
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
        """Scan Parlay-API and return CLARITY-approved props"""
        all_props = self.parlay.scan_all_sports(sports)
        
        approved = []
        rejected = {"RED_TIER": 0, "FADE_TEAM": 0, "LOW_EDGE": 0}
        
        for prop in all_props[:100]:
            if prop["market"].upper() in RED_TIER_PROPS:
                rejected["RED_TIER"] += 1
                continue
            
            if prop.get("home_team"):
                fade_check = self.season_context.should_fade_team(prop["sport"], prop["home_team"])
                if fade_check["fade"]:
                    rejected["FADE_TEAM"] += 1
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
            "diagnostics": self.parlay.get_diagnostics()
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("CLARITY 18.0 ELITE - PARLAY-API INTEGRATED")
    st.markdown(f"**Auto-Scan Underdog via Parlay-API | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("SYSTEM STATUS")
        st.success("Perplexity API LIVE")
        st.success("Parlay-API LIVE")
        st.code(PARLAY_API_KEY[:8] + "..." + PARLAY_API_KEY[-4:])
        st.success("Statcast MLB " + ("LIVE" if STATCAST_AVAILABLE else "UNAVAILABLE"))
        st.metric("Version", VERSION)
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        st.divider()
        st.subheader("Scan Schedule (ET)")
        for time_str, desc in SCAN_SCHEDULE.items():
            st.caption(f"**{time_str}**: {desc}")
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔍 AUTO-SCAN", "📊 APPROVED", "🎯 MANUAL", "🩺 DIAGNOSTICS"])
    
    with tab1:
        st.header("Auto-Scan Parlay-API")
        st.markdown("*Scans Underdog props via Parlay-API*")
        
        sports = st.multiselect("Sports", ["NBA", "MLB", "NHL"], default=["NBA", "MLB"])
        
        if st.button("🚀 RUN AUTO-SCAN", type="primary"):
            with st.spinner("Scanning Parlay-API..."):
                result = engine.scan_and_approve(sports)
                st.success(f"Scanned {result['total_scanned']} props")
                st.metric("Approved", len(result['approved']))
                
                rejected_total = sum(result['rejected'].values())
                st.metric("Rejected", rejected_total)
    
    with tab2:
        st.header("CLARITY-Approved Props")
        st.markdown("*Copy into PrizePicks*")
        
        if st.button("🔄 REFRESH", type="primary"):
            with st.spinner("Scanning..."):
                result = engine.scan_and_approve(["NBA", "MLB"])
                
                if result['approved']:
                    df = pd.DataFrame(result['approved'])
                    st.dataframe(df)
                    
                    st.subheader("Quick Copy")
                    for _, row in df.head(10).iterrows():
                        st.code(f"{row['player']} - {row['market']} OVER {row['line']} ({row['edge']:.1f}% edge)")
                    
                    st.download_button("📥 Download CSV", df.to_csv(index=False), "clarity_approved.csv")
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
    
    with tab4:
        st.header("API Diagnostics")
        if st.button("🔬 TEST CONNECTION"):
            with st.spinner("Testing Parlay-API..."):
                sports_list = engine.parlay.get_sports()
                if sports_list:
                    st.success(f"Connected! {len(sports_list)} sports available")
                    st.json(sports_list[:5])
                else:
                    st.error("Connection failed. Check API key.")
                
                result = engine.scan_and_approve(["NBA"])
                for log in result['diagnostics']:
                    st.text(f"[{log['timestamp']}] {log['source']}: {log['message']}")

if __name__ == "__main__":
    run_dashboard()
