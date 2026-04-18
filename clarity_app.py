"""
CLARITY 18.0 ELITE – ULTRA-SAFE BUILD
-------------------------------------

- One paste board for player props.
- Simple PrizePicks-style parsing.
- Optional API calls (wrapped in try/except so they NEVER crash the app).
- No database, no ML retrain, no background jobs.
- Designed to "just work" on Streamlit Cloud with minimal moving parts.
"""

import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any

import numpy as np
import pandas as pd
import requests
import streamlit as st
from scipy.stats import norm

# =============================================================================
# CONFIG – YOUR API KEYS (kept hard-coded as requested)
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
OCR_SPACE_API_KEY = "K89641020988957"
ODDS_API_IO_KEY = "17d53b439b1e8dd6dfa35744326b3797408246c1fd2f9f2f252a48a1df690630"
BALLSDONTLIE_API_KEY = "9d7c9ea5-54ea-4084-b0d0-2541ac7c360d"

VERSION = "18.0 Elite – Ultra-Safe"
BUILD_DATE = "2026-04-17"

BALLSDONTLIE_BASE = "https://api.balldontlie.io/v1"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# BASIC SPORT CONFIG
# =============================================================================
SPORT_MODELS: Dict[str, Dict[str, Any]] = {
    "NBA": {"avg_total": 228.5},
    "NFL": {"avg_total": 44.5},
    "MLB": {"avg_total": 8.5},
    "NHL": {"avg_total": 6.0},
}

STAT_CONFIG: Dict[str, Dict[str, Any]] = {
    "PTS": {"tier": "MED", "buffer": 1.5, "reject": False},
    "REB": {"tier": "LOW", "buffer": 1.0, "reject": False},
    "AST": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "PRA": {"tier": "HIGH", "buffer": 3.0, "reject": True},
    "PR": {"tier": "HIGH", "buffer": 2.0, "reject": True},
    "PA": {"tier": "HIGH", "buffer": 2.0, "reject": True},
}

RED_TIER_PROPS = ["PRA", "PR", "PA"]

# =============================================================================
# TIMING WARNING (READ-ONLY, NO CRASH RISK)
# =============================================================================
def check_scan_timing(sport: str) -> None:
    now = datetime.now()
    hour = now.hour
    weekday = now.weekday()

    if sport in ["NBA", "MLB", "NHL"]:
        if hour not in [6, 14, 21]:
            st.warning(
                "⏰ Optimal scanning times for NBA/MLB/NHL are 6 AM, 2 PM, and 9 PM. "
                "Current time may yield less stable lines."
            )
    elif sport == "NFL":
        if not (
            (weekday == 0 and 9 <= hour <= 11)
            or (weekday == 1 and 5 <= hour <= 7)
            or (weekday == 6 and 9 <= hour <= 11)
        ):
            st.warning(
                "🏈 NFL lines are best scanned Monday 10 AM, Tuesday 6 AM, or Sunday 10 AM. "
                "Current time may not capture optimal value."
            )

# =============================================================================
# PASTEBOARD PARSER (PrizePicks-style)
# Example line: "LeBron James PTS 27.5 OVER"
# =============================================================================
PROP_PATTERN = re.compile(
    r"(?P<player>[A-Za-z .'-]+)\s+(?P<market>[A-Z+]+)\s+(?P<line>\d+\.?\d*)\s+(?P<pick>OVER|UNDER)",
    re.IGNORECASE,
)

def parse_pasteboard(text: str, default_sport: str) -> List[Dict[str, Any]]:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    results: List[Dict[str, Any]] = []
    for line in lines:
        m = PROP_PATTERN.search(line)
        if not m:
            continue
        d = m.groupdict()
        results.append(
            {
                "sport": default_sport,
                "player": d["player"].strip(),
                "market": d["market"].upper(),
                "line": float(d["line"]),
                "pick": d["pick"].upper(),
            }
        )
    return results

