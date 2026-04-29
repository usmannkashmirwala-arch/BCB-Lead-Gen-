# Workflow: Daily Lead Generation

## Objective
Generate 50 ICP-aligned leads per day and append them to the `DailyLeads` Google Sheet with a personalised outreach `Note` for each lead. No person should appear twice across the full history.

## ICP Reference
**Always read `Lead Details /BCB ICP and Offer.pdf` before running or modifying this workflow.** The script does this automatically at startup via `pdfplumber`. The PDF defines the target personas, messaging angles, fit grades, and disqualification rules that drive note generation.

Key persona angles (from the PDF):
- **Visionary Founder** (CEO/Founder/Owner): *"Let your support team handle the humans, while AI handles the 'Where's my order?' and size guide questions instantly — in any language."*
- **Progressive Support Lead** (Head of CX, Support Manager): *"Give your team superpowers. AI drafts the answer; they approve and focus on VIPs. You look like a strategic leader, not a firefighter."*

## Inputs Required
| Env var | Description |
|---------|-------------|
| `GOOGLE_MAPS_API_KEY` | Maps Platform key with Places API enabled |
| `VIBEPROSPECTING_API_KEY` | Vibe Prospecting (Explorium) tenant API key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Service account key JSON for Sheets access |
| `GOOGLE_SHEET_ID` | Workbook ID; must contain `DailyLeads` and `UsedContacts` tabs |

## Tool
`tools/generate_leads.py`

## End-to-End Steps

1. **Load ICP context** — reads `Lead Details /BCB ICP and Offer.pdf` via pdfplumber. Used to ground note generation in the latest ICP positioning.
2. **Authenticate** to Google Sheets via service account.
3. **Load `UsedContacts`** into memory: phone set (Pakistan), email set (MENA/USA).
4. **Pakistan leads (30)** via Google Maps:
   - Pick 5 random queries from the city × keyword pool.
   - Maps Text Search → up to 3 pages (60 results) per query.
   - Place Details call per candidate → get phone number.
   - Skip if no phone or phone already in UsedContacts.
   - FitGrade = "C" (size unknown from Maps data).
   - Generate outreach Note (Pakistan template — company-level, no contact name).
5. **MENA leads (10)** via Vibe Prospecting:
   - `vpai fetch-entities`: entity_type=prospects, company_size=51-200, linkedin_category=Apparel & Fashion, company_country_code=AE/SA/QA/KW, job_level=c-suite/founder/owner/director, has_email=true. Fetch 3× target.
   - `vpai enrich-prospects`: enrichments=["contacts"] — gets actual email and phone (2 credits/prospect).
   - Filter: skip if no email or email already in UsedContacts.
   - FitGrade = "A" for decision-maker job levels (c-suite, founder, owner, director), else "B".
   - Generate personalised Note based on role type (Founder vs Support Lead vs Generic).
6. **USA leads (10)**: same as MENA with company_country_code=US.
7. **Append to `DailyLeads`**: all leads with Status="New", IntentScore=0, Note filled.
8. **Append to `UsedContacts`**: one row per lead (Region, Identifier, DateAdded, LeadSource).

## Running Manually

```bash
# Install deps (first time only)
pip install -r requirements.txt
npm install -g @vibeprospecting/vpai@latest

# Test run — fetches from APIs but does NOT write to Sheets
python tools/generate_leads.py --dry-run

# Full run — writes to Sheets
python tools/generate_leads.py
```

**Important:** confirm with the user before re-running a full run on the same day — vpai enrichment costs credits (2 per prospect).

## Credit Usage Estimate (Vibe Prospecting, per day)
| Operation | Count | Credits |
|-----------|-------|---------|
| fetch-entities MENA (30 candidates) | 30 | 30 |
| fetch-entities USA (30 candidates) | 30 | 30 |
| enrich-prospects contacts (~60 enrichments) | 60 | 120 |
| **Total** | | **~180 credits/day** |

## Google Maps Cost Estimate (per day)
| Operation | Count | Est. Cost |
|-----------|-------|-----------|
| Text Search (5 queries × up to 3 pages) | ~15 | ~$0.48 |
| Place Details (~50 calls) | ~50 | ~$0.40 |
| **Total** | | **~$0.88/day** |

## Edge Cases & Known Constraints

**Pakistan — fewer than 30 results:** Expand PK_QUERIES list in the script with more city/keyword combos. After many days the Maps dataset for a given area may be exhausted — at that point supplement with yellowpages.pk or daraz.pk scraping (v2 feature).

**vpai — no email after enrichment:** Enrichment returns null for some prospects. They're silently skipped; the script logs how many contact records it got vs. candidates fetched.

**vpai — rate limit:** The script makes at most 2 fetch + 2 enrich calls per run (well within Explorium's limits). If a 429 is returned, the `_vpai()` function logs it and returns `{}`. Add `time.sleep(5)` between fetch and enrich calls in `fetch_vibe_leads()` if it occurs.

**Google Sheets rate limit (100 req/100s):** The script uses one `append_rows()` batch call per sheet — 2 writes total. Never an issue.

**Headers on first run:** `ensure_headers()` auto-writes header row if the sheet is empty. Share both tabs with the service account email before the first run.

**PDF not found:** If `Lead Details /BCB ICP and Offer.pdf` is missing, the script logs a warning and falls back to default templates. Note quality degrades but the run still completes.

## Scheduler (GitHub Actions)
- Cron: `0 1 * * *` (1:00 AM UTC = 6:00 AM PKT)
- File: `.github/workflows/daily_leads.yml`
- Manual trigger: GitHub → Actions → Daily Lead Generation → Run workflow
- Required secrets in GitHub repo Settings → Secrets → Actions:
  - `GOOGLE_SERVICE_ACCOUNT_JSON`
  - `GOOGLE_SHEET_ID`
  - `VIBEPROSPECTING_API_KEY`
  - `GOOGLE_MAPS_API_KEY`

## Google Sheet Schema

**`DailyLeads`** (append-only):
`Company | Industry | EmployeeEstimate | Country | SupportChannel | ContactName | ContactRole | LinkedIn | Email | Phone | LeadSource | FitGrade | IntentScore | DateAdded | Status | Note`

**`UsedContacts`** (dedup registry):
`Region | Identifier | DateAdded | LeadSource`
- Pakistan: Identifier = normalized phone (digits only)
- MENA/USA: Identifier = email (lowercase)

## v2 Improvements
- Pakistan fallback: scrape daraz.pk seller pages or yellowpages.pk when Maps results thin out.
- Intent signals: filter vpai results by `events: [{values: ["hiring_in_support_department", "increase_in_customer_service_department"], last_occurrence: 60}]` to surface high-intent accounts.
- Auto-detect support stack from company website and populate SupportChannel column.
- Daily summary email/Slack message with lead counts and any errors.
