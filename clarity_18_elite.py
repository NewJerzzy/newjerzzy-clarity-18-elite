""")

prop_text = st.text_area("Paste player props here", height=250)
game_date_input = st.date_input("Game date (default: yesterday)", value=datetime.now() - timedelta(days=1))

if st.button("🔍 Import & Auto-Settle Props", type="primary"):
    if prop_text.strip():
        with st.spinner("Parsing and settling props..."):
            props = parse_pasted_props(prop_text, default_date=game_date_input.strftime("%Y-%m-%d"))
            if not props:
                st.warning("No props recognized. Check format.")
            else:
                imported_count = 0
                for prop in props:
                    # Auto-settle
                    result, actual = auto_settle_prop(
                        prop["player"], prop["market"], prop["line"], prop["pick"],
                        prop.get("sport", "NBA"), prop.get("opponent", ""), prop["game_date"]
                    )
                    # Determine profit (assuming standard -110 odds)
                    odds = -110
                    profit = (abs(odds)/100 * 100) if result == "WIN" else -100
                    if odds > 0:
                        profit = (odds/100 * 100) if result == "WIN" else -100
                    
                    conn = sqlite3.connect(engine.db_path)
                    c = conn.cursor()
                    bet_id = hashlib.md5(f"{prop['player']}{prop['market']}{prop['line']}{datetime.now()}".encode()).hexdigest()[:12]
                    c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, actual, date, settled_date, bolt_signal, profit)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
    if st.button("Settle Selected Bet"):
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

if __name__ == "__main__":
run_dashboard()
