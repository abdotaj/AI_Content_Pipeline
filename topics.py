# ============================================================
#  topics.py  —  Structured topic registry for Dark Crime Decoded
#  Keys: lowercase, stripped, no duplicates
#  Usage: from topics import USA_TOPICS, WORLD_TOPICS, ARABIC_TOPICS, generate_topic
#         from topics import ALIASES, normalize_topic
# ============================================================
import random

# ── USA Topics ───────────────────────────────────────────────

USA_TOPICS = {
    # Serial killers
    "ted bundy": {
        "show": "Extremely Wicked Shockingly Evil and Vile",
        "type": "serial killer"
    },
    "jeffrey dahmer": {
        "show": "Monster (Netflix)",
        "type": "serial killer"
    },
    "richard ramirez": {
        "show": "Night Stalker (Netflix)",
        "type": "serial killer"
    },
    "john wayne gacy": {
        "show": "Gacy Tapes",
        "type": "serial killer"
    },
    "charles manson": {
        "show": "Mindhunter",
        "type": "cult leader"
    },
    "ed kemper": {
        "show": "Mindhunter",
        "type": "serial killer"
    },
    "zodiac killer": {
        "show": "Zodiac",
        "type": "unsolved"
    },

    # Drug cartels
    "pablo escobar": {
        "show": "Narcos",
        "type": "cartel"
    },
    "el chapo": {
        "show": "Narcos Mexico",
        "type": "cartel"
    },
    "griselda blanco": {
        "show": "Griselda (Netflix)",
        "type": "cartel"
    },

    # Mafia
    "al capone": {
        "show": "Boardwalk Empire",
        "type": "mafia"
    },
    "john gotti": {
        "show": "Gotti",
        "type": "mafia"
    },
    "whitey bulger": {
        "show": "Black Mass",
        "type": "mafia"
    },

    # Fraud
    "jordan belfort": {
        "show": "Wolf of Wall Street",
        "type": "fraud"
    },
    "anna delvey": {
        "show": "Inventing Anna",
        "type": "fraud"
    },
    "elizabeth holmes": {
        "show": "The Dropout",
        "type": "fraud"
    },

    # True crime / scandal
    "gypsy rose blanchard": {
        "show": "The Act",
        "type": "true crime"
    },
    "jeffrey epstein": {
        "show": "Filthy Rich (Netflix)",
        "type": "scandal"
    },
    "tinder swindler": {
        "show": "The Tinder Swindler",
        "type": "fraud"
    },

    # ── NEW ADDITIONS ────────────────────────────────────────

    "henry hill": {
        "show": "Goodfellas",
        "type": "mafia"
    },
    "frank lucas": {
        "show": "American Gangster",
        "type": "drug trafficking"
    },
    "ted kaczynski": {
        "show": "Manhunt Unabomber (Netflix)",
        "type": "domestic terrorism"
    },
    "btk killer": {
        "show": "BTK Documentary (A&E)",
        "type": "serial killer"
    },
    "bernie madoff": {
        "show": "Madoff (Netflix)",
        "type": "fraud"
    },
    "bonnie and clyde": {
        "show": "Bonnie and Clyde (History Channel)",
        "type": "gangster"
    },
    "d.b. cooper": {
        "show": "D.B. Cooper Where Are You? (Netflix)",
        "type": "unsolved"
    },

    # ── From: 50 Best True-Crime Docuseries ──────────────────
    "steven avery": {
        "show": "Making a Murderer (Netflix)",
        "type": "wrongful conviction"
    },
    "robert durst": {
        "show": "The Jinx (HBO)",
        "type": "serial killer"
    },
    "joe exotic": {
        "show": "Tiger King (Netflix)",
        "type": "true crime"
    },
    "rajneesh cult": {
        "show": "Wild Wild Country (Netflix)",
        "type": "cult"
    },
    "heaven's gate cult": {
        "show": "Heaven's Gate: The Cult of Cults (HBO Max)",
        "type": "cult"
    },
    "nxivm cult": {
        "show": "Seduced: Inside the NXIVM Cult (Starz)",
        "type": "cult"
    },
    "r. kelly": {
        "show": "Surviving R. Kelly (Lifetime)",
        "type": "scandal"
    },
    "aaron hernandez": {
        "show": "Killer Inside: The Mind of Aaron Hernandez (Netflix)",
        "type": "true crime"
    },
    "kalief browder": {
        "show": "TIME: The Kalief Browder Story (Spike TV)",
        "type": "wrongful imprisonment"
    },
    "adnan syed": {
        "show": "The Case Against Adnan Syed (HBO)",
        "type": "wrongful conviction"
    },
    "mcmillion fraud": {
        "show": "McMillion$ (HBO)",
        "type": "fraud"
    },
}


