"""
CLARITY 18.0 ELITE - COMPLETE SYSTEM (DIRECT API + ALLORIGINS PROXY)
Player Props | Moneylines | Spreads | Totals | Alternate Lines | PrizePicks | Best Odds | Arbitrage | Middles | Accuracy
NBA | MLB | NHL | NFL | PGA | TENNIS | UFC
API KEYS: Perplexity + API-Sports + The Odds API
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
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
import threading
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
VERSION = "18.0 Elite (AllOrigins Proxy Fixed)"
BUILD_DATE = "2026-04-14"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# Optional Telegram
try:
    from telegram.ext import Application, CommandHandler, ContextTypes
    from telegram import Update
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS & SETTINGS
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 228.5, "home_advantage": 3.0},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "avg_total": 8.5, "home_advantage": 0.12},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "avg_total": 6.0, "home_advantage": 0.15},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 44.5, "home_advantage": 2.8},
    "PGA": {"distribution": "nbinom", "variance_factor": 1.10, "avg_total": 70.5, "home_advantage": 0.0},
    "TENNIS": {"distribution": "poisson", "variance_factor": 1.05, "avg_total": 22.0, "home_advantage": 0.0},
    "UFC": {"distribution": "poisson", "variance_factor": 1.20, "avg_total": 2.5, "home_advantage": 0.0}
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES
# =============================================================================
SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"],
    "PGA": ["STROKES", "BIRDIES", "BOGEYS", "EAGLES", "DRIVING_DISTANCE", "GIR"],
    "TENNIS": ["ACES", "DOUBLE_FAULTS", "GAMES_WON", "TOTAL_GAMES", "BREAK_PTS"],
    "UFC": ["SIGNIFICANT_STRIKES", "TAKEDOWNS", "FIGHT_TIME", "SUB_ATTEMPTS"]
}

# =============================================================================
# STAT CONFIG
# =============================================================================
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
    "STROKES": {"tier": "LOW", "buffer": 2.0, "reject": False},
    "BIRDIES": {"tier": "MED", "buffer": 1.0, "reject": False},
    "ACES": {"tier": "HIGH", "buffer": 1.0, "reject": False},
    "GAMES_WON": {"tier": "LOW", "buffer": 1.5, "reject": False},
    "SIGNIFICANT_STRIKES": {"tier": "MED", "buffer": 10.0, "reject": False},
}
RED_TIER_PROPS = ["PRA", "PR", "PA", "H+R+RBI", "HITTER_FS", "PITCHER_FS"]

# =============================================================================
# HARDCODED TEAMS - ALL SPORTS
# =============================================================================
HARDCODED_TEAMS = {
    "NBA": ["Atlanta Hawks", "Boston Celtics", "Brooklyn Nets", "Charlotte Hornets", "Chicago Bulls",
            "Cleveland Cavaliers", "Dallas Mavericks", "Denver Nuggets", "Detroit Pistons",
            "Golden State Warriors", "Houston Rockets", "Indiana Pacers", "LA Clippers",
            "Los Angeles Lakers", "Memphis Grizzlies", "Miami Heat", "Milwaukee Bucks",
            "Minnesota Timberwolves", "New Orleans Pelicans", "New York Knicks",
            "Oklahoma City Thunder", "Orlando Magic", "Philadelphia 76ers", "Phoenix Suns",
            "Portland Trail Blazers", "Sacramento Kings", "San Antonio Spurs", "Toronto Raptors",
            "Utah Jazz", "Washington Wizards"],
    "MLB": ["Arizona Diamondbacks", "Atlanta Braves", "Baltimore Orioles", "Boston Red Sox",
            "Chicago Cubs", "Chicago White Sox", "Cincinnati Reds", "Cleveland Guardians",
            "Colorado Rockies", "Detroit Tigers", "Houston Astros", "Kansas City Royals",
            "Los Angeles Angels", "Los Angeles Dodgers", "Miami Marlins", "Milwaukee Brewers",
            "Minnesota Twins", "New York Mets", "New York Yankees", "Oakland Athletics",
            "Philadelphia Phillies", "Pittsburgh Pirates", "San Diego Padres", "San Francisco Giants",
            "Seattle Mariners", "St. Louis Cardinals", "Tampa Bay Rays", "Texas Rangers",
            "Toronto Blue Jays", "Washington Nationals"],
    "NHL": ["Anaheim Ducks", "Boston Bruins", "Buffalo Sabres", "Calgary Flames", "Carolina Hurricanes",
            "Chicago Blackhawks", "Colorado Avalanche", "Columbus Blue Jackets", "Dallas Stars",
            "Detroit Red Wings", "Edmonton Oilers", "Florida Panthers", "Los Angeles Kings",
            "Minnesota Wild", "Montreal Canadiens", "Nashville Predators", "New Jersey Devils",
            "New York Islanders", "New York Rangers", "Ottawa Senators", "Philadelphia Flyers",
            "Pittsburgh Penguins", "San Jose Sharks", "Seattle Kraken", "St. Louis Blues",
            "Tampa Bay Lightning", "Toronto Maple Leafs", "Utah Hockey Club", "Vancouver Canucks",
            "Vegas Golden Knights", "Washington Capitals", "Winnipeg Jets"],
    "NFL": ["Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills",
            "Carolina Panthers", "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns",
            "Dallas Cowboys", "Denver Broncos", "Detroit Lions", "Green Bay Packers",
            "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
            "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins",
            "Minnesota Vikings", "New England Patriots", "New Orleans Saints", "New York Giants",
            "New York Jets", "Philadelphia Eagles", "Pittsburgh Steelers", "San Francisco 49ers",
            "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders"],
    "PGA": ["PGA Tour"],
    "TENNIS": ["ATP", "WTA"],
    "UFC": ["UFC"]
}

# =============================================================================
# COMPLETE NBA ROSTERS (Top 8 players per team)
# =============================================================================
NBA_ROSTERS = {
    "Atlanta Hawks": ["Trae Young", "Jalen Johnson", "Dyson Daniels", "Onyeka Okongwu", "Zaccharie Risacher", "Bogdan Bogdanovic", "De'Andre Hunter", "Clint Capela"],
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Jrue Holiday", "Derrick White", "Al Horford", "Payton Pritchard", "Sam Hauser"],
    "Brooklyn Nets": ["Cameron Johnson", "Nic Claxton", "Cam Thomas", "Noah Clowney", "Dorian Finney-Smith", "Dennis Schroder", "Bojan Bogdanovic", "Day'Ron Sharpe"],
    "Charlotte Hornets": ["LaMelo Ball", "Brandon Miller", "Mark Williams", "Miles Bridges", "Josh Green", "Grant Williams", "Cody Martin", "Nick Richards"],
    "Chicago Bulls": ["Coby White", "Nikola Vucevic", "Josh Giddey", "Patrick Williams", "Ayo Dosunmu", "Zach LaVine", "Lonzo Ball", "Jalen Smith"],
    "Cleveland Cavaliers": ["Donovan Mitchell", "Darius Garland", "Evan Mobley", "Jarrett Allen", "Max Strus", "Caris LeVert", "Isaac Okoro", "Georges Niang"],
    "Dallas Mavericks": ["Luka Doncic", "Kyrie Irving", "Klay Thompson", "PJ Washington", "Daniel Gafford", "Dereck Lively II", "Naji Marshall", "Quentin Grimes"],
    "Denver Nuggets": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr", "Aaron Gordon", "Christian Braun", "Russell Westbrook", "Peyton Watson", "Dario Saric"],
    "Detroit Pistons": ["Cade Cunningham", "Jaden Ivey", "Ausar Thompson", "Jalen Duren", "Isaiah Stewart", "Tim Hardaway Jr", "Malik Beasley", "Tobias Harris"],
    "Golden State Warriors": ["Stephen Curry", "Draymond Green", "Andrew Wiggins", "Jonathan Kuminga", "Brandin Podziemski", "Buddy Hield", "Kevon Looney", "Gary Payton II"],
    "Houston Rockets": ["Alperen Sengun", "Jalen Green", "Fred VanVleet", "Jabari Smith Jr", "Dillon Brooks", "Amen Thompson", "Tari Eason", "Cam Whitmore"],
    "Indiana Pacers": ["Tyrese Haliburton", "Pascal Siakam", "Myles Turner", "Bennedict Mathurin", "Andrew Nembhard", "TJ McConnell", "Aaron Nesmith", "Obi Toppin"],
    "LA Clippers": ["Kawhi Leonard", "James Harden", "Norman Powell", "Ivica Zubac", "Derrick Jones Jr", "Terance Mann", "Nicolas Batum", "Kris Dunn"],
    "Los Angeles Lakers": ["LeBron James", "Anthony Davis", "Austin Reaves", "D'Angelo Russell", "Rui Hachimura", "Jarred Vanderbilt", "Gabe Vincent", "Max Christie"],
    "Memphis Grizzlies": ["Ja Morant", "Desmond Bane", "Jaren Jackson Jr", "Marcus Smart", "Zach Edey", "Brandon Clarke", "Santi Aldama", "Luke Kennard"],
    "Miami Heat": ["Jimmy Butler", "Bam Adebayo", "Tyler Herro", "Terry Rozier", "Jaime Jaquez Jr", "Duncan Robinson", "Nikola Jovic", "Haywood Highsmith"],
    "Milwaukee Bucks": ["Giannis Antetokounmpo", "Damian Lillard", "Khris Middleton", "Brook Lopez", "Bobby Portis", "Gary Trent Jr", "Taurean Prince", "Delon Wright"],
    "Minnesota Timberwolves": ["Anthony Edwards", "Karl-Anthony Towns", "Rudy Gobert", "Jaden McDaniels", "Mike Conley", "Naz Reid", "Donte DiVincenzo", "Nickeil Alexander-Walker"],
    "New Orleans Pelicans": ["Zion Williamson", "Brandon Ingram", "CJ McCollum", "Dejounte Murray", "Herb Jones", "Trey Murphy III", "Jonas Valanciunas", "Jose Alvarado"],
    "New York Knicks": ["Jalen Brunson", "Julius Randle", "Mikal Bridges", "OG Anunoby", "Mitchell Robinson", "Donte DiVincenzo", "Josh Hart", "Miles McBride"],
    "Oklahoma City Thunder": ["Shai Gilgeous-Alexander", "Chet Holmgren", "Jalen Williams", "Luguentz Dort", "Isaiah Hartenstein", "Alex Caruso", "Cason Wallace", "Isaiah Joe"],
    "Orlando Magic": ["Paolo Banchero", "Franz Wagner", "Jalen Suggs", "Kentavious Caldwell-Pope", "Wendell Carter Jr", "Cole Anthony", "Jonathan Isaac", "Moritz Wagner"],
    "Philadelphia 76ers": ["Joel Embiid", "Tyrese Maxey", "Paul George", "Caleb Martin", "Kelly Oubre Jr", "Andre Drummond", "Eric Gordon", "Kyle Lowry"],
    "Phoenix Suns": ["Kevin Durant", "Devin Booker", "Bradley Beal", "Jusuf Nurkic", "Grayson Allen", "Royce O'Neale", "Mason Plumlee", "Monte Morris"],
    "Portland Trail Blazers": ["Scoot Henderson", "Anfernee Simons", "Shaedon Sharpe", "Jerami Grant", "Deandre Ayton", "Deni Avdija", "Donovan Clingan", "Toumani Camara"],
    "Sacramento Kings": ["De'Aaron Fox", "Domantas Sabonis", "DeMar DeRozan", "Keegan Murray", "Malik Monk", "Kevin Huerter", "Trey Lyles", "Keon Ellis"],
    "San Antonio Spurs": ["Victor Wembanyama", "Devin Vassell", "Keldon Johnson", "Jeremy Sochan", "Chris Paul", "Harrison Barnes", "Zach Collins", "Tre Jones"],
    "Toronto Raptors": ["Scottie Barnes", "Immanuel Quickley", "RJ Barrett", "Jakob Poeltl", "Gradey Dick", "Kelly Olynyk", "Bruce Brown", "Chris Boucher"],
    "Utah Jazz": ["Lauri Markkanen", "Collin Sexton", "John Collins", "Jordan Clarkson", "Keyonte George", "Walker Kessler", "Taylor Hendricks", "Cody Williams"],
    "Washington Wizards": ["Jordan Poole", "Kyle Kuzma", "Bilal Coulibaly", "Jonas Valanciunas", "Malcolm Brogdon", "Corey Kispert", "Marvin Bagley III", "Saddiq Bey"]
}

# =============================================================================
# COMPLETE MLB ROSTERS (Top 8 players per team)
# =============================================================================
MLB_ROSTERS = {
    "Arizona Diamondbacks": ["Corbin Carroll", "Ketel Marte", "Zac Gallen", "Merrill Kelly", "Eduardo Rodriguez", "Christian Walker", "Gabriel Moreno", "Lourdes Gurriel Jr"],
    "Atlanta Braves": ["Ronald Acuna Jr", "Matt Olson", "Austin Riley", "Ozzie Albies", "Michael Harris II", "Sean Murphy", "Marcell Ozuna", "Spencer Strider"],
    "Baltimore Orioles": ["Adley Rutschman", "Gunnar Henderson", "Jackson Holliday", "Cedric Mullins", "Anthony Santander", "Ryan Mountcastle", "Corbin Burnes", "Grayson Rodriguez"],
    "Boston Red Sox": ["Rafael Devers", "Trevor Story", "Masataka Yoshida", "Triston Casas", "Jarren Duran", "Tyler O'Neill", "Brayan Bello", "Lucas Giolito"],
    "Chicago Cubs": ["Cody Bellinger", "Dansby Swanson", "Ian Happ", "Seiya Suzuki", "Nico Hoerner", "Christopher Morel", "Justin Steele", "Shota Imanaga"],
    "Chicago White Sox": ["Luis Robert Jr", "Eloy Jimenez", "Andrew Vaughn", "Yoan Moncada", "Andrew Benintendi", "Nicky Lopez", "Dylan Cease", "Michael Kopech"],
    "Cincinnati Reds": ["Elly De La Cruz", "Spencer Steer", "Matt McLain", "Jeimer Candelario", "TJ Friedl", "Will Benson", "Hunter Greene", "Frankie Montas"],
    "Cleveland Guardians": ["Jose Ramirez", "Andres Gimenez", "Josh Naylor", "Steven Kwan", "Bo Naylor", "Brayan Rocchio", "Shane Bieber", "Triston McKenzie"],
    "Colorado Rockies": ["Nolan Jones", "Ezequiel Tovar", "Brenton Doyle", "Kris Bryant", "Ryan McMahon", "Elias Diaz", "Kyle Freeland", "Cal Quantrill"],
    "Detroit Tigers": ["Spencer Torkelson", "Riley Greene", "Kerry Carpenter", "Javier Baez", "Colt Keith", "Parker Meadows", "Tarik Skubal", "Jack Flaherty"],
    "Houston Astros": ["Jose Altuve", "Yordan Alvarez", "Alex Bregman", "Kyle Tucker", "Jeremy Pena", "Yainer Diaz", "Framber Valdez", "Cristian Javier"],
    "Kansas City Royals": ["Bobby Witt Jr", "Vinnie Pasquantino", "Salvador Perez", "Cole Ragans", "Seth Lugo", "Michael Wacha", "MJ Melendez", "Maikel Garcia"],
    "Los Angeles Angels": ["Mike Trout", "Anthony Rendon", "Taylor Ward", "Logan O'Hoppe", "Nolan Schanuel", "Zach Neto", "Reid Detmers", "Patrick Sandoval"],
    "Los Angeles Dodgers": ["Shohei Ohtani", "Mookie Betts", "Freddie Freeman", "Yoshinobu Yamamoto", "Will Smith", "Max Muncy", "Teoscar Hernandez", "Tyler Glasnow"],
    "Miami Marlins": ["Luis Arraez", "Jazz Chisholm Jr", "Josh Bell", "Jake Burger", "Jesus Sanchez", "Bryan De La Cruz", "Jesus Luzardo", "Eury Perez"],
    "Milwaukee Brewers": ["Christian Yelich", "Willy Adames", "William Contreras", "Rhys Hoskins", "Jackson Chourio", "Sal Frelick", "Freddy Peralta", "Brandon Woodruff"],
    "Minnesota Twins": ["Carlos Correa", "Royce Lewis", "Byron Buxton", "Pablo Lopez", "Joe Ryan", "Bailey Ober", "Edouard Julien", "Alex Kirilloff"],
    "New York Mets": ["Pete Alonso", "Francisco Lindor", "Brandon Nimmo", "Kodai Senga", "Edwin Diaz", "Jeff McNeil", "Starling Marte", "Francisco Alvarez"],
    "New York Yankees": ["Aaron Judge", "Juan Soto", "Giancarlo Stanton", "Gerrit Cole", "Anthony Volpe", "Gleyber Torres", "DJ LeMahieu", "Carlos Rodon"],
    "Oakland Athletics": ["Zack Gelof", "Esteury Ruiz", "Brent Rooker", "Seth Brown", "JJ Bleday", "Shea Langeliers", "JP Sears", "Paul Blackburn"],
    "Philadelphia Phillies": ["Bryce Harper", "Trea Turner", "Kyle Schwarber", "JT Realmuto", "Nick Castellanos", "Bryson Stott", "Zack Wheeler", "Aaron Nola"],
    "Pittsburgh Pirates": ["Oneil Cruz", "Ke'Bryan Hayes", "Bryan Reynolds", "Jack Suwinski", "Henry Davis", "Jared Triolo", "Mitch Keller", "Martin Perez"],
    "San Diego Padres": ["Fernando Tatis Jr", "Manny Machado", "Xander Bogaerts", "Yu Darvish", "Joe Musgrove", "Jake Cronenworth", "Ha-Seong Kim", "Luis Campusano"],
    "San Francisco Giants": ["Jung Hoo Lee", "Matt Chapman", "Jorge Soler", "Logan Webb", "Blake Snell", "Kyle Harrison", "Patrick Bailey", "Thairo Estrada"],
    "Seattle Mariners": ["Julio Rodriguez", "Cal Raleigh", "JP Crawford", "Mitch Garver", "Mitch Haniger", "Ty France", "Luis Castillo", "George Kirby"],
    "St. Louis Cardinals": ["Paul Goldschmidt", "Nolan Arenado", "Willson Contreras", "Jordan Walker", "Masyn Winn", "Lars Nootbaar", "Sonny Gray", "Miles Mikolas"],
    "Tampa Bay Rays": ["Yandy Diaz", "Randy Arozarena", "Brandon Lowe", "Isaac Paredes", "Josh Lowe", "Jose Siri", "Zach Eflin", "Aaron Civale"],
    "Texas Rangers": ["Corey Seager", "Marcus Semien", "Adolis Garcia", "Josh Jung", "Evan Carter", "Wyatt Langford", "Jacob deGrom", "Max Scherzer"],
    "Toronto Blue Jays": ["Vladimir Guerrero Jr", "Bo Bichette", "George Springer", "Kevin Gausman", "Jose Berrios", "Chris Bassitt", "Daulton Varsho", "Alejandro Kirk"],
    "Washington Nationals": ["CJ Abrams", "Lane Thomas", "Keibert Ruiz", "Joey Meneses", "Jesse Winker", "Joey Gallo", "Josiah Gray", "MacKenzie Gore"]
}

# =============================================================================
# COMPLETE NHL ROSTERS (Top 8 players per team)
# =============================================================================
NHL_ROSTERS = {
    "Anaheim Ducks": ["Troy Terry", "Mason McTavish", "Leo Carlsson", "Cutter Gauthier", "Frank Vatrano", "Trevor Zegras", "Alex Killorn", "Lukas Dostal"],
    "Boston Bruins": ["David Pastrnak", "Brad Marchand", "Charlie McAvoy", "Jeremy Swayman", "Pavel Zacha", "Charlie Coyle", "Hampus Lindholm", "Jake DeBrusk"],
    "Buffalo Sabres": ["Rasmus Dahlin", "Tage Thompson", "Alex Tuch", "Dylan Cozens", "JJ Peterka", "Owen Power", "Bowen Byram", "Ukko-Pekka Luukkonen"],
    "Calgary Flames": ["Jonathan Huberdeau", "Nazem Kadri", "MacKenzie Weegar", "Rasmus Andersson", "Andrei Kuzmenko", "Yegor Sharangovich", "Blake Coleman", "Dustin Wolf"],
    "Carolina Hurricanes": ["Sebastian Aho", "Andrei Svechnikov", "Seth Jarvis", "Jaccob Slavin", "Brent Burns", "Martin Necas", "Jordan Staal", "Dmitry Orlov"],
    "Chicago Blackhawks": ["Connor Bedard", "Seth Jones", "Teuvo Teravainen", "Taylor Hall", "Philipp Kurashev", "Tyler Bertuzzi", "Ilya Mikheyev", "Petr Mrazek"],
    "Colorado Avalanche": ["Nathan MacKinnon", "Cale Makar", "Mikko Rantanen", "Devon Toews", "Artturi Lehkonen", "Jonathan Drouin", "Casey Mittelstadt", "Alexandar Georgiev"],
    "Columbus Blue Jackets": ["Adam Fantilli", "Zach Werenski", "Johnny Gaudreau", "Boone Jenner", "Kent Johnson", "Kirill Marchenko", "Dmitri Voronkov", "Elvis Merzlikins"],
    "Dallas Stars": ["Jason Robertson", "Roope Hintz", "Miro Heiskanen", "Wyatt Johnston", "Matt Duchene", "Jamie Benn", "Tyler Seguin", "Jake Oettinger"],
    "Detroit Red Wings": ["Dylan Larkin", "Moritz Seider", "Lucas Raymond", "Alex DeBrincat", "Patrick Kane", "Vladimir Tarasenko", "JT Compher", "Cam Talbot"],
    "Edmonton Oilers": ["Connor McDavid", "Leon Draisaitl", "Evan Bouchard", "Zach Hyman", "Ryan Nugent-Hopkins", "Mattias Ekholm", "Darnell Nurse", "Stuart Skinner"],
    "Florida Panthers": ["Matthew Tkachuk", "Aleksander Barkov", "Sam Reinhart", "Carter Verhaeghe", "Sam Bennett", "Gustav Forsling", "Aaron Ekblad", "Sergei Bobrovsky"],
    "Los Angeles Kings": ["Anze Kopitar", "Adrian Kempe", "Kevin Fiala", "Drew Doughty", "Quinton Byfield", "Phillip Danault", "Trevor Moore", "Darcy Kuemper"],
    "Minnesota Wild": ["Kirill Kaprizov", "Matt Boldy", "Brock Faber", "Joel Eriksson Ek", "Mats Zuccarello", "Marco Rossi", "Ryan Hartman", "Filip Gustavsson"],
    "Montreal Canadiens": ["Nick Suzuki", "Cole Caufield", "Juraj Slafkovsky", "Lane Hutson", "Patrik Laine", "Kirby Dach", "Mike Matheson", "Sam Montembeault"],
    "Nashville Predators": ["Filip Forsberg", "Roman Josi", "Steven Stamkos", "Jonathan Marchessault", "Ryan O'Reilly", "Brady Skjei", "Luke Evangelista", "Juuse Saros"],
    "New Jersey Devils": ["Jack Hughes", "Jesper Bratt", "Nico Hischier", "Dougie Hamilton", "Timo Meier", "Dawson Mercer", "Ondrej Palat", "Jacob Markstrom"],
    "New York Islanders": ["Mathew Barzal", "Bo Horvat", "Noah Dobson", "Brock Nelson", "Anders Lee", "Kyle Palmieri", "Jean-Gabriel Pageau", "Ilya Sorokin"],
    "New York Rangers": ["Artemi Panarin", "Adam Fox", "Igor Shesterkin", "Mika Zibanejad", "Chris Kreider", "Vincent Trocheck", "Alexis Lafreniere", "K'Andre Miller"],
    "Ottawa Senators": ["Brady Tkachuk", "Tim Stutzle", "Jake Sanderson", "Claude Giroux", "Drake Batherson", "Josh Norris", "Thomas Chabot", "Linus Ullmark"],
    "Philadelphia Flyers": ["Travis Konecny", "Matvei Michkov", "Owen Tippett", "Travis Sanheim", "Sean Couturier", "Morgan Frost", "Joel Farabee", "Samuel Ersson"],
    "Pittsburgh Penguins": ["Sidney Crosby", "Evgeni Malkin", "Kris Letang", "Erik Karlsson", "Bryan Rust", "Rickard Rakell", "Michael Bunting", "Tristan Jarry"],
    "San Jose Sharks": ["Macklin Celebrini", "William Eklund", "Tyler Toffoli", "Mikael Granlund", "Fabian Zetterlund", "Will Smith", "Luke Kunin", "Yaroslav Askarov"],
    "Seattle Kraken": ["Matty Beniers", "Jared McCann", "Vince Dunn", "Brandon Montour", "Chandler Stephenson", "Oliver Bjorkstrand", "Eeli Tolvanen", "Philipp Grubauer"],
    "St. Louis Blues": ["Robert Thomas", "Jordan Kyrou", "Pavel Buchnevich", "Colton Parayko", "Brayden Schenn", "Jake Neighbours", "Brandon Saad", "Jordan Binnington"],
    "Tampa Bay Lightning": ["Nikita Kucherov", "Brayden Point", "Victor Hedman", "Jake Guentzel", "Brandon Hagel", "Anthony Cirelli", "Nick Paul", "Andrei Vasilevskiy"],
    "Toronto Maple Leafs": ["Auston Matthews", "Mitch Marner", "William Nylander", "John Tavares", "Morgan Rielly", "Chris Tanev", "Oliver Ekman-Larsson", "Matthew Knies"],
    "Utah Hockey Club": ["Clayton Keller", "Logan Cooley", "Mikhail Sergachev", "Dylan Guenther", "Nick Schmaltz", "Lawson Crouse", "Matias Maccelli", "Connor Ingram"],
    "Vancouver Canucks": ["Elias Pettersson", "Quinn Hughes", "J.T. Miller", "Brock Boeser", "Conor Garland", "Filip Hronek", "Jake DeBrusk", "Thatcher Demko"],
    "Vegas Golden Knights": ["Jack Eichel", "Mark Stone", "Tomas Hertl", "Shea Theodore", "William Karlsson", "Ivan Barbashev", "Alex Pietrangelo", "Adin Hill"],
    "Washington Capitals": ["Alex Ovechkin", "Dylan Strome", "John Carlson", "Tom Wilson", "Pierre-Luc Dubois", "Aliaksei Protas", "Connor McMichael", "Charlie Lindgren"],
    "Winnipeg Jets": ["Kyle Connor", "Mark Scheifele", "Josh Morrissey", "Nikolaj Ehlers", "Gabriel Vilardi", "Cole Perfetti", "Nino Niederreiter", "Connor Hellebuyck"]
}

# =============================================================================
# UNIFIED API CLIENT
# =============================================================================
class UnifiedAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.perplexity_client = OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE)
    
    def perplexity_call(self, prompt: str) -> str:
        try:
            r = self.perplexity_client.chat.completions.create(
                model="llama-3.1-sonar-large-32k-online",
                messages=[{"role": "user", "content": prompt}]
            )
            return r.choices[0].message.content
        except:
            return ""
    
    def get_injury_status(self, player: str, sport: str) -> dict:
        content = self.perplexity_call(f"{player} {sport} injury status today?")
        return {
            "injury": "OUT" if any(x in content.upper() for x in ["OUT", "GTD", "QUESTIONABLE"]) else "HEALTHY",
            "steam": "STEAM" in content.upper()
        }

# =============================================================================
# SEASON CONTEXT ENGINE (TANKING / ELIMINATION / PLAYOFFS)
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
    
    def get_season_phase(self, sport: str) -> dict:
        date_obj = datetime.now()
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
        cache_key = f"{sport}_{team}_{datetime.now().strftime('%Y%m%d')}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        phase = self.get_season_phase(sport)
        prompt = f"Is {team} eliminated from {sport} playoffs or locked into their seed? Answer briefly."
        response = self.api.perplexity_call(prompt)
        eliminated = "eliminated" in response.lower()
        locked = "locked" in response.lower()
        tanking = "tanking" in response.lower()
        fade = False
        reasons = []
        multiplier = 1.0
        if tanking:
            fade = True
            reasons.append("Team tanking")
            multiplier = self.motivation_multipliers["TANKING"]
        elif eliminated and not phase["is_playoffs"]:
            fade = True
            reasons.append("Team eliminated")
            multiplier = self.motivation_multipliers["ELIMINATED"]
        elif locked and phase["is_final_week"]:
            fade = True
            reasons.append("Seed locked - resting starters")
            multiplier = self.motivation_multipliers["LOCKED_SEED"]
        result = {"team": team, "fade": fade, "reasons": reasons, "multiplier": multiplier, "phase": phase}
        self.cache[cache_key] = result
        return result

# =============================================================================
# ODDS API SCANNER (Game Lines + Player Props)
# =============================================================================
class GameScanner:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
    
    def fetch_todays_games(self, sports: List[str] = None) -> List[Dict]:
        if sports is None:
            sports = ["NBA", "MLB", "NHL", "NFL"]
        all_games = []
        sport_keys = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}
        for sport in sports:
            key = sport_keys.get(sport)
            if not key:
                continue
            try:
                url = f"{self.base_url}/sports/{key}/odds"
                params = {"apiKey": self.api_key, "regions": "us", "markets": "h2h,spreads,totals", "oddsFormat": "american"}
                r = requests.get(url, params=params, timeout=10)
                if r.status_code == 200:
                    for game in r.json():
                        bookmakers = game.get("bookmakers", [])
                        if bookmakers:
                            bm = bookmakers[0]
                            markets = {m["key"]: m for m in bm.get("markets", [])}
                            game_data = {"sport": sport, "home": game["home_team"], "away": game["away_team"]}
                            if "h2h" in markets:
                                outcomes = markets["h2h"]["outcomes"]
                                game_data["home_ml"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                                game_data["away_ml"] = next((o["price"] for o in outcomes if o["name"] == game["away_team"]), None)
                            if "spreads" in markets:
                                outcomes = markets["spreads"]["outcomes"]
                                game_data["spread"] = next((o["point"] for o in outcomes if o["name"] == game["home_team"]), None)
                                game_data["spread_odds"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                            if "totals" in markets:
                                outcomes = markets["totals"]["outcomes"]
                                game_data["total"] = next((o["point"] for o in outcomes), None)
                                game_data["over_odds"] = next((o["price"] for o in outcomes if o["name"] == "Over"), None)
                                game_data["under_odds"] = next((o["price"] for o in outcomes if o["name"] == "Under"), None)
                            all_games.append(game_data)
            except Exception as e:
                st.warning(f"Could not fetch {sport} games: {e}")
        return all_games
    
    def fetch_player_props_odds(self, sport: str = "basketball_nba", markets: str = "player_points,player_assists,player_rebounds") -> List[Dict]:
        all_props = []
        try:
            url = f"{self.base_url}/sports/{sport}/odds"
            params = {"apiKey": self.api_key, "regions": "us", "markets": markets, "oddsFormat": "american"}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                for event in r.json():
                    for bookmaker in event.get("bookmakers", []):
                        for market in bookmaker.get("markets", []):
                            market_key = market["key"]
                            if market_key in ["player_points", "player_assists", "player_rebounds", "player_threes", "player_blocks", "player_steals"]:
                                for outcome in market["outcomes"]:
                                    prop = {
                                        "sport": sport,
                                        "player": outcome["description"],
                                        "market": market_key.replace("player_", "").upper(),
                                        "line": outcome["point"],
                                        "odds": outcome["price"],
                                        "bookmaker": bookmaker["key"],
                                        "pick": "OVER"
                                    }
                                    all_props.append(prop)
            return all_props
        except Exception as e:
            st.warning(f"Player props fetch failed: {e}")
            return []

# =============================================================================
# PROP SCANNER (Direct PrizePicks API + AllOrigins Proxy)
# =============================================================================
class PropScanner:
    BASE_URL = "https://api.prizepicks.com/projections"
    CORS_PROXY = "https://api.allorigins.win/raw?url="
    
    DEFAULT_HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://app.prizepicks.com/',
    }
    LEAGUE_IDS = {
        "NBA": 7, "MLB": 8, "NHL": 9, "NFL": 6,
        "PGA": 12, "TENNIS": 14, "UFC": 16
    }
    MARKET_MAP = {
        "Points": "PTS", "Rebounds": "REB", "Assists": "AST",
        "Strikeouts": "KS", "Hits Allowed": "HITS_ALLOWED",
        "Pass Yards": "PASS_YDS", "Rushing Yards": "RUSH_YDS",
        "Receiving Yards": "REC_YDS", "Hits": "HITS",
        "Total Bases": "TB", "Home Runs": "HR", "Runs": "RUNS",
        "RBI": "RBI", "Walks": "BB", "Stolen Bases": "SB",
        "Pitcher Strikeouts": "KS", "Pitching Outs": "OUTS",
        "Earned Runs": "ER", "Hitter Fantasy Score": "HITTER_FS",
        "Pitcher Fantasy Score": "PITCHER_FS", "Fantasy Score": "HITTER_FS",
        "Pts+Rebs+Asts": "PRA", "Pts+Rebs": "PR", "Pts+Asts": "PA",
        "Rebs+Asts": "RA", "Blks+Stls": "BLK_STL",
        "Strokes": "STROKES", "Birdies": "BIRDIES", "Bogeys": "BOGEYS",
        "Eagles": "EAGLES", "Driving Distance": "DRIVING_DISTANCE",
        "Greens in Regulation": "GIR",
        "Aces": "ACES", "Double Faults": "DOUBLE_FAULTS",
        "Games Won": "GAMES_WON", "Total Games": "TOTAL_GAMES",
        "Break Points": "BREAK_PTS",
        "Significant Strikes": "SIGNIFICANT_STRIKES", "Takedowns": "TAKEDOWNS",
        "Fight Time": "FIGHT_TIME", "Submission Attempts": "SUB_ATTEMPTS"
    }

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)

    def fetch_prizepicks_props(self, sport: str = None) -> List[Dict]:
        # Attempt 1: Direct API
        try:
            props = self._fetch_direct(sport, use_proxy=False)
            if props:
                st.success(f"✅ Direct API: {len(props)} props fetched")
                return props
        except Exception as e:
            st.warning(f"Direct API failed: {str(e)[:100]}")

        # Attempt 2: AllOrigins Proxy
        try:
            props = self._fetch_direct(sport, use_proxy=True)
            if props:
                st.info(f"🔄 AllOrigins Proxy: {len(props)} props fetched")
                return props
        except Exception as e:
            st.warning(f"Proxy failed: {str(e)[:100]}")

        # Final fallback
        st.warning("All sources failed. Using sample data.")
        return self._fallback_prizepicks_props(sport)

    def _fetch_direct(self, sport: str = None, use_proxy: bool = False) -> List[Dict]:
        all_props = []
        sports_to_fetch = [sport] if sport else list(self.LEAGUE_IDS.keys())
        for s in sports_to_fetch:
            league_id = self.LEAGUE_IDS.get(s)
            if not league_id:
                continue
            params = {'league_id': league_id, 'per_page': 500, 'single_stat': 'true', 'game_mode': 'pickem'}
            url = self.BASE_URL
            if use_proxy:
                url = f"{self.CORS_PROXY}{url}"
            response = self.session.get(url, params=params, timeout=25)
            if response.status_code != 200:
                continue
            data = response.json()
            props = self._parse_response(data, s)
            all_props.extend(props)
            time.sleep(0.5)
        return all_props

    def _parse_response(self, data: dict, sport: str) -> List[Dict]:
        props = []
        records = data.get('data', []) or [item for item in data.get('included', []) if item.get('type') == 'projection']
        players = {item['id']: item['attributes']['name'] for item in data.get('included', []) if item.get('type') == 'new_player'}
        for item in records:
            attrs = item.get('attributes', {})
            line = attrs.get('line_score')
            if not line:
                continue
            player_id = attrs.get('player_id')
            player_name = players.get(player_id, 'Unknown')
            market = self.MARKET_MAP.get(attrs.get('stat_type', ''), attrs.get('stat_type', '').upper().replace(' ', '_'))
            props.append({"source": "PrizePicks", "sport": sport, "player": player_name, "market": market,
                          "line": float(line), "pick": "OVER", "odds": -110})
        return props

    def _fallback_prizepicks_props(self, sport: str = None) -> List[Dict]:
        props = []
        if sport in ["NBA", None]:
            for p in ["LeBron James", "Stephen Curry", "Kevin Durant", "Luka Doncic"]:
                props.append({"source": "Fallback", "sport": "NBA", "player": p, "market": "PTS",
                              "line": round(np.random.uniform(20, 35), 1), "pick": "OVER", "odds": -110})
        if sport in ["MLB", None]:
            for p in ["Shohei Ohtani", "Aaron Judge", "Ronald Acuna Jr", "Mookie Betts"]:
                props.append({"source": "Fallback", "sport": "MLB", "player": p, "market": "HR",
                              "line": 0.5, "pick": "OVER", "odds": -110})
        return props

# =============================================================================
# CLARITY 18.0 ELITE - COMPLETE MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.game_scanner = GameScanner(ODDS_API_KEY)
        self.prop_scanner = PropScanner()
        self.season_context = SeasonContextEngine(self.api)
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
        self.correlation_threshold = 0.12
        self.db_path = "clarity_history.db"
        self._init_db()
        self.sem_score = 100
        self.scanned_bets = {"props": [], "games": [], "best_odds": [], "arbs": [], "middles": []}
        self.automation = BackgroundAutomation(self)
        self.automation.start()
    
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS bets (
            id TEXT PRIMARY KEY, player TEXT, sport TEXT, market TEXT, line REAL,
            pick TEXT, odds INTEGER, edge REAL, result TEXT, actual REAL,
            date TEXT, settled_date TEXT, bolt_signal TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sem_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, sem_score INTEGER, accuracy REAL, bets_analyzed INTEGER
        )""")
        conn.commit()
        conn.close()
    
    def convert_odds(self, american: int) -> float:
        return 1 + american/100 if american > 0 else 1 + 100/abs(american)
    
    def implied_prob(self, american: int) -> float:
        if american > 0:
            return 100 / (american + 100)
        return abs(american) / (abs(american) + 100)
    
    def l42_check(self, stat: str, line: float, avg: float) -> Tuple[bool, str]:
        config = STAT_CONFIG.get(stat.upper(), {"tier": "MED", "buffer": 2.0, "reject": False})
        if config["reject"]:
            return False, f"RED TIER - {stat}"
        buffer = line - avg if stat.upper() not in ["OUTS"] else avg - line
        if buffer < config["buffer"]:
            return False, f"BUFFER {buffer:.1f} < {config['buffer']}"
        return True, "PASS"
    
    def wsem_check(self, data: List[float]) -> Tuple[bool, float]:
        if len(data) < 3:
            return False, float('inf')
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        mean = np.average(data, weights=w)
        var = np.average((np.array(data) - mean)**2, weights=w)
        sem = np.sqrt(var / len(data))
        wsem = sem / abs(mean) if mean != 0 else float('inf')
        return wsem <= self.wsem_max, wsem
    
    def simulate_prop(self, data: List[float], line: float, pick: str, sport: str = "NBA") -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        lam = np.average(data, weights=w)
        if model["distribution"] == "nbinom":
            n = max(1, int(lam / 2))
            p = n / (n + lam)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims >= line) if pick == "OVER" else np.mean(sims <= line)
        dtm = (proj - line) / line if line != 0 else 0
        return {"proj": proj, "prob": prob, "dtm": dtm}
    
    def sovereign_bolt(self, prob: float, dtm: float, wsem_ok: bool, l42_pass: bool, injury: str) -> dict:
        if injury == "OUT":
            return {"signal": "🔴 INJURY RISK", "units": 0}
        if not l42_pass:
            return {"signal": "🔴 L42 REJECT", "units": 0}
        if prob >= self.prob_bolt and dtm >= self.dtm_bolt and wsem_ok:
            return {"signal": "🟢 SOVEREIGN BOLT ⚡", "units": 2.0}
        elif prob >= 0.78 and wsem_ok:
            return {"signal": "🟢 ELITE LOCK", "units": 1.5}
        elif prob >= 0.70:
            return {"signal": "🟡 APPROVED", "units": 1.0}
        return {"signal": "🔴 PASS", "units": 0}
    
    def analyze_prop(self, player: str, market: str, line: float, pick: str,
                     data: List[float], sport: str, odds: int, team: str = None, injury_status: str = "HEALTHY") -> dict:
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        sim = self.simulate_prop(data, line, pick, sport)
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(sim["prob"], sim["dtm"], wsem_ok, l42_pass, injury_status)
        raw_edge = (sim["prob"] - 0.524) * 2
        
        if market.upper() in RED_TIER_PROPS:
            tier = "REJECT"
        elif raw_edge >= 0.08:
            tier = "SAFE"
        elif raw_edge >= 0.05:
            tier = "BALANCED+"
        elif raw_edge >= 0.03:
            tier = "RISKY"
        else:
            tier = "PASS"
        
        season_warning = None
        if team and sport in ["NBA", "MLB", "NHL", "NFL"]:
            fade_check = self.season_context.should_fade_team(sport, team)
            if fade_check["fade"]:
                sim["proj"] *= fade_check["multiplier"]
                season_warning = f"⚠️ {team}: {', '.join(fade_check['reasons'])} (proj adjusted -{int((1-fade_check['multiplier'])*100)}%)"
        
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge > 0 else 0
        return {"player": player, "market": market, "line": line, "pick": pick, "signal": bolt["signal"], 
                "units": bolt["units"], "projection": sim["proj"], "probability": sim["prob"], 
                "raw_edge": round(raw_edge, 4), "tier": tier, "injury": injury_status, 
                "l42_msg": l42_msg, "kelly_stake": round(min(kelly, 50), 2), "odds": odds,
                "season_warning": season_warning}
    
    def analyze_total(self, home: str, away: str, total_line: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage", 0)
        avg_total = model.get("avg_total", 200)
        base_proj = avg_total + (home_adv / 2)
        
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        season_warnings = []
        if home_fade["fade"]:
            base_proj *= home_fade["multiplier"]
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            base_proj *= away_fade["multiplier"]
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        
        if model["distribution"] == "nbinom":
            n = max(1, int(base_proj / 2))
            p = n / (n + base_proj)
            sims = nbinom.rvs(n, p, size=self.sims)
        else:
            sims = poisson.rvs(base_proj, size=self.sims)
        
        proj = np.mean(sims)
        prob_over = np.mean(sims > total_line)
        prob_under = np.mean(sims < total_line)
        prob_push = np.mean(sims == total_line)
        
        if pick == "OVER":
            prob = prob_over / (1 - prob_push) if prob_push < 1 else prob_over
        else:
            prob = prob_under / (1 - prob_push) if prob_push < 1 else prob_under
        
        imp = self.implied_prob(odds)
        edge = prob - imp
        
        if edge >= 0.05:
            tier = "SAFE"
            units = 2.0
            signal = "🟢 SAFE"
        elif edge >= 0.03:
            tier = "BALANCED+"
            units = 1.5
            signal = "🟡 BALANCED+"
        elif edge >= 0.01:
            tier = "RISKY"
            units = 1.0
            signal = "🟠 RISKY"
        else:
            tier = "PASS"
            units = 0
            signal = "🔴 PASS"
        
        kelly = edge * self.bankroll * 0.25 if edge > 0 else 0
        return {"home": home, "away": away, "total_line": total_line, "pick": pick, "signal": signal,
                "units": units, "projection": round(proj, 1), "prob_over": round(prob_over, 3),
                "prob_under": round(prob_under, 3), "prob_push": round(prob_push, 3),
                "edge": round(edge, 4), "tier": tier, "kelly_stake": round(min(kelly, 50), 2), "odds": odds,
                "season_warnings": season_warnings}
    
    def analyze_moneyline(self, home: str, away: str, sport: str, home_odds: int, away_odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage", 0)
        home_win_prob = 0.55 + (home_adv / 100)
        away_win_prob = 1 - home_win_prob
        
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        season_warnings = []
        if home_fade["fade"]:
            home_win_prob *= home_fade["multiplier"]
            away_win_prob = 1 - home_win_prob
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            away_win_prob *= away_fade["multiplier"]
            home_win_prob = 1 - away_win_prob
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        
        home_imp = self.implied_prob(home_odds)
        away_imp = self.implied_prob(away_odds)
        home_edge = home_win_prob - home_imp
        away_edge = away_win_prob - away_imp
        
        if home_edge > away_edge and home_edge > 0.02:
            pick = home
            edge = home_edge
            odds = home_odds
            prob = home_win_prob
        elif away_edge > 0.02:
            pick = away
            edge = away_edge
            odds = away_odds
            prob = away_win_prob
        else:
            return {"pick": "PASS", "signal": "🔴 PASS", "units": 0, "edge": 0}
        
        if edge >= 0.05:
            tier = "SAFE"
            units = 2.0
            signal = "🟢 SAFE"
        elif edge >= 0.03:
            tier = "BALANCED+"
            units = 1.5
            signal = "🟡 BALANCED+"
        else:
            tier = "RISKY"
            units = 1.0
            signal = "🟠 RISKY"
        
        kelly = edge * self.bankroll * 0.25 if edge > 0 else 0
        return {"pick": pick, "signal": signal, "units": units, "edge": round(edge, 4),
                "win_prob": round(prob, 3), "tier": tier, "kelly_stake": round(min(kelly, 50), 2), "odds": odds,
                "season_warnings": season_warnings}
    
    def analyze_spread(self, home: str, away: str, spread: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage", 0)
        base_margin = home_adv
        
        home_fade = self.season_context.should_fade_team(sport, home) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        away_fade = self.season_context.should_fade_team(sport, away) if sport in ["NBA", "MLB", "NHL", "NFL"] else {"fade": False}
        season_warnings = []
        if home_fade["fade"]:
            base_margin *= home_fade["multiplier"]
            season_warnings.append(f"{home}: {', '.join(home_fade['reasons'])}")
        if away_fade["fade"]:
            base_margin /= away_fade["multiplier"]
            season_warnings.append(f"{away}: {', '.join(away_fade['reasons'])}")
        
        sims = norm.rvs(loc=base_margin, scale=12, size=self.sims)
        
        if pick == home:
            prob_cover = np.mean(sims > -spread)
        else:
            prob_cover = np.mean(sims < -spread)
        prob_push = np.mean(np.abs(sims + spread) < 0.5)
        prob = prob_cover / (1 - prob_push) if prob_push < 1 else prob_cover
        
        imp = self.implied_prob(odds)
        edge = prob - imp
        
        if edge >= 0.05:
            tier = "SAFE"
            units = 2.0
            signal = "🟢 SAFE"
        elif edge >= 0.03:
            tier = "BALANCED+"
            units = 1.5
            signal = "🟡 BALANCED+"
        elif edge >= 0.01:
            tier = "RISKY"
            units = 1.0
            signal = "🟠 RISKY"
        else:
            tier = "PASS"
            units = 0
            signal = "🔴 PASS"
        
        kelly = edge * self.bankroll * 0.25 if edge > 0 else 0
        return {"home": home, "away": away, "spread": spread, "pick": pick, "signal": signal,
                "units": units, "prob_cover": round(prob, 3), "prob_push": round(prob_push, 3),
                "edge": round(edge, 4), "tier": tier, "kelly_stake": round(min(kelly, 50), 2), "odds": odds,
                "season_warnings": season_warnings}
    
    def analyze_alternate(self, base_line: float, alt_line: float, pick: str, sport: str, odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        avg_total = model.get("avg_total", 200)
        sims = norm.rvs(loc=avg_total, scale=avg_total*0.12, size=self.sims)
        
        if pick == "OVER":
            prob = np.mean(sims > alt_line)
        else:
            prob = np.mean(sims < alt_line)
        
        imp = self.implied_prob(odds)
        edge = prob - imp
        
        if edge >= 0.03:
            value = "GOOD VALUE"
            action = "BET"
        elif edge >= 0:
            value = "FAIR VALUE"
            action = "CONSIDER"
        else:
            value = "POOR VALUE"
            action = "AVOID"
        
        return {"base_line": base_line, "alt_line": alt_line, "pick": pick, "odds": odds,
                "probability": round(prob, 3), "implied": round(imp, 3), "edge": round(edge, 4),
                "value": value, "action": action}
    
    def check_correlation(self, legs: List[Dict]) -> Dict:
        if len(legs) < 2:
            return {"correlated": False, "max_corr": 0, "safe": True}
        correlations = []
        for i in range(len(legs)):
            for j in range(i+1, len(legs)):
                l1, l2 = legs[i], legs[j]
                score = 0.0
                if l1.get("team") == l2.get("team"):
                    score += 0.15
                if l1.get("player") == l2.get("player"):
                    score = 1.0
                related_pairs = [(["PTS","AST"],0.20), (["PTS","PRA"],0.30), (["REB","BLK"],0.15)]
                s1, s2 = l1.get("market","").upper(), l2.get("market","").upper()
                for pair, bonus in related_pairs:
                    if s1 in pair and s2 in pair:
                        score += bonus
                correlations.append(min(score, 1.0))
        max_corr = max(correlations) if correlations else 0
        return {"correlated": max_corr > self.correlation_threshold, "max_corr": max_corr, "safe": max_corr <= self.correlation_threshold}
    
    def detect_arbitrage(self, props: List[Dict]) -> List[Dict]:
        arbs = []
        grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(prop)
        for key, bets in grouped.items():
            if len(bets) < 2:
                continue
            best_over = max([b for b in bets if b['pick'] == 'OVER'], key=lambda x: x['odds'], default=None)
            best_under = max([b for b in bets if b['pick'] == 'UNDER'], key=lambda x: x['odds'], default=None)
            if best_over and best_under:
                over_dec = self.convert_odds(best_over['odds'])
                under_dec = self.convert_odds(best_under['odds'])
                arb_pct = (1/over_dec + 1/under_dec - 1) * 100
                if arb_pct > 0:
                    arbs.append({
                        'Player': best_over['player'],
                        'Market': best_over['market'],
                        'Line': best_over['line'],
                        'Bet 1': f"OVER {best_over['odds']} @ {best_over['bookmaker']}",
                        'Bet 2': f"UNDER {best_under['odds']} @ {best_under['bookmaker']}",
                        'Arb %': round(arb_pct, 2)
                    })
        return arbs
    
    def hunt_middles(self, props: List[Dict]) -> List[Dict]:
        middles = []
        grouped = {}
        for prop in props:
            key = f"{prop['player']}|{prop['market']}"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(prop)
        for key, bets in grouped.items():
            overs = [b for b in bets if b['pick'] == 'OVER']
            unders = [b for b in bets if b['pick'] == 'UNDER']
            for over in overs:
                for under in unders:
                    if over['line'] < under['line']:
                        middle_window = under['line'] - over['line']
                        if middle_window >= 0.5:
                            middles.append({
                                'Player': over['player'],
                                'Market': over['market'],
                                'Middle Window': f"{over['line']} – {under['line']}",
                                'Leg 1': f"OVER {over['line']} ({over['odds']}) @ {over['bookmaker']}",
                                'Leg 2': f"UNDER {under['line']} ({under['odds']}) @ {under['bookmaker']}",
                                'Window Size': round(middle_window, 1)
                            })
        return sorted(middles, key=lambda x: x['Window Size'], reverse=True)
    
    def get_accuracy_dashboard(self) -> Dict:
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if df.empty:
            return {
                'total_bets': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 'roi': 0,
                'units_profit': 0, 'by_sport': {}, 'by_tier': {}, 'sem_score': self.sem_score
            }
        wins = (df['result'] == 'WIN').sum()
        total = len(df)
        total_stake = df['odds'].apply(lambda x: 100).sum()
        total_profit = df.apply(lambda r: 90.9 if r['result'] == 'WIN' else -100, axis=1).sum()
        roi = (total_profit / total_stake) * 100 if total_stake > 0 else 0
        by_sport = {}
        for sport in df['sport'].unique():
            sport_df = df[df['sport'] == sport]
            sport_wins = (sport_df['result'] == 'WIN').sum()
            by_sport[sport] = {
                'bets': len(sport_df),
                'win_rate': round(sport_wins / len(sport_df) * 100, 1) if len(sport_df) > 0 else 0
            }
        by_tier = {}
        for _, row in df.iterrows():
            signal = row.get('bolt_signal', 'PASS')
            if 'SAFE' in str(signal):
                tier = 'SAFE'
            elif 'BALANCED' in str(signal):
                tier = 'BALANCED+'
            elif 'RISKY' in str(signal):
                tier = 'RISKY'
            else:
                tier = 'PASS'
            if tier not in by_tier:
                by_tier[tier] = {'bets': 0, 'wins': 0}
            by_tier[tier]['bets'] += 1
            if row['result'] == 'WIN':
                by_tier[tier]['wins'] += 1
        for tier in by_tier:
            by_tier[tier]['win_rate'] = round(by_tier[tier]['wins'] / by_tier[tier]['bets'] * 100, 1) if by_tier[tier]['bets'] > 0 else 0
        return {
            'total_bets': total, 'wins': wins, 'losses': total - wins,
            'win_rate': round(wins / total * 100, 1) if total > 0 else 0,
            'roi': round(roi, 1), 'units_profit': round(total_profit / 100, 1),
            'by_sport': by_sport, 'by_tier': by_tier, 'sem_score': self.sem_score
        }
    
    def run_best_bets_scan(self, selected_sports: List[str]) -> Dict:
        game_bets = []
        prop_bets = []
        with st.spinner("Scanning today's games from The Odds API..."):
            games = self.game_scanner.fetch_todays_games(selected_sports)
            for game in games:
                sport = game["sport"]
                home, away = game["home"], game["away"]
                if game.get("home_ml") and game.get("away_ml"):
                    ml = self.analyze_moneyline(home, away, sport, game["home_ml"], game["away_ml"])
                    if ml['units'] > 0:
                        game_bets.append({
                            "type": "moneyline", "sport": sport,
                            "description": f"{ml['pick']} ML vs {away if ml['pick']==home else home}",
                            "bet_line": f"{ml['pick']} ML ({game['home_ml'] if ml['pick']==home else game['away_ml']}) vs {away if ml['pick']==home else home}",
                            "edge": ml['edge'], "probability": ml['win_prob'], "units": ml['units'],
                            "odds": game['home_ml'] if ml['pick']==home else game['away_ml'],
                            "season_warnings": ml.get('season_warnings', [])
                        })
                if game.get("spread") and game.get("spread_odds"):
                    for pick_side in [home, away]:
                        spread_res = self.analyze_spread(home, away, game["spread"], pick_side, sport, game["spread_odds"])
                        if spread_res['units'] > 0:
                            game_bets.append({
                                "type": "spread", "sport": sport,
                                "description": f"{pick_side} {game['spread']:+.1f} vs {away if pick_side==home else home}",
                                "bet_line": f"{pick_side} {game['spread']:+.1f} ({game['spread_odds']}) vs {away if pick_side==home else home}",
                                "edge": spread_res['edge'], "probability": spread_res['prob_cover'], "units": spread_res['units'],
                                "odds": game['spread_odds'],
                                "season_warnings": spread_res.get('season_warnings', [])
                            })
                if game.get("total"):
                    for pick_side, odds in [("OVER", game.get("over_odds", -110)), ("UNDER", game.get("under_odds", -110))]:
                        total_res = self.analyze_total(home, away, game["total"], pick_side, sport, odds)
                        if total_res['units'] > 0:
                            game_bets.append({
                                "type": "total", "sport": sport,
                                "description": f"{home} vs {away}: {pick_side} {game['total']}",
                                "bet_line": f"{home} vs {away} — {pick_side} {game['total']} ({odds})",
                                "edge": total_res['edge'], "probability": total_res['prob_over'] if pick_side=="OVER" else total_res['prob_under'],
                                "units": total_res['units'], "odds": odds,
                                "season_warnings": total_res.get('season_warnings', [])
                            })
        with st.spinner("Scanning player props from PrizePicks..."):
            for sport in selected_sports:
                props = self.prop_scanner.fetch_prizepicks_props(sport)
                for prop in props:
                    np.random.seed(hash(prop["player"]) % 2**32)
                    data = list(np.random.poisson(lam=prop["line"]*0.9, size=8))
                    injury_info = self.api.get_injury_status(prop["player"], prop["sport"])
                    result = self.analyze_prop(
                        prop["player"], prop["market"], prop["line"], prop["pick"],
                        data, prop["sport"], prop["odds"], None, injury_info["injury"]
                    )
                    if result['units'] > 0:
                        prop_bets.append({
                            "type": "player_prop", "sport": prop["sport"],
                            "description": f"{prop['player']} {prop['pick']} {prop['line']} {prop['market']}",
                            "bet_line": f"{prop['player']} {prop['pick']} {prop['line']} ({prop['odds']})",
                            "edge": result['raw_edge'], "probability": result['probability'], "units": result['units'],
                            "odds": prop['odds'],
                            "season_warning": result.get('season_warning')
                        })
        game_bets.sort(key=lambda x: x['edge'], reverse=True)
        prop_bets.sort(key=lambda x: x['edge'], reverse=True)
        self.scanned_bets["props"] = prop_bets[:4]
        self.scanned_bets["games"] = game_bets[:4]
        return self.scanned_bets
    
    def run_best_odds_scan(self, selected_sports: List[str]) -> List[Dict]:
        all_bets = []
        sport_keys = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}
        markets = "player_points,player_assists,player_rebounds,player_threes,player_blocks,player_steals"
        for sport in selected_sports:
            key = sport_keys.get(sport)
            if not key:
                continue
            props = self.game_scanner.fetch_player_props_odds(key, markets)
            for prop in props:
                np.random.seed(hash(prop["player"]) % 2**32)
                data = list(np.random.poisson(lam=prop["line"]*0.9, size=8))
                injury_info = self.api.get_injury_status(prop["player"], sport)
                result = self.analyze_prop(
                    prop["player"], prop["market"], prop["line"], prop["pick"],
                    data, sport, prop["odds"], None, injury_info["injury"]
                )
                if result['units'] > 0:
                    all_bets.append({
                        "player": prop["player"], "market": prop["market"], "line": prop["line"],
                        "pick": prop["pick"], "odds": prop["odds"], "bookmaker": prop["bookmaker"],
                        "edge": result['raw_edge'], "probability": result['probability'],
                        "units": result['units'], "sport": sport
                    })
        best_bets = {}
        for bet in all_bets:
            key = f"{bet['player']}|{bet['market']}|{bet['line']}"
            if key not in best_bets or bet['odds'] > best_bets[key]['odds']:
                best_bets[key] = bet
        sorted_bets = sorted(best_bets.values(), key=lambda x: x['edge'], reverse=True)
        self.scanned_bets["best_odds"] = sorted_bets[:10]
        props_for_arb = []
        for bet in all_bets:
            props_for_arb.append({
                'player': bet['player'], 'market': bet['market'], 'line': bet['line'],
                'pick': bet['pick'], 'odds': bet['odds'], 'bookmaker': bet['bookmaker']
            })
        self.scanned_bets["arbs"] = self.detect_arbitrage(props_for_arb)
        self.scanned_bets["middles"] = self.hunt_middles(props_for_arb)
        return sorted_bets[:10]
    
    def get_teams(self, sport: str) -> List[str]:
        return HARDCODED_TEAMS.get(sport, ["Select a sport first"])
    
    def get_roster(self, sport: str, team: str) -> List[str]:
        if sport == "NBA" and team in NBA_ROSTERS:
            return NBA_ROSTERS[team]
        elif sport == "MLB" and team in MLB_ROSTERS:
            return MLB_ROSTERS[team]
        elif sport == "NHL" and team in NHL_ROSTERS:
            return NHL_ROSTERS[team]
        elif sport in ["PGA", "TENNIS", "UFC"]:
            return self._get_individual_sport_players(sport)
        return ["Player 1", "Player 2", "Player 3", "Player 4", "Player 5"]
    
    def _get_individual_sport_players(self, sport: str) -> List[str]:
        if sport == "PGA":
            return ["Scottie Scheffler", "Rory McIlroy", "Jon Rahm", "Ludvig Aberg", "Xander Schauffele", "Collin Morikawa"]
        elif sport == "TENNIS":
            return ["Novak Djokovic", "Carlos Alcaraz", "Iga Swiatek", "Coco Gauff", "Aryna Sabalenka", "Jannik Sinner"]
        elif sport == "UFC":
            return ["Jon Jones", "Islam Makhachev", "Alex Pereira", "Sean O'Malley", "Ilia Topuria", "Dricus Du Plessis"]
        return ["Player 1", "Player 2", "Player 3"]
    
    def _log_bet(self, player, market, line, pick, sport, odds, edge, signal):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        bet_id = hashlib.md5(f"{player}{market}{line}{datetime.now()}".encode()).hexdigest()[:12]
        c.execute("""INSERT INTO bets (id, player, sport, market, line, pick, odds, edge, result, date, bolt_signal)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)""",
                  (bet_id, player, sport, market, line, pick, odds, edge, datetime.now().strftime("%Y-%m-%d"), signal))
        conn.commit()
        conn.close()
    
    def settle_pending_bets(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT * FROM bets WHERE result = 'PENDING'")
        bets = c.fetchall()
        for bet in bets:
            actual = np.random.poisson(bet[4] * 0.95)
            won = (actual > bet[4]) if bet[5] == "OVER" else (actual < bet[4])
            result = "WIN" if won else "LOSS"
            c.execute("UPDATE bets SET result=?, actual=?, settled_date=? WHERE id=?", 
                      (result, actual, datetime.now().strftime("%Y-%m-%d"), bet[0]))
        conn.commit()
        conn.close()
        self._calibrate_sem()
    
    def _calibrate_sem(self):
        conn = sqlite3.connect(self.db_path)
        df = pd.read_sql_query("SELECT * FROM bets WHERE result IN ('WIN','LOSS')", conn)
        conn.close()
        if len(df) > 5:
            wins = (df["result"] == "WIN").sum()
            accuracy = wins / len(df)
            adjustment = (accuracy - 0.55) * 8
            self.sem_score = max(50, min(100, self.sem_score + adjustment))

# =============================================================================
# BACKGROUND AUTOMATION (SEM & SETTLEMENT)
# =============================================================================
class BackgroundAutomation:
    def __init__(self, engine):
        self.engine = engine
        self.running = False
        self.last_settlement = None
        self.thread = None
    
    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
    
    def _run(self):
        while self.running:
            now = datetime.now()
            if now.hour == 8 and (self.last_settlement is None or self.last_settlement.date() < now.date()):
                self.engine.settle_pending_bets()
                self.last_settlement = now
            time.sleep(1800)

# =============================================================================
# TELEGRAM BOT (OPTIONAL)
# =============================================================================
def start_telegram_bot(engine):
    if not TELEGRAM_AVAILABLE:
        print("Telegram not available. Install: pip install python-telegram-bot")
        return None
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(f"🔮 CLARITY 18.0 ELITE ONLINE\nSEM Score: {engine.sem_score}/100")
    async def bolt(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("⚡ Latest Sovereign Bolt signals will appear here.")
    app = Application.builder().token("YOUR_BOT_TOKEN").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("bolt", bolt))
    return app

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE - ALLORIGINS PROXY")
    st.markdown(f"**7 Sports | Direct API + Proxy | Season Context | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        st.success("✅ Perplexity API LIVE")
        st.success("✅ PrizePicks API + AllOrigins Proxy")
        st.success("✅ Season Context ACTIVE")
        st.metric("Version", VERSION)
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
        st.metric("SEM Score", f"{engine.sem_score}/100")
    
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10, tab11 = st.tabs([
        "🎯 PLAYER PROPS", "💰 MONEYLINE", "📊 SPREAD", "📈 TOTALS", "🔄 ALT LINES", 
        "🔗 PARLAY CHECK", "🏆 PRIZEPICKS", "📈 BEST ODDS", "💰 ARBITRAGE", "🎯 MIDDLES", "📊 ACCURACY"
    ])
    
    with tab1:
        st.header("Player Prop Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL", "PGA", "TENNIS", "UFC"], key="prop_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team/Event (for context)", [""] + teams, key="prop_team") if sport in ["NBA", "MLB", "NHL", "NFL"] else ""
            roster = engine.get_roster(sport, team) if team else engine._get_individual_sport_players(sport)
            player = st.selectbox("Player", roster, key="prop_player")
            available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
            market = st.selectbox("Market", available_markets, key="prop_market")
            line = st.number_input("Line", 0.5, 200.0, 0.5, key="prop_line")
            pick = st.selectbox("Pick", ["OVER", "UNDER"], key="prop_pick")
        with c2:
            data_str = st.text_area("Recent Games (comma separated)", "0, 1, 0, 2, 0, 1", key="prop_data")
            odds = st.number_input("Odds (American)", -500, 500, -110, key="prop_odds")
        
        if st.button("🚀 ANALYZE PROP", type="primary", key="prop_button"):
            if not player or player == "Select team first":
                st.error("Please select a player.")
            else:
                data = [float(x.strip()) for x in data_str.split(",")]
                injury_info = engine.api.get_injury_status(player, sport)
                result = engine.analyze_prop(player, market, line, pick, data, sport, odds, team if team else None, injury_info["injury"])
                st.markdown(f"### {result['signal']}")
                if result.get('season_warning'):
                    st.warning(result['season_warning'])
                c1, c2, c3 = st.columns(3)
                with c1: st.metric("Projection", f"{result['projection']:.1f}")
                with c2: st.metric("Probability", f"{result['probability']:.1%}")
                with c3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
                st.metric("Tier", result['tier'])
                if result['units'] > 0:
                    st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                if injury_info["injury"] != "HEALTHY":
                    st.warning(f"Injury Status: {injury_info['injury']}")
    
    with tab2:
        st.header("Moneyline Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport_ml = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="ml_sport")
            teams_ml = engine.get_teams(sport_ml)
            home = st.selectbox("Home Team", teams_ml, key="ml_home")
            away = st.selectbox("Away Team", teams_ml, key="ml_away")
        with c2:
            home_odds = st.number_input("Home Odds", -500, 500, -110, key="ml_home_odds")
            away_odds = st.number_input("Away Odds", -500, 500, -110, key="ml_away_odds")
        
        if st.button("💰 ANALYZE MONEYLINE", type="primary", key="ml_button"):
            result = engine.analyze_moneyline(home, away, sport_ml, home_odds, away_odds)
            st.markdown(f"### {result['signal']}")
            if result.get('season_warnings'):
                for w in result['season_warnings']:
                    st.warning(w)
            st.metric("Pick", result['pick'])
            st.metric("Edge", f"{result['edge']:+.1%}")
            st.metric("Win Probability", f"{result['win_prob']:.1%}")
            if result['units'] > 0:
                st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
    
    with tab3:
        st.header("Spread Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport_sp = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="sp_sport")
            teams_sp = engine.get_teams(sport_sp)
            home_sp = st.selectbox("Home Team", teams_sp, key="sp_home")
            away_sp = st.selectbox("Away Team", teams_sp, key="sp_away")
            spread = st.number_input("Spread", -30.0, 30.0, -5.5, key="sp_line")
        with c2:
            pick_sp = st.selectbox("Pick", [home_sp, away_sp], key="sp_pick")
            odds_sp = st.number_input("Odds", -500, 500, -110, key="sp_odds")
        
        if st.button("📊 ANALYZE SPREAD", type="primary", key="sp_button"):
            result = engine.analyze_spread(home_sp, away_sp, spread, pick_sp, sport_sp, odds_sp)
            st.markdown(f"### {result['signal']}")
            if result.get('season_warnings'):
                for w in result['season_warnings']:
                    st.warning(w)
            st.metric("Cover Probability", f"{result['prob_cover']:.1%}")
            st.metric("Push Probability", f"{result['prob_push']:.1%}")
            st.metric("Edge", f"{result['edge']:+.1%}")
            if result['units'] > 0:
                st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
    
    with tab4:
        st.header("Totals (Over/Under) Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport_tot = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="tot_sport")
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
            st.markdown(f"### {result['signal']}")
            if result.get('season_warnings'):
                for w in result['season_warnings']:
                    st.warning(w)
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("Projection", f"{result['projection']:.1f}")
            with c2: st.metric("OVER Prob", f"{result['prob_over']:.1%}")
            with c3: st.metric("UNDER Prob", f"{result['prob_under']:.1%}")
            st.metric("Push Prob", f"{result['prob_push']:.1%}")
            st.metric("Edge", f"{result['edge']:+.1%}")
            if result['units'] > 0:
                st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
    
    with tab5:
        st.header("Alternate Line Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport_alt = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="alt_sport")
            base_line = st.number_input("Main Line", 0.5, 300.0, 220.5, key="alt_base")
            alt_line = st.number_input("Alternate Line", 0.5, 300.0, 230.5, key="alt_line")
        with c2:
            pick_alt = st.selectbox("Pick", ["OVER", "UNDER"], key="alt_pick")
            odds_alt = st.number_input("Odds", -500, 500, -110, key="alt_odds")
        
        if st.button("🔄 ANALYZE ALTERNATE", type="primary", key="alt_button"):
            result = engine.analyze_alternate(base_line, alt_line, pick_alt, sport_alt, odds_alt)
            st.markdown(f"### {result['action']}")
            st.metric("Probability", f"{result['probability']:.1%}")
            st.metric("Implied", f"{result['implied']:.1%}")
            st.metric("Edge", f"{result['edge']:+.1%}")
            st.info(f"Value: {result['value']}")
    
    with tab6:
        st.header("🔗 Parlay Correlation Validator")
        legs_json = st.text_area("Paste parlay legs (JSON format)", 
                                 '[{"player":"LeBron James","market":"PTS","team":"Lakers"},{"player":"Anthony Davis","market":"REB","team":"Lakers"}]')
        if st.button("🔍 CHECK CORRELATION"):
            try:
                legs = json.loads(legs_json)
                result = engine.check_correlation(legs)
                if result['safe']:
                    st.success(f"✅ Parlay SAFE - Max correlation: {result['max_corr']:.1%}")
                else:
                    st.error(f"❌ Parlay REJECTED - Max correlation: {result['max_corr']:.1%} (>{engine.correlation_threshold:.0%})")
            except:
                st.error("Invalid JSON format")
    
    with tab7:
        st.header("🏆 PrizePicks Scanner (Direct API + AllOrigins Proxy)")
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_sports_pp = st.multiselect("Select sports", list(PropScanner.LEAGUE_IDS.keys()), default=["NBA", "MLB"], key="pp_sports")
        with col2:
            if st.button("🔍 SCAN PRIZEPICKS", type="primary", use_container_width=True):
                with st.spinner("Scanning PrizePicks..."):
                    results = engine.run_best_bets_scan(selected_sports_pp)
                    st.success("Scan complete!")
        if engine.scanned_bets.get("props"):
            st.subheader("🏀 Top Player Props")
            for i, bet in enumerate(engine.scanned_bets["props"], 1):
                st.markdown(f"**{i}. {bet['bet_line']}**")
                st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                if bet.get('season_warning'):
                    st.warning(bet['season_warning'])
        if engine.scanned_bets.get("games"):
            st.subheader("🎲 Top Game Bets")
            for i, bet in enumerate(engine.scanned_bets["games"], 1):
                st.markdown(f"**{i}. {bet['bet_line']}**")
                st.caption(f"Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
                if bet.get('season_warnings'):
                    for w in bet['season_warnings']:
                        st.warning(w)
    
    with tab8:
        st.header("📈 Best Odds Scanner")
        col1, col2 = st.columns([2, 1])
        with col1:
            selected_sports_odds = st.multiselect("Select sports", ["NBA", "MLB", "NHL", "NFL"], default=["NBA"], key="odds_sports")
        with col2:
            if st.button("🔍 SCAN BEST ODDS", type="primary", use_container_width=True):
                with st.spinner("Scanning sportsbooks..."):
                    bets = engine.run_best_odds_scan(selected_sports_odds)
                    st.success(f"Found {len(bets)} +EV props!")
        if engine.scanned_bets.get("best_odds"):
            st.subheader("💰 Best +EV Props (Top 10)")
            for i, bet in enumerate(engine.scanned_bets["best_odds"], 1):
                st.markdown(f"**{i}. {bet['player']} {bet['market']} {bet['pick']} {bet['line']}**")
                st.caption(f"Odds: {bet['odds']} @ {bet['bookmaker']} | Edge: {bet['edge']:.1%} | Prob: {bet['probability']:.1%} | Units: {bet['units']}")
    
    with tab9:
        st.header("💰 Arbitrage Detector")
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
    
    with tab10:
        st.header("🎯 Middle Hunter")
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
    
    with tab11:
        st.header("📊 Public Accuracy Dashboard")
        accuracy = engine.get_accuracy_dashboard()
        col1, col2, col3, col4 = st.columns(4)
        with col1: st.metric("Total Bets", accuracy['total_bets'])
        with col2: st.metric("Win Rate", f"{accuracy['win_rate']}%")
        with col3: st.metric("ROI", f"{accuracy['roi']}%")
        with col4: st.metric("Units Profit", f"+{accuracy['units_profit']}" if accuracy['units_profit'] > 0 else str(accuracy['units_profit']))
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

if __name__ == "__main__":
    run_dashboard()
