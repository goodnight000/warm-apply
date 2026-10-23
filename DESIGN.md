# warm-apply: design

A portable agent skill that turns a user's resume and projects into a daily **shortlist** of a few jobs worth applying to, each with company research, warm paths for a referral (current employees who share the user's school or past employer), the likely hiring manager and recruiter, and a **packet**: a tailored resume PDF and the employer's real application form answered field by field, so the user reviews and clicks submit. Built on Monid for the paid data; free public ATS feeds and forms for the rest.

The goal it optimizes, in order: find the jobs the user would actually want and can get (fit), make applying one review and one click (packet), and raise the odds of an interview (channel, timing, quality).

Research behind each decision is in `research/` (01 what gets interviews, 02 tool landscape, 03 ATS feeds and terms, 04 agents and scheduling), all gathered 2026-10-21.

## Principles, and the evidence for them

| Principle | Why |
|---|---|
| Fewer, better applications (default 5/day, max 10) | Applications per hire roughly tripled since 2021 to 200–300+; mass auto-apply tools have no measured success (01). |
| Referral first, then the hiring contact, then the form | Cold applications reach interview ~3% of the time, recruiter-sourced 25%, referrals 40% (Ashby, 38M applications); a connection inside the company makes a hire ~7× likelier (LinkedIn 2026) (01). |
| Apply on the company's careers page | Careers pages: 24% of applications, 34% of hires; job boards: 50% and 23% (Greenhouse 2025) (01). |
| The user submits and sends | Every ATS submit API needs the employer's secret key; hosted forms run bot detection; LinkedIn, Indeed and MyGreenhouse forbid automation, Indeed naming "AI or Agentic AI" (03). Fabricated résumés are the top complaint about auto-apply tools (02). |
| Every draft claim traceable to `profile.md` | Recruiters penalize generic and fabricated text; edited AI drafts outperform raw ones (01). |
| Show the cost of each lookup | No competitor does; paid tools draw billing complaints (02). |
| Respect "no outreach" postings | Some employers state they ignore emailed resumes (seen live: EvenUp). |
| Apply early | After the first run, searches cover only the last day's postings, and score favors postings under a day old. The "early applicants win" claim traces only to vendor data (01), so it's a tie-breaker, not a rule. |
| The legal and policy fields stay the user's | Forms ask applicants to certify they completed the application themselves (OpenAI on Ashby), to accept arbitration, and some carry an AI-use policy (Anthropic on Greenhouse). The prefill never touches these. |

## Position

career-ops (~76K GitHub stars) already runs inside Claude Code and Codex, scores jobs, scans Greenhouse/Lever/Ashby and leaves Submit to the user. It drafts LinkedIn notes but finds no emails and does no company research (02). This skill's difference is referral-first and the research Monid makes cheap (warm paths through ContactOut's people search, whose API normally needs a sales meeting; funding and news; the likely hiring manager and a domain-checked email; LinkedIn applicant counts, recently funded companies to target, a ledger of what each lookup cost) and the packet: the real form read and prefilled, PDFs rendered, drafts linted. Credit career-ops; don't claim to be first.

## Sources and how Monid is used

| Source | Monid? | Cost | What it adds |
|---|---|---|---|
| LinkedIn (`anyapi /linkedin.jobs`, Apify fallback) | yes | ~$0.03/page | volume, applicant counts, salary filters, company website |
| Google Jobs (`dataforseo`) | yes | ~$0.005/call | aggregator breadth |
| Startups' ATS pages via Google (`dataforseo /serp/google-organic`, `site:jobs.ashbyhq.com …`) | yes | ~$0.004/query | postings that never reach LinkedIn, each confirmed live and form-readable |
| Hiring posts on X (`anyapi /twitter.search`) | yes | ~$0.002/query | founders' and managers' posts; the poster as a direct contact |
| Glassdoor (`anyapi /glassdoor.jobs`, opt-in) | yes | ~$0.05/search | salary estimates, ratings |
| Target companies' Greenhouse/Lever/Ashby boards | no | free | every opening at chosen companies |
| Hacker News "Who is hiring?" (Algolia) | no | free | founders' posts with salary and links |
| Workday targets (`wday/cxs`) | no | free | big companies |
| Recently funded companies (`fundable /deals`) | yes | ~$0.06/row, setup | targets that are likely hiring |
| Research: ContactOut people search and enrich, Apollo, Fundable, context.dev news, AnyAPI email/verify/profile, Hunter, Ploid (fallback) | yes | ~$0.16/job | warm paths, likely hiring manager, recruiter, domain-checked email, company health |

