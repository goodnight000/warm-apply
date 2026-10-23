---
name: job-hunt
description: >-
  Research-first job hunting through Monid: learns the user's resume once, then finds fresh postings
  that fit (LinkedIn, Google Jobs, startups' own job boards, Hacker News, hiring posts on X, opt-in),
  researches each company, finds the recruiter and people inside who can refer them, reads the real
  application form, and prepares everything (tailored resume PDF, form answers, referral ask,
  outreach email) in an inbox page so the user reviews and clicks submit. Use when the user wants
  to find jobs, set up, run or schedule a daily job search, apply to a job, get a referral, reach a recruiter, or review their job pipeline.
license: MIT
compatibility: Shell mode needs Python 3.9+ and the Monid CLI (npm i -g @monid-ai/cli); Chrome, Chromium or Edge renders PDFs. Chat apps without a shell use the Monid connector (https://mcp.monid.ai/v1).
---

# Job hunt

A daily **shortlist** of the few jobs most worth the user's time, each **packet**-ready: tailored resume PDF, every form field answered or flagged, a referral ask and an outreach email, all in one **inbox** page. The user reviews and clicks submit; this skill never presses it.

`JH` below means `python3 SKILL_DIR/scripts/jobhunt.py` (`SKILL_DIR` is this skill's folder); write it out in full or define a shell function, since zsh won't split a variable holding it. Every command prints JSON and exits 1 with an `error` and a `hint`; `JH COMMAND --help` lists flags. State lives in one folder, `JH_HOME`: `$JOB_HUNT_HOME`, else `./job-hunt` if the current directory has one, else `~/job-search/job-hunt`.

No shell (Claude.ai, ChatGPT chat)? Follow [references/chat-mode.md](references/chat-mode.md) instead.

## Set up (first session)

The goal of the first session is a real inbox with real jobs in it, not a configured tool. Keep questions to one message.

1. **Install check.** `JH init` if there's no `JH_HOME`, then `JH check`; its `next_steps` list what's missing. For a missing Monid key, send the user to https://monid.ai?fpr=charles to sign up (new accounts get $1 free credit, enough for the first day), then to https://app.monid.ai/access/api-keys for a key. Have them run `monid keys add -k KEY -l main` themselves, or paste the key to you, and never echo it back. Scheduled runs may pass `MONID_API_KEY` instead.
2. **One message to the user.** Ask for: resume (file or paste), project links, GitHub and LinkedIn URLs, what they want next (roles, cities or remote, salary floor, company stage, industries to avoid), and anything they're proud of that the resume leaves out. For LinkedIn and GitHub links you can't open, use Monid (`anyapi /linkedin.profile`, `anyapi /github.user_repositories`, about $0.005 together).
3. **Write the three files** in `JH_HOME`:
   - `profile.md`, the **facts inventory**: experience with the numbers the user confirmed, projects with links, skills, education, short true stories (used in referral asks, notes and form answers). The only source of claims for every draft.
   - `answers.json`: the answers forms ask for, used to fill fields automatically: name, contact, location, country, links, current company and title, `years_experience`, `work_authorized`, `needs_sponsorship`, `security_clearance` (`""` if none), `willing_to_relocate`, `in_office_ok`, `us_citizen` (true/false; drops US-citizens-only jobs when false), `earliest_start`, `salary_expectation` (`""` leaves it to the user), pronouns, and `self_id` (voluntary demographic questions; default `decline`). `custom` grows as the user answers recurring questions.
   - `config.json`: `roles` (2–4 titles as postings phrase them; they also filter titles), `skills` (10–25 the user actually has per `profile.md`, named the way postings name them; they drive fit scoring and the resume keyword check, so a skill they lack here would push a false claim), `level` (`entry`, `mid`, `senior`, `staff`, `principal`, or a list like `["mid", "senior"]` for a range), `locations` + `workplace` (a job passes if it's remote and `workplace` includes `remote`, or it's in one of `locations` with an arrangement in `workplace`), `salary_min`, `exclude_title_keywords`, `exclude_companies`, `dealbreaker_keywords`, `warm_paths` (schools and past employers, most distinctive first; `{"school": …}` or `{"company": …}` when a name is ambiguous), `job_function` (ContactOut's functional area for the user: Engineering, Product Management, Design, Marketing, Sales, Operations…; it keeps referral paths and the hiring manager in the user's own function), `contact_titles` (hiring-manager titles for their field first), `daily_shortlist` (default 5, max 10), `sources` (see below), `budget`.
4. **Target companies.** `JH companies --industry X --location Y` finds recently funded companies and their job boards (about $0.06 per funding round returned). Propose 10–20 targets with the user (these, plus peers of past employers) and add them to `target_companies` with their ATS slugs ([references/sources.md](references/sources.md)); their boards are scanned free every run. Big companies on Workday go in as `{"name", "ats": "workday", "url": "<careers site URL>"}`.
5. **Confirm** a five-line summary (roles, level, places, salary floor, sources) with the user. `JH check` must show `ready: true`.
6. **Run the first daily run now** (below), so the session ends with an inbox, and `JH open` it.
7. **Offer the automation.** "Want this every weekday morning?" If yes, ask the time and run `JH schedule --at HH:MM` (add `--agent codex` under Codex), then offer to test it now with the `run_now` command it prints (about $0.20: today's jobs are already seen, so it mostly searches). Other setups (desktop apps, GitHub Actions, chat apps): [references/scheduling.md](references/scheduling.md). If they'd rather run it by hand, they say "run my job hunt" any day.

## Daily run

Five steps. A scheduled run does all five and leaves the inbox for the user.

1. **Find.** `JH run`. It checks Monid, searches every enabled source in parallel, dedupes against every job ever seen, removes knockouts (no sponsorship when needed, clearance, citizenship, years far beyond the user's), agencies, and wrong titles, places and pay, scores the rest on fit, and returns `to_judge`: the top new and saved jobs with scoring reasons and an excerpt of each posting's requirements. After the first run it searches only the last day's postings. Mention anything in `notes` or `monid.failed`.
2. **Judge.** Fit is your call; the score is a pre-rank. For each job in `to_judge`, read the excerpt against `profile.md` (`JH show ID` for the full posting when it's close): hard requirements, years, seniority, domain, dealbreakers, red flags (`possible scam`, no salary where required, vague duties, months-old posting). `JH set ID shortlisted --note "one-line why"` for up to `daily_shortlist`; `saved --note` for good fits that missed today's cut; `skipped --note` for misses. Done when every job in `to_judge` has a status and a note. `to_judge` holds the top 15; `JH list --status new --top 40` shows the rest, worth a look on the first run or when few fit. Between close calls, prefer fewer applicants, posted in the last day, and founders' posts (Hacker News, X), where the poster is often the hiring manager.
3. **Prepare.** `JH prepare ID...` for the shortlist: research (funding, news, the recruiter, the **likely hiring manager** when a manager's title matches the role or team, and **warm paths**: current employees who share the user's school or past employer, found through ContactOut's people search, same job function first; emails kept only on the company's domain) and the real application form prefilled from `answers.json`, all jobs in parallel. Each result lists `write_these` (form fields for you), `for_user` (only the user can answer or agree: legal, AI-use policy, preferences), warnings, the contact and warm paths.
4. **Draft**, per job, following [references/drafting.md](references/drafting.md):
   - `resume.md`: the user's resume reordered and reworded for this posting.
   - each `write_these` field: `JH answer ID FIELD "text"`. When the profile can't support an honest answer: `JH answer ID FIELD --to-user "what to ask them"`. Facts from the user or `answers.json` take `--user` (`JH answer ID FIELD "Yes" --user`); leave it off only for answers you drafted, so the inbox marks those for review. When a prefilled answer is wrong, correct it with `--user` and fix `answers.json`.
   - `note.md` if the form has a cover letter field; `referral.md` if there's a warm path (sent first: referrals reach interviews far more often than cold applications); `email.md` if there's a contact and no "Careers page only" notice.

   Every claim is **traceable** to `profile.md`; requirements the user lacks go under "Gaps" in `job.md`.
5. **Packet and inbox.** `JH packet ID` renders the PDFs, attaches them, scores keyword match (how many of the posting's skills that the user has appear in the tailored resume; shown on the inbox card) and lints (untraceable numbers, length limits, filler, placeholders, posting skills the resume omits, empty required fields); fix everything in `blocking` and rerun until `ready: true`, which marks the job drafted. A job whose form can't be read (Workday, LinkedIn-only, a plain link) is ready by hand (`by_hand: true`): the user applies on the posting with the PDFs and drafts. `ready` means your part is done; `required_for_user` lists required fields the user still has to answer at submit time (often a LinkedIn URL: add it to `answers.json` if they have one). Then `JH digest` writes the inbox and `JH open` opens it (a scheduled run skips opening). Report in three lines: how many are ready, the best one and why, today's Monid cost.

## Inbox and applying

The inbox (`JH_HOME/inbox.html`) has a card per job: why it fits, what's left for the user, the referral note with a copy button and the person's profile, an **Open application** button with the PDFs, every form field with its answer and a copy button, and the outreach email as an **Open in mail** button. When the user wants help applying ("apply to 2"), follow [references/applying.md](references/applying.md): referral first, then the form (filled in their browser up to the submit button when you can drive it), then the email; record it with `JH set ID applied --via referral,email,form`. Follow-ups and outcomes are in the same file.

## Sources

`config.sources` turns each on or off. On by default: `linkedin`, `google_jobs`, `ats_boards` (target companies' own boards, free), `ats_web` (Google restricted to Greenhouse, Lever and Ashby pages: startups that never post to LinkedIn, each one a form the skill can read), `hackernews` (this month's "Who is hiring?", free; strongest for engineers and designers at startups), and Workday targets. Off by default: `x` (hiring posts on X; noisy for most roles in testing) and `glassdoor` (about $0.05 a search; adds salary estimates and company ratings). Watch `stats.by_source` and turn sources off that never produce a shortlisted job. Costs and behavior: [references/sources.md](references/sources.md).

## Weekly review

After about ten applications, `JH stats`: interview rate by channel, and `by_source` (found → passed filters → shortlisted → applied → interviews). Tell the user what's working ("referrals: 2 of 3 led to interviews; cold forms: 0 of 7"; "Hacker News gave 4 of your 6 shortlisted jobs") and propose config changes: sources to drop or add, roles, level, skills, places, targets. Change config only after they agree.

## Spend and failures

- Every paid call is logged in `ledger.jsonl` (`cost_unreported: true` where Monid reported no cost; those record $0, which matched the balance in testing); `JH spend` totals it. Each paid command (`run`, `prepare`) stops before `budget.per_run_usd` (default $1.00), and all of them together before `budget.per_day_usd` ($1.50), checked against the ledger before every call; setup steps like `companies` don't count against the day. Calls skipped for budget are listed in `monid.skipped_for_budget`: say so, since that job's research is incomplete. A day costs about $0.20 to search plus $0.15–0.20 per prepared job (company research is cached for 30 days); forms, PDFs and the free sources cost nothing.
- A provider failing with an auth error is skipped for 24 hours (`providers.json`); a source that errors is reported in `notes` and the rest of the run continues. The user can tell Monid with `monid feedback -t "..."`.
- A Monid `BLOCKED` run means a workspace budget or run cap stopped it; tell the user where to change it (https://app.monid.ai).
- If a source stops returning data, `monid discover -q "linkedin jobs"` (free) finds a replacement.
