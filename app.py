import streamlit as st
import sqlite3
import re
import os
import json
import csv
import io
import hashlib
import hmac
import secrets
from html import escape as html_escape
from pathlib import Path
from datetime import date, datetime
from urllib.parse import quote_plus
from email.parser import Parser
from email.utils import parsedate_to_datetime, parseaddr


# ============================================================
# BASIC CONFIG
# ============================================================

BASE = Path(__file__).parent
DB = Path(os.environ.get("INQUIRY_DB_PATH", str(BASE / "inquiry_manager.db"))).expanduser()


def config_value(name, default=""):
    """Read a deployment setting from environment variables or Streamlit secrets."""
    value = os.environ.get(name)
    if value:
        return value.strip() if isinstance(value, str) else value
    try:
        value = st.secrets.get(name, default)
    except Exception:
        value = default
    return value.strip() if isinstance(value, str) else value


SUPABASE_DB_URL = config_value("SUPABASE_DB_URL")

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
    "2ND RESPONSE",
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

NEXT_ACTIONS = list(STAGES)

NEXT_ACTION_ALIASES = {
    "company / sales channel verification": "QUALIFICATION",
    "company/sales channel verification": "QUALIFICATION",
    "qualify company / sales channel": "QUALIFICATION",
    "qulification": "QUALIFICATION",
    "qualification": "QUALIFICATION",
    "1st response": "1ST RESPONSE",
    "2nd response": "2ND RESPONSE",
    "request missing information": "QUALIFICATION",
    "request company information": "QUALIFICATION",
    "send quotation": "QUOTATION",
    "quotation follow-up": "QUOTATION",
    "negotiation follow-up": "NEGOTIATION",
    "negotiation": "NEGOTIATION",
    "deal closing": "DEAL CLOSING",
    "first order follow-up": "FIRST ORDER",
    "no response follow-up": "NO RESPONSE",
    "hold / revisit": "HOLD",
    "other": "NEW",
}


def canonical_next_action(value, fallback="NEW"):
    value = clean_value(str(value or ""))
    if not value:
        return fallback if fallback in STAGES else "NEW"
    lowered = value.casefold()
    if lowered in NEXT_ACTION_ALIASES:
        return NEXT_ACTION_ALIASES[lowered]
    for stage in STAGES:
        if lowered == stage.casefold():
            return stage
    return fallback if fallback in STAGES else "NEW"

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

DISTRIBUTION_TYPES = [
    "Unknown",
    "Direct",
    "Distributor",
    "Agent",
    "Retail",
    "Online",
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

class CompatRow:
    """Support both numeric and named row lookup for SQLite and psycopg."""
    def __init__(self, names, values):
        self._values = tuple(values)
        self._mapping = dict(zip(names, self._values))

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._mapping[key]


class PostgresCursor:
    def __init__(self, cursor):
        self.cursor = cursor
        self.names = [column.name for column in (cursor.description or [])]

    def _convert(self, row):
        return CompatRow(self.names, row) if row is not None else None

    def fetchone(self):
        return self._convert(self.cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self.cursor.fetchall()]


class PostgresConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        # Queries use qmark placeholders in the SQLite version of the app.
        return PostgresCursor(self.connection.execute(sql.replace("?", "%s"), tuple(params)))

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def company_table_columns(connection):
    if SUPABASE_DB_URL:
        return {
            row["column_name"] for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='companies'"
            ).fetchall()
        }
    return {row[1] for row in connection.execute("PRAGMA table_info(companies)").fetchall()}


def db():
    if SUPABASE_DB_URL:
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("Supabase mode requires psycopg. Install requirements.txt.") from exc
        connection = psycopg.connect(
            SUPABASE_DB_URL,
            sslmode="require",
            connect_timeout=15,
            prepare_threshold=None,
        )
        return PostgresConnection(connection)
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=30)
    c.execute("PRAGMA busy_timeout=30000")
    c.row_factory = sqlite3.Row
    return c


def hash_password(password, salt=None):
    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt_bytes, n=2**14, r=8, p=1, dklen=32
    )
    return salt_bytes.hex(), digest.hex()


def verify_password(password, salt_hex, digest_hex):
    try:
        _, candidate = hash_password(password, bytes.fromhex(salt_hex))
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, TypeError):
        return False


def init_db():
    c = db()
    if not SUPABASE_DB_URL:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    company_id_type = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if SUPABASE_DB_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    activity_id_type = "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" if SUPABASE_DB_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    user_id_column = "username TEXT PRIMARY KEY" if SUPABASE_DB_URL else "username TEXT PRIMARY KEY COLLATE NOCASE"

    c.execute("""
        CREATE TABLE IF NOT EXISTS companies(
            company_id {company_id_type},
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
    """.format(company_id_type=company_id_type))

    c.execute("""
        CREATE TABLE IF NOT EXISTS activities(
            activity_id {activity_id_type},
            company_id INTEGER,
            activity_date TEXT,
            activity_type TEXT,
            subject TEXT,
            summary TEXT,
            next_action TEXT,
            next_action_date TEXT
        )
    """.format(activity_id_type=activity_id_type))

    c.execute("""
        CREATE TABLE IF NOT EXISTS users(
            {user_id_column},
            salt TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
    """.format(user_id_column=user_id_column))

    # Existing DB compatibility
    cols = company_table_columns(c)

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
        "next_action_date": "TEXT",
        "distribution_type": "TEXT DEFAULT 'Unknown'",
        "last_modified": "TEXT",
        "legal_entity": "TEXT"
    }

    for key, typ in needed.items():
        if key not in cols:
            c.execute(
                f"ALTER TABLE companies ADD COLUMN {key} {typ}"
            )

    # Align legacy task labels with their equivalent pipeline stages.
    for legacy_action, stage_action in NEXT_ACTION_ALIASES.items():
        c.execute(
            "UPDATE companies SET next_action=? WHERE lower(trim(next_action))=?",
            (stage_action, legacy_action)
        )
    for stage_action in STAGES:
        c.execute(
            "UPDATE companies SET next_action=? WHERE lower(trim(next_action))=?",
            (stage_action, stage_action.casefold())
        )

    # Older records start with their creation/inquiry date so the idle period
    # remains useful immediately after migration.
    c.execute("""
        UPDATE companies
        SET last_modified=COALESCE(NULLIF(created_at,''), NULLIF(inquiry_date,''), ?)
        WHERE last_modified IS NULL OR last_modified=''
    """, (datetime.now().isoformat(timespec="seconds"),))

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


def extract_inquiry_date(text):
    """Return an email's received/sent date as ISO YYYY-MM-DD and its source."""
    try:
        message = Parser().parsestr(text or "", headersonly=True)
    except Exception:
        message = None

    candidates = []
    if message is not None:
        received = message.get_all("Received", [])
        if received:
            candidates.append(("Received", str(received[0]).rsplit(";", 1)[-1].strip()))
        for header in ("Date", "Sent", "Date Sent"):
            value = message.get(header)
            if value:
                candidates.append((header, str(value).strip()))

    if not candidates:
        for match in re.finditer(
            r"(?im)^\s*(Received|Date|Sent|Date Sent)\s*:\s*(.+?)\s*$", text or ""
        ):
            header, value = match.group(1), match.group(2)
            if header.lower() == "received" and ";" in value:
                value = value.rsplit(";", 1)[-1].strip()
            candidates.append((header, value))

    for source, value in candidates:
        try:
            parsed = parsedate_to_datetime(value)
            return parsed.date().isoformat(), source
        except (TypeError, ValueError, OverflowError):
            pass
        for fmt in (
            "%A, %B %d, %Y %I:%M %p",
            "%a, %B %d, %Y %I:%M %p",
            "%m/%d/%Y %I:%M %p",
            "%d/%m/%Y %H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, fmt).date().isoformat(), source
            except ValueError:
                continue
    return "", ""


def extract_research_fields(text):
    """Extract common labeled company facts from pasted research notes."""
    aliases = {
        "company": "company_name", "company name": "company_name", "name": "company_name",
        "website": "website", "web site": "website", "url": "website",
        "country": "country", "business": "business_area", "business area": "business_area",
        "business type": "business_type", "distribution": "distribution_type",
        "distribution type": "distribution_type", "email": "email", "e-mail": "email",
        "legal entity": "legal_entity", "legal name": "legal_entity",
        "contact": "contact_name", "contact person": "contact_name", "phone": "phone",
    }
    result = {}
    for raw_line in (text or "").splitlines():
        match = re.match(r"^\s*[-*•]?\s*([^:：]{2,50})\s*[:：]\s*(.*?)\s*$", raw_line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1).strip().lower())
        field = aliases.get(label)
        value = clean_value(match.group(2)).rstrip("\\")
        if field and value:
            result[field] = value

    if not result.get("email"):
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text or "", re.I)
        if match:
            result["email"] = match.group(0)
    if not result.get("website"):
        match = re.search(r"https?://[^\s<>]+", text or "", re.I)
        if match:
            result["website"] = match.group(0).rstrip(".,)")
    return result