# ── World Topics ─────────────────────────────────────────────

WORLD_TOPICS = {
    # India
    "nirav modi": {
        "show": "Bad Boy Billionaires",
        "type": "fraud",
        "region": "india"
    },
    "delhi crime case": {
        "show": "Delhi Crime",
        "type": "crime",
        "region": "india"
    },
    "abu salem": {
        "show": "Mumbai Mafia",
        "type": "mafia",
        "region": "india"
    },
    "dawood ibrahim": {
        "show": "Sacred Games inspiration",
        "type": "crime",
        "region": "india"
    },

    # Japan
    "tokyo vice yakuza": {
        "show": "Tokyo Vice",
        "type": "mafia",
        "region": "japan"
    },

    # Italy
    "gomorrah mafia": {
        "show": "Gomorrah",
        "type": "mafia",
        "region": "italy"
    },

    # Spain
    "money heist real inspiration": {
        "show": "Money Heist",
        "type": "heist",
        "region": "spain"
    },

    # ── NEW ADDITIONS ────────────────────────────────────────

    "kray twins": {
        "show": "Legend",
        "type": "gangster",
        "region": "uk"
    },
    "charles sobhraj": {
        "show": "The Serpent (Netflix/BBC)",
        "type": "serial killer",
        "region": "asia"
    },
    "salvatore riina": {
        "show": "The Traitor (Il Traditore)",
        "type": "mafia",
        "region": "italy"
    },
    "semion mogilevich": {
        "show": "McMafia (BBC)",
        "type": "organized crime",
        "region": "russia"
    },
    "amado carrillo fuentes": {
        "show": "Narcos Mexico",
        "type": "cartel",
        "region": "mexico"
    },
    "kim jong nam assassination": {
        "show": "Kim Jong Nam documentary",
        "type": "political crime",
        "region": "north korea"
    },

    # ── From: BuzzFeed World Crime Cases ─────────────────────
    "abby choi murder": {
        "show": "Hong Kong True Crime (documentary)",
        "type": "murder",
        "region": "hong kong"
    },
    "xavier dupont de ligonnes": {
        "show": "L'Affaire Dupont de Ligonnès (documentary)",
        "type": "family murder",
        "region": "france"
    },
    "madeleine mccann": {
        "show": "The Disappearance of Madeleine McCann (Netflix)",
        "type": "missing persons",
        "region": "uk"
    },
    "beaumont children disappearance": {
        "show": "The Beaumont Children (Australian documentary)",
        "type": "missing persons",
        "region": "australia"
    },
    "isdal woman": {
        "show": "Death in Ice Valley (BBC / NRK)",
        "type": "unsolved",
        "region": "norway"
    },
    "setagaya family murder": {
        "show": "Japan's Unsolved Murders (documentary)",
        "type": "unsolved",
        "region": "japan"
    },
    "lars mittank disappearance": {
        "show": "Disappearance in Bulgaria (European true crime)",
        "type": "missing persons",
        "region": "bulgaria"
    },
    "gareth williams spy": {
        "show": "The Spy in the Bag (Channel 4 documentary)",
        "type": "espionage murder",
        "region": "uk"
    },
    "highway of tears": {
        "show": "Highway of Tears (documentary)",
        "type": "serial killer",
        "region": "canada"
    },
    "carlos robledo puch": {
        "show": "El Ángel de la Muerte (Argentine true crime)",
        "type": "serial killer",
        "region": "argentina"
    },
    "brabant killers": {
        "show": "Bende van Nijvel (Belgian documentary)",
        "type": "organized crime",
        "region": "belgium"
    },
    "william tyrrell disappearance": {
        "show": "Who Took William Tyrrell? (Australian documentary)",
        "type": "missing persons",
        "region": "australia"
    },
}


