with tabs[5]:
    st.header("🛠️ System Health & Error Monitor")
    
    # --- API Health Dashboard (colored dots) ---
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
    
    # --- Recent Errors from Logs (last 5 lines of clarity_debug.log) ---
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
    
    # --- Manual Test Buttons (lightweight, on demand) ---
    st.subheader("🔍 Quick Diagnostics")
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
    
    # --- Existing tools ---
    st.divider()
    st.subheader("🧹 Maintenance")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Clear Pending Slips"):
            clear_pending_slips()
            st.success("Done")
    with col2:
        if st.button("Recalibrate SEM"):
            _calibrate_sem()
            st.success("SEM recalibrated")
    with col3:
        if st.button("Export Logs"):
            if os.path.exists("clarity_debug.log"):
                with open("clarity_debug.log", "rb") as f:
                    st.download_button("Download debug log", f, file_name="clarity_debug.log")
            else:
                st.warning("No log file")
