import streamlit as st
import sqlite3
import re
import os
import json
from pathlib import Path
from datetime import date, datetime
from urllib.parse import quote_plus


# ============================================================
# BASIC CONFIG
# ============================================================

BASE = Path(__file__).parent
DB = BASE / "inquiry_manager.db"

CONTINENTS = [
    "Europe",
    "North America",
    "Latin America",
    "Asia",
    "Middle East",
    "Africa",
    "Oceania",
    "Other"
]

STAGES = [
    "NEW",
    "1ST RESPONSE",
    "QUALIFICATION",
    "QUOTATION",
    "NEGOTIATION",
    "FIRST ORDER",
    "DEAL CLOSING",
    "HOLD",
    "NO RESPONSE",
    "REJECTED",
    "LOST"
]

POTENTIALS = [
    "Review",
    "A",
    "B",
    "C",
    "D",
    "Unqualified"
]

BUSINESS_TYPES = [
    "Distributor",
    "Retailer",
    "Wholesaler",
    "E-commerce",
    "Agent",
    "Trading",
    "Brand",
    "Marketplace",
    "Other"
]

INQUIRY_TYPES = [
    "Wholesale",
    "Distribution",
    "Authorized Reseller",
    "Retail Partnership",
    "Retail",
    "Agency",
    "Sample",
    "Pricing",
    "Authorization",
    "Other"
]


# ============================================================
# DATABASE
# ============================================================

def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = db()

    c.execute("""
        CREATE TABLE IF NOT EXISTS companies(
            company_id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            continent TEXT,
            country TEXT,
            contact_name TEXT,
            position TEXT,
            email TEXT,
            phone TEXT,
            website TEXT,
            instagram TEXT,
            facebook TEXT,
            linkedin TEXT,
            tiktok TEXT,
            business_area TEXT,
            inquiry_type TEXT,
            business_type TEXT,
            inquiry_summary TEXT,
            potential TEXT,
            potential_reason TEXT,
            stage TEXT DEFAULT 'NEW',
            owner TEXT,
            inquiry_date TEXT,
            last_contact_date TEXT,
            next_action TEXT,
            next_action_date TEXT,
            status TEXT DEFAULT 'Active',
            remarks TEXT,
            original_email TEXT,
            created_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS activities(
            activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER,
            activity_date TEXT,
            activity_type TEXT,
            subject TEXT,
            summary TEXT,
            next_action TEXT,
            next_action_date TEXT
        )
    """)

    # Existing DB compatibility
    cols = {
        r[1]
        for r in c.execute("PRAGMA table_info(companies)").fetchall()
    }

    needed = {
        "inquiry_type": "TEXT",
        "business_type": "TEXT",
        "original_email": "TEXT",
        "instagram": "TEXT",
        "facebook": "TEXT",
        "linkedin": "TEXT",
        "tiktok": "TEXT",
        "business_area": "TEXT",
        "potential_reason": "TEXT",
        "remarks": "TEXT",
        "status": "TEXT",
        "next_action": "TEXT",
        "next_action_date": "TEXT"
    }

    for key, typ in needed.items():
        if key not in cols:
            c.execute(
                f"ALTER TABLE companies ADD COLUMN {key} {typ}"
            )

    c.commit()
    c.close()


def rows(sql, params=()):
    c = db()
    result = c.execute(sql, params).fetchall()
    c.close()
    return result


def scalar(sql, params=()):
    c = db()
    value = c.execute(sql, params).fetchone()[0]
    c.close()
    return value


def add_activity(
    company_id,
    activity_type,
    subject,
    summary,
    next_action="",
    next_date=""
):
    c = db()

    c.execute("""
        INSERT INTO activities(
            company_id,
            activity_date,
            activity_type,
            subject,
            summary,
            next_action,
            next_action_date
        )
        VALUES(?,?,?,?,?,?,?)
    """, (
        company_id,
        date.today().isoformat(),
        activity_type,
        subject,
        summary,
        next_action,
        next_date
    ))

    c.commit()
    c.close()


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_value(value):
    if not value:
        return ""

    value = value.strip()
    value = re.sub(r"\s+", " ", value)

    return value.strip(
        " \t\r\n:;-–—"
    )


def clean_company_name(value):
    value = clean_value(value)

    value = re.sub(
        r"\s*(?:Email|Tel|Phone|Mobile|Address)\s*:.*$",
        "",
        value,
        flags=re.I
    )

    return value.strip(
        " .,:;-–—"
    )


# ============================================================
# EMAIL EXTRACTION
# ============================================================

EMAIL_RE = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I
)

URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>]*)?",
    re.I
)


def extract_email(text):
    """
    Priority:
    1. From header
    2. Any email in message
    """

    m = re.search(
        r"^From:\s*(?:.*?<)?([\w.+-]+@[\w.-]+\.[A-Za-z]{2,})",
        text,
        re.I | re.M
    )

    if m:
        return m.group(1).strip()

    m = EMAIL_RE.search(text)

    return m.group(0).strip() if m else ""


def is_probable_person_name(value):
    if not value:
        return False

    x = value.strip()

    if len(x) < 3 or len(x) > 80:
        return False

    if "@" in x:
        return False

    if "http" in x.lower():
        return False

    if "www." in x.lower():
        return False

    bad_words = [
        "company",
        "limited",
        "ltd",
        "inc",
        "corp",
        "group",
        "manager",
        "director",
        "commercial",
        "sales",
        "address",
        "phone",
        "mobile",
        "tel",
        "email",
        "www"
    ]

    if any(
        word in x.lower()
        for word in bad_words
    ):
        return False

    words = x.split()

    if not 2 <= len(words) <= 5:
        return False

    return bool(
        re.match(
            r"^[A-Za-zÀ-ÿ'’.-]+(?:\s+[A-Za-zÀ-ÿ'’.-]+){1,4}$",
            x
        )
    )


def is_generic_sender_name(value):
    """Return True when the From display name is a company/team mailbox label,
    not an individual person's name."""

    if not value:
        return True

    x = clean_value(value).strip('"\' ')
    xl = x.lower()

    generic_terms = [
        "team", "sales team", "sales", "commercial team",
        "marketing team", "export team", "international sales",
        "customer service", "customer support", "support team",
        "info", "contact", "office", "admin", "hello",
        "partnerships", "business development", "procurement",
        "purchasing", "company", "corporation", "group"
    ]

    if any(term == xl or xl.endswith(" " + term) for term in generic_terms):
        return True

    # Company-style legal forms / obvious business names.
    if re.search(
        r"\b(?:UAB|AB|MB|Ltd\.?|LLC|Inc\.?|Corp\.?|GmbH|PLC|S\.A\.?|S\.L\.?|B\.V\.?|Pty)\b",
        x,
        re.I
    ):
        return True

    # A phrase such as "SN Nutrition & Beauty" is not a person's name.
    if "&" in x or "@" in x or "www." in xl:
        return True

    return not is_probable_person_name(x)


def extract_name(text):
    """Extract individual contact name accurately."""

    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1. From header
    m = re.search(
        r"^From:\s*(.*?)\s*<[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}>",
        text,
        re.I | re.M
    )

    if m:
        name = clean_value(m.group(1)).strip('"\' ')

        # Remove company suffix from display name
        name = re.split(r"\s+[-–—]\s+", name)[0].strip()

        if name and not is_generic_sender_name(name):
            if is_probable_person_name(name):
                return name

    # 2. "My name is ..."
    m = re.search(
        r"\bMy name is\s+"
        r"([A-Za-zÀ-ÿ'’-]+(?:\s+[A-Za-zÀ-ÿ'’-]+){1,4})"
        r"(?=\s*[,.\n]|$)",
        text,
        re.I
    )

    if m:
        candidate = clean_value(m.group(1))

        if (
            candidate
            and not is_generic_sender_name(candidate)
            and is_probable_person_name(candidate)
        ):
            return candidate

    # 3. "I'm NAME, POSITION..."
    m = re.search(
        r"\bI['’]m\s+"
        r"([A-Za-zÀ-ÿ'’-]+(?:\s+[A-Za-zÀ-ÿ'’-]+){1,4})"
        r"\s*(?=,|\s+and\s+|\s+at\s+|\s+from\s+)",
        text,
        re.I
    )

    if m:
        candidate = clean_value(m.group(1))

        if (
            candidate
            and not is_generic_sender_name(candidate)
            and is_probable_person_name(candidate)
        ):
            return candidate

    # 4. Signature
    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    closing_words = (
        "best regards",
        "kind regards",
        "warm regards",
        "regards",
        "best",
        "sincerely",
        "thanks",
        "thank you",
        "many thanks"
    )

    for i, line in enumerate(lines):

        if line.lower().rstrip(",!:") in closing_words:

            for candidate in lines[i + 1:i + 6]:

                candidate = re.split(
                    r"\s+[-–—]\s+",
                    candidate
                )[0].strip()

                if (
                    is_probable_person_name(candidate)
                    and not is_generic_sender_name(candidate)
                ):
                    return candidate

    return ""