def unmanaged_days(last_modified):
    if not last_modified:
        return None
    try:
        stamp = datetime.fromisoformat(str(last_modified).replace("Z", "+00:00"))
        if stamp.tzinfo:
            stamp = stamp.astimezone().replace(tzinfo=None)
        return max(0, (datetime.now() - stamp).days)
    except (TypeError, ValueError):
        return None


def format_last_modified(value):
    """Format timestamps for display without seconds."""
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo:
            stamp = stamp.astimezone()
        return stamp.strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return str(value).replace("T", " ")[:16]


def render_horizontal_count_table(headers, counts):
    """Render a compact, equal-width summary row without dataframe canvas rendering."""
    safe_headers = "".join(
        '<th style="background:#fbf3f5;padding:10px 5px;border-bottom:1px solid #f0e1e5;'
        f'border-right:1px solid #f0e1e5;color:#76515d;font-size:.75rem;font-weight:650;white-space:normal;overflow-wrap:anywhere">{html_escape(str(header))}</th>'
        for header in headers
    )
    safe_values = "".join(
        '<td style="padding:12px 14px 12px 5px;font-size:1.05rem;font-weight:650;'
        'border-right:1px solid #f0e1e5;color:#9d526b;text-align:right;background:#fff">'
        f'{html_escape(str(counts.get(header, 0)))}</td>'
        for header in headers
    )
    st.markdown(
        '<div style="width:100%;overflow-x:auto;border:1px solid #f0e1e5;border-radius:11px;margin:0.25rem 0 .3rem">'
        '<table style="width:100%;min-width:900px;table-layout:fixed;border-collapse:collapse;text-align:center">'
        f'<thead><tr>{safe_headers}</tr></thead><tbody><tr>{safe_values}</tr></tbody>'
        '</table></div>',
        unsafe_allow_html=True,
    )


def reset_inquiry_form():
    for state_key in list(st.session_state.keys()):
        if state_key.startswith("v5_") and state_key != "v5_email_original":
            st.session_state.pop(state_key, None)
    # Set the actual text-area widget value so the original email visibly clears.
    st.session_state["v5_email_original"] = ""
    st.session_state.analysis = None


def save_dashboard_company(company_id, distribution_type, stage, potential):
    c = db()
    c.execute(
        "UPDATE companies SET distribution_type=?, stage=?, potential=?, last_modified=? WHERE company_id=?",
        (distribution_type, stage, potential, datetime.now().isoformat(timespec="seconds"), company_id)
    )
    c.commit()
    c.close()


