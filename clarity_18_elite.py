# =============================================================================
# STREAMLIT DASHBOARD - UPDATED WITH 5 TABS
# =============================================================================
engine = Clarity18Elite()

def export_database():
    if os.path.exists(engine.db_path):
        backup_name = f"clarity_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy(engine.db_path, backup_name)
        return backup_name
    return None

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide", page_icon="🔮")
    col_title_left, col_title_center, col_title_right = st.columns([1,2,1])
    with col_title_center:
        st.title("🔮 CLARITY 18.0 ELITE")
        st.markdown(f"<p style='text-align: center;'>Unified Quick Scanner | Auto-Settle | Advanced Modeling | {VERSION}</p>", unsafe_allow_html=True)
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        col_status1, col_status2 = st.columns(2)
        with col_status1:
            st.success("✅ BallsDontLie (NBA props)")
            st.success("✅ Odds-API.io (game lines)")
            st.success("✅ Auto-Settle")
        with col_status2:
            st.success("✅ Real Rosters")
            st.success("✅ Slip Import")
            st.success("✅ Smart Scheduling")
        st.divider()
        new_max_unit = st.slider("Max unit size (% of bankroll)", 1, 15, int(engine.max_unit_size*100), 1) / 100.0
        if new_max_unit != engine.max_unit_size:
            engine.max_unit_size = new_max_unit
            st.info(f"Max unit size set to {engine.max_unit_size*100:.0f}%")
        if st.button("💾 Export Database Backup", use_container_width=True):
            backup_file = export_database()
            if backup_file:
                st.success(f"✅ Backup saved: {backup_file}")
            else:
                st.error("❌ Database file not found.")
        st.divider()
        col_metrics1, col_metrics2, col_metrics3 = st.columns(3)
        with col_metrics1: st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        with col_metrics2: st.metric("Daily Loss Left", f"${max(0, engine.daily_loss_limit - engine.daily_loss_today):.0f}")
        with col_metrics3: st.metric("SEM Score", f"{engine.sem_score}/100")
        col_metrics4, col_metrics5 = st.columns(2)
        with col_metrics4: st.metric("Prob Bolt", f"{engine.prob_bolt:.2f}")
        with col_metrics5: st.metric("DTM Bolt", f"{engine.dtm_bolt:.3f}")

    # =========================================================================
    # 5 TABS: GAME MARKETS, PASTE & SCAN, SCANNERS & ACCURACY, PLAYER PROPS, SELF EVALUATION
    # =========================================================================
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🎮 GAME MARKETS", "📋 PASTE & SCAN", "📊 SCANNERS & ACCURACY", "🎯 PLAYER PROPS", "🔧 SELF EVALUATION"
    ])

    all_sports = ["NBA", "MLB", "NHL", "NFL", "SOCCER_EPL", "SOCCER_LALIGA", "COLLEGE_BASKETBALL", "COLLEGE_FOOTBALL", "ESPORTS_LOL", "ESPORTS_CS2"]
    scanning_info = """
    **📅 Optimal Scanning Windows (for best lines & player props):**
    - **NBA, MLB, NHL**: 6 AM, 2 PM, 9 PM
    - **NFL**: Monday 10 AM, Tuesday 6 AM, Sunday 10 AM
    - **EPL / La Liga**: Afternoon (2 PM) the day before matches
    """

    # =========================================================================
    # TAB 1: GAME MARKETS - Live game lines, alternate lines, parlays
    # =========================================================================
    with tab1:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("🎮 Game Markets")
        st.subheader("📅 Auto-Load Games")
        col1, col2, col3 = st.columns([2,1,1])
        with col1:
            auto_sport = st.selectbox("Select Sport", all_sports, key="auto_sport")
        with col2:
            load_tomorrow = st.checkbox("Load tomorrow's games", value=False)
        with col3:
            if st.button("📅 LOAD GAMES", type="primary"):
                days_offset = 1 if load_tomorrow else 0
                check_scan_timing(auto_sport)
                with st.spinner(f"Fetching {'tomorrow' if days_offset else 'today'}'s games..."):
                    games = engine.game_scanner.fetch_games_by_date([auto_sport], days_offset)
                    if games:
                        st.session_state["auto_games"] = games
                        st.session_state["auto_games_analyzed"] = None
                        st.success(f"Loaded {len(games)} games")
                    else:
                        st.warning(f"No games found for {'tomorrow' if days_offset else 'today'}.")
        
        if "auto_games" in st.session_state and st.session_state["auto_games"]:
            game_options = [f"{g['home']} vs {g['away']}" for g in st.session_state["auto_games"]]
            selected_game = st.selectbox("Select a game", game_options)
            if selected_game:
                idx = game_options.index(selected_game)
                game = st.session_state["auto_games"][idx]
                home = game['home']; away = game['away']; sport = game['sport']
                st.info(f"**{home}** vs **{away}**")
                recommendations_found = False
                approved_bets_for_parlay = []
                
                # Moneyline Analysis
                if game.get("home_ml") and game.get("away_ml"):
                    ml_result = engine.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                    if ml_result.get('units', 0) > 0:
                        st.success(f"✅ CLARITY APPROVED: **{ml_result['pick']} ML** ({ml_result['odds']}) – Edge: {ml_result['edge']:.1%} – Units: {ml_result['units']}")
                        approved_bets_for_parlay.append({"description": f"{ml_result['pick']} ML", "odds": ml_result['odds'], "edge": ml_result['edge'], "units": ml_result['units'], "game": f"{home} vs {away}"})
                        recommendations_found = True
                    else:
                        st.info(f"❌ Moneyline not approved – {ml_result.get('reject_reason', 'Insufficient edge')}")
                
                # Spread Analysis
                if game.get("spread") and game.get("spread_odds"):
                    spread_approved = False
                    for pick_side in [home, away]:
                        spread_res = engine.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                        if spread_res.get('units', 0) > 0:
                            st.success(f"✅ CLARITY APPROVED: **{pick_side} {game['spread']:+.1f}** ({game['spread_odds']}) – Edge: {spread_res['edge']:.1%} – Units: {spread_res['units']}")
                            approved_bets_for_parlay.append({"description": f"{pick_side} {game['spread']:+.1f}", "odds": game['spread_odds'], "edge": spread_res['edge'], "units": spread_res['units'], "game": f"{home} vs {away}"})
                            spread_approved = True
                            recommendations_found = True
                    if not spread_approved:
                        st.info(f"❌ Spread not approved – No significant edge")
                
                # Total Analysis
                if game.get("total"):
                    total_approved = False
                    for pick_side, odds in [("OVER", game.get("over_odds", -110)), ("UNDER", game.get("under_odds", -110))]:
                        total_res = engine.analyze_total(home, away, game["total"], pick_side, sport, odds)
                        if total_res.get('units', 0) > 0:
                            st.success(f"✅ CLARITY APPROVED: **{pick_side} {game['total']}** ({odds}) – Edge: {total_res['edge']:.1%} – Units: {total_res['units']}")
                            approved_bets_for_parlay.append({"description": f"{pick_side} {game['total']}", "odds": odds, "edge": total_res['edge'], "units": total_res['units'], "game": f"{home} vs {away}"})
                            total_approved = True
                            recommendations_found = True
                    if not total_approved:
                        st.info(f"❌ Total not approved – No significant edge")
                
                st.markdown("---")
                st.subheader("🔄 Best Alternate Lines")
                alt_found = False
                if game.get("spread") and game.get("spread_odds"):
                    alt_spreads = [game["spread"] + 1, game["spread"] - 1]
                    for alt_spread in alt_spreads:
                        if abs(alt_spread - game["spread"]) <= 2:
                            est_odds = game["spread_odds"] + (10 if alt_spread > game["spread"] else -10)
                            for pick_side in [home, away]:
                                alt_res = engine.analyze_spread(home, away, alt_spread, pick_side, sport, est_odds)
                                if alt_res.get('units', 0) > 0:
                                    st.success(f"✅ CLARITY APPROVED (Alternate): **{pick_side} {alt_spread:+.1f}** (est. {est_odds}) – Edge: {alt_res['edge']:.1%} – Units: {alt_res['units']}")
                                    alt_found = True
                                    break
                if game.get("total"):
                    alt_totals = [game["total"] + 1, game["total"] - 1]
                    for alt_total in alt_totals:
                        est_over_odds = game.get("over_odds", -110) - 10 if alt_total > game["total"] else game.get("over_odds", -110) + 10
                        est_under_odds = game.get("under_odds", -110) - 10 if alt_total < game["total"] else game.get("under_odds", -110) + 10
                        for pick_side, odds in [("OVER", est_over_odds), ("UNDER", est_under_odds)]:
                            alt_res = engine.analyze_total(home, away, alt_total, pick_side, sport, odds)
                            if alt_res.get('units', 0) > 0:
                                st.success(f"✅ CLARITY APPROVED (Alternate): **{pick_side} {alt_total}** (est. {odds}) – Edge: {alt_res['edge']:.1%} – Units: {alt_res['units']}")
                                alt_found = True
                                break
                if not alt_found:
                    st.info("No alternate lines with significant edge found.")
                if not recommendations_found and not alt_found:
                    st.warning("⚠️ No CLARITY approved bets found for this game.")
                
                st.markdown("---")
                st.subheader("🎯 CLARITY SUGGESTED PARLAYS")
                if "auto_games_analyzed" not in st.session_state or st.session_state["auto_games_analyzed"] is None:
                    all_approved = []
                    for g in st.session_state["auto_games"]:
                        g_home = g['home']; g_away = g['away']; g_sport = g['sport']
                        if g.get("home_ml") and g.get("away_ml"):
                            ml_res = engine.analyze_moneyline(g_home, g_away, g_sport, g["home_ml"], g["away_ml"])
                            if ml_res.get('units', 0) > 0:
                                all_approved.append({"description": f"{ml_res['pick']} ML","odds": ml_res['odds'],"edge": ml_res['edge'],"game": f"{g_home} vs {g_away}"})
                        if g.get("spread") and g.get("spread_odds"):
                            for pick_side in [g_home, g_away]:
                                spread_res = engine.analyze_spread(g_home, g_away, g["spread"], pick_side, g_sport, g["spread_odds"])
                                if spread_res.get('units', 0) > 0:
                                    all_approved.append({"description": f"{pick_side} {g['spread']:+.1f}","odds": g['spread_odds'],"edge": spread_res['edge'],"game": f"{g_home} vs {g_away}"})
                        if g.get("total"):
                            for pick_side, odds in [("OVER", g.get("over_odds", -110)), ("UNDER", g.get("under_odds", -110))]:
                                total_res = engine.analyze_total(g_home, g_away, g["total"], pick_side, g_sport, odds)
                                if total_res.get('units', 0) > 0:
                                    all_approved.append({"description": f"{pick_side} {g['total']}","odds": odds,"edge": total_res['edge'],"game": f"{g_home} vs {g_away}"})
                    st.session_state["auto_games_analyzed"] = all_approved
                
                all_approved = st.session_state.get("auto_games_analyzed", [])
                if len(all_approved) >= 2:
                    def decimal_odds(american): return american/100+1 if american>0 else 100/abs(american)+1
                    best_bets = sorted(all_approved, key=lambda x: x['edge'], reverse=True)
                    leg1 = best_bets[0]
                    leg2 = next((b for b in best_bets[1:] if b['game'] != leg1['game']), None)
                    if leg2:
                        dec1 = decimal_odds(leg1['odds']); dec2 = decimal_odds(leg2['odds'])
                        parlay_odds = round((dec1 * dec2 - 1) * 100)
                        st.success(f"**🔒 2-LEG PARLAY**")
                        st.markdown(f"- {leg1['description']} ({leg1['odds']}) – Edge: {leg1['edge']:.1%}")
                        st.markdown(f"- {leg2['description']} ({leg2['odds']}) – Edge: {leg2['edge']:.1%}")
                        st.caption(f"📊 Estimated odds: {'+'+str(parlay_odds) if parlay_odds>0 else parlay_odds}")
                    
                    leg3 = next((b for b in best_bets[2:] if b['game'] not in [leg1['game'], leg2['game']]), None)
                    if leg2 and leg3:
                        dec1, dec2, dec3 = decimal_odds(leg1['odds']), decimal_odds(leg2['odds']), decimal_odds(leg3['odds'])
                        parlay_odds = round((dec1 * dec2 * dec3 - 1) * 100)
                        st.success(f"**🚀 3-LEG PARLAY**")
                        st.markdown(f"- {leg1['description']} ({leg1['odds']}) – Edge: {leg1['edge']:.1%}")
                        st.markdown(f"- {leg2['description']} ({leg2['odds']}) – Edge: {leg2['edge']:.1%}")
                        st.markdown(f"- {leg3['description']} ({leg3['odds']}) – Edge: {leg3['edge']:.1%}")
                        st.caption(f"📊 Estimated odds: {'+'+str(parlay_odds) if parlay_odds>0 else parlay_odds}")
                else:
                    st.info("Need at least 2 approved bets from different games to build a parlay.")
        
        st.markdown("---")
        st.subheader("✏️ Manual Entry")
        game_tab1, game_tab2, game_tab3, game_tab4 = st.tabs(["💰 Moneyline", "📊 Spread", "📈 Totals", "🔄 Alt Lines"])
        
        with game_tab1:
            c1, c2 = st.columns(2)
            with c1:
                sport_ml = st.selectbox("Sport", all_sports, key="ml_sport")
                teams_ml = engine.get_teams(sport_ml)
                home = st.selectbox("Home Team", teams_ml, key="ml_home")
                away = st.selectbox("Away Team", teams_ml, key="ml_away")
            with c2:
                home_odds = st.number_input("Home Odds", -500, 500, -110, key="ml_home_odds")
                away_odds = st.number_input("Away Odds", -500, 500, -110, key="ml_away_odds")
            if st.button("💰 ANALYZE MONEYLINE", type="primary", key="ml_button"):
                result = engine.analyze_moneyline(home, away, sport_ml, home_odds, away_odds)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {result['pick']} ({result['odds']})")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.metric("Win Probability", f"{result['win_prob']:.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        
        with game_tab2:
            c1, c2 = st.columns(2)
            with c1:
                sport_sp = st.selectbox("Sport", all_sports, key="sp_sport")
                teams_sp = engine.get_teams(sport_sp)
                home_sp = st.selectbox("Home Team", teams_sp, key="sp_home")
                away_sp = st.selectbox("Away Team", teams_sp, key="sp_away")
                spread = st.number_input("Spread", -30.0, 30.0, -5.5, key="sp_line")
            with c2:
                pick_sp = st.selectbox("Pick", [home_sp, away_sp], key="sp_pick")
                odds_sp = st.number_input("Odds", -500, 500, -110, key="sp_odds")
            if st.button("📊 ANALYZE SPREAD", type="primary", key="sp_button"):
                result = engine.analyze_spread(home_sp, away_sp, spread, pick_sp, sport_sp, odds_sp)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {pick_sp} {spread:+.1f} ({odds_sp})")
                    st.metric("Cover Probability", f"{result['prob_cover']:.1%}")
                    st.metric("Push Probability", f"{result['prob_push']:.1%}")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        
        with game_tab3:
            c1, c2 = st.columns(2)
            with c1:
                sport_tot = st.selectbox("Sport", all_sports, key="tot_sport")
                teams_tot = engine.get_teams(sport_tot)
                home_tot = st.selectbox("Home Team", teams_tot, key="tot_home")
                away_tot = st.selectbox("Away Team", teams_tot, key="tot_away")
                max_total = SPORT_MODELS[sport_tot]["avg_total"] * 2 if sport_tot in SPORT_MODELS else 300.0
                total_line = st.number_input("Total Line", 0.5, max_total, SPORT_MODELS.get(sport_tot, {}).get("avg_total", 220.5), key="tot_line")
            with c2:
                pick_tot = st.selectbox("Pick", ["OVER", "UNDER"], key="tot_pick")
                odds_tot = st.number_input("Odds", -500, 500, -110, key="tot_odds")
            if st.button("📈 ANALYZE TOTAL", type="primary", key="tot_button"):
                result = engine.analyze_total(home_tot, away_tot, total_line, pick_tot, sport_tot, odds_tot)
                if result.get('units', 0) > 0:
                    st.success(f"### {result['signal']} - {pick_tot} {total_line} ({odds_tot})")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Projection", f"{result['projection']:.1f}")
                    with c2: st.metric("OVER Prob", f"{result['prob_over']:.1%}")
                    with c3: st.metric("UNDER Prob", f"{result['prob_under']:.1%}")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")
        
        with game_tab4:
            c1, c2 = st.columns(2)
            with c1:
                sport_alt = st.selectbox("Sport", all_sports, key="alt_sport")
                base_line = st.number_input("Main Line", 0.5, 300.0, 220.5, key="alt_base")
                alt_line = st.number_input("Alternate Line", 0.5, 300.0, 230.5, key="alt_line")
            with c2:
                pick_alt = st.selectbox("Pick", ["OVER", "UNDER"], key="alt_pick")
                odds_alt = st.number_input("Odds", -500, 500, -110, key="alt_odds")
            if st.button("🔄 ANALYZE ALTERNATE", type="primary", key="alt_button"):
                result = engine.analyze_alternate(base_line, alt_line, pick_alt, sport_alt, odds_alt)
                if result['action'] == "BET":
                    st.success(f"### {result['action']}")
                elif result['action'] == "CONSIDER":
                    st.warning(f"### {result['action']}")
                else:
                    st.error(f"### {result['action']}")
                st.metric("Probability", f"{result['probability']:.1%}")
                st.metric("Implied", f"{result['implied']:.1%}")
                st.metric("Edge", f"{result['edge']:+.1%}")
                st.info(f"Value: {result['value']}")

    # =========================================================================
    # TAB 2: PASTE & SCAN - Paste or screenshot anything – props, game slips, tickets
    # =========================================================================
    with tab2:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("📋 PASTE & SCAN")
        st.markdown("Paste player props, game slips, or winning/losing tickets. Clarity auto‑detects and analyzes.")
        
        input_method = st.radio("Input method:", ["📝 Paste Text", "📸 Upload Screenshot"], key="ps_input_method")
        pasted_text = ""
        
        if input_method == "📝 Paste Text":
            pasted_text = st.text_area("Paste here", height=300, key="ps_text",
                                       placeholder="Examples:\n\nPlayer props:\nBrandon Miller Points 20.5 More\nStephen Curry Points 28.5 More\n\nGame slips:\nNew York Yankees +120 vs Boston Red Sox\nLos Angeles Dodgers -1.5 (-110) vs San Diego Padres\n\nWith results:\nSan Jose Sharks (+1.5) -182 ... Win")
        else:
            uploaded_file = st.file_uploader("Choose a screenshot", type=["png","jpg","jpeg"], key="ps_screenshot")
            if uploaded_file and st.button("📸 Extract from Screenshot", type="secondary"):
                with st.spinner("Extracting text via OCR..."):
                    extracted = parse_props_from_image(uploaded_file.getvalue(), uploaded_file.name, uploaded_file.type)
                    if extracted:
                        pasted_text = str(extracted)
                        st.success(f"Extracted {len(extracted)} props from screenshot")
                    else:
                        st.warning("No props found in image.")
        
        if st.button("🔍 ANALYZE & IMPORT", type="primary", use_container_width=True):
            if not pasted_text.strip():
                st.warning("Please paste something or upload a screenshot.")
            else:
                approved_props = []
                imported_bets = []
                rejected_items = []
                
                # Parse as player props
                props = parse_pasted_props(pasted_text)
                if props:
                    for prop in props:
                        result = engine.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"],
                                                     [], prop["sport"], -110, None, "HEALTHY", prop.get("opponent"))
                        if result.get('units', 0) > 0:
                            approved_props.append((prop, result))
                        else:
                            rejected_items.append((prop, result))
                
                # Parse as game slips with results
                slips = import_slip_text(pasted_text)
                for slip in slips:
                    if slip.get('result'):
                        conn = sqlite3.connect(engine.db_path)
                        c = conn.cursor()
                        bet_id = hashlib.md5(f"{slip['player']}{slip['market']}{slip['odds']}{datetime.now()}".encode()).hexdigest()[:12]
                        profit = slip.get('profit', 0) if slip.get('result') == 'WIN' else (-slip.get('risk', 100) if slip.get('result') == 'LOSS' else 0)
                        c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, settled_date, bolt_signal, profit)
                                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                  (bet_id, slip.get('player', ''), slip.get('sport', 'MLB'), slip.get('market', 'MONEYLINE'),
                                   slip.get('line', 0), slip.get('pick', ''), slip.get('odds', -110), 0,
                                   slip.get('result', 'PENDING'), 0, slip.get('date', datetime.now().strftime("%Y-%m-%d")),
                                   datetime.now().strftime("%Y-%m-%d"), "SLIP_IMPORT", profit))
                        conn.commit()
                        conn.close()
                        imported_bets.append(slip)
                
                # Display results
                if approved_props:
                    st.subheader("✅ APPROVED PLAYER PROPS")
                    for prop, res in approved_props:
                        st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                        st.caption(f"Edge: {res['raw_edge']:.1%} | Prob: {res['probability']:.1%} | Units: {res['units']} | Tier: {res['tier']}")
                
                if imported_bets:
                    st.subheader("✅ IMPORTED BETS (for learning)")
                    for bet in imported_bets:
                        result_emoji = "✅" if bet.get('result') == 'WIN' else "❌"
                        st.markdown(f"{result_emoji} **{bet.get('player', '')} {bet.get('market', '')}** – {bet.get('result', '?')}")
                
                if rejected_items:
                    with st.expander(f"❌ REJECTED / NO EDGE ({len(rejected_items)})"):
                        for item in rejected_items:
                            if isinstance(item, tuple) and len(item) == 2:
                                prop, res = item
                                st.markdown(f"**{prop['player']} {prop['pick']} {prop['line']} {prop['market']}**")
                                st.caption(f"Reason: {res.get('reject_reason', 'Insufficient edge')}")
                
                if not approved_props and not imported_bets and not rejected_items:
                    st.warning("No recognizable bets found. Please check format.")
        
        st.info("💡 **Tip:** You can paste multiple lines at once. Clarity auto-detects PrizePicks, MyBookie, and Bovada formats.")

    # =========================================================================
    # TAB 3: SCANNERS & ACCURACY - Best odds, arbitrage, middles, win rate
    # =========================================================================
    with tab3:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("📊 Scanners & Accuracy Dashboard")
        
        scanner_tabs = st.tabs(["📈 Best Odds", "💰 Arbitrage", "🎯 Middles", "📊 Accuracy"])
        
        with scanner_tabs[0]:
            st.header("Best Odds Scanner (Powered by Odds-API.io)")
            col1, col2 = st.columns([2,1])
            with col1:
                selected_sports_odds = st.multiselect("Select sports", ["NBA","MLB","NHL","NFL","TENNIS","PGA"], default=["NBA"], key="odds_sports")
            with col2:
                if st.button("🔍 SCAN BEST ODDS", type="primary", use_container_width=True):
                    with st.spinner("Scanning sportsbooks via Odds-API.io..."):
                        bets = engine.run_best_odds_scan(selected_sports_odds)
                        st.success(f"Found {len(bets)} +EV props!")
            if engine.scanned_bets.get("best_odds"):
                st.subheader("💰 Best +EV Props (Top 10)")
                for i, bet in enumerate(engine.scanned_bets["best_odds"], 1):
                    st.markdown(f"**{i}. {bet['player']} {bet['market']} {bet['pick']} {bet['line']}**")
                    st.caption(f"Odds: {bet['odds']} @ {bet['bookmaker']} | Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
            else:
                st.info("No +EV props found at this time. Try again when games are live.")
        
        with scanner_tabs[1]:
            st.header("Arbitrage Detector")
            if st.button("🔍 SCAN FOR ARBITRAGE", type="primary"):
                with st.spinner("Scanning..."):
                    if not engine.scanned_bets.get("best_odds"):
                        engine.run_best_odds_scan(["NBA"])
                    arbs = engine.scanned_bets.get("arbs", [])
                    if arbs:
                        st.success(f"Found {len(arbs)} arbitrage opportunities!")
                        for arb in arbs:
                            st.markdown(f"**{arb['Player']} - {arb['Market']}**")
                            st.caption(f"{arb['Bet 1']} | {arb['Bet 2']}")
                            st.metric("Arbitrage %", f"{arb['Arb %']}%")
                    else:
                        st.info("No arbitrage opportunities found.")
        
        with scanner_tabs[2]:
            st.header("Middle Hunter")
            if st.button("🔍 HUNT FOR MIDDLES", type="primary"):
                with st.spinner("Hunting..."):
                    if not engine.scanned_bets.get("best_odds"):
                        engine.run_best_odds_scan(["NBA"])
                    middles = engine.scanned_bets.get("middles", [])
                    if middles:
                        st.success(f"Found {len(middles)} middle opportunities!")
                        for mid in middles:
                            st.markdown(f"**{mid['Player']} - {mid['Market']}**")
                            st.caption(f"Window: {mid['Middle Window']} (Size: {mid['Window Size']})")
                            st.caption(f"{mid['Leg 1']} | {mid['Leg 2']}")
                    else:
                        st.info("No middle opportunities found.")
        
        with scanner_tabs[3]:
            st.header("Public Accuracy Dashboard")
            accuracy = engine.get_accuracy_dashboard()
            col1, col2, col3, col4 = st.columns(4)
            with col1: st.metric("Total Bets", accuracy['total_bets'])
            with col2: st.metric("Win Rate", f"{accuracy['win_rate']}%")
            with col3: st.metric("ROI", f"{accuracy['roi']}%")
            with col4: st.metric("Units Profit", f"+{accuracy['units_profit']}" if accuracy['units_profit']>0 else str(accuracy['units_profit']))
            
            st.subheader("By Sport")
            if accuracy['by_sport']:
                sport_df = pd.DataFrame(accuracy['by_sport']).T
                st.dataframe(sport_df)
            else:
                st.info("No settled bets by sport yet.")
            
            st.subheader("By Tier")
            if accuracy['by_tier']:
                tier_df = pd.DataFrame(accuracy['by_tier']).T
                st.dataframe(tier_df)
            else:
                st.info("No settled bets by tier yet.")
            
            st.metric("SEM Score", f"{accuracy['sem_score']}/100")

    # =========================================================================
    # TAB 4: PLAYER PROPS - Manual dropdown analyzer
    # =========================================================================
    with tab4:
        with st.expander("📅 Optimal Scanning Times (click to expand)"):
            st.markdown(scanning_info)
        st.header("🎯 Manual Player Prop Analyzer (Real Rosters)")
        
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", all_sports, key="prop_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team (for context)", [""] + teams, key="prop_team") if sport in ["NBA","MLB","NHL","NFL","SOCCER_EPL","SOCCER_LALIGA","COLLEGE_BASKETBALL","COLLEGE_FOOTBALL"] else ""
            roster = engine.get_roster(sport, team) if team else engine._get_individual_sport_players(sport)
            player = st.selectbox("Player", roster, key="prop_player")
            available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
            market = st.selectbox("Market", available_markets, key="prop_market")
            line = st.number_input("Line", 0.5, 200.0, 0.5, key="prop_line")
            pick = st.selectbox("Pick", ["OVER","UNDER"], key="prop_pick")
            opponent = st.selectbox("Opponent (optional)", [""] + teams, key="prop_opponent") if teams else ""
        with c2:
            use_real_stats = st.checkbox("Fetch real stats & injuries (API-Sports)", value=False)
            st.info("Note: Real stats are currently using BallsDontLie for NBA, fallback for others.")
            odds = st.number_input("Odds (American)", -500, 500, -110, key="prop_odds")
        
        if st.button("🚀 ANALYZE PROP", type="primary", use_container_width=True):
            if not player or player == "Select team first" or player.startswith("Player "):
                st.error("Please select a valid player.")
            else:
                result = engine.analyze_prop(player, market, line, pick, [], sport, odds, team if team else None, "HEALTHY", opponent)
                if result.get('units',0) > 0:
                    st.success(f"### {result['signal']}")
                    if result.get('season_warning'): st.warning(result['season_warning'])
                    if result.get('injury') != "HEALTHY": st.error(f"⚠️ Injury flag: {result['injury']}")
                    col1, col2, col3 = st.columns(3)
                    with col1: st.metric("Projection", f"{result['projection']:.1f}")
                    with col2: st.metric("Probability", f"{result['probability']:.1%}")
                    with col3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
                    st.metric("Tier", result['tier'])
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                else:
                    st.error(f"### {result['signal']}")
                    if result.get('reject_reason'): st.warning(f"Reason: {result['reject_reason']}")

    # =========================================================================
    # TAB 5: SELF EVALUATION - Auto-settle, pending bets, tuning history, SEM calibration
    # =========================================================================
    with tab5:
        st.header("🔧 Self Evaluation & Data Management")
        
        # Auto-Tune History
        st.subheader("📈 Auto-Tune History (ROI-based)")
        conn = sqlite3.connect(engine.db_path)
        df = pd.read_sql_query("SELECT * FROM tuning_log ORDER BY id DESC", conn)
        conn.close()
        if df.empty:
            st.info("No tuning events yet. After 50+ settled bets, auto-tune will run weekly.")
        else:
            st.dataframe(df)
        
        st.markdown("---")
        
        # Import Player Props with Auto-Settle
        st.subheader("📥 Import Player Props (Auto-Settle)")
        st.markdown("""
        **Paste player props in numbered format.** Clarity will automatically fetch actual stats and mark WIN/LOSS.
        Example:
1
Brandin Podziemski
GSW vs LAC · PRA
22.5
NONE
REVERSE
0.0
        """)
        prop_text = st.text_area("Paste player props here", height=200, key="player_props_import")
        game_date_input = st.date_input("Game date (default: yesterday)", value=datetime.now() - timedelta(days=1))
        
        if st.button("🔍 Import & Auto-Settle Props", type="primary", use_container_width=True):
            if prop_text.strip():
                with st.spinner("Parsing and settling props..."):
                    props = parse_pasted_props(prop_text, default_date=game_date_input.strftime("%Y-%m-%d"))
                    if not props:
                        st.warning("No props recognized. Check format.")
                    else:
                        imported_count = 0
                        for prop in props:
                            result, actual = auto_settle_prop(
                                prop["player"], prop["market"], prop["line"], prop["pick"],
                                prop.get("sport", "NBA"), prop.get("opponent", ""), prop["game_date"]
                            )
                            odds = -110
                            profit = (abs(odds)/100 * 100) if result == "WIN" else -100
                            if odds > 0:
                                profit = (odds/100 * 100) if result == "WIN" else -100
                            conn = sqlite3.connect(engine.db_path)
                            c = conn.cursor()
                            bet_id = hashlib.md5(f"{prop['player']}{prop['market']}{prop['line']}{datetime.now()}".encode()).hexdigest()[:12]
                            c.execute("INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, settled_date, bolt_signal, profit) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                      (bet_id, prop['player'], prop.get('sport','NBA'), prop['market'], prop['line'],
                                       prop['pick'], odds, 0.0, result, actual,
                                       prop['game_date'], datetime.now().strftime("%Y-%m-%d"), "AUTO_SETTLED", profit))
                            conn.commit()
                            conn.close()
                            imported_count += 1
                        st.success(f"✅ Imported and settled {imported_count} props!")
                        engine._calibrate_sem()
                        engine.auto_tune_thresholds()
                        engine._auto_retrain_ml()
                        st.rerun()
            else:
                st.warning("Please paste some props.")
        
        st.markdown("---")
        
        # Pending Bets Management
        st.subheader("📋 Pending Bets")
        conn = sqlite3.connect(engine.db_path)
        pending_df = pd.read_sql_query("SELECT id, player, sport, market, line, pick, odds, date FROM bets WHERE result = 'PENDING' ORDER BY date DESC", conn)
        conn.close()
        
        if pending_df.empty:
            st.info("No pending bets.")
        else:
            st.dataframe(pending_df)
            st.subheader("Settle a Pending Bet")
            bet_ids = pending_df['id'].tolist()
            selected_bet_id = st.selectbox("Select bet to settle", bet_ids, format_func=lambda x: pending_df[pending_df['id']==x]['player'].iloc[0])
            actual_result = st.number_input("Actual result", value=0.0, step=0.5)
            
            if st.button("Settle Selected Bet", use_container_width=True):
                conn = sqlite3.connect(engine.db_path)
                c = conn.cursor()
                c.execute("SELECT line, pick, odds FROM bets WHERE id = ?", (selected_bet_id,))
                row = c.fetchone()
                if row:
                    line, pick, odds = row
                    if pick and line:
                        won = (actual_result > line) if pick == "OVER" else (actual_result < line)
                        result = "WIN" if won else "LOSS"
                        profit = (abs(odds)/100 * 100) if won else -100
                        if odds > 0:
                            profit = (odds/100 * 100) if won else -100
                        c.execute("UPDATE bets SET result = ?, actual = ?, settled_date = ?, profit = ? WHERE id = ?",
                                  (result, actual_result, datetime.now().strftime("%Y-%m-%d"), profit, selected_bet_id))
                    else:
                        c.execute("UPDATE bets SET result = ?, settled_date = ? WHERE id = ?",
                                  ("SETTLED", datetime.now().strftime("%Y-%m-%d"), selected_bet_id))
                    conn.commit()
                    st.success(f"Bet settled")
                    engine._calibrate_sem()
                    engine.auto_tune_thresholds()
                    engine._auto_retrain_ml()
                    st.rerun()
                conn.close()
        
        st.markdown("---")
        
        # SEM Score Calibration Info
        st.subheader("📊 SEM Score Calibration")
        st.metric("Current SEM Score", f"{engine.sem_score}/100")
        st.caption("SEM Score auto-calibrates based on betting accuracy. Higher score = more confident predictions.")

if __name__ == "__main__":
    run_dashboard()