Using Monid well: one key and one balance across 13 providers; calls run in parallel under a thread-safe per-run and per-day budget; cheap routes pinned where they accept the inputs (Glassdoor's flat route doesn't, so it's opt-in); providers that return auth errors are skipped for 24 hours, because Monid's health labels showed "stable" for five broken ones; runs that report no cost are logged at the listed price and marked `estimated`; and `stats.by_source` shows which sources produce shortlisted jobs and interviews, so spend follows yield.

## Experience

Install with `./install.sh` (checks, CLI, key, PDF renderer, links the skill, self-test). The first session ends with a real inbox: one message to the user, three files written, target companies proposed, first run. The daily run is five agent steps: `run` (check + search + a judging sheet), judge, `prepare` (research + form, in parallel), draft, `packet` + `digest`. The user's surface is `inbox.html`: a card per job with what's left for them, the referral note to copy, an Open application button and PDFs, every form answer with a copy button, and the recruiter email as an Open in mail button.

## Architecture

```
SKILL.md                 the agent's procedure: setup, onboarding, daily run, approval queue, weekly review
references/              drafting, applying (one-click fill), sources & costs, scheduling, chat mode
scripts/jobhunt.py       deterministic pipeline (stdlib Python): search, dedupe, filter, fit score,
                         enrich, form, answer, lint, packet, digest, stats, companies, spend, budget, health, schedule
scripts/forms.py         read Greenhouse/Lever/Ashby forms, trace board jobs to them, prefill, render PDFs
JH_HOME/                 the user's state, one folder:
  config.json            roles, skills, level, places, salary floor, targets, warm paths, budget
  profile.md             facts inventory (only source of draft claims)
  answers.json           answers forms ask for; grows with remembered answers to recurring questions
  state.json             last search time (sets the posting-age window)
  jobs.json              every job ever seen, with status history (dedupe across runs)
  applications/<job>/    job.md (facts, research, why, gaps, red flags), resume.md + PDF, note.md + PDF,
                         referral.md, email.md, application.json + apply.md (the form, field by field)
  digests/<date>.md      the approval queue
  ledger.jsonl           every Monid call: endpoint, ok, http, cost
  providers.json         providers that failed with auth/server errors (skipped 24h)
```

Split of work: the script does everything that should be the same every run (calls, parsing, dedupe, money); the agent does judgment (fit, red flags) and writing (drafts). Fit is never decided by the score alone.

## Pipeline and measured cost (live run, 2026-10-22, mock profile)

| Step | What | Live result | Cost |
|---|---|---|---|
| Search | LinkedIn (2 role×city calls), Google Jobs (2), Anthropic + OpenAI ATS boards | 138 postings → 119 unique → 75 new, 44 filtered (agencies, remote-only, below floor) in 77 s | $0.067 |
| Judge | agent reads top descriptions vs profile | 2 shortlisted; skipped a defense contractor (dealbreaker) and a recruiter posing as an employer | $0 |
| Research | funding, news, contact, email, warm paths per job | OpenAI: SF recruiter, verified email, 2 real warm paths. EvenUp: Series E (Oct 2025), staff recruiter, verified email, flagged "careers page only" | $0.026 per job |
| Draft | resume, referral note, email, form answers | all claims trace to profile; gaps listed | $0 |
| Form + packet (2026-10-22, later) | read the real Ashby forms; prefill; PDF; lint | OpenAI: 12 of 20 fields filled automatically, 5 left to the user (start date, 2 preferences, 2 legal). EvenUp: 11 of 13 after one drafted essay answer; one-page PDF; lint clean | $0 |
| Trace-back | jobs found on LinkedIn → company form | Unify, Code Metal (Ashby by slug), Trustly (careers page `ashby_jid`) resolved; ADP-hosted HED falls back to by-hand | $0 |
| Target discovery | recent SF AI Series A–C rounds | Mach9 (Series A that day, 18 openings, 3 matching ML roles) | ~$0.06 per round |
| Rerun next day | dedupe | 107 already seen, 10 new | $0.067 |

These were the first measurements. After warm paths moved to ContactOut (2026-10-22), research costs about $0.16 per job (two ContactOut searches at $0.04, contact reveals at $0.026, email lookups, funding once per company). A day is about $0.20 to search plus $0.16 per prepared job, so about $1 for five.

## Guardrails

- Budget per run and per day, checked before each call against a conservative price table; the ledger records actual charges. Monid workspace caps (`BLOCKED`) are surfaced to the user.
- Provider health learned from real failures, because Monid's health labels showed "stable" for five providers that all returned an upstream 401 (Wellfound, Built In, Levels.fyi, YC, Indeed).
- Emails kept only when the domain matches the company's (a LinkedIn-URL lookup returned a previous employer's address in testing). Lookups go by name + company domain.
- Former employees excluded from warm paths and contacts by reading the headline's current employer ("Prev. @ X", "ex-X").
- Scam signals (fees, WhatsApp/Telegram, crypto pay, "task" jobs) sink a job's score; staffing agencies and "my client" postings are filtered.
- Enrichment is cached per job; `--refresh` re-researches.
- Fit knockouts from the posting text: no sponsorship when the user needs it, citizenship-only, clearance, years ≥ user's + 4. Softer signals score: skill overlap with `config.skills`, years, title level vs the user's level, freshness, applicant count, pay, months-old postings.
- `lint` blocks a packet until every number in the resume, note, emails and drafted answers appears in the profile, the posting or the user's answers; it also enforces length limits and flags filler, placeholders and posting skills the resume omits.
- `answer --remember` never saves legal or policy answers.

