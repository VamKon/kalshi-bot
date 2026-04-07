"""
Cricket venue mappings for franchises and international teams.
Covers IPL, BBL, PSL, CPL, SA20, ILT20, The Hundred, and international venues.
"""

from typing import TypedDict


class VenueInfo(TypedDict):
    venue: str
    city: str
    country: str
    pitch_type: str  # "spin_friendly" | "pace_friendly" | "batting_friendly" | "balanced"
    capacity: int | None


# =============================================================================
# IPL VENUES (India)
# =============================================================================
IPL_VENUES: dict[str, VenueInfo] = {
    "Sunrisers Hyderabad": {
        "venue": "Rajiv Gandhi International Cricket Stadium",
        "city": "Hyderabad",
        "country": "India",
        "pitch_type": "balanced",
        "capacity": 55000,
    },
    "Chennai Super Kings": {
        "venue": "MA Chidambaram Stadium",
        "city": "Chennai",
        "country": "India",
        "pitch_type": "spin_friendly",
        "capacity": 50000,
    },
    "Mumbai Indians": {
        "venue": "Wankhede Stadium",
        "city": "Mumbai",
        "country": "India",
        "pitch_type": "batting_friendly",
        "capacity": 33000,
    },
    "Royal Challengers Bengaluru": {
        "venue": "M. Chinnaswamy Stadium",
        "city": "Bengaluru",
        "country": "India",
        "pitch_type": "batting_friendly",
        "capacity": 40000,
    },
    "Royal Challengers Bangalore": {
        "venue": "M. Chinnaswamy Stadium",
        "city": "Bengaluru",
        "country": "India",
        "pitch_type": "batting_friendly",
        "capacity": 40000,
    },
    "Kolkata Knight Riders": {
        "venue": "Eden Gardens",
        "city": "Kolkata",
        "country": "India",
        "pitch_type": "balanced",
        "capacity": 66000,
    },
    "Delhi Capitals": {
        "venue": "Arun Jaitley Stadium",
        "city": "Delhi",
        "country": "India",
        "pitch_type": "batting_friendly",
        "capacity": 41000,
    },
    "Rajasthan Royals": {
        "venue": "Sawai Mansingh Stadium",
        "city": "Jaipur",
        "country": "India",
        "pitch_type": "spin_friendly",
        "capacity": 30000,
    },
    "Punjab Kings": {
        "venue": "IS Bindra Stadium",
        "city": "Mohali",
        "country": "India",
        "pitch_type": "pace_friendly",
        "capacity": 26000,
    },
    "Gujarat Titans": {
        "venue": "Narendra Modi Stadium",
        "city": "Ahmedabad",
        "country": "India",
        "pitch_type": "balanced",
        "capacity": 132000,
    },
    "Lucknow Super Giants": {
        "venue": "BRSABV Ekana Cricket Stadium",
        "city": "Lucknow",
        "country": "India",
        "pitch_type": "balanced",
        "capacity": 50000,
    },
}

# =============================================================================
# BIG BASH LEAGUE VENUES (Australia)
# =============================================================================
BBL_VENUES: dict[str, VenueInfo] = {
    "Sydney Sixers": {
        "venue": "Sydney Cricket Ground",
        "city": "Sydney",
        "country": "Australia",
        "pitch_type": "balanced",
        "capacity": 48000,
    },
    "Sydney Thunder": {
        "venue": "Sydney Showground Stadium",
        "city": "Sydney",
        "country": "Australia",
        "pitch_type": "batting_friendly",
        "capacity": 25000,
    },
    "Melbourne Stars": {
        "venue": "Melbourne Cricket Ground",
        "city": "Melbourne",
        "country": "Australia",
        "pitch_type": "balanced",
        "capacity": 100024,
    },
    "Melbourne Renegades": {
        "venue": "Marvel Stadium",
        "city": "Melbourne",
        "country": "Australia",
        "pitch_type": "batting_friendly",
        "capacity": 53359,
    },
    "Brisbane Heat": {
        "venue": "The Gabba",
        "city": "Brisbane",
        "country": "Australia",
        "pitch_type": "pace_friendly",
        "capacity": 42000,
    },
    "Adelaide Strikers": {
        "venue": "Adelaide Oval",
        "city": "Adelaide",
        "country": "Australia",
        "pitch_type": "balanced",
        "capacity": 53500,
    },
    "Perth Scorchers": {
        "venue": "Optus Stadium",
        "city": "Perth",
        "country": "Australia",
        "pitch_type": "pace_friendly",
        "capacity": 60000,
    },
    "Hobart Hurricanes": {
        "venue": "Blundstone Arena",
        "city": "Hobart",
        "country": "Australia",
        "pitch_type": "pace_friendly",
        "capacity": 20000,
    },
}

