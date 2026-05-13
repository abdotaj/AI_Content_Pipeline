"""
Topic Risk Escalation System — Dark Crime Decoded Pipeline.

Classifies topics as LOW / MEDIUM / HIGH risk for autonomous generation.

LOW   → auto-generation allowed (serial killers, cults, fraud, etc.)
MEDIUM → allowed with editorial-assist mode (intelligence, controversial cases)
HIGH  → AUTO mode: blocked with Telegram warning.
        MANUAL mode: allowed through with full editorial-assist mode.

Never hard-blocks manual creator choices — only governs autonomous drift.
"""

from __future__ import annotations

# ── Signal lists ──────────────────────────────────────────────────────────────
# Matched case-insensitively against the full topic string.

_HIGH_RISK_SIGNALS: list[str] = [
    # Active Middle-East geopolitical conflicts
    "israel", "palestine", "palestinian", "gaza", "west bank",
    "hamas", "hezbollah", "islamic jihad",
    # Active global conflicts
    "ukraine war", "russia ukraine", "invasion of ukraine",
    "war in ukraine", "donbas",
    # Active terrorism organisations (current operations)
    "isis", "islamic state", "al-qaeda", "al qaeda",
    "boko haram", "al-shabaab",
    # Ethnic / religious conflicts
    "ethnic cleansing", "genocide campaign", "religious war",
    "sectarian conflict", "sectarian violence",
    "uyghur", "rohingya",
    # Modern propaganda-heavy narratives
    "propaganda war", "information war",
    # Kashmir / active territorial disputes
    "kashmir conflict",
    "china taiwan conflict",
]

_MEDIUM_RISK_SIGNALS: list[str] = [
    # Intelligence operations (historical or ongoing)
    "cia operation", "mossad operation", "kgb operation",
    "mi6 operation", "nsa surveillance",
    "covert operation", "black ops",
    # Political-adjacent (but often fine for true crime)
    "political assassination", "coup d'état", "coup detat",
    "regime change operation", "election interference",
    "political prisoner",
    # Drug-war political dimensions
    "drug war policy", "war on drugs",
    # Controversial legal/court territory
    "war crimes tribunal", "international criminal court",
    "crimes against humanity",
    # Historical conflicts (fine to cover, but with care)
    "fauda", "iraq war documentary", "iraq war crime",
    "afghanistan war", "iran hostage",
    "cold war spy", "soviet spy",
]

_LOW_RISK_SIGNALS: list[str] = [
    # Classic true crime — always safe
    "serial killer", "mass murderer", "serial murder",
    "cult", "cult leader",
    "fraud", "scam", "ponzi", "embezzlement", "forgery",
    "kidnapping", "abduction", "disappearance",
    "prison escape", "prison break",
    "forensic", "cold case", "unsolved murder", "unsolved case",
    "organized crime", "mob", "mafia", "yakuza", "triad",
    "cartel", "drug lord", "drug trafficking", "smuggling",
    "heist", "robbery", "art theft",
    "hitman", "contract killer",
    "true crime", "documentary crime",
    # Known safe NICHES from config
    "godfather", "scarface", "narcos", "money heist",
    "breaking bad", "peaky blinders", "goodfellas", "casino",
    "ozark", "the wire", "griselda", "american gangster",
    "donnie brasco", "city of god", "sicario",
    "mcmafia", "gomorrah", "suburra", "tokyo vice",
    "kray twins",
]


def classify_topic_risk(topic_name: str, is_manual: bool = False) -> dict:
    """
    Classify a topic's risk level for autonomous vs manual generation.

    Args:
        topic_name: The topic string to evaluate.
        is_manual:  True if the creator explicitly typed this topic.

    Returns dict with keys:
        risk_level                  : "LOW" | "MEDIUM" | "HIGH"
        selection_mode              : "MANUAL" | "AUTO"
        editorial_mode              : bool — True means softer narration framing
        manual_confirmation_required: bool — True means AUTO mode should skip this
        matched_signals             : list[str] — which signals triggered the level
    """
    low = topic_name.lower()

    matched_high   = [s for s in _HIGH_RISK_SIGNALS   if s in low]
    matched_medium = [s for s in _MEDIUM_RISK_SIGNALS if s in low]
    matched_low    = [s for s in _LOW_RISK_SIGNALS    if s in low]

    if matched_high:
        risk_level = "HIGH"
    elif matched_medium and not matched_low:
        risk_level = "MEDIUM"
    elif matched_medium:
        # Medium signal present even alongside low-risk signal — flag as medium
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    selection_mode              = "MANUAL" if is_manual else "AUTO"
    editorial_mode              = risk_level in ("MEDIUM", "HIGH")
    # HIGH-RISK auto-topics require creator confirmation — pipeline should skip them
    manual_confirmation_required = (risk_level == "HIGH") and (not is_manual)

    all_matched = (matched_high + matched_medium)[:5]

    return {
        "risk_level":                   risk_level,
        "selection_mode":               selection_mode,
        "editorial_mode":               editorial_mode,
        "manual_confirmation_required": manual_confirmation_required,
        "matched_signals":              all_matched,
    }


def get_editorial_assist_prompt(risk_level: str, topic_name: str = "") -> str:
    """
    Return a narration rule block for injection into script prompts.
    Returns empty string for LOW risk — no modification needed.
    """
    if risk_level == "HIGH":
        return (
            "\n══════════════════════════════════════\n"
            "EDITORIAL-ASSIST MODE — HIGH SENSITIVITY\n"
            "══════════════════════════════════════\n"
            "This topic involves geopolitical, religious, or ideological territory.\n\n"
            "NARRATION FRAMING RULES:\n"
            "- Do NOT use authoritative framing: avoid 'the truth was', 'the facts clearly show', "
            "'it is undeniable that', 'the evidence proves'\n"
            "- Use evidential framing: 'reports indicate', 'some believed', 'critics argued', "
            "'according to investigators', 'court documents showed', 'witnesses testified that'\n"
            "- Where documented sources conflict: present the conflict — do NOT take a side\n"
            "- Do NOT editorialize on religion, ethnicity, political ideology, or nationality\n"
            "- Focus on documented events, legal proceedings, and human consequences\n"
            "- Attribute ALL claims to specific sources, investigations, or reports\n"
            "- Present supporter and critic perspectives where relevant and documented\n"
        )
    elif risk_level == "MEDIUM":
        return (
            "\n══════════════════════════════════════\n"
            "EDITORIAL-ASSIST MODE — MODERATE SENSITIVITY\n"
            "══════════════════════════════════════\n"
            "This topic involves intelligence, political, or controversial territory.\n\n"
            "NARRATION FRAMING RULES:\n"
            "- Prefer evidential framing: 'reports indicate', 'investigators found', "
            "'according to court records', 'documents revealed'\n"
            "- Avoid definitive claims where sources are disputed or contested\n"
            "- Attribute controversial claims to their specific source\n"
            "- Focus on documented events, legal proceedings, and verified consequences\n"
        )
    return ""


def log_risk(topic_name: str, risk_info: dict) -> None:
    """Print standardised [RISK] log line."""
    print(
        f"[RISK] topic_risk={risk_info['risk_level']} | "
        f"selection_mode={risk_info['selection_mode']} | "
        f"editorial_mode={risk_info['editorial_mode']} | "
        f"manual_confirmation_required={risk_info['manual_confirmation_required']}"
    )
    if risk_info.get("matched_signals"):
        print(f"[RISK] Triggered by: {risk_info['matched_signals']}")
