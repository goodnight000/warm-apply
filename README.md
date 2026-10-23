# warm-apply

**An AI job hunter that gets you referred before it helps you apply.**

Recruiters now get more than 300 applications for every hire ([Ashby, May 2026](https://www.prnewswire.com/news-releases/new-data-from-ashby-reveals-surge-in-applications-rising-selectivity-and-shifting-recruiter-workloads-302765846.html)). In Ashby's data from 38 million applications, cold applications reached an interview about 3% of the time; referred candidates reached one about 40% of the time ([Ashby referrals report](https://www.ashbyhq.com/talent-trends-report/reports/referrals)). Having AI apply to 500 jobs for you just adds to the pile.

warm-apply is an agent skill for Claude Code, Codex, Cursor, Gemini CLI and other agents that run shell commands. Give it your resume once. Every morning it hands you a short list of jobs that fit, each with a referral message ready to send and the company's real application filled in up to the Submit button.

## What it does each morning

1. **Finds new jobs** on LinkedIn, Google Jobs, startups' own Greenhouse, Lever and Ashby job pages, Hacker News "Who is hiring?", and the job boards of companies you pick. Hiring posts on X and Glassdoor are optional.
2. **Throws out jobs you'd be auto-rejected from**: no visa sponsorship when you need it, clearance or citizenship requirements, years far beyond yours, pay below your floor, the wrong place, staffing agencies and scam signals. It scores the rest, and your agent reads the best ones against your resume and picks up to five.
3. **Finds people who can refer you**: current employees who went to your school or worked at your old company, found through ContactOut's people search and in your own job function first. It also finds the likely hiring manager and the recruiter. It keeps an email only when it's on the company's own domain.
4. **Fills in the real application.** It reads the company's actual Greenhouse, Lever or Ashby form and answers every field it can from your saved answers. Legal acknowledgements, AI-use policies and personal preferences are left for you.
5. **Writes the drafts**: a resume tailored to the posting (rendered to PDF), the referral message, a cover note if the form asks for one, and an email to the hiring manager or recruiter. Every claim must trace back to your resume; a check blocks numbers it can't find there.
6. **Puts it all in one inbox page.** For each job you copy the referral message, click **Open application**, check the highlighted fields and click Submit.

It never submits an application, sends a message or emails anyone; you do those yourself. It doesn't touch LinkedIn Easy Apply or Indeed Apply, whose terms forbid automation. It prepares five jobs a day by default (ten at most), because the point is fewer, better applications.

## Powered by Monid

All the paid data goes through [Monid](https://monid.ai?fpr=charles): one API key for 1,700+ APIs, paid per call, no subscriptions. That covers LinkedIn job search, Google Jobs, Apollo, Hunter, funding and news data, and ContactOut. ContactOut normally makes you book a sales meeting just to get an API key; through Monid it's the same key as everything else, about $0.02 per profile. Without Monid, each of those would be a separate account, contract or scraper.

**Cost:** about $0.20 a day to search, plus about $0.16 for each job it prepares, so roughly $1 a day for five prepared jobs. Applications, forms and PDFs are free. Default caps are $1.00 per run and $1.50 per day, and every call is logged with what it cost. New Monid accounts get $1 of free credit, enough for the first day.

**[Sign up for Monid →](https://monid.ai?fpr=charles)** (referral link)

## Set up

### Ask your agent (easiest)

Open Claude Code, Codex, Cursor or Gemini CLI and paste:

> Set up https://github.com/goodnight000/warm-apply for me and run my job hunt.

Have your resume handy. The agent installs everything, asks you one round of questions (roles, cities or remote, salary floor, work authorization, schools and past employers), and finishes with your first inbox. It then offers to run every weekday morning.

### By hand

```sh
git clone https://github.com/goodnight000/warm-apply ~/warm-apply
~/warm-apply/install.sh
```

The installer checks for Python 3.9+, installs the Monid CLI, registers your key (`MONID_API_KEY=… ~/warm-apply/install.sh`, or it tells you how), checks for Chrome (used to render PDFs), links the skill into Claude Code (`~/.claude/skills`) and Codex, Cursor and Gemini CLI (`~/.agents/skills`), and runs the offline self-test. Then open your agent anywhere and say **"Set up my job hunt"** with your resume attached.

It runs on macOS and Linux. On Windows, use WSL.

### One-off or every morning

- **One-off:** say "run my job hunt" whenever you want a fresh inbox.
- **Every morning:** say "run my job hunt every weekday at 9". The agent runs `jobhunt.py schedule --at 09:07`, which installs a launchd job on macOS or a cron job on Linux that starts your agent headless and does the full run. On a Mac it sends a notification when it's done, and if the Mac was asleep at that time, it runs when it wakes. Under Claude Code the unattended agent may run only this skill's script and write only inside your job-hunt folder, because job postings are untrusted text and nobody is watching. Codex runs need its full-access mode, since its sandbox blocks the network. To stop it: "stop my job hunt schedule" (`schedule --remove`).
- The Claude Code Desktop and Codex app scheduled tasks and GitHub Actions also work; see [scheduling.md](skills/job-hunt/references/scheduling.md).

### Claude.ai or ChatGPT (no terminal)

Add the Monid connector (`https://mcp.monid.ai/v1`) and upload the `skills/job-hunt` folder as a skill. The agent follows [chat-mode.md](skills/job-hunt/references/chat-mode.md). It does a lighter version of the same run, with no form filling or PDFs.

## For AI agents installing this

You've been asked to set this up for your user. Do these in order:

1. **Get the code.** `git clone https://github.com/goodnight000/warm-apply ~/warm-apply`, or `git -C ~/warm-apply pull` if it's already there.
2. **Install.** Run `~/warm-apply/install.sh` and read what it prints.
   - If the Monid key is missing, send the user to https://monid.ai?fpr=charles to sign up ($1 free credit), then to https://app.monid.ai/access/api-keys to create a key. Have them run `monid keys add -k THEIR_KEY -l main` themselves. If they paste the key to you instead, run that command for them and never repeat the key back.
   - Rerun `install.sh` until it prints "Ready".
3. **Load the skill.** It's now linked into `~/.claude/skills/job-hunt` and `~/.agents/skills/job-hunt`, so future sessions find it. If your session loaded skills before the install, read `~/warm-apply/skills/job-hunt/SKILL.md` now and follow it directly.
4. **Set it up** by following SKILL.md's "Set up (first session)". The user's files go in `~/job-search/job-hunt/`. Ask your questions in one message, and end the session with the first inbox open.
5. **Offer the automation** (SKILL.md, step 7). Ask the time, run `jobhunt.py schedule --at HH:MM` (add `--agent codex` if you are Codex), and offer one test run.

Hard rules: never click Submit, send a message or email anyone for the user. Never put the user's `job-hunt/` folder in a public repository, because it holds their resume and answers. Stop at the budget the user sets.

If you can't run shell commands, use [chat-mode.md](skills/job-hunt/references/chat-mode.md) instead.

## What's in here

- `skills/job-hunt/SKILL.md` is the agent's procedure: setup, the daily run, applying, the weekly review.
- `skills/job-hunt/references/` covers drafting, applying, sources and costs, scheduling, and chat mode.
- `skills/job-hunt/scripts/jobhunt.py` is the pipeline: search, dedupe, filter, score, research, forms, lint, PDFs, inbox, stats and schedule. It's standard-library Python with no dependencies. `forms.py` reads and prefills Greenhouse, Lever and Ashby forms.
- `DESIGN.md` explains why it works the way it does. `research/` has the evidence behind it, gathered October 2026.

Your files stay on your machine, in `~/job-search/job-hunt/`. Paid lookups go through Monid, free job boards and forms are fetched directly, and your agent's model provider sees what the agent reads.

Tests: `cd skills/job-hunt/scripts && python3 -m unittest test_jobhunt.py`. They run offline, replaying recorded responses.

## License

MIT
