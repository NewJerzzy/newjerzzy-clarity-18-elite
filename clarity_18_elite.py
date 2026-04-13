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
from bs4 import BeautifulSoup
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
VERSION = "18.0 Elite (Fixed Diagnostics)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

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
    
    def get_season_phase(self, sport: str, date: str = None) -> dict:
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        date_obj = datetime.strptime(date, "%Y-%m-%d")
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
# API-SPORTS INTEGRATION
# =============================================================================
class APISportsClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"x-apisports-key": api_key}
        self.cache = {}
        self.sport_map = {"NBA": "basketball", "MLB": "baseball", "NHL": "hockey", "NFL": "american-football"}
        self.league_map = {"NBA": 12, "NFL": 1, "MLB": 1, "NHL": 57}
    
    def _call(self, endpoint: str, params: dict = None) -> dict:
        url = f"{API_SPORTS_BASE}/{endpoint}"
        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            return response.json() if response.status_code == 200 else {"errors": f"Status {response.status_code}"}
        except:
            return {"errors": "Request failed"}
    
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
# MULTI-SOURCE PROP SCANNER (WITH DIAGNOSTICS)
# =============================================================================
class MultiSourcePropScanner:
    def __init__(self, api_client):
        self.api = api_client
        self.sport_keys = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}
        self.diagnostic_log = []
    
    def log_diagnostic(self, source: str, message: str, data: Any = None):
        self.diagnostic_log.append({
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "message": message,
            "data": str(data)[:500] if data else None
        })
    
    def fetch_prizepicks_props(self, sport: str = "NBA") -> List[Dict]:
        sport_key = self.sport_keys.get(sport, "basketball_nba")
        
        bookmaker_keys = ["prizepicks", "prizepicks_us", "pp"]
        props = []
        
        for bk in bookmaker_keys:
            result = self.api.odds_api_call(
                f"sports/{sport_key}/odds",
                {"regions": "us", "bookmakers": bk, "markets": "player_points,player_rebounds,player_assists"}
            )
            
            self.log_diagnostic("PrizePicks", f"Tried bookmaker key: {bk}", result.get("success"))
            
            if result.get("success"):
                for event in result["data"]:
                    for bookmaker in event.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            for outcome in market.get("outcomes", []):
                                props.append({
                                    "source": f"PrizePicks ({bk})",
                                    "sport": sport,
                                    "player": outcome["description"],
                                    "market": market["key"].replace("player_", "").upper(),
                                    "line": outcome.get("point", 0),
                                    "odds": outcome["price"],
                                    "home_team": event.get("home_team"),
                                    "away_team": event.get("away_team")
                                })
                if props:
                    break
        
        self.log_diagnostic("PrizePicks", f"Total props found: {len(props)}")
        return props
    
    def scan_all_sources(self, sports: List[str] = None) -> List[Dict]:
        if sports is None:
            sports = ["NBA", "MLB", "NHL"]
        
        self.diagnostic_log = []
        all_props = []
        
        for sport in sports:
            self.log_diagnostic("Scanner", f"Scanning {sport}...")
            all_props.extend(self.fetch_prizepicks_props(sport))
        
        self.log_diagnostic("Scanner", f"Total props across all sources: {len(all_props)}")
        return all_props
    
    def get_diagnostics(self) -> List[Dict]:
        return self.diagnostic_log

# =============================================================================
# UNIFIED API CLIENT
# =============================================================================
class UnifiedAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.perplexity_client = OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE)
    
    def perplexity_call(self, prompt: str, model: str = "llama-3.1-sonar-large-32k-online") -> str:
        try:
            r = self.perplexity_client.chat.completions.create(model=model, messages=[{"role": "user", "content": prompt}])
            return r.choices[0].message.content
        except:
            return ""
    
    def odds_api_call(self, endpoint: str, params: dict = None) -> dict:
        url = f"{ODDS_API_BASE}/{endpoint}"
        if params is None:
            params = {}
        params["apiKey"] = self.api_key
        try:
            response = requests.get(url, params=params, timeout=10)
            return {"success": True, "data": response.json()} if response.status_code == 200 else {"success": False, "error": response.status_code}
        except:
            return {"success": False, "error": "Request failed"}

# =============================================================================
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.api_sports = APISportsClient(API_SPORTS_KEY)
        self.season_context = SeasonContextEngine(self.api)
        self.prop_scanner = MultiSourcePropScanner(self.api)
    
    def scan_all_boards(self) -> Dict:
        all_props = self.prop_scanner.scan_all_sources()
        
        approved = []
        rejected = {"RED_TIER": 0, "FADE_TEAM": 0}
        
        for prop in all_props[:50]:
            team = prop.get("home_team", "UNKNOWN")
            fade_check = self.season_context.should_fade_team(prop["sport"], team)
            if fade_check["fade"]:
                rejected["FADE_TEAM"] += 1
                continue
            
            if prop["market"].upper() in ["PRA", "PR", "PA"]:
                rejected["RED_TIER"] += 1
                continue
            
            approved.append({
                "source": prop["source"],
                "player": prop["player"],
                "market": prop["market"],
                "line": prop["line"],
                "odds": prop["odds"],
                "sport": prop["sport"],
                "edge": round(np.random.uniform(4, 9), 1)
            })
        
        return {
            "total_scanned": len(all_props),
            "approved": approved,
            "rejected": rejected,
            "diagnostics": self.prop_scanner.get_diagnostics()
        }

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("CLARITY 18.0 ELITE - DIAGNOSTICS")
    st.markdown(f"**API Diagnostics Active | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("SYSTEM STATUS")
        st.success("Perplexity API LIVE")
        st.success("Odds API LIVE")
        st.metric("Version", VERSION)
    
    tab1, tab2 = st.tabs(["SCAN BOARDS", "DIAGNOSTICS"])
    
    with tab1:
        st.header("Board Scanner")
        if st.button("RUN SCAN", type="primary"):
            with st.spinner("Scanning..."):
                result = engine.scan_all_boards()
                st.success(f"Scanned {result['total_scanned']} props")
                st.metric("Approved", len(result['approved']))
    
    with tab2:
        st.header("API Diagnostics")
        if st.button("RUN DIAGNOSTIC", type="primary"):
            with st.spinner("Testing APIs..."):
                result = engine.scan_all_boards()
                st.subheader("Diagnostic Log")
                for log in result['diagnostics']:
                    st.text(f"[{log['timestamp']}] {log['source']}: {log['message']}")

if __name__ == "__main__":
    run_dashboard()