# =============================================================================
# PAKISTAN SUPER LEAGUE VENUES
# =============================================================================
PSL_VENUES: dict[str, VenueInfo] = {
    "Karachi Kings": {
        "venue": "National Stadium",
        "city": "Karachi",
        "country": "Pakistan",
        "pitch_type": "batting_friendly",
        "capacity": 34228,
    },
    "Lahore Qalandars": {
        "venue": "Gaddafi Stadium",
        "city": "Lahore",
        "country": "Pakistan",
        "pitch_type": "balanced",
        "capacity": 27000,
    },
    "Islamabad United": {
        "venue": "Rawalpindi Cricket Stadium",
        "city": "Rawalpindi",
        "country": "Pakistan",
        "pitch_type": "pace_friendly",
        "capacity": 15000,
    },
    "Peshawar Zalmi": {
        "venue": "Arbab Niaz Stadium",
        "city": "Peshawar",
        "country": "Pakistan",
        "pitch_type": "balanced",
        "capacity": 50000,
    },
    "Quetta Gladiators": {
        "venue": "Bugti Stadium",
        "city": "Quetta",
        "country": "Pakistan",
        "pitch_type": "spin_friendly",
        "capacity": 15000,
    },
    "Multan Sultans": {
        "venue": "Multan Cricket Stadium",
        "city": "Multan",
        "country": "Pakistan",
        "pitch_type": "spin_friendly",
        "capacity": 35000,
    },
}

# =============================================================================
# CARIBBEAN PREMIER LEAGUE VENUES
# =============================================================================
CPL_VENUES: dict[str, VenueInfo] = {
    "Trinbago Knight Riders": {
        "venue": "Queen's Park Oval",
        "city": "Port of Spain",
        "country": "Trinidad and Tobago",
        "pitch_type": "balanced",
        "capacity": 25000,
    },
    "Jamaica Tallawahs": {
        "venue": "Sabina Park",
        "city": "Kingston",
        "country": "Jamaica",
        "pitch_type": "pace_friendly",
        "capacity": 20000,
    },
    "Guyana Amazon Warriors": {
        "venue": "Providence Stadium",
        "city": "Georgetown",
        "country": "Guyana",
        "pitch_type": "spin_friendly",
        "capacity": 20000,
    },
    "Barbados Royals": {
        "venue": "Kensington Oval",
        "city": "Bridgetown",
        "country": "Barbados",
        "pitch_type": "pace_friendly",
        "capacity": 28000,
    },
    "St Kitts and Nevis Patriots": {
        "venue": "Warner Park",
        "city": "Basseterre",
        "country": "St Kitts and Nevis",
        "pitch_type": "batting_friendly",
        "capacity": 8000,
    },
    "Saint Lucia Kings": {
        "venue": "Daren Sammy National Cricket Stadium",
        "city": "Gros Islet",
        "country": "Saint Lucia",
        "pitch_type": "balanced",
        "capacity": 15000,
    },
}