## Modes

- **Shell agents** (Claude Code, Codex, Cursor, Gemini CLI, OpenClaw): the script. Scheduled via Desktop/Codex scheduled tasks, cron, or GitHub Actions; `MONID_API_KEY` is registered with the CLI when no key is active.
- **Chat apps** (Claude.ai, ChatGPT): code sandboxes can't reach Monid, so `references/chat-mode.md` drives the Monid connector's tools directly; state lives in a tracker the user keeps (Sheet/Notion via connectors).

## Tested

- 59 offline tests (Python 3.9 and 3.15) replaying recorded Monid responses and three real application forms (fixtures scrubbed of real names): parsing, dedupe, filters, fit scoring and knockouts, gig and agency detection, current-employer and warm-path verification, email domain checks, budget stop and reporting, provider health, masking, digest and inbox, form detection and prefill, identity-field guards, location questions, saved answers, hand-off to the user, lint, stats, company discovery, Hacker News parsing, run → prepare → digest.
- Live runs on two mock candidates (ML engineer, product designer): every source exercised, forms read and packets built at OpenAI, EvenUp, Mercury, Ramp, Baseten; nothing submitted or sent.
- Fresh agents (SKILL.md only), three rounds: round 1 found 6 bugs and ~10 unclear instructions; round 2 found the "based in NYC?" misfill, hidden geo fields, false warm paths; round 3 (new sources and UX: ~17 tool calls for a full daily run, 3 ready packets) found the worst bug yet, a "Name a framework…" question auto-filled with the candidate's name, plus silent budget skips that left contacts empty, Europe-hours remote jobs, hourly gig postings scoring high, and noisy X results. All fixed and re-verified live (contacts return; the question is now a draft).

Not yet tested: chat mode through the connector; `MONID_API_KEY` bootstrap on a clean machine; GitHub Actions scheduling; `schedule --agent codex`; non-US locations (Google Jobs is pinned to the US).

## Open items

- Salary benchmarks: Levels.fyi through Monid is broken; when fixed, add "posted vs typical pay" to `job.md`.
- Workday, SmartRecruiters, Rippling, iCIMS and ADP forms aren't read; those jobs get a by-hand packet. Workday and Rippling feeds (03 documents them) are the next to add.
- The Ashby form endpoint and Lever's embedded cards are undocumented; they can change without notice.
- Fundable's `/deals` reports no per-call cost in the run record, so the ledger shows $0; the listed price is $0.06 per row returned.
- Fundable's totals can be wrong (OpenAI showed a "$1" valuation); the skill treats funding as a lead to confirm.
