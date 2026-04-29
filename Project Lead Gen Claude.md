# claude.md – Daily Lead Generation System for AI Automation Consultancy

## Project Goal
Automatically generate 50 high-quality, ICP-aligned leads every day and append them to a Google Sheet. I will then personally reach out using cold emails, Loom videos, and LinkedIn. The system must be fully automated, run on a daily schedule, and cost under $20/day total.

## Background & ICP
I run an AI automation consultancy in Pakistan. I sell customer-support AI agents and audit businesses for workflow gaps. My current sweet-spot customer (ICP v1.1) is:

**Ideal Account (Company):**
- Industry: E-commerce – Apparel & Fashion (primary). Adjacent: DTC physical goods (shoes, accessories, home textiles).
- Size: 50–200 employees. Revenue $1M–$25M.
- Technographics: Uses a structured support channel (helpdesk like Zendesk/Gorgias, live chat, or WhatsApp Business API with volume management). Signals: modern e-commerce stack, growing support team.
- Ticket volume: >400 repetitive queries/month (inferred from size/scale okay if not known).
- Geography: Pakistan, MENA (UAE, Saudi Arabia), USA.
- Not for: B2B wholesale, custom/bespoke low-ticket brands, companies where CEO thinks “AI will scare away customers” or wants to build entirely in-house.

**Ideal Contact (Person to Pitch):**
- Role: Founder, CEO, COO, Head of Customer Experience, Customer Support Manager. Preferably a decision-maker who believes in innovation and AI, not an insecure employee.
- Champion (support lead) + Budget holder (CEO) alignment is ideal, but we’ll start outreach anyway and nurture for internal selling later.

## Daily Lead Deliverable
Every day at 6:00 AM PKT, a Google Sheet named `DailyLeads` gets 50 new rows appended. The sheet must contain these columns (exactly, as they are my CRM):

| Column | Description |
|--------|-------------|
| `Company` | Company name |
| `Industry` | Category (e.g., Apparel, Fashion) |
| `EmployeeEstimate` | Number range (e.g., 51-200) |
| `Country` | Country of operation |
| `SupportChannel` | If known (e.g., Zendesk, WhatsApp Business) |
| `ContactName` | Full name of individual |
| `ContactRole` | Job title |
| `LinkedIn` | LinkedIn profile URL |
| `Email` | Verified email address |
| `Phone` | Phone number (especially important for Pakistan) |
| `LeadSource` | How we found them (e.g., GoogleMaps, Apollo) |
| `FitGrade` | A-F based on ICP match (auto-assigned by rules) |
| `IntentScore` | Numeric (starts 0, later updated manually after reply) |
| `DateAdded` | Date of lead generation (YYYY-MM-DD) |
| `Status` | “New” by default |

**Split of 50 Leads:**
- 30 leads from Pakistan (local e-commerce apparel brands)
- 10 leads from MENA (UAE, Saudi Arabia primary)
- 10 leads from USA (DTC apparel brands)

