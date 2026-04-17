# Add this right before the Streamlit dashboard code (around line 1500+)

# =============================================================================
# CLARITY ENGINE – COMPLETE WITH ALL METHODS
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.game_scanner = GameScanner(ODDS_API_KEY)
        self.season_context = SeasonContextEngine()
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
        self.daily_loss_limit = 200.0
        self.max_unit_size = 0.05
        self.correlation_threshold = 0.12
        self.db_path = "clarity_history.db"
        self._init_db()
        self.sem_score = 100
        self.scanned_bets = {"props":[],"games":[],"rejected":[],"best_odds":[],"arbs":[],"middles":[]}
        self.daily_loss_today = 0.0
        self.last_reset_date = datetime.now().date()
        self.last_tune_date = None
        self.last_ml_retrain_date = None
        self._load_tuning_state()
        self._load_ml_retrain_date()
        self._auto_retrain_ml()
        self._correlation_cache = {}
        self._venue_cache = {}
        self._pace_cache = {}

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY, player TEXT, sport TEXT, market TEXT, line REAL,
            pick TEXT, odds INTEGER, edge REAL, result TEXT, actual REAL,
            date TEXT, settled_date TEXT, bolt_signal TEXT, profit REAL,
            closing_odds INTEGER, ml_proba REAL, wa_proba REAL,
            is_home INTEGER DEFAULT 0
        )""")
        try:
            c.execute("ALTER TABLE bets ADD COLUMN is_home INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        c.execute("""CREATE TABLE IF NOT EXISTS correlations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player TEXT, market1 TEXT, market2 TEXT, covariance REAL, sample_size INTEGER,
            last_updated TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sem_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, sem_score INTEGER, accuracy REAL, bets_analyzed INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tuning_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, prob_bolt_old REAL, prob_bolt_new REAL,
            dtm_bolt_old REAL, dtm_bolt_new REAL, roi REAL, bets_used INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS ml_retrain_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, bets_used INTEGER, rmse REAL
        )""")
        conn.commit()
        conn.close()

    def _load_tuning_state(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT timestamp FROM tuning_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row: 
            self.last_tune_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        conn.close()
        
    def _load_ml_retrain_date(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT timestamp FROM ml_retrain_log ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row: 
            self.last_ml_retrain_date = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        conn.close()
        
    def _auto_retrain_ml(self):
        if not LGB_AVAILABLE:
            return
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT player, sport, market, line, odds, result, actual FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if len(df) < 100:
            return
        if self.last_ml_retrain_date and (datetime.now() - self.last_ml_retrain_date).days < 7:
            return
        X = df[['line', 'odds']].values.astype(float)
        y = (df['result'] == 'WIN').astype(int).values
        ensemble.ml_model.train(X, y)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO ml_retrain_log (timestamp, bets_used, rmse) VALUES (?,?,?)",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), len(df), 0.0))
        conn.commit()
        conn.close()
        self.last_ml_retrain_date = datetime.now()
        # st.info removed to avoid Streamlit context issues

    def convert_odds(self, american): 
        return 1+american/100 if american>0 else 1+100/abs(american)
        
    def implied_prob(self, american): 
        return 100/(american+100) if american>0 else abs(american)/(abs(american)+100)
        
    def l42_check(self, stat, line, avg):
        config = STAT_CONFIG.get(stat.upper(), {"tier":"MED","buffer":2.0,"reject":False})
        if config["reject"]: 
            return False, f"RED TIER - {stat}"
        buffer = line - avg if stat.upper() not in ["OUTS"] else avg - line
        return (buffer >= config["buffer"]), f"BUFFER {buffer:.1f} < {config['buffer']}" if buffer < config["buffer"] else "PASS"
        
    def wsem_check(self, data):
        if len(data)<3: 
            return False, float('inf')
        w = np.ones(len(data)); w[-3:]*=1.5; w/=w.sum()
        mean = np.average(data, weights=w)
        var = np.average((np.array(data)-mean)**2, weights=w)
        wsem = np.sqrt(var/len(data))/abs(mean) if mean!=0 else float('inf')
        return wsem <= self.wsem_max, wsem

    def apply_bayesian_prior(self, data: List[float], market: str, sport: str, prior_weight: int = 3) -> List[float]:
        if len(data) >= 5:
            return data
        priors = {"NBA": {"PTS": 15.0, "REB": 5.0, "AST": 4.0, "STL": 1.0, "BLK": 0.8, "PRA": 24.0, "PR": 20.0, "PA": 19.0}}
        prior_mean = priors.get(sport, {}).get(market.upper(), 10.0)
        smoothed = (sum(data) + prior_mean * prior_weight) / (len(data) + prior_weight)
        return [smoothed] * 5

    def fetch_team_pace(self, team: str) -> float:
        return 1.0

    def get_player_venue_split(self, player: str, market: str, is_home: bool) -> float:
        return 1.0

    def update_correlation(self, player: str, market1: str, market2: str, actual1: float, actual2: float):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT covariance, sample_size FROM correlations WHERE player=? AND market1=? AND market2=?", (player, market1, market2))
        row = c.fetchone()
        if row:
            old_cov, n = row
            new_cov = (old_cov * n + (actual1 * actual2)) / (n + 1)
            new_n = n + 1
            c.execute("UPDATE correlations SET covariance=?, sample_size=?, last_updated=? WHERE player=? AND market1=? AND market2=?",
                      (new_cov, new_n, datetime.now().isoformat(), player, market1, market2))
        else:
            c.execute("INSERT INTO correlations (player, market1, market2, covariance, sample_size, last_updated) VALUES (?,?,?,?,?,?)",
                      (player, market1, market2, actual1 * actual2, 1, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_correlation(self, player: str, market1: str, market2: str) -> float:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT covariance FROM correlations WHERE player=? AND market1=? AND market2=?", (player, market1, market2))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 0.0

    def adjust_parlay_probability(self, probs: List[float], covariances: List[float]) -> float:
        if len(probs) == 1:
            return probs[0]
        result = probs[0] * probs[1] + covariances[0]
        for i in range(2, len(probs)):
            result = result * probs[i]
        return min(max(result, 0.0), 1.0)

    def simulate_prop(self, data, line, pick, sport="NBA", opponent=None, player=None, market=None, team=None, is_home=False):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        if data and len(data) > 0:
            w = np.ones(len(data)); w[-3:]*=1.5; w/=w.sum()
            lam = np.average(data, weights=w)
        else:
            lam = line * 0.95
        if opponent and sport in ["NBA", "NHL", "MLB"]:
            def_rating = opponent_strength.get_defensive_rating(sport, opponent)
            lam *= def_rating
        sims = nbinom.rvs(max(1,int(lam/2)), max(1,int(lam/2))/(max(1,int(lam/2))+lam), size=self.sims) if model["distribution"]=="nbinom" else poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims>=line) if pick=="OVER" else np.mean(sims<=line)
        dtm = (proj-line)/line if line!=0 else 0
        return {"proj":proj, "prob":prob, "dtm":dtm}

    def sovereign_bolt(self, prob, dtm, wsem_ok, l42_pass, injury, rest_fade=1.0):
        if injury=="OUT": 
            return {"signal":"🔴 INJURY RISK","units":0}
        if not l42_pass: 
            return {"signal":"🔴 L42 REJECT","units":0}
        if rest_fade < 0.9: 
            return {"signal":"🟠 REST FADE","units":0.5}
        if prob>=self.prob_bolt and dtm>=self.dtm_bolt and wsem_ok: 
            return {"signal":"🟢 SOVEREIGN BOLT ⚡","units":2.0}
        elif prob>=0.78 and wsem_ok: 
            return {"signal":"🟢 ELITE LOCK","units":1.5}
        elif prob>=0.70: 
            return {"signal":"🟡 APPROVED","units":1.0}
        return {"signal":"🔴 PASS","units":0}

    def analyze_prop(self, player, market, line, pick, data, sport, odds, team=None, injury_status="HEALTHY", opponent=None, is_home=False):
        if not data:
            if sport == "NBA":
                real_stats = balldontlie_get_player_stats(player, datetime.now().strftime("%Y-%m-%d"))
                if real_stats:
                    market_map = {"PTS": "pts", "REB": "reb", "AST": "ast", "STL": "stl", "BLK": "blk"}
                    stat_val = real_stats.get(market_map.get(market.upper(), "pts"), 0)
                    if stat_val:
                        data = [stat_val] * 5
            if not data:
                real_stats, real_injury = fetch_player_stats_and_injury(player, sport, market)
                if real_stats:
                    data = real_stats
                if real_injury != "HEALTHY":
                    injury_status = real_injury
        if not data:
            data = [line * 0.95] * 5
        rest_fade = 1.0
        if team:
            rest_fade, _ = rest_detector.get_rest_fade(sport, team)
        wa_sim = self.simulate_prop(data, line, pick, sport, opponent, player, market, team, is_home)
        final_prob = wa_sim["prob"]
        raw_edge = final_prob - self.implied_prob(odds)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(final_prob, wa_sim["dtm"], wsem_ok, l42_pass, injury_status, rest_fade)
        if market.upper() in RED_TIER_PROPS:
            tier, reject_reason = "REJECT", f"RED TIER - {market}"
            bolt["units"] = 0
        elif raw_edge >= 0.08:
            tier, reject_reason = "SAFE", None
        elif raw_edge >= 0.05:
            tier, reject_reason = "BALANCED+", None
        elif raw_edge >= 0.03:
            tier, reject_reason = "RISKY", None
        else:
            tier, reject_reason = "PASS", f"Insufficient edge ({raw_edge:.1%})"
            bolt["units"] = 0
        if injury_status != "HEALTHY":
            tier, reject_reason = "REJECT", f"Injury: {injury_status}"
            bolt["units"] = 0
        if rest_fade < 0.9:
            bolt["units"] = min(bolt["units"], 0.5)
        if datetime.now().date() > self.last_reset_date:
            self.daily_loss_today = 0.0
            self.last_reset_date = datetime.now().date()
        max_units = min(bolt["units"], self.max_unit_size * self.bankroll / 100)
        if self.daily_loss_today >= self.daily_loss_limit:
            bolt["units"] = 0
            tier = "REJECT"
            reject_reason = "Daily loss limit reached"
        else:
            bolt["units"] = min(bolt["units"], max_units)
        season_warning = None
        if team and sport in ["NBA","MLB","NHL","NFL"]:
            fade_check = self.season_context.should_fade_team(sport, team)
            if fade_check["fade"]:
                wa_sim["proj"] *= fade_check["multiplier"]
                season_warning = f"⚠️ {team}: {', '.join(fade_check['reasons'])}"
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge>0 and tier!="REJECT" else 0
        return {"player":player,"market":market,"line":line,"pick":pick,"signal":bolt["signal"],
                "units":bolt["units"] if tier!="REJECT" else 0,"projection":wa_sim["proj"],"probability":final_prob,
                "raw_edge":round(raw_edge,4),"tier":tier,"injury":injury_status,"l42_msg":l42_msg,
                "kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warning":season_warning,"reject_reason":reject_reason}

    def analyze_moneyline(self, home, away, sport, home_odds, away_odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_win_prob = 0.55 + (model.get("home_advantage",0)/100)
        away_win_prob = 1 - home_win_prob
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: home_win_prob *= home_fade["multiplier"]; away_win_prob = 1-home_win_prob; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: away_win_prob *= away_fade["multiplier"]; home_win_prob = 1-away_win_prob; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        home_imp, away_imp = self.implied_prob(home_odds), self.implied_prob(away_odds)
        home_edge, away_edge = home_win_prob - home_imp, away_win_prob - away_imp
        if home_edge > away_edge and home_edge > 0.02:
            pick, edge, odds, prob = home, home_edge, home_odds, home_win_prob
        elif away_edge > 0.02:
            pick, edge, odds, prob = away, away_edge, away_odds, away_win_prob
        else:
            return {"pick":"PASS","signal":"🔴 PASS","units":0,"edge":0,"reject_reason":"No significant edge"}
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        else: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"pick":pick,"signal":signal,"units":units,"edge":round(edge,4),"win_prob":round(prob,3),
                "tier":tier,"kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_spread(self, home, away, spread, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        base_margin = model.get("home_advantage",0)
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: base_margin *= home_fade["multiplier"]; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: base_margin /= away_fade["multiplier"]; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        sims = norm.rvs(loc=base_margin, scale=12, size=self.sims)
        prob_cover = np.mean(sims > -spread) if pick==home else np.mean(sims < -spread)
        prob_push = np.mean(np.abs(sims+spread)<0.5)
        prob = prob_cover/(1-prob_push) if prob_push<1 else prob_cover
        edge = prob - self.implied_prob(odds)
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else: tier, units, signal, reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"spread":spread,"pick":pick,"signal":signal,"units":units,
                "prob_cover":round(prob,3),"prob_push":round(prob_push,3),"edge":round(edge,4),
                "tier":tier,"kelly_stake":round(min(kelly,50),2),"odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_total(self, home, away, total_line, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        base_proj = model.get("avg_total",200) + (model.get("home_advantage",0)/2)
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA","MLB","NHL","NFL"] else {"fade":False}
        season_warnings = []
        if home_fade["fade"]: base_proj *= home_fade["multiplier"]; season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]: base_proj *= away_fade["multiplier"]; season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        sims = nbinom.rvs(max(1,int(base_proj/2)), max(1,int(base_proj/2))/(max(1,int(base_proj/2))+base_proj), size=self.sims) if model["distribution"]=="nbinom" else poisson.rvs(base_proj, size=self.sims)
        proj, prob_over, prob_under, prob_push = np.mean(sims), np.mean(sims>total_line), np.mean(sims<total_line), np.mean(sims==total_line)
        prob = (prob_over/(1-prob_push) if prob_push<1 else prob_over) if pick=="OVER" else (prob_under/(1-prob_push) if prob_push<1 else prob_under)
        edge = prob - self.implied_prob(odds)
        if edge>=0.05: tier, units, signal, reject_reason = "SAFE",2.0,"🟢 SAFE",None
        elif edge>=0.03: tier, units, signal, reject_reason = "BALANCED+",1.5,"🟡 BALANCED+",None
        elif edge>=0.01: tier, units, signal, reject_reason = "RISKY",1.0,"🟠 RISKY",None
        else: tier, units, signal, reject_reason = "PASS",0,"🔴 PASS",f"Insufficient edge ({edge:.1%})"
        kelly = edge * self.bankroll * 0.25 if edge>0 else 0
        return {"home":home,"away":away,"total_line":total_line,"pick":pick,"signal":signal,"units":units,
                "projection":round(proj,1),"prob_over":round(prob_over,3),"prob_under":round(prob_under,3),
                "prob_push":round(prob_push,3),"edge":round(edge,4),"tier":tier,"kelly_stake":round(min(kelly,50),2),
                "odds":odds,"season_warnings":season_warnings,"reject_reason":reject_reason}

    def analyze_alternate(self, base_line, alt_line, pick, sport, odds):
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        avg_total = model.get("avg_total",200)
        sims = norm.rvs(loc=avg_total, scale=avg_total*0.12, size=self.sims)
        prob = np.mean(sims>alt_line) if pick=="OVER" else np.mean(sims<alt_line)
        edge = prob - self.implied_prob(odds)
        if edge>=0.03: value, action = "GOOD VALUE","BET"
        elif edge>=0: value, action = "FAIR VALUE","CONSIDER"
        else: value, action = "POOR VALUE","AVOID"
        return {"base_line":base_line,"alt_line":alt_line,"pick":pick,"odds":odds,"probability":round(prob,3),
                "implied":round(self.implied_prob(odds),3),"edge":round(edge,4),"value":value,"action":action}

    def get_teams(self, sport): 
        return HARDCODED_TEAMS.get(sport, ["Select a sport first"])
        
    def get_roster(self, sport, team):
        if sport in ["PGA","TENNIS","UFC"]: 
            return self._get_individual_sport_players(sport)
        if team and sport in ["NBA","MLB","NHL","NFL"]:
            roster, is_fallback = fetch_team_roster(sport, team)
            if is_fallback and sport == "NBA":
                pass  # Skip st.warning to avoid Streamlit context issues
            return roster
        return ["Player 1","Player 2","Player 3","Player 4","Player 5"]
        
    def _get_individual_sport_players(self, sport):
        if sport=="PGA": 
            return ["Scottie Scheffler","Rory McIlroy","Jon Rahm","Ludvig Aberg","Xander Schauffele","Collin Morikawa"]
        elif sport=="TENNIS": 
            return ["Novak Djokovic","Carlos Alcaraz","Iga Swiatek","Coco Gauff","Aryna Sabalenka","Jannik Sinner"]
        elif sport=="UFC": 
            return ["Jon Jones","Islam Makhachev","Alex Pereira","Sean O'Malley","Ilia Topuria","Dricus Du Plessis"]
        return ["Player 1","Player 2","Player 3"]

    def run_best_bets_scan(self, selected_sports, stop_event=None, progress_callback=None, result_callback=None, days_offset=0):
        game_bets, prop_bets, rejected = [], [], []
        games = self.game_scanner.fetch_games_by_date(selected_sports, days_offset)
        for game in games:
            if stop_event and stop_event.is_set(): break
            sport, home, away = game["sport"], game["home"], game["away"]
            if game.get("home_ml") and game.get("away_ml"):
                ml = self.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                bet_info = {"type":"moneyline","sport":sport,"description":f"{ml.get('pick','PASS')} ML vs {away if ml.get('pick')==home else home}",
                            "bet_line":f"{ml.get('pick','N/A')} ML ({game['home_ml'] if ml.get('pick')==home else game['away_ml']}) vs {away if ml.get('pick')==home else home}",
                            "edge":ml.get('edge',0),"probability":ml.get('win_prob',0.0),"units":ml.get('units',0),
                            "odds":game['home_ml'] if ml.get('pick')==home else game['away_ml'],"season_warnings":ml.get('season_warnings',[]),"reject_reason":ml.get('reject_reason')}
                if ml.get('units',0)>0: game_bets.append(bet_info)
                else: rejected.append(bet_info)
            if game.get("spread") and game.get("spread_odds"):
                for pick_side in [home, away]:
                    spread_res = self.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                    bet_info = {"type":"spread","sport":sport,"description":f"{pick_side} {game['spread']:+.1f} vs {away if pick_side==home else home}",
                                "bet_line":f"{pick_side} {game['spread']:+.1f} ({game['spread_odds']}) vs {away if pick_side==home else home}",
                                "edge":spread_res.get('edge',0),"probability":spread_res.get('prob_cover',0.0),"units":spread_res.get('units',0),
                                "odds":game['spread_odds'],"season_warnings":spread_res.get('season_warnings',[]),"reject_reason":spread_res.get('reject_reason')}
                    if spread_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
            if game.get("total"):
                for pick_side, odds in [("OVER",game.get("over_odds",-110)),("UNDER",game.get("under_odds",-110))]:
                    total_res = self.analyze_total(home, away, game["total"], pick_side, sport, odds)
                    bet_info = {"type":"total","sport":sport,"description":f"{home} vs {away}: {pick_side} {game['total']}",
                                "bet_line":f"{home} vs {away} — {pick_side} {game['total']} ({odds})",
                                "edge":total_res.get('edge',0),"probability":total_res.get('prob_over' if pick_side=="OVER" else 'prob_under',0.0),
                                "units":total_res.get('units',0),"odds":odds,"season_warnings":total_res.get('season_warnings',[]),"reject_reason":total_res.get('reject_reason')}
                    if total_res.get('units',0)>0: game_bets.append(bet_info)
                    else: rejected.append(bet_info)
        for sport in selected_sports:
            if stop_event and stop_event.is_set(): break
            if progress_callback: progress_callback(f"Scanning {sport}...")
            check_scan_timing(sport)
            if sport == "NBA":
                props = self._fetch_balldontlie_props()
            else:
                sport_key = {"NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl"}.get(sport, "basketball_nba")
                props = self.game_scanner.fetch_player_props_odds(sport_key)
            for prop in props:
                if stop_event and stop_event.is_set(): break
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"], [], sport, prop["odds"], None, "HEALTHY")
                bet_info = {"type":"player_prop","sport":sport,"description":f"{prop['player']} {prop['pick']} {prop['line']} {prop['market']}",
                            "bet_line":f"{prop['player']} {prop['pick']} {prop['line']} ({prop['odds']})","edge":result.get('raw_edge',0),
                            "probability":result.get('probability',0.0),"units":result.get('units',0),"odds":prop['odds'],
                            "season_warning":result.get('season_warning'),"reject_reason":result.get('reject_reason')}
                if result.get('units',0)>0: prop_bets.append(bet_info)
                else: rejected.append(bet_info)
                if result_callback: result_callback(bet_info)
        game_bets.sort(key=lambda x:x['edge'], reverse=True); prop_bets.sort(key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["props"] = prop_bets; self.scanned_bets["games"] = game_bets; self.scanned_bets["rejected"] = rejected
        return self.scanned_bets

    def _fetch_balldontlie_props(self) -> List[Dict]:
        all_props = []
        today = datetime.now().strftime("%Y-%m-%d")
        games_data = balldontlie_request("/nba/games", params={"dates[]": today})
        if not games_data or not games_data.get("data"):
            return []
        for game in games_data.get("data", []):
            game_id = game.get("id")
            if not game_id:
                continue
            props_data = balldontlie_request("/nba/player_props", params={"game_id": game_id})
            if props_data and "data" in props_data:
                for prop in props_data["data"]:
                    player = prop.get("player", {})
                    player_name = f"{player.get('first_name', '')} {player.get('last_name', '')}".strip()
                    market = prop.get("market", "").upper()
                    line = prop.get("line", 0)
                    odds = prop.get("price", -110)
                    bookmaker = prop.get("bookmaker", "BallsDontLie")
                    pick = prop.get("side", "OVER").upper()
                    market_map = {"POINTS": "PTS", "REBOUNDS": "REB", "ASSISTS": "AST",
                                  "THREE_POINTERS_MADE": "THREES", "STEALS": "STL", "BLOCKS": "BLK"}
                    market = market_map.get(market, market)
                    if player_name and market and line:
                        all_props.append({"sport": "NBA", "player": player_name, "market": market,
                                          "line": line, "odds": odds, "bookmaker": bookmaker, "pick": pick})
        return all_props

    def run_best_odds_scan(self, selected_sports):
        all_bets = []
        for sport in selected_sports:
            check_scan_timing(sport)
            if sport == "NBA":
                props = self._fetch_balldontlie_props()
            else:
                sport_key = {"NBA":"basketball_nba","MLB":"baseball_mlb","NHL":"icehockey_nhl","NFL":"americanfootball_nfl"}.get(sport)
                if not sport_key: continue
                props = self.game_scanner.fetch_player_props_odds(sport_key)
            for prop in props:
                result = self.analyze_prop(prop["player"], prop["market"], prop["line"], prop["pick"], [], sport, prop["odds"], None, "HEALTHY")
                if result.get('units',0)>0:
                    all_bets.append({"player":prop["player"],"market":prop["market"],"line":prop["line"],"pick":prop["pick"],
                                     "odds":prop["odds"],"bookmaker":prop["bookmaker"],"edge":result.get('raw_edge',0),
                                     "probability":result.get('probability',0),"units":result.get('units',0),"sport":sport})
        best_bets = {}
        for bet in all_bets:
            key = f"{bet['player']}|{bet['market']}|{bet['line']}"
            if key not in best_bets or bet['odds'] > best_bets[key]['odds']: best_bets[key] = bet
        sorted_bets = sorted(best_bets.values(), key=lambda x:x['edge'], reverse=True)
        self.scanned_bets["best_odds"] = sorted_bets[:10]
        props_for_arb = [{'player':bet['player'],'market':bet['market'],'line':bet['line'],'pick':bet['pick'],
                          'odds':bet['odds'],'bookmaker':bet['bookmaker']} for bet in all_bets]
        self.scanned_bets["arbs"] = self.detect_arbitrage(props_for_arb)
        self.scanned_bets["middles"] = self.hunt_middles(props_for_arb)
        return sorted_bets[:10]

    def get_accuracy_dashboard(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if df.empty: 
            return {'total_bets':0,'wins':0,'losses':0,'win_rate':0,'roi':0,'units_profit':0,'by_sport':{},'by_tier':{},'sem_score':self.sem_score}
        wins, total = (df['result']=='WIN').sum(), len(df)
        total_stake, total_profit = df['odds'].apply(lambda x:100).sum(), df.apply(lambda r:90.9 if r['result']=='WIN' else -100, axis=1).sum()
        roi = (total_profit/total_stake)*100 if total_stake>0 else 0
        by_sport = {}
        for sport in df['sport'].unique():
            sport_df = df[df['sport']==sport]
            sport_wins = (sport_df['result']=='WIN').sum()
            by_sport[sport] = {'bets':len(sport_df),'win_rate':round(sport_wins/len(sport_df)*100,1) if len(sport_df)>0 else 0}
        by_tier = {}
        for _,row in df.iterrows():
            signal = row.get('bolt_signal','PASS')
            tier = 'SAFE' if 'SAFE' in str(signal) else 'BALANCED+' if 'BALANCED' in str(signal) else 'RISKY' if 'RISKY' in str(signal) else 'PASS'
            if tier not in by_tier: by_tier[tier] = {'bets':0,'wins':0}
            by_tier[tier]['bets'] += 1
            if row['result']=='WIN': by_tier[tier]['wins'] += 1
        for tier in by_tier: by_tier[tier]['win_rate'] = round(by_tier[tier]['wins']/by_tier[tier]['bets']*100,1) if by_tier[tier]['bets']>0 else 0
        return {'total_bets':total,'wins':wins,'losses':total-wins,'win_rate':round(wins/total*100,1) if total>0 else 0,
                'roi':round(roi,1),'units_profit':round(total_profit/100,1),'by_sport':by_sport,'by_tier':by_tier,'sem_score':self.sem_score}

    def detect_arbitrage(self, props):
        arbs = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            if len(bets)<2: continue
            best_over = max([b for b in bets if b['pick']=='OVER'], key=lambda x:x['odds'], default=None)
            best_under = max([b for b in bets if b['pick']=='UNDER'], key=lambda x:x['odds'], default=None)
            if best_over and best_under:
                over_dec, under_dec = self.convert_odds(best_over['odds']), self.convert_odds(best_under['odds'])
                arb_pct = (1/over_dec + 1/under_dec - 1)*100
                if arb_pct>0: arbs.append({'Player':best_over['player'],'Market':best_over['market'],'Line':best_over['line'],
                                           'Bet 1':f"OVER {best_over['odds']} @ {best_over['bookmaker']}",
                                           'Bet 2':f"UNDER {best_under['odds']} @ {best_under['bookmaker']}",'Arb %':round(arb_pct,2)})
        return arbs

    def hunt_middles(self, props):
        middles = []; grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            grouped.setdefault(key, []).append(prop)
        for key,bets in grouped.items():
            overs = [b for b in bets if b['pick']=='OVER']; unders = [b for b in bets if b['pick']=='UNDER']
            for over in overs:
                for under in unders:
                    if over['line'] < under['line'] and under['line']-over['line']>=0.5:
                        middles.append({'Player':over['player'],'Market':over['market'],
                                        'Middle Window':f"{over['line']} – {under['line']}",
                                        'Leg 1':f"OVER {over['line']} ({over['odds']}) @ {over['bookmaker']}",
                                        'Leg 2':f"UNDER {under['line']} ({under['odds']}) @ {under['bookmaker']}",
                                        'Window Size':round(under['line']-over['line'],1)})
        return sorted(middles, key=lambda x:x['Window Size'], reverse=True)

    def _log_bet(self, player, market, line, pick, sport, odds, edge, signal):
        conn = sqlite3.connect(self.db_path); c = conn.cursor()
        bet_id = hashlib.md5(f"{player}{market}{line}{datetime.now()}".encode()).hexdigest()[:12]
        c.execute("INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, date, bolt_signal) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (bet_id, player, sport, market, line, pick, odds, edge, 'PENDING', datetime.now().strftime("%Y-%m-%d"), signal))
        conn.commit(); conn.close()

    def settle_pending_bets(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT id, player, sport, market, line, pick, odds, date FROM bets WHERE result='PENDING'")
        bets = c.fetchall()
        for bet in bets:
            bet_id, player, sport, market, line, pick, odds, date_str = bet
            result, actual = auto_settle_prop(player, market, line, pick, sport, "", date_str)
            if result == "PENDING":
                continue
            if odds > 0:
                profit = (odds / 100) * 100 if result == "WIN" else -100
            else:
                profit = (100 / abs(odds)) * 100 if result == "WIN" else -100
            c.execute("""UPDATE bets SET result=?, actual=?, settled_date=?, profit=?
                         WHERE id=?""",
                      (result, actual, datetime.now().strftime("%Y-%m-%d"), profit, bet_id))
            if result == "LOSS":
                self.daily_loss_today += abs(profit)
        conn.commit()
        conn.close()
        self._calibrate_sem()
        self.auto_tune_thresholds()
        self._auto_retrain_ml()

    def _calibrate_sem(self):
        conn = sqlite3.connect(self.db_path); df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn); conn.close()
        if len(df)>5:
            wins = (df["result"]=="WIN").sum(); accuracy = wins/len(df); adjustment = (accuracy-0.55)*8
            self.sem_score = max(50, min(100, self.sem_score+adjustment))
            
    def auto_tune_thresholds(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT profit FROM bets WHERE result IN ('WIN','LOSS') ORDER BY date DESC LIMIT 50", conn)
        conn.close()
        if len(df) < 50: return
        if self.last_tune_date and (datetime.now() - self.last_tune_date).days < 7: return
        total_profit, total_stake = df["profit"].sum(), 100 * len(df)
        roi = total_profit / total_stake if total_stake>0 else 0
        delta = roi - 0.05
        prob_old, dtm_old = self.prob_bolt, self.dtm_bolt
        self.prob_bolt = max(0.70, min(0.90, self.prob_bolt + delta * 0.5))
        self.dtm_bolt = max(0.10, min(0.25, self.dtm_bolt + delta * 0.25))
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT INTO tuning_log (timestamp, prob_bolt_old, prob_bolt_new, dtm_bolt_old, dtm_bolt_new, roi, bets_used) VALUES (?,?,?,?,?,?,?)",
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), prob_old, self.prob_bolt, dtm_old, self.dtm_bolt, roi, 50))
        conn.commit(); conn.close()
        self.last_tune_date = datetime.now()
        # st.info removed to avoid Streamlit context issues

# =============================================================================
# BACKGROUND AUTOMATION (disabled)
# =============================================================================
class BackgroundAutomation:
    def __init__(self, engine):
        self.engine = engine
        self.running = False
        self.thread = None
    def start(self):
        pass
    def _run(self):
        pass

# =============================================================================
# Now initialize the engine AFTER all class definitions
# =============================================================================
engine = Clarity18Elite()
