# Chat mode (no shell)

For Claude.ai, Claude Desktop chat, ChatGPT and other apps where you can't run `jobhunt.py`. Their code sandboxes can't reach Monid's API, so everything goes through the Monid connector's tools: `monid_discover`, `monid_inspect`, `monid_run`, `monid_get_run`, `monid_balance`.

## Connect

If no `monid_*` tools are available, ask the user to add the connector once: Settings → Connectors → Add custom connector, name `Monid`, URL `https://mcp.monid.ai/v1`, then Connect and sign in. No account yet: https://monid.ai?fpr=charles (new accounts get $1 free credit). In ChatGPT it's Settings → Plugins → Developer Mode → new plugin with the same URL.

## Run

Same promises as shell mode: the user sends every application and email, and every claim in a draft is **traceable** to the facts the user gave you.

1. **Onboarding.** Ask for the resume, project links, target roles, cities, salary floor and work authorization in one message. Restate the facts inventory, standard form answers and preferences back as a compact block the user can save and paste into future chats, or keep in a Claude Project's knowledge.
2. **Search.** `monid_run` provider `anyapi`, endpoint `/linkedin.jobs`, body `{"query": ROLE, "location": CITY, "postedLimit": "week", "salary": BAND, "sortBy": "date"}`; BAND is the highest of `40k+ … 200k+` at or below the salary floor. One call per role × city, at most 4. Add `dataforseo /serp/google-jobs` with `{"keyword": "ROLE CITY", "location_code": 2840, "language_code": "en", "depth": 20}` for breadth.
3. **Filter and judge.** Drop staffing agencies (company description mentions recruitment or staffing), jobs whose top salary is below the floor, and anything the user already saw (ask them, or check the tracker they keep). Read the remaining descriptions against the facts inventory and pick up to 5.
4. **Research** each pick, stopping when the user's per-run budget (default $0.50) would be passed:
   - funding: `fundable /company`, query `{"domain": DOMAIN}` (startups only);
   - contact: `apollo /mixed_people/api_search`, query `{"person_titles[]": ["technical recruiter", "recruiter", "engineering manager"], "q_organization_domains_list[]": [DOMAIN], "per_page": 25}`, then `apollo /people/match`, query `{"id": ID}`;
   - email: `anyapi /email_finding.quickenrich`, body `{"companyDomain": DOMAIN, "firstName": F, "lastName": L}`, then `hunterio /email-finder`, query `{"domain": DOMAIN, "first_name": F, "last_name": L}` if not found. Keep an email only if its domain is the company's.

   A result whose `providerResponse.httpStatus` isn't 2xx failed (cost $0); skip that provider for the rest of the chat.
5. **Draft** per [drafting.md](drafting.md). For Greenhouse, Lever and Ashby jobs you can read the real form if your web tool can fetch it (Greenhouse: `https://boards-api.greenhouse.io/v1/boards/SLUG/jobs/ID?questions=true`) and answer its fields in order. Then show a table: role, company, pay, applicants, contact, why it fits, plus each draft. Report the Monid cost from the run results.
6. **Tracker.** End with a short table of every job shown and its status (shortlisted, skipped) for the user to keep, so tomorrow's run can skip them. If a Google Sheets or Notion connector is available, offer to append it there instead.

## Schedule

Claude.ai and ChatGPT can run a prompt on a schedule ("every weekday at 9, run my job hunt"); the connector's sign-in persists, so the run works unattended. Without a tracker it can't reliably skip jobs already shown, so pair a schedule with the Sheet or Notion tracker.
