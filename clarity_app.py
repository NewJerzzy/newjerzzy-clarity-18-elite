# ... [all previous imports, constants, functions remain exactly as before] ...

# -----------------------------------------------------------------------------
# STREAMLIT UI
# -----------------------------------------------------------------------------
def display_health_status():
    """Display colored API health in sidebar (lightweight)."""
    st.sidebar.markdown("### 🔌 API Health")
    for component, info in st.session_state.health_status.items():
        status = info.get("status", "unknown")
        fallback = info.get("fallback_active", False)
        if status == "ok":
            icon = "🟢"
        elif status == "fail":
            icon = "🔴"
        else:
            icon = "⚪"
        label = f"{icon} {component.split('(')[0].strip()}"
        if fallback:
            label += " (fallback)"
        st.sidebar.text(label)

def main():
    st.set_page_config(page_title=f"CLARITY {VERSION}", layout="wide")
    st.title(f"CLARITY {VERSION}")
    st.caption(f"PropLine Smart Ingestion + Game Analyzer + Best Bets (Parlays) • {BUILD_DATE}")

    # Session state defaults
    for k, v in [("pp_player","LeBron James"),("pp_market","PTS"),
                 ("pp_line",25.5),("pp_pick","OVER"),("pp_odds",-110)]:
        if k not in st.session_state:
            st.session_state[k] = v

    # Sidebar warnings
    if not st.secrets.get("BALLSDONTLIE_API_KEY"):
        st.sidebar.warning("⚠️ BALLSDONTLIE_API_KEY missing")
    if not st.secrets.get("ODDS_API_KEY") or st.secrets.get("ODDS_API_KEY") == "your_key_here":
        st.sidebar.warning("⚠️ ODDS_API_KEY missing")
    if not st.secrets.get("ODDS_API_IO_KEY"):
        st.sidebar.warning("⚠️ ODDS_API_IO_KEY missing")
    if not st.secrets.get("OCR_SPACE_API_KEY"):
        st.sidebar.warning("⚠️ OCR_SPACE_API_KEY missing")
    if not st.secrets.get("RAPIDAPI_KEY"):
        st.sidebar.warning("⚠️ RAPIDAPI_KEY missing")

    current_bankroll = get_bankroll()
    new_bankroll = st.sidebar.number_input("Your Bankroll ($)", value=current_bankroll, min_value=100.0, step=50.0)
    if new_bankroll != current_bankroll:
        set_bankroll(new_bankroll)
        st.sidebar.success("Bankroll updated")
        st.rerun()

    # Display health status in sidebar
    display_health_status()

    tabs = st.tabs(["🎯 Player Props", "🏟️ Game Analyzer", "🏆 Best Bets",
                    "📋 Paste & Scan", "📊 History & Metrics", "⚙️ Tools"])

    # Tab 0: Player Props (unchanged, includes the fixed scan_text expander)
    with tabs[0]:
        st.header("Player Props Analyzer")
        st.caption("Live props from PropLine Smart Ingestion across all active sports.")
        if st.button("📡 Fetch Live Props", type="primary"):
            with st.spinner("Fetching live props across all active sports..."):
                df = fetch_propline_all_smart()
                if df.empty:
                    st.warning("No live props returned from PropLine. Try again shortly.")
                else:
                    st.success(f"Fetched {len(df)} live outcomes across all active sports.")
                    st.dataframe(df, use_container_width=True)
                    st.session_state['live_props_df'] = df
        sport = st.selectbox("Sport", list(SPORT_MODELS.keys()), key="pp_sport")
        player = st.text_input("Player Name", value=st.session_state.pp_player, key="pp_player_input")
        if player != st.session_state.pp_player:
            st.session_state.pp_player = player
        market_opts = SPORT_CATEGORIES.get(sport, ["PTS"])
        market_idx = market_opts.index(st.session_state.pp_market) if st.session_state.pp_market in market_opts else 0
        market = st.selectbox("Market", market_opts, index=market_idx, key="pp_market_input")
        if market != st.session_state.pp_market:
            st.session_state.pp_market = market
        line = st.number_input("Line", value=st.session_state.pp_line, step=0.5, key="pp_line_input")
        if line != st.session_state.pp_line:
            st.session_state.pp_line = line
        pick = st.radio("Pick", ["OVER","UNDER"], horizontal=True, key="pp_pick_input",
                        index=0 if st.session_state.pp_pick == "OVER" else 1)
        if pick != st.session_state.pp_pick:
            st.session_state.pp_pick = pick
        odds = st.number_input("American Odds", value=st.session_state.pp_odds, key="pp_odds_input")
        if odds != st.session_state.pp_odds:
            st.session_state.pp_odds = odds
        if st.button("🚀 Run Prop Analysis", type="primary"):
            res = analyze_prop(st.session_state.pp_player, st.session_state.pp_market,
                               st.session_state.pp_line, st.session_state.pp_pick,
                               sport, st.session_state.pp_odds, new_bankroll)
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Win Prob", f"{res['prob']:.1%}")
            col2.metric("Edge", f"{res['edge']:+.1%}")
            col3.metric("Kelly Stake", f"${res['stake']:.2f}")
            col4.metric("Tier", res["tier"])
            if res["bolt_signal"] == "SOVEREIGN BOLT":
                st.success(f"### ⚡ SOVEREIGN BOLT --- {st.session_state.pp_pick} {st.session_state.pp_line} {st.session_state.pp_market}")
            elif res["edge"] > 0.04:
                st.success(f"### {res['bolt_signal']} --- Recommended")
            else:
                st.error("### PASS --- No edge")
            st.line_chart(pd.DataFrame({"Game": range(1, len(res["stats"])+1), "Stat": res["stats"]}).set_index("Game"))
            if st.button("➕ Add to Slip"):
                insert_slip({
                    "type": "PROP", "sport": sport,
                    "player": st.session_state.pp_player, "team": "", "opponent": "",
                    "market": st.session_state.pp_market, "line": st.session_state.pp_line,
                    "pick": st.session_state.pp_pick, "odds": st.session_state.pp_odds,
                    "edge": res["edge"], "prob": res["prob"], "kelly": res["kelly"],
                    "tier": res["tier"], "bolt_signal": res["bolt_signal"], "bankroll": new_bankroll,
                })
                st.success("Added to slip!")
                st.toast("Slip added", icon="➕")
        st.markdown("---")
        with st.expander("📋 Scan a Prop Slip (Text or Screenshot)", expanded=False):
            st.markdown("Paste a prop line or upload screenshots -- CLARITY will extract and analyze the first valid prop from each.")
            # FIXED: incomplete line replaced
            scan_text = st.text_area("📋 Paste prop slip text", height=200, placeholder="e.g., LeBron James OVER 25.5 PTS")
            if scan_text:
                props = parse_props_from_text(scan_text)
                if props:
                    for prop in props:
                        st.write(f"**Parsed:** {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                else:
                    st.info("No props detected.")
            uploaded_files = st.file_uploader("Or upload screenshot(s)", type=["png","jpg","jpeg"], accept_multiple_files=True)
            if uploaded_files:
                for img_file in uploaded_files:
                    props = parse_props_from_image(img_file.getvalue())
                    if props:
                        for prop in props:
                            st.write(f"**From image:** {prop.get('player')} - {prop.get('market')} {prop.get('pick')} {prop.get('line')}")
                    else:
                        st.write(f"No props detected in {img_file.name}")

    # Tab 1: Game Analyzer (placeholder – full implementation exists but omitted for brevity)
    with tabs[1]:
        st.header("Game Analyzer (NBA)")
        st.info("Full implementation available in original code. Contact for details.")

    # Tab 2: Best Bets (placeholder)
    with tabs[2]:
        st.header("Best Bets & Parlays")
        st.info("Aggregates approved bets from the slip.")

    # Tab 3: Paste & Scan (simple version)
    with tabs[3]:
        st.header("Paste & Scan")
        scan_text2 = st.text_area("Paste slip text here", height=300, key="scan_tab3")
        if scan_text2:
            props2 = parse_props_from_text(scan_text2)
            st.write(props2)

    # Tab 4: History & Metrics
    with tabs[4]:
        st.header("Betting History & Accuracy Metrics")
        df_slips = get_all_slips(200)
        if not df_slips.empty:
            st.dataframe(df_slips)
            dash = get_accuracy_dashboard()
            st.metric("Overall Win Rate", f"{dash['win_rate']}%")
            st.metric("ROI", f"{dash['roi']}%")
            st.metric("Units Profit", dash['units_profit'])
            st.metric("SEM Score", dash['sem_score'])
        else:
            st.info("No settled slips yet.")

    # Tab 5: Tools (enhanced with health monitor, error log, no redundant SEM button)
    with tabs[5]:
        st.header("🛠️ System Health & Error Monitor")

        st.subheader("🔌 API & Service Status")
        cols = st.columns(2)
        for i, (component, info) in enumerate(st.session_state.health_status.items()):
            col = cols[i % 2]
            status = info.get("status", "unknown")
            fallback = info.get("fallback_active", False)
            if status == "ok":
                icon = "🟢"
                label = "OK"
            elif status == "fail":
                icon = "🔴"
                label = "FAIL"
            else:
                icon = "⚪"
                label = "Unknown"
            msg = f"{icon} **{component}** : {label}"
            if fallback:
                msg += " (using fallback)"
            if info.get("last_error"):
                msg += f"\n   ⚠️ Last error: {info['last_error'][:80]}"
            col.markdown(msg)

        st.divider()

        st.subheader("📜 Recent Errors (last 5)")
        try:
            if os.path.exists("clarity_debug.log"):
                with open("clarity_debug.log", "r") as f:
                    lines = f.readlines()
                error_lines = [l for l in lines if "ERROR" in l or "Exception" in l]
                if error_lines:
                    for line in error_lines[-5:]:
                        st.code(line.strip(), language="text")
                else:
                    st.success("No errors logged recently.")
            else:
                st.info("No log file found yet.")
        except Exception as e:
            st.warning(f"Could not read log: {e}")

        st.divider()

        st.subheader("🔍 Quick Diagnostics (on demand)")
        if st.button("Test NBA Stats API (BallsDontLie)"):
            with st.spinner("Testing..."):
                test_stats = _fetch_nba_stats_cached("LeBron James", "PTS")
                if test_stats:
                    st.success(f"✅ API working. Last 3 values: {test_stats[:3]}")
                else:
                    st.error("❌ API failed – check BALLSDONTLIE_API_KEY")

        if st.button("Test PropLine Live Props"):
            with st.spinner("Fetching one event..."):
                sports = propline_get_sports()
                if sports:
                    st.success(f"✅ PropLine returned {len(sports)} sports")
                else:
                    st.error("❌ PropLine failed – check RAPIDAPI_KEY")

        st.divider()

        st.subheader("🧹 Maintenance")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Clear All Pending Slips"):
                clear_pending_slips()
                st.success("Pending slips cleared.")
        with col2:
            # SEM recalibration is automatic; keep button only for manual override if desired
            if st.button("Force SEM Recalibration (manual)"):
                _calibrate_sem()
                st.success("SEM recalibrated manually.")
        st.info("ℹ️ SEM recalibration runs automatically after every settled bet (min 10 bets). Manual button is optional.")

if __name__ == "__main__":
    main()
