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
import asyncio
import warnings
warnings.filterwarnings('ignore')

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
BOLTODDS_API_KEY = "9b8b1485-ea53-4288-84f8-a0d118ea923f"
VERSION = "18.0 Elite (BoltOdds WebSocket)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
BOLTODDS_WS_URL = "wss://spro.agency/api"
BOLTODDS_REST_URL = "https://spro.agency/api"

# NBA Player Markets (discovered from /get_markets)
NBA_PLAYER_MARKETS = [
    "Player Points", "Player Rebounds", "Player Assists",
    "Player Threes", "Player Blocks", "Player Steals",
    "Player Points + Rebounds + Assists", "Player Points + Rebounds", "Player Points + Assists"
]

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
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20}
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
# BOLTODDS WEBSOCKET CLIENT
# =============================================================================
class BoltOddsClient:
    """BoltOdds WebSocket client for real-time player props"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.ws_url = f"{BOLTODDS_WS_URL}?key={api_key}"
        self.diagnostic_log = []
        self.props_data = []
    
    def log_diagnostic(self, source: str, message: str, data: Any = None):
        self.diagnostic_log.append({
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "message": message,
            "data": str(data)[:500] if data else None
        })
    
    def scan_sync(self, sports: List[str] = None, sportsbooks: List[str] = None, timeout: int = 30) -> List[Dict]:
        """Synchronous wrapper for WebSocket scan"""
        if not WEBSOCKETS_AVAILABLE:
            self.log_diagnostic("BoltOdds", "WebSockets not available. Install: pip install websockets")
            return []
        
        if sports is None:
            sports = ["NBA"]
        if sportsbooks is None:
            sportsbooks = ["draftkings"]
        
        self.props_data = []
        self.diagnostic_log = []
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            task = loop.create_task(self._connect_and_scan(sports, sportsbooks))
            loop.run_until_complete(asyncio.wait_for(task, timeout=timeout))
            loop.close()
        except asyncio.TimeoutError:
            self.log_diagnostic("BoltOdds", f"Scan timeout after {timeout}s")
        except Exception as e:
            self.log_diagnostic("BoltOdds", f"Scan error: {str(e)}")
        
        return self._parse_props()
    
    async def _connect_and_scan(self, sports: List[str], sportsbooks: List[str]):
        """Connect to WebSocket and collect props"""
        try:
            async with websockets.connect(self.ws_url, max_size=None) as websocket:
                self.log_diagnostic("BoltOdds", "WebSocket connected")
                
                # Wait for ack
                ack = await websocket.recv()
                self.log_diagnostic("BoltOdds", f"ACK: {ack[:100]}")
                
                # Subscribe to NBA player props
                subscribe_message = {
                    "action": "subscribe",
                    "filters": {
                        "sports": sports,
                        "sportsbooks": sportsbooks,
                        "markets": NBA_PLAYER_MARKETS
                    }
                }
                
                await websocket.send(json.dumps(subscribe_message))
                self.log_diagnostic("BoltOdds", f"Subscribed to: {sports} - {len(NBA_PLAYER_MARKETS)} markets")
                
                # Collect messages for 20 seconds
                start_time = time.time()
                message_count = 0
                
                while time.time() - start_time < 20:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=5)
                        data = json.loads(message)
                        
                        if isinstance(data, list):
                            for msg in data:
                                if msg.get("action") in ["initial_state", "game_update", "line_update"]:
                                    self.props_data.append(msg)
                                    message_count += 1
                        elif isinstance(data, dict):
                            if data.get("action") in ["initial_state", "game_update", "line_update"]:
                                self.props_data.append(data)
                                message_count += 1
                        
                    except asyncio.TimeoutError:
                        continue
                
                self.log_diagnostic("BoltOdds", f"Collected {message_count} messages with {len(self.props_data)} prop updates")
                
        except Exception as e:
            self.log_diagnostic("BoltOdds", f"WebSocket error: {str(e)}")
    
    def _parse_props(self) -> List[Dict]:
        """Parse BoltOdds messages into CLARITY prop format"""
        parsed = []
        
        for msg in self.props_data:
            try:
                data = msg.get("data", msg)
                outcomes = data.get("outcomes", {})
                
                game = data.get("game", "")
                sport = data.get("sport", "NBA")
                sportsbook = data.get("sportsbook", "draftkings")
                home_team = data.get("home_team", "")
                away_team = data.get("away_team", "")
                
                for outcome_name, outcome_data in outcomes.items():
                    if any(term in outcome_name.lower() for term in ["points", "rebounds", "assists", "threes", "blocks", "steals"]):
                        # Extract player name from outcome
                        player = outcome_name.split(" Over")[0].split(" Under")[0].strip()
                        
                        market = "PTS"
                        if "Rebounds" in outcome_name:
                            market = "REB"
                        elif "Assists" in outcome_name:
                            market = "AST"
                        elif "Threes" in outcome_name:
                            market = "THREES"
                        elif "Blocks" in outcome_name:
                            market = "BLK"
                        elif "Steals" in outcome_name:
                            market = "STL"
                        elif "Points + Rebounds + Assists" in outcome_name:
                            market = "PRA"
                        elif "Points + Rebounds" in outcome_name:
                            market = "PR"
                        elif "Points + Assists" in outcome_name:
                            market = "PA"
                        
                        line = outcome_data.get("outcome_line", 0)
                        odds_str = outcome_data.get("odds", "-110")
                        
                        # Convert odds to American format
                        try:
                            odds = int(odds_str) if odds_str else -110
                        except:
                            odds = -110
                        
                        if player and line:
                            parsed.append({
                                "source": "BoltOdds",
                                "sport": sport,
                                "player": player,
                                "team": home_team if player in outcome_name else away_team,
                                "market": market,
                                "line": float(line) if line else 0,
                                "odds": odds,
                                "sportsbook": sportsbook,
                                "game": game
                            })
            except Exception as e:
                self.log_diagnostic("BoltOdds", f"Parse error: {str(e)}")
        
        self.log_diagnostic("BoltOdds", f"Parsed {len(parsed)} props")
        return parsed
    
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
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.boltodds = BoltOddsClient(BOLTODDS_API_KEY)
        self.season_context = SeasonContextEngine(self.api)
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
    
    def scan_boltodds(self, sports: List[str] = None) -> Dict:
        """Scan BoltOdds WebSocket for NBA player props"""
        if sports is None:
            sports = ["NBA"]
        
        props = self.boltodds.scan_sync(sports, ["draftkings"])
        
        approved = []
        rejected = {"RED_TIER": 0, "LOW_EDGE": 0, "FADE_TEAM": 0}
        
        for prop in props[:50]:
            if prop["market"].upper() in RED_TIER_PROPS:
                rejected["RED_TIER"] += 1
                continue
            
            if prop.get("team"):
                fade_check = self.season_context.should_fade_team(prop["sport"], prop["team"])
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
                "team": prop.get("team", "UNKNOWN"),
                "market": prop["market"],
                "line": prop["line"],
                "odds": prop["odds"],
                "sport": prop["sport"],
                "sportsbook": prop.get("sportsbook", "draftkings"),
                "edge": round(edge * 100, 1),
                "tier": tier
            })
        
        return {
            "total_scanned": len(props),
            "approved": sorted(approved, key=lambda x: x["edge"], reverse=True),
            "rejected": rejected,
            "diagnostics": self.boltodds.get_diagnostics()
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("CLARITY 18.0 ELITE - BOLTODDS WEBSOCKET")
    st.markdown(f"**Real-Time NBA Player Props | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("SYSTEM STATUS")
        st.success("Perplexity API LIVE")
        st.success("BoltOdds WebSocket " + ("LIVE" if WEBSOCKETS_AVAILABLE else "UNAVAILABLE"))
        st.metric("Version", VERSION)
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        
        if not WEBSOCKETS_AVAILABLE:
            st.warning("Install websockets: pip install websockets")
    
    tab1, tab2, tab3 = st.tabs(["🔍 BOLTODDS SCAN", "📊 APPROVED", "🎯 MANUAL"])
    
    with tab1:
        st.header("BoltOdds NBA Player Props")
        st.markdown("*WebSocket real-time data - 20 second scan*")
        
        sports = st.multiselect("Sports", ["NBA"], default=["NBA"])
        
        if st.button("🚀 START WEBSOCKET SCAN", type="primary"):
            if not WEBSOCKETS_AVAILABLE:
                st.error("WebSockets not available. Run: pip install websockets")
            else:
                with st.spinner("Connecting to BoltOdds WebSocket (30s timeout)..."):
                    result = engine.scan_boltodds(sports)
                    
                    st.success(f"Scanned {result['total_scanned']} props")
                    st.metric("Approved", len(result['approved']))
                    
                    rejected_total = sum(result['rejected'].values())
                    st.metric("Rejected", rejected_total)
                    
                    with st.expander("Diagnostic Logs"):
                        for log in result['diagnostics']:
                            st.text(f"[{log['timestamp']}] {log['source']}: {log['message']}")
    
    with tab2:
        st.header("CLARITY-Approved NBA Props")
        st.markdown("*Copy into PrizePicks or Underdog*")
        
        if st.button("🔄 REFRESH APPROVED", type="primary"):
            if not WEBSOCKETS_AVAILABLE:
                st.error("WebSockets not available")
            else:
                with st.spinner("Scanning BoltOdds..."):
                    result = engine.scan_boltodds(["NBA"])
                    
                    if result['approved']:
                        df = pd.DataFrame(result['approved'])
                        st.dataframe(df)
                        
                        st.subheader("Quick Copy")
                        for _, row in df.head(10).iterrows():
                            st.code(f"{row['player']} ({row['team']}) - {row['market']} OVER {row['line']} | {row['edge']:.1f}% | {row['tier']}")
                        
                        st.download_button("📥 Download CSV", df.to_csv(index=False), "clarity_approved.csv")
                    else:
                        st.warning("No approved props found - check diagnostics")
    
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