# ── Arabic Topics ────────────────────────────────────────────

ARABIC_TOPICS = {
    # Egypt
    "raya and sakina": {
        "arabic": "ريا وسكينة",
        "show": "Raya and Sakina series",
        "type": "serial killers",
        "region": "egypt"
    },
    "gezira serial killer": {
        "arabic": "سفاح الجيزة",
        "show": "Gezira Killer Series",
        "type": "serial killer",
        "region": "egypt"
    },
    "ezzat hanafi": {
        "arabic": "عزت حنفي",
        "show": "El Gezira Movie",
        "type": "crime",
        "region": "egypt"
    },

    # Saudi Arabia
    "dammam kidnapper": {
        "arabic": "خاطف الدمام",
        "show": "Saudi crime documentaries",
        "type": "crime",
        "region": "saudi"
    },
    "khobar crime case": {
        "arabic": "جريمة الخبر",
        "show": "Saudi investigation shows",
        "type": "crime",
        "region": "saudi"
    },

    # UAE
    "reem island murder": {
        "arabic": "جريمة جزيرة الريم",
        "show": "UAE crime documentary",
        "type": "crime",
        "region": "uae"
    },

    # Kuwait
    "abdali cell kuwait": {
        "arabic": "خلية العبدلي",
        "show": "documentaries",
        "type": "crime",
        "region": "kuwait"
    },

    # Morocco
    "taroudant serial killer": {
        "arabic": "سفاح تارودانت",
        "show": "news coverage",
        "type": "serial killer",
        "region": "morocco"
    },

    # ── NEW ADDITIONS ────────────────────────────────────────

    # Saudi Arabia
    "juhayman al otaybi": {
        "arabic": "جهيمان العتيبي",
        "show": "Juhayman (Saudi series)",
        "type": "siege crime",
        "region": "saudi"
    },

    # Egypt
    "rafat el hagan": {
        "arabic": "رأفت الهجان",
        "show": "Rafat El Hagan (Egyptian series)",
        "type": "espionage",
        "region": "egypt"
    },
    "king farouk": {
        "arabic": "الملك فاروق",
        "show": "King Farouk (Egyptian series)",
        "type": "political crime",
        "region": "egypt"
    },

    # Iraq
    "saddam hussein": {
        "arabic": "صدام حسين",
        "show": "House of Saddam (HBO)",
        "type": "dictatorship crime",
        "region": "iraq"
    },
    "ali hassan al majid": {
        "arabic": "علي حسن المجيد",
        "show": "Chemical Ali documentary",
        "type": "war crime",
        "region": "iraq"
    },

    # Israel / Palestine
    "fauda real story": {
        "arabic": "فودا القصة الحقيقية",
        "show": "Fauda (Netflix)",
        "type": "political crime",
        "region": "palestine"
    },
}


# ── Alias map ────────────────────────────────────────────────
# Maps common alternate inputs to canonical topic keys.
# Values MUST match an existing key in USA_TOPICS, WORLD_TOPICS, or ARABIC_TOPICS.

