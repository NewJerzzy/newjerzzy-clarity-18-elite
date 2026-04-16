"""
CLARITY 18.0 ELITE – LEAN EDITION
- Game Markets (tomorrow support)
- PrizePicks Scanner (live API + paste/screenshot)
- Scanners & Accuracy
- Player Props (real rosters)
- Image Analysis & Auto-Tune
"""
import numpy as np, pandas as pd
from scipy.stats import poisson, norm, nbinom
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
import sqlite3, re, time, requests, hashlib, threading, warnings, pickle, os
try:
    import lightgbm as lgb
    LGB_AVAILABLE = True
except ImportError:
    LGB_AVAILABLE = False
warnings.filterwarnings('ignore')

# ---- CONFIG ----
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
VERSION = "18.0 Lean"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"
ODDS_API_IO_BASE = "https://api.odds-api.io/v4"

# ---- SPORT MODELS ----
SPORT_MODELS = {
    "NBA": {"dist":"nbinom","vf":1.15,"avg":228.5,"ha":3.0},
    "MLB": {"dist":"poisson","vf":1.08,"avg":8.5,"ha":0.12},
    "NHL": {"dist":"poisson","vf":1.12,"avg":6.0,"ha":0.15},
    "NFL": {"dist":"nbinom","vf":1.20,"avg":44.5,"ha":2.8},
    "PGA": {"dist":"nbinom","vf":1.10,"avg":70.5,"ha":0},
    "TENNIS": {"dist":"poisson","vf":1.05,"avg":22.0,"ha":0},
    "UFC": {"dist":"poisson","vf":1.20,"avg":2.5,"ha":0},
    "SOCCER_EPL": {"dist":"poisson","vf":1.10,"avg":2.5,"ha":0.3},
    "SOCCER_LALIGA": {"dist":"poisson","vf":1.10,"avg":2.5,"ha":0.3},
    "COLLEGE_BASKETBALL": {"dist":"nbinom","vf":1.15,"avg":145.5,"ha":3.5},
    "COLLEGE_FOOTBALL": {"dist":"nbinom
