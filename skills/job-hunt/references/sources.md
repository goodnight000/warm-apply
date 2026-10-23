# Sources

What `jobhunt.py` calls, in order, and why. Prices are Monid's listed prices on 2026-10-21, with what was actually charged in test runs where it differed; the ledger records real charges. Failed calls cost nothing.

## Job postings

| Order | Source | Endpoint | Cost | Why |
|---|---|---|---|---|
| 1 | LinkedIn | `anyapi /linkedin.jobs` | $0.029 per page of 25 | Salary-band and recency filters, full description, applicant count, company website (gives the domain for research). |
| 1b | LinkedIn (fallback) | `apify /harvestapi/linkedin-job-search` | ~$0.001 per job | Runs only if 1 fails. Sometimes includes the job poster (`hiringTeam`), which saves a contact search. |
| 2 | Google Jobs | `dataforseo /serp/google-jobs` | $0.0048 per call (20 results) | Breadth: aggregates company career pages and smaller boards. No description or domain. |
| 3 | Target companies' ATS boards | Greenhouse, Lever, Ashby public JSON | free | Every opening at the companies the user cares most about, straight from the source, often with pay ranges. |
| 4 | Startups' job pages via Google (`ats_web`) | `dataforseo /serp/google-organic`: `site:jobs.ashbyhq.com "ROLE" "PLACE"`, same for `greenhouse.io` and `jobs.lever.co` | ~$0.004 per query (2 pages), up to 9 per run | Postings at startups that never reach LinkedIn. Each hit is confirmed live against the board's own API (free), so closed postings drop out, and each is a form `form` can read. In testing one query returned 10 live Ashby postings. Results aren't date-filtered, so old evergreen postings appear and score lower. |
| 5 | Hacker News "Who is hiring?" (`hackernews`) | Algolia HN API | free | This month's thread; top-level posts mentioning a role's distinctive words. Mostly founders posting, often with salary and a direct link; the poster is listed as the contact. |
| 6 | Hiring posts on X (`x`) | `anyapi /twitter.search` | ~$0.002 per query, 2 per run | "ROLE" with hiring language, links only, no replies or retweets, last 7 days. Links are resolved (t.co); an ATS link becomes a full posting. Job-board bot accounts are dropped unless they link to a readable posting. A person's post keeps them as the contact. |
| 7 | Workday targets (`workday`) | the careers site's own `wday/cxs` JSON | free | For big companies: add `{"name", "ats": "workday", "url": "https://acme.wd5.myworkdayjobs.com/External"}` to `target_companies`. Forms aren't read; those jobs get a by-hand packet. |
| off | Glassdoor (`glassdoor`) | `anyapi /glassdoor.jobs` | ~$0.05 per search (billed per result; the flat-priced route doesn't accept search filters) | Breadth plus Glassdoor salary estimates and company ratings. |

Searches run in parallel. Each role × location pair is one LinkedIn call, plus one remote-anywhere call per role when `workplace` includes `remote` (capped at 6), up to 3 × 2 Google Jobs calls, and up to 9 `ats_web` queries. A typical run is 13–20 paid calls and $0.08–0.12.

### Not used, and why (2026-10-21)

- Wellfound, Built In, YC Work at a Startup, Indeed, Levels.fyi, Crunchbase: return an upstream 401 through Monid (Crunchbase checked 2026-10-21).
- LinkedIn post search (`apify /harvestapi/linkedin-post-search`, ~$0.09 for 10): mostly job-board and agency reposts in testing.
- Reddit, Upwork, Idealist, web3.career: niche; enable by hand for freelance, nonprofit or crypto searches.

### Adding a target company's ATS board

Open the company's careers page and read the job link:

| Link contains | `ats` | `slug` is |
|---|---|---|
| `boards.greenhouse.io/X` or `job-boards.greenhouse.io/X` | `greenhouse` | `X` |
| `jobs.lever.co/X` | `lever` | `X` |
| `jobs.ashbyhq.com/X` | `ashby` | `X` |

Add `{"name": "Anthropic", "ats": "greenhouse", "slug": "anthropic", "domain": "anthropic.com"}` to `target_companies`. Check it with `curl -s https://boards-api.greenhouse.io/v1/boards/SLUG/jobs | head -c 200` (Lever: `https://api.lever.co/v0/postings/SLUG?mode=json`; Ashby: `https://api.ashbyhq.com/posting-api/job-board/SLUG`). Ashby returns `{"jobs":[]}` with HTTP 200 for any slug, including companies not on Ashby, so an empty Ashby board is a wrong slug, not a company with no openings. Workday boards are read when added as a target with their careers-site URL (row 7). Other systems (iCIMS, SmartRecruiters, ADP and the rest) aren't read directly; their jobs reach the pipeline through LinkedIn and Google Jobs. A bare company name in `target_companies` still boosts that company's score.

## Research for a shortlisted job

| Step | Endpoint | Cost | Notes |
|---|---|---|---|
| Domain (if the posting lacked one) | `fundable /company/search` | $0.01 | |
| Funding | `fundable /company` | listed $0.06; charged $0 in testing | Skipped for companies over 5,000 employees. Its totals can disagree with its own round descriptions; confirm against news before quoting. |
| News | `context.dev /news/search` | ~$0.0003 per 10 | Articles mentioning the company; read before citing, some only mention it in passing. |
| Contact search | `apollo /mixed_people/api_search` | free | Up to 25 people matching `contact_titles` plus recruiter titles at the company domain (last names hidden). The **likely hiring manager** is a manager whose title shares words with the user's role or the team in the posting title; at a company of 300 or fewer, a manager in the user's job family. No match, no guess. |
| Contact reveal | `apollo /people/match` | $0.026 | Full name, title, LinkedIn; an email only when Apollo has a verified one. |
| Contact fallback, warm paths | `ploid /linkedin/search` | listed $0.01; charged $0 in testing | Keyword search ("Company recruiter", "Company Stanford"); structured title filters returned nothing in testing. |
| Warm paths | `contactout /v1/people/search/work-email` | $0.02 per profile returned (2 per search) | Current employees (company domain) with the user's school (`education`) or past employer (`past_company`), terms OR-ed so it's one search per kind; filtered to the user's `job_function` first, without it if that finds nobody. The profile data itself shows the current employer and the shared history. ContactOut's API normally requires booking a sales meeting for a key (api.contactout.com); through Monid it's per call. In testing: three OpenAI engineers sharing UC Berkeley or Stripe, where LinkedIn keyword search had found comms and workplace staff. |
| Warm path fallback check | `anyapi /linkedin.profile` | $0.0029 | Keeps a warm path only if the person's current position is at the company (same name or LinkedIn company URL) and the shared school or employer is in their education or experience. Keyword matches alone included former employees and a different company with the same name. |
| Email | `anyapi /email_finding.quickenrich` → `contactout /v1/people/enrich/work-email` → `hunterio /email-finder` | $0.0072 (free if not found) → listed $0.02 → $0.0245 | Name + company domain first; then ContactOut from the contact's LinkedIn URL (ContactOut's own API, which normally requires booking a sales meeting for a key: api.contactout.com, checked 2026-10-21); then Hunter. ContactOut returns every work email it has, including past employers'; only one on the company's domain is kept. |
| Verify | `anyapi /email.verify` | $0.0066 | Only when the finder didn't verify it. |

