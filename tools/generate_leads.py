#!/usr/bin/env python3
"""
Daily lead generation pipeline for Better Call Bot.
Generates 30 Pakistan leads/day via Google Maps.
Enriches each lead with website URL and scraped business email.
Appends to Google Sheets: DailyLeads and UsedContacts (dedup registry).

Usage:
    python tools/generate_leads.py           # Full run (writes to Sheets)
    python tools/generate_leads.py --dry-run # Fetch leads, skip writing to Sheets
"""

import json
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

import gspread
import pdfplumber
import requests
from dotenv import load_dotenv

load_dotenv()

# ── Paths ─────────────────────────────────────────────────────────────────────
_SCRIPT_DIR = Path(__file__).parent
PDF_PATH = _SCRIPT_DIR / ".." / "Lead Details " / "BCB ICP and Offer.pdf"

# ── Sheet config ──────────────────────────────────────────────────────────────
DAILY_LEADS_SHEET = "DailyLeads"
USED_CONTACTS_SHEET = "UsedContacts"

DAILY_LEADS_HEADERS = [
    "Company", "Industry", "EmployeeEstimate", "Country", "SupportChannel",
    "ContactName", "ContactRole", "LinkedIn", "Email", "Phone",
    "LeadSource", "FitGrade", "IntentScore", "DateAdded", "Status", "Note",
    "Website",
]
USED_CONTACTS_HEADERS = ["Region", "Identifier", "DateAdded", "LeadSource"]

# ── Targets ───────────────────────────────────────────────────────────────────
PK_TARGET = 30

# ── Pakistan query pool ───────────────────────────────────────────────────────
PK_QUERIES = [
    # Apparel & Fashion
    "clothing store Karachi", "apparel brand Karachi", "fashion boutique Karachi",
    "pret wear Karachi", "women clothing Karachi", "kurta brand Karachi",
    "lawn brand Karachi", "ethnic wear Karachi", "designer clothes Karachi",
    "readymade garments Karachi", "men clothing store Karachi",
    "garment store Lahore", "pret wear Lahore", "fashion retailer Lahore",
    "apparel brand Lahore", "lawn brand Lahore", "women clothing Lahore",
    "clothing brand Islamabad", "fashion boutique Islamabad", "apparel store Islamabad",
    "textile brand Faisalabad", "garment manufacturer Faisalabad",
    "fashion store Rawalpindi", "pret wear Sialkot",
    "fashion boutique Multan", "apparel store Gujranwala", "fashion store Peshawar",
    "boutique Hyderabad Pakistan",
    # Footwear
    "shoe brand Karachi", "footwear brand Karachi", "sneaker store Karachi",
    "shoes brand Lahore", "footwear store Lahore", "sandal brand Pakistan",
    "shoe store Islamabad", "footwear brand Faisalabad",
    # Supplements & Health
    "supplement brand Pakistan", "protein supplement Karachi",
    "health supplement Lahore", "vitamin supplement Pakistan",
    "nutrition brand Pakistan", "sports nutrition Karachi",
    "supplement store Islamabad",
    # Herbal & Organic
    "herbal products Pakistan", "herbal brand Karachi",
    "organic products Pakistan", "natural products Lahore",
    "herbal tea brand Pakistan", "organic skincare Pakistan",
    # Beauty & Personal Care
    "cosmetics brand Pakistan", "skincare brand Karachi",
    "beauty products Pakistan", "hair care brand Pakistan",
    "makeup brand Lahore",
]

# ── Google Maps config ────────────────────────────────────────────────────────
MAPS_BASE = "https://maps.googleapis.com/maps/api/place"

# Platform/SaaS domains that show up in website HTML but aren't business emails
_SKIP_EMAIL_DOMAINS = {
    "sentry.io", "example.com", "wordpress.com", "wix.com", "wixpress.com",
    "shopify.com", "squarespace.com", "mailchimp.com", "cloudflare.com",
    "googletagmanager.com", "google.com", "facebook.com", "instagram.com",
    "w3.org", "schema.org", "jquery.com",
}

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')
_MAILTO_RE = re.compile(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})')