def clean_company_name(value):
    value = clean_value(value)

    value = re.sub(
        r"\s*(?:Email|Tel(?:éfono)?|Phone|Mobile|Address)\s*:.*$",
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
    1. Sender in the email header
    2. Labeled contact email in the body
    3. First email in the body as fallback
    """
    message = Parser().parsestr(text or "", headersonly=True)
    sender = parseaddr(message.get("From", ""))[1]
    if sender and EMAIL_RE.fullmatch(sender):
        return sender.strip()

    parts = re.split(r"\n\s*\n", text or "", maxsplit=1)
    body = parts[1] if len(parts) > 1 else (text or "")
    labeled = re.search(
        r"(?im)^\s*(?:contact\s+)?(?:e-?mail|email\s+address)\s*[:：]\s*([^\s<>;,]+)",
        body,
    )
    if labeled:
        match = EMAIL_RE.search(labeled.group(1))
        if match:
            return match.group(0).strip()
    match = EMAIL_RE.search(body)
    return match.group(0).strip() if match else ""


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
        r"(?:S\.?r\.?l\.?|S\.?l\.?|"
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
        r"(?:S\.?r\.?l\.?|S\.?l\.?|"
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
        r"S\.?r\.?l\.?|S\.?l\.?|"
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

    # A labeled company website is stronger evidence than incidental links.
    m = re.search(
        r"(?im)^\s*(?:official\s+website|website|web\s*site|web|homepage)\s*[:\-]\s*(https?://[^\s<>]+|www\.[^\s<>]+|[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        text or "",
    )
    if m:
        website = m.group(1).rstrip(".,;:)>]")
        return website if website.lower().startswith("http") else "https://" + website

    # The sender's organization domain is usually stronger than incidental links.
    if email and "@" in email:
        domain = email.split("@", 1)[1].strip().lower()
        if domain and "." in domain and not any(
            domain == ignored or domain.endswith("." + ignored)
            for ignored in ignored_domains
        ):
            return "https://" + domain

    # Otherwise accept a direct URL but ignore common social and mail services.
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
            if not any(domain == ignored or domain.endswith("." + ignored) for ignored in ignored_domains):
                return clean if clean.lower().startswith("http") else "https://" + clean

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

    # A country mentioned in sales copy is not enough to establish location.
    location_parts = [
        match.group(1)
        for match in re.finditer(
            r"(?im)^\s*(?:country|location|address|based\s+in|located\s+in|headquarters(?:\s+in)?)\s*[:\-]?\s*(.{1,160})$",
            text or "",
        )
    ]
    if not location_parts:
        location_match = re.search(
            r"(?i)\b(?:based|located|headquartered)\s+in\s+([A-Z][A-Za-z .'-]{1,50})",
            text or "",
        )
        if location_match:
            location_parts.append(location_match.group(1))
    location_text = "\n".join(location_parts)

    # 1. Explicit country/location fields
    for country in sorted(
        COUNTRIES,
        key=len,
        reverse=True
    ):

        if re.search(
            r"\b" + re.escape(country) + r"\b",
            location_text,
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

            hostname = re.sub(r"^https?://", "", web).split("/", 1)[0].split(":", 1)[0]
            if hostname.endswith(suffix):

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

    message_headers = Parser().parsestr(text or "", headersonly=True)
    sender_name = parseaddr(message_headers.get("From", ""))[0]
    contact_name = extract_name(text)

    position = extract_position(text)

    company = extract_company(text, from_name=sender_name)
    legal_entity = ""
    legal_form = r"(?:S\.?r\.?l\.?|S\.?l\.?|S\.?p\.?A\.?|Ltd\.?|Limited|LLC|Inc\.?|GmbH|S\.?A\.?|B\.?V\.?|PLC|Pte\.?\s*Ltd\.?)"
    if company and re.search(r"\s" + legal_form + r"$", company, re.I):
        legal_entity = company
        company = re.sub(r"\s" + legal_form + r"$", "", company, flags=re.I).strip()
        # A country prefix is sometimes included in a legal/trading name line.
        company = re.sub(r"^(?:Spain|España)\s+", "", company, flags=re.I).strip()
    # Capture a legal entity written with the company, including a leading country label.
    if not legal_entity:
        legal_match = re.search(
            r"(?im)^\s*((?:Spain|España)\s+)?([A-Z][A-Za-z0-9&.'()\-]*(?:\s+[A-Z][A-Za-z0-9&.'()\-]*){0,5}\s+" + legal_form + r")\s*$",
            text
        )
        if legal_match:
            legal_entity = clean_value(legal_match.group(0))
            if company == "Unknown":
                company = re.sub(r"\s+" + legal_form + r"$", "", legal_match.group(2), flags=re.I).strip()
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
    inquiry_date, inquiry_date_source = extract_inquiry_date(text)

    return {
        "inquiry_date": inquiry_date or date.today().isoformat(),
        "inquiry_date_source": inquiry_date_source or "No email date found; current date used",
        "company_name": company,
        "legal_entity": legal_entity,
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
    "business_area",
    "legal_entity",
    "distribution_type"
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
    "business_area": "Business Area",
    "legal_entity": "Legal Entity",
    "distribution_type": "Distribution"
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
            "allowed_distribution_types": DISTRIBUTION_TYPES,
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
    cols = company_table_columns(c)
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


def provision_initial_admin():
    username = str(config_value("INQUIRY_ADMIN_USERNAME", "")).strip().casefold()
    password = str(config_value("INQUIRY_ADMIN_PASSWORD", ""))
    if not username or not password or len(password) < 8:
        return
    if rows("SELECT 1 FROM users WHERE lower(username)=?", (username,)):
        return
    salt, password_hash = hash_password(password)
    c = db()
    c.execute(
        "INSERT INTO users(username,salt,password_hash,role,is_active,created_at) VALUES(?,?,?,'admin',1,?) "
        "ON CONFLICT(username) DO NOTHING",
        (username, salt, password_hash, datetime.now().isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()


def authenticate_user():
    active_user = st.session_state.get("authenticated_username")
    if active_user:
        user = rows(
            "SELECT username, role FROM users WHERE lower(username)=? AND is_active=1",
            (str(active_user).casefold(),),
        )
        if user:
            st.session_state.authenticated_role = user[0]["role"]
            return True
        st.session_state.pop("authenticated_username", None)
        st.session_state.pop("authenticated_role", None)

    st.markdown("""
    <style>
      [data-testid="stAppViewContainer"] > .main .block-container {padding-top:8vh;}
      [data-testid="stTextInput"] label {color:#344054 !important;font-size:0.9rem !important;font-weight:600 !important;}
      [data-testid="stTextInput"] input {height:2.65rem !important;border-radius:8px !important;border-color:#d0d5dd !important;color:#101828 !important;font-size:0.95rem !important;}
      [data-testid="stFormSubmitButton"] button {width:auto !important;min-width:120px;padding:0.45rem 1.15rem !important;border-radius:8px !important;}
    </style>
    """, unsafe_allow_html=True)
    st.markdown("<div style='height:5vh'></div>", unsafe_allow_html=True)
    login_left, login_center, login_right = st.columns([1, 1.05, 1])
    with login_center:
        with st.container(border=True):
            st.markdown("<div style='color:#b54766;font-size:0.72rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin-bottom:.35rem'>MARY &amp; MAY · GLOBAL SALES</div>", unsafe_allow_html=True)
            st.markdown("## 🌎 Global Inquiry Manager")
            st.caption("팀 계정으로 로그인하세요")
            if scalar("SELECT COUNT(*) FROM users") == 0:
                st.warning("관리자 계정이 아직 설정되지 않았습니다. 서버 계정 설정 후 앱을 다시 시작하세요.")

            with st.form("team_sign_in"):
                username = st.text_input("User ID", placeholder="아이디 입력")
                password = st.text_input("Password", type="password", placeholder="비밀번호 입력")
                submitted = st.form_submit_button("로그인", type="primary")
    if submitted:
        user = rows(
            "SELECT username, salt, password_hash, role FROM users WHERE lower(username)=? AND is_active=1",
            (username.strip().casefold(),),
        )
        if user and verify_password(password, user[0]["salt"], user[0]["password_hash"]):
            st.session_state.authenticated_username = user[0]["username"]
            st.session_state.authenticated_role = user[0]["role"]
            st.rerun()
        st.error("User ID or password is incorrect.")
    return False


def create_or_reset_team_account():
    if st.session_state.get("authenticated_role") != "admin":
        st.session_state.team_account_status = ("error", "Administrator access is required.")
        return
    username = str(st.session_state.get("team_account_username", "")).strip()
    password = str(st.session_state.get("team_account_password", ""))
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._@+-]{2,99}", username):
        st.session_state.team_account_status = ("error", "User ID must be 3–100 characters using letters, numbers, ., _, @, +, or -.")
        return
    if len(password) < 8:
        st.session_state.team_account_status = ("error", "Use a password with at least 8 characters.")
        return
    username = username.casefold()
    salt, password_hash = hash_password(password)
    c = db()
    c.execute(
        "INSERT INTO users(username,salt,password_hash,role,is_active,created_at) VALUES(?,?,?,'user',1,?) "
        "ON CONFLICT(username) DO UPDATE SET salt=excluded.salt,password_hash=excluded.password_hash,is_active=1",
        (username, salt, password_hash, datetime.now().isoformat(timespec="seconds")),
    )
    c.commit()
    c.close()
    st.session_state.team_account_status = ("success", f"Account ready for {username}. Share the ID and password with that team member securely.")
    st.session_state.team_account_username = ""
    st.session_state.team_account_password = ""


# ============================================================
# INIT
# ============================================================

init_db()
ensure_v5_columns()
provision_initial_admin()

st.set_page_config(
    page_title="Global Inquiry Manager",
    page_icon="🌎",
    layout="wide"
)

if not authenticate_user():
    st.stop()

st.markdown("""
<style>
    [data-testid="stDataEditor"] {font-size: 0.82rem;}
    /* Keep the wide management grid inside the viewport on narrow screens. */
    .st-key-company-management-table {
        width: 100%;
        max-width: 100%;
        min-width: 0;
    }
    .st-key-company-management-table [data-testid="stDataEditor"] {
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
    }
    .st-key-company-management-table [data-testid="stDataEditor"] > div {
        max-width: 100% !important;
        min-width: 0 !important;
    }
    @media (max-width: 768px) {
        [data-testid="stMainBlockContainer"] {
            width: 100% !important;
            max-width: 100vw !important;
            padding-left: .7rem !important;
            padding-right: .7rem !important;
            box-sizing: border-box !important;
        }
        .st-key-company-management-table,
        .st-key-company-management-table [data-testid="stDataEditor"] {
            width: 100% !important;
            max-width: 100% !important;
            min-width: 0 !important;
            box-sizing: border-box !important;
        }
    }
    .dashboard-hero {padding:1.35rem 1.6rem;margin:.2rem 0 1.25rem;border:1px solid #f0dfe4;border-radius:18px;
        background:linear-gradient(115deg,#fffaf8 0%,#fbf3f6 58%,#f6eef3 100%);}
    .dashboard-eyebrow {font-size:.68rem;font-weight:750;letter-spacing:.16em;color:#a66d7e;text-transform:uppercase;margin-bottom:.45rem;}
    .dashboard-title {font-size:1.8rem;line-height:1.18;font-weight:720;letter-spacing:-.035em;color:#30282c;}
    .dashboard-subtitle {font-size:.88rem;color:#766b70;margin-top:.45rem;}
    .dashboard-period-label {font-size:.68rem;font-weight:750;letter-spacing:.1em;text-transform:uppercase;color:#a66d7e;}
    [data-testid="stTextInput"] label,
    [data-testid="stTextArea"] label,
    [data-testid="stSelectbox"] label,
    [data-testid="stDateInput"] label {color:#344054 !important; font-weight:600 !important;}
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea,
    [data-testid="stSelectbox"] div[data-baseweb="select"] > div,
    [data-testid="stDateInput"] input {color:#1d2939 !important; font-size:0.92rem !important;}
    [data-testid="stTextInput"] input:disabled,
    [data-testid="stTextArea"] textarea:disabled {
        color:#344054 !important; -webkit-text-fill-color:#344054 !important;
        opacity:1 !important; background:#f2f4f7 !important;
    }
</style>
""", unsafe_allow_html=True)


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
    st.caption(f"Signed in: {st.session_state.get('authenticated_username', '')}")

    for page_name in [
        "Dashboard",
        "Inquiry Input"
    ]:

        if st.button(
            page_name,
            use_container_width=True
        ):

            st.session_state.page = page_name
            st.rerun()

    if st.session_state.get("authenticated_role") == "admin":
        with st.expander("Team Accounts"):
            st.caption("Create an account or enter an existing ID to reset its password.")
            with st.form("team_account_form"):
                st.text_input("User ID", key="team_account_username")
                st.text_input("Temporary Password (8+ characters)", type="password", key="team_account_password")
                st.form_submit_button("Create / Reset Account", on_click=create_or_reset_team_account, use_container_width=True)
            account_status = st.session_state.pop("team_account_status", None)
            if account_status:
                if account_status[0] == "success":
                    st.success(account_status[1])
                else:
                    st.error(account_status[1])
            st.markdown("**Active accounts**")
            for account in rows("SELECT username, role FROM users WHERE is_active=1 ORDER BY role DESC, lower(username)"):
                st.text(f"{account['username']} · {account['role']}")

    if st.sidebar.button("Sign Out", use_container_width=True):
        st.session_state.pop("authenticated_username", None)
        st.session_state.pop("authenticated_role", None)
        st.rerun()


page = st.session_state.page
if page in {"Inquiry List", "Company Detail"}:
    page = "Dashboard"
    st.session_state.page = page


# ============================================================
# DASHBOARD
# ============================================================

if page == "Dashboard":

    st.markdown(
        '<section class="dashboard-hero">'
        '<div class="dashboard-eyebrow">MARY &amp; MAY · GLOBAL INBOUND</div>'
        '<div class="dashboard-title">🌎 Global Inquiry Dashboard</div>'
        '<div class="dashboard-subtitle">A clear view of incoming opportunities by sales stage and market.</div>'
        '</section>',
        unsafe_allow_html=True,
    )

    inquiry_date_bounds = rows(
        "SELECT MIN(NULLIF(inquiry_date,'')) AS first_date, MAX(NULLIF(inquiry_date,'')) AS last_date FROM companies"
    )
    today = date.today()
    try:
        default_stats_start = date.fromisoformat(str(inquiry_date_bounds[0]["first_date"])[:10])
    except (ValueError, TypeError):
        default_stats_start = today
    try:
        latest_inquiry_date = date.fromisoformat(str(inquiry_date_bounds[0]["last_date"])[:10])
        default_stats_end = max(today, latest_inquiry_date)
    except (ValueError, TypeError):
        default_stats_end = today
    with st.container(border=True):
        st.markdown('<div class="dashboard-period-label">📅 STATISTICS PERIOD</div>', unsafe_allow_html=True)
        period_start_col, period_end_col = st.columns(2, gap="small")
        stats_start = period_start_col.date_input(
            "From", value=default_stats_start, key="dashboard_stats_start"
        )
        stats_end = period_end_col.date_input(
            "To", value=default_stats_end, key="dashboard_stats_end"
        )
        if stats_start > stats_end:
            stats_start, stats_end = stats_end, stats_start
            st.info("시작일이 종료일보다 늦어 두 날짜를 순서대로 적용했습니다.")
        stats_start_text = stats_start.isoformat()
        stats_end_text = stats_end.isoformat()
        st.caption(f"문의일 기준 · {stats_start_text} – {stats_end_text}")

    stage_counts = {
        row["stage"]: row["inquiry"]
        for row in rows(
            "SELECT COALESCE(stage,'NEW') AS stage, COUNT(*) AS inquiry "
            "FROM companies WHERE inquiry_date BETWEEN ? AND ? GROUP BY stage",
            (stats_start_text, stats_end_text),
        )
    }
    stage_summary = {stage_name: stage_counts.get(stage_name, 0) for stage_name in STAGES}
    with st.container(border=True):
        st.markdown("#### 📈 Inquiries by Stage")
        render_horizontal_count_table(STAGES, stage_summary)

    continent_data = rows(
        """
        SELECT COALESCE(NULLIF(continent,''),'Unknown') AS continent, COUNT(*) AS inquiry
        FROM companies
        WHERE inquiry_date BETWEEN ? AND ?
        GROUP BY COALESCE(NULLIF(continent,''),'Unknown')
        """,
        (stats_start_text, stats_end_text),
    )
    continent_headers = CONTINENTS + ["Unknown"]
    continent_summary = {continent: 0 for continent in continent_headers}
    for item in continent_data:
        label = item["continent"]
        if label not in continent_summary:
            label = "Other"
        continent_summary[label] += item["inquiry"]
    with st.container(border=True):
        st.markdown("#### 🌍 By Continent")
        render_horizontal_count_table(continent_headers, continent_summary)

    st.divider()
    st.markdown("#### 🏢 Company Management")
    st.caption("회사명을 클릭하면 상세 정보가 아래에 표시됩니다. 삭제할 문의는 표 오른쪽 끝의 Delete 체크박스로 선택하세요.")
    deleted_names = st.session_state.pop("dashboard_deleted_notice", None)
    if deleted_names:
        st.success(f"Deleted inquiries: {deleted_names}")
    managed_companies = rows(
        "SELECT company_id, company_name, continent, country, distribution_type, stage, potential, "
        "next_action, next_action_date, inquiry_date, last_modified "
        "FROM companies ORDER BY "
        "CASE WHEN next_action_date<>'' AND next_action_date<=? "
        "AND stage NOT IN ('DEAL CLOSING','LOST','REJECTED') THEN 0 ELSE 1 END, "
        "next_action_date, lower(company_name)",
        (date.today().isoformat(),)
    )
    idle_threshold = st.number_input(
        "Highlight companies unchanged for this many days", min_value=1,
        max_value=3650, value=30, step=1, key="dashboard_idle_threshold"
    )
    if managed_companies:
        table_rows = []
        originals = {item["company_id"]: item for item in managed_companies}
        company_ids = [item["company_id"] for item in managed_companies]
        has_company_button = hasattr(st.column_config, "ButtonColumn")

        def open_company_from_table(company_ids):
            click = st.session_state.get("dashboard_company_detail_click")
            if click is None:
                return
            row_index = click["row"] if isinstance(click, dict) else click.row
            if 0 <= row_index < len(company_ids):
                st.session_state.dashboard_detail_company = company_ids[row_index]

        next_action_options = list(STAGES)
        for row_number, item in enumerate(managed_companies, start=1):
            elapsed = unmanaged_days(item["inquiry_date"])
            idle = unmanaged_days(item["last_modified"])
            due = (item["next_action_date"] or "")[:10]
            try:
                due_value = date.fromisoformat(due) if due else None
            except ValueError:
                due_value = None
            table_rows.append({
                "Company ID": item["company_id"],
                "No.": row_number,
                "Delete": False,
                "Company": item["company_name"] or "Unnamed",
                "Continent": item["continent"] or "Unknown",
                "Country": item["country"] or "Unknown",
                "Distribution": item["distribution_type"] if item["distribution_type"] in DISTRIBUTION_TYPES else "Unknown",
                "Stage": item["stage"] if item["stage"] in STAGES else "NEW",
                "Potential": item["potential"] if item["potential"] in POTENTIALS else "Review",
                "Next Action": canonical_next_action(
                    item["next_action"], item["stage"] if item["stage"] in STAGES else "NEW"
                ),
                "Next Action Date": due_value,
                "Inquiry Date": item["inquiry_date"] or "—",
                "Last Modified": format_last_modified(item["last_modified"]),
                "Elapsed Days": elapsed if elapsed is not None else "—",
                "Days Since Update": idle if idle is not None else "—",
            })
            if not has_company_button:
                table_rows[-1]["Open Detail"] = item["company_id"] == st.session_state.get("dashboard_detail_company")

        with st.container(key="company-management-table"):
            edited = st.data_editor(
                table_rows,
                key=f"dashboard_company_editor_v4_{st.session_state.get('dashboard_editor_version', 0)}",
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                disabled=["Company ID", "No.", "Company", "Continent", "Country", "Inquiry Date", "Last Modified", "Elapsed Days", "Days Since Update"],
                column_order=(["No.", "Company", "Continent", "Country", "Distribution", "Stage", "Potential", "Next Action", "Next Action Date", "Inquiry Date", "Last Modified", "Elapsed Days", "Days Since Update", "Delete"] if has_company_button else ["No.", "Open Detail", "Company", "Continent", "Country", "Distribution", "Stage", "Potential", "Next Action", "Next Action Date", "Inquiry Date", "Last Modified", "Elapsed Days", "Days Since Update", "Delete"]),
                column_config={
                    "No.": st.column_config.NumberColumn("No.", width=45, alignment="center"),
                    "Delete": st.column_config.CheckboxColumn("Delete", help="Select inquiries for deletion.", width=60),
                    **({"Open Detail": st.column_config.CheckboxColumn("Select company", width="small")} if not has_company_button else {}),
                    "Company": (
                        st.column_config.ButtonColumn(
                            "Company", type="tertiary", width=245, alignment="left",
                            on_click=open_company_from_table, args=(company_ids,),
                            key="dashboard_company_detail_click"
                        ) if has_company_button else st.column_config.TextColumn("Company", width=245)
                    ),
                    "Continent": st.column_config.TextColumn("Continent", width=120),
                    "Country": st.column_config.TextColumn("Country", width=120),
                    "Distribution": st.column_config.SelectboxColumn("Distribution", options=DISTRIBUTION_TYPES, required=True, width=125),
                    "Stage": st.column_config.SelectboxColumn("Stage", options=STAGES, required=True, width=130),
                    "Potential": st.column_config.SelectboxColumn("Potential", options=POTENTIALS, required=True, width=75),
                    "Next Action": st.column_config.SelectboxColumn(
                        "Next Action", options=STAGES, required=False, width=145
                    ),
                    "Next Action Date": st.column_config.DateColumn("Next Action Date", format="YYYY-MM-DD", width=145),
                    "Inquiry Date": st.column_config.TextColumn("Inquiry Date", width=95),
                    "Last Modified": st.column_config.TextColumn("Last Modified", width=120),
                    "Elapsed Days": st.column_config.NumberColumn("Elapsed Days", width=75),
                    "Days Since Update": st.column_config.NumberColumn("Days Idle", width=70),
                }
            )
        edited_records = edited.to_dict("records") if hasattr(edited, "to_dict") else edited
        action_cols = st.columns([1, 1, 6])
        save_clicked = action_cols[0].button("Save Table Changes", type="primary", key="dashboard_save_table")
        delete_clicked = action_cols[1].button("Delete Selected", key="dashboard_delete_selected")
        if not has_company_button:
            selected_rows = [row for row in edited_records if row.get("Open Detail")]
            if selected_rows:
                st.session_state.dashboard_detail_company = int(selected_rows[0]["Company ID"])

        selected_delete_ids = [
            int(row["Company ID"]) for row in edited_records if row.get("Delete")
        ]
        if delete_clicked:
            if selected_delete_ids:
                st.session_state.dashboard_pending_delete_ids = selected_delete_ids
                st.rerun()
            else:
                st.warning("삭제할 업체의 체크박스를 먼저 선택하세요.")

        pending_delete_ids = st.session_state.get("dashboard_pending_delete_ids", [])
        pending_delete_ids = [company_id for company_id in pending_delete_ids if company_id in originals]
        if pending_delete_ids:
            pending_names = [originals[company_id]["company_name"] or f"Inquiry {company_id}" for company_id in pending_delete_ids]
            st.warning(f"선택한 {len(pending_delete_ids)}개 문의와 관련 활동 기록을 삭제할까요? 이 작업은 되돌릴 수 없습니다.\n\n" + ", ".join(pending_names))
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("Confirm Delete", type="primary", key="dashboard_confirm_delete"):
                c = db()
                placeholders = ",".join("?" for _ in pending_delete_ids)
                c.execute(f"DELETE FROM activities WHERE company_id IN ({placeholders})", pending_delete_ids)
                c.execute(f"DELETE FROM companies WHERE company_id IN ({placeholders})", pending_delete_ids)
                c.commit()
                c.close()
                st.session_state.dashboard_deleted_notice = ", ".join(pending_names)
                st.session_state.pop("dashboard_pending_delete_ids", None)
                st.session_state.dashboard_editor_version = st.session_state.get("dashboard_editor_version", 0) + 1
                if st.session_state.get("dashboard_detail_company") in pending_delete_ids:
                    st.session_state.dashboard_detail_company = None
                st.rerun()
            if cancel_col.button("Cancel", key="dashboard_cancel_delete"):
                st.session_state.pop("dashboard_pending_delete_ids", None)
                st.rerun()

        if save_clicked:
            changed_count = 0
            c = db()
            for row in edited_records:
                company_id = int(row["Company ID"])
                old = originals.get(company_id)
                if not old:
                    continue
                due_value = row.get("Next Action Date")
                due_text = due_value.isoformat() if hasattr(due_value, "isoformat") else (str(due_value or "")[:10])
                updates = (
                    row.get("Distribution") or "Unknown",
                    row.get("Stage") or "NEW",
                    row.get("Potential") or "Review",
                    canonical_next_action(row.get("Next Action"), row.get("Stage") or "NEW"),
                    due_text,
                )
                previous = (
                    old["distribution_type"] or "Unknown", old["stage"] or "NEW",
                    old["potential"] or "Review", canonical_next_action(old["next_action"], old["stage"] or "NEW"),
                    (old["next_action_date"] or "")[:10],
                )
                if updates != previous:
                    c.execute(
                        "UPDATE companies SET distribution_type=?, stage=?, potential=?, next_action=?, "
                        "next_action_date=?, last_modified=? WHERE company_id=?",
                        (*updates, datetime.now().isoformat(timespec="seconds"), company_id)
                    )
                    changed_count += 1
            c.commit()
            c.close()
            st.success(f"Saved changes for {changed_count} company(ies).")
            st.rerun()

        detail_id = st.session_state.get("dashboard_detail_company")
        if detail_id in originals:
            detail = rows("SELECT * FROM companies WHERE company_id=?", (detail_id,))[0]
            def detail_field(label, value, link_scheme=""):
                value = str(value or "—")
                safe_value = html_escape(value)
                if link_scheme and value != "—":
                    href = value if link_scheme == "url" else link_scheme + value
                    safe_href = html_escape(href, quote=True)
                    safe_value = f'<a href="{safe_href}" target="_blank">{safe_value}</a>'
                st.markdown(
                    f'<div style="margin:0 0 0.8rem 0"><div style="font-size:0.78rem;color:#667085;margin-bottom:0.15rem">{html_escape(label)}</div>'
                    f'<div style="font-weight:500;overflow-wrap:anywhere">{safe_value}</div></div>',
                    unsafe_allow_html=True,
                )

            st.divider()
            st.markdown("### 🗂️ Company Detail")
            st.markdown(f"#### {html_escape(detail['company_name'] or 'Unnamed')}", unsafe_allow_html=True)
            location_label = html_escape(" · ".join(x for x in [detail["country"] or "Country unknown", detail["continent"] or "Continent unknown"] if x))
            st.markdown(f'<div style="color:#667085;margin-top:-0.5rem;margin-bottom:1rem">{location_label}</div>', unsafe_allow_html=True)

            saved_notice = st.session_state.pop("company_detail_saved_notice", None)
            if saved_notice:
                st.success(saved_notice)

            with st.container(border=True):
                st.markdown("#### ✏️ Company & Contact Information")
                continent_choices = [""] + CONTINENTS
                current_continent = detail["continent"] or ""
                if current_continent not in continent_choices:
                    continent_choices.append(current_continent)
                business_choices = [""] + BUSINESS_TYPES
                current_business_type = detail["business_type"] or ""
                if current_business_type not in business_choices:
                    business_choices.append(current_business_type)
                inquiry_choices = [""] + INQUIRY_TYPES
                current_inquiry_type = detail["inquiry_type"] or ""
                if current_inquiry_type not in inquiry_choices:
                    inquiry_choices.append(current_inquiry_type)

                with st.form(key=f"company_detail_form_{detail_id}"):
                    detail_c1, detail_c2, detail_c3, detail_c4 = st.columns(4, gap="small")
                    edited_company_name = detail_c1.text_input("Company Name", detail["company_name"] or "", key=f"detail_{detail_id}_company")
                    edited_contact_name = detail_c2.text_input("Contact Name", detail["contact_name"] or "", key=f"detail_{detail_id}_contact_name")
                    edited_continent = detail_c3.selectbox("Continent", continent_choices, index=continent_choices.index(current_continent), key=f"detail_{detail_id}_continent")
                    edited_country = detail_c4.text_input("Country", detail["country"] or "", key=f"detail_{detail_id}_country")

                    detail_c5, detail_c6, detail_c7, detail_c8 = st.columns(4, gap="small")
                    edited_position = detail_c5.text_input("Position", detail["position"] or "", key=f"detail_{detail_id}_position")
                    edited_email = detail_c6.text_input("Email", detail["email"] or "", key=f"detail_{detail_id}_email")
                    edited_phone = detail_c7.text_input("Phone", detail["phone"] or "", key=f"detail_{detail_id}_phone")
                    edited_website = detail_c8.text_input("Website", detail["website"] or "", key=f"detail_{detail_id}_website")

                    detail_c9, detail_c10, detail_c11, detail_c12 = st.columns(4, gap="small")
                    edited_legal_entity = detail_c9.text_input("Legal Entity", detail["legal_entity"] or "", key=f"detail_{detail_id}_legal_entity")
                    edited_business_type = detail_c10.selectbox("Business Type", business_choices, index=business_choices.index(current_business_type), key=f"detail_{detail_id}_business_type")
                    edited_business_area = detail_c11.text_input("Business Area", detail["business_area"] or "", key=f"detail_{detail_id}_business_area")
                    edited_inquiry_type = detail_c12.selectbox("Inquiry Type", inquiry_choices, index=inquiry_choices.index(current_inquiry_type), key=f"detail_{detail_id}_inquiry_type")

                    st.markdown("##### 📝 Inquiry Notes")
                    edited_summary = st.text_area("Inquiry Summary", detail["inquiry_summary"] or "", height=110, key=f"detail_{detail_id}_summary")
                    edited_remarks = st.text_area("Remarks", detail["remarks"] or "", height=90, key=f"detail_{detail_id}_remarks")
                    save_detail_clicked = st.form_submit_button("Save Company Details", type="primary")

                if save_detail_clicked:
                    clean_name = clean_value(edited_company_name)
                    clean_email = clean_value(edited_email)
                    if not clean_name:
                        st.error("Company Name cannot be empty.")
                    elif clean_email and not EMAIL_RE.fullmatch(clean_email):
                        st.error("Enter a valid email address, or leave the field blank.")
                    else:
                        c = db()
                        c.execute(
                            "UPDATE companies SET company_name=?, continent=?, country=?, legal_entity=?, website=?, "
                            "business_type=?, business_area=?, inquiry_type=?, contact_name=?, position=?, email=?, phone=?, "
                            "inquiry_summary=?, remarks=?, last_modified=? WHERE company_id=?",
                            (
                                clean_name, edited_continent, clean_value(edited_country), clean_value(edited_legal_entity),
                                clean_value(edited_website), edited_business_type, clean_value(edited_business_area),
                                edited_inquiry_type, clean_value(edited_contact_name), clean_value(edited_position),
                                clean_email, clean_value(edited_phone), clean_value(edited_summary), clean_value(edited_remarks),
                                datetime.now().isoformat(timespec="seconds"), detail_id,
                            ),
                        )
                        c.commit()
                        c.close()
                        st.session_state.company_detail_saved_notice = "Company details saved."
                        st.rerun()

            info1, info2 = st.columns([1, 1.2])
            with info1:
                with st.container(border=True):
                    st.markdown("##### 📌 Management")
                    management_rows = [
                        ("Distribution", detail["distribution_type"] or "Unknown"),
                        ("Stage", detail["stage"] or "NEW"),
                        ("Potential", detail["potential"] or "Review"),
                        ("Next Action", detail["next_action"] or "—"),
                        ("Next Action Date", detail["next_action_date"] or "—"),
                        ("Inquiry Date", detail["inquiry_date"] or "—"),
                        ("Last Modified", format_last_modified(detail["last_modified"])),
                    ]
                    management_table = "".join(
                        f"<tr><th>{html_escape(label)}</th><td>{html_escape(value)}</td></tr>"
                        for label, value in management_rows
                    )
                    st.markdown(
                        '<table style="width:100%;border-collapse:collapse;font-size:.9rem">'
                        '<tbody>' + management_table + '</tbody></table>'
                        '<style>table td,table th{padding:.48rem .55rem;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}'
                        'table th{width:38%;color:#475467;background:#f8fafc;font-weight:600}table td{color:#101828;overflow-wrap:anywhere}</style>',
                        unsafe_allow_html=True,
                    )
            with info2:
                with st.container(border=True):
                    st.markdown("##### 📝 Inquiry Summary / Remarks")
                    note_rows = [
                        ("Inquiry Summary", detail["inquiry_summary"] or "—"),
                        ("Remarks", detail["remarks"] or "—"),
                    ]
                    notes_table = "".join(
                        f'<tr><th>{html_escape(label)}</th><td style="white-space:pre-wrap;line-height:1.55">{html_escape(value)}</td></tr>'
                        for label, value in note_rows
                    )
                    st.markdown(
                        '<table style="width:100%;border-collapse:collapse;font-size:.9rem">'
                        '<tbody>' + notes_table + '</tbody></table>'
                        '<style>table td,table th{padding:.62rem .7rem;border-bottom:1px solid #eaecf0;text-align:left;vertical-align:top}'
                        'table th{width:24%;color:#475467;background:#f8fafc;font-weight:600}table td{color:#101828;overflow-wrap:anywhere;min-height:2.5rem}</style>',
                        unsafe_allow_html=True,
                    )
            with st.expander("Original Email"):
                st.text(detail["original_email"] or "No original email saved.")
            activity_rows = rows(
                "SELECT activity_date, activity_type, subject, summary, next_action, next_action_date "
                "FROM activities WHERE company_id=? ORDER BY activity_id DESC", (detail_id,)
            )
            st.markdown("#### Activity History")
            if activity_rows:
                activity_data = [{
                    "Date": activity["activity_date"],
                    "Activity": activity["activity_type"],
                    "Subject": activity["subject"],
                    "Summary": activity["summary"],
                    "Next Action": activity["next_action"],
                    "Next Action Date": activity["next_action_date"],
                } for activity in activity_rows]
                st.dataframe(
                    activity_data, use_container_width=True, hide_index=True, height=260,
                    column_config={
                        "Date": st.column_config.TextColumn("Date", width="small"),
                        "Activity": st.column_config.TextColumn("Activity", width="small"),
                        "Subject": st.column_config.TextColumn("Subject", width="medium"),
                        "Summary": st.column_config.TextColumn("Summary", width="large"),
                        "Next Action": st.column_config.TextColumn("Next Action", width="medium"),
                        "Next Action Date": st.column_config.TextColumn("Next Action Date", width="small"),
                    },
                )
            else:
                st.caption("No activity has been recorded yet.")
    else:
        st.info("No companies to manage yet.")


# ============================================================
# INQUIRY INPUT
# ============================================================

elif page == "Inquiry Input":

    st.subheader("📥 새 문의 등록")
    st.button("내용 재작성", key="reset_inquiry_content", on_click=reset_inquiry_form)

    st.caption("이메일 분석 → 필요한 경우 업체 정보 조사 → 추출값 확인 및 등록")

    email_text = st.text_area(
        "이메일 원문",
        height=240,
        key="v5_email_original",
        placeholder="Paste the complete email here..."
    )

    if st.button("🔎 이메일 분석", type="primary", use_container_width=True):
        if not email_text.strip():
            st.warning("메일 원문을 입력하세요.")
        else:
            extracted = analyze_email(email_text)
            extracted.update({
                "potential": "Review",
                "stage": "NEW",
                "owner": "",
                "next_action": "QUALIFICATION",
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
        with st.expander("자동 추출 정보 확인", expanded=False):
            if extracted.get("company_name"):
                st.success(f"Company detected: **{extracted['company_name']}**")
            else:
                st.warning("회사명을 자동으로 확인하지 못했습니다.")

            # Keep the primary identity fields together across the page width.
            ec1, ec2, ec3, ec4 = st.columns(4, gap="small")
            ec1.text_input("Company", extracted.get("company_name", ""), disabled=True)
            ec2.text_input("Contact", extracted.get("contact_name", ""), disabled=True)
            ec3.text_input("Continent", extracted.get("continent", ""), disabled=True)
            ec4.text_input("Country", extracted.get("country", ""), disabled=True)

            # Related contact and inquiry attributes are also arranged in full-width rows.
            ec5, ec6, ec7, ec8 = st.columns(4, gap="small")
            ec5.text_input("Email", extracted.get("email", ""), disabled=True)
            ec6.text_input("Phone", extracted.get("phone", ""), disabled=True)
            ec7.text_input("Website", extracted.get("website", ""), disabled=True)
            ec8.text_input("Inquiry Date", extracted.get("inquiry_date", ""), disabled=True)
            ec8.caption(f"Date source: {extracted.get('inquiry_date_source', '—')}")

            ec9, ec10, ec11, ec12 = st.columns(4, gap="small")
            ec9.text_input("Position", extracted.get("position", ""), disabled=True)
            ec10.text_input("Inquiry Type", extracted.get("inquiry_type", ""), disabled=True)
            ec11.text_input("Business Type", extracted.get("business_type", ""), disabled=True)
            ec12.text_input("Business Area", extracted.get("business_area", ""), disabled=True)

            st.text_area("Inquiry Summary", extracted.get("inquiry_summary", ""), height=75, disabled=True)

        with st.expander("선택 조사 · 업체 정보 보완", expanded=False):
            st.caption("조사 결과가 없으면 비워 두세요. 입력한 조사값은 이메일 추출값보다 우선합니다.")
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
            st.markdown("**Paste complete research results**")
            research_paste = st.text_area(
                "Research Paste",
                key="v5_research_paste",
                height=150,
                placeholder="Company: Example Co.\nWebsite: https://example.com\nCountry: Korea\nBusiness: Cosmetics distributor\nBusiness Type: Distributor\nDistribution: Direct\nEmail: info@example.com\nLegal Entity: Example Corporation"
            )
            if st.button("Extract Research Fields", key="v5_extract_research"):
                parsed = extract_research_fields(research_paste)
                if parsed:
                    research.update(parsed)
                    st.session_state.v5_research = research
                    widget_keys = {
                        "company_name": "v5_r_company", "contact_name": "v5_r_contact",
                        "position": "v5_r_position", "email": "v5_r_email",
                        "phone": "v5_r_phone", "website": "v5_r_website",
                        "country": "v5_r_country", "business_type": "v5_r_business",
                        "business_area": "v5_r_area", "legal_entity": "v5_r_legal_entity",
                        "distribution_type": "v5_r_distribution"
                    }
                    for field, widget_key in widget_keys.items():
                        if field in parsed:
                            st.session_state[widget_key] = parsed[field]
                    st.success(f"Extracted {len(parsed)} research fields. Review them below.")
                    st.rerun()
                else:
                    st.warning("No labeled fields were detected. Use labels such as Company:, Country:, or Website:.")

            research = dict(st.session_state.get("v5_research", {}))
            empty_continents = [""] + CONTINENTS
            empty_inquiry_types = [""] + INQUIRY_TYPES
            rc1, rc2, rc3, rc4 = st.columns(4, gap="small")
            research["company_name"] = rc1.text_input("Research — Company", research.get("company_name", extracted.get("company_name", "Unknown")), key="v5_r_company")
            research["contact_name"] = rc2.text_input("Research — Contact", research.get("contact_name", ""), key="v5_r_contact")
            research["continent"] = rc3.selectbox("Research — Continent", empty_continents, index=empty_continents.index(research.get("continent", "")) if research.get("continent", "") in empty_continents else 0, key="v5_r_continent")
            research["country"] = rc4.text_input("Research — Country", research.get("country", ""), key="v5_r_country")

            rc5, rc6, rc7, rc8 = st.columns(4, gap="small")
            research["position"] = rc5.text_input("Research — Position", research.get("position", ""), key="v5_r_position")
            research["email"] = rc6.text_input("Research — Email", research.get("email", ""), key="v5_r_email")
            research["phone"] = rc7.text_input("Research — Phone", research.get("phone", ""), key="v5_r_phone")
            research["website"] = rc8.text_input("Research — Website", research.get("website", ""), key="v5_r_website")

            rc9, rc10, rc11, rc12 = st.columns(4, gap="small")
            research["inquiry_type"] = rc9.selectbox("Research — Inquiry Type", empty_inquiry_types, index=empty_inquiry_types.index(research.get("inquiry_type", "")) if research.get("inquiry_type", "") in empty_inquiry_types else 0, key="v5_r_inquiry")
            research["business_type"] = rc10.selectbox("Research — Business Type", [""] + BUSINESS_TYPES, index=([""] + BUSINESS_TYPES).index(research.get("business_type", "")) if research.get("business_type", "") in [""] + BUSINESS_TYPES else 0, key="v5_r_business")
            research["business_area"] = rc11.text_input("Research — Business Area", research.get("business_area", ""), key="v5_r_area")
            research["distribution_type"] = rc12.selectbox("Research — Distribution", DISTRIBUTION_TYPES, index=DISTRIBUTION_TYPES.index(research.get("distribution_type", "Unknown")) if research.get("distribution_type", "Unknown") in DISTRIBUTION_TYPES else 0, key="v5_r_distribution")

            rc13, rc14, _, _ = st.columns(4, gap="small")
            research["legal_entity"] = rc13.text_input("Research — Legal Entity", research.get("legal_entity", ""), key="v5_r_legal_entity")
            research["research_status"] = rc14.selectbox("Research Status", ["Not Started", "Partially Verified", "Verified", "Not Found"], index=["Not Started", "Partially Verified", "Verified", "Not Found"].index(research.get("research_status", "Not Started")), key="v5_r_status")
            research["research_notes"] = st.text_area("Research Notes / Investigation Result", research.get("research_notes", ""), height=95, key="v5_r_notes")
            st.session_state.v5_research = research
            st.session_state.v5_researched = research.get("research_status") != "Not Started" or bool(research.get("research_notes"))

        st.divider()
        st.subheader("2. 등록 정보 제안")
        st.caption("이메일과 조사 결과를 비교해 등록할 값을 제안합니다. AI 키가 없으면 규칙 기반으로 처리합니다.")

        if st.button("이메일·조사 정보 반영", type="primary", use_container_width=True):
            mapping = rule_based_mapping(extracted, research)
            st.session_state.v5_mapping = mapping
            st.session_state.v5_mapping_status = "Rule-based comparison"
            st.session_state.v5_mapped = True
            # Initialize final candidates from mapping only after mapping is run.
            st.session_state.v5_final = {
                field: item.get("suggestion", "") for field, item in mapping.items()
            }
            st.rerun()

        if st.session_state.get("v5_mapped"):
            status = st.session_state.get("v5_mapping_status", "Rule-based mapping")
            st.caption("Research 값이 있으면 우선 적용하고, 비어 있으면 이메일 추출값을 사용합니다.")

            mapping = st.session_state.get("v5_mapping", {})
            table = []
            for field in MAPPING_FIELDS:
                item = mapping.get(field, {})
                table.append({
                    "Field": FIELD_LABELS[field],
                    "Email": item.get("email", ""),
                    "Research": item.get("research", ""),
                    "Suggestion": item.get("suggestion", ""),
                    "Source": item.get("source", "—"),
                    "Confidence": item.get("confidence", "Low"),
                    "Reason": item.get("reason", "")
                })
            with st.expander("Email / Research 비교 세부 정보", expanded=False):
                st.dataframe(table, use_container_width=True, hide_index=True)

            st.divider()
            st.subheader("3. 최종 확인")
            final = dict(st.session_state.get("v5_final", {}))
            fc1, fc2, fc3, fc4 = st.columns(4, gap="small")
            company_default = final.get("company_name") or extracted.get("company_name", "Unknown") or "Unknown"
            final["company_name"] = fc1.text_input("FINAL — Company", company_default, key="v5_final_company_name")
            final["contact_name"] = fc2.text_input("FINAL — Contact", final.get("contact_name", extracted.get("contact_name", "")), key="v5_final_contact_name")
            final["continent"] = fc3.selectbox("FINAL — Continent", CONTINENTS, index=CONTINENTS.index(final.get("continent", extracted.get("continent", ""))) if final.get("continent", extracted.get("continent", "")) in CONTINENTS else 0, key="v5_final_continent")
            final["country"] = fc4.text_input("FINAL — Country", final.get("country", extracted.get("country", "")), key="v5_final_country")

            fc5, fc6, fc7, fc8 = st.columns(4, gap="small")
            final["position"] = fc5.text_input("FINAL — Position", final.get("position", extracted.get("position", "")), key="v5_final_position")
            final["email"] = fc6.text_input("FINAL — Email", final.get("email", extracted.get("email", "")), key="v5_final_email")
            final["phone"] = fc7.text_input("FINAL — Phone", final.get("phone", extracted.get("phone", "")), key="v5_final_phone")
            final["website"] = fc8.text_input("FINAL — Website", final.get("website", extracted.get("website", "")), key="v5_final_website")

            fc9, fc10, fc11, fc12 = st.columns(4, gap="small")
            final["inquiry_type"] = fc9.selectbox("FINAL — Inquiry Type", INQUIRY_TYPES, index=INQUIRY_TYPES.index(final.get("inquiry_type", extracted.get("inquiry_type", ""))) if final.get("inquiry_type", extracted.get("inquiry_type", "")) in INQUIRY_TYPES else 0, key="v5_final_inquiry")
            final["business_type"] = fc10.selectbox("FINAL — Business Type", BUSINESS_TYPES, index=BUSINESS_TYPES.index(final.get("business_type", extracted.get("business_type", ""))) if final.get("business_type", extracted.get("business_type", "")) in BUSINESS_TYPES else 0, key="v5_final_business")
            final["business_area"] = fc11.text_input("FINAL — Business Area", final.get("business_area", extracted.get("business_area", "")), key="v5_final_area")
            final["legal_entity"] = fc12.text_input("FINAL — Legal Entity", final.get("legal_entity", extracted.get("legal_entity", "")), key="v5_final_legal_entity")

            fc13, fc14, fc15, fc16 = st.columns(4, gap="small")
            current_distribution = final.get("distribution_type", extracted.get("distribution_type", "Unknown"))
            final["distribution_type"] = fc13.selectbox("FINAL — Distribution", DISTRIBUTION_TYPES, index=DISTRIBUTION_TYPES.index(current_distribution) if current_distribution in DISTRIBUTION_TYPES else 0, key="v5_final_distribution")
            final["potential"] = fc14.selectbox("Potential", POTENTIALS, index=POTENTIALS.index(final.get("potential", extracted.get("potential", "Review"))) if final.get("potential", extracted.get("potential", "Review")) in POTENTIALS else 0, key="v5_final_potential")
            final["stage"] = fc15.selectbox("Stage", STAGES, index=STAGES.index(final.get("stage", extracted.get("stage", "NEW"))) if final.get("stage", extracted.get("stage", "NEW")) in STAGES else 0, key="v5_final_stage")
            final["owner"] = fc16.text_input("Owner", final.get("owner", extracted.get("owner", "")), key="v5_final_owner")
            current_next_action = canonical_next_action(
                final.get("next_action") or extracted.get("next_action") or "QUALIFICATION",
                final.get("stage") or extracted.get("stage") or "NEW"
            )
            if "v5_final_next_action" in st.session_state:
                st.session_state["v5_final_next_action"] = canonical_next_action(
                    st.session_state["v5_final_next_action"], final.get("stage") or extracted.get("stage") or "NEW"
                )
            fc17, fc18, fc19, fc20 = st.columns(4, gap="small")
            final["next_action"] = fc17.selectbox(
                "Next Action", STAGES,
                index=STAGES.index(current_next_action),
                key="v5_final_next_action"
            )
            final["next_action_date"] = fc18.date_input("Next Action Date", extracted.get("next_action_date", date.today()), key="v5_final_next_date")
            inquiry_date_value = final.get("inquiry_date") or extracted.get("inquiry_date") or date.today().isoformat()
            try:
                inquiry_date_default = date.fromisoformat(str(inquiry_date_value)[:10])
            except ValueError:
                inquiry_date_default = date.today()
            final["inquiry_date"] = fc19.date_input("Inquiry Date", inquiry_date_default, key="v5_final_inquiry_date")
            final["potential_reason"] = fc20.text_input("Potential Reason", final.get("potential_reason", extracted.get("potential_reason", "")), key="v5_final_potential_reason")
            final["remarks"] = st.text_area("Research / Remarks", final.get("remarks", research.get("research_notes", "") or extracted.get("remarks", "")), key="v5_final_remarks", height=95)
            st.session_state.v5_final = final

            st.divider()
            st.subheader("4. 등록")
            st.caption("아래 확인된 값으로 문의를 등록합니다.")

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
                            "distribution_type": final.get("distribution_type", "Unknown"),
                            "legal_entity": final.get("legal_entity", ""),
                            "inquiry_summary": extracted.get("inquiry_summary", ""),
                            "potential": final.get("potential", "Review"),
                            "potential_reason": final.get("potential_reason", ""),
                            "stage": final.get("stage", "NEW"),
                            "owner": final.get("owner", ""),
                            "inquiry_date": final.get("inquiry_date", date.today()).isoformat() if hasattr(final.get("inquiry_date", date.today()), "isoformat") else str(final.get("inquiry_date", date.today()))[:10],
                            "last_contact_date": date.today().isoformat(),
                            "next_action": final.get("next_action", ""),
                            "next_action_date": str(final.get("next_action_date", date.today())),
                            "status": "Active",
                            "remarks": final.get("remarks", ""),
                            "original_email": extracted.get("original_email", ""),
                            "created_at": datetime.now().isoformat(),
                            "last_modified": datetime.now().isoformat(timespec="seconds"),
                            "email_extraction_json": json.dumps(extracted, ensure_ascii=False, default=str),
                            "research_json": json.dumps(research, ensure_ascii=False, default=str),
                            "mapping_json": json.dumps(st.session_state.get("v5_mapping", {}), ensure_ascii=False, default=str),
                            "research_status": research.get("research_status", "Not Started"),
                            "research_date": date.today().isoformat() if st.session_state.get("v5_researched") else ""
                        }
                        keys = list(record.keys())
                        c = db()
                        insert_sql = f"INSERT INTO companies({','.join(keys)}) VALUES({','.join(['?']*len(keys))})"
                        insert_values = [record[k] for k in keys]
                        if SUPABASE_DB_URL:
                            company_id = c.execute(insert_sql + " RETURNING company_id", insert_values).fetchone()[0]
                        else:
                            company_id = c.execute(insert_sql, insert_values).lastrowid
                        c.commit()
                        c.close()
                        add_activity(company_id, "Inquiry", "Initial inquiry", extracted.get("inquiry_summary", ""), final.get("next_action", ""), str(final.get("next_action_date", date.today())))
                        st.success(f"Registered: {company_name} (ID {company_id})")
                        for key in list(st.session_state.keys()):
                            if key.startswith("v5_"):
                                st.session_state.pop(key, None)
                        st.rerun()

        else:
            st.info("위에서 이메일·조사 정보를 반영하면 등록 항목을 확인할 수 있습니다. 업체 조사는 선택 사항입니다.")


# ============================================================

# ============================================================

elif page == "Inquiry List":

    st.subheader(
        "Inquiry List"
    )
    deleted_notice = st.session_state.pop("inquiry_deleted_notice", None)
    if deleted_notice:
        st.success(f"Deleted inquiry: {deleted_notice}")

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

    display_rows = [
            {
                "ID": x["company_id"],
                "Company": x["company_name"],
                "Country": x["country"],
                "Contact": x["contact_name"],
                "Business": x["business_type"],
                "Distribution": x["distribution_type"],
                "Potential": x["potential"],
                "Stage": x["stage"],
                "Last Modified": format_last_modified(x["last_modified"]),
                "Unmanaged Days": unmanaged_days(x["last_modified"]),
                "Next Action": x["next_action"],
                "Due": x["next_action_date"]
            }
            for x in data
        ]
    st.dataframe(
        display_rows,
        use_container_width=True,
        hide_index=True
    )

    if data:

        export_buffer = io.StringIO(newline="")
        if display_rows:
            writer = csv.DictWriter(export_buffer, fieldnames=list(display_rows[0].keys()))
            writer.writeheader()
            writer.writerows(display_rows)
        st.download_button(
            "Export filtered inquiries (CSV)",
            data=export_buffer.getvalue().encode("utf-8-sig"),
            file_name=f"inquiries_{date.today().isoformat()}.csv",
            mime="text/csv",
            key="inquiry_list_export_csv"
        )

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

        open_col, delete_col = st.columns([1, 1])
        if open_col.button("Open Detail", key="inquiry_list_open_detail"):
            st.session_state.selected_company = (
                selected_id
            )

            st.session_state.page = (
                "Company Detail"
            )

            st.rerun()

        if delete_col.button("Delete Inquiry", key="inquiry_list_delete"):
            st.session_state.inquiry_delete_pending = selected_id
            st.rerun()

        pending_delete_id = st.session_state.get("inquiry_delete_pending")
        if pending_delete_id in company_ids:
            pending_record = next(x for x in data if x["company_id"] == pending_delete_id)
            pending_name = pending_record["company_name"] or f"Inquiry {pending_delete_id}"
            st.warning(f"Delete **{pending_name}** and its activity history? This cannot be undone.")
            confirm_col, cancel_col = st.columns(2)
            if confirm_col.button("Confirm Delete", type="primary", key="inquiry_list_confirm_delete"):
                c = db()
                c.execute("DELETE FROM activities WHERE company_id=?", (pending_delete_id,))
                c.execute("DELETE FROM companies WHERE company_id=?", (pending_delete_id,))
                c.commit()
                c.close()
                st.session_state.inquiry_deleted_notice = pending_name
                st.session_state.pop("inquiry_delete_pending", None)
                if st.session_state.get("selected_company") == pending_delete_id:
                    st.session_state.selected_company = None
                st.rerun()
            if cancel_col.button("Cancel", key="inquiry_list_cancel_delete"):
                st.session_state.pop("inquiry_delete_pending", None)
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
        idle_days = unmanaged_days(company["last_modified"])
        st.caption(
            f"Distribution: {company['distribution_type'] or 'Unknown'} | "
            f"Last Modified: {format_last_modified(company['last_modified'])} | "
            f"Unmanaged: {f'{idle_days} days' if idle_days is not None else 'Unknown'}"
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

            st.markdown("**Legal Entity**")
            st.write(company["legal_entity"] or "-")

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
