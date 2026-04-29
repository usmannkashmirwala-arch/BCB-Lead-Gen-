#!/usr/bin/env python3
"""
Daily lead generation pipeline for Better Call Bot.
Generates 50 leads/day: 30 Pakistan (Google Maps), 10 MENA + 10 USA (Vibe Prospecting).
Appends to Google Sheets: DailyLeads and UsedContacts (dedup registry).

ICP context is loaded at startup from Lead Details/BCB ICP and Offer.pdf
so notes always reflect the latest ICP positioning.

Usage:
    python tools/generate_leads.py           # Full run (writes to Sheets)
    python tools/generate_leads.py --dry-run # Fetch leads, skip writing to Sheets
"""

import json
import os
import random
import re
import subprocess
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
]
USED_CONTACTS_HEADERS = ["Region", "Identifier", "DateAdded", "LeadSource"]

# ── Targets ───────────────────────────────────────────────────────────────────
PK_TARGET = 30
MENA_TARGET = 10
USA_TARGET = 10

# ── Pakistan query pool ───────────────────────────────────────────────────────
PK_QUERIES = [
    "clothing store Karachi", "apparel brand Karachi", "fashion boutique Karachi",
    "pret wear Karachi", "women clothing Karachi", "kurta brand Karachi",
    "lawn brand Karachi", "ethnic wear Karachi", "designer clothes Karachi",
    "readymade garments Karachi", "men clothing store Karachi",
    "garment store Lahore", "pret wear Lahore", "fashion retailer Lahore",
    "apparel brand Lahore", "lawn brand Lahore", "women clothing Lahore",
    "clothing brand Islamabad", "fashion boutique Islamabad", "apparel store Islamabad",
    "textile brand Faisalabad", "garment manufacturer Faisalabad", "clothing Faisalabad",
    "fashion store Rawalpindi", "clothing store Rawalpindi",
    "pret wear Sialkot", "garment Sialkot",
    "fashion boutique Multan", "clothing store Multan",
    "apparel store Gujranwala", "clothing store Gujranwala",
    "fashion store Peshawar",
    "boutique Hyderabad Pakistan",
    "apparel Bahawalpur",
    "fashion clothing Sargodha",
]

# ── Vibe Prospecting config ───────────────────────────────────────────────────
VIBE_JOB_LEVELS = ["c-suite", "founder", "owner", "director", "vice president"]
VIBE_DEPARTMENTS = ["c-suite", "operations", "customer success", "support"]
VIBE_LINKEDIN_CATEGORIES = ["Apparel & Fashion", "Retail", "Consumer Goods"]
MENA_COUNTRY_CODES = ["AE", "SA", "QA", "KW"]
USA_COUNTRY_CODES = ["US"]

# ── Google Maps config ────────────────────────────────────────────────────────
MAPS_BASE = "https://maps.googleapis.com/maps/api/place"


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
    used = {"Pakistan": set(), "MENA": set(), "USA": set()}
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
    elif first_row != headers:
        log(f"[WARN] {ws.title} headers differ from expected. Current: {first_row}")


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


def fetch_pk_leads(used_phones: set, target: int, icp_text: str) -> list:
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        log("[Maps] GOOGLE_MAPS_API_KEY not set — skipping Pakistan leads.")
        return []

    queries = random.sample(PK_QUERIES, min(5, len(PK_QUERIES)))
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
        lead = {
            "Company": place.get("name", ""),
            "Industry": "Apparel & Fashion",
            "EmployeeEstimate": "",
            "Country": "Pakistan",
            "SupportChannel": "",
            "ContactName": "",
            "ContactRole": "",
            "LinkedIn": "",
            "Email": "",
            "Phone": phone_raw,
            "LeadSource": "GoogleMaps",
            "FitGrade": "C",
            "IntentScore": 0,
            "DateAdded": str(date.today()),
            "Status": "New",
            "Note": "",
            "_identifier": phone_norm,
            "_region": "Pakistan",
        }
        lead["Note"] = generate_note(lead, icp_text)
        leads.append(lead)

    return leads