# ============================================================
# POSITION
# ============================================================

def is_probable_position(value):
    """Return True when a string looks like a person's job title."""

    if not value:
        return False

    value = value.strip()

    if not value:
        return False

    low = value.lower()

    position_keywords = (
        "manager",
        "director",
        "founder",
        "co-founder",
        "owner",
        "officer",
        "president",
        "ceo",
        "cfo",
        "coo",
        "cto",
        "cmo",
        "buyer",
        "head of",
        "chief ",
        "executive",
        "specialist",
        "consultant",
        "acquisition",
        "development",
        "sales",
        "marketing",
        "commercial",
        "purchasing",
        "export",
        "international sales",
        "brand",
        "r&d",
    )

    if any(keyword in low for keyword in position_keywords):
        return True

    return False

def extract_position(text):
    """Extract the full job title, not only the final keyword."""

    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ---------------------------------------------------------
    # 1. Explicit Position / Title
    # ---------------------------------------------------------

    m = re.search(
        r"(?:Position|Title|Job Title|Role)\s*[:\-]\s*([^\n,.;]+)",
        text,
        re.I
    )

    if m:
        return clean_value(m.group(1))

    # ---------------------------------------------------------
    # 2. "I'm NAME, POSITION at COMPANY"
    # ---------------------------------------------------------

    m = re.search(
        r"\bI['’]m\s+"
        r"[A-Za-zÀ-ÿ'’-]+(?:\s+[A-Za-zÀ-ÿ'’-]+){1,4}"
        r"\s*,\s*"
        r"(.{2,80}?)"
        r"\s+\bat\s+"
        r"[A-Z][A-Za-z0-9&.'’\- ]{1,80}"
        r"(?=\s+(?:in|from|for)\b|[.,\n]|$)",
        text,
        re.I
    )

    if m:
        candidate = clean_value(m.group(1))

        if is_probable_position(candidate):
            return candidate

    # ---------------------------------------------------------
    # 3. "POSITION at COMPANY"
    #
    # Example:
    # Brand Acquisition Manager at Simpli GmbH
    # R&D Manager at Beautimport Srl
    # ---------------------------------------------------------

    position_words = (
        r"(?:"
        r"brand acquisition manager|"
        r"business development officer|"
        r"business development manager|"
        r"international sales manager|"
        r"commercial director|"
        r"sales director|"
        r"commercial manager|"
        r"sales manager|"
        r"export manager|"
        r"purchasing manager|"
        r"managing director|"
        r"general manager|"
        r"marketing manager|"
        r"marketing director|"
        r"r\s*&\s*d manager|"
        r"r&d manager|"
        r"business development|"
        r"brand manager|"
        r"account manager|"
        r"project manager|"
        r"product manager|"
        r"country manager|"
        r"sales officer|"
        r"business development officer|"
        r"chief executive officer|"
        r"chief operating officer|"
        r"chief marketing officer|"
        r"ceo|"
        r"coo|"
        r"cmo|"
        r"cto|"
        r"founder|"
        r"co-founder|"
        r"director|"
        r"manager|"
        r"officer|"
        r"buyer"
        r")"
    )

    m = re.search(
        r"\b("
        + position_words +
        r"(?:\s+[A-Za-z&/\-]+){0,3}"
        r")\s+at\s+"
        r"[A-Z][A-Za-z0-9&.'’\- ]{1,80}"
        r"(?=\s+(?:in|from|for)\b|[.,\n]|$)",
        text,
        re.I
    )

    if m:
        candidate = clean_value(m.group(1))

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 4. "NAME, POSITION"
    # ---------------------------------------------------------

    m = re.search(
        r"\b[A-Z][A-Za-zÀ-ÿ'’-]+"
        r"(?:\s+[A-Z][A-Za-zÀ-ÿ'’-]+){1,4}"
        r"\s*,\s*"
        r"([A-Za-z&/\- ]{3,80})"
        r"(?=\s+at\s+|\s+from\s+|[.\n]|$)",
        text
    )

    if m:
        candidate = clean_value(m.group(1))

        if is_probable_position(candidate):
            return candidate

    # ---------------------------------------------------------
    # 5. Signature
    # ---------------------------------------------------------

    lines = [
        x.strip()
        for x in text.splitlines()
        if x.strip()
    ]

    closing_words = (
        "best regards",
        "kind regards",
        "warm regards",
        "regards",
        "best",
        "sincerely",
        "thanks",
        "thank you"
    )

    for i, line in enumerate(lines):

        if line.lower().rstrip(",!:") in closing_words:

            for candidate in lines[i + 1:i + 5]:

                if is_probable_position(candidate):
                    return clean_value(candidate)

    return ""
# ============================================================
# COMPANY
# ============================================================

