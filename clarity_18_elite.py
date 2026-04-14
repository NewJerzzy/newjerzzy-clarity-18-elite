Here’s **Clarity 18.2 Elite** — rebuilt, patched, and ready for a **single copy‑paste** over `clarity_18_elite.py`.

```python
"""
CLARITY 18.2 ELITE - COMPLETE SYSTEM (FULL ROSTERS) - REBUILT & PATCHED
Player Props | Moneylines | Spreads | Totals | Alternate Lines
NBA | MLB | NHL | NFL - ALL TEAMS HAVE REAL PLAYERS
API KEYS: Perplexity + API-Sports + The Odds API
"""

import numpy as np
import pandas as pd
from scipy.stats import poisson, nbinom, norm
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

warnings.filterwarnings("ignore")

# =============================================================================
# CONFIGURATION - API KEYS
# =============================================================================
UNIFIED_API_KEY = "96241c1a5ba686f34a9e4c3463b61661"
API_SPORTS_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"
ODDS_API_KEY = "8c20c34c3b0a6314e04c4997bf0922d2"

VERSION = "18.2 Elite (Rebuilt - Live Data)"
BUILD_DATE = "2026-04-13"

PERPLEXITY_BASE = "https://api.perplexity.ai"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
API_SPORTS_BASE = "https://v1.api-sports.io"

# =============================================================================
# SPORT-SPECIFIC DISTRIBUTIONS & SETTINGS
# =============================================================================
SPORT_MODELS: Dict[str, Dict[str, Any]] = {
    "NBA": {
        "distribution": "nbinom",
        "variance_factor": 1.15,
        "avg_total": 228.5,
        "home_advantage": 3.0,
        "max_total": 300.0,
        "spread_std": 12.0,
    },
    "MLB": {
        "distribution": "poisson",
        "variance_factor": 1.08,
        "avg_total": 8.5,
        "home_advantage": 0.12,
        "max_total": 20.0,
        "spread_std": 4.5,
    },
    "NHL": {
        "distribution": "poisson",
        "variance_factor": 1.12,
        "avg_total": 6.0,
        "home_advantage": 0.15,
        "max_total": 10.0,
        "spread_std": 2.8,
    },
    "NFL": {
        "distribution": "nbinom",
        "variance_factor": 1.20,
        "avg_total": 44.5,
        "home_advantage": 2.8,
        "max_total": 80.0,
        "spread_std": 14.0,
    },
}

# =============================================================================
# SPORT-SPECIFIC CATEGORIES & API-SPORTS MAPPINGS
# =============================================================================
SPORT_CATEGORIES: Dict[str, List[str]] = {
    "NBA": ["PTS", "REB", "AST", "STL", "BLK", "THREES", "PRA", "PR", "PA"],
    "MLB": ["OUTS", "KS", "HITS", "TB", "HR", "RBI", "H+R+RBI", "HITTER_FS", "PITCHER_FS"],
    "NHL": ["SOG", "SAVES", "GOALS", "ASSISTS", "HITS", "BLK_SHOTS"],
    "NFL": ["PASS_YDS", "PASS_TD", "RUSH_YDS", "RUSH_TD", "REC_YDS", "REC", "TD"],
}

API_SPORT_KEYS: Dict[str, str] = {
    "NBA": "basketball",
    "MLB": "baseball",
    "NHL": "hockey",
    "NFL": "american-football",
}
API_LEAGUE_IDS: Dict[str, int] = {"NBA": 12, "MLB": 1, "NHL": 57, "NFL": 1}

STAT_MAPPING: Dict[str, Dict[str, str]] = {
    "NBA": {"PTS": "points", "REB": "totReb", "AST": "assists", "STL": "steals", "BLK": "blocks", "THREES": "tpm"},
    "MLB": {"HITS": "hits", "HR": "homeRuns", "RBI": "rbi", "TB": "totalBases", "KS": "strikeOuts"},
    "NHL": {"SOG": "shots", "GOALS": "goals", "ASSISTS": "assists", "HITS": "hits"},
    "NFL": {
        "PASS_YDS": "passingYards",
        "PASS_TD": "passingTDs",
        "RUSH_YDS": "rushingYards",
        "RUSH_TD": "rushingTDs",
        "REC_YDS": "receivingYards",
        "REC": "receptions",
    },
}

STAT_CONFIG: Dict[str, Dict[str, Any]] = {
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
# API-SPORTS TEAM IDS (COARSE MAPPING)
# =============================================================================
API_TEAM_IDS: Dict[str, Dict[str, int]] = {
    "NBA": {
        "Atlanta Hawks": 1,
        "Boston Celtics": 2,
        "Brooklyn Nets": 4,
        "Charlotte Hornets": 5,
        "Chicago Bulls": 6,
        "Cleveland Cavaliers": 7,
        "Dallas Mavericks": 8,
        "Denver Nuggets": 9,
        "Detroit Pistons": 10,
        "Golden State Warriors": 11,
        "Houston Rockets": 14,
        "Indiana Pacers": 15,
        "LA Clippers": 16,
        "Los Angeles Lakers": 17,
        "Memphis Grizzlies": 19,
        "Miami Heat": 20,
        "Milwaukee Bucks": 21,
        "Minnesota Timberwolves": 22,
        "New Orleans Pelicans": 23,
        "New York Knicks": 24,
        "Oklahoma City Thunder": 25,
        "Orlando Magic": 26,
        "Philadelphia 76ers": 27,
        "Phoenix Suns": 28,
        "Portland Trail Blazers": 29,
        "Sacramento Kings": 30,
        "San Antonio Spurs": 31,
        "Toronto Raptors": 38,
        "Utah Jazz": 40,
        "Washington Wizards": 41,
    },
    "MLB": {
        "Arizona Diamondbacks": 1,
        "Atlanta Braves": 2,
        "Baltimore Orioles": 3,
        "Boston Red Sox": 4,
        "Chicago Cubs": 5,
        "Chicago White Sox": 6,
        "Cincinnati Reds": 7,
        "Cleveland Guardians": 8,
        "Colorado Rockies": 9,
        "Detroit Tigers": 10,
        "Houston Astros": 11,
        "Kansas City Royals": 12,
        "Los Angeles Angels": 13,
        "Los Angeles Dodgers": 14,
        "Miami Marlins": 15,
        "Milwaukee Brewers": 16,
        "Minnesota Twins": 17,
        "New York Mets": 18,
        "New York Yankees": 19,
        "Oakland Athletics": 20,
        "Philadelphia Phillies": 21,
        "Pittsburgh Pirates": 22,
        "San Diego Padres": 23,
        "San Francisco Giants": 24,
        "Seattle Mariners": 25,
        "St. Louis Cardinals": 26,
        "Tampa Bay Rays": 27,
        "Texas Rangers": 28,
        "Toronto Blue Jays": 29,
        "Washington Nationals": 30,
    },
    "NHL": {
        "Anaheim Ducks": 1,
        "Boston Bruins": 2,
        "Buffalo Sabres": 3,
        "Calgary Flames": 4,
        "Carolina Hurricanes": 5,
        "Chicago Blackhawks": 6,
        "Colorado Avalanche": 7,
        "Columbus Blue Jackets": 8,
        "Dallas Stars": 9,
        "Detroit Red Wings": 10,
        "Edmonton Oilers": 11,
        "Florida Panthers": 12,
        "Los Angeles Kings": 13,
        "Minnesota Wild": 14,
        "Montreal Canadiens": 15,
        "Nashville Predators": 16,
        "New Jersey Devils": 17,
        "New York Islanders": 18,
        "New York Rangers": 19,
        "Ottawa Senators": 20,
        "Philadelphia Flyers": 21,
        "Pittsburgh Penguins": 22,
        "San Jose Sharks": 23,
        "Seattle Kraken": 24,
        "St. Louis Blues": 25,
        "Tampa Bay Lightning": 26,
        "Toronto Maple Leafs": 27,
        "Utah Hockey Club": 28,
        "Vancouver Canucks": 29,
        "Vegas Golden Knights": 30,
        "Washington Capitals": 31,
        "Winnipeg Jets": 32,
    },
    "NFL": {
        "Arizona Cardinals": 1,
        "Atlanta Falcons": 2,
        "Baltimore Ravens": 3,
        "Buffalo Bills": 4,
        "Carolina Panthers": 5,
        "Chicago Bears": 6,
        "Cincinnati Bengals": 7,
        "Cleveland Browns": 8,
        "Dallas Cowboys": 9,
        "Denver Broncos": 10,
        "Detroit Lions": 11,
        "Green Bay Packers": 12,
        "Houston Texans": 13,
        "Indianapolis Colts": 14,
        "Jacksonville Jaguars": 15,
        "Kansas City Chiefs": 16,
        "Las Vegas Raiders": 17,
        "Los Angeles Chargers": 18,
        "Los Angeles Rams": 19,
        "Miami Dolphins": 20,
        "Minnesota Vikings": 21,
        "New England Patriots": 22,
        "New Orleans Saints": 23,
        "New York Giants": 24,
        "New York Jets": 25,
        "Philadelphia Eagles": 26,
        "Pittsburgh Steelers": 27,
        "San Francisco 49ers": 28,
        "Seattle Seahawks": 29,
        "Tampa Bay Buccaneers": 30,
        "Tennessee Titans": 31,
        "Washington Commanders": 32,
    },
}

# =============================================================================
# HARDCODED TEAMS & ROSTERS (FULL)
# =============================================================================
HARDCODED_TEAMS = {
    "NBA": [
        "Atlanta Hawks",
        "Boston Celtics",
        "Brooklyn Nets",
        "Charlotte Hornets",
        "Chicago Bulls",
        "Cleveland Cavaliers",
        "Dallas Mavericks",
        "Denver Nuggets",
        "Detroit Pistons",
        "Golden State Warriors",
        "Houston Rockets",
        "Indiana Pacers",
        "LA Clippers",
        "Los Angeles Lakers",
        "Memphis Grizzlies",
        "Miami Heat",
        "Milwaukee Bucks",
        "Minnesota Timberwolves",
        "New Orleans Pelicans",
        "New York Knicks",
        "Oklahoma City Thunder",
        "Orlando Magic",
        "Philadelphia 76ers",
        "Phoenix Suns",
        "Portland Trail Blazers",
        "Sacramento Kings",
        "San Antonio Spurs",
        "Toronto Raptors",
        "Utah Jazz",
        "Washington Wizards",
    ],
    "MLB": [
        "Arizona Diamondbacks",
        "Atlanta Braves",
        "Baltimore Orioles",
        "Boston Red Sox",
        "Chicago Cubs",
        "Chicago White Sox",
        "Cincinnati Reds",
        "Cleveland Guardians",
        "Colorado Rockies",
        "Detroit Tigers",
        "Houston Astros",
        "Kansas City Royals",
        "Los Angeles Angels",
        "Los Angeles Dodgers",
        "Miami Marlins",
        "Milwaukee Brewers",
        "Minnesota Twins",
        "New York Mets",
        "New York Yankees",
        "Oakland Athletics",
        "Philadelphia Phillies",
        "Pittsburgh Pirates",
        "San Diego Padres",
        "San Francisco Giants",
        "Seattle Mariners",
        "St. Louis Cardinals",
        "Tampa Bay Rays",
        "Texas Rangers",
        "Toronto Blue Jays",
        "Washington Nationals",
    ],
    "NHL": [
        "Anaheim Ducks",
        "Boston Bruins",
        "Buffalo Sabres",
        "Calgary Flames",
        "Carolina Hurricanes",
        "Chicago Blackhawks",
        "Colorado Avalanche",
        "Columbus Blue Jackets",
        "Dallas Stars",
        "Detroit Red Wings",
        "Edmonton Oilers",
        "Florida Panthers",
        "Los Angeles Kings",
        "Minnesota Wild",
        "Montreal Canadiens",
        "Nashville Predators",
        "New Jersey Devils",
        "New York Islanders",
        "New York Rangers",
        "Ottawa Senators",
        "Philadelphia Flyers",
        "Pittsburgh Penguins",
        "San Jose Sharks",
        "Seattle Kraken",
        "St. Louis Blues",
        "Tampa Bay Lightning",
        "Toronto Maple Leafs",
        "Utah Hockey Club",
        "Vancouver Canucks",
        "Vegas Golden Knights",
        "Washington Capitals",
        "Winnipeg Jets",
    ],
    "NFL": [
        "Arizona Cardinals",
        "Atlanta Falcons",
        "Baltimore Ravens",
        "Buffalo Bills",
        "Carolina Panthers",
        "Chicago Bears",
        "Cincinnati Bengals",
        "Cleveland Browns",
        "Dallas Cowboys",
        "Denver Broncos",
        "Detroit Lions",
        "Green Bay Packers",
        "Houston Texans",
        "Indianapolis Colts",
        "Jacksonville Jaguars",
        "Kansas City Chiefs",
        "Las Vegas Raiders",
        "Los Angeles Chargers",
        "Los Angeles Rams",
        "Miami Dolphins",
        "Minnesota Vikings",
        "New England Patriots",
        "New Orleans Saints",
        "New York Giants",
        "New York Jets",
        "Philadelphia Eagles",
        "Pittsburgh Steelers",
        "San Francisco 49ers",
        "Seattle Seahawks",
        "Tampa Bay Buccaneers",
        "Tennessee Titans",
        "Washington Commanders",
    ],
}

NBA_ROSTERS = {
    "Atlanta Hawks": [
        "Trae Young",
        "Jalen Johnson",
        "Dyson Daniels",
        "Onyeka Okongwu",
        "Zaccharie Risacher",
        "Bogdan Bogdanovic",
        "De'Andre Hunter",
        "Clint Capela",
    ],
    "Boston Celtics": [
        "Jayson Tatum",
        "Jaylen Brown",
        "Kristaps Porzingis",
        "Jrue Holiday",
        "Derrick White",
        "Al Horford",
        "Payton Pritchard",
        "Sam Hauser",
    ],
    "Brooklyn Nets": [
        "Cameron Johnson",
        "Nic Claxton",
        "Cam Thomas",
        "Noah Clowney",
        "Dorian Finney-Smith",
        "Dennis Schroder",
        "Bojan Bogdanovic",
        "Day'Ron Sharpe",
    ],
    "Charlotte Hornets": [
        "LaMelo Ball",
        "Brandon Miller",
        "Mark Williams",
        "Miles Bridges",
        "Josh Green",
        "Grant Williams",
        "Cody Martin",
        "Nick Richards",
    ],
    "Chicago Bulls": [
        "Coby White",
        "Nikola Vucevic",
        "Josh Giddey",
        "Patrick Williams",
        "Ayo Dosunmu",
        "Zach LaVine",
        "Lonzo Ball",
        "Jalen Smith",
    ],
    "Cleveland Cavaliers": [
        "Donovan Mitchell",
        "Darius Garland",
        "Evan Mobley",
        "Jarrett Allen",
        "Max Strus",
        "Caris LeVert",
        "Isaac Okoro",
        "Georges Niang",
    ],
    "Dallas Mavericks": [
        "Luka Doncic",
        "Kyrie Irving",
        "Klay Thompson",
        "PJ Washington",
        "Daniel Gafford",
        "Dereck Lively II",
        "Naji Marshall",
        "Quentin Grimes",
    ],
    "Denver Nuggets": [
        "Nikola Jokic",
        "Jamal Murray",
        "Michael Porter Jr",
        "Aaron Gordon",
        "Christian Braun",
        "Russell Westbrook",
        "Peyton Watson",
        "Dario Saric",
    ],
    "Detroit Pistons": [
        "Cade Cunningham",
        "Jaden Ivey",
        "Ausar Thompson",
        "Jalen Duren",
        "Isaiah Stewart",
        "Tim Hardaway Jr",
        "Malik Beasley",
        "Tobias Harris",
    ],
    "Golden State Warriors": [
        "Stephen Curry",
        "Draymond Green",
        "Andrew Wiggins",
        "Jonathan Kuminga",
        "Brandin Podziemski",
        "Buddy Hield",
        "Kevon Looney",
        "Gary Payton II",
    ],
    "Houston Rockets": [
        "Alperen Sengun",
        "Jalen Green",
        "Fred VanVleet",
        "Jabari Smith Jr",
        "Dillon Brooks",
        "Amen Thompson",
        "Tari Eason",
        "Cam Whitmore",
    ],
    "Indiana Pacers": [
        "Tyrese Haliburton",
        "Pascal Siakam",
        "Myles Turner",
        "Bennedict Mathurin",
        "Andrew Nembhard",
        "TJ McConnell",
        "Aaron Nesmith",
        "Obi Toppin",
    ],
    "LA Clippers": [
        "Kawhi Leonard",
        "James Harden",
        "Norman Powell",
        "Ivica Zubac",
        "Derrick Jones Jr",
        "Terance Mann",
        "Nicolas Batum",
        "Kris Dunn",
    ],
    "Los Angeles Lakers": [
        "LeBron James",
        "Anthony Davis",
        "Austin Reaves",
        "D'Angelo Russell",
        "Rui Hachimura",
        "Jarred Vanderbilt",
        "Gabe Vincent",
        "Max Christie",
    ],
    "Memphis Grizzlies": [
        "Ja Morant",
        "Desmond Bane",
        "Jaren Jackson Jr",
        "Marcus Smart",
        "Zach Edey",
        "Brandon Clarke",
        "Santi Aldama",
        "Luke Kennard",
    ],
    "Miami Heat": [
        "Jimmy Butler",
        "Bam Adebayo",
        "Tyler Herro",
        "Terry Rozier",
        "Jaime Jaquez Jr",
        "Duncan Robinson",
        "Nikola Jovic",
        "Haywood Highsmith",
    ],
    "Milwaukee Bucks": [
        "Giannis Antetokounmpo",
        "Damian Lillard",
        "Khris Middleton",
        "Brook Lopez",
        "Bobby Portis",
        "Gary Trent Jr",
        "Taurean Prince",
        "Delon Wright",
    ],
    "Minnesota Timberwolves": [
        "Anthony Edwards",
        "Karl-Anthony Towns",
        "Rudy Gobert",
        "Jaden McDaniels",
        "Mike Conley",
        "Naz Reid",
        "Donte DiVincenzo",
        "Nickeil Alexander-Walker",
    ],
    "New Orleans Pelicans": [
        "Zion Williamson",
        "Brandon Ingram",
        "CJ McCollum",
        "Dejounte Murray",
        "Herb Jones",
        "Trey Murphy III",
        "Jonas Valanciunas",
        "Jose Alvarado",
    ],
    "New York Knicks": [
        "Jalen Brunson",
        "Julius Randle",
        "Mikal Bridges",
        "OG Anunoby",
        "Mitchell Robinson",
        "Donte DiVincenzo",
        "Josh Hart",
        "Miles McBride",
    ],
    "Oklahoma City Thunder": [
        "Shai Gilgeous-Alexander",
        "Chet Holmgren",
        "Jalen Williams",
        "Luguentz Dort",
        "Isaiah Hartenstein",
        "Alex Caruso",
        "Cason Wallace",
        "Isaiah Joe",
    ],
    "Orlando Magic": [
        "Paolo Banchero",
        "Franz Wagner",
        "Jalen Suggs",
        "Kentavious Caldwell-Pope",
        "Wendell Carter Jr",
        "Cole Anthony",
        "Jonathan Isaac",
        "Moritz Wagner",
    ],
    "Philadelphia 76ers": [
        "Joel Embiid",
        "Tyrese Maxey",
        "Paul George",
        "Caleb Martin",
        "Kelly Oubre Jr",
        "Andre Drummond",
        "Eric Gordon",
        "Kyle Lowry",
    ],
    "Phoenix Suns": [
        "Kevin Durant",
        "Devin Booker",
        "Bradley Beal",
        "Jusuf Nurkic",
        "Grayson Allen",
        "Royce O'Neale",
        "Mason Plumlee",
        "Monte Morris",
    ],
    "Portland Trail Blazers": [
        "Scoot Henderson",
        "Anfernee Simons",
        "Shaedon Sharpe",
        "Jerami Grant",
        "Deandre Ayton",
        "Deni Avdija",
        "Donovan Clingan",
        "Toumani Camara",
    ],
    "Sacramento Kings": [
        "De'Aaron Fox",
        "Domantas Sabonis",
        "DeMar DeRozan",
        "Keegan Murray",
        "Malik Monk",
        "Kevin Huerter",
        "Trey Lyles",
        "Keon Ellis",
    ],
    "San Antonio Spurs": [
        "Victor Wembanyama",
        "Devin Vassell",
        "Keldon Johnson",
        "Jeremy Sochan",
        "Chris Paul",
        "Harrison Barnes",
        "Zach Collins",
        "Tre Jones",
    ],
    "Toronto Raptors": [
        "Scottie Barnes",
        "Immanuel Quickley",
        "RJ Barrett",
        "Jakob Poeltl",
        "Gradey Dick",
        "Kelly Olynyk",
        "Bruce Brown",
        "Chris Boucher",
    ],
    "Utah Jazz": [
        "Lauri Markkanen",
        "Collin Sexton",
        "John Collins",
        "Jordan Clarkson",
        "Keyonte George",
        "Walker Kessler",
        "Taylor Hendricks",
        "Cody Williams",
    ],
    "Washington Wizards": [
        "Jordan Poole",
        "Kyle Kuzma",
        "Bilal Coulibaly",
        "Jonas Valanciunas",
        "Malcolm Brogdon",
        "Corey Kispert",
        "Marvin Bagley III",
        "Saddiq Bey",
    ],
}

MLB_ROSTERS = {
    "Arizona Diamondbacks": [
        "Corbin Carroll",
        "Ketel Marte",
        "Zac Gallen",
        "Merrill Kelly",
        "Eduardo Rodriguez",
        "Christian Walker",
        "Gabriel Moreno",
        "Lourdes Gurriel Jr",
    ],
    "Atlanta Braves": [
        "Ronald Acuna Jr",
        "Matt Olson",
        "Austin Riley",
        "Ozzie Albies",
        "Michael Harris II",
        "Sean Murphy",
        "Marcell Ozuna",
        "Spencer Strider",
    ],
    "Baltimore Orioles": [
        "Adley Rutschman",
        "Gunnar Henderson",
        "Jackson Holliday",
        "Cedric Mullins",
        "Anthony Santander",
        "Ryan Mountcastle",
        "Corbin Burnes",
        "Grayson Rodriguez",
    ],
    "Boston Red Sox": [
        "Rafael Devers",
        "Trevor Story",
        "Masataka Yoshida",
        "Triston Casas",
        "Jarren Duran",
        "Tyler O'Neill",
        "Brayan Bello",
        "Lucas Giolito",
    ],
    "Chicago Cubs": [
        "Cody Bellinger",
        "Dansby Swanson",
        "Ian Happ",
        "Seiya Suzuki",
        "Nico Hoerner",
        "Christopher Morel",
        "Justin Steele",
        "Shota Imanaga",
    ],
    "Chicago White Sox": [
        "Luis Robert Jr",
        "Eloy Jimenez",
        "Andrew Vaughn",
        "Yoan Moncada",
        "Andrew Benintendi",
        "Nicky Lopez",
        "Dylan Cease",
        "Michael Kopech",
    ],
    "Cincinnati Reds": [
        "Elly De La Cruz",
        "Spencer Steer",
        "Matt McLain",
        "Jeimer Candelario",
        "TJ Friedl",
        "Will Benson",
        "Hunter Greene",
        "Frankie Montas",
    ],
    "Cleveland Guardians": [
        "Jose Ramirez",
        "Andres Gimenez",
        "Josh Naylor",
        "Steven Kwan",
        "Bo Naylor",
        "Brayan Rocchio",
        "Shane Bieber",
        "Triston McKenzie",
    ],
    "Colorado Rockies": [
        "Nolan Jones",
        "Ezequiel Tovar",
        "Brenton Doyle",
        "Kris Bryant",
        "Ryan McMahon",
        "Elias Diaz",
        "Kyle Freeland",
        "Cal Quantrill",
    ],
    "Detroit Tigers": [
        "Spencer Torkelson",
        "Riley Greene",
        "Kerry Carpenter",
        "Javier Baez",
        "Colt Keith",
        "Parker Meadows",
        "Tarik Skubal",
        "Jack Flaherty",
    ],
    "Houston Astros": [
        "Jose Altuve",
        "Yordan Alvarez",
        "Alex Bregman",
        "Kyle Tucker",
        "Jeremy Pena",
        "Yainer Diaz",
        "Framber Valdez",
        "Cristian Javier",
    ],
    "Kansas City Royals": [
        "Bobby Witt Jr",
        "Vinnie Pasquantino",
        "Salvador Perez",
        "Cole Ragans",
        "Seth Lugo",
        "Michael Wacha",
        "MJ Melendez",
        "Maikel Garcia",
    ],
    "Los Angeles Angels": [
        "Mike Trout",
        "Anthony Rendon",
        "Taylor Ward",
        "Logan O'Hoppe",
        "Nolan Schanuel",
        "Zach Neto",
        "Reid Detmers",
        "Patrick Sandoval",
    ],
    "Los Angeles Dodgers": [
        "Shohei Ohtani",
        "Mookie Betts",
        "Freddie Freeman",
        "Yoshinobu Yamamoto",
        "Will Smith",
        "Max Muncy",
        "Teoscar Hernandez",
        "Tyler Glasnow",
    ],
    "Miami Marlins": [
        "Luis Arraez",
        "Jazz Chisholm Jr",
        "Josh Bell",
        "Jake Burger",
        "Jesus Sanchez",
        "Bryan De La Cruz",
        "Jesus Luzardo",
        "Eury Perez",
    ],
    "Milwaukee Brewers": [
        "Christian Yelich",
        "Willy Adames",
        "William Contreras",
        "Rhys Hoskins",
        "Jackson Chourio",
        "Sal Frelick",
        "Freddy Peralta",
        "Brandon Woodruff",
    ],
    "Minnesota Twins": [
        "Carlos Correa",
        "Royce Lewis",
        "Byron Buxton",
        "Pablo Lopez",
        "Joe Ryan",
        "Bailey Ober",
        "Edouard Julien",
        "Alex Kirilloff",
    ],
    "New York Mets": [
        "Pete Alonso",
        "Francisco Lindor",
        "Brandon Nimmo",
        "Kodai Senga",
        "Edwin Diaz",
        "Jeff McNeil",
        "Starling Marte",
        "Francisco Alvarez",
    ],
    "New York Yankees": [
        "Aaron Judge",
        "Juan Soto",
        "Giancarlo Stanton",
        "Gerrit Cole",
        "Anthony Volpe",
        "Gleyber Torres",
        "DJ LeMahieu",
        "Carlos Rodon",
    ],
    "Oakland Athletics": [
        "Zack Gelof",
        "Esteury Ruiz",
        "Brent Rooker",
        "Seth Brown",
        "JJ Bleday",
        "Shea Langeliers",
        "JP Sears",
        "Paul Blackburn",
    ],
    "Philadelphia Phillies": [
        "Bryce Harper",
        "Trea Turner",
        "Kyle Schwarber",
        "JT Realmuto",
        "Nick Castellanos",
        "Bryson Stott",
        "Zack Wheeler",
        "Aaron Nola",
    ],
    "Pittsburgh Pirates": [
        "Oneil Cruz",
        "Ke'Bryan Hayes",
        "Bryan Reynolds",
        "Jack Suwinski",
        "Henry Davis",
        "Jared Triolo",
        "Mitch Keller",
        "Martin Perez",
    ],
    "San Diego Padres": [
        "Fernando Tatis Jr",
        "Manny Machado",
        "Xander Bogaerts",
        "Yu Darvish",
        "Joe Musgrove",
        "Jake Cronenworth",
        "Ha-Seong Kim",
        "Luis Campusano",
    ],
    "San Francisco Giants": [
        "Jung Hoo Lee",
        "Matt Chapman",
        "Jorge Soler",
        "Logan Webb",
        "Blake Snell",
        "Kyle Harrison",
        "Patrick Bailey",
        "Thairo Estrada",
    ],
    "Seattle Mariners": [
        "Julio Rodriguez",
        "Cal Raleigh",
        "JP Crawford",
        "Mitch Garver",
        "Mitch Haniger",
        "Ty France",
        "Luis Castillo",
        "George Kirby",
    ],
    "St. Louis Cardinals": [
        "Paul Goldschmidt",
        "Nolan Arenado",
        "Willson Contreras",
        "Jordan Walker",
        "Masyn Winn",
        "Lars Nootbaar",
        "Sonny Gray",
        "Miles Mikolas",
    ],
    "Tampa Bay Rays": [
        "Yandy Diaz",
        "Randy Arozarena",
        "Brandon Lowe",
        "Isaac Paredes",
        "Josh Lowe",
        "Jose Siri",
        "Zach Eflin",
        "Aaron Civale",
    ],
    "Texas Rangers": [
        "Corey Seager",
        "Marcus Semien",
        "Adolis Garcia",
        "Josh Jung",
        "Evan Carter",
        "Wyatt Langford",
        "Jacob deGrom",
        "Max Scherzer",
    ],
    "Toronto Blue Jays": [
        "Vladimir Guerrero Jr",
        "Bo Bichette",
        "George Springer",
        "Kevin Gausman",
        "Jose Berrios",
        "Chris Bassitt",
        "Daulton Varsho",
        "Alejandro Kirk",
    ],
    "Washington Nationals": [
        "CJ Abrams",
        "Lane Thomas",
        "Keibert Ruiz",
        "Joey Meneses",
        "Jesse Winker",
        "Joey Gallo",
        "Josiah Gray",
        "MacKenzie Gore",
    ],
}

NHL_ROSTERS = {
    "Anaheim Ducks": [
        "Troy Terry",
        "Mason McTavish",
        "Leo Carlsson",
        "Cutter Gauthier",
        "Frank Vatrano",
        "Trevor Zegras",
        "Alex Killorn",
        "Lukas Dostal",
    ],
    "Boston Bruins": [
        "David Pastrnak",
        "Brad Marchand",
        "Charlie McAvoy",
        "Jeremy Swayman",
        "Pavel Zacha",
        "Charlie Coyle",
        "Hampus Lindholm",
        "Jake DeBrusk",
    ],
    "Buffalo Sabres": [
        "Rasmus Dahlin",
        "Tage Thompson",
        "Alex Tuch",
        "Dylan Cozens",
        "JJ Peterka",
        "Owen Power",
        "Bowen Byram",
        "Ukko-Pekka Luukkonen",
    ],
    "Calgary Flames": [
        "Jonathan Huberdeau",
        "Nazem Kadri",
        "MacKenzie Weegar",
        "Rasmus Andersson",
        "Andrei Kuzmenko",
        "Yegor Sharangovich",
        "Blake Coleman",
        "Dustin Wolf",
    ],
    "Carolina Hurricanes": [
        "Sebastian Aho",
        "Andrei Svechnikov",
        "Seth Jarvis",
        "Jaccob Slavin",
        "Brent Burns",
        "Martin Necas",
        "Jordan Staal",
        "Dmitry Orlov",
    ],
    "Chicago Blackhawks": [
        "Connor Bedard",
        "Seth Jones",
        "Teuvo Teravainen",
        "Taylor Hall",
        "Philipp Kurashev",
        "Tyler Bertuzzi",
        "Ilya Mikheyev",
        "Petr Mrazek",
    ],
    "Colorado Avalanche": [
        "Nathan MacKinnon",
        "Cale Makar",
        "Mikko Rantanen",
        "Devon Toews",
        "Artturi Lehkonen",
        "Jonathan Drouin",
        "Casey Mittelstadt",
        "Alexandar Georgiev",
    ],
    "Columbus Blue Jackets": [
        "Adam Fantilli",
        "Zach Werenski",
        "Johnny Gaudreau",
        "Boone Jenner",
        "Kent Johnson",
        "Kirill Marchenko",
        "Dmitri Voronkov",
        "Elvis Merzlikins",
    ],
    "Dallas Stars": [
        "Jason Robertson",
        "Roope Hintz",
        "Miro Heiskanen",
        "Wyatt Johnston",
        "Matt Duchene",
        "Jamie Benn",
        "Tyler Seguin",
        "Jake Oettinger",
    ],
    "Detroit Red Wings": [
        "Dylan Larkin",
        "Moritz Seider",
        "Lucas Raymond",
        "Alex DeBrincat",
        "Patrick Kane",
        "Vladimir Tarasenko",
        "JT Compher",
        "Cam Talbot",
    ],
    "Edmonton Oilers": [
        "Connor McDavid",
        "Leon Draisaitl",
        "Evan Bouchard",
        "Zach Hyman",
        "Ryan Nugent-Hopkins",
        "Mattias Ekholm",
        "Darnell Nurse",
        "Stuart Skinner",
    ],
    "Florida Panthers": [
        "Matthew Tkachuk",
        "Aleksander Barkov",
        "Sam Reinhart",
        "Carter Verhaeghe",
        "Sam Bennett",
        "Gustav Forsling",
        "Aaron Ekblad",
        "Sergei Bobrovsky",
    ],
    "Los Angeles Kings": [
        "Anze Kopitar",
        "Adrian Kempe",
        "Kevin Fiala",
        "Drew Doughty",
        "Quinton Byfield",
        "Phillip Danault",
        "Trevor Moore",
        "Darcy Kuemper",
    ],
    "Minnesota Wild": [
        "Kirill Kaprizov",
        "Matt Boldy",
        "Brock Faber",
        "Joel Eriksson Ek",
        "Mats Zuccarello",
        "Marco Rossi",
        "Ryan Hartman",
        "Filip Gustavsson",
    ],
    "Montreal Canadiens": [
        "Nick Suzuki",
        "Cole Caufield",
        "Juraj Slafkovsky",
        "Lane Hutson",
        "Patrik Laine",
        "Kirby Dach",
        "Mike Matheson",
        "Sam Montembeault",
    ],
    "Nashville Predators": [
        "Filip Forsberg",
        "Roman Josi",
        "Steven Stamkos",
        "Jonathan Marchessault",
        "Ryan O'Reilly",
        "Brady Skjei",
        "Luke Evangelista",
        "Juuse Saros",
    ],
    "New Jersey Devils": [
        "Jack Hughes",
        "Jesper Bratt",
        "Nico Hischier",
        "Dougie Hamilton",
        "Timo Meier",
        "Dawson Mercer",
        "Ondrej Palat",
        "Jacob Markstrom",
    ],
    "New York Islanders": [
        "Mathew Barzal",
        "Bo Horvat",
        "Noah Dobson",
        "Brock Nelson",
        "Anders Lee",
        "Kyle Palmieri",
        "Jean-Gabriel Pageau",
        "Ilya Sorokin",
    ],
    "New York Rangers": [
        "Artemi Panarin",
        "Adam Fox",
        "Igor Shesterkin",
        "Mika Zibanejad",
        "Chris Kreider",
        "Vincent Trocheck",
        "Alexis Lafreniere",
        "K'Andre Miller",
    ],
    "Ottawa Senators": [
        "Brady Tkachuk",
        "Tim Stutzle",
        "Jake Sanderson",
        "Claude Giroux",
        "Drake Batherson",
        "Josh Norris",
        "Thomas Chabot",
        "Linus Ullmark",
    ],
    "Philadelphia Flyers": [
        "Travis Konecny",
        "Matvei Michkov",
        "Owen Tippett",
        "Travis Sanheim",
        "Sean Couturier",
        "Morgan Frost",
        "Joel Farabee",
        "Samuel Ersson",
    ],
    "Pittsburgh Penguins": [
        "Sidney Crosby",
        "Evgeni Malkin",
        "Kris Letang",
        "Erik Karlsson",
        "Bryan Rust",
        "Rickard Rakell",
        "Michael Bunting",
        "Tristan Jarry",
    ],
    "San Jose Sharks": [
        "Macklin Celebrini",
        "William Eklund",
        "Tyler Toffoli",
        "Mikael Granlund",
        "Fabian Zetterlund",
        "Will Smith",
        "Luke Kunin",
        "Yaroslav Askarov",
    ],
    "Seattle Kraken": [
        "Matty Beniers",
        "Jared McCann",
        "Vince Dunn",
        "Brandon Montour",
        "Chandler Stephenson",
        "Oliver Bjorkstrand",
        "Eeli Tolvanen",
        "Philipp Grubauer",
    ],
    "St. Louis Blues": [
        "Robert Thomas",
        "Jordan Kyrou",
        "Pavel Buchnevich",
        "Colton Parayko",
        "Brayden Schenn",
        "Jake Neighbours",
        "Brandon Saad",
        "Jordan Binnington",
    ],
    "Tampa Bay Lightning": [
        "Nikita Kucherov",
        "Brayden Point",
        "Victor Hedman",
        "Jake Guentzel",
        "Brandon Hagel",
        "Anthony Cirelli",
        "Nick Paul",
        "Andrei Vasilevskiy",
    ],
    "Toronto Maple Leafs": [
        "Auston Matthews",
        "Mitch Marner",
        "William Nylander",
        "John Tavares",
        "Morgan Rielly",
        "Chris Tanev",
        "Oliver Ekman-Larsson",
        "Matthew Knies",
    ],
    "Utah Hockey Club": [
        "Clayton Keller",
        "Logan Cooley",
        "Mikhail Sergachev",
        "Dylan Guenther",
        "Nick Schmaltz",
        "Lawson Crouse",
        "Matias Maccelli",
        "Connor Ingram",
    ],
    "Vancouver Canucks": [
        "Elias Pettersson",
        "Quinn Hughes",
        "J.T. Miller",
        "Brock Boeser",
        "Conor Garland",
        "Filip Hronek",
        "Jake DeBrusk",
        "Thatcher Demko",
    ],
    "Vegas Golden Knights": [
        "Jack Eichel",
        "Mark Stone",
        "Tomas Hertl",
        "Shea Theodore",
        "William Karlsson",
        "Ivan Barbashev",
        "Alex Pietrangelo",
        "Adin Hill",
    ],
    "Washington Capitals": [
        "Alex Ovechkin",
        "Dylan Strome",
        "John Carlson",
        "Tom Wilson",
        "Pierre-Luc Dubois",
        "Aliaksei Protas",
        "Connor McMichael",
        "Charlie Lindgren",
    ],
    "Winnipeg Jets": [
        "Kyle Connor",
        "Mark Scheifele",
        "Josh Morrissey",
        "Nikolaj Ehlers",
        "Gabriel Vilardi",
        "Cole Perfetti",
        "Nino Niederreiter",
        "Connor Hellebuyck",
    ],
}

NFL_ROSTERS = {
    "Arizona Cardinals": [
        "Kyler Murray",
        "James Conner",
        "Marvin Harrison Jr",
        "Trey McBride",
        "Michael Wilson",
        "Greg Dortch",
        "Zay Jones",
        "Trey Benson",
        "Budda Baker",
        "Jalen Thompson",
        "Zaven Collins",
        "Dennis Gardeck",
    ],
    "Atlanta Falcons": [
        "Kirk Cousins",
        "Bijan Robinson",
        "Drake London",
        "Kyle Pitts",
        "Darnell Mooney",
        "Ray-Ray McCloud",
        "Tyler Allgeier",
        "Jessie Bates III",
        "A.J. Terrell",
        "Kaden Elliss",
        "Matthew Judon",
        "Grady Jarrett",
    ],
    "Baltimore Ravens": [
        "Lamar Jackson",
        "Derrick Henry",
        "Zay Flowers",
        "Mark Andrews",
        "Isaiah Likely",
        "Rashod Bateman",
        "Justice Hill",
        "Roquan Smith",
        "Marlon Humphrey",
        "Kyle Hamilton",
        "Justin Madubuike",
        "Odafe Oweh",
    ],
    "Buffalo Bills": [
        "Josh Allen",
        "James Cook",
        "Stefon Diggs",
        "Dalton Kincaid",
        "Khalil Shakir",
        "Curtis Samuel",
        "Keon Coleman",
        "Matt Milano",
        "Terrel Bernard",
        "Greg Rousseau",
        "Ed Oliver",
        "Taron Johnson",
    ],
    "Carolina Panthers": [
        "Bryce Young",
        "Chuba Hubbard",
        "Diontae Johnson",
        "Adam Thielen",
        "Jonathan Mingo",
        "Xavier Legette",
        "Tommy Tremble",
        "Derrick Brown",
        "Jaycee Horn",
        "Shaq Thompson",
        "Jadeveon Clowney",
        "Brian Burns",
    ],
    "Chicago Bears": [
        "Caleb Williams",
        "D'Andre Swift",
        "DJ Moore",
        "Keenan Allen",
        "Rome Odunze",
        "Cole Kmet",
        "Khalil Herbert",
        "Montez Sweat",
        "Tremaine Edmunds",
        "Jaylon Johnson",
        "Jaquan Brisker",
        "Gervon Dexter",
    ],
    "Cincinnati Bengals": [
        "Joe Burrow",
        "Zack Moss",
        "Ja'Marr Chase",
        "Tee Higgins",
        "Mike Gesicki",
        "Andrei Iosivas",
        "Chase Brown",
        "Logan Wilson",
        "Germaine Pratt",
        "Trey Hendrickson",
        "Sam Hubbard",
        "Cam Taylor-Britt",
    ],
    "Cleveland Browns": [
        "Deshaun Watson",
        "Nick Chubb",
        "Amari Cooper",
        "Jerry Jeudy",
        "David Njoku",
        "Elijah Moore",
        "Jerome Ford",
        "Myles Garrett",
        "Denzel Ward",
        "Jeremiah Owusu-Koramoah",
        "Za'Darius Smith",
        "Grant Delpit",
    ],
    "Dallas Cowboys": [
        "Dak Prescott",
        "Ezekiel Elliott",
        "CeeDee Lamb",
        "Brandin Cooks",
        "Jake Ferguson",
        "Jalen Tolbert",
        "Rico Dowdle",
        "Micah Parsons",
        "Trevon Diggs",
        "DeMarcus Lawrence",
        "Leighton Vander Esch",
        "Malik Hooker",
    ],
    "Denver Broncos": [
        "Bo Nix",
        "Javonte Williams",
        "Courtland Sutton",
        "Tim Patrick",
        "Josh Reynolds",
        "Marvin Mims",
        "Greg Dulcich",
        "Patrick Surtain II",
        "Justin Simmons",
        "Alex Singleton",
        "Baron Browning",
        "Zach Allen",
    ],
    "Detroit Lions": [
        "Jared Goff",
        "Jahmyr Gibbs",
        "David Montgomery",
        "Amon-Ra St. Brown",
        "Sam LaPorta",
        "Jameson Williams",
        "Kalif Raymond",
        "Aidan Hutchinson",
        "Alex Anzalone",
        "Brian Branch",
        "Kerby Joseph",
        "Alim McNeill",
    ],
    "Green Bay Packers": [
        "Jordan Love",
        "Josh Jacobs",
        "Christian Watson",
        "Romeo Doubs",
        "Jayden Reed",
        "Luke Musgrave",
        "Tucker Kraft",
        "Rashan Gary",
        "Jaire Alexander",
        "Quay Walker",
        "Xavier McKinney",
        "Kenny Clark",
    ],
    "Houston Texans": [
        "C.J. Stroud",
        "Joe Mixon",
        "Nico Collins",
        "Tank Dell",
        "Stefon Diggs",
        "Dalton Schultz",
        "Robert Woods",
        "Will Anderson Jr.",
        "Danielle Hunter",
        "Derek Stingley Jr.",
        "Azeez Al-Shaair",
        "Jalen Pitre",
    ],
    "Indianapolis Colts": [
        "Anthony Richardson",
        "Jonathan Taylor",
        "Michael Pittman Jr.",
        "Adonai Mitchell",
        "Josh Downs",
        "Jelani Woods",
        "Mo Alie-Cox",
        "Zaire Franklin",
        "DeForest Buckner",
        "Kenny Moore II",
        "Julian Blackmon",
        "Kwity Paye",
    ],
    "Jacksonville Jaguars": [
        "Trevor Lawrence",
        "Travis Etienne",
        "Christian Kirk",
        "Gabe Davis",
        "Brian Thomas Jr.",
        "Evan Engram",
        "Tank Bigsby",
        "Josh Hines-Allen",
        "Foyesade Oluokun",
        "Tyson Campbell",
        "Andre Cisco",
        "Travon Walker",
    ],
    "Kansas City Chiefs": [
        "Patrick Mahomes",
        "Isiah Pacheco",
        "Rashee Rice",
        "Xavier Worthy",
        "Marquise Brown",
        "Travis Kelce",
        "Clyde Edwards-Helaire",
        "Chris Jones",
        "Nick Bolton",
        "Trent McDuffie",
        "Justin Reid",
        "George Karlaftis",
    ],
    "Las Vegas Raiders": [
        "Gardner Minshew",
        "Zamir White",
        "Davante Adams",
        "Jakobi Meyers",
        "Tre Tucker",
        "Brock Bowers",
        "Michael Mayer",
        "Maxx Crosby",
        "Robert Spillane",
        "Jack Jones",
        "Tre'von Moehrig",
        "Christian Wilkins",
    ],
    "Los Angeles Chargers": [
        "Justin Herbert",
        "Gus Edwards",
        "Quentin Johnston",
        "Josh Palmer",
        "Ladd McConkey",
        "Will Dissly",
        "Hayden Hurst",
        "Joey Bosa",
        "Khalil Mack",
        "Derwin James",
        "Asante Samuel Jr.",
        "Tuli Tuipulotu",
    ],
    "Los Angeles Rams": [
        "Matthew Stafford",
        "Kyren Williams",
        "Puka Nacua",
        "Cooper Kupp",
        "Demarcus Robinson",
        "Tutu Atwell",
        "Colby Parkinson",
        "Byron Young",
        "Ernest Jones",
        "Kobie Turner",
        "Jared Verse",
        "Quentin Lake",
    ],
    "Miami Dolphins": [
        "Tua Tagovailoa",
        "Raheem Mostert",
        "De'Von Achane",
        "Tyreek Hill",
        "Jaylen Waddle",
        "Odell Beckham Jr.",
        "Jonnu Smith",
        "Jaelan Phillips",
        "Jalen Ramsey",
        "Bradley Chubb",
        "David Long Jr.",
        "Zach Sieler",
    ],
    "Minnesota Vikings": [
        "Sam Darnold",
        "Aaron Jones",
        "Justin Jefferson",
        "Jordan Addison",
        "T.J. Hockenson",
        "Brandon Powell",
        "Ty Chandler",
        "Jonathan Greenard",
        "Blake Cashman",
        "Harrison Smith",
        "Byron Murphy",
        "Ivan Pace Jr.",
    ],
    "New England Patriots": [
        "Jacoby Brissett",
        "Rhamondre Stevenson",
        "Kendrick Bourne",
        "DeMario Douglas",
        "K.J. Osborn",
        "Hunter Henry",
        "Austin Hooper",
        "Matthew Judon",
        "Christian Gonzalez",
        "Kyle Dugger",
        "Jabrill Peppers",
        "Davon Godchaux",
    ],
    "New Orleans Saints": [
        "Derek Carr",
        "Alvin Kamara",
        "Chris Olave",
        "Rashid Shaheed",
        "A.T. Perry",
        "Juwan Johnson",
        "Taysom Hill",
        "Demario Davis",
        "Marshon Lattimore",
        "Tyrann Mathieu",
        "Cameron Jordan",
        "Pete Werner",
    ],
    "New York Giants": [
        "Daniel Jones",
        "Devin Singletary",
        "Malik Nabers",
        "Darius Slayton",
        "Wan'Dale Robinson",
        "Darren Waller",
        "Daniel Bellinger",
        "Dexter Lawrence",
        "Brian Burns",
        "Bobby Okereke",
        "Kayvon Thibodeaux",
        "Jason Pinnock",
    ],
    "New York Jets": [
        "Aaron Rodgers",
        "Breece Hall",
        "Garrett Wilson",
        "Mike Williams",
        "Allen Lazard",
        "Tyler Conklin",
        "Jeremy Ruckert",
        "Quinnen Williams",
        "Sauce Gardner",
        "C.J. Mosley",
        "Haason Reddick",
        "Jermaine Johnson",
    ],
    "Philadelphia Eagles": [
        "Jalen Hurts",
        "Saquon Barkley",
        "A.J. Brown",
        "DeVonta Smith",
        "Jahan Dotson",
        "Dallas Goedert",
        "Kenneth Gainwell",
        "Jalen Carter",
        "Darius Slay",
        "Bryce Huff",
        "C.J. Gardner-Johnson",
        "Nolan Smith",
    ],
    "Pittsburgh Steelers": [
        "Russell Wilson",
        "Najee Harris",
        "George Pickens",
        "Van Jefferson",
        "Calvin Austin III",
        "Pat Freiermuth",
        "Jaylen Warren",
        "T.J. Watt",
        "Minkah Fitzpatrick",
        "Alex Highsmith",
        "Joey Porter Jr.",
        "Cam Heyward",
    ],
    "San Francisco 49ers": [
        "Brock Purdy",
        "Christian McCaffrey",
        "Deebo Samuel",
        "Brandon Aiyuk",
        "George Kittle",
        "Jauan Jennings",
        "Elijah Mitchell",
        "Nick Bosa",
        "Fred Warner",
        "Dre Greenlaw",
        "Charvarius Ward",
        "Talanoa Hufanga",
    ],
    "Seattle Seahawks": [
        "Geno Smith",
        "Kenneth Walker III",
        "Zach Charbonnet",
        "DK Metcalf",
        "Tyler Lockett",
        "Jaxon Smith-Njigba",
        "Noah Fant",
        "Boye Mafe",
        "Devon Witherspoon",
        "Riq Woolen",
        "Jamal Adams",
        "Leonard Williams",
    ],
    "Tampa Bay Buccaneers": [
        "Baker Mayfield",
        "Rachaad White",
        "Mike Evans",
        "Chris Godwin",
        "Jalen McMillan",
        "Cade Otton",
        "Chase Edmonds",
        "Lavonte David",
        "Devin White",
        "Antoine Winfield Jr.",
        "Jamel Dean",
        "Vita Vea",
    ],
    "Tennessee Titans": [
        "Will Levis",
        "Tony Pollard",
        "Tyjae Spears",
        "DeAndre Hopkins",
        "Calvin Ridley",
        "Chigoziem Okonkwo",
        "Treylon Burks",
        "Jeffery Simmons",
        "Harold Landry",
        "Amani Hooker",
        "Roger McCreary",
        "Kenneth Murray",
    ],
    "Washington Commanders": [
        "Jayden Daniels",
        "Brian Robinson Jr.",
        "Austin Ekeler",
        "Terry McLaurin",
        "Jahan Dotson",
        "Zach Ertz",
        "Luke McCaffrey",
        "Jonathan Allen",
        "Daron Payne",
        "Jamin Davis",
        "Kendall Fuller",
        "Kamren Curl",
    ],
}

# =============================================================================
# ODDS API CLIENT
# =============================================================================
class OddsAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = ODDS_API_BASE
        self.rate_limit_reset = 0.0

    def _rate_limit_wait(self) -> None:
        now = time.time()
        if now < self.rate_limit_reset:
            time.sleep(self.rate_limit_reset - now)

    def get_odds(
        self,
        sport_key: str,
        regions: str = "us",
        markets: str = "h2h,spreads,totals",
    ) -> Optional[List[Dict[str, Any]]]:
        self._rate_limit_wait()
        try:
            params = {
                "apiKey": self.api_key,
                "sport": sport_key,
                "regions": regions,
                "markets": markets,
                "oddsFormat": "american",
            }
            resp = requests.get(
                f"{self.base_url}/sports/{sport_key}/odds",
                params=params,
                timeout=20,
            )
            if resp.status_code == 429:
                reset = resp.headers.get("x-requests-resets")
                if reset:
                    self.rate_limit_reset = float(reset)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def extract_game_odds(self, odds_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        games: List[Dict[str, Any]] = []
        if not odds_data:
            return games

        for game in odds_data:
            try:
                home_team = game.get("home_team")
                away_team = game.get("away_team")
                commence_time = game.get("commence_time")
                markets = game.get("bookmakers", [])

                best_spread = None
                best_total = None
                best_ml_home = None
                best_ml_away = None

                for book in markets:
                    for market in book.get("markets", []):
                        if market["key"] == "spreads":
                            for out in market.get("outcomes", []):
                                if out["name"] == home_team:
                                    best_spread = out
                        elif market["key"] == "totals":
                            for out in market.get("outcomes", []):
                                best_total = out
                        elif market["key"] == "h2h":
                            for out in market.get("outcomes", []):
                                if out["name"] == home_team:
                                    best_ml_home = out
                                elif out["name"] == away_team:
                                    best_ml_away = out

                games.append(
                    {
                        "home_team": home_team,
                        "away_team": away_team,
                        "commence_time": commence_time,
                        "spread": best_spread,
                        "total": best_total,
                        "ml_home": best_ml_home,
                        "ml_away": best_ml_away,
                    }
                )
            except Exception:
                continue

        return games


# =============================================================================
# STATS API CLIENT (API-SPORTS) – WITH UNREACHABLE HANDLING
# =============================================================================
class StatsAPIClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = API_SPORTS_BASE
        self.session = requests.Session()
        self.session.headers.update({"x-apisports-key": self.api_key})
        self.unreachable = False

    def _mark_unreachable(self) -> None:
        self.unreachable = True

    def _get_player_id(
        self,
        sport: str,
        league_id: int,
        team_id: int,
        player_name: str,
    ) -> Optional[int]:
        if self.unreachable:
            return None
        try:
            url = f"{self.base_url}/{API_SPORT_KEYS[sport]}/players"
            params = {"league": league_id, "team": team_id, "search": player_name}
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code >= 500 or r.status_code in (401, 403):
                self._mark_unreachable()
                return None
            r.raise_for_status()
            data = r.json()
            for item in data.get("response", []):
                name = item.get("player", {}).get("name", "")
                if player_name.lower() in name.lower():
                    return item["player"]["id"]
        except Exception:
            self._mark_unreachable()
        return None

    def get_player_stats(
        self,
        sport: str,
        league_id: int,
        team_id: int,
        player_name: str,
        stat_field: str,
        season: Optional[int] = None,
    ) -> Optional[float]:
        if self.unreachable:
            return None

        if season is None:
            season = datetime.now().year

        pid = self._get_player_id(sport, league_id, team_id, player_name)
        if pid is None:
            return None

        try:
            url = f"{self.base_url}/{API_SPORT_KEYS[sport]}/players/statistics"
            params = {"league": league_id, "team": team_id, "id": pid, "season": season}
            r = self.session.get(url, params=params, timeout=15)
            if r.status_code >= 500 or r.status_code in (401, 403):
                self._mark_unreachable()
                return None
            r.raise_for_status()
            data = r.json()
            resp = data.get("response", [])
            if not resp:
                return None

            stats_blocks = resp[0].get("statistics", [])

            def flatten(d: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
                items: List[Tuple[str, Any]] = []
                for k, v in d.items():
                    new_key = f"{parent_key}{sep}{k}" if parent_key else k
                    if isinstance(v, dict):
                        items.extend(flatten(v, new_key, sep=sep).items())
                    else:
                        items.append((new_key, v))
                return dict(items)

            flat: Dict[str, Any] = {}
            for block in stats_blocks:
                flat.update(flatten(block))

            if stat_field in flat and isinstance(flat[stat_field], (int, float)):
                return float(flat[stat_field])

            for k, v in flat.items():
                if k.lower().endswith(stat_field.lower()) and isinstance(v, (int, float)):
                    return float(v)

            return None
        except Exception:
            self._mark_unreachable()
            return None


# =============================================================================
# PERPLEXITY CLIENT – FIXED get_injury_status
# =============================================================================
class PerplexityClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = PERPLEXITY_BASE
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def get_injury_status(self, sport: str, team: str, player: str) -> Dict[str, Any]:
        """
        Ask Perplexity for a structured injury report.

        Returns:
            {
                "status": "active" | "out" | "questionable" | "unknown",
                "details": "...",
                "last_updated": "ISO8601 string"
            }
        """
        try:
            prompt = (
                "Return ONLY a JSON object with the following keys:\n"
                '  "status": one of ["active", "out", "questionable", "unknown"],\n'
                '  "details": a short human-readable summary string,\n'
                '  "last_updated": an ISO8601 datetime string.\n\n'
                "Context:\n"
                f"Sport: {sport}\n"
                f"Team: {team}\n"
                f"Player: {player}\n\n"
                "If you are not sure, set status to \"unknown\" and explain briefly in details.\n"
                "Do not include any extra text, only valid JSON."
            )

            resp = self.client.chat.completions.create(
                model="sonar-small-online",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )

            content = resp.choices[0].message.content.strip()

            # Strip code fences if present
            if content.startswith("```"):
                content = re.sub(r"^```(json)?", "", content).strip()
                content = re.sub(r"```$", "", content).strip()

            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Non-dict JSON returned")

            status = str(data.get("status", "unknown")).lower()
            if status not in ["active", "out", "questionable", "unknown"]:
                status = "unknown"

            details = str(data.get("details", "")).strip()
            last_updated = str(data.get("last_updated", datetime.utcnow().isoformat()))

            return {
                "status": status,
                "details": details,
                "last_updated": last_updated,
            }

        except Exception:
            return {
                "status": "unknown",
                "details": "Injury status could not be retrieved from Perplexity.",
                "last_updated": datetime.utcnow().isoformat(),
            }