# ── MENA / USA: Vibe Prospecting (vpai CLI) ───────────────────────────────────

def _vpai(tool: str, args: dict) -> dict:
    """Call the vpai CLI via subprocess and return parsed JSON output."""
    vp_key = os.environ.get("VIBEPROSPECTING_API_KEY", os.environ.get("VP_API_KEY", ""))
    if not vp_key:
        log("[vpai] VIBEPROSPECTING_API_KEY not set")
        return {}

    reasoning = args.get("tool_reasoning", f"Lead generation via {tool}")
    env = {**os.environ, "VP_API_KEY": vp_key}

    cmd = [
        "npx", "--yes", "@vibeprospecting/vpai@latest", tool,
        "--args", json.dumps(args),
        "--tool-reasoning", reasoning,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)
    except subprocess.TimeoutExpired:
        log(f"[vpai] Timeout running {tool}")
        return {}

    # stderr has npm notices; only log actual errors
    if proc.stderr:
        err_lines = [l for l in proc.stderr.splitlines() if '"level":"error"' in l]
        if err_lines:
            log(f"[vpai] Errors: {err_lines[0][:200]}")

    if not proc.stdout.strip():
        log(f"[vpai] Empty output from {tool}")
        return {}

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        log(f"[vpai] JSON parse error ({tool}): {e} — raw: {proc.stdout[:200]}")
        return {}


def _parse_enrich_contacts(result: dict) -> dict:
    """
    Parse enrich-prospects response.
    Returns {prospect_id: {"email": str, "phone": str}}.
    """
    contacts_str = result.get("enrichment_results", {}).get("contacts", "")
    if not contacts_str:
        return {}
    try:
        contacts_data = json.loads(contacts_str)
    except (json.JSONDecodeError, TypeError):
        return {}

    out = {}
    for item in contacts_data.get("data", []):
        pid = item.get("prospect_id", "")
        data = item.get("data", {}) or {}

        # Prefer current professional email
        email = data.get("professions_email", "")
        if not email:
            for em in data.get("emails") or []:
                if em.get("type") == "current_professional":
                    email = em.get("address", "")
                    break
        if not email:
            emails = data.get("emails") or []
            email = emails[0].get("address", "") if emails else ""

        phone = data.get("mobile_phone", "") or ""
        if not phone:
            nums = data.get("phone_numbers") or []
            if nums:
                phone = nums[0] if isinstance(nums[0], str) else nums[0].get("raw_number", "")

        if pid:
            out[pid] = {"email": email or "", "phone": phone or ""}
    return out