**Critical Rule:** No individual person should appear twice in the history of the `DailyLeads` sheet. The same company can appear across days, but only with different contacts. Duplicate detection must use unique identifiers: email for international (Apollo), phone number for Pakistan (since many don't have emails). Keep a separate `UsedContacts` sheet in the same Google Sheets workbook to track these identifiers and automatically exclude them daily.

## Lead Generation Strategy (No Pre-Built Pool)
I do not have a pre-existing master pool of companies. The system must generate fresh, unique leads on the fly every day using public sources and APIs. Here’s how:

### 1. Pakistan Leads (30/day)
**Primary source:** Google Maps Places API (Nearby Search or Text Search). It’s free within the $200 monthly credit, more than enough for this volume.

- **Method:** Use the Text Search (“clothing store in Karachi”, “apparel brand Lahore”, “fashion boutique Islamabad”, etc.) with a rotating list of 20+ Pakistani city + keyword combinations to avoid exhausting results. Each request returns up to 60 results; we’ll sample 3–5 queries daily and filter down to 30 new leads.
- **Extract:** Company name, formatted address, phone number (international format), website if available, place_id.
- **Enrichment:** Use Place Details (free) to get phone number if missing. For LinkedIn, later I will manually search, but the script could also auto-search if we integrate a free search. For now, leave LinkedIn blank; I’ll add manually. (Optional: use a clearbit or similar free tool, but no budget.)
- **Deduplication:** Compare phone number (normalise to digits only) against the `UsedContacts` sheet (Region=Pakistan). If phone already exists, skip that lead.
- **Fallback source:** If Maps API results start repeating too much (after many days), supplement with scraping public directories like yellowpages.pk or daraz.pk seller pages using a lightweight request/BS4 approach. But for v1, Maps API is sufficient for at least a month of unique leads given Pakistan's large number of clothing retailers.
- **FitGrade assignment:** Since Maps doesn’t tell employee count, set FitGrade = “C” (industry match but size unknown). We can manually upgrade after visiting website.

### 2. MENA Leads (10/day)
**Source:** Apollo.io People Search API.

**Query filters:**
- Person titles: “Founder”, “CEO”, “COO”, “Head of Customer Experience”, “Customer Support Manager”
- Company industry: “apparel & fashion”, “retail apparel”
- Company employee count: 51-200
- Location: United Arab Emirates, Saudi Arabia, Qatar (can add more MENA later)
- Optional: exclude contacts already in `UsedContacts` by Apollo Contact ID.

**Process:**
- Make API call with per_page=10, page=random(1-5) to get varying results daily.
- For each result that has an email (revealed or already provided), check if email already exists in `UsedContacts` (Region=MENA). Skip if yes.
- Extract: First name, last name, title, company name, email (if revealed, else skip), LinkedIn URL, company employee count, company industry.
- Email reveal costs ~$0.15/contact, within budget (~$3/day for all Apollos). So we can afford to reveal all 20 daily.

### 3. USA Leads (10/day)
**Source:** Apollo.io People Search API, same as MENA but with location = United States. Use same filters. Deduplicate on email.

## Technical Implementation Requirements

### APIs & Credentials (to be stored securely as environment variables in GitHub Actions)
- `GOOGLE_SERVICE_ACCOUNT_JSON` – Service account key for Google Sheets/Docs access.
- `GOOGLE_SHEET_ID` – ID of the Google Sheet workbook (must contain `DailyLeads` and `UsedContacts` sheets).
- `APOLLO_API_KEY` – Apollo.io API key.
- `GOOGLE_MAPS_API_KEY` – Google Maps API key with Places API enabled.

### Python Script (written by Claude Code)
The script `generate_leads.py` should:
1. Authenticate to Google Sheets using service account.
2. Load the `UsedContacts` sheet into memory (phone numbers for Pakistan, emails for MENA/USA with region).
3. For Pakistan:
   - Choose 4-5 random city+keyword combinations from a predefined list (e.g., “clothing store Karachi”, “apparel brand Lahore”, “boutique Islamabad”, “fashion retailer Faisalabad”, “pret wear seller Sialkot”, etc.).
   - For each, call Maps Text Search, collect up to 60 places.
   - Use Place Details to look up phone number and website for each.
   - Filter out results without a phone number or with phone already used.
   - Randomly select 30 (or as many available) and mark them as new leads.
4. For MENA:
   - Call Apollo People Search with region filters, per_page=10, page random 1-5.
   - For each contact, if email missing skip (only keep email-revealed or existing). Check email against used list. Use `reveal_personal_emails=true` in the API call to get email if not already revealed.
   - Extract fields; assign FitGrade based on Apollo data if employee count matches, else “B”.
5. For USA: similar to MENA.
6. Append all 50 rows to `DailyLeads` with DateAdded = today, Status = “New”, IntentScore = 0.
7. For each successfully added lead, append to `UsedContacts` sheet with: Region (Pakistan/MENA/USA), Identifier (normalized phone or email), DateAdded, LeadSource.
8. Handle API rate limits, errors gracefully, and log what happened (print to console for GitHub Actions logs).

### Scheduling
Use GitHub Actions with a cron schedule:
```yaml
on:
  schedule:
    - cron: '0 1 * * *'  # 6:00 AM PKT (UTC+5) = 1:00 AM UTC