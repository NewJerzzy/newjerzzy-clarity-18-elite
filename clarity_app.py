    # ---------- Tab 1: Game Analyzer (FULL CLARITY MODEL) ----------
    with tabs[1]:
        st.header("Game Analyzer – ML, Spreads, Totals with CLARITY Approval")
        st.caption("Fetches real team stats (NBA) and applies the full weighted moving average, volatility, edge, and tier model.")
        sport2 = st.selectbox("Sport", ["NBA", "NFL", "MLB", "NHL"], index=0, key="game_sport")
        col1, col2 = st.columns([3, 1])
        with col1:
            load_tomorrow = st.checkbox("Load tomorrow's games", value=False, key="load_tomorrow")
        with col2:
            if st.button("📅 Load Games", type="primary"):
                days_offset = 1 if load_tomorrow else 0
                with st.spinner(f"Fetching {'tomorrow' if days_offset else 'today'}'s games..."):
                    games = game_scanner.fetch_games_by_date([sport2], days_offset)
                    if games:
                        st.session_state["auto_games"] = games
                        st.success(f"Loaded {len(games)} games")
                    else:
                        st.warning("No games found.")
        if "auto_games" in st.session_state and st.session_state["auto_games"]:
            for idx, game in enumerate(st.session_state["auto_games"]):
                home = game.get('home_team', '')
                away = game.get('away_team', '')
                if not home or not away:
                    continue
                st.subheader(f"{home} vs {away}")

                # ---------- MONEYLINE ----------
                if game.get('home_ml') and game.get('away_ml'):
                    ml_res = analyze_moneyline_advanced(home, away, sport2, game['home_ml'], game['away_ml'])
                    col_ml1, col_ml2 = st.columns(2)
                    with col_ml1:
                        tier = ml_res['home_tier']
                        bolt = ml_res['home_bolt']
                        if tier != "PASS":
                            st.success(f"**{home} ML ({game['home_ml']})**")
                            st.caption(f"{bolt} | Edge: {ml_res['home_edge']:.1%} | Prob: {ml_res['home_prob']:.1%}")
                        else:
                            st.error(f"**{home} ML ({game['home_ml']})** — PASS")
                    with col_ml2:
                        tier = ml_res['away_tier']
                        bolt = ml_res['away_bolt']
                        if tier != "PASS":
                            st.success(f"**{away} ML ({game['away_ml']})**")
                            st.caption(f"{bolt} | Edge: {ml_res['away_edge']:.1%} | Prob: {ml_res['away_prob']:.1%}")
                        else:
                            st.error(f"**{away} ML ({game['away_ml']})** — PASS")

                # ---------- SPREAD ----------
                if game.get('spread') is not None and game.get('spread_odds'):
                    spread_res = analyze_spread_advanced(home, away, sport2, game['spread'], game['spread_odds'])
                    col_sp1, col_sp2 = st.columns(2)
                    with col_sp1:
                        tier = spread_res['home_tier']
                        bolt = spread_res['home_bolt']
                        if tier != "PASS":
                            st.success(f"**{home} {game['spread']:+.1f} ({game['spread_odds']})**")
                            st.caption(f"{bolt} | Edge: {spread_res['home_edge']:.1%} | Cover Prob: {spread_res['home_cover_prob']:.1%}")
                        else:
                            st.error(f"**{home} {game['spread']:+.1f} ({game['spread_odds']})** — PASS")
                    with col_sp2:
                        tier = spread_res['away_tier']
                        bolt = spread_res['away_bolt']
                        if tier != "PASS":
                            st.success(f"**{away} {game['spread']:+.1f} ({game['spread_odds']})**")
                            st.caption(f"{bolt} | Edge: {spread_res['away_edge']:.1%} | Cover Prob: {spread_res['away_cover_prob']:.1%}")
                        else:
                            st.error(f"**{away} {game['spread']:+.1f} ({game['spread_odds']})** — PASS")

                # ---------- TOTAL ----------
                if game.get('total') is not None and game.get('over_odds') and game.get('under_odds'):
                    total_res = analyze_total_advanced(home, away, sport2, game['total'], game['over_odds'], game['under_odds'])
                    col_tot1, col_tot2 = st.columns(2)
                    with col_tot1:
                        tier = total_res['over_tier']
                        bolt = total_res['over_bolt']
                        if tier != "PASS":
                            st.success(f"**OVER {game['total']} ({game['over_odds']})**")
                            st.caption(f"{bolt} | Edge: {total_res['over_edge']:.1%} | Prob: {total_res['over_prob']:.1%} | Proj: {total_res['projection']:.1f}")
                        else:
                            st.error(f"**OVER {game['total']} ({game['over_odds']})** — PASS")
                    with col_tot2:
                        tier = total_res['under_tier']
                        bolt = total_res['under_bolt']
                        if tier != "PASS":
                            st.success(f"**UNDER {game['total']} ({game['under_odds']})**")
                            st.caption(f"{bolt} | Edge: {total_res['under_edge']:.1%} | Prob: {total_res['under_prob']:.1%} | Proj: {total_res['projection']:.1f}")
                        else:
                            st.error(f"**UNDER {game['total']} ({game['under_odds']})** — PASS")
                st.markdown("---")

        # Keep manual entry as fallback (optional)
        st.markdown("---")
        st.subheader("Manual Entry (fallback)")
        with st.expander("Click to enter a game manually"):
            home_man = st.text_input("Home Team", key="game_home")
            away_man = st.text_input("Away Team", key="game_away")
            market_man = st.selectbox("Market", ["ML", "SPREAD", "TOTAL"], key="game_market")
            if market_man == "ML":
                home_odds = st.number_input("Home Odds", value=-110, key="ml_home")
                away_odds = st.number_input("Away Odds", value=-110, key="ml_away")
                if st.button("Analyze ML (Manual)"):
                    res = analyze_moneyline_advanced(home_man, away_man, sport2, home_odds, away_odds)
                    st.markdown(f"{home_man}: {'✅ '+res['home_tier'] if res['home_tier']!='PASS' else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                    st.markdown(f"{away_man}: {'✅ '+res['away_tier'] if res['away_tier']!='PASS' else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
            elif market_man == "SPREAD":
                spread = st.number_input("Spread (home margin)", value=-5.5, key="spread_line")
                odds_sp = st.number_input("Odds", value=-110, key="spread_odds")
                if st.button("Analyze Spread (Manual)"):
                    res = analyze_spread_advanced(home_man, away_man, sport2, spread, odds_sp)
                    st.markdown(f"{home_man} {spread:+.1f}: {'✅ '+res['home_tier'] if res['home_tier']!='PASS' else '❌ PASS'} (Edge: {res['home_edge']:.1%})")
                    st.markdown(f"{away_man} {spread:+.1f}: {'✅ '+res['away_tier'] if res['away_tier']!='PASS' else '❌ PASS'} (Edge: {res['away_edge']:.1%})")
            else:
                total = st.number_input("Total Line", value=220.5, key="total_line")
                over_odds = st.number_input("Over Odds", value=-110, key="over_odds")
                under_odds = st.number_input("Under Odds", value=-110, key="under_odds")
                if st.button("Analyze Total (Manual)"):
                    res = analyze_total_advanced(home_man, away_man, sport2, total, over_odds, under_odds)
                    st.markdown(f"OVER {total}: {'✅ '+res['over_tier'] if res['over_tier']!='PASS' else '❌ PASS'} (Edge: {res['over_edge']:.1%})")
                    st.markdown(f"UNDER {total}: {'✅ '+res['under_tier'] if res['under_tier']!='PASS' else '❌ PASS'} (Edge: {res['under_edge']:.1%})")