# =============================================================================
# SIMPLE SIMULATION ENGINE FOR TEAM TOTALS & SPREADS
# =============================================================================
class SimulationEngine:
    def __init__(self, sport: str):
        self.sport = sport
        self.model_cfg = SPORT_MODELS[sport]

    def _team_score_distribution(self, mean: float) -> np.ndarray:
        dist_type = self.model_cfg["distribution"]
        var_factor = self.model_cfg["variance_factor"]

        if dist_type == "poisson":
            max_goals = int(self.model_cfg["max_total"])
            xs = np.arange(0, max_goals + 1)
            pmf = poisson.pmf(xs, mean)
            pmf /= pmf.sum()
            return pmf
        elif dist_type == "nbinom":
            var = mean * var_factor
            if var <= mean:
                var = mean + 1.0
            p = mean / var
            r = mean * p / (1 - p)
            xs = np.arange(0, int(self.model_cfg["max_total"]) + 1)
            pmf = nbinom.pmf(xs, r, 1 - p)
            pmf /= pmf.sum()
            return pmf
        else:
            xs = np.arange(0, int(self.model_cfg["max_total"]) + 1)
            pmf = poisson.pmf(xs, mean)
            pmf /= pmf.sum()
            return pmf

    def simulate_game(
        self,
        home_mean: float,
        away_mean: float,
        n_sims: int = 50000,
    ) -> Dict[str, Any]:
        home_dist = self._team_score_distribution(home_mean)
        away_dist = self._team_score_distribution(away_mean)

        xs = np.arange(len(home_dist))
        home_samples = np.random.choice(xs, size=n_sims, p=home_dist)
        away_samples = np.random.choice(xs, size=n_sims, p=away_dist)

        total = home_samples + away_samples
        margin = home_samples - away_samples

        return {
            "home_scores": home_samples,
            "away_scores": away_samples,
            "totals": total,
            "margins": margin,
        }

    def prob_over_total(self, sims: Dict[str, Any], line: float) -> float:
        totals = sims["totals"]
        return float((totals > line).mean())

    def prob_home_cover(self, sims: Dict[str, Any], spread: float) -> float:
        margins = sims["margins"]
        return float((margins + spread > 0).mean())

    def prob_home_win(self, sims: Dict[str, Any]) -> float:
        margins = sims["margins"]
        return float((margins > 0).mean())