# =============================================================================
# SAFE NBA STATS FETCH (Balldontlie) – NEVER CRASHES
# =============================================================================
def fetch_nba_last_games(player_name: str, num_games: int = 8) -> List[float]:
    """
    Returns a list of recent points for the player.
    If anything fails, returns [] instead of crashing.
    """
    try:
        headers = {"Authorization": BALLSDONTLIE_API_KEY}
        # Find player
        r = requests.get(
            f"{BALLSDONTLIE_BASE}/players",
            headers=headers,
            params={"search": player_name},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        if not data:
            return []
        player_id = data[0]["id"]

        # Get stats
        r2 = requests.get(
            f"{BALLSDONTLIE_BASE}/stats",
            headers=headers,
            params={"player_ids[]": player_id, "per_page": num_games},
            timeout=10,
        )
        if r2.status_code != 200:
            return []
        stats_data = r2.json().get("data", [])
        vals: List[float] = []
        for g in stats_data:
            pts = g.get("pts", 0)
            vals.append(float(pts) if pts is not None else 0.0)
        return vals
    except Exception:
        return []

# =============================================================================
# SIMPLE EDGE ESTIMATION
# =============================================================================
def estimate_edge_from_history(
    values: List[float],
    line: float,
    pick: str,
) -> Tuple[float, float]:
    """
    Returns (edge, win_prob).
    If no values, returns (0.0, 0.5).
    """
    if not values:
        return 0.0, 0.5

    mu = float(np.mean(values))
    sigma = float(np.std(values) + 1e-6)

    if pick.upper() == "OVER":
        prob = 1.0 - norm.cdf(line, loc=mu, scale=sigma)
    else:
        prob = norm.cdf(line, loc=mu, scale=sigma)

    edge = prob - 0.5
    return edge, prob

# =============================================================================
# STREAMLIT APP
# =============================================================================
def main():
    st.set_page_config(
        page_title="Clarity 18.0 Elite – Ultra-Safe",
        layout="wide",
    )

    st.title("CLARITY 18.0 ELITE – Unified Quick Scanner (Ultra-Safe Build)")
    st.caption(f"Version: {VERSION} | Build: {BUILD_DATE}")
    st.info(
        "This build is simplified to avoid crashes: no database, no auto-ML, "
        "all external calls are wrapped so they cannot break the app."
    )

    # Sidebar
    with st.sidebar:
        st.subheader("Scan Settings")
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), index=0)
        check_scan_timing(sport)
        use_live_nba = st.checkbox(
            "Use live NBA stats (Balldontlie) when sport = NBA",
            value=True,
        )

    # Paste area
    st.markdown("### Paste Board")
    paste_text = st.text_area(
        "Paste PrizePicks / slips / tickets here:",
        height=220,
        placeholder="Example:\nLeBron James PTS 27.5 OVER\nNikola Jokic PRA 47.5 UNDER",
    )

    run_scan = st.button("Scan & Analyze")

    if run_scan:
        if not paste_text.strip():
            st.warning("Paste something first.")
            return

        parsed = parse_pasteboard(paste_text, sport)
        if not parsed:
            st.warning("No valid props detected. Check formatting.")
            return

        rows = []
        for p in parsed:
            player = p["player"]
            market = p["market"]
            line = p["line"]
            pick = p["pick"]

            # Default: no history
            history: List[float] = []

            # Only try live stats for NBA PTS (to keep it safe and simple)
            if sport == "NBA" and market == "PTS" and use_live_nba:
                history = fetch_nba_last_games(player, num_games=8)

            edge, prob = estimate_edge_from_history(history, line, pick)
            tier_info = STAT_CONFIG.get(market, {"tier": "LOW", "buffer": 0.0, "reject": False})

            rows.append(
                {
                    "Player": player,
                    "Market": market,
                    "Line": line,
                    "Pick": pick,
                    "Sport": sport,
                    "Games Used": len(history),
                    "Mean Stat": round(np.mean(history), 2) if history else 0.0,
                    "Edge %": round(edge * 100, 1),
                    "Win Prob %": round(prob * 100, 1),
                    "Tier": tier_info["tier"],
                    "Red Tier": tier_info["reject"],
                }
            )

        df = pd.DataFrame(rows)
        st.markdown("### Scan Results")
        st.dataframe(df, use_container_width=True)

        # Simple highlight
        st.markdown("#### Notes")
        st.write(
            "- **Edge %** is based on recent games (when available). "
            "If `Games Used` is 0, it's just a neutral 50/50 baseline."
        )
        st.write(
            "- **Red Tier = True** means this market is considered higher volatility / lower trust "
            "in your original config."
        )

    st.markdown("---")
    st.caption(
        "Ultra-safe build: no database, no background ML, all external APIs wrapped to avoid crashes. "
        "Keys remain hard-coded as requested."
    )

if __name__ == "__main__":
    main()