# =============================================================================
# SA20 VENUES (South Africa)
# =============================================================================
SA20_VENUES: dict[str, VenueInfo] = {
    "Sunrisers Eastern Cape": {
        "venue": "St George's Park",
        "city": "Gqeberha",
        "country": "South Africa",
        "pitch_type": "pace_friendly",
        "capacity": 19000,
    },
    "MI Cape Town": {
        "venue": "Newlands Cricket Ground",
        "city": "Cape Town",
        "country": "South Africa",
        "pitch_type": "pace_friendly",
        "capacity": 25000,
    },
    "Paarl Royals": {
        "venue": "Boland Park",
        "city": "Paarl",
        "country": "South Africa",
        "pitch_type": "balanced",
        "capacity": 10000,
    },
    "Pretoria Capitals": {
        "venue": "SuperSport Park",
        "city": "Centurion",
        "country": "South Africa",
        "pitch_type": "pace_friendly",
        "capacity": 22000,
    },
    "Durban's Super Giants": {
        "venue": "Kingsmead",
        "city": "Durban",
        "country": "South Africa",
        "pitch_type": "balanced",
        "capacity": 25000,
    },
    "Joburg Super Kings": {
        "venue": "The Wanderers Stadium",
        "city": "Johannesburg",
        "country": "South Africa",
        "pitch_type": "batting_friendly",
        "capacity": 34000,
    },
}

# =============================================================================
# ILT20 VENUES (UAE)
# =============================================================================
ILT20_VENUES: dict[str, VenueInfo] = {
    "Abu Dhabi Knight Riders": {
        "venue": "Sheikh Zayed Stadium",
        "city": "Abu Dhabi",
        "country": "UAE",
        "pitch_type": "spin_friendly",
        "capacity": 20000,
    },
    "Desert Vipers": {
        "venue": "Sheikh Zayed Stadium",
        "city": "Abu Dhabi",
        "country": "UAE",
        "pitch_type": "spin_friendly",
        "capacity": 20000,
    },
    "Dubai Capitals": {
        "venue": "Dubai International Cricket Stadium",
        "city": "Dubai",
        "country": "UAE",
        "pitch_type": "balanced",
        "capacity": 25000,
    },
    "Gulf Giants": {
        "venue": "Dubai International Cricket Stadium",
        "city": "Dubai",
        "country": "UAE",
        "pitch_type": "balanced",
        "capacity": 25000,
    },
    "MI Emirates": {
        "venue": "Sharjah Cricket Stadium",
        "city": "Sharjah",
        "country": "UAE",
        "pitch_type": "spin_friendly",
        "capacity": 27000,
    },
    "Sharjah Warriors": {
        "venue": "Sharjah Cricket Stadium",
        "city": "Sharjah",
        "country": "UAE",
        "pitch_type": "spin_friendly",
        "capacity": 27000,
    },
}

# =============================================================================
# THE HUNDRED VENUES (England)
# =============================================================================
HUNDRED_VENUES: dict[str, VenueInfo] = {
    "Oval Invincibles": {
        "venue": "The Oval",
        "city": "London",
        "country": "England",
        "pitch_type": "balanced",
        "capacity": 25500,
    },
    "London Spirit": {
        "venue": "Lord's Cricket Ground",
        "city": "London",
        "country": "England",
        "pitch_type": "balanced",
        "capacity": 30000,
    },
    "Southern Brave": {
        "venue": "Rose Bowl",
        "city": "Southampton",
        "country": "England",
        "pitch_type": "pace_friendly",
        "capacity": 25000,
    },
    "Welsh Fire": {
        "venue": "Sophia Gardens",
        "city": "Cardiff",
        "country": "Wales",
        "pitch_type": "balanced",
        "capacity": 16000,
    },
    "Birmingham Phoenix": {
        "venue": "Edgbaston",
        "city": "Birmingham",
        "country": "England",
        "pitch_type": "batting_friendly",
        "capacity": 25000,
    },
    "Trent Rockets": {
        "venue": "Trent Bridge",
        "city": "Nottingham",
        "country": "England",
        "pitch_type": "batting_friendly",
        "capacity": 17500,
    },
    "Northern Superchargers": {
        "venue": "Headingley",
        "city": "Leeds",
        "country": "England",
        "pitch_type": "pace_friendly",
        "capacity": 18350,
    },
    "Manchester Originals": {
        "venue": "Old Trafford",
        "city": "Manchester",
        "country": "England",
        "pitch_type": "balanced",
        "capacity": 26000,
    },
}

