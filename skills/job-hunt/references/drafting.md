# Drafting

Every draft is built from two inputs only: the posting (`job.md`) and the facts inventory (`profile.md`). A claim is **traceable** when you can point to the line in `profile.md` it came from; a draft is done when every claim is. Rewording, reordering, choosing which facts to lead with, and mirroring the posting's vocabulary for skills the user actually has are all fair. A number, title, date, tool or outcome that isn't in `profile.md` stays out, however well it would fit; it goes under "Gaps" in `job.md` as a question for the user instead.

Write in the user's voice: plain, specific, first person, no filler openers ("I am writing to express…", "I hope this finds you well"), no superlatives about themselves. `JH lint ID` checks the mechanical part: every number must appear in `profile.md`, the posting or `answers.json`; length limits; filler; placeholders.

If the form has an AI-policy question (`apply.md` warns about it), the employer may restrict AI-written answers. Keep drafted answers short and factual, and tell the user to read the policy and rewrite in their own words where it asks.

## resume.md

- Start from the user's resume structure; keep it to one page (lint warns over 750 words). Plain Markdown: `# Name`, a contact line, `## Section` headings, `**Role, Company** — place, years`, and `-` bullets; `JH packet` renders that to a clean single-column PDF that applicant tracking systems parse.
- Reorder sections and bullets so the posting's top three requirements are answered in the top third.
- Use the posting's exact names for skills and tools the user has (applicant tracking systems match terms literally). Lint lists posting skills from `config.skills` that the resume leaves out; add the ones the user really has.
- Each bullet: action, what, result with a number where `profile.md` has one.
- At the top, a comment listing what changed from the base resume, so the user can review in seconds.

## note.md (cover note)

Only when the form has a cover letter field (it's in `apply.md`; for forms `JH form` couldn't read, put `<!-- use only if the form asks for a cover letter -->` on the first line). `JH packet` renders it to a PDF for file fields and pastes it into text fields. Under 150 words, three short paragraphs:

1. The role and one specific reason this company (from `job.md`: product, news, funding round, team).
2. The one or two facts from `profile.md` that best match the posting's top requirement, with numbers.
3. One line on what the user wants to do there. No sign-off flourish.

## referral.md (ask a warm path)

To the first person in `job.md`'s Warm paths (ranked: verified, same job function as the user, closest title), sent before the application. A LinkedIn note under 400 characters (shorter notes get more replies), or an email under 100 words if the user knows them:

- The shared school or employer, in the first line, stated exactly as their profile shows it.
- The role, by title and link, and one fact from `profile.md` that fits it.
- The ask: "Would you be comfortable referring me, or telling me who the hiring manager is?" Make it easy to say no.

Warm paths are keyword matches; tell the user to confirm the shared history on the person's profile before sending.

## email.md (outreach to the contact)

Skip it when `job.md` has a "Careers page only" section: the employer asked candidates not to send resumes or interest by email or social media, and ignoring that hurts the application. Write `referral.md` in that case only if the user personally knows the warm path.

To the likely hiring manager in `job.md` when they have an email (they own the role; a short note from a qualified candidate is easy for them to forward). When the hiring manager has no email, write them a LinkedIn connection note under 300 characters instead, and send `email.md` to the recruiter. One email per company. Subject under 8 words, specific ("AI platform engineer: built X at Y"). Body under 120 words:

- Line 1: why them: they're the recruiter or manager for this role, and one concrete thing about the company or team.
- Lines 2–3: the user's single most relevant result, with a link (project, GitHub, portfolio) if `profile.md` has one.
- Close with one small ask: "Would you be open to a 15-minute chat, or to passing my application to the hiring manager?"
- Mention that the user applied (or is applying) through the official form, so the email supplements the application, never replaces it.

One email per company; one follow-up after 5–7 days with no reply, under 60 words, adding one new fact; then stop. Address LinkedIn-only contacts with a connection note under 400 characters built the same way.

## Form answers (`draft` fields)

Write each with `JH answer ID FIELD "text"`; options are validated. Add `--remember` for questions that recur across companies with the same answer ("Do you have a non-compete?"), never for company-specific ones ("Why Anthropic?").

- **Why this company / why this role**: two to four sentences. One specific thing about the company (product, customers, news in `job.md`), then the user's most relevant result from `profile.md`.
- **Describe your experience with X**: the one or two `profile.md` facts that show X, with numbers, in under 100 words unless the field asks for more.
- **Short factual questions** ("years with Python", "preferred team"): answer from `profile.md`; when the profile doesn't say, leave it for the user rather than estimating.
- **Additional information** (optional): leave blank unless there's something the form gave no room for, such as a relevant project link.

## job.md additions

Append:

- **Why it fits**: two or three lines tying posting requirements to profile facts.
- **Gaps**: requirements the user doesn't clearly meet, and any fact you wanted but couldn't trace.
- **Red flags**: anything from step 3 (no salary where required, repost, vague duties, contact email unverified).