ALIASES: dict[str, str] = {
    # USA
    "escobar":              "pablo escobar",
    "joaquin guzman":       "el chapo",
    "el chapo guzman":      "el chapo",
    "dennis rader":         "btk killer",
    "btk":                  "btk killer",
    "henry hill goodfellas":"henry hill",
    "jordan belfort":       "jordan belfort",   # already canonical — kept for clarity
    "unabomber":            "ted kaczynski",
    "night stalker":        "richard ramirez",
    "monster dahmer":       "jeffrey dahmer",
    "godmother of cocaine":  "griselda blanco",
    "nucky johnson":        "al capone",        # closest; boardwalk empire real person

    # World
    "la casa de papel":     "money heist real inspiration",
    "yakuza":               "tokyo vice yakuza",
    "camorra":              "gomorrah mafia",
    "naples mafia":         "gomorrah mafia",
    "sicilian mafia":       "salvatore riina",
    "the serpent":          "charles sobhraj",
    "ronnie kray":          "kray twins",
    "reggie kray":          "kray twins",
    "lord of the skies":    "amado carrillo fuentes",
    "russian mob":          "semion mogilevich",
    "mcmafia":              "semion mogilevich",
    "kim jong nam":         "kim jong nam assassination",

    # Docuseries USA
    "making a murderer":    "steven avery",
    "the jinx":             "robert durst",
    "tiger king":           "joe exotic",
    "wild wild country":    "rajneesh cult",
    "osho":                 "rajneesh cult",
    "bhagwan":              "rajneesh cult",
    "heavens gate":         "heaven's gate cult",
    "keith raniere":        "nxivm cult",
    "nxivm":                "nxivm cult",
    "r kelly":              "r. kelly",
    "mcmillions":           "mcmillion fraud",
    "mcdonalds monopoly":   "mcmillion fraud",

    # World crimes
    "dupont de ligonnes":   "xavier dupont de ligonnes",
    "maddie mccann":        "madeleine mccann",
    "ice valley woman":     "isdal woman",
    "miyazawa family":      "setagaya family murder",
    "angel of death":       "carlos robledo puch",
    "nijvel gang":          "brabant killers",
    "bende van nijvel":     "brabant killers",
    "spy in the bag":       "gareth williams spy",

    # Arabic
    "juhayman":             "juhayman al otaybi",
    "mecca siege":          "juhayman al otaybi",
    "rafat":                "rafat el hagan",
    "al hagan":             "rafat el hagan",
    "farouk":               "king farouk",
    "saddam":               "saddam hussein",
    "chemical ali":         "ali hassan al majid",
    "fauda":                "fauda real story",
    "raya sakina":          "raya and sakina",
    "gezira killer":        "gezira serial killer",
}


def normalize_topic(topic: str) -> str:
    """Resolve an alias to its canonical topic key. Returns unchanged if not an alias."""
    key = topic.lower().strip()
    return ALIASES.get(key, key)


# ── Generator ────────────────────────────────────────────────

def generate_topic(region: str | None = None) -> dict:
    """Return a random topic dict from the given region pool (or all pools)."""
    if region == "usa":
        key, data = random.choice(list(USA_TOPICS.items()))
    elif region == "world":
        key, data = random.choice(list(WORLD_TOPICS.items()))
    elif region == "arabic":
        key, data = random.choice(list(ARABIC_TOPICS.items()))
    else:
        combined = {**USA_TOPICS, **WORLD_TOPICS, **ARABIC_TOPICS}
        key, data = random.choice(list(combined.items()))

    return {
        "keyword":  key,
        "arabic":   data.get("arabic", ""),
        "show":     data.get("show", ""),
        "type":     data.get("type", ""),
        "region":   data.get("region", region or "usa"),
    }


def build_title(topic: dict, lang: str = "en") -> str:
    """Build a YouTube title from a topic dict."""
    if lang == "ar" and topic.get("arabic"):
        return f"القصة الحقيقية لـ {topic['arabic']} مقارنة بـ {topic['show']}"
    return f"The REAL story of {topic['keyword']} vs {topic['show']}"


def lookup_topic(user_input: str) -> dict | None:
    """
    Resolve a user-supplied string (alias or canonical key) to a topic dict.
    Returns None if no match found in any pool.

    Usage:
        topic = lookup_topic("escobar")      # → pablo escobar entry
        topic = lookup_topic("juhayman")     # → juhayman al otaybi entry
    """
    canonical = normalize_topic(user_input)
    combined = {**USA_TOPICS, **WORLD_TOPICS, **ARABIC_TOPICS}
    data = combined.get(canonical)
    if data is None:
        return None
    return {
        "keyword": canonical,
        "arabic":  data.get("arabic", ""),
        "show":    data.get("show", ""),
        "type":    data.get("type", ""),
        "region":  data.get("region", ""),
    }