An email is discarded unless its domain belongs to the company (another top-level domain of the same name counts: acme.io for acme.com).

## Application forms (free, no Monid)

`JH form` reads the real form from the employer's applicant tracking system, the same requests its job page makes:

| System | Form source | Notes |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/SLUG/jobs/ID?questions=true` | Documented. Custom questions, location questions, EEO compliance, demographic questions. |
| Ashby | `jobs.ashbyhq.com/api/non-user-graphql` (`ApiJobPosting`) | Undocumented: what Ashby's own job page calls. Field types, required flags, options. |
| Lever | `jobs.lever.co/SLUG/ID/apply` page | Standard fields plus custom question cards embedded as JSON; EEO selects. |

A job found on LinkedIn or Google Jobs is traced to the company's own posting by trying the company name and domain as board slugs on all three and matching the title, and by reading `gh_jid` / `ashby_jid` parameters on careers-page links. Workday, iCIMS, ADP, Avature and others aren't read; `JH form` says so and the agent prepares answers by hand. The undocumented endpoints can change without notice; if one stops working, `form` reports it couldn't read the form and the job falls back to by-hand.

## Known failures (2026-10-21)

Wellfound, Built In, Levels.fyi, YC Work at a Startup and Indeed all returned an upstream "Invalid API key" through Monid while their health showed "stable", so health labels aren't proof. The script learns from real failures instead (`providers.json`, 24-hour skip).

## Onboarding helpers

`anyapi /linkedin.profile` ($0.0029–0.0042) reads the user's own LinkedIn profile; `anyapi /github.user_repositories` ($0.0012) lists their repos. Use them only on the user's own links, with `monid run -p anyapi -e /linkedin.profile -i '{"url":"..."}' -w 60 -j`.
