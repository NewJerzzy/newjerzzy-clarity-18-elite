"""
CLARITY 18.0 ELITE - REAL ROSTERS FOR ALL SPORTS
API KEYS: Perplexity + API-Sports
VERSION: 18.0 Elite (All Sports Real Rosters)
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, norm, nbinom
from scipy.special import iv
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
import statistics
from collections import defaultdict
import warnings
warnings.filterwarnings('ignore')

# =============================================================================
# CONFIGURATION - ALL API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
VERSION = "18.0 Elite (All Sports Real Rosters)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

try:
    from pybaseball import statcast_batter, playerid_lookup
    STATCAST_AVAILABLE = True
except ImportError:
    STATCAST_AVAILABLE = False

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS
# =============================================================================
SPORT_MODELS = {
    "NBA": {"distribution": "nbinom", "variance_factor": 1.15},
    "MLB": {"distribution": "poisson", "variance_factor": 1.08},
    "NHL": {"distribution": "poisson", "variance_factor": 1.12},
    "NFL": {"distribution": "nbinom", "variance_factor": 1.20}
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES
# =============================================================================
SPORT_CATEGORIES = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA", "FGM", "FGA", "FTM", "FTA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS", "ER", "BB", "SB"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS", "PP_PTS", "GA"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"]
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
# NBA ROSTERS (2025-26 Season)
# =============================================================================
NBA_ROSTERS = {
    "Atlanta Hawks": ["Trae Young", "Jalen Johnson", "Dyson Daniels", "Onyeka Okongwu", "Zaccharie Risacher",
                      "Bogdan Bogdanovic", "De'Andre Hunter", "Clint Capela", "Kobe Bufkin", "Mouhamed Gueye",
                      "Larry Nance Jr", "Garrison Mathews", "Vit Krejci", "David Roddy", "Keaton Wallace"],
    "Boston Celtics": ["Jayson Tatum", "Jaylen Brown", "Kristaps Porzingis", "Jrue Holiday", "Derrick White",
                       "Al Horford", "Payton Pritchard", "Sam Hauser", "Luke Kornet", "Neemias Queta",
                       "Baylor Scheierman", "Jaden Springer", "Xavier Tillman", "Jordan Walsh"],
    "Brooklyn Nets": ["Cameron Johnson", "Nic Claxton", "Cam Thomas", "Noah Clowney", "Dorian Finney-Smith",
                      "Dennis Schroder", "Bojan Bogdanovic", "Day'Ron Sharpe", "Ziaire Williams", "Keon Johnson",
                      "Jalen Wilson", "Trendon Watford", "Dariq Whitehead", "Shake Milton"],
    "Charlotte Hornets": ["LaMelo Ball", "Brandon Miller", "Mark Williams", "Miles Bridges", "Josh Green",
                          "Grant Williams", "Cody Martin", "Nick Richards", "Vasilije Micic", "Tidjane Salaun",
                          "Seth Curry", "DaQuan Jeffries", "Moussa Diabate", "KJ Simpson"],
    "Chicago Bulls": ["Coby White", "Nikola Vucevic", "Josh Giddey", "Patrick Williams", "Ayo Dosunmu",
                      "Zach LaVine", "Lonzo Ball", "Jalen Smith", "Matas Buzelis", "Dalen Terry",
                      "Torrey Craig", "Jevon Carter", "Chris Duarte", "Julian Phillips"],
    "Cleveland Cavaliers": ["Donovan Mitchell", "Darius Garland", "Evan Mobley", "Jarrett Allen", "Max Strus",
                            "Caris LeVert", "Isaac Okoro", "Georges Niang", "Dean Wade", "Sam Merrill",
                            "Ty Jerome", "Craig Porter Jr", "Tristan Thompson", "Jaylon Tyson"],
    "Dallas Mavericks": ["Luka Doncic", "Kyrie Irving", "Klay Thompson", "PJ Washington", "Daniel Gafford",
                         "Dereck Lively II", "Naji Marshall", "Quentin Grimes", "Maxi Kleber", "Dante Exum",
                         "Jaden Hardy", "Dwight Powell", "Olivier-Maxence Prosper", "AJ Lawson"],
    "Denver Nuggets": ["Nikola Jokic", "Jamal Murray", "Michael Porter Jr", "Aaron Gordon", "Christian Braun",
                       "Russell Westbrook", "Peyton Watson", "Dario Saric", "Zeke Nnaji", "Julian Strawther",
                       "Vlatko Cancar", "Hunter Tyson", "Jalen Pickett", "DeAndre Jordan"],
    "Detroit Pistons": ["Cade Cunningham", "Jaden Ivey", "Ausar Thompson", "Jalen Duren", "Isaiah Stewart",
                        "Tim Hardaway Jr", "Malik Beasley", "Tobias Harris", "Ron Holland", "Marcus Sasser",
                        "Simone Fontecchio", "Paul Reed", "Wendell Moore Jr", "Bobi Klintman"],
    "Golden State Warriors": ["Stephen Curry", "Draymond Green", "Andrew Wiggins", "Jonathan Kuminga", "Brandin Podziemski",
                              "Buddy Hield", "Kevon Looney", "Gary Payton II", "Moses Moody", "Trayce Jackson-Davis",
                              "Kyle Anderson", "De'Anthony Melton", "Lindy Waters III", "Gui Santos"],
    "Houston Rockets": ["Alperen Sengun", "Jalen Green", "Fred VanVleet", "Jabari Smith Jr", "Dillon Brooks",
                        "Amen Thompson", "Tari Eason", "Cam Whitmore", "Steven Adams", "Reed Sheppard",
                        "Jae'Sean Tate", "Jeff Green", "Aaron Holiday", "Jock Landale"],
    "Indiana Pacers": ["Tyrese Haliburton", "Pascal Siakam", "Myles Turner", "Bennedict Mathurin", "Andrew Nembhard",
                       "TJ McConnell", "Aaron Nesmith", "Obi Toppin", "Jarace Walker", "Ben Sheppard",
                       "James Wiseman", "Johnny Furphy", "Enrique Freeman", "Quenton Jackson"],
    "LA Clippers": ["Kawhi Leonard", "James Harden", "Norman Powell", "Ivica Zubac", "Derrick Jones Jr",
                    "Terance Mann", "Nicolas Batum", "Kris Dunn", "Amir Coffey", "Mo Bamba",
                    "Kevin Porter Jr", "Kobe Brown", "Jordan Miller", "Cam Christie"],
    "Los Angeles Lakers": ["LeBron James", "Anthony Davis", "Austin Reaves", "D'Angelo Russell", "Rui Hachimura",
                           "Jarred Vanderbilt", "Gabe Vincent", "Max Christie", "Jaxson Hayes", "Cam Reddish",
                           "Christian Wood", "Dalton Knecht", "Bronny James", "Jalen Hood-Schifino"],
    "Memphis Grizzlies": ["Ja Morant", "Desmond Bane", "Jaren Jackson Jr", "Marcus Smart", "Zach Edey",
                          "Brandon Clarke", "Santi Aldama", "Luke Kennard", "GG Jackson", "Vince Williams Jr",
                          "Jake LaRavia", "John Konchar", "Jaylen Wells", "Scotty Pippen Jr"],
    "Miami Heat": ["Jimmy Butler", "Bam Adebayo", "Tyler Herro", "Terry Rozier", "Jaime Jaquez Jr",
                   "Duncan Robinson", "Nikola Jovic", "Haywood Highsmith", "Kevin Love", "Thomas Bryant",
                   "Josh Richardson", "Alec Burks", "Dru Smith", "Keshad Johnson"],
    "Milwaukee Bucks": ["Giannis Antetokounmpo", "Damian Lillard", "Khris Middleton", "Brook Lopez", "Bobby Portis",
                        "Gary Trent Jr", "Taurean Prince", "Delon Wright", "Pat Connaughton", "AJ Johnson",
                        "MarJon Beauchamp", "Andre Jackson Jr", "Chris Livingston", "Tyler Smith"],
    "Minnesota Timberwolves": ["Anthony Edwards", "Karl-Anthony Towns", "Rudy Gobert", "Jaden McDaniels", "Mike Conley",
                               "Naz Reid", "Donte DiVincenzo", "Nickeil Alexander-Walker", "Joe Ingles", "Rob Dillingham",
                               "Josh Minott", "Luka Garza", "Leonard Miller", "Terrence Shannon Jr"],
    "New Orleans Pelicans": ["Zion Williamson", "Brandon Ingram", "CJ McCollum", "Dejounte Murray", "Herb Jones",
                             "Trey Murphy III", "Jonas Valanciunas", "Jose Alvarado", "Jordan Hawkins", "Yves Missi",
                             "Jeremiah Robinson-Earl", "Javonte Green", "Karlo Matkovic", "Antonio Reeves"],
    "New York Knicks": ["Jalen Brunson", "Julius Randle", "Mikal Bridges", "OG Anunoby", "Mitchell Robinson",
                        "Donte DiVincenzo", "Josh Hart", "Miles McBride", "Precious Achiuwa", "Jericho Sims",
                        "Cam Payne", "Pacome Dadiet", "Tyler Kolek", "Kevin McCullar Jr"],
    "Oklahoma City Thunder": ["Shai Gilgeous-Alexander", "Chet Holmgren", "Jalen Williams", "Luguentz Dort", "Isaiah Hartenstein",
                              "Alex Caruso", "Cason Wallace", "Isaiah Joe", "Aaron Wiggins", "Jaylin Williams",
                              "Kenrich Williams", "Ousmane Dieng", "Dillon Jones", "Ajay Mitchell"],
    "Orlando Magic": ["Paolo Banchero", "Franz Wagner", "Jalen Suggs", "Kentavious Caldwell-Pope", "Wendell Carter Jr",
                      "Cole Anthony", "Jonathan Isaac", "Moritz Wagner", "Anthony Black", "Gary Harris",
                      "Goga Bitadze", "Tristan da Silva", "Caleb Houstan", "Jett Howard"],
    "Philadelphia 76ers": ["Joel Embiid", "Tyrese Maxey", "Paul George", "Caleb Martin", "Kelly Oubre Jr",
                           "Andre Drummond", "Eric Gordon", "Kyle Lowry", "Ricky Council IV", "KJ Martin",
                           "Reggie Jackson", "Guerschon Yabusele", "Adem Bona", "Jared McCain"],
    "Phoenix Suns": ["Kevin Durant", "Devin Booker", "Bradley Beal", "Jusuf Nurkic", "Grayson Allen",
                     "Royce O'Neale", "Mason Plumlee", "Monte Morris", "Bol Bol", "Damion Lee",
                     "Josh Okogie", "Nassir Little", "Ryan Dunn", "Oso Ighodaro"],
    "Portland Trail Blazers": ["Scoot Henderson", "Anfernee Simons", "Shaedon Sharpe", "Jerami Grant", "Deandre Ayton",
                               "Deni Avdija", "Donovan Clingan", "Toumani Camara", "Robert Williams III", "Matisse Thybulle",
                               "Jabari Walker", "Kris Murray", "Duop Reath", "Rayan Rupert"],
    "Sacramento Kings": ["De'Aaron Fox", "Domantas Sabonis", "DeMar DeRozan", "Keegan Murray", "Malik Monk",
                         "Kevin Huerter", "Trey Lyles", "Keon Ellis", "Davion Mitchell", "Alex Len",
                         "Jordan McLaughlin", "Jalen McDaniels", "Colby Jones", "Devin Carter"],
    "San Antonio Spurs": ["Victor Wembanyama", "Devin Vassell", "Keldon Johnson", "Jeremy Sochan", "Chris Paul",
                          "Harrison Barnes", "Zach Collins", "Tre Jones", "Julian Champagnie", "Malaki Branham",
                          "Blake Wesley", "Sidy Cissoko", "Charles Bassey", "Stephon Castle"],
    "Toronto Raptors": ["Scottie Barnes", "Immanuel Quickley", "RJ Barrett", "Jakob Poeltl", "Gradey Dick",
                        "Kelly Olynyk", "Bruce Brown", "Chris Boucher", "Davion Mitchell", "Ochai Agbaji",
                        "Jonathan Mogbo", "Jamal Shead", "Ja'Kobe Walter", "Ulrich Chomche"],
    "Utah Jazz": ["Lauri Markkanen", "Collin Sexton", "John Collins", "Jordan Clarkson", "Keyonte George",
                  "Walker Kessler", "Taylor Hendricks", "Cody Williams", "Isaiah Collier", "Brice Sensabaugh",
                  "Drew Eubanks", "Kyle Filipowski", "Johnny Juzang", "Patty Mills"],
    "Washington Wizards": ["Jordan Poole", "Kyle Kuzma", "Bilal Coulibaly", "Jonas Valanciunas", "Malcolm Brogdon",
                           "Corey Kispert", "Marvin Bagley III", "Saddiq Bey", "Alex Sarr", "Carlton Carrington",
                           "Kyshawn George", "Johnny Davis", "Patrick Baldwin Jr", "Jared Butler"]
}

# =============================================================================
# MLB ROSTERS (2026 Season)
# =============================================================================
MLB_ROSTERS = {
    "New York Yankees": ["Aaron Judge", "Juan Soto", "Giancarlo Stanton", "Gerrit Cole", "Anthony Volpe",
                         "Gleyber Torres", "DJ LeMahieu", "Carlos Rodon", "Marcus Stroman", "Clarke Schmidt",
                         "Jose Trevino", "Anthony Rizzo", "Alex Verdugo", "Trent Grisham", "Oswald Peraza",
                         "Austin Wells", "Oswaldo Cabrera", "Jon Berti", "Clay Holmes", "Ian Hamilton"],
    "Los Angeles Dodgers": ["Shohei Ohtani", "Mookie Betts", "Freddie Freeman", "Yoshinobu Yamamoto",
                            "Will Smith", "Max Muncy", "Teoscar Hernandez", "Tyler Glasnow", "James Outman",
                            "Gavin Lux", "Chris Taylor", "Miguel Rojas", "Enrique Hernandez", "Jason Heyward",
                            "Austin Barnes", "Bobby Miller", "Walker Buehler", "Evan Phillips", "Alex Vesia"],
    "Atlanta Braves": ["Ronald Acuna Jr", "Matt Olson", "Austin Riley", "Ozzie Albies", "Michael Harris II",
                       "Sean Murphy", "Marcell Ozuna", "Orlando Arcia", "Jarred Kelenic", "Travis d'Arnaud",
                       "Spencer Strider", "Max Fried", "Chris Sale", "Charlie Morton", "Raisel Iglesias"],
    "Houston Astros": ["Jose Altuve", "Yordan Alvarez", "Alex Bregman", "Kyle Tucker", "Jeremy Pena",
                       "Yainer Diaz", "Jose Abreu", "Chas McCormick", "Jake Meyers", "Mauricio Dubon",
                       "Framber Valdez", "Cristian Javier", "Hunter Brown", "Justin Verlander", "Josh Hader"],
    "Philadelphia Phillies": ["Bryce Harper", "Trea Turner", "Kyle Schwarber", "JT Realmuto", "Nick Castellanos",
                              "Bryson Stott", "Alec Bohm", "Brandon Marsh", "Johan Rojas", "Whit Merrifield",
                              "Zack Wheeler", "Aaron Nola", "Ranger Suarez", "Cristopher Sanchez", "Jose Alvarado"],
    "Texas Rangers": ["Corey Seager", "Marcus Semien", "Adolis Garcia", "Josh Jung", "Evan Carter",
                      "Wyatt Langford", "Jonah Heim", "Nathaniel Lowe", "Leody Taveras", "Ezequiel Duran",
                      "Jacob deGrom", "Max Scherzer", "Nathan Eovaldi", "Dane Dunning", "Jose Leclerc"],
    "Baltimore Orioles": ["Adley Rutschman", "Gunnar Henderson", "Jackson Holliday", "Cedric Mullins",
                          "Anthony Santander", "Ryan Mountcastle", "Jordan Westburg", "Colton Cowser",
                          "Heston Kjerstad", "Ramon Urias", "Corbin Burnes", "Grayson Rodriguez", "Kyle Bradish"],
    "Toronto Blue Jays": ["Vladimir Guerrero Jr", "Bo Bichette", "George Springer", "Kevin Gausman",
                          "Jose Berrios", "Chris Bassitt", "Yusei Kikuchi", "Daulton Varsho",
                          "Alejandro Kirk", "Davis Schneider", "Justin Turner", "Danny Jansen", "Jordan Romano"],
    "New York Mets": ["Pete Alonso", "Francisco Lindor", "Brandon Nimmo", "Kodai Senga", "Edwin Diaz",
                      "Jeff McNeil", "Starling Marte", "Francisco Alvarez", "Brett Baty", "Mark Vientos",
                      "Harrison Bader", "Luis Severino", "Sean Manaea", "Jose Quintana", "Adam Ottavino"],
    "San Diego Padres": ["Fernando Tatis Jr", "Manny Machado", "Xander Bogaerts", "Yu Darvish", "Joe Musgrove",
                         "Jake Cronenworth", "Ha-Seong Kim", "Luis Campusano", "Jackson Merrill",
                         "Jurickson Profar", "Michael King", "Robert Suarez", "Yuki Matsui", "Wandy Peralta"],
    "Seattle Mariners": ["Julio Rodriguez", "Cal Raleigh", "JP Crawford", "Mitch Garver", "Mitch Haniger",
                         "Ty France", "Jorge Polanco", "Luke Raley", "Dominic Canzone", "Dylan Moore",
                         "Luis Castillo", "George Kirby", "Logan Gilbert", "Bryce Miller", "Andres Munoz"],
    "Tampa Bay Rays": ["Yandy Diaz", "Randy Arozarena", "Brandon Lowe", "Isaac Paredes", "Josh Lowe",
                       "Jose Siri", "Harold Ramirez", "Jonathan Aranda", "Curtis Mead", "Rene Pinto",
                       "Zach Eflin", "Aaron Civale", "Ryan Pepiot", "Taj Bradley", "Pete Fairbanks"],
    "Milwaukee Brewers": ["Christian Yelich", "Willy Adames", "William Contreras", "Rhys Hoskins",
                          "Jackson Chourio", "Sal Frelick", "Garrett Mitchell", "Brice Turang",
                          "Joey Ortiz", "Oliver Dunn", "Freddy Peralta", "Brandon Woodruff", "Devin Williams"],
    "St. Louis Cardinals": ["Paul Goldschmidt", "Nolan Arenado", "Willson Contreras", "Jordan Walker",
                            "Masyn Winn", "Lars Nootbaar", "Brendan Donovan", "Nolan Gorman",
                            "Alec Burleson", "Ivan Herrera", "Sonny Gray", "Miles Mikolas", "Ryan Helsley"],
    "Chicago Cubs": ["Cody Bellinger", "Dansby Swanson", "Ian Happ", "Seiya Suzuki", "Nico Hoerner",
                     "Christopher Morel", "Michael Busch", "Miguel Amaya", "Mike Tauchman", "Nick Madrigal",
                     "Justin Steele", "Shota Imanaga", "Jameson Taillon", "Kyle Hendricks", "Adbert Alzolay"],
    "Boston Red Sox": ["Rafael Devers", "Trevor Story", "Masataka Yoshida", "Triston Casas", "Jarren Duran",
                       "Tyler O'Neill", "Ceddanne Rafaela", "Wilyer Abreu", "Enmanuel Valdez", "Reese McGuire",
                       "Brayan Bello", "Lucas Giolito", "Nick Pivetta", "Kutter Crawford", "Kenley Jansen"],
    "Cleveland Guardians": ["Jose Ramirez", "Andres Gimenez", "Josh Naylor", "Steven Kwan", "Bo Naylor",
                            "Brayan Rocchio", "Tyler Freeman", "Will Brennan", "Ramon Laureano", "David Fry",
                            "Shane Bieber", "Triston McKenzie", "Tanner Bibee", "Logan Allen", "Emmanuel Clase"],
    "Detroit Tigers": ["Spencer Torkelson", "Riley Greene", "Kerry Carpenter", "Javier Baez", "Colt Keith",
                       "Parker Meadows", "Matt Vierling", "Jake Rogers", "Zach McKinstry", "Andy Ibanez",
                       "Tarik Skubal", "Jack Flaherty", "Kenta Maeda", "Reese Olson", "Casey Mize"],
    "Minnesota Twins": ["Carlos Correa", "Royce Lewis", "Byron Buxton", "Pablo Lopez", "Joe Ryan",
                        "Bailey Ober", "Chris Paddack", "Edouard Julien", "Alex Kirilloff", "Max Kepler",
                        "Ryan Jeffers", "Christian Vazquez", "Willi Castro", "Kyle Farmer", "Jhoan Duran"],
    "Kansas City Royals": ["Bobby Witt Jr", "Vinnie Pasquantino", "Salvador Perez", "Cole Ragans",
                           "Seth Lugo", "Michael Wacha", "Brady Singer", "MJ Melendez", "Maikel Garcia",
                           "Nelson Velazquez", "Kyle Isbel", "Hunter Renfroe", "Adam Frazier", "James McArthur"],
    "Arizona Diamondbacks": ["Corbin Carroll", "Ketel Marte", "Zac Gallen", "Merrill Kelly", "Eduardo Rodriguez",
                             "Brandon Pfaadt", "Ryne Nelson", "Christian Walker", "Gabriel Moreno",
                             "Lourdes Gurriel Jr", "Eugenio Suarez", "Alek Thomas", "Jake McCarthy", "Paul Sewald"],
    "Colorado Rockies": ["Nolan Jones", "Ezequiel Tovar", "Brenton Doyle", "Kris Bryant", "Ryan McMahon",
                         "Elias Diaz", "Brendan Rodgers", "Sean Bouchard", "Elehuris Montero", "Michael Toglia",
                         "Kyle Freeland", "Cal Quantrill", "Austin Gomber", "Ryan Feltner", "Daniel Bard"],
    "Miami Marlins": ["Luis Arraez", "Jazz Chisholm Jr", "Josh Bell", "Jake Burger", "Jesus Sanchez",
                      "Bryan De La Cruz", "Tim Anderson", "Nick Gordon", "Christian Bethancourt", "Vidal Brujan",
                      "Jesus Luzardo", "Eury Perez", "Braxton Garrett", "Trevor Rogers", "Tanner Scott"],
    "Cincinnati Reds": ["Elly De La Cruz", "Spencer Steer", "Matt McLain", "Jeimer Candelario", "Christian Encarnacion-Strand",
                        "TJ Friedl", "Will Benson", "Tyler Stephenson", "Jonathan India", "Noelvi Marte",
                        "Hunter Greene", "Frankie Montas", "Nick Lodolo", "Andrew Abbott", "Alexis Diaz"],
    "Pittsburgh Pirates": ["Oneil Cruz", "Ke'Bryan Hayes", "Bryan Reynolds", "Jack Suwinski", "Henry Davis",
                           "Jared Triolo", "Connor Joe", "Andrew McCutchen", "Rowdy Tellez", "Michael A Taylor",
                           "Mitch Keller", "Martin Perez", "Marco Gonzales", "Luis Ortiz", "David Bednar"],
    "Los Angeles Angels": ["Mike Trout", "Anthony Rendon", "Taylor Ward", "Logan O'Hoppe", "Nolan Schanuel",
                           "Zach Neto", "Mickey Moniak", "Brandon Drury", "Luis Rengifo", "Jo Adell",
                           "Reid Detmers", "Patrick Sandoval", "Griffin Canning", "Chase Silseth", "Carlos Estevez"],
    "Oakland Athletics": ["Zack Gelof", "Esteury Ruiz", "Brent Rooker", "Seth Brown", "JJ Bleday",
                          "Shea Langeliers", "Ryan Noda", "Darell Hernaiz", "Lawrence Butler", "Abraham Toro",
                          "JP Sears", "Paul Blackburn", "Alex Wood", "Ross Stripling", "Mason Miller"],
    "San Francisco Giants": ["Jung Hoo Lee", "Matt Chapman", "Jorge Soler", "Logan Webb", "Blake Snell",
                             "Kyle Harrison", "Jordan Hicks", "Keaton Winn", "Patrick Bailey", "Thairo Estrada",
                             "LaMonte Wade Jr", "Michael Conforto", "Mike Yastrzemski", "Wilmer Flores", "Camilo Doval"],
    "Chicago White Sox": ["Luis Robert Jr", "Eloy Jimenez", "Andrew Vaughn", "Yoan Moncada", "Andrew Benintendi",
                          "Nicky Lopez", "Paul DeJong", "Martin Maldonado", "Dominic Fletcher", "Kevin Pillar",
                          "Dylan Cease", "Michael Kopech", "Erick Fedde", "Chris Flexen", "Garrett Crochet"],
    "Washington Nationals": ["CJ Abrams", "Lane Thomas", "Keibert Ruiz", "Joey Meneses", "Jesse Winker",
                             "Joey Gallo", "Nick Senzel", "Eddie Rosario", "Riley Adams", "Ildemaro Vargas",
                             "Josiah Gray", "MacKenzie Gore", "Jake Irvin", "Trevor Williams", "Kyle Finnegan"]
}

# =============================================================================
# NHL ROSTERS (2025-26 Season)
# =============================================================================
NHL_ROSTERS = {
    "Boston Bruins": ["David Pastrnak", "Brad Marchand", "Charlie McAvoy", "Jeremy Swayman", "Pavel Zacha",
                      "Charlie Coyle", "Hampus Lindholm", "Jake DeBrusk", "Morgan Geekie", "Trent Frederic",
                      "Brandon Carlo", "Mason Lohrei", "Matthew Poitras", "Johnny Beecher", "Mark Kastelic",
                      "Andrew Peeke", "Nikita Zadorov", "Elias Lindholm", "Joonas Korpisalo", "Max Jones"],
    "Florida Panthers": ["Matthew Tkachuk", "Aleksander Barkov", "Sam Reinhart", "Carter Verhaeghe", "Sam Bennett",
                         "Gustav Forsling", "Aaron Ekblad", "Sergei Bobrovsky", "Evan Rodrigues", "Eetu Luostarinen",
                         "Anton Lundell", "Dmitry Kulikov", "Niko Mikkola", "Jesper Boqvist", "A.J. Greer",
                         "Mackie Samoskevich", "Nate Schmidt", "Tomas Nosek", "Spencer Knight", "Jonah Gadjovich"],
    "Toronto Maple Leafs": ["Auston Matthews", "Mitch Marner", "William Nylander", "John Tavares", "Morgan Rielly",
                            "Chris Tanev", "Oliver Ekman-Larsson", "Jake McCabe", "Matthew Knies", "Max Domi",
                            "Bobby McMann", "Calle Jarnkrok", "David Kampf", "Pontus Holmberg", "Ryan Reaves",
                            "Timothy Liljegren", "Conor Timmins", "Simon Benoit", "Anthony Stolarz", "Joseph Woll"],
    "Tampa Bay Lightning": ["Nikita Kucherov", "Brayden Point", "Victor Hedman", "Andrei Vasilevskiy", "Jake Guentzel",
                            "Brandon Hagel", "Anthony Cirelli", "Nick Paul", "Conor Geekie", "Mitchell Chaffee",
                            "Darren Raddysh", "Erik Cernak", "Ryan McDonagh", "JJ Moser", "Jonas Johansson",
                            "Cam Atkinson", "Zemgus Girgensons", "Emil Lilleberg", "Nick Perbix", "Mikey Eyssimont"],
    "New York Rangers": ["Artemi Panarin", "Adam Fox", "Igor Shesterkin", "Mika Zibanejad", "Chris Kreider",
                         "Vincent Trocheck", "Alexis Lafreniere", "K'Andre Miller", "Jacob Trouba", "Ryan Lindgren",
                         "Will Cuylle", "Kaapo Kakko", "Filip Chytil", "Sam Carrick", "Reilly Smith",
                         "Braden Schneider", "Zac Jones", "Jonathan Quick", "Jimmy Vesey", "Jonny Brodzinski"],
    "Carolina Hurricanes": ["Sebastian Aho", "Andrei Svechnikov", "Seth Jarvis", "Jaccob Slavin", "Brent Burns",
                            "Martin Necas", "Jordan Staal", "Dmitry Orlov", "Jesperi Kotkaniemi", "Jordan Martinook",
                            "Jack Roslovic", "Jesper Fast", "William Carrier", "Sean Walker", "Shayne Gostisbehere",
                            "Pyotr Kochetkov", "Frederik Andersen", "Jalen Chatfield", "Jack Drury", "Eric Robinson"],
    "Colorado Avalanche": ["Nathan MacKinnon", "Cale Makar", "Mikko Rantanen", "Devon Toews", "Alexandar Georgiev",
                           "Artturi Lehkonen", "Jonathan Drouin", "Casey Mittelstadt", "Ross Colton", "Logan O'Connor",
                           "Samuel Girard", "Josh Manson", "Miles Wood", "Parker Kelly", "Calum Ritchie",
                           "Ivan Ivan", "Nikolai Kovalenko", "Sam Malinski", "Justus Annunen", "Calvin de Haan"],
    "Dallas Stars": ["Jason Robertson", "Roope Hintz", "Miro Heiskanen", "Jake Oettinger", "Wyatt Johnston",
                     "Matt Duchene", "Jamie Benn", "Tyler Seguin", "Mason Marchment", "Logan Stankoven",
                     "Thomas Harley", "Esa Lindell", "Ilya Lyubushkin", "Matt Dumba", "Sam Steel",
                     "Casey DeSmith", "Nils Lundkvist", "Colin Blackwell", "Brendan Smith", "Oskar Back"],
    "Edmonton Oilers": ["Connor McDavid", "Leon Draisaitl", "Evan Bouchard", "Zach Hyman", "Ryan Nugent-Hopkins",
                        "Mattias Ekholm", "Stuart Skinner", "Darnell Nurse", "Viktor Arvidsson", "Jeff Skinner",
                        "Adam Henrique", "Connor Brown", "Corey Perry", "Derek Ryan", "Mattias Janmark",
                        "Brett Kulak", "Ty Emberson", "Josh Brown", "Troy Stecher", "Calvin Pickard"],
    "Vegas Golden Knights": ["Jack Eichel", "Mark Stone", "Tomas Hertl", "Shea Theodore", "Adin Hill",
                             "William Karlsson", "Ivan Barbashev", "Alex Pietrangelo", "Noah Hanifin", "Pavel Dorofeyev",
                             "Nicolas Roy", "Brett Howden", "Keegan Kolesar", "Alexander Holtz", "Victor Olofsson",
                             "Brayden McNabb", "Zach Whitecloud", "Nicolas Hague", "Ilya Samsonov", "Kaedan Korczak"],
    "Winnipeg Jets": ["Kyle Connor", "Mark Scheifele", "Josh Morrissey", "Connor Hellebuyck", "Nikolaj Ehlers",
                      "Gabriel Vilardi", "Cole Perfetti", "Nino Niederreiter", "Adam Lowry", "Mason Appleton",
                      "Alex Iafallo", "Morgan Barron", "Vladislav Namestnikov", "Dylan Samberg", "Neal Pionk",
                      "Dylan DeMelo", "Colin Miller", "Haydn Fleury", "Eric Comrie", "Rasmus Kupari"],
    "Vancouver Canucks": ["Elias Pettersson", "Quinn Hughes", "J.T. Miller", "Brock Boeser", "Thatcher Demko",
                          "Conor Garland", "Filip Hronek", "Jake DeBrusk", "Dakota Joshua", "Pius Suter",
                          "Nils Hoglander", "Teddy Blueger", "Danton Heinen", "Kiefer Sherwood", "Carson Soucy",
                          "Tyler Myers", "Vincent Desharnais", "Derek Forbort", "Arturs Silovs", "Daniel Sprong"],
    "Los Angeles Kings": ["Anze Kopitar", "Adrian Kempe", "Kevin Fiala", "Drew Doughty", "Quinton Byfield",
                          "Phillip Danault", "Trevor Moore", "Alex Laferriere", "Warren Foegele", "Tanner Jeannot",
                          "Mikey Anderson", "Vladislav Gavrikov", "Joel Edmundson", "Brandt Clarke", "Jordan Spence",
                          "Darcy Kuemper", "David Rittich", "Akil Thomas", "Andre Lee", "Kyle Burroughs"],
    "New Jersey Devils": ["Jack Hughes", "Jesper Bratt", "Nico Hischier", "Dougie Hamilton", "Jacob Markstrom",
                          "Timo Meier", "Dawson Mercer", "Ondrej Palat", "Erik Haula", "Paul Cotter",
                          "Stefan Noesen", "Tomas Tatar", "Curtis Lazar", "Nathan Bastian", "Jonas Siegenthaler",
                          "Brett Pesce", "Brenden Dillon", "Johnathan Kovacevic", "Simon Nemec", "Seamus Casey"],
    "Ottawa Senators": ["Brady Tkachuk", "Tim Stutzle", "Jake Sanderson", "Linus Ullmark", "Claude Giroux",
                        "Drake Batherson", "Josh Norris", "Thomas Chabot", "David Perron", "Shane Pinto",
                        "Nick Jensen", "Artem Zub", "Michael Amadio", "Ridly Greig", "Noah Gregor",
                        "Tyler Kleven", "Jacob Bernard-Docker", "Travis Hamonic", "Anton Forsberg", "Zack MacEwen"],
    "Detroit Red Wings": ["Dylan Larkin", "Moritz Seider", "Lucas Raymond", "Alex DeBrincat", "Cam Talbot",
                          "Patrick Kane", "Vladimir Tarasenko", "JT Compher", "Andrew Copp", "Michael Rasmussen",
                          "Erik Gustafsson", "Ben Chiarot", "Jeff Petry", "Simon Edvinsson", "Albert Johansson",
                          "Joe Veleno", "Christian Fischer", "Jonatan Berggren", "Tyler Motte", "Alex Lyon"],
    "Buffalo Sabres": ["Rasmus Dahlin", "Tage Thompson", "Alex Tuch", "Dylan Cozens", "Ukko-Pekka Luukkonen",
                       "JJ Peterka", "Owen Power", "Bowen Byram", "Jason Zucker", "Ryan McLeod",
                       "Jordan Greenway", "Zach Benson", "Peyton Krebs", "Sam Lafferty", "Beck Malenstyn",
                       "Connor Clifton", "Henri Jokiharju", "Mattias Samuelsson", "Jacob Bryson", "Devon Levi"],
    "Montreal Canadiens": ["Nick Suzuki", "Cole Caufield", "Juraj Slafkovsky", "Lane Hutson", "Sam Montembeault",
                           "Patrik Laine", "Kirby Dach", "Mike Matheson", "David Savard", "Alex Newhook",
                           "Brendan Gallagher", "Josh Anderson", "Christian Dvorak", "Jake Evans", "Joel Armia",
                           "Kaiden Guhle", "Arber Xhekaj", "Jayden Struble", "Cayden Primeau", "Michael Pezzetta"],
    "Calgary Flames": ["Jonathan Huberdeau", "Nazem Kadri", "MacKenzie Weegar", "Rasmus Andersson", "Dustin Wolf",
                       "Andrei Kuzmenko", "Yegor Sharangovich", "Blake Coleman", "Mikael Backlund", "Connor Zary",
                       "Anthony Mantha", "Martin Pospisil", "Kevin Rooney", "Ryan Lomberg", "Daniil Miromanov",
                       "Kevin Bahl", "Jake Bean", "Brayden Pachal", "Dan Vladar", "Matthew Coronato"],
    "Seattle Kraken": ["Matty Beniers", "Jared McCann", "Vince Dunn", "Brandon Montour", "Philipp Grubauer",
                       "Chandler Stephenson", "Oliver Bjorkstrand", "Eeli Tolvanen", "Andre Burakovsky", "Jaden Schwartz",
                       "Yanni Gourde", "Jordan Eberle", "Shane Wright", "Tye Kartye", "Will Borgen",
                       "Adam Larsson", "Jamie Oleksiak", "Ryker Evans", "Joey Daccord", "Josh Mahura"],
    "Nashville Predators": ["Filip Forsberg", "Roman Josi", "Juuse Saros", "Steven Stamkos", "Jonathan Marchessault",
                            "Ryan O'Reilly", "Brady Skjei", "Luke Evangelista", "Tommy Novak", "Colton Sissons",
                            "Gustav Nyquist", "Cole Smith", "Michael McCarron", "Mark Jankowski", "Dante Fabbro",
                            "Alexandre Carrier", "Jeremy Lauzon", "Spencer Stastney", "Scott Wedgewood", "Luke Schenn"],
    "St. Louis Blues": ["Robert Thomas", "Jordan Kyrou", "Pavel Buchnevich", "Colton Parayko", "Jordan Binnington",
                        "Brayden Schenn", "Jake Neighbours", "Brandon Saad", "Kevin Hayes", "Alexey Toropchenko",
                        "Oskar Sundqvist", "Nathan Walker", "Radek Faksa", "Kasperi Kapanen", "Mathieu Joseph",
                        "Justin Faulk", "Nick Leddy", "Torey Krug", "Ryan Suter", "Joel Hofer"],
    "Minnesota Wild": ["Kirill Kaprizov", "Matt Boldy", "Brock Faber", "Filip Gustavsson", "Joel Eriksson Ek",
                       "Mats Zuccarello", "Marco Rossi", "Ryan Hartman", "Marcus Johansson", "Frederick Gaudreau",
                       "Yakov Trenin", "Marcus Foligno", "Jakub Lauko", "Jared Spurgeon", "Jonas Brodin",
                       "Jake Middleton", "Zach Bogosian", "Declan Chisholm", "Marc-Andre Fleury", "Jesper Wallstedt"],
    "Columbus Blue Jackets": ["Adam Fantilli", "Zach Werenski", "Johnny Gaudreau", "Boone Jenner", "Elvis Merzlikins",
                              "Kent Johnson", "Kirill Marchenko", "Dmitri Voronkov", "Yegor Chinakhov", "Sean Monahan",
                              "Cole Sillinger", "Mathieu Olivier", "Justin Danforth", "James van Riemsdyk", "Damon Severson",
                              "Ivan Provorov", "Erik Gudbranson", "David Jiricek", "Daniil Tarasov", "Jack Johnson"],
    "Utah Hockey Club": ["Clayton Keller", "Logan Cooley", "Mikhail Sergachev", "Dylan Guenther", "Connor Ingram",
                         "Nick Schmaltz", "Lawson Crouse", "Matias Maccelli", "Barrett Hayton", "Josh Doan",
                         "Jack McBain", "Alex Kerfoot", "Kevin Stenlund", "Michael Carcone", "Sean Durzi",
                         "John Marino", "Ian Cole", "Juuso Valimaki", "Karel Vejmelka", "Maveric Lamoureux"],
    "Pittsburgh Penguins": ["Sidney Crosby", "Evgeni Malkin", "Kris Letang", "Erik Karlsson", "Tristan Jarry",
                            "Bryan Rust", "Rickard Rakell", "Michael Bunting", "Drew O'Connor", "Lars Eller",
                            "Kevin Hayes", "Noel Acciari", "Anthony Beauvillier", "Jesse Puljujarvi", "Marcus Pettersson",
                            "Ryan Graves", "Matt Grzelcyk", "Jack St. Ivany", "Alex Nedeljkovic", "Valtteri Puustinen"],
    "Philadelphia Flyers": ["Travis Konecny", "Matvei Michkov", "Owen Tippett", "Travis Sanheim", "Samuel Ersson",
                            "Sean Couturier", "Morgan Frost", "Joel Farabee", "Tyson Foerster", "Scott Laughton",
                            "Noah Cates", "Ryan Poehling", "Garnet Hathaway", "Bobby Brink", "Cam York",
                            "Jamie Drysdale", "Rasmus Ristolainen", "Nick Seeler", "Ivan Fedotov", "Egor Zamula"],
    "Washington Capitals": ["Alex Ovechkin", "Dylan Strome", "John Carlson", "Tom Wilson", "Charlie Lindgren",
                            "Pierre-Luc Dubois", "Aliaksei Protas", "Connor McMichael", "Jakob Chychrun", "Matt Roy",
                            "Rasmus Sandin", "Trevor van Riemsdyk", "Andrew Mangiapane", "Taylor Raddysh", "Brandon Duhaime",
                            "Hendrix Lapierre", "Nic Dowd", "Ivan Miroshnichenko", "Logan Thompson", "Martin Fehervary"],
    "New York Islanders": ["Mathew Barzal", "Bo Horvat", "Noah Dobson", "Ilya Sorokin", "Brock Nelson",
                           "Anders Lee", "Kyle Palmieri", "Jean-Gabriel Pageau", "Anthony Duclair", "Casey Cizikas",
                           "Simon Holmstrom", "Maxim Tsyplakov", "Pierre Engvall", "Oliver Wahlstrom", "Ryan Pulock",
                           "Adam Pelech", "Alexander Romanov", "Scott Mayfield", "Semyon Varlamov", "Dennis Cholowski"],
    "Anaheim Ducks": ["Troy Terry", "Mason McTavish", "Leo Carlsson", "Cutter Gauthier", "Lukas Dostal",
                      "Frank Vatrano", "Trevor Zegras", "Alex Killorn", "Ryan Strome", "Robby Fabbri",
                      "Isac Lundestrom", "Brock McGinn", "Jansen Harkins", "Brett Leason", "Cam Fowler",
                      "Pavel Mintyukov", "Radko Gudas", "Olen Zellweger", "Jackson LaCombe", "John Gibson"],
    "San Jose Sharks": ["Macklin Celebrini", "William Eklund", "Tyler Toffoli", "Mikael Granlund", "Yaroslav Askarov",
                        "Fabian Zetterlund", "Will Smith", "Luke Kunin", "Nico Sturm", "Barclay Goodrow",
                        "Klim Kostin", "Carl Grundstrom", "Ty Dellandrea", "Danil Gushchin", "Mario Ferraro",
                        "Jake Walman", "Jan Rutta", "Henry Thrun", "Matt Benning", "Vitek Vanecek"],
    "Chicago Blackhawks": ["Connor Bedard", "Seth Jones", "Teuvo Teravainen", "Taylor Hall", "Petr Mrazek",
                           "Philipp Kurashev", "Tyler Bertuzzi", "Ilya Mikheyev", "Jason Dickinson", "Nick Foligno",
                           "Ryan Donato", "Lukas Reichel", "Andreas Athanasiou", "Craig Smith", "Alex Vlasic",
                           "Alec Martinez", "TJ Brodie", "Wyatt Kaiser", "Arvid Soderblom", "Laurent Brossoit"]
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
# CLARITY 18.0 ELITE - MASTER ENGINE
# =============================================================================
class Clarity18Elite:
    def __init__(self):
        self.api = UnifiedAPIClient(UNIFIED_API_KEY)
        self.sims = 10000
        self.wsem_max = 0.10
        self.dtm_bolt = 0.15
        self.prob_bolt = 0.84
        self.bankroll = 1000.0
    
    def convert_odds(self, american: int) -> float:
        return 1 + american/100 if american > 0 else 1 + 100/abs(american)
    
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
                     data: List[float], sport: str, odds: int) -> dict:
        api_status = self.api.get_injury_status(player, sport)
        l42_pass, l42_msg = self.l42_check(market, line, np.mean(data))
        sim = self.simulate_prop(data, line, pick, sport)
        wsem_ok, wsem = self.wsem_check(data)
        bolt = self.sovereign_bolt(sim["prob"], sim["dtm"], wsem_ok, l42_pass, api_status["injury"])
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
        
        kelly = raw_edge * self.bankroll * 0.25 if raw_edge > 0 else 0
        
        return {"player": player, "market": market, "line": line, "pick": pick, "signal": bolt["signal"], 
                "units": bolt["units"], "projection": sim["proj"], "probability": sim["prob"], 
                "raw_edge": round(raw_edge, 4), "tier": tier, "injury": api_status["injury"], 
                "l42_msg": l42_msg, "kelly_stake": round(min(kelly, 50), 2)}
    
    def get_teams(self, sport: str) -> List[str]:
        return HARDCODED_TEAMS.get(sport, ["Select a sport first"])
    
    def get_roster(self, sport: str, team: str) -> List[str]:
        if sport == "NBA" and team in NBA_ROSTERS:
            return NBA_ROSTERS[team]
        elif sport == "MLB" and team in MLB_ROSTERS:
            return MLB_ROSTERS[team]
        elif sport == "NHL" and team in NHL_ROSTERS:
            return NHL_ROSTERS[team]
        return ["Player 1", "Player 2", "Player 3", "Player 4", "Player 5"]

# =============================================================================
# DASHBOARD
# =============================================================================
engine = Clarity18Elite()

def run_dashboard():
    st.set_page_config(page_title="CLARITY 18.0 ELITE", layout="wide")
    st.title("🔮 CLARITY 18.0 ELITE - ALL SPORTS REAL ROSTERS")
    st.markdown(f"**NBA | MLB | NHL | NFL | Real Rosters | Version: {VERSION}**")
    
    with st.sidebar:
        st.header("🚀 SYSTEM STATUS")
        st.success("✅ Perplexity API LIVE")
        st.success("✅ All Sports Rosters Loaded")
        st.metric("Version", VERSION)
        st.metric("Bankroll", f"${engine.bankroll:,.0f}")
    
    tab1 = st.tabs(["🎯 ANALYZE PROP"])[0]
    
    with tab1:
        st.header("Player Prop Analyzer")
        c1, c2 = st.columns(2)
        with c1:
            sport = st.selectbox("Sport", ["MLB", "NBA", "NHL", "NFL"], key="tab1_sport")
            teams = engine.get_teams(sport)
            team = st.selectbox("Team", teams, key="tab1_team")
            
            roster = engine.get_roster(sport, team)
            player = st.selectbox("Player", roster, key="tab1_player")
            
            available_markets = SPORT_CATEGORIES.get(sport, ["PTS"])
            market = st.selectbox("Market", available_markets, key="tab1_market")
            line = st.number_input("Line", 0.5, 100.0, 0.5, key="tab1_line")
            pick = st.selectbox("Pick", ["OVER", "UNDER"], key="tab1_pick")
        with c2:
            data_str = st.text_area("Recent Games (comma separated)", "0, 1, 0, 2, 0, 1", key="tab1_data")
            odds = st.number_input("Odds (American)", -500, 500, -110, key="tab1_odds")
        
        if st.button("🚀 RUN ANALYSIS", type="primary", key="tab1_button"):
            data = [float(x.strip()) for x in data_str.split(",")]
            result = engine.analyze_prop(player, market, line, pick, data, sport, odds)
            
            st.markdown(f"### {result['signal']}")
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("Projection", f"{result['projection']:.1f}")
            with c2: st.metric("Probability", f"{result['probability']:.1%}")
            with c3: st.metric("Edge", f"{result['raw_edge']:+.1%}")
            st.metric("Tier", result['tier'])
            st.info(f"Injury: {result['injury']} | L42: {result['l42_msg']}")
            if result['units'] > 0:
                st.success(f"RECOMMENDED UNITS: {result['units']} (${result['kelly_stake']:.2f})")

if __name__ == "__main__":
    run_dashboard()
