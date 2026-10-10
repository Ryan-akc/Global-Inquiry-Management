from pathlib import Path

app = Path(r"C:\Global_Inquiry_Manager\app.py")

text = app.read_text(encoding="utf-8")

start = text.find("def extract_company(")
end = text.find("# ============================================================\n# PHONE", start)

if start == -1:
    raise SystemExit("ERROR: def extract_company() not found")

if end == -1:
    raise SystemExit("ERROR: PHONE section not found")

new_function = r'''def extract_company(text, from_name=""):
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
    # 3. Role + "at COMPANY"
    #
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

    m = re.search(
        r"\b"
        + role_words
        + r"\s+at\s+"
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


'''

new_text = text[:start] + new_function + text[end:]

app.write_text(new_text, encoding="utf-8")

print("OK: extract_company() replaced successfully.")