# =============================================================================
# PLAYER PROP MODEL (SIMPLE)
# =============================================================================
def prop_over_probability(mean: float, line: float, stat: str, sport: str) -> float:
    """
    Simple over probability using Poisson or Normal approximation.
    """
    if mean <= 0:
        return 0.0

    if sport in ["MLB", "NHL"]:
        # Discrete, low counts -> Poisson
        k = int(np.floor(line)) + 1
        xs = np.arange(0, k)
        cdf = poisson.cdf(k - 1, mean)
        return float(1.0 - cdf)
    else:
        # Use Normal approx
        std = max(0.5, np.sqrt(mean * 1.2))
        z = (line + 0.5 - mean) / std
        return float(1.0 - norm.cdf(z))


# =============================================================================
# STREAMLIT UI
# =============================================================================
def main():
    st.set_page_config(page_title="CLARITY 18.2 ELITE", layout="wide")
    st.title("CLARITY 18.2 ELITE - Sports Betting System")

    st.sidebar.markdown(f"**Version:** {VERSION}")
    st.sidebar.markdown(f"**Build Date:** {BUILD_DATE}")

    sport = st.sidebar.selectbox("Sport", ["NBA", "MLB", "NHL", "NFL"])
    mode = st.sidebar.radio("Mode", ["Game Lines", "Player Props"])

    odds_client = OddsAPIClient(ODDS_API_KEY)
    stats_client = StatsAPIClient(API_SPORTS_KEY)
    perp_client = PerplexityClient(UNIFIED_API_KEY)

    if mode == "Game Lines":
        st.header(f"{sport} - Game Lines")

        col1, col2 = st.columns(2)
        with col1:
            home_team = st.selectbox("Home Team", HARDCODED_TEAMS[sport])
        with col2:
            away_team = st.selectbox(
                "Away Team",
                [t for t in HARDCODED_TEAMS[sport] if t != home_team],
            )

        model_cfg = SPORT_MODELS[sport]
        default_total = model_cfg["avg_total"]
        max_total = model_cfg["max_total"]

        # IMPORTANT: default value must be <= max_value to avoid Streamlit crash
        total_line = st.number_input(
            "Total Line",
            min_value=0.5,
            max_value=float(max_total),
            value=float(min(default_total, max_total - 0.5)),
            step=0.5,
        )

        spread_default = model_cfg["home_advantage"]
        spread_line = st.number_input(
            "Home Spread (negative = favorite)",
            min_value=-40.0,
            max_value=40.0,
            value=float(-spread_default),
            step=0.5,
        )

        col3, col4 = st.columns(2)
        with col3:
            use_odds_api = st.checkbox("Pull market lines from Odds API", value=False)
        with col4:
            n_sims = st.slider("Simulations", 5000, 100000, 50000, step=5000)

        if use_odds_api:
            st.info("Attempting to fetch live odds...")
            sport_key_map = {
                "NBA": "basketball_nba",
                "MLB": "baseball_mlb",
                "NHL": "icehockey_nhl",
                "NFL": "americanfootball_nfl",
            }
            sport_key = sport_key_map.get(sport)
            odds_data = odds_client.get_odds(sport_key) if sport_key else None
            games = odds_client.extract_game_odds(odds_data or [])

            matched = None
            for g in games:
                if (
                    g["home_team"] in home_team
                    or home_team in (g["home_team"] or "")
                ) and (
                    g["away_team"] in away_team
                    or away_team in (g["away_team"] or "")
                ):
                    matched = g
                    break

            if matched and matched["total"] and matched["spread"]:
                try:
                    total_line = float(matched["total"]["point"])
                    spread_line = float(matched["spread"]["point"])
                    st.success(
                        f"Loaded market lines: Total {total_line}, Spread {spread_line}"
                    )
                except Exception:
                    st.warning("Could not parse market lines; using manual inputs.")
            else:
                st.warning("No matching game found in Odds API; using manual inputs.")

        st.subheader("Model Inputs")

        col5, col6 = st.columns(2)
        with col5:
            home_mean = st.number_input(
                "Home Expected Points/Goals/Runs",
                min_value=0.0,
                max_value=float(model_cfg["max_total"]),
                value=float(model_cfg["avg_total"] / 2 + model_cfg["home_advantage"]),
                step=0.5,
            )
        with col6:
            away_mean = st.number_input(
                "Away Expected Points/Goals/Runs",
                min_value=0.0,
                max_value=float(model_cfg["max_total"]),
                value=float(model_cfg["avg_total"] / 2 - model_cfg["home_advantage"]),
                step=0.5,
            )

        if st.button("Run Simulation"):
            engine = SimulationEngine(sport)
            sims = engine.simulate_game(home_mean, away_mean, n_sims=n_sims)

            p_over = engine.prob_over_total(sims, total_line)
            p_home_cover = engine.prob_home_cover(sims, spread_line)
            p_home_win = engine.prob_home_win(sims)

            col7, col8, col9 = st.columns(3)
            with col7:
                st.metric("P(Over Total)", f"{p_over*100:.1f}%")
            with col8:
                st.metric("P(Home Covers)", f"{p_home_cover*100:.1f}%")
            with col9:
                st.metric("P(Home Wins)", f"{p_home_win*100:.1f}%")

            st.subheader("Distribution Summary")
            df = pd.DataFrame(
                {
                    "Home Score": sims["home_scores"],
                    "Away Score": sims["away_scores"],
                    "Total": sims["totals"],
                    "Margin": sims["margins"],
                }
            )
            st.write(df.describe())

    else:
        st.header(f"{sport} - Player Props")

        team = st.selectbox("Team", HARDCODED_TEAMS[sport])
        if sport == "NBA":
            roster = NBA_ROSTERS[team]
        elif sport == "MLB":
            roster = MLB_ROSTERS[team]
        elif sport == "NHL":
            roster = NHL_ROSTERS[team]
        else:
            roster = NFL_ROSTERS[team]

        player = st.selectbox("Player", roster)
        category = st.selectbox("Stat Category", SPORT_CATEGORIES[sport])

        line = st.number_input(
            "Prop Line",
            min_value=0.0,
            max_value=500.0,
            value=20.5 if sport == "NBA" else 0.5,
            step=0.5,
        )

        col10, col11 = st.columns(2)
        with col10:
            use_api_stats = st.checkbox("Use API-Sports for baseline", value=True)
        with col11:
            use_injury = st.checkbox("Check injury status (Perplexity)", value=False)

        baseline_mean: Optional[float] = None
        if use_api_stats:
            st.info("Attempting to fetch player stats from API-Sports...")
            team_id = API_TEAM_IDS.get(sport, {}).get(team)
            stat_field = STAT_MAPPING[sport].get(category, "")
            if team_id is None or not stat_field:
                st.warning("Missing team ID or stat mapping; using manual mean.")
            else:
                baseline_mean = stats_client.get_player_stats(
                    sport,
                    API_LEAGUE_IDS[sport],
                    team_id,
                    player,
                    stat_field,
                )
                if baseline_mean is None:
                    st.warning("Could not retrieve stats; using manual mean.")
                else:
                    st.success(f"Baseline mean from API-Sports: {baseline_mean:.2f}")

        manual_default = baseline_mean if baseline_mean is not None else (
            24.5 if sport == "NBA" else 3.5
        )
        mean_input = st.number_input(
            "Model Mean (override or confirm)",
            min_value=0.0,
            max_value=500.0,
            value=float(manual_default),
            step=0.5,
        )

        injury_info: Optional[Dict[str, Any]] = None
        if use_injury:
            st.info("Querying Perplexity for injury status...")
            injury_info = perp_client.get_injury_status(sport, team, player)
            st.write("**Injury Status:**", injury_info.get("status", "unknown"))
            st.write("**Details:**", injury_info.get("details", ""))
            st.write("**Last Updated:**", injury_info.get("last_updated", ""))

        if st.button("Evaluate Prop"):
            p_over = prop_over_probability(mean_input, line, category, sport)
            st.metric("P(Over)", f"{p_over*100:.1f}%")
            st.metric("P(Under)", f"{(1-p_over)*100:.1f}%")

            cfg = STAT_CONFIG.get(category, {"tier": "MED", "buffer": 1.0, "reject": False})
            edge = mean_input - line
            st.write(f"**Model Edge (mean - line):** {edge:.2f}")
            st.write(f"**Tier:** {cfg['tier']}  |  **Red-Flag Reject:** {cfg['reject']}")

            if cfg["reject"]:
                st.warning("This is a RED-TIER prop. System flags it as high volatility / rejection candidate.")

            if injury_info and injury_info.get("status") in ["out", "questionable"]:
                st.error(
                    f"Injury flag: {player} is {injury_info.get('status')} – "
                    "treat any projection with caution."
                )


if __name__ == "__main__":
    main()
```