def _clean_linkedin(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("http"):
        return url
    return f"https://www.{url}" if url.startswith("linkedin.com") else url


def _fit_grade(job_levels: list) -> str:
    decision_maker_levels = {"c-suite", "founder", "owner", "director", "vice president"}
    if any(level in decision_maker_levels for level in job_levels):
        return "A"
    return "B"


def fetch_vibe_leads(
    region: str,
    country_codes: list,
    used_emails: set,
    target: int,
    icp_text: str,
) -> list:
    vp_key = os.environ.get("VIBEPROSPECTING_API_KEY", os.environ.get("VP_API_KEY", ""))
    if not vp_key:
        log(f"[vpai] VIBEPROSPECTING_API_KEY not set — skipping {region} leads.")
        return []

    fetch_size = target * 3  # over-fetch to account for missing emails and dupes
    reasoning = (
        f"Find decision makers at apparel and fashion e-commerce companies "
        f"in {region} for AI customer support agent outreach"
    )

    # Step 1: fetch prospect list
    fetch_args = {
        "entity_type": "prospects",
        "filters": {
            "company_size": {"values": ["51-200"]},
            "linkedin_category": {"values": VIBE_LINKEDIN_CATEGORIES},
            "company_country_code": {"values": country_codes},
            "job_level": {"values": VIBE_JOB_LEVELS},
            "job_department": {"values": VIBE_DEPARTMENTS},
            "has_email": True,
        },
        "page_size": fetch_size,
        "tool_reasoning": reasoning,
    }

    log(f"[vpai] {region}: fetching up to {fetch_size} candidates from {country_codes}")
    fetch_result = _vpai("fetch-entities", fetch_args)
    prospects = fetch_result.get("data", [])
    log(f"[vpai] {region}: {len(prospects)} candidates returned")

    if not prospects:
        return []

    # Step 2: enrich to get actual emails (batch up to 100 per call)
    prospect_ids = [p["prospect_id"] for p in prospects if p.get("prospect_id")]
    enrich_args = {
        "prospect_ids": prospect_ids[:100],
        "enrichments": ["contacts"],
        "tool_reasoning": reasoning,
    }
    log(f"[vpai] {region}: enriching {len(prospect_ids[:100])} prospects for contact details")
    enrich_result = _vpai("enrich-prospects", enrich_args)
    # Detect plain-text error messages returned inside the contacts field
    contacts_raw = enrich_result.get("enrichment_results", {}).get("contacts", "")
    if isinstance(contacts_raw, str) and contacts_raw and not contacts_raw.strip().startswith("{"):
        log(f"[vpai] {region}: enrich returned an error — {contacts_raw[:300]}")
    contact_map = _parse_enrich_contacts(enrich_result)
    log(f"[vpai] {region}: got contact data for {len(contact_map)} prospects")

    # Step 3: build leads, filtering dupes
    id_to_prospect = {p["prospect_id"]: p for p in prospects}
    leads = []

    for pid, contacts in contact_map.items():
        if len(leads) >= target:
            break

        email = contacts.get("email", "").lower().strip()
        if not email or email in used_emails:
            continue

        person = id_to_prospect.get(pid, {})

        # Pick the cleanest LinkedIn URL (vanity slug preferred over obfuscated ID)
        li_urls = person.get("linkedin_url_array") or []
        linkedin = _clean_linkedin(li_urls[-1] if li_urls else person.get("linkedin", ""))

        job_levels = person.get("job_level_array") or []
        used_emails.add(email)

        lead = {
            "Company": person.get("company_name", ""),
            "Industry": "Apparel & Fashion",
            "EmployeeEstimate": "51-200",
            "Country": person.get("country_name", "").title(),
            "SupportChannel": "",
            "ContactName": person.get("full_name", ""),
            "ContactRole": person.get("job_title", "").title(),
            "LinkedIn": linkedin,
            "Email": email,
            "Phone": contacts.get("phone", ""),
            "LeadSource": "VibeProspecting",
            "FitGrade": _fit_grade(job_levels),
            "IntentScore": 0,
            "DateAdded": str(date.today()),
            "Status": "New",
            "Note": "",
            "_identifier": email,
            "_region": region,
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
    log(
        f"UsedContacts loaded: {len(used['Pakistan'])} PK | "
        f"{len(used['MENA'])} MENA | {len(used['USA'])} USA"
    )

    # Fetch leads from each source
    pk_leads = fetch_pk_leads(used["Pakistan"], PK_TARGET, icp_text)
    log(f"Pakistan: {len(pk_leads)}/{PK_TARGET}")

    mena_leads = fetch_vibe_leads("MENA", MENA_COUNTRY_CODES, used["MENA"], MENA_TARGET, icp_text)
    log(f"MENA: {len(mena_leads)}/{MENA_TARGET}")

    usa_leads = fetch_vibe_leads("USA", USA_COUNTRY_CODES, used["USA"], USA_TARGET, icp_text)
    log(f"USA: {len(usa_leads)}/{USA_TARGET}")

    all_leads = pk_leads + mena_leads + usa_leads
    log(f"Total: {len(all_leads)}/50")

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
