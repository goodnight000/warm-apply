# Applying

The user's part of an application should be: read what was prepared, answer the few `user` fields, click submit. Everything else is done before they look.

## Show the queue

From `digests/<date>.md`, for each job: role, company, pay, why it fits (the shortlist note), gaps from `job.md`, warm paths, and what's left for them (`for_user` from `JH packet ID`). Lead with jobs that have a warm path or posted in the last day.

## On "apply" for a job

1. `JH set ID approved`.
2. **Referral first.** If `referral.md` exists, show it with the person's LinkedIn link; the user sends it from their own account. Warm paths marked `verified` were checked against the person's profile; for any that aren't, the user confirms the shared school or employer first. If the application can wait a day for a reply, say so; otherwise apply now and the referral can still flag the application internally.
3. **The form.** Use the company's own form (`apply_url` from `application.json`), which converts better than job-board quick-apply.
   - **If you can drive the user's browser** (Claude in Chrome, a browser or Playwright MCP, computer use): open `apply_url` in their browser, so logins and CAPTCHAs are theirs. Fill each field from `application.json` in order, choosing the listed option for selects; upload the PDFs from the job folder into the file fields. Leave every `user` field empty, including legal acknowledgements and AI-policy questions, which the user must read and decide themselves. Scroll to the submit button and stop there: "Everything's filled except N fields for you (listed). Review, finish those, and click Submit."
   - **Otherwise**: give the user `apply.md` (fields in form order, answers ready to paste) and the PDF paths, plus the link.
   - Never automate LinkedIn Easy Apply or Indeed Apply; both platforms' terms forbid it. If a job only offers those, give the user the answers to paste.
   - **Several at once** ("apply to all ready"): with browser control, run an apply session: for each approved job in inbox order, open its form in a new tab and fill it as above, then move to the next, leaving every tab parked at its Submit button. The user goes tab by tab, answers their highlighted fields and clicks Submit. Tell them how many tabs are ready and what's left in each.
4. **Outreach.** After they've applied, show `email.md` ready to send from their own mail (a `mailto:` link, or a draft in their mail tool if one is connected).
5. When the user confirms: `JH set ID applied --via form` (add `referral` and/or `email` for what they sent). The channels feed `JH stats`.

If the user asks you to submit for them: explain that forms often ask them to certify they completed the application themselves, that hosted forms run bot detection, and that a reviewed application does better; then get it to the point where their click is the only step.

## After applying

- **Follow-up.** The digest lists applications 5+ days old with no update. Offer one follow-up (see drafting.md), then `JH set ID applied --followed-up`.
- **Ghosted.** At 14 days with nothing, the digest suggests `ghosted`; confirm with the user and set it.
- **Outcomes.** `interviewing`, `offer`, `rejected`. These feed `JH stats`, which shows which channels and sources are landing interviews.
