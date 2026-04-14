"""
CLARITY 18.1 ELITE - COMPLETE SYSTEM (FULL ROSTERS) - PATCHED VERSION
Player Props | Moneylines | Spreads | Totals | Alternate Lines
NBA | MLB | NHL | NFL - ALL TEAMS HAVE REAL PLAYERS
API KEYS: Perplexity + API-Sports + The Odds API
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
from openai import OpenAI
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import time
import requests
from collections import defaultdict
import warnings
import json
import re

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURATION - API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
VERSION = "18.1 Elite (Patched - Live Data)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS & SETTINGS
# =============================================================================
SPORT_MODELS = {
    "NBA": {
        "distribution": "nbinom",
        "variance_factor": 1.15,
        "avg_total": 228.5,
        "home_advantage": 3.0,
        "max_total": 300.0,
        "spread_std": 12.0,
    },
    "MLB": {
        "distribution": "poisson",
        "variance_factor": 1.08,
        "avg_total": 8.5,
        "home_advantage": 0.12,
        "max_total": 20.0,
        "spread_std": 4.5,
    },
    "NHL": {
        "distribution": "poisson",
        "variance_factor": 1.12,
        "avg_total": 6.0,
        "home_advantage": 0.15,
        "max_total": 10.0,
        "spread_std": 2.8,
    },
    "NFL": {
        "distribution": "nbinom",
        "variance_factor": 1.20,
        "avg_total": 44.5,
        "home_advantage": 2.8,
        "max_total": 80.0,
        "spread_std": 14.0,
    },
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES & API-SPORTS MAPPINGS
# =============================================================================
SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"],
}

API_SPORT_KEYS = {
    "NBA": "basketball",
    "MLB": "baseball",
    "NHL": "hockey",
    "NFL": "american-football",
}
API_LEAGUE_IDS = {"NBA": 12, "MLB": 1, "NHL": 57, "NFL": 1}

STAT_MAPPING = {
    "NBA": {"PTS": "points", "REB": "totReb", "AST": "assists", "STL": "steals", "BLK": "blocks", "THREES": "tpm"},
    "MLB": {"HITS": "hits", "HR": "homeRuns", "RBI": "rbi", "TB": "totalBases", "KS": "strikeOuts"},
    "NHL": {"SOG": "shots", "GOALS": "goals", "ASSISTS": "assists", "HITS": "hits"},
    "NFL": {
        "PASS_YDS": "passingYards",
        "PASS_TD": "passingTDs",
        "RUSH_YDS": "rushingYards",
        "RUSH_TD": "rushingTDs",
        "REC_YDS": "receivingYards",
        "REC": "receptions",
    },
}

STAT_CONFIG = {
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "STL": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "BLK": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "THREES": {"tier": "MED", "buffer": 0.5, "reject": False},
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "OUTS": {"tier": "LOW", "buffer": 0.0, "reject": False},
    "KS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "HITS": {"tier": "MED", "buffer": 0.5, "reject": False},
    "TB": {"tier": "MED", "buffer": 1.0, "reject": False},
    "HR": {"tier": "HIGH", "buffer": 0.5, "reject": False},
    "SOG": {"tier": "LOW", "buffer": 0.5, "reject": False},
    "SAVES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "H+R+RBI": {"tier": "HIGH", "buffer": 0.5, "reject": True},
    "HITTER_FS": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PITCHER_FS": {"tier": "HIGH", "buffer": 5.0, "reject": True},
}
RED_TIER_PROPS = ["PRA", "PR", "PA", "H+R+RBI", "HITTER_FS", "PITCHER_FS"]

# =============================================================================
# HARDCODED TEAMS & ROSTERS
# =============================================================================
# Paste your existing blocks from GitHub here, unchanged:
#
# HARDCODED_TEAMS = { ... }
# NBA_ROSTERS = { ... }
# MLB_ROSTERS = { ... }
# NHL_ROSTERS = { ... }
# NFL_ROSTERS = { ... }

# =============================================================================
# ODDS API CLIENT
# =============================================================================
class OddsAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.rate_limit_reset = 0

    def _rate_limit_wait(self):
        now = time.time()
        if now < self.rate_limit_reset:
            time.sleep(self.rate_limit_reset - now)

    def get_odds(
        self,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
    ) -> Optional[List[Dict[str, Any]]]:
        self._rate_limit_wait()
        try:
            params = {
                "apiKey": self.api_key,
                "sport": sport_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": "american",
            }
            resp = requests.get(
                f"{self.base_url}/sports/{sport_key}/odds",
                params=params,
                timeout=20,
            )
            if resp.status_code == 429:
                reset = resp.headers.get("x-requests-resets")
                if reset:
                    self.rate_limit_reset = float(reset)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def extract_game_odds(self, odds_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        games = []
        if not odds_data:
            return games

        for game in odds_data:
            try:
                home_team = game.get("home_team")
                away_team = game.get("away_team")
                commence_time = game.get("commence_time")
                markets = game.get("bookmakers", [])

                best_spread = None
                best_total = None
                best_ml_home = None
                best_ml_away = None

                for book in markets:
                    for market in book.get("markets", []):
                        if market["key"] == "spreads":
                            for out in market.get("outcomes", []):
                                if out["name"] == home_team:
                                    best_spread = out
                        elif market["key"] == "totals":
                            for out in market.get("outcomes", []):
                                best_total = out
                        elif market["key"] == "h2h":
                            for out in market.get("outcomes", []):
                                if out["name"] == home_team:
                                    best_ml_home = out
                                elif out["name"] == away_team:
                                    best_ml_away = out

                games.append(
                    {
                        "home_team": home_team,
                        "away_team": away_team,
                        "commence_time": commence_time,
                        "spread": best_spread,
                        "total": best_total,
                        "ml_home": best_ml_home,
                        "ml_away": best_ml_away,
                    }
                )
            except Exception:
                continue

        return games


# =============================================================================
# STATS API CLIENT (API-SPORTS) – WITH UNREACHABLE HANDLING
# =============================================================================
class StatsAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = API_SPORTS_BASE
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.unreachable = False  # flag so we don't keep hammering if it's down

    def _check_unreachable(self, resp: Optional[requests.Response]) -> None:
        """
        Mark API as unreachable if we get network errors or 5xx/401/403.
        """
        if resp is None:
            self.unreachable = True
            return
        if resp.status_code >= 500 or resp.status_code in (401, 403):
            self.unreachable = True
