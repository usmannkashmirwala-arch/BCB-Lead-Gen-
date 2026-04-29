# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Builds

A daily automated lead generation pipeline for Better Call Bot (AI automation consultancy). Generates 50 ICP-aligned leads/day and appends them to a Google Sheet (`DailyLeads`) with a personalised outreach `Note` per lead. Runs via GitHub Actions at 1:00 AM UTC (6:00 AM PKT) daily.

The main deliverable is `tools/generate_leads.py`.

## ICP — Always Read First

**Before making any changes to note generation, lead filters, or scoring, read `Lead Details /BCB ICP and Offer.pdf`.** The script reads this PDF at startup (`load_icp_context()`) so notes always reflect the latest ICP. The PDF defines:
- Target personas: Visionary Founder and Progressive Support Lead
- Fit grades A–F with exact criteria
- Technographic signals and disqualification rules
- Messaging angles for each persona

## WAT Framework

This project uses the WAT (Workflows, Agents, Tools) pattern — see `GLOBAL CLAUDE.md` for architecture details. The full workflow SOP is at `workflows/lead_gen_sop.md`.

## Environment & Auth

Required env vars (local: `.env`; CI: GitHub Actions secrets):

| Var | Purpose |
|-----|---------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account key JSON for Sheets |
| `GOOGLE_SHEET_ID` | Target workbook ID |
| `VIBEPROSPECTING_API_KEY` | Vibe Prospecting (Explorium) tenant key — used as `VP_API_KEY` by the vpai CLI |
| `GOOGLE_MAPS_API_KEY` | Maps Platform key with Places API enabled |

`credentials.json` in the project root is for local Google OAuth only (gitignored).

## Running the Pipeline

```bash
# Install Python dependencies
pip install -r requirements.txt

# Install vpai CLI (Node.js required)
npm install -g @vibeprospecting/vpai@latest

# Dry run — calls APIs, prints sample, does NOT write to Sheets
python tools/generate_leads.py --dry-run

# Full run — writes to DailyLeads and UsedContacts
python tools/generate_leads.py
```

**Confirm with the user before re-running a full run on the same day** — vpai enrichment costs ~2 credits per prospect.

## Lead Split & Sources

| Region | Count | Source | Tool | Dedup Key |
|--------|-------|--------|------|-----------|
| Pakistan | 30 | Google Maps Places API (Text Search + Place Details) | `requests` | Phone (normalized, digits only) |
| MENA | 10 | Vibe Prospecting (`vpai fetch-entities` + `enrich-prospects`) | `subprocess` → vpai CLI | Email (lowercase) |
| USA | 10 | Vibe Prospecting (same) | `subprocess` → vpai CLI | Email (lowercase) |

## Vibe Prospecting (vpai) API

The vpai CLI is called via `subprocess` from Python. Key commands:
- `vpai fetch-entities --args '{...}' --tool-reasoning '...'` — search prospects
- `vpai enrich-prospects --args '{...}' --tool-reasoning '...'` — get actual emails/phones

Auth: set `VP_API_KEY` env var (the script maps `VIBEPROSPECTING_API_KEY` → `VP_API_KEY` for the subprocess).

Key `fetch-entities` filters used:
- `entity_type`: `"prospects"`
- `company_size`: `{"values": ["51-200"]}`
- `linkedin_category`: `{"values": ["Apparel & Fashion", "Retail", "Consumer Goods"]}`
- `company_country_code`: ISO Alpha-2 codes (e.g., `["AE", "SA", "QA", "KW"]`)
- `job_level`: `["c-suite", "founder", "owner", "director", "vice president"]`
- `has_email`: `true`

`enrich-prospects` response: `enrichment_results.contacts` is a **double-encoded JSON string** — call `json.loads()` on it separately.

## Google Sheet Schema

**`DailyLeads`** (append-only, never overwrite):
`Company | Industry | EmployeeEstimate | Country | SupportChannel | ContactName | ContactRole | LinkedIn | Email | Phone | LeadSource | FitGrade | IntentScore | DateAdded | Status | Note`

**`UsedContacts`** (dedup registry):
`Region | Identifier | DateAdded | LeadSource`

## FitGrade Logic

- **Pakistan (Maps)**: always `"C"` — industry match confirmed, size unknown
- **Vibe (MENA/USA)**: `"A"` if job_level_array contains c-suite/founder/owner/director; else `"B"`

## GitHub Actions Schedule

```yaml
on:
  schedule:
    - cron: '0 1 * * *'  # 6:00 AM PKT = 1:00 AM UTC
  workflow_dispatch:       # manual trigger from GitHub UI
```

Secrets to configure in repo → Settings → Secrets → Actions:
`GOOGLE_SERVICE_ACCOUNT_JSON`, `GOOGLE_SHEET_ID`, `VIBEPROSPECTING_API_KEY`, `GOOGLE_MAPS_API_KEY`

## Key Constraints

- **Cost cap**: Under $20/day. Maps ~$0.88/day + vpai ~180 credits/day.
- **Paid API calls**: Always confirm with user before re-running scripts that call vpai enrich or Maps Place Details.
- **No overwriting**: `DailyLeads` is append-only. Never modify existing rows.
- **Google Sheets rate limit**: Batch writes only (`append_rows()`); 2 write calls per run total.