def _extract_email_from_website(url: str) -> str:
    """Scrape a business email from the website. Tries homepage then /contact."""
    if not url:
        return ""
    base = url.rstrip("/")
    pages = [base, f"{base}/contact", f"{base}/contact-us"]
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LeadBot/1.0)"}

    for page_url in pages:
        try:
            resp = requests.get(page_url, headers=headers, timeout=6, allow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text
            # Prefer explicit mailto: links
            for em in _MAILTO_RE.findall(html):
                domain = em.split("@")[1].lower()
                if domain not in _SKIP_EMAIL_DOMAINS and not em.lower().startswith("noreply"):
                    return em.lower()
            # Fall back to any email found in page text
            for em in _EMAIL_RE.findall(html):
                domain = em.split("@")[1].lower()
                if domain not in _SKIP_EMAIL_DOMAINS and not em.lower().startswith("noreply"):
                    return em.lower()
        except Exception:
            pass
    return ""


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(msg, flush=True)


# ── ICP context: read PDF at startup ─────────────────────────────────────────

def load_icp_context() -> str:
    """Read the ICP PDF and return its full text for note generation context."""
    path = PDF_PATH.resolve()
    try:
        with pdfplumber.open(str(path)) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        log(f"[ICP] Loaded ICP PDF ({len(text)} chars) from {path.name}")
        return text
    except Exception as e:
        log(f"[ICP] Could not read PDF at {path}: {e} — notes will use default templates.")
        return ""


# ── Note generation ───────────────────────────────────────────────────────────
# Angles drawn directly from ICP v1.1 persona sketches in the PDF:
# Visionary Founder: "Let your support team handle the humans, while AI handles
#   the 'Where's my order?' and size guide questions instantly — in any language."
# Progressive Support Lead: "Give your team superpowers. AI drafts the answer;
#   they approve and focus on VIPs. You look like a strategic leader."

_FOUNDER_NOTES = [
    ("{hi} As {company} scales in {country}, your support team is probably drowning "
     "in 'Where's my order?' and size guide queries every peak season. Our AI handles "
     "those 24/7 in any language — your team focuses on the humans. Worth a 15-min call?"),
    ("{hi} Growing an apparel brand means repetitive support queries eat into margin fast. "
     "We help founders like you automate 60-70% of those tickets so the team focuses on "
     "VIP customers instead. Can I send you a quick case study from a brand your size?"),
    ("{hi} Quick thought for {company}: instead of adding more support reps each season, "
     "our AI handles 'order status', sizing, and returns 24/7 in any language. "
     "No new hires, instant replies. Would a short demo make sense?"),
]

_SUPPORT_NOTES = [
    ("{hi} Support leads at apparel brands tell me peak-season spikes are brutal — "
     "same queries, nonstop. Our AI drafts instant responses your team approves in one click, "
     "cutting repetitive tickets by 70%. Interested in a 15-min walkthrough?"),
    ("{hi} At {company} your team probably handles waves of order status and size queries daily. "
     "Our AI takes those off their plate 24/7 so they focus on complex cases and VIPs. "
     "You'd look like a strategic leader at the next company review — want to see how?"),
    ("{hi} Give your team superpowers: AI drafts answers to repetitive queries, they approve "
     "and focus on VIPs. 70% fewer repetitive tickets. Happy to walk you through a "
     "15-min demo if you're curious."),
]

_GENERIC_NOTES = [
    ("{hi} We help growing apparel brands like {company} in {country} automate repetitive "
     "customer support queries with AI — handling order status, sizing, returns, all 24/7. "
     "Would love to show you how if you're open to it!"),
    ("{hi} Quick note for {company}: our AI support agent handles the repetitive queries "
     "(60-70% of most apparel brands' ticket volume) 24/7 in any language, "
     "so your team can focus on real issues. Worth a brief chat?"),
]

_PK_NOTES = [
    ("Hi! We help apparel brands in Pakistan like {company} reduce repetitive customer "
     "support queries by 60-70% using AI. Brands your size typically handle hundreds of "
     "'order status' and sizing questions daily — our AI takes those 24/7. "
     "Happy to show you a quick 15-min demo!"),
    ("Reaching out because we work with Pakistani apparel brands to automate repetitive "
     "customer queries. AI handles 'Where's my order?' and sizing instantly — your team "
     "focuses on real issues. Would love to connect with the right person!"),
    ("Hi! Growing apparel brands in Pakistan get flooded with support queries during "
     "campaign launches. Our AI handles those 24/7 — no extra staff needed. "
     "Would {company} be open to a quick look?"),
]


def generate_note(lead: dict, icp_text: str) -> str:
    name = lead.get("ContactName", "")
    first_name = name.split()[0] if name else ""
    role = lead.get("ContactRole", "").lower()
    company = lead.get("Company", "your brand")
    country = lead.get("Country", "your market")
    region = lead.get("_region", "")

    hi = f"Hi {first_name}," if first_name else "Hi,"

    is_founder = any(w in role for w in ["founder", "ceo", "chief executive", "owner"])
    is_coo = any(w in role for w in ["coo", "chief operating", "co-founder"])
    is_support = any(w in role for w in [
        "support", "customer experience", "cx", "customer success",
        "head of customer", "service", "care",
    ])

    if region == "Pakistan" and not name:
        template = random.choice(_PK_NOTES)
    elif is_founder or is_coo:
        template = random.choice(_FOUNDER_NOTES)
    elif is_support:
        template = random.choice(_SUPPORT_NOTES)
    else:
        template = random.choice(_GENERIC_NOTES)

    return template.format(hi=hi, company=company, country=country)


# ── Google Sheets auth ────────────────────────────────────────────────────────

def _parse_svc_account_json(raw: str) -> dict:
    s = raw.strip()
    # Try direct parse first
    try:
        parsed = json.loads(s)
        if isinstance(parsed, str):  # double-encoded
            parsed = json.loads(parsed)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Strip surrounding single or double quotes added by some secret managers
    if len(s) >= 2 and s[0] in ('"', "'") and s[-1] in ('"', "'"):
        s = s[1:-1].strip()
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    # Last resort: extract the first {...} blob (handles extra chars like BOM)
    match = re.search(r'\{.*\}', s, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise SystemExit(
        "GOOGLE_SERVICE_ACCOUNT_JSON cannot be parsed as JSON. "
        "In GitHub → Settings → Secrets, paste only the raw JSON object "
        "with no surrounding quotes."
    )


def get_sheets_client():
    svc_json_str = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if svc_json_str:
        return gspread.service_account_from_dict(_parse_svc_account_json(svc_json_str))
    return gspread.service_account(filename="credentials.json")


def load_used_contacts(ws) -> dict:
    used = {"Pakistan": set()}
    for row in ws.get_all_records():
        region = row.get("Region", "")
        ident = str(row.get("Identifier", "")).lower().strip()
        if region in used and ident:
            used[region].add(ident)
    return used


def ensure_headers(ws, headers: list):
    first_row = ws.row_values(1)
    if not first_row:
        ws.append_row(headers, value_input_option="RAW")
    elif first_row == headers:
        return
    elif first_row == headers[:len(first_row)]:
        # Existing headers are a prefix — just add the new columns at the end
        extra = headers[len(first_row):]
        col = len(first_row) + 1
        ws.update([extra], range_name=f"R1C{col}:R1C{len(headers)}")
        log(f"[Sheets] {ws.title}: added new columns {extra}")
    else:
        log(f"[WARN] {ws.title} headers differ. Expected: {headers}, current: {first_row}")


def write_to_sheets(daily_ws, used_ws, leads: list):
    if not leads:
        log("No leads to write.")
        return
    today = str(date.today())
    daily_rows = [[lead.get(h, "") for h in DAILY_LEADS_HEADERS] for lead in leads]
    used_rows = [
        [lead["_region"], lead["_identifier"], today, lead["LeadSource"]]
        for lead in leads
    ]
    daily_ws.append_rows(daily_rows, value_input_option="RAW")
    log(f"Wrote {len(daily_rows)} rows to {DAILY_LEADS_SHEET}")
    used_ws.append_rows(used_rows, value_input_option="RAW")
    log(f"Wrote {len(used_rows)} rows to {USED_CONTACTS_SHEET}")


# ── Pakistan: Google Maps Places API ─────────────────────────────────────────

def _maps_text_search(query: str, api_key: str) -> list:
    results = []
    params = {"query": query, "key": api_key, "language": "en"}
    url = f"{MAPS_BASE}/textsearch/json"
    for _ in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
        except Exception as e:
            log(f"[Maps] Request error for '{query}': {e}")
            break
        status = data.get("status")
        if status == "ZERO_RESULTS":
            break
        if status != "OK":
            log(f"[Maps] Status '{status}' for '{query}'")
            break
        results.extend(data.get("results", []))
        token = data.get("next_page_token")
        if not token:
            break
        time.sleep(2)  # Maps requires ≥2s before next_page_token is valid
        params = {"pagetoken": token, "key": api_key}
    return results


def _maps_place_details(place_id: str, api_key: str) -> dict:
    try:
        resp = requests.get(
            f"{MAPS_BASE}/details/json",
            params={
                "place_id": place_id,
                "fields": "name,international_phone_number,formatted_phone_number,website",
                "key": api_key,
            },
            timeout=15,
        )
        return resp.json().get("result", {})
    except Exception as e:
        log(f"[Maps] Details error for {place_id}: {e}")
        return {}


def normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _infer_industry(name: str, place_types: list) -> str:
    n = name.lower()
    if any(w in n for w in ["shoe", "footwear", "sneaker", "sandal", "boot"]):
        return "Footwear"
    if any(w in n for w in ["supplement", "protein", "nutrition", "vitamin", "health", "nutra"]):
        return "Health & Supplements"
    if any(w in n for w in ["herbal", "organic", "natural", "hakeem", "unani", "ayur"]):
        return "Herbal & Organic"
    if any(w in n for w in ["cosmetic", "skincare", "beauty", "makeup", "hair", "skin"]):
        return "Beauty & Personal Care"
    return "Apparel & Fashion"


def fetch_pk_leads(used_phones: set, target: int, icp_text: str) -> list:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        log("[Maps] GOOGLE_MAPS_API_KEY not set — skipping Pakistan leads.")
        return []

    queries = random.sample(PK_QUERIES, min(8, len(PK_QUERIES)))
    log(f"[Maps] Today's queries: {queries}")

    candidates = []
    for q in queries:
        results = _maps_text_search(q, api_key)
        candidates.extend(results)
        log(f"[Maps] '{q}' → {len(results)} places")

    random.shuffle(candidates)
    seen_place_ids: set = set()
    leads = []

    for place in candidates:
        if len(leads) >= target:
            break
        place_id = place.get("place_id", "")
        if not place_id or place_id in seen_place_ids:
            continue
        seen_place_ids.add(place_id)

        detail = _maps_place_details(place_id, api_key)
        time.sleep(0.15)

        phone_raw = (
            detail.get("international_phone_number")
            or detail.get("formatted_phone_number", "")
        )
        if not phone_raw:
            continue
        phone_norm = normalize_phone(phone_raw)
        if not phone_norm or phone_norm in used_phones:
            continue

        used_phones.add(phone_norm)
        website = detail.get("website", "")
        email = _extract_email_from_website(website)
        # Derive industry from the query that surfaced this place
        industry = _infer_industry(place.get("name", ""), place.get("types", []))
        lead = {
            "Company": place.get("name", ""),
            "Industry": industry,
            "EmployeeEstimate": "",
            "Country": "Pakistan",
            "SupportChannel": "",
            "ContactName": "",
            "ContactRole": "",
            "LinkedIn": "",
            "Email": email,
            "Phone": phone_raw,
            "LeadSource": "GoogleMaps",
            "FitGrade": "C",
            "IntentScore": 0,
            "DateAdded": str(date.today()),
            "Status": "New",
            "Note": "",
            "Website": website,
            "_identifier": phone_norm,
            "_region": "Pakistan",
        }
        lead["Note"] = generate_note(lead, icp_text)
        leads.append(lead)

    return leads


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv
    today = str(date.today())
    log(f"{'[DRY RUN] ' if dry_run else ''}Lead gen run: {today}")

    # Load ICP context from PDF — informs note generation
    icp_text = load_icp_context()

    # Connect to Google Sheets
    gc = get_sheets_client()
    sheet_id = os.environ["GOOGLE_SHEET_ID"]
    wb = gc.open_by_key(sheet_id)

    existing_titles = {ws.title for ws in wb.worksheets()}
    if DAILY_LEADS_SHEET not in existing_titles:
        log(f"[Sheets] Creating tab '{DAILY_LEADS_SHEET}'")
        daily_ws = wb.add_worksheet(title=DAILY_LEADS_SHEET, rows=5000, cols=len(DAILY_LEADS_HEADERS))
    else:
        daily_ws = wb.worksheet(DAILY_LEADS_SHEET)

    if USED_CONTACTS_SHEET not in existing_titles:
        log(f"[Sheets] Creating tab '{USED_CONTACTS_SHEET}'")
        used_ws = wb.add_worksheet(title=USED_CONTACTS_SHEET, rows=50000, cols=len(USED_CONTACTS_HEADERS))
    else:
        used_ws = wb.worksheet(USED_CONTACTS_SHEET)

    ensure_headers(daily_ws, DAILY_LEADS_HEADERS)
    ensure_headers(used_ws, USED_CONTACTS_HEADERS)

    used = load_used_contacts(used_ws)
    log(f"UsedContacts loaded: {len(used['Pakistan'])} PK deduped")

    all_leads = fetch_pk_leads(used["Pakistan"], PK_TARGET, icp_text)
    log(f"Pakistan: {len(all_leads)}/{PK_TARGET}")

    log(f"Total: {len(all_leads)}/30")

    if dry_run:
        log("\n[DRY RUN] Sample output (first 3 leads):")
        for lead in all_leads[:3]:
            display = {k: v for k, v in lead.items() if not k.startswith("_")}
            log(json.dumps(display, indent=2, ensure_ascii=False))
        log("[DRY RUN] No writes made.")
        return

    write_to_sheets(daily_ws, used_ws, all_leads)
    log("Done.")


if __name__ == "__main__":
    main()