# =============================================================================
# INTERNATIONAL VENUES (country-level defaults for T20I/ODI/Test)
# =============================================================================
INTERNATIONAL_VENUES: dict[str, VenueInfo] = {
    "India": {
        "venue": "Various",
        "city": "Various",
        "country": "India",
        "pitch_type": "spin_friendly",
        "capacity": None,
    },
    "Australia": {
        "venue": "Various",
        "city": "Various",
        "country": "Australia",
        "pitch_type": "pace_friendly",
        "capacity": None,
    },
    "England": {
        "venue": "Various",
        "city": "Various",
        "country": "England",
        "pitch_type": "pace_friendly",
        "capacity": None,
    },
    "South Africa": {
        "venue": "Various",
        "city": "Various",
        "country": "South Africa",
        "pitch_type": "pace_friendly",
        "capacity": None,
    },
    "Pakistan": {
        "venue": "Various",
        "city": "Various",
        "country": "Pakistan",
        "pitch_type": "spin_friendly",
        "capacity": None,
    },
    "New Zealand": {
        "venue": "Various",
        "city": "Various",
        "country": "New Zealand",
        "pitch_type": "pace_friendly",
        "capacity": None,
    },
    "West Indies": {
        "venue": "Various",
        "city": "Various",
        "country": "West Indies",
        "pitch_type": "balanced",
        "capacity": None,
    },
    "Sri Lanka": {
        "venue": "Various",
        "city": "Various",
        "country": "Sri Lanka",
        "pitch_type": "spin_friendly",
        "capacity": None,
    },
    "Bangladesh": {
        "venue": "Various",
        "city": "Various",
        "country": "Bangladesh",
        "pitch_type": "spin_friendly",
        "capacity": None,
    },
    "Afghanistan": {
        "venue": "Various",
        "city": "Various",
        "country": "Afghanistan",
        "pitch_type": "spin_friendly",
        "capacity": None,
    },
    "Zimbabwe": {
        "venue": "Various",
        "city": "Various",
        "country": "Zimbabwe",
        "pitch_type": "balanced",
        "capacity": None,
    },
    "Ireland": {
        "venue": "Various",
        "city": "Various",
        "country": "Ireland",
        "pitch_type": "pace_friendly",
        "capacity": None,
    },
}

# =============================================================================
# SPORT KEY → VENUE MAP
# =============================================================================
SPORT_KEY_VENUE_MAPS: dict[str, dict[str, VenueInfo]] = {
    "cricket_ipl":                        IPL_VENUES,
    "cricket_big_bash":                   BBL_VENUES,
    "cricket_psl":                        PSL_VENUES,
    "cricket_caribbean_premier_league":   CPL_VENUES,
    "cricket_sa20":                       SA20_VENUES,
    "cricket_ilt20":                      ILT20_VENUES,
    "cricket_the_hundred":                HUNDRED_VENUES,
    "cricket_international_t20":          INTERNATIONAL_VENUES,
    "cricket_odi":                        INTERNATIONAL_VENUES,
    "cricket_test_match":                 INTERNATIONAL_VENUES,
}


# =============================================================================
# LOOKUP HELPERS
# =============================================================================

def resolve_venue(home_team: str, sport_key: str) -> VenueInfo | None:
    """Return full VenueInfo for a home team + sport key, or None if not found."""
    venue_map = SPORT_KEY_VENUE_MAPS.get(sport_key, {})
    return venue_map.get(home_team)


def get_venue_name(home_team: str, sport_key: str) -> str | None:
    info = resolve_venue(home_team, sport_key)
    return info["venue"] if info else None


def get_venue_city(home_team: str, sport_key: str) -> str | None:
    info = resolve_venue(home_team, sport_key)
    return info["city"] if info else None


def get_venue_country(home_team: str, sport_key: str) -> str | None:
    info = resolve_venue(home_team, sport_key)
    return info["country"] if info else None


def get_pitch_type(home_team: str, sport_key: str) -> str | None:
    info = resolve_venue(home_team, sport_key)
    return info["pitch_type"] if info else None


def get_match_format(sport_key: str) -> str:
    """Derive match format from sport key."""
    if "test" in sport_key:
        return "Test"
    elif "odi" in sport_key:
        return "ODI"
    return "T20"


def get_all_supported_sport_keys() -> list[str]:
    """Return all cricket sport keys the bot supports."""
    return list(SPORT_KEY_VENUE_MAPS.keys())
