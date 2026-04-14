"""
CLARITY 18.0 ELITE - COMPLETE SYSTEM (FULL ROSTERS) - MERGED BEST VERSION
Player Props | Moneylines | Spreads | Totals | Alternate Lines
NBA | MLB | NHL | NFL - ALL TEAMS HAVE REAL PLAYERS
API KEYS: Perplexity + API-Sports + The Odds API
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom, gamma
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
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - API KEYS (KEPT AS REQUESTED)
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"  # Replace with actual Odds API key if different
VERSION = "18.0 Elite (Merged Best Version)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS & SETTINGS
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15, "avg_total": 228.5, "home_advantage": 3.0,
            "max_total": 300.0, "spread_std": 12.0},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08, "avg_total": 8.5, "home_advantage": 0.12,
            "max_total": 20.0, "spread_std": 4.5},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12, "avg_total": 6.0, "home_advantage": 0.15,
            "max_total": 10.0, "spread_std": 2.8},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20, "avg_total": 44.5, "home_advantage": 2.8,
            "max_total": 80.0, "spread_std": 14.0}
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES & API-SPORTS MAPPINGS
# =============================================================================
SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"]
}

API_SPORT_KEYS = {"NBA": "basketball", "MLB": "baseball", "NHL": "hockey", "NFL": "american-football"}
API_LEAGUE_IDS = {"NBA": 12, "MLB": 1, "NHL": 57, "NFL": 1}

STAT_MAPPING = {
    "NBA": {"PTS": "points", "REB": "totReb", "AST": "assists", "STL": "steals", "BLK": "blocks", "THREES": "tpm"},
    "MLB": {"HITS": "hits", "HR": "homeRuns", "RBI": "rbi", "TB": "totalBases", "KS": "strikeOuts"},
    "NHL": {"SOG": "shots", "GOALS": "goals", "ASSISTS": "assists", "HITS": "hits"},
    "NFL": {"PASS_YDS": "passingYards", "PASS_TD": "passingTDs", "RUSH_YDS": "rushingYards",
            "RUSH_TD": "rushingTDs", "REC_YDS": "receivingYards", "REC": "receptions"}
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
            "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans", "Washington Commanders"]
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
# COMPLETE NFL ROSTERS (Top 12 players per team)
# =============================================================================
NFL_ROSTERS = {
    "Arizona Cardinals": ["Kyler Murray", "James Conner", "Marvin Harrison Jr", "Trey McBride", "Michael Wilson", "Greg Dortch", "Zay Jones", "Trey Benson", "Budda Baker", "Jalen Thompson", "Zaven Collins", "Dennis Gardeck"],
    "Atlanta Falcons": ["Kirk Cousins", "Bijan Robinson", "Drake London", "Kyle Pitts", "Darnell Mooney", "Ray-Ray McCloud", "Tyler Allgeier", "Jessie Bates III", "A.J. Terrell", "Kaden Elliss", "Matthew Judon", "Grady Jarrett"],
    "Baltimore Ravens": ["Lamar Jackson", "Derrick Henry", "Zay Flowers", "Mark Andrews", "Isaiah Likely", "Rashod Bateman", "Justice Hill", "Roquan Smith", "Marlon Humphrey", "Kyle Hamilton", "Justin Madubuike", "Odafe Oweh"],
    "Buffalo Bills": ["Josh Allen", "James Cook", "Stefon Diggs", "Dalton Kincaid", "Khalil Shakir", "Curtis Samuel", "Keon Coleman", "Matt Milano", "Terrel Bernard", "Greg Rousseau", "Ed Oliver", "Taron Johnson"],
    "Carolina Panthers": ["Bryce Young", "Chuba Hubbard", "Diontae Johnson", "Adam Thielen", "Jonathan Mingo", "Xavier Legette", "Tommy Tremble", "Derrick Brown", "Jaycee Horn", "Shaq Thompson", "Jadeveon Clowney", "Brian Burns"],
    "Chicago Bears": ["Caleb Williams", "D'Andre Swift", "DJ Moore", "Keenan Allen", "Rome Odunze", "Cole Kmet", "Khalil Herbert", "Montez Sweat", "Tremaine Edmunds", "Jaylon Johnson", "Jaquan Brisker", "Gervon Dexter"],
    "Cincinnati Bengals": ["Joe Burrow", "Zack Moss", "Ja'Marr Chase", "Tee Higgins", "Mike Gesicki", "Andrei Iosivas", "Chase Brown", "Logan Wilson", "Germaine Pratt", "Trey Hendrickson", "Sam Hubbard", "Cam Taylor-Britt"],
    "Cleveland Browns": ["Deshaun Watson", "Nick Chubb", "Amari Cooper", "Jerry Jeudy", "David Njoku", "Elijah Moore", "Jerome Ford", "Myles Garrett", "Denzel Ward", "Jeremiah Owusu-Koramoah", "Za'Darius Smith", "Grant Delpit"],
    "Dallas Cowboys": ["Dak Prescott", "Ezekiel Elliott", "CeeDee Lamb", "Brandin Cooks", "Jake Ferguson", "Jalen Tolbert", "Rico Dowdle", "Micah Parsons", "Trevon Diggs", "DeMarcus Lawrence", "Leighton Vander Esch", "Malik Hooker"],
    "Denver Broncos": ["Bo Nix", "Javonte Williams", "Courtland Sutton", "Tim Patrick", "Josh Reynolds", "Marvin Mims", "Greg Dulcich", "Patrick Surtain II", "Justin Simmons", "Alex Singleton", "Baron Browning", "Zach Allen"],
    "Detroit Lions": ["Jared Goff", "Jahmyr Gibbs", "David Montgomery", "Amon-Ra St. Brown", "Sam LaPorta", "Jameson Williams", "Kalif Raymond", "Aidan Hutchinson", "Alex Anzalone", "Brian Branch", "Kerby Joseph", "Alim McNeill"],
    "Green Bay Packers": ["Jordan Love", "Josh Jacobs", "Christian Watson", "Romeo Doubs", "Jayden Reed", "Luke Musgrave", "Tucker Kraft", "Rashan Gary", "Jaire Alexander", "Quay Walker", "Xavier McKinney", "Kenny Clark"],
    "Houston Texans": ["C.J. Stroud", "Joe Mixon", "Nico Collins", "Tank Dell", "Stefon Diggs", "Dalton Schultz", "Robert Woods", "Will Anderson Jr.", "Danielle Hunter", "Derek Stingley Jr.", "Azeez Al-Shaair", "Jalen Pitre"],
    "Indianapolis Colts": ["Anthony Richardson", "Jonathan Taylor", "Michael Pittman Jr.", "Adonai Mitchell", "Josh Downs", "Jelani Woods", "Mo Alie-Cox", "Zaire Franklin", "DeForest Buckner", "Kenny Moore II", "Julian Blackmon", "Kwity Paye"],
    "Jacksonville Jaguars": ["Trevor Lawrence", "Travis Etienne", "Christian Kirk", "Gabe Davis", "Brian Thomas Jr.", "Evan Engram", "Tank Bigsby", "Josh Hines-Allen", "Foyesade Oluokun", "Tyson Campbell", "Andre Cisco", "Travon Walker"],
    "Kansas City Chiefs": ["Patrick Mahomes", "Isiah Pacheco", "Rashee Rice", "Xavier Worthy", "Marquise Brown", "Travis Kelce", "Clyde Edwards-Helaire", "Chris Jones", "Nick Bolton", "Trent McDuffie", "Justin Reid", "George Karlaftis"],
    "Las Vegas Raiders": ["Gardner Minshew", "Zamir White", "Davante Adams", "Jakobi Meyers", "Tre Tucker", "Brock Bowers", "Michael Mayer", "Maxx Crosby", "Robert Spillane", "Jack Jones", "Tre'von Moehrig", "Christian Wilkins"],
    "Los Angeles Chargers": ["Justin Herbert", "Gus Edwards", "Quentin Johnston", "Josh Palmer", "Ladd McConkey", "Will Dissly", "Hayden Hurst", "Joey Bosa", "Khalil Mack", "Derwin James", "Asante Samuel Jr.", "Tuli Tuipulotu"],
    "Los Angeles Rams": ["Matthew Stafford", "Kyren Williams", "Puka Nacua", "Cooper Kupp", "Demarcus Robinson", "Tutu Atwell", "Colby Parkinson", "Byron Young", "Ernest Jones", "Kobie Turner", "Jared Verse", "Quentin Lake"],
    "Miami Dolphins": ["Tua Tagovailoa", "Raheem Mostert", "De'Von Achane", "Tyreek Hill", "Jaylen Waddle", "Odell Beckham Jr.", "Jonnu Smith", "Jaelan Phillips", "Jalen Ramsey", "Bradley Chubb", "David Long Jr.", "Zach Sieler"],
    "Minnesota Vikings": ["Sam Darnold", "Aaron Jones", "Justin Jefferson", "Jordan Addison", "T.J. Hockenson", "Brandon Powell", "Ty Chandler", "Jonathan Greenard", "Blake Cashman", "Harrison Smith", "Byron Murphy", "Ivan Pace Jr."],
    "New England Patriots": ["Jacoby Brissett", "Rhamondre Stevenson", "Kendrick Bourne", "DeMario Douglas", "K.J. Osborn", "Hunter Henry", "Austin Hooper", "Matthew Judon", "Christian Gonzalez", "Kyle Dugger", "Jabrill Peppers", "Davon Godchaux"],
    "New Orleans Saints": ["Derek Carr", "Alvin Kamara", "Chris Olave", "Rashid Shaheed", "A.T. Perry", "Juwan Johnson", "Taysom Hill", "Demario Davis", "Marshon Lattimore", "Tyrann Mathieu", "Cameron Jordan", "Pete Werner"],
    "New York Giants": ["Daniel Jones", "Devin Singletary", "Malik Nabers", "Darius Slayton", "Wan'Dale Robinson", "Darren Waller", "Daniel Bellinger", "Dexter Lawrence", "Brian Burns", "Bobby Okereke", "Kayvon Thibodeaux", "Jason Pinnock"],
    "New York Jets": ["Aaron Rodgers", "Breece Hall", "Garrett Wilson", "Mike Williams", "Allen Lazard", "Tyler Conklin", "Jeremy Ruckert", "Quinnen Williams", "Sauce Gardner", "C.J. Mosley", "Haason Reddick", "Jermaine Johnson"],
    "Philadelphia Eagles": ["Jalen Hurts", "Saquon Barkley", "A.J. Brown", "DeVonta Smith", "Jahan Dotson", "Dallas Goedert", "Kenneth Gainwell", "Jalen Carter", "Darius Slay", "Bryce Huff", "C.J. Gardner-Johnson", "Nolan Smith"],
    "Pittsburgh Steelers": ["Russell Wilson", "Najee Harris", "George Pickens", "Van Jefferson", "Calvin Austin III", "Pat Freiermuth", "Darnell Washington", "T.J. Watt", "Minkah Fitzpatrick", "Alex Highsmith", "Patrick Queen", "Joey Porter Jr."],
    "San Francisco 49ers": ["Brock Purdy", "Christian McCaffrey", "Brandon Aiyuk", "Deebo Samuel", "Ricky Pearsall", "George Kittle", "Elijah Mitchell", "Nick Bosa", "Fred Warner", "Charvarius Ward", "Talanoa Hufanga", "Javon Hargrave"],
    "Seattle Seahawks": ["Geno Smith", "Kenneth Walker III", "DK Metcalf", "Tyler Lockett", "Jaxon Smith-Njigba", "Noah Fant", "Zach Charbonnet", "Devon Witherspoon", "Julian Love", "Boye Mafe", "Leonard Williams", "Dre'Mont Jones"],
    "Tampa Bay Buccaneers": ["Baker Mayfield", "Rachaad White", "Mike Evans", "Chris Godwin", "Trey Palmer", "Cade Otton", "Bucky Irving", "Antoine Winfield Jr.", "Lavonte David", "Vita Vea", "Jamel Dean", "Yaya Diaby"],
    "Tennessee Titans": ["Will Levis", "Tony Pollard", "DeAndre Hopkins", "Calvin Ridley", "Treylon Burks", "Tyler Boyd", "Chigoziem Okonkwo", "Jeffery Simmons", "Harold Landry", "L'Jarius Sneed", "Kenneth Murray", "Amani Hooker"],
    "Washington Commanders": ["Jayden Daniels", "Brian Robinson Jr.", "Terry McLaurin", "Jahan Dotson", "Luke McCaffrey", "Zach Ertz", "Austin Ekeler", "Jonathan Allen", "Daron Payne", "Bobby Wagner", "Frankie Luvu", "Emmanuel Forbes"]
}

# =============================================================================
# LIVE API CLIENTS
# =============================================================================

class OddsAPIClient:
    """Fetches live odds from The Odds API with error handling"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.last_request = 0
        self.rate_limit = 1.0

    def _rate_limit_wait(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()

    def get_odds(self, sport: str, regions: str = "us", markets: str = "h2h,spreads,totals") -> Dict:
        sport_key = {"NBA": "basketball_nba", "MLB": "baseball_mlb", "NHL": "icehockey_nhl", "NFL": "americanfootball_nfl"}.get(sport)
        if not sport_key:
            return {"error": f"Unsupported sport: {sport}"}
        self._rate_limit_wait()
        try:
            url = f"{self.base_url}/sports/{sport_key}/odds"
            params = {"apiKey": self.api_key, "regions": regions, "markets": markets, "oddsFormat": "american"}
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                return {"data": r.json()}
            else:
                return {"error": f"API error {r.status_code}: {r.text}"}
        except Exception as e:
            return {"error": str(e)}

    def extract_game_odds(self, sport: str, home_team: str, away_team: str) -> Dict:
        odds_data = self.get_odds(sport)
        if "error" in odds_data:
            return odds_data
        games = odds_data.get("data", [])
        for game in games:
            if home_team.lower() in game["home_team"].lower() and away_team.lower() in game["away_team"].lower():
                bookmakers = game.get("bookmakers", [])
                if bookmakers:
                    bm = bookmakers[0]
                    markets = {m["key"]: m for m in bm.get("markets", [])}
                    result = {"home_team": game["home_team"], "away_team": game["away_team"]}
                    if "h2h" in markets:
                        outcomes = markets["h2h"]["outcomes"]
                        result["home_ml"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                        result["away_ml"] = next((o["price"] for o in outcomes if o["name"] == game["away_team"]), None)
                    if "spreads" in markets:
                        outcomes = markets["spreads"]["outcomes"]
                        result["spread"] = next((o["point"] for o in outcomes if o["name"] == game["home_team"]), None)
                        result["spread_odds"] = next((o["price"] for o in outcomes if o["name"] == game["home_team"]), None)
                    if "totals" in markets:
                        outcomes = markets["totals"]["outcomes"]
                        result["total"] = next((o["point"] for o in outcomes), None)
                    return result
        return {"error": "No matching game found"}


class StatsAPIClient:
    """Fetches real player stats from API-Sports"""
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = API_SPORTS_BASE
        self.headers = {"x-apisports-key": api_key}
        self.cache = {}
        self.cache_ttl = 3600

    def _get_player_id(self, sport: str, player_name: str, team: str) -> Optional[int]:
        sport_key = API_SPORT_KEYS.get(sport)
        league_id = API_LEAGUE_IDS.get(sport)
        if not sport_key or not league_id:
            return None
        cache_key = f"pid_{sport}_{player_name}_{team}"
        if cache_key in self.cache and time.time() - self.cache[cache_key]["ts"] < self.cache_ttl:
            return self.cache[cache_key]["id"]
        try:
            url = f"{self.base_url}/{sport_key}/players"
            params = {"league": league_id, "season": "2025", "search": player_name}
            r = requests.get(url, headers=self.headers, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                players = data.get("response", [])
                for p in players:
                    if team.lower() in p.get("team", {}).get("name", "").lower():
                        pid = p["player"]["id"]
                        self.cache[cache_key] = {"id": pid, "ts": time.time()}
                        return pid
        except:
            pass
        return None

    def get_player_stats(self, sport: str, player_name: str, team: str, market: str) -> List[float]:
        sport_key = API_SPORT_KEYS.get(sport)
        league_id = API_LEAGUE_IDS.get(sport)
        if not sport_key or not league_id:
            return []
        player_id = self._get_player_id(sport, player_name, team)
        if not player_id:
            return []

        stat_field = STAT_MAPPING.get(sport, {}).get(market)
        if not stat_field:
            return []

        cache_key = f"stats_{sport}_{player_id}_{market}"
        if cache_key in self.cache and time.time() - self.cache[cache_key]["ts"] < self.cache_ttl:
            return self.cache[cache_key]["data"]

        try:
            url = f"{self.base_url}/{sport_key}/players/statistics"
            params = {"league": league_id, "season": "2025", "player": player_id}
            r = requests.get(url, headers=self.headers, params=params, timeout=10)
            if r.status_code == 200:
                data = r.json()
                games = data.get("response", [])
                stats = []
                for game in games[-10:]:
                    val = game.get("statistics", {}).get(stat_field, 0)
                    if val is not None:
                        stats.append(float(val))
                if stats:
                    self.cache[cache_key] = {"data": stats, "ts": time.time()}
                    return stats
        except:
            pass
        return []


class PerplexityClient:
    """Injury and news checks via Perplexity with structured prompt"""
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, base_url=PERPLEXITY_BASE)

    def get_injury_status(self, player: str, sport: str) -> Dict[str, Any]:
        prompt = f"""Provide the current injury status for {player} ({sport}) as of today. 
        Respond with a JSON object containing:
        - "status": one of "HEALTHY", "QUESTIONABLE", "DOUBTFUL", "OUT"
        - "steam": true if there is significant line movement (STEAM) reported, else false
        - "note": brief explanation
        Example: {{"status": "QUESTIONABLE", "steam": false, "note": "Ankle sprain, game-time decision"}}
        Only return valid JSON, no other text."""
        try:
            r = self.client.chat.completions.create(
                model="llama-3.1-sonar-large-32k-online",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                timeout=15
            )
            content = r.choices[0].message.content
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return {"injury": data.get("status", "UNKNOWN").upper(),
                        "steam": data.get("steam", False),
                        "note": data.get("note", "")}
        except:
            pass
        return {"injury": "UNKNOWN", "steam": False, "note": "Unable to fetch"}


# =============================================================================
# IMPROVED SIMULATION ENGINE
# =============================================================================
class SimulationEngine:
    def __init__(self, sims: int = 10000):
        self.sims = sims

    def simulate_prop(self, data: List[float], line: float, pick: str, sport: str) -> dict:
        if len(data) == 0:
            return {"proj": 0, "prob": 0.5, "dtm": 0}
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        w = np.ones(len(data))
        w[-3:] *= 1.5
        w /= w.sum()
        lam = np.average(data, weights=w)
        var_factor = model["variance_factor"]
        if var_factor > 1.0:
            shape = lam / (var_factor - 1) if var_factor > 1.001 else 1000
            scale = var_factor - 1 if var_factor > 1.001 else 0.001
            rates = gamma.rvs(a=shape, scale=scale, size=self.sims)
            rates = np.maximum(rates, 0.1)
            sims = poisson.rvs(rates)
        else:
            sims = poisson.rvs(lam, size=self.sims)
        proj = np.mean(sims)
        prob = np.mean(sims >= line) if pick == "OVER" else np.mean(sims <= line)
        dtm = (proj - line) / line if line != 0 else 0
        return {"proj": proj, "prob": prob, "dtm": dtm}

    def simulate_total(self, home_team: str, away_team: str, total_line: float, sport: str) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        base_proj = model["avg_total"]
        var_factor = model["variance_factor"]
        if var_factor > 1.0:
            shape = base_proj / (var_factor - 1) if var_factor > 1.001 else 1000
            scale = var_factor - 1 if var_factor > 1.001 else 0.001
            rates = gamma.rvs(a=shape, scale=scale, size=self.sims)
            rates = np.maximum(rates, 0.1)
            sims = poisson.rvs(rates)
        else:
            sims = poisson.rvs(base_proj, size=self.sims)
        proj = np.mean(sims)
        prob_over = np.mean(sims > total_line)
        prob_under = np.mean(sims < total_line)
        prob_push = np.mean(sims == total_line)
        return {"proj": proj, "prob_over": prob_over, "prob_under": prob_under, "prob_push": prob_push}


# =============================================================================
# BET EVALUATOR
# =============================================================================
class BetEvaluator:
    def __init__(self, bankroll: float = 1000.0):
        self.bankroll = bankroll
        self.prob_bolt = 0.84
        self.dtm_bolt = 0.15
        self.wsem_max = 0.10

    def convert_odds(self, american: int) -> float:
        return 1 + american/100 if american > 0 else 1 + 100/abs(american)

    def implied_prob(self, american: int) -> float:
        if american > 0:
            return 100 / (american + 100)
        return abs(american) / (abs(american) + 100)

    def kelly_stake(self, prob: float, odds: int, fraction: float = 0.25) -> float:
        b = self.convert_odds(odds) - 1
        if b <= 0:
            return 0.0
        f = (prob * b - (1 - prob)) / b
        return max(0.0, f * fraction * self.bankroll)

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

    def sovereign_bolt(self, prob: float, dtm: float, wsem_ok: bool, l42_pass: bool, injury: str) -> dict:
        if injury in ["OUT", "DOUBTFUL"]:
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

    def evaluate_prop(self, player: str, market: str, line: float, pick: str,
                      data: List[float], sport: str, odds: int, injury_status: str) -> dict:
        if not data:
            return {"signal": "🔴 NO DATA", "units": 0, "projection": 0, "probability": 0,
                    "edge": 0, "tier": "PASS", "injury": injury_status, "l42_msg": "No data", "kelly_stake": 0}
        sim = SimulationEngine().simulate_prop(data, line, pick, sport)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(sim["prob"], sim["dtm"], wsem_ok, l42_pass, injury_status)
        imp = self.implied_prob(odds)
        edge = sim["prob"] - imp

        if market.upper() in RED_TIER_PROPS:
            tier = "REJECT"
        elif edge >= 0.08:
            tier = "SAFE"
        elif edge >= 0.05:
            tier = "BALANCED+"
        elif edge >= 0.03:
            tier = "RISKY"
        else:
            tier = "PASS"

        kelly = self.kelly_stake(sim["prob"], odds)
        return {"player": player, "market": market, "line": line, "pick": pick,
                "signal": bolt["signal"], "units": bolt["units"], "projection": sim["proj"],
                "probability": sim["prob"], "edge": round(edge, 4), "tier": tier,
                "injury": injury_status, "l42_msg": l42_msg, "kelly_stake": round(kelly, 2)}

    def evaluate_total(self, home: str, away: str, total_line: float, pick: str,
                       sport: str, odds: int) -> dict:
        sim = SimulationEngine().simulate_total(home, away, total_line, sport)
        if pick == "OVER":
            prob = sim["prob_over"] / (1 - sim["prob_push"]) if sim["prob_push"] < 1 else sim["prob_over"]
        else:
            prob = sim["prob_under"] / (1 - sim["prob_push"]) if sim["prob_push"] < 1 else sim["prob_under"]
        imp = self.implied_prob(odds)
        edge = prob - imp
        if edge >= 0.05:
            tier, units, signal = "SAFE", 2.0, "🟢 SAFE"
        elif edge >= 0.03:
            tier, units, signal = "BALANCED+", 1.5, "🟡 BALANCED+"
        elif edge >= 0.01:
            tier, units, signal = "RISKY", 1.0, "🟠 RISKY"
        else:
            tier, units, signal = "PASS", 0, "🔴 PASS"
        kelly = self.kelly_stake(prob, odds)
        return {"home": home, "away": away, "total_line": total_line, "pick": pick,
                "signal": signal, "units": units, "projection": round(sim["proj"], 1),
                "prob_over": round(sim["prob_over"], 3), "prob_under": round(sim["prob_under"], 3),
                "prob_push": round(sim["prob_push"], 3), "edge": round(edge, 4),
                "tier": tier, "kelly_stake": round(kelly, 2)}

    def evaluate_moneyline(self, home: str, away: str, sport: str,
                           home_odds: int, away_odds: int) -> dict:
        model = SPORT_MODELS.get(sport, SPORT_MODELS["NBA"])
        home_adv = model.get("home_advantage", 0)
        home_win_prob = 0.55 + (home_adv / 100)
        away_win_prob = 1 - home_win_prob
        home_imp = self.implied_prob(home_odds)
        away_imp = self.implied_prob(away_odds)
        home_edge = home_win_prob - home_imp
        away_edge = away_win_prob - away_imp
        if home_edge > away_edge and home_edge > 0.02:
            pick, edge, odds, prob = home, home_edge, home_odds, home_win_prob
        elif away_edge > 0.02:
            pick, edge, odds, prob = away, away_edge, away_odds, away_win_prob
        else:
            return {"pick": "PASS", "signal": "🔴 PASS", "units": 0, "edge": 0}
        if edge >= 0.05:
            tier, units, signal = "SAFE", 2.0, "🟢 SAFE"
        elif edge >= 0.03:
            tier, units, signal = "BALANCED+", 1.5, "🟡 BALANCED+"
        else:
            tier, units, signal = "RISKY", 1.0, "🟠 RISKY"
        kelly = self.kelly_stake(prob, odds)
        return {"pick": pick, "signal": signal, "units": units, "edge": round(edge, 4),
                "win_prob": round(prob, 3), "tier": tier, "kelly_stake": round(kelly, 2)}


# =============================================================================
# MAIN APPLICATION
# =============================================================================
class ClarityApp:
    def __init__(self):
        self.evaluator = BetEvaluator()
        self.perplexity = PerplexityClient(UNIFIED_API_KEY)
        self.odds_client = OddsAPIClient(ODDS_API_KEY)
        self.stats_client = StatsAPIClient(API_SPORTS_KEY)
        self.sport_models = SPORT_MODELS
        self.roster_cache = {}

    def get_teams(self, sport: str) -> List[str]:
        return HARDCODED_TEAMS.get(sport, ["Select a sport first"])

    def get_roster(self, sport: str, team: str) -> List[str]:
        cache_key = f"{sport}_{team}"
        if cache_key in self.roster_cache:
            return self.roster_cache[cache_key]
        # Attempt live fetch
        sport_key = API_SPORT_KEYS.get(sport)
        league_id = API_LEAGUE_IDS.get(sport)
        if sport_key and league_id:
            try:
                url = f"{API_SPORTS_BASE}/{sport_key}/players"
                params = {"league": league_id, "season": "2025", "team": team}
                headers = {"x-apisports-key": API_SPORTS_KEY}
                r = requests.get(url, headers=headers, params=params, timeout=10)
                if r.status_code == 200:
                    players = r.json().get("response", [])
                    roster = [p["player"]["name"] for p in players[:15]]
                    if roster:
                        self.roster_cache[cache_key] = roster
                        return roster
            except:
                pass
        # Fallback to hardcoded
        if sport == "NBA":
            roster = NBA_ROSTERS.get(team, [])
        elif sport == "MLB":
            roster = MLB_ROSTERS.get(team, [])
        elif sport == "NHL":
            roster = NHL_ROSTERS.get(team, [])
        elif sport == "NFL":
            roster = NFL_ROSTERS.get(team, [])
        else:
            roster = []
        if not roster:
            roster = [f"{team} Player {i}" for i in range(1,9)]
        self.roster_cache[cache_key] = roster
        return roster

    def run(self):
        st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
        st.title("🔮 CLARITY 18.0 ELITE - MERGED BEST VERSION")
        st.markdown(f"**Player Props | Moneylines | Spreads | Totals | Alternate Lines | Version: {VERSION}**")

        with st.sidebar:
            st.header("🚀 SYSTEM STATUS")
            try:
                requests.get("https://api.perplexity.ai", timeout=3)
                st.success("✅ Perplexity API Reachable")
            except:
                st.warning("⚠️ Perplexity API Unreachable")
            st.info("ℹ️ API-Sports: Using fallback/manual stats")
            try:
                requests.get(f"{ODDS_API_BASE}/sports", params={"apiKey": ODDS_API_KEY}, timeout=3)
                st.success("✅ Odds API Connected")
            except:
                st.warning("⚠️ Odds API Unreachable")
            st.metric("Version", VERSION)
            st.metric("Bankroll", f"${self.evaluator.bankroll:,.0f}")
            st.info("💡 Live odds are fetched in real-time.")

        tab1, tab2, tab3, tab4, tab5 = st.tabs([
            "🎯 PLAYER PROPS", "💰 MONEYLINE", "📊 SPREAD", "📈 TOTALS", "🔄 ALT LINES"
        ])

        # TAB 1: PLAYER PROPS
        with tab1:
            st.header("Player Prop Analyzer")
            c1, c2 = st.columns(2)
            with c1:
                sport = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"], key="prop_sport")
                teams = self.get_teams(sport)
                team = st.selectbox("Team", teams, key="prop_team")
                roster = self.get_roster(sport, team)
                player = st.selectbox("Player", roster, key="prop_player")
                available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
                market = st.selectbox("Market", available_markets, key="prop_market")
                line = st.number_input("Line", 0.5, 100.0, 0.5, key="prop_line")
                pick = st.selectbox("Pick", ["OVER", "UNDER"], key="prop_pick")
                use_live_stats = st.checkbox("Fetch live stats from API-Sports", value=True)
            with c2:
                if not use_live_stats:
                    data_str = st.text_area("Recent Games (comma separated)", "0,1,0,2,0,1", key="prop_data")
                auto_odds = st.checkbox("Auto-fetch odds", value=True)
                if auto_odds:
                    odds = -110
                else:
                    odds = st.number_input("Odds (American)", -500, 500, -110, key="prop_odds")

            if st.button("🚀 ANALYZE PROP", type="primary", key="prop_button"):
                with st.spinner("Fetching injury status and stats..."):
                    injury_info = self.perplexity.get_injury_status(player, sport)
                    if use_live_stats:
                        data = self.stats_client.get_player_stats(sport, player, team, market)
                        if not data:
                            st.warning("No live stats found. Using fallback random data.")
                            np.random.seed(hash(player) % 2**32)
                            data = list(np.random.poisson(lam=15, size=8))
                        st.info(f"Fetched {len(data)} recent games: {data}")
                    else:
                        data = [float(x.strip()) for x in data_str.split(",")]
                    if auto_odds:
                        odds = -110
                        st.caption("Auto odds: Using standard -110 (player props not in Odds API)")
                    result = self.evaluator.evaluate_prop(
                        player, market, line, pick, data, sport, odds, injury_info["injury"]
                    )
                    st.markdown(f"### {result['signal']}")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Projection", f"{result['projection']:.1f}")
                    with c2: st.metric("Probability", f"{result['probability']:.1%}")
                    with c3: st.metric("Edge", f"{result['edge']:+.1%}")
                    st.metric("Tier", result['tier'])
                    if result['units'] > 0:
                        st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")
                    if injury_info["injury"] != "HEALTHY":
                        st.warning(f"Injury Status: {injury_info['injury']} - {injury_info.get('note','')}")
                    if injury_info["steam"]:
                        st.info("⚠️ STEAM detected - line may move quickly")

        # TAB 2: MONEYLINE
        with tab2:
            st.header("Moneyline Analyzer")
            c1, c2 = st.columns(2)
            with c1:
                sport_ml = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"], key="ml_sport")
                teams_ml = self.get_teams(sport_ml)
                home = st.selectbox("Home Team", teams_ml, key="ml_home")
                away = st.selectbox("Away Team", teams_ml, key="ml_away")
            with c2:
                auto_fetch = st.checkbox("Auto-fetch odds", value=True, key="ml_auto")
                if auto_fetch:
                    home_odds = -110
                    away_odds = -110
                else:
                    home_odds = st.number_input("Home Odds", -500, 500, -110, key="ml_home_odds")
                    away_odds = st.number_input("Away Odds", -500, 500, -110, key="ml_away_odds")

            if st.button("💰 ANALYZE MONEYLINE", type="primary", key="ml_button"):
                with st.spinner("Fetching odds..."):
                    if auto_fetch:
                        odds_data = self.odds_client.extract_game_odds(sport_ml, home, away)
                        if "error" not in odds_data:
                            home_odds = odds_data.get("home_ml", -110)
                            away_odds = odds_data.get("away_ml", -110)
                            st.success(f"Odds fetched: Home {home_odds}, Away {away_odds}")
                        else:
                            st.warning(f"Using default odds: {odds_data['error']}")
                            home_odds = -110
                            away_odds = -110
                    result = self.evaluator.evaluate_moneyline(home, away, sport_ml, home_odds, away_odds)
                    st.markdown(f"### {result['signal']}")
                    st.metric("Pick", result['pick'])
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    st.metric("Win Probability", f"{result['win_prob']:.1%}")
                    if result['units'] > 0:
                        st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")

        # TAB 3: SPREAD
        with tab3:
            st.header("Spread Analyzer")
            c1, c2 = st.columns(2)
            with c1:
                sport_sp = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"], key="sp_sport")
                teams_sp = self.get_teams(sport_sp)
                home_sp = st.selectbox("Home Team", teams_sp, key="sp_home")
                away_sp = st.selectbox("Away Team", teams_sp, key="sp_away")
                spread = st.number_input("Spread", -30.0, 30.0, -5.5, key="sp_line")
            with c2:
                pick_sp = st.selectbox("Pick", [home_sp, away_sp], key="sp_pick")
                auto_fetch_sp = st.checkbox("Auto-fetch odds", value=True, key="sp_auto")
                if auto_fetch_sp:
                    odds_sp = -110
                else:
                    odds_sp = st.number_input("Odds", -500, 500, -110, key="sp_odds")

            if st.button("📊 ANALYZE SPREAD", type="primary", key="sp_button"):
                with st.spinner("Fetching odds..."):
                    if auto_fetch_sp:
                        odds_data = self.odds_client.extract_game_odds(sport_sp, home_sp, away_sp)
                        if "error" not in odds_data and "spread_odds" in odds_data:
                            odds_sp = odds_data["spread_odds"]
                            spread_fetched = odds_data.get("spread")
                            if spread_fetched:
                                spread = spread_fetched
                                st.success(f"Fetched spread {spread} odds {odds_sp}")
                        else:
                            st.warning("Could not fetch spread odds, using default -110")
                            odds_sp = -110
                    model = self.sport_models.get(sport_sp, self.sport_models["NBA"])
                    std_dev = model.get("spread_std", 12.0)
                    home_adv = model.get("home_advantage", 0)
                    sims = norm.rvs(loc=home_adv, scale=std_dev, size=10000)
                    if pick_sp == home_sp:
                        prob_cover = np.mean(sims > -spread)
                    else:
                        prob_cover = np.mean(sims < -spread)
                    prob_push = np.mean(np.abs(sims + spread) < 0.5)
                    prob = prob_cover / (1 - prob_push) if prob_push < 1 else prob_cover
                    imp = self.evaluator.implied_prob(odds_sp)
                    edge = prob - imp
                    if edge >= 0.05:
                        tier, units, signal = "SAFE", 2.0, "🟢 SAFE"
                    elif edge >= 0.03:
                        tier, units, signal = "BALANCED+", 1.5, "🟡 BALANCED+"
                    elif edge >= 0.01:
                        tier, units, signal = "RISKY", 1.0, "🟠 RISKY"
                    else:
                        tier, units, signal = "PASS", 0, "🔴 PASS"
                    kelly = self.evaluator.kelly_stake(prob, odds_sp)
                    st.markdown(f"### {signal}")
                    st.metric("Cover Probability", f"{prob:.1%}")
                    st.metric("Push Probability", f"{prob_push:.1%}")
                    st.metric("Edge", f"{edge:+.1%}")
                    if units > 0:
                        st.success(f"RECOMMENDED UNITS: {units} (${kelly:.2f})")

        # TAB 4: TOTALS
        with tab4:
            st.header("Totals (Over/Under) Analyzer")
            c1, c2 = st.columns(2)
            with c1:
                sport_tot = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"], key="tot_sport")
                teams_tot = self.get_teams(sport_tot)
                home_tot = st.selectbox("Home Team", teams_tot, key="tot_home")
                away_tot = st.selectbox("Away Team", teams_tot, key="tot_away")
                max_total = self.sport_models[sport_tot]["max_total"]
                default_total = self.sport_models[sport_tot]["avg_total"]
                total_line = st.number_input("Total Line", 0.5, max_total, default_total, key="tot_line")
            with c2:
                pick_tot = st.selectbox("Pick", ["OVER", "UNDER"], key="tot_pick")
                auto_fetch_tot = st.checkbox("Auto-fetch odds & line", value=True, key="tot_auto")
                if auto_fetch_tot:
                    odds_tot = -110
                else:
                    odds_tot = st.number_input("Odds", -500, 500, -110, key="tot_odds")

            if st.button("📈 ANALYZE TOTAL", type="primary", key="tot_button"):
                with st.spinner("Fetching odds..."):
                    if auto_fetch_tot:
                        odds_data = self.odds_client.extract_game_odds(sport_tot, home_tot, away_tot)
                        if "error" not in odds_data and "total" in odds_data:
                            total_fetched = odds_data["total"]
                            if total_fetched:
                                total_line = total_fetched
                                st.success(f"Fetched total line: {total_line}")
                            odds_tot = -110
                        else:
                            st.warning("Could not fetch total line, using default")
                    result = self.evaluator.evaluate_total(home_tot, away_tot, total_line, pick_tot, sport_tot, odds_tot)
                    st.markdown(f"### {result['signal']}")
                    c1, c2, c3 = st.columns(3)
                    with c1: st.metric("Projection", f"{result['projection']:.1f}")
                    with c2: st.metric("OVER Prob", f"{result['prob_over']:.1%}")
                    with c3: st.metric("UNDER Prob", f"{result['prob_under']:.1%}")
                    st.metric("Push Prob", f"{result['prob_push']:.1%}")
                    st.metric("Edge", f"{result['edge']:+.1%}")
                    if result['units'] > 0:
                        st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")

        # TAB 5: ALT LINES
        with tab5:
            st.header("Alternate Line Analyzer")
            c1, c2 = st.columns(2)
            with c1:
                sport_alt = st.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"], key="alt_sport")
                teams_alt = self.get_teams(sport_alt)
                home_alt = st.selectbox("Home Team", teams_alt, key="alt_home")
                away_alt = st.selectbox("Away Team", teams_alt, key="alt_away")
                base_line = st.number_input("Main Line", 0.5, 300.0, 220.5, key="alt_base")
                alt_line = st.number_input("Alternate Line", 0.5, 300.0, 230.5, key="alt_line")
            with c2:
                pick_alt = st.selectbox("Pick", ["OVER", "UNDER"], key="alt_pick")
                odds_alt = st.number_input("Odds", -500, 500, -110, key="alt_odds")

            if st.button("🔄 ANALYZE ALTERNATE", type="primary", key="alt_button"):
                sim = SimulationEngine().simulate_total(home_alt, away_alt, base_line, sport_alt)
                if pick_alt == "OVER":
                    prob = np.mean(sim["proj"] > alt_line)
                else:
                    prob = np.mean(sim["proj"] < alt_line)
                imp = self.evaluator.implied_prob(odds_alt)
                edge = prob - imp
                if edge >= 0.03:
                    value, action = "GOOD VALUE", "BET"
                elif edge >= 0:
                    value, action = "FAIR VALUE", "CONSIDER"
                else:
                    value, action = "POOR VALUE", "AVOID"
                st.markdown(f"### {action}")
                st.metric("Probability", f"{prob:.1%}")
                st.metric("Implied", f"{imp:.1%}")
                st.metric("Edge", f"{edge:+.1%}")
                st.info(f"Value: {value}")

if __name__ == "__main__":
    app = ClarityApp()
    app.run()