def extract_company(text, from_name=""):
    """
    Extract company name using context-first rules.

    Priority:
    1. Explicit company field
    2. From display name
    3. Role + at COMPANY
    4. Role + of COMPANY
    5. representing / on behalf of COMPANY
    6. Legal entity name
    7. Signature company
    8. Unknown
    """

    if not text:
        return "Unknown"

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ---------------------------------------------------------
    # Remove email / mailing-list footer noise
    # ---------------------------------------------------------
    footer_markers = [
        "to unsubscribe",
        "unsubscribe from this",
        "manage your subscription",
        "you are receiving this email",
        "to stop receiving",
        "to stop getting",
        "email preferences",
        "privacy policy",
        "terms of service",
    ]

    lines = []

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        lower = stripped.lower()

        if any(marker in lower for marker in footer_markers):
            break

        if re.match(
            r"^(from|sent|to|cc|bcc|subject)\s*:",
            stripped,
            re.I
        ):
            continue

        lines.append(stripped)

    clean_text = "\n".join(lines)

    # ---------------------------------------------------------
    # Candidate cleaner
    # ---------------------------------------------------------
    def clean_company(value):

        if not value:
            return None

        value = value.strip()
        value = value.strip(" \t\r\n:,-")
        value = re.sub(r"[.,;:]+$", "", value).strip()

        if not value:
            return None

        value = re.sub(
            r"^the\s+",
            "",
            value,
            flags=re.I
        ).strip()

        # Email / URL
        if "@" in value:
            return None

        if re.search(r"https?://|www\.", value, re.I):
            return None

        # Sentence fragments
        bad_phrases = [
            "unsubscribe",
            "send an email",
            "receiving this email",
            "stop receiving",
            "manage your subscription",
            "that has been",
            "that has ",
            "which has been",
            "which has ",
            "who has been",
            "we currently",
            "we are ",
            "we have ",
            "currently distribute",
            "has been distributing",
            "have been distributing",
            "looking forward",
            "please ",
            "click here",
        ]

        low = value.lower()

        if any(x in low for x in bad_phrases):
            return None

        # Too long = sentence
        if len(value) > 100:
            return None

        if len(value.split()) > 10:
            return None

        # Obvious non-company values
        bad_exact = {
            "manager",
            "director",
            "founder",
            "co-founder",
            "owner",
            "ceo",
            "cfo",
            "coo",
            "cto",
            "president",
            "vice president",
            "officer",
            "buyer",
            "sales manager",
            "marketing manager",
            "business development officer",
            "brand acquisition manager",
            "r&d manager",
            "company",
            "organization",
            "organisation",
            "unknown",
        }

        if low in bad_exact:
            return None

        return value

    # ---------------------------------------------------------
    # 1. Explicit company field
    # ---------------------------------------------------------
    explicit_patterns = [
        r"(?im)^\s*company\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*company name\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*organization\s*[:\-]\s*(.+?)\s*$",
        r"(?im)^\s*organisation\s*[:\-]\s*(.+?)\s*$",
    ]

    for pattern in explicit_patterns:
        m = re.search(pattern, clean_text)

        if m:
            candidate = clean_company(m.group(1))

            if candidate:
                return candidate

    # ---------------------------------------------------------
    # 2. From display name
    #
    # Anna Maria Palumbo - Beautimport
    # ---------------------------------------------------------
    if from_name:

        display = from_name.strip()

        display = re.sub(
            r"<[^>]+>",
            "",
            display
        ).strip()

        # Company after hyphen
        m = re.search(
            r"\s+-\s*"
            r"([A-Za-z0-9&.'()\- ]{2,80})$",
            display
        )

        if m:
            candidate = clean_company(m.group(1))

            if candidate:
                return candidate

        # Company in parentheses
        m = re.search(
            r"\(([^()]{2,80})\)\s*$",
            display
        )

        if m:
            candidate = clean_company(m.group(1))

            if candidate:
                return candidate

    # ---------------------------------------------------------
    # 3. LEGAL ENTITY AFTER "AT"
    #
    # Examples:
    # Brand Acquisition Manager at Simpli GmbH in Switzerland
    # R&D Manager at Beautimport Srl, an Italian company
    #
    # Important:
    # Stop exactly at the legal entity suffix.
    # ---------------------------------------------------------

    legal_suffix = (
        r"(?:S\.?r\.?l\.?|"
        r"S\.?p\.?A\.?|"
        r"Ltd\.?|"
        r"Limited|"
        r"LLC|"
        r"Inc\.?|"
        r"Incorporated|"
        r"GmbH|"
        r"Corp\.?|"
        r"Corporation|"
        r"S\.A\.?|"
        r"B\.V\.?|"
        r"PLC|"
        r"Pte\.?\s*Ltd\.?)"
    )

    m = re.search(
        r"\bat\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,6})"
        r"\s+"
        + legal_suffix +
        r"(?=\s|[.,\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(
            m.group(1) + " " + m.group(0).split()[-1]
        )

        if candidate:
            return candidate

   
    # ---------------------------------------------------------
    # 3. Role + "at COMPANY"
    #
    # Examples:
    # Brand Acquisition Manager at Simpli GmbH
    # R&D Manager at Beautimport Srl
    # ---------------------------------------------------------

    role_words = (
        r"(?:"
        r"brand acquisition manager|"
        r"business development officer|"
        r"business development manager|"
        r"international sales manager|"
        r"commercial director|"
        r"sales director|"
        r"commercial manager|"
        r"sales manager|"
        r"export manager|"
        r"purchasing manager|"
        r"managing director|"
        r"general manager|"
        r"marketing manager|"
        r"marketing director|"
        r"r\s*&\s*d manager|"
        r"r&d manager|"
        r"business development|"
        r"brand manager|"
        r"account manager|"
        r"project manager|"
        r"product manager|"
        r"country manager|"
        r"sales officer|"
        r"chief executive officer|"
        r"chief operating officer|"
        r"chief marketing officer|"
        r"ceo|"
        r"cfo|"
        r"coo|"
        r"cmo|"
        r"cto|"
        r"founder|"
        r"co-founder|"
        r"director|"
        r"manager|"
        r"officer|"
        r"buyer|"
        r"owner|"
        r"president"
        r")"
    )

    # ---------------------------------------------------------
    # 3-A. Legal entity after "at"
    #
    # This must run BEFORE generic "at COMPANY".
    # ---------------------------------------------------------

    legal_suffix = (
        r"(?:S\.?r\.?l\.?|"
        r"S\.?p\.?A\.?|"
        r"Ltd\.?|"
        r"Limited|"
        r"LLC|"
        r"Inc\.?|"
        r"Incorporated|"
        r"GmbH|"
        r"Corp\.?|"
        r"Corporation|"
        r"S\.A\.?|"
        r"B\.V\.?|"
        r"PLC|"
        r"Pte\.?\s*Ltd\.?)"
    )

    m = re.search(
        r"\b"
        + role_words
        + r"\s+at\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,6})"
        r"\s+"
        + legal_suffix
        + r"(?=\s|[.,\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(
            m.group(1) + " " + re.search(
                legal_suffix,
                m.group(0),
                re.I
            ).group(0)
        )

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 3-B. Generic "at COMPANY"
    #
    # Used when there is no legal suffix.
    # ---------------------------------------------------------

    m = re.search(
        r"\b"
        + role_words
        + r"\s+at\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,5})"
        r"(?=\s+(?:in|from|for|based)\b|[.,\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(m.group(1))

        if candidate:
            return candidate
    # ---------------------------------------------------------
    # 4. "I'm NAME, POSITION at COMPANY"
    # ---------------------------------------------------------
    m = re.search(
        r"\b(?:I'm|I am)\s+"
        r".{1,120}?"
        r"\bat\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,8})"
        r"(?=\s+(?:in|from|for|based)\b|[.,\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(m.group(1))

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 5. Founder / CEO / Director "of COMPANY"
    # ---------------------------------------------------------
    m = re.search(
        r"\b(?:founder|co-founder|owner|ceo|cfo|coo|cto|"
        r"director|manager|president|partner)\s+of\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,8})"
        r"(?=[,.\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(m.group(1))

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 6. "representing COMPANY"
    # ---------------------------------------------------------
    m = re.search(
        r"\b(?:representing|on behalf of|working for)\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,8})"
        r"(?=[,.\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(m.group(1))

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 7. Legal entity
    #
    # Beautimport Srl
    # Simpli GmbH
    # ABC Ltd
    # XYZ LLC
    # ---------------------------------------------------------
    legal_re = re.compile(
        r"\b("
        r"[A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,8}"
        r"\s+"
        r"(?:"
        r"S\.?r\.?l\.?|"
        r"S\.?p\.?A\.?|"
        r"Ltd\.?|"
        r"Limited|"
        r"LLC|"
        r"Inc\.?|"
        r"Incorporated|"
        r"GmbH|"
        r"Corp\.?|"
        r"Corporation|"
        r"S\.A\.?|"
        r"B\.V\.?|"
        r"PLC|"
        r"Pte\.?\s*Ltd\.?"
        r")"
        r")\b",
        re.I
    )

    for m in legal_re.finditer(clean_text):

        candidate = clean_company(m.group(1))

        if candidate:
            return candidate

    # ---------------------------------------------------------
    # 8. Signature company
    # ---------------------------------------------------------
    signoff_re = re.compile(
        r"^(best regards|kind regards|warm regards|"
        r"regards|best|sincerely|thanks|thank you|"
        r"many thanks)\s*[,!:]?$",
        re.I
    )

    for i, line in enumerate(lines):

        if signoff_re.match(line):

            signature_lines = lines[i + 1:i + 7]

            for candidate_line in signature_lines:

                if is_probable_person_name(candidate_line):
                    continue

                if is_probable_position(candidate_line):
                    continue

                candidate = clean_company(candidate_line)

                if candidate:
                    return candidate

    # ---------------------------------------------------------
    # 9. Company explicitly mentioned in "company is ..."
    # ---------------------------------------------------------
    m = re.search(
        r"\b(?:company|organization|organisation)\s+"
        r"(?:is|called|named)\s+"
        r"([A-Z][A-Za-z0-9&.'()\-]*"
        r"(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,8})"
        r"(?=[,.\n]|$)",
        clean_text,
        re.I
    )

    if m:
        candidate = clean_company(m.group(1))

        if candidate:
            return candidate

    return "Unknown"


# ============================================================
# PHONE
# ============================================================

def clean_phone(value):

    value = value.strip()

    value = re.sub(
        r"[^\d+().\-\s]",
        "",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def extract_phone(text):

    explicit_patterns = [
        r"(?:Phone|Tel|Telephone|Mobile|Mob\.|WhatsApp|CP|Contact)\s*[:\-]?\s*(\+?\d[\d\s().\-]{7,25})"
    ]

    for pattern in explicit_patterns:

        m = re.search(
            pattern,
            text,
            re.I
        )

        if m:
            return clean_phone(
                m.group(1)
            )

    # International number
    for m in re.finditer(
        r"\+\d{1,3}[\d\s().\-]{7,25}",
        text
    ):

        phone = clean_phone(
            m.group(0)
        )

        if len(
            re.sub(r"\D", "", phone)
        ) >= 8:

            return phone

    return ""


# ============================================================
# SUBJECT
# ============================================================

def extract_subject(text):

    m = re.search(
        r"^Subject:\s*(.+)$",
        text,
        re.I | re.M
    )

    return (
        clean_value(m.group(1))
        if m
        else ""
    )


# ============================================================
# WEBSITE
# ============================================================

def extract_website(text, email=""):

    ignored_domains = {
        "google.com", "facebook.com", "instagram.com",
        "linkedin.com", "tiktok.com", "microsoft.com",
        "gmail.com", "outlook.com", "hotmail.com",
        "yahoo.com", "naver.com"
    }

    # 1. Explicit website lines / www / http(s) URLs.
    #    Remove email addresses first so the local-part is never
    #    interpreted as a website.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        line_without_email = EMAIL_RE.sub("", line)

        matches = re.findall(
            r"(?:https?://|www\.)[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>]*)?",
            line_without_email,
            re.I
        )

        for url in matches:
            clean = url.rstrip(".,;:)>]")
            domain = re.sub(r"^https?://", "", clean, flags=re.I)
            domain = domain.split("/", 1)[0].lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if domain not in ignored_domains:
                return clean if clean.lower().startswith("http") else "https://" + clean

    # 2. Explicit Website / Homepage field.
    m = re.search(
        r"(?:Official Website|Website|Web|Homepage)\s*[:\-]\s*(https?://[^\s]+|www\.[^\s]+|[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        text,
        re.I
    )
    if m:
        website = m.group(1).rstrip(".,;:)>]")
        return website if website.lower().startswith("http") else "https://" + website

    # 3. Email-domain fallback.
    #    martynas.virbickas@camelia.lt -> https://camelia.lt
    if email and "@" in email:
        domain = email.split("@", 1)[1].strip().lower()
        if domain and "." in domain and domain not in ignored_domains:
            return "https://" + domain

    return ""


# ============================================================
# COUNTRY / CONTINENT
# ============================================================

COUNTRIES = {
    "Canada": "North America",
    "United States": "North America",
    "USA": "North America",
    "U.S.": "North America",

    "Mexico": "Latin America",
    "Brazil": "Latin America",
    "Argentina": "Latin America",
    "Chile": "Latin America",
    "Colombia": "Latin America",
    "Peru": "Latin America",
    "Uruguay": "Latin America",
    "Ecuador": "Latin America",

    "United Kingdom": "Europe",
    "UK": "Europe",
    "France": "Europe",
    "Germany": "Europe",
    "Spain": "Europe",
    "Italy": "Europe",
    "Portugal": "Europe",
    "Netherlands": "Europe",
    "Belgium": "Europe",
    "Poland": "Europe",
    "Czech Republic": "Europe",
    "Czechia": "Europe",
    "Austria": "Europe",
    "Switzerland": "Europe",
    "Sweden": "Europe",
    "Norway": "Europe",
    "Denmark": "Europe",
    "Finland": "Europe",
    "Ireland": "Europe",
    "Romania": "Europe",
    "Hungary": "Europe",
    "Greece": "Europe",
    "Bulgaria": "Europe",
    "Croatia": "Europe",
    "Serbia": "Europe",
    "Slovakia": "Europe",
    "Slovenia": "Europe",
    "Lithuania": "Europe",
    "Latvia": "Europe",
    "Estonia": "Europe",
    "Türkiye": "Europe",
    "Turkey": "Europe",
    "Russia": "Europe",

    "China": "Asia",
    "Japan": "Asia",
    "South Korea": "Asia",
    "Korea": "Asia",
    "Vietnam": "Asia",
    "Thailand": "Asia",
    "India": "Asia",
    "Pakistan": "Asia",
    "Bangladesh": "Asia",
    "Indonesia": "Asia",
    "Malaysia": "Asia",
    "Singapore": "Asia",
    "Philippines": "Asia",

    "Saudi Arabia": "Middle East",
    "UAE": "Middle East",
    "United Arab Emirates": "Middle East",
    "Qatar": "Middle East",
    "Kuwait": "Middle East",
    "Oman": "Middle East",
    "Bahrain": "Middle East",
    "Jordan": "Middle East",
    "Israel": "Middle East",
    "Iran": "Middle East",
    "Iraq": "Middle East",

    "Egypt": "Africa",
    "Morocco": "Africa",
    "South Africa": "Africa",
    "Kenya": "Africa",
    "Nigeria": "Africa",

    "Australia": "Oceania",
    "New Zealand": "Oceania"
}


COUNTRY_DOMAINS = {
    ".lt": "Lithuania",
    ".lv": "Latvia",
    ".ee": "Estonia",
    ".pl": "Poland",
    ".de": "Germany",
    ".fr": "France",
    ".it": "Italy",
    ".es": "Spain",
    ".pt": "Portugal",
    ".nl": "Netherlands",
    ".be": "Belgium",
    ".uk": "United Kingdom",
    ".co.uk": "United Kingdom",
    ".ru": "Russia",
    ".tr": "Türkiye",
    ".jp": "Japan",
    ".kr": "South Korea",
    ".cn": "China",
    ".in": "India",
    ".au": "Australia",
    ".nz": "New Zealand",
    ".br": "Brazil",
    ".mx": "Mexico"
}


PHONE_COUNTRY_CODES = {
    "+370": "Lithuania",
    "+371": "Latvia",
    "+372": "Estonia",
    "+48": "Poland",
    "+49": "Germany",
    "+33": "France",
    "+39": "Italy",
    "+34": "Spain",
    "+351": "Portugal",
    "+31": "Netherlands",
    "+32": "Belgium",
    "+44": "United Kingdom",
    "+7": "Russia",
    "+90": "Türkiye",
    "+81": "Japan",
    "+82": "South Korea",
    "+86": "China",
    "+91": "India",
    "+61": "Australia",
    "+64": "New Zealand",
    "+55": "Brazil",
    "+52": "Mexico",
    "+1": "United States"
}


def infer_country(
    text,
    email="",
    phone="",
    website=""
):

    # 1. Explicit country
    for country in sorted(
        COUNTRIES,
        key=len,
        reverse=True
    ):

        if re.search(
            r"\b" + re.escape(country) + r"\b",
            text,
            re.I
        ):

            return (
                country,
                COUNTRIES[country]
            )

    # 2. Phone
    for code, country in sorted(
        PHONE_COUNTRY_CODES.items(),
        key=lambda x: len(x[0]),
        reverse=True
    ):

        if phone and phone.startswith(code):

            return (
                country,
                COUNTRIES.get(
                    country,
                    ""
                )
            )

        if re.search(
            re.escape(code),
            text
        ):

            return (
                country,
                COUNTRIES.get(
                    country,
                    ""
                )
            )

    # 3. Email domain
    if email and "@" in email:

        domain = "." + email.split(
            "@",
            1
        )[1].lower()

        for suffix, country in sorted(
            COUNTRY_DOMAINS.items(),
            key=lambda x: len(x[0]),
            reverse=True
        ):

            if domain.endswith(suffix):

                return (
                    country,
                    COUNTRIES.get(
                        country,
                        ""
                    )
                )

    # 4. Website
    if website:

        web = website.lower()

        for suffix, country in sorted(
            COUNTRY_DOMAINS.items(),
            key=lambda x: len(x[0]),
            reverse=True
        ):

            if suffix in web:

                return (
                    country,
                    COUNTRIES.get(
                        country,
                        ""
                    )
                )

    return "", ""


# ============================================================
# BUSINESS AREA
# ============================================================

def infer_business_area(
    text,
    company=""
):

    x = (
        text + " " + company
    ).lower()

    if any(
        key in x
        for key in [
            "vaistinė",
            "vaistine",
            "pharmacy",
            "pharmacies",
            "drugstore",
            "drug store"
        ]
    ):

        return (
            "Pharmacy / Retail Pharmacy / Healthcare & Beauty"
        )

    if any(
        key in x
        for key in [
            "cosmetics",
            "cosmetic",
            "beauty",
            "skincare",
            "skin care"
        ]
    ):

        return (
            "Beauty / Cosmetics / Skincare"
        )

    if any(
        key in x
        for key in [
            "e-commerce",
            "ecommerce",
            "online store",
            "online shop",
            "marketplace"
        ]
    ):

        return "E-commerce"

    if any(
        key in x
        for key in [
            "distributor",
            "distribution"
        ]
    ):

        return "Distribution"

    if any(
        key in x
        for key in [
            "retailer",
            "retail"
        ]
    ):

        return "Retail"

    return ""


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(text, subject=""):

    x = (
        text + " " + subject
    ).lower()

    # Inquiry Type
    if any(
        key in x
        for key in [
            "distribution",
            "distributor",
            "official supplier",
            "official distributor",
            "partnership opportunities",
            "distribution partnership"
        ]
    ):

        inquiry_type = "Distribution"

    elif any(
        key in x
        for key in [
            "authorized reseller",
            "reseller"
        ]
    ):

        inquiry_type = "Authorized Reseller"

    elif any(
        key in x
        for key in [
            "wholesale",
            "wholesaler"
        ]
    ):

        inquiry_type = "Wholesale"

    elif any(
        key in x
        for key in [
            "retail",
            "retailer",
            "retail chain",
            "store"
        ]
    ):

        inquiry_type = "Retail"

    elif any(
        key in x
        for key in [
            "agency",
            "agent"
        ]
    ):

        inquiry_type = "Agency"

    elif any(
        key in x
        for key in [
            "sample",
            "testing sample"
        ]
    ):

        inquiry_type = "Sample"

    elif any(
        key in x
        for key in [
            "price",
            "pricing",
            "price list",
            "quotation"
        ]
    ):

        inquiry_type = "Pricing"

    elif any(
        key in x
        for key in [
            "authorization",
            "loa",
            "letter of authorization"
        ]
    ):

        inquiry_type = "Authorization"

    else:

        inquiry_type = "Other"

    # Business Type
    if any(
        key in x
        for key in [
            "distributor",
            "distribution"
        ]
    ):

        business_type = "Distributor"

    elif any(
        key in x
        for key in [
            "pharmacy",
            "pharmacies",
            "drugstore",
            "vaistinė"
        ]
    ):

        business_type = "Retailer"

    elif any(
        key in x
        for key in [
            "e-commerce",
            "ecommerce",
            "online store",
            "online shop",
            "marketplace"
        ]
    ):

        business_type = "E-commerce"

    elif (
        "agent" in x
        or "agency" in x
    ):

        business_type = "Agent"

    elif "trading" in x:

        business_type = "Trading"

    else:

        business_type = "Other"

    return (
        inquiry_type,
        business_type
    )


# ============================================================
# SUMMARY
# ============================================================

def generate_inquiry_summary(text, subject="", country=""):
    x = text.lower()
    market = country or "the market"

    if ("distribution" in x or "distributor" in x) and ("direct" in x or "import" in x or "register" in x):
        parts = [
            f"Direct supply and distribution inquiry for Mary&May in {market}.",
            "The company is seeking direct purchase, local registration and sales authorization."
        ]
        requests = []
        if "moq" in x or "minimum order" in x:
            requests.append("MOQ/opening order value")
        if "price list" in x or "wholesale price" in x:
            requests.append("wholesale pricing")
        if "sample" in x:
            requests.append("samples")
        if "payment terms" in x:
            requests.append("payment terms")
        if "shipping" in x or "lead time" in x:
            requests.append("shipping/lead time")
        if any(k in x for k in ["gmp", "iso 22716", "msds", "registration"]):
            requests.append("regulatory documentation")
        if requests:
            parts.append("Requests include " + ", ".join(requests) + ".")
        return " ".join(parts)

    if "baltic" in x and ("distribution" in x or "distributor" in x):
        return (
            f"Partnership inquiry for official Mary&May distribution in {market} "
            "and the Baltic markets."
        )

    if "distribution" in x or "distributor" in x:
        return f"Partnership inquiry for Mary&May distribution in {market}."

    if "wholesale" in x:
        return "Wholesale inquiry for Mary&May products."

    if "reseller" in x:
        return "Inquiry regarding authorized resale of Mary&May products."

    if subject:
        return subject

    return "Inbound sales inquiry."


# ============================================================
# MASTER EMAIL ANALYZER
# ============================================================

def analyze_email(text):

    subject = extract_subject(text)

    email = extract_email(text)

    phone = extract_phone(text)

    contact_name = extract_name(text)

    position = extract_position(text)

    company = extract_company(text)
    # Company is a required CRM identity field. If it cannot be verified from the email,
    # keep an explicit placeholder instead of leaving it blank.
    company = company or "Unknown"

    website = extract_website(
        text,
        email
    )

    country, continent = infer_country(
        text,
        email=email,
        phone=phone,
        website=website
    )

    inquiry_type, business_type = classify(
        text,
        subject
    )

    business_area = infer_business_area(
        text,
        company
    )

    inquiry_summary = generate_inquiry_summary(
        text,
        subject,
        country
    )

    return {
        "company_name": company,
        "contact_name": contact_name,
        "position": position,
        "email": email,
        "phone": phone,
        "website": website,
        "country": country,
        "continent": continent,
        "inquiry_type": inquiry_type,
        "business_type": business_type,
        "business_area": business_area,
        "inquiry_summary": inquiry_summary,
        "original_email": text
    }


# ============================================================
# RESEARCH LINKS
# ============================================================

def search_urls(company, country, contact="", email="", website=""):

    company = (company or "").strip()
    country = (country or "").strip()
    contact = (contact or "").strip()
    website = (website or "").strip()

    domain = ""
    if email and "@" in email:
        domain = email.split("@", 1)[1].strip().lower()

    # Company search: company name + domain + country gives much better
    # results than searching the contact person's name alone.
    company_parts = []
    if company:
        company_parts.append(f'"{company}"')
    if domain:
        company_parts.append(domain)
    if country:
        company_parts.append(country)
    if not company_parts and contact:
        company_parts.append(f'"{contact}"')

    base_query = " ".join(company_parts).strip()
    q = quote_plus(base_query)

    social_query = quote_plus(" ".join(
        x for x in [
            f'"{company}"' if company else "",
            domain,
            country,
            "Instagram Facebook LinkedIn"
        ] if x
    ))

    # If the official site is known, provide a direct site link.
    official = website
    if not official and domain and domain not in {
        "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "naver.com"
    }:
        official = "https://" + domain

    return {
        "Google Company": f"https://www.google.com/search?q={q}",
        "Google Social": f"https://www.google.com/search?q={social_query}",
        "Bing Company": f"https://www.bing.com/search?q={q}",
        "Instagram Search": "https://www.google.com/search?q=" + quote_plus(f'site:instagram.com "{company}"'),
        "Facebook Search": "https://www.google.com/search?q=" + quote_plus(f'site:facebook.com "{company}"'),
        "LinkedIn Search": "https://www.google.com/search?q=" + quote_plus(f'site:linkedin.com/company "{company}"'),
        "TikTok Search": "https://www.google.com/search?q=" + quote_plus(f'site:tiktok.com "{company}"'),
        "Official Website": official
    }


# ============================================================
# V5 RESEARCH + AI FIELD MAPPING
# ============================================================

MAPPING_FIELDS = [
    "company_name",
    "contact_name",
    "position",
    "email",
    "phone",
    "website",
    "country",
    "continent",
    "inquiry_type",
    "business_type",
    "business_area"
]

FIELD_LABELS = {
    "company_name": "Company",
    "contact_name": "Contact",
    "position": "Position",
    "email": "Email",
    "phone": "Phone",
    "website": "Website",
    "country": "Country",
    "continent": "Continent",
    "inquiry_type": "Inquiry Type",
    "business_type": "Business Type",
    "business_area": "Business Area"
}


def get_ai_api_key():
    # 1) OS environment (recommended)
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if key:
        return key

    # 2) Streamlit secrets
    try:
        key = str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
        if key:
            return key
    except Exception:
        pass

    # 3) Optional local .env file (no python-dotenv dependency required)
    try:
        env_path = BASE / ".env"
        if env_path.exists():
            for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "OPENAI_API_KEY":
                    v = v.strip().strip('\"').strip("'")
                    if v:
                        return v
    except Exception:
        pass

    return ""


def normalize_mapping_value(field, value):
    value = clean_value(str(value or ""))
    if not value:
        return ""
    if field == "continent" and value not in CONTINENTS:
        return value
    return value


def rule_based_mapping(extracted, research):
    mapping = {}
    for field in MAPPING_FIELDS:
        e = normalize_mapping_value(field, extracted.get(field, ""))
        r = normalize_mapping_value(field, research.get(field, ""))
        if r:
            final = r
            source = "Research"
            confidence = "High"
            reason = "Research value is available and is used as the latest verification candidate."
        elif e:
            final = e
            source = "Email"
            confidence = "High"
            reason = "Extracted directly from the inquiry email."
        else:
            final = "Unknown" if field == "company_name" else ""
            source = "System" if field == "company_name" else "—"
            confidence = "Low"
            reason = "Company could not be identified from the available information." if field == "company_name" else "No value available yet."
        mapping[field] = {
            "email": e,
            "research": r,
            "suggestion": final,
            "source": source,
            "confidence": confidence,
            "reason": reason
        }
    return mapping


def ai_field_mapping(extracted, research):
    """OpenAI-assisted mapping using the REST API, with no openai package dependency."""
    key = get_ai_api_key()
    if not key:
        return None, "API key not detected"

    try:
        import urllib.request
        import urllib.error

        payload = {
            "email_extraction": {k: extracted.get(k, "") for k in MAPPING_FIELDS},
            "research": {k: research.get(k, "") for k in MAPPING_FIELDS},
            "allowed_inquiry_types": INQUIRY_TYPES,
            "allowed_business_types": BUSINESS_TYPES,
            "allowed_continents": CONTINENTS
        }
        prompt = (
            "You are a sales CRM data mapping assistant. Compare email extraction and external research. "
            "Return JSON only. For each field return: suggestion, source (Email/Research/Both/System), "
            "confidence (High/Medium/Low), reason. Do not invent missing information. "
            "Prefer verified research when it directly conflicts with an email assumption, but preserve "
            "the email value when research is empty. If company_name is unavailable from both sources, "
            "use Unknown.\n\n" + json.dumps(payload, ensure_ascii=False)
        )
        body = {
            "model": "gpt-4o-mini",
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return valid JSON only."},
                {"role": "user", "content": prompt}
            ]
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))

        content = response_data["choices"][0]["message"]["content"]
        data = json.loads(content)
        base_mapping = rule_based_mapping(extracted, research)
        result = {}
        for field in MAPPING_FIELDS:
            item = data.get(field, {}) if isinstance(data, dict) else {}
            if not isinstance(item, dict):
                item = {}
            base = base_mapping.get(field, {})
            suggestion = normalize_mapping_value(field, item.get("suggestion", "")) or base.get("suggestion", "")
            if field == "company_name" and not suggestion:
                suggestion = "Unknown"
            result[field] = {
                "email": base.get("email", ""),
                "research": base.get("research", ""),
                "suggestion": suggestion,
                "source": item.get("source", base.get("source", "—")),
                "confidence": item.get("confidence", base.get("confidence", "Medium")),
                "reason": item.get("reason", base.get("reason", ""))
            }
        return result, "AI-assisted mapping enabled"
    except Exception as e:
        return None, f"AI unavailable; rule-based mapping used ({type(e).__name__}: {e})"


def ensure_v5_columns():
    c = db()
    cols = {r[1] for r in c.execute("PRAGMA table_info(companies)").fetchall()}
    needed = {
        "email_extraction_json": "TEXT",
        "research_json": "TEXT",
        "mapping_json": "TEXT",
        "research_status": "TEXT",
        "research_date": "TEXT"
    }
    for key, typ in needed.items():
        if key not in cols:
            c.execute(f"ALTER TABLE companies ADD COLUMN {key} {typ}")
    c.commit()
    c.close()


# ============================================================
# INIT
# ============================================================

init_db()
ensure_v5_columns()

st.set_page_config(
    page_title="Global Inquiry Manager",
    page_icon="🌎",
    layout="wide"
)

st.title("🌎 Global Inquiry Manager")
st.caption(
    "Mary & May — Global Inbound Sales Inquiry Management"
)


# ============================================================
# SESSION STATE
# ============================================================

if "page" not in st.session_state:
    st.session_state.page = "Dashboard"

if "analysis" not in st.session_state:
    st.session_state.analysis = None

if "selected_company" not in st.session_state:
    st.session_state.selected_company = None


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("Menu")

    for page_name in [
        "Dashboard",
        "Inquiry Input",
        "Inquiry List",
        "Company Detail"
    ]:

        if st.button(
            page_name,
            use_container_width=True
        ):

            st.session_state.page = page_name
            st.rerun()


page = st.session_state.page


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":

    st.subheader(
        "Global Inquiry Dashboard"
    )

    vals = [
        scalar(
            "SELECT COUNT(*) FROM companies"
        ),

        scalar(
            "SELECT COUNT(*) FROM companies "
            "WHERE stage='NEW'"
        ),

        scalar(
            "SELECT COUNT(*) FROM companies "
            "WHERE stage NOT IN "
            "('DEAL CLOSING','LOST','REJECTED')"
        ),

        scalar(
            "SELECT COUNT(*) FROM companies "
            "WHERE stage='NEGOTIATION'"
        ),

        scalar(
            "SELECT COUNT(*) FROM companies "
            "WHERE stage='FIRST ORDER'"
        ),

        scalar(
            "SELECT COUNT(*) FROM companies "
            "WHERE stage='DEAL CLOSING'"
        ),

        scalar(
            """
            SELECT COUNT(*)
            FROM companies
            WHERE next_action_date<?
            AND next_action_date<>''
            AND stage NOT IN (
                'DEAL CLOSING',
                'LOST',
                'REJECTED'
            )
            """,
            (date.today().isoformat(),)
        )
    ]

    columns = st.columns(7)

    labels = [
        "Total",
        "New",
        "Active",
        "Negotiation",
        "First Order",
        "Closing",
        "Overdue"
    ]

    for column, label, value in zip(
        columns,
        labels,
        vals
    ):

        column.metric(
            label,
            value
        )

    a, b = st.columns(2)

    with a:

        st.markdown(
            "### By Continent"
        )

        data = rows(
            """
            SELECT
                COALESCE(continent,'Unknown') continent,
                COUNT(*) inquiry
            FROM companies
            GROUP BY continent
            ORDER BY inquiry DESC
            """
        )

        st.dataframe(
            [dict(x) for x in data],
            use_container_width=True,
            hide_index=True
        )

    with b:

        st.markdown(
            "### Pipeline"
        )

        data = rows(
            """
            SELECT
                COALESCE(stage,'NEW') stage,
                COUNT(*) inquiry
            FROM companies
            GROUP BY stage
            ORDER BY inquiry DESC
            """
        )

        st.dataframe(
            [dict(x) for x in data],
            use_container_width=True,
            hide_index=True
        )

    st.markdown(
        "### 🔴 Action Required"
    )

    overdue = rows(
        """
        SELECT
            company_id,
            company_name,
            country,
            stage,
            next_action,
            next_action_date
        FROM companies
        WHERE next_action_date<=?
        AND next_action_date<>''
        AND stage NOT IN (
            'DEAL CLOSING',
            'LOST',
            'REJECTED'
        )
        ORDER BY next_action_date
        """,
        (date.today().isoformat(),)
    )

    if overdue:

        st.dataframe(
            [dict(x) for x in overdue],
            use_container_width=True,
            hide_index=True
        )

    else:

        st.success(
            "No overdue actions."
        )


# ============================================================
# INQUIRY INPUT
# ============================================================

elif page == "Inquiry Input":

    st.subheader("📥 New Inquiry — Paste Email")
    st.caption("Paste Email → 2) Analyze → 3) Research → 4) AI Field Mapping → 5) Review → 6) Register")

    email_text = st.text_area(
        "1. Email Original",
        height=330,
        key="v5_email_original",
        placeholder="Paste the complete email here..."
    )

    if st.button("🔎 2. Analyze Inquiry", type="primary", use_container_width=True):
        if not email_text.strip():
            st.warning("메일 원문을 입력하세요.")
        else:
            extracted = analyze_email(email_text)
            extracted.update({
                "potential": "Review",
                "stage": "NEW",
                "owner": "",
                "next_action": "Company / sales channel verification",
                "next_action_date": date.today(),
                "instagram": "",
                "facebook": "",
                "linkedin": "",
                "tiktok": "",
                "potential_reason": "",
                "remarks": ""
            })
            st.session_state.v5_extracted = extracted
            st.session_state.v5_research = {}
            st.session_state.v5_mapping = {}
            st.session_state.v5_final = {}
            st.session_state.v5_researched = False
            st.session_state.v5_mapped = False
            st.rerun()

    extracted = st.session_state.get("v5_extracted")

    if extracted:
        st.divider()
        st.subheader("2. 📧 Email Extraction")
        if extracted.get("company_name"):
            st.success(f"Company detected: **{extracted['company_name']}**")
        else:
            st.warning("회사명을 자동으로 확인하지 못했습니다.")

        ec1, ec2 = st.columns(2)
        with ec1:
            st.text_input("Company", extracted.get("company_name", ""), disabled=True)
            st.text_input("Contact Person", extracted.get("contact_name", ""), disabled=True)
            st.text_input("Position", extracted.get("position", ""), disabled=True)
            st.text_input("Email", extracted.get("email", ""), disabled=True)
            st.text_input("Phone", extracted.get("phone", ""), disabled=True)
            st.text_input("Website", extracted.get("website", ""), disabled=True)
        with ec2:
            st.text_input("Country", extracted.get("country", ""), disabled=True)
            st.text_input("Continent", extracted.get("continent", ""), disabled=True)
            st.text_input("Inquiry Type", extracted.get("inquiry_type", ""), disabled=True)
            st.text_input("Business Type", extracted.get("business_type", ""), disabled=True)
            st.text_input("Business Area", extracted.get("business_area", ""), disabled=True)
            st.text_area("Inquiry Summary", extracted.get("inquiry_summary", ""), height=150, disabled=True)

        st.divider()
        st.subheader("3. 🌐 Research")
        st.caption("Email에서 추출된 값과 별도로 실제 업체 조사 결과를 입력합니다.")
        st.markdown(f"**Research Target: {extracted.get('company_name') or 'Unknown'}**")

        links = search_urls(
            extracted.get("company_name", ""),
            extracted.get("country", ""),
            extracted.get("contact_name", ""),
            extracted.get("email", ""),
            extracted.get("website", "")
        )
        lc = st.columns(4)
        for i, (label, url) in enumerate(links.items()):
            lc[i % 4].markdown(f"[{label}]({url})")

        research = dict(st.session_state.get("v5_research", {}))
        r1, r2 = st.columns(2)
        with r1:
            research["company_name"] = st.text_input("Research — Company", research.get("company_name", extracted.get("company_name", "Unknown")), key="v5_r_company")
            research["contact_name"] = st.text_input("Research — Contact", research.get("contact_name", ""), key="v5_r_contact")
            research["position"] = st.text_input("Research — Position", research.get("position", ""), key="v5_r_position")
            research["email"] = st.text_input("Research — Email", research.get("email", ""), key="v5_r_email")
            research["phone"] = st.text_input("Research — Phone", research.get("phone", ""), key="v5_r_phone")
            research["website"] = st.text_input("Research — Website", research.get("website", ""), key="v5_r_website")
        with r2:
            research["country"] = st.text_input("Research — Country", research.get("country", ""), key="v5_r_country")
            research["continent"] = st.selectbox("Research — Continent", [""] + CONTINENTS, index=([""] + CONTINENTS).index(research.get("continent", "")) if research.get("continent", "") in [""] + CONTINENTS else 0, key="v5_r_continent")
            research["inquiry_type"] = st.selectbox("Research — Inquiry Type", [""] + INQUIRY_TYPES, index=([""] + INQUIRY_TYPES).index(research.get("inquiry_type", "")) if research.get("inquiry_type", "") in [""] + INQUIRY_TYPES else 0, key="v5_r_inquiry")
            research["business_type"] = st.selectbox("Research — Business Type", [""] + BUSINESS_TYPES, index=([""] + BUSINESS_TYPES).index(research.get("business_type", "")) if research.get("business_type", "") in [""] + BUSINESS_TYPES else 0, key="v5_r_business")
            research["business_area"] = st.text_input("Research — Business Area", research.get("business_area", ""), key="v5_r_area")

        rs1, rs2 = st.columns(2)
        research["research_status"] = rs1.selectbox("Research Status", ["Not Started", "Partially Verified", "Verified", "Not Found"], index=["Not Started", "Partially Verified", "Verified", "Not Found"].index(research.get("research_status", "Not Started")), key="v5_r_status")
        research["research_notes"] = rs2.text_area("Research Notes / Investigation Result", research.get("research_notes", ""), height=120, key="v5_r_notes")
        st.session_state.v5_research = research
        st.session_state.v5_researched = research.get("research_status") != "Not Started" or bool(research.get("research_notes"))

        st.divider()
        st.subheader("4. 🤖 AI Field Mapping")
        st.caption("Email 추출값 + Research 결과를 비교하여 최종 후보값을 만듭니다.")

        if st.button("🤖 Run AI Field Mapping", type="primary", use_container_width=True):
            mapping, status = ai_field_mapping(extracted, research)
            if mapping is None:
                mapping = rule_based_mapping(extracted, research)
            st.session_state.v5_mapping = mapping
            st.session_state.v5_mapping_status = status
            st.session_state.v5_mapped = True
            # Initialize final candidates from mapping only after mapping is run.
            st.session_state.v5_final = {
                field: item.get("suggestion", "") for field, item in mapping.items()
            }
            st.rerun()

        if st.session_state.get("v5_mapped"):
            status = st.session_state.get("v5_mapping_status", "Rule-based mapping")
            if status.startswith("AI-assisted"):
                st.success(status)
            elif get_ai_api_key():
                st.warning(f"OPENAI_API_KEY detected, but AI mapping is unavailable. {status}")
            else:
                st.info("AI API not detected — rule-based mapping is being used.")

            mapping = st.session_state.get("v5_mapping", {})
            table = []
            for field in MAPPING_FIELDS:
                item = mapping.get(field, {})
                table.append({
                    "Field": FIELD_LABELS[field],
                    "Email": item.get("email", ""),
                    "Research": item.get("research", ""),
                    "AI / Rule Suggestion": item.get("suggestion", ""),
                    "Source": item.get("source", "—"),
                    "Confidence": item.get("confidence", "Low"),
                    "Reason": item.get("reason", "")
                })
            st.dataframe(table, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("5. ✅ Final Review")
            final = dict(st.session_state.get("v5_final", {}))
            fc1, fc2 = st.columns(2)
            with fc1:
                for field in ["company_name", "contact_name", "position", "email", "phone", "website"]:
                    default_value = final.get(field, "")
                    if field == "company_name" and not default_value:
                        default_value = extracted.get("company_name", "Unknown") or "Unknown"
                    final[field] = st.text_input(f"FINAL — {FIELD_LABELS[field]}", default_value, key=f"v5_final_{field}")
            with fc2:
                final["country"] = st.text_input("FINAL — Country", final.get("country", ""), key="v5_final_country")
                final["continent"] = st.selectbox("FINAL — Continent", CONTINENTS, index=CONTINENTS.index(final.get("continent")) if final.get("continent") in CONTINENTS else 0, key="v5_final_continent")
                final["inquiry_type"] = st.selectbox("FINAL — Inquiry Type", INQUIRY_TYPES, index=INQUIRY_TYPES.index(final.get("inquiry_type")) if final.get("inquiry_type") in INQUIRY_TYPES else 0, key="v5_final_inquiry")
                final["business_type"] = st.selectbox("FINAL — Business Type", BUSINESS_TYPES, index=BUSINESS_TYPES.index(final.get("business_type")) if final.get("business_type") in BUSINESS_TYPES else 0, key="v5_final_business")
                final["business_area"] = st.text_input("FINAL — Business Area", final.get("business_area", ""), key="v5_final_area")
            final["potential"] = st.selectbox("Potential", POTENTIALS, index=POTENTIALS.index(extracted.get("potential", "Review")) if extracted.get("potential") in POTENTIALS else 0, key="v5_final_potential")
            final["stage"] = st.selectbox("Stage", STAGES, index=STAGES.index(extracted.get("stage", "NEW")) if extracted.get("stage") in STAGES else 0, key="v5_final_stage")
            final["owner"] = st.text_input("Owner", extracted.get("owner", ""), key="v5_final_owner")
            final["next_action"] = st.text_input("Next Action", extracted.get("next_action", "Company / sales channel verification"), key="v5_final_next_action")
            final["next_action_date"] = st.date_input("Next Action Date", extracted.get("next_action_date", date.today()), key="v5_final_next_date")
            final["potential_reason"] = st.text_input("Potential Reason", extracted.get("potential_reason", ""), key="v5_final_potential_reason")
            final["remarks"] = st.text_area("Research / Remarks", research.get("research_notes", "") or extracted.get("remarks", ""), key="v5_final_remarks", height=140)
            st.session_state.v5_final = final

            st.divider()
            st.subheader("6. 📥 Register")
            st.markdown("**Registration Preview**")
            st.json({k: final.get(k, "") for k in MAPPING_FIELDS})

            if st.button("✅ Confirm & Register", type="primary", use_container_width=True):
                company_name = clean_value(final.get("company_name", ""))
                email = clean_value(final.get("email", ""))
                if not company_name:
                    st.error("Company 정보를 입력하세요.")
                elif not email:
                    st.error("Email 정보를 확인하세요.")
                else:
                    # "Unknown" is a placeholder, not a real company identity.
                    # Do not treat it as a company-name duplicate. Email is still
                    # checked because the same contact/email may represent an existing inquiry.
                    if company_name.strip().lower() == "unknown":
                        dup = rows("""
                            SELECT company_id, company_name, email FROM companies
                            WHERE (email<>'' AND email=?)
                        """, (email,))
                    else:
                        dup = rows("""
                            SELECT company_id, company_name, email FROM companies
                            WHERE (email<>'' AND email=?)
                               OR (company_name<>'' AND lower(company_name)=lower(?))
                        """, (email, company_name))
                    if dup:
                        st.warning(f"Possible existing company: {dup[0]['company_name']} (ID {dup[0]['company_id']}).")
                        st.info("중복 가능성이 있으므로 현재 등록을 중단했습니다.")
                    else:
                        record = {
                            "company_name": company_name,
                            "continent": final.get("continent", ""),
                            "country": final.get("country", ""),
                            "contact_name": final.get("contact_name", ""),
                            "position": final.get("position", ""),
                            "email": email,
                            "phone": final.get("phone", ""),
                            "website": final.get("website", ""),
                            "instagram": research.get("instagram", ""),
                            "facebook": research.get("facebook", ""),
                            "linkedin": research.get("linkedin", ""),
                            "tiktok": research.get("tiktok", ""),
                            "business_area": final.get("business_area", ""),
                            "inquiry_type": final.get("inquiry_type", ""),
                            "business_type": final.get("business_type", ""),
                            "inquiry_summary": extracted.get("inquiry_summary", ""),
                            "potential": final.get("potential", "Review"),
                            "potential_reason": final.get("potential_reason", ""),
                            "stage": final.get("stage", "NEW"),
                            "owner": final.get("owner", ""),
                            "inquiry_date": date.today().isoformat(),
                            "last_contact_date": date.today().isoformat(),
                            "next_action": final.get("next_action", ""),
                            "next_action_date": str(final.get("next_action_date", date.today())),
                            "status": "Active",
                            "remarks": final.get("remarks", ""),
                            "original_email": extracted.get("original_email", ""),
                            "created_at": datetime.now().isoformat(),
                            "email_extraction_json": json.dumps(extracted, ensure_ascii=False, default=str),
                            "research_json": json.dumps(research, ensure_ascii=False, default=str),
                            "mapping_json": json.dumps(st.session_state.get("v5_mapping", {}), ensure_ascii=False, default=str),
                            "research_status": research.get("research_status", "Not Started"),
                            "research_date": date.today().isoformat() if st.session_state.get("v5_researched") else ""
                        }
                        keys = list(record.keys())
                        c = db()
                        cur = c.execute(
                            f"INSERT INTO companies({','.join(keys)}) VALUES({','.join(['?']*len(keys))})",
                            [record[k] for k in keys]
                        )
                        company_id = cur.lastrowid
                        c.commit()
                        c.close()
                        add_activity(company_id, "Inquiry", "Initial inquiry", extracted.get("inquiry_summary", ""), final.get("next_action", ""), str(final.get("next_action_date", date.today())))
                        st.success(f"Registered: {company_name} (ID {company_id})")
                        for key in list(st.session_state.keys()):
                            if key.startswith("v5_"):
                                st.session_state.pop(key, None)
                        st.rerun()

        else:
            st.info("Research 결과를 입력한 후 **Run AI Field Mapping**을 눌러주세요.")


# ============================================================

# ============================================================

elif page == "Inquiry List":

    st.subheader(
        "Inquiry List"
    )

    query = st.text_input(
        "Search company / contact / country / email"
    )

    a, b, c = st.columns(3)

    stage_filter = a.selectbox(
        "Stage",
        ["All"] + STAGES
    )

    continent_filter = b.selectbox(
        "Continent",
        ["All"] + CONTINENTS
    )

    potential_filter = c.selectbox(
        "Potential",
        ["All"] + POTENTIALS
    )

    sql = """
        SELECT *
        FROM companies
        WHERE 1=1
    """

    params = []

    if query:

        sql += """
            AND (
                company_name LIKE ?
                OR contact_name LIKE ?
                OR country LIKE ?
                OR email LIKE ?
            )
        """

        params += [
            f"%{query}%"
        ] * 4

    if stage_filter != "All":

        sql += " AND stage=?"
        params.append(
            stage_filter
        )

    if continent_filter != "All":

        sql += " AND continent=?"
        params.append(
            continent_filter
        )

    if potential_filter != "All":

        sql += " AND potential=?"
        params.append(
            potential_filter
        )

    data = rows(
        sql + " ORDER BY company_id DESC",
        params
    )

    st.dataframe(
        [
            {
                "ID": x["company_id"],
                "Company": x["company_name"],
                "Country": x["country"],
                "Contact": x["contact_name"],
                "Business": x["business_type"],
                "Potential": x["potential"],
                "Stage": x["stage"],
                "Next Action": x["next_action"],
                "Due": x["next_action_date"]
            }
            for x in data
        ],
        use_container_width=True,
        hide_index=True
    )

    if data:

        company_ids = [
            x["company_id"]
            for x in data
        ]

        def company_label(company_id):

            for item in data:

                if item["company_id"] == company_id:

                    return (
                        f"{item['company_name']} "
                        f"— {item['country']}"
                    )

            return str(company_id)

        selected_id = st.selectbox(
            "Open",
            company_ids,
            format_func=company_label
        )

        if st.button(
            "Open Detail"
        ):

            st.session_state.selected_company = (
                selected_id
            )

            st.session_state.page = (
                "Company Detail"
            )

            st.rerun()


# ============================================================
# COMPANY DETAIL
# ============================================================

elif page == "Company Detail":

    st.subheader(
        "Company Detail"
    )

    all_companies = rows(
        """
        SELECT
            company_id,
            company_name
        FROM companies
        ORDER BY company_name
        """
    )

    if not all_companies:

        st.info(
            "No inquiries yet."
        )

    else:

        ids = [
            x["company_id"]
            for x in all_companies
        ]

        default_id = (
            st.session_state.get(
                "selected_company"
            )
        )

        if default_id not in ids:

            default_id = ids[0]

        selected_id = st.selectbox(
            "Company",
            ids,
            index=ids.index(
                default_id
            ),
            format_func=lambda company_id:
                next(
                    x["company_name"]
                    for x in all_companies
                    if x["company_id"] == company_id
                )
        )

        company = rows(
            """
            SELECT *
            FROM companies
            WHERE company_id=?
            """,
            (selected_id,)
        )[0]

        st.markdown(
            f"## {company['company_name']}"
        )

        st.caption(
            f"{company['country']} | "
            f"{company['continent']} | "
            f"{company['business_type']} | "
            f"{company['stage']} | "
            f"Potential {company['potential']}"
        )

        c1, c2, c3 = st.columns(3)

        with c1:

            st.markdown(
                "**Contact**"
            )

            st.write(
                company["contact_name"]
                or "-"
            )

            st.write(
                company["position"]
                or "-"
            )

            st.write(
                company["email"]
                or "-"
            )

            st.write(
                company["phone"]
                or "-"
            )

        with c2:

            st.markdown(
                "**Company / Website**"
            )

            st.write(
                company["website"]
                or "-"
            )

            st.markdown(
                "**Business Area**"
            )

            st.write(
                company["business_area"]
                or "-"
            )

        with c3:

            st.markdown(
                "**Inquiry**"
            )

            st.write(
                company["inquiry_type"]
                or "-"
            )

            st.markdown(
                "**Next Action**"
            )

            st.write(
                company["next_action"]
                or "-"
            )

            st.write(
                company["next_action_date"]
                or "-"
            )

        st.markdown(
            "### Inquiry Summary"
        )

        st.write(
            company["inquiry_summary"]
            or "-"
        )

        st.markdown(
            "### Potential"
        )

        st.write(
            company["potential_reason"]
            or "-"
        )

        st.markdown(
            "### Social"
        )

        st.write(
            f"Instagram: "
            f"{company['instagram'] or 'Not Found / Not Verified'}"
        )

        st.write(
            f"Facebook: "
            f"{company['facebook'] or 'Not Found / Not Verified'}"
        )

        st.write(
            f"LinkedIn: "
            f"{company['linkedin'] or 'Not Found / Not Verified'}"
        )

        st.write(
            f"TikTok: "
            f"{company['tiktok'] or 'Not Found / Not Verified'}"
        )

        st.markdown(
            "### Research / Remarks"
        )

        st.write(
            company["remarks"]
            or "-"
        )

        st.markdown(
            "### Activity"
        )

        activities = rows(
            """
            SELECT *
            FROM activities
            WHERE company_id=?
            ORDER BY activity_id DESC
            """,
            (selected_id,)
        )

        st.dataframe(
            [dict(x) for x in activities],
            use_container_width=True,
            hide_index=True
        )

        st.markdown(
            "### Original Email"
        )

        st.text_area(
            "Email",
            company["original_email"] or "",
            height=300,
            disabled=True
        )