# ============================================================
#  topics.py  —  Structured topic registry for Dark Crime Decoded
#  Keys: lowercase, stripped, no duplicates
#  Usage: from topics import USA_TOPICS, WORLD_TOPICS, ARABIC_TOPICS, generate_topic
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
