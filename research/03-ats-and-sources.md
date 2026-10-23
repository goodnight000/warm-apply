# 03 — ATS feeds, job sources, application mechanics

Researched 2026-10-21. **tested** = I called it with curl that day and the excerpts below are real responses. **docs** = confirmed in the vendor's own documentation. **[untested]/[u]** = neither.

## 1. Public ATS job-board feeds (no auth)

| ATS | List endpoint | Salary in feed? | Remote flag | Date field | Apply URL | Status |
|---|---|---|---|---|---|---|
| Greenhouse | `GET boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true&pay_transparency=true` | `pay_input_ranges[]` (`min_cents`, `max_cents`, `currency_type`), only if the employer fills it in | none; parse `location.name`/`offices` | `first_published`, `updated_at` | `absolute_url` | tested + docs |
| Lever | `GET api.lever.co/v0/postings/{slug}?mode=json` (EU: `api.eu.lever.co`) | `salaryRange{min,max,currency,interval}`, `salaryDescription`; optional | `workplaceType` (`on-site`/`remote`/`hybrid`/`unspecified`) | `createdAt` (epoch ms; undocumented) | `hostedUrl`, `applyUrl` | tested + docs |
| Ashby | `GET api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true` | `compensation.compensationTiers[].components[]` (`minValue`, `maxValue`, `currencyCode`, `interval`) plus summary strings | `isRemote`, `workplaceType` | `publishedAt` | `jobUrl`, `applyUrl` | tested + docs |
| SmartRecruiters | `GET api.smartrecruiters.com/v1/companies/{CompanyId}/postings?limit=100&offset=0` (filters: `q`, `country`, `region`, `city`, `department`) | none seen in list or detail | `location.remote`, `location.hybrid` | `releasedDate` | detail `/postings/{id}` → `postingUrl`, `applyUrl` | tested + docs |
| Workable | `GET apply.workable.com/api/v1/widget/accounts/{slug}` | none in widget feed | `telecommuting` (bool) | `published_on`, `created_at` | `url`, `application_url` | tested; no official doc found for the widget endpoint |
| Recruitee | `GET {slug}.recruitee.com/api/offers/` | `salary{min,max,period,currency}` (null in both boards tested) | `remote`, `hybrid`, `on_site` | `published_at`, `updated_at` | `careers_apply_url` | tested |
| Workday | `POST {tenant}.wd{N}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`; detail `GET .../wday/cxs/{tenant}/{site}{externalPath}` | no structured field; sometimes inside `jobDescription` HTML | none; `locationsText` / `additionalLocations` mention "Remote" | `postedOn` is relative text ("Posted Today", "Posted 30+ Days Ago"); detail has `startDate` | detail `externalUrl` | tested; undocumented internal API |
| BambooHR | `GET {slug}.bamboohr.com/careers/list`; detail `/careers/{id}/detail` | detail `compensation` string, e.g. `"$176,000 - $242,000"` | `isRemote`, `locationType` | detail `datePosted` | `jobOpeningShareUrl` | tested; undocumented |
| Rippling | `GET ats.rippling.com/api/v2/board/{slug}/jobs` (paged, 20 per page); detail `/jobs/{uuid}` | detail `payRangeDetails[]` (`rangeStart`, `rangeEnd`, `currency`, `frequency`) | `locations[].workplaceType` (`ON_SITE`/…) | detail `createdOn` | `url` | tested; undocumented |
| Gem | `GET api.gem.com/job_board/v0/{slug}/job_posts/` (Greenhouse-shaped) | none structured (Gem's own board has the range in `content` text) | `location_type` | `first_published_at`, `updated_at` | `absolute_url` | tested; undocumented |

Things I checked by hand:

- **Greenhouse:** docs list `pay_transparency` only for single jobs, but it also works on the list endpoint together with `content=true` (tested). `?questions=true` on a single job returns the full application form (custom questions, `compliance`/EEOC, `demographic_questions`), so answers can be drafted against the real schema.
- **Salary is per employer:** on Lever, `palantir` 0/304 postings had `salaryRange`, `zoox` 222/236, `outreach` 16/25.
- **Workday:** `limit` >20 returns 400. `total` was exactly 2000 for NVIDIA, which looks like a cap (a third-party repo, hiver-data/workday-jobs-scraper, says the same) [u]. The tenant, `wd{N}`, and site have to come from the careers URL.
- **Wrong slug:** 404 (Lever `{"ok":false,"error":"Document not found"}`), a BambooHR 302 to bamboohr.com, or SmartRecruiters `200` with `totalFound:0`. `lever/netflix` is a 404 because Netflix isn't on Lever.
- **Rate limits:** none published for GETs. Greenhouse: "authentication is not required for any GET endpoints". Lever limits only application POSTs (2/sec). Ashby sends `cache-control: public, max-age=60`. One poll per company per day should be safe (my inference).

```bash
# Greenhouse (anthropic: 678 jobs on 2026-10-21)
curl -s "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs?content=true&pay_transparency=true"
# {"jobs":[{"id":5456619008,"title":"3P Revenue Accounting & Reporting","updated_at":"2026-10-19T11:17:24-04:00",
#   "location":{"name":"San Francisco, CA | Seattle, WA"},"absolute_url":"https://job-boards.greenhouse.io/anthropic/jobs/5456619008",
#   "pay_input_ranges":[{"min_cents":19000000,"max_cents":23000000,"currency_type":"USD","title":"Annual Salary:"}], ...}],"meta":{"total":678}}

# Lever (zoox: 236 jobs)
curl -s "https://api.lever.co/v0/postings/zoox?mode=json"
# [{"text":"...","categories":{"commitment":"Full-time","location":"...","team":"..."},"workplaceType":"hybrid",
#   "salaryRange":{"min":123000,"max":168000,"currency":"USD","interval":"per-year-salary"},"hostedUrl":"https://jobs.lever.co/...","applyUrl":".../apply"}]

# Ashby (openai: 814 jobs; notion also 200)
curl -s "https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true"
# {"jobs":[{"title":"Technical Program Manager, Compute Infrastructure","location":"San Francisco","publishedAt":"2026-03-12T16:38:15.322+00:00",
#   "compensation":{"scrapeableCompensationSalarySummary":"$257K - $335K","compensationTiers":[{"components":[{"compensationType":"Salary","interval":"1 YEAR","currencyCode":"USD","minValue":257000,...}]}]},
#   "applyUrl":"https://jobs.ashbyhq.com/openai/8fb1.../application"}],"apiVersion":"1"}

# SmartRecruiters (ServiceNow: 712)
curl -s "https://api.smartrecruiters.com/v1/companies/ServiceNow/postings?limit=100"
# {"totalFound":712,"content":[{"id":"744000156912806","name":"Sr Staff Software Engineer ... - Remote","releasedDate":"2026-10-22T00:52:46.633Z",
#   "location":{"city":"San Diego","region":"CALIFORNIA","country":"us","remote":true,"hybrid":false}, ...}]}

# Workable (huggingface)
curl -s "https://apply.workable.com/api/v1/widget/accounts/huggingface"
# {"name":"Hugging Face","jobs":[{"title":"Open-Source Machine Learning Engineer - EMEA Remote","telecommuting":true,"published_on":"2026-05-29",
#   "application_url":"https://apply.workable.com/j/81B46579FE/apply", ...}]}

# Recruitee (bunq)
curl -s "https://bunq.recruitee.com/api/offers/"
# {"offers":[{"title":"Head of Product","remote":false,"hybrid":true,"published_at":"2026-10-15 09:31:36 UTC",
#   "salary":{"min":null,"max":null,"period":null,"currency":null},"careers_apply_url":"https://careers.bunq.com/o/head-of-product-6/c/new"}]}

# Workday (NVIDIA)
curl -s -X POST "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs" \
  -H 'Content-Type: application/json' -d '{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}'
# {"total":2000,"jobPostings":[{"title":"Senior Assembly Process Engineer","externalPath":"/job/Taiwan-Hsinchu/Senior-Assembly-Process-Engineer_JR2028277-1",
#   "locationsText":"2 Locations","postedOn":"Posted Today"}],"facets":[...]}

# BambooHR (bitly)
curl -s "https://bitly.bamboohr.com/careers/list"   # then /careers/8/detail
# {"meta":{"totalCount":1},"result":[{"id":"8","jobOpeningName":"Director, Website Strategy","isRemote":null,"locationType":"1"}]}
# detail: {"result":{"jobOpening":{"compensation":"$176,000 - $242,000","datePosted":"2026-09-18", ...}}}

# Rippling (rippling: 653)
curl -s "https://ats.rippling.com/api/v2/board/rippling/jobs?page=0"
# {"items":[{"id":"84d3...","name":"Business Operations Manager","locations":[{"city":"San Francisco","workplaceType":"ON_SITE"}]}],"totalItems":653,"totalPages":33}
# detail payRangeDetails: [{"location":"US","currency":"USD","frequency":"YEAR","rangeStart":185000.0,"rangeEnd":240000.0}]

# Gem (gem)
curl -s "https://api.gem.com/job_board/v0/gem/job_posts/"
# [{"absolute_url":"https://jobs.gem.com/gem/4965519002","title":"Software Engineer","location_type":"hybrid","employment_type":"full_time","updated_at":"2026-03-10T21:20:54.077Z", ...}]
```

Docs: [Greenhouse](https://docs.greenhouse.io/job-board.html) · [Lever](https://github.com/lever/postings-api/blob/master/README.md) · [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api) · [SmartRecruiters](https://developers.smartrecruiters.com/docs/endpoints). Workable, Recruitee, Workday, BambooHR, Rippling, and Gem have no public docs. Their endpoints are the ones each vendor's own careers page calls, and they can change without notice.

## 2. Finding a company's ATS and slug

Careers-URL patterns (each slug maps directly onto the endpoint in §1):

| Pattern seen on careers page / apply link | ATS → slug |
|---|---|
| `job-boards.greenhouse.io/{slug}`, `boards.greenhouse.io/{slug}`, `boards.greenhouse.io/embed/job_board?for={slug}`, `?gh_jid=` on a company domain | Greenhouse |
| `jobs.lever.co/{slug}` (`jobs.eu.lever.co` → EU API) | Lever |
| `jobs.ashbyhq.com/{slug}` | Ashby |
| `jobs.smartrecruiters.com/{CompanyId}` (case-sensitive, e.g. `BoschGroup`) | SmartRecruiters |
| `apply.workable.com/{slug}` | Workable |
| `{slug}.recruitee.com` | Recruitee |
| `{tenant}.wd{N}.myworkdayjobs.com/{site}` | Workday (need all three parts) |
| `{slug}.bamboohr.com/careers` | BambooHR |
| `ats.rippling.com/{slug}/jobs` | Rippling |
| `jobs.gem.com/{slug}` | Gem |

Detection: (1) fetch the careers page and regex for these hosts. That worked for `notion.com/careers` → `jobs.ashbyhq.com/notion` but found nothing on anthropic.com or openai.com, which are JS-rendered or block bots. (2) Otherwise, guess slugs from the name and domain, probe Greenhouse → Lever → Ashby → SmartRecruiters → Workable → Recruitee, and take the first non-empty result. Wrong slugs fail cleanly. (3) For Workday, the URL has to come from the user or a search hit.

Open-source company-per-ATS lists (GitHub API, 2026-10-21):

- **[Riley-Dorrington/job-board-aggregator](https://github.com/Riley-Dorrington/job-board-aggregator)** (173★, MIT, pushed 2026-10-21) is the best seed I found: `data/greenhouse_companies.json` 8,333 slugs, `lever_companies.json` 4,368, `ashby_companies.json` 3,161, plus BambooHR/iCIMS/Paylocity lists.
- [adgramigna/job-board-scraper](https://github.com/adgramigna/job-board-scraper) (46★, 2025-11) is a scraper with no published list. [noble-ronin/ats-job-apis](https://github.com/noble-ronin/ats-job-apis) (4★) is an endpoint cheatsheet whose `lever/netflix` example 404s.
- [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) (18,251★, updated daily): curated new-grad postings, not slugs. [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) (4,533★) scrapes LinkedIn and Indeed, which conflicts with §3.

## 3. Submitting applications

**You can't submit through an API without the employer's secret key, on any of these ATSs.**

- **Greenhouse** ([docs](https://docs.greenhouse.io/job-board.html)): "Only the application submission endpoint ( POST https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{id} ) requires Basic Auth." and "Any form posts should be proxied by your own servers." Confirmed: the key belongs to the employer.
- **Lever** ([README](https://github.com/lever/postings-api/blob/master/README.md)): `POST /v0/postings/SITE/POSTING-ID?key=APIKEY`. "To use the POST API, you need an API key, which a Super Admin of your account can generate." The limit is 2 application POSTs/sec, and Lever tells employers to "Implement spam mitigations such as captchas".
- **Ashby** ([applicationForm.submit](https://developers.ashbyhq.com/reference/applicationformsubmit)): "Requires the `candidatesWrite` permission". Auth is "your API key as the basic auth username" ([auth](https://developers.ashbyhq.com/docs/authentication)), and that key belongs to the employer.

**Hosted forms** are the only route a candidate has, and they push back on bots:

- **Greenhouse:** reCAPTCHA "to help keep robots and spammers from easily applying to jobs on your job board" ([support](https://support.greenhouse.io/hc/en-us/articles/115005448066-Invisible-reCAPTCHA)). Voluntary EEOC self-ID (race, gender, veteran, disability), switched on per job ([support](https://support.greenhouse.io/hc/en-us/articles/360031720371)). On Anthropic's form (via `?questions=true`), the 21 fields include "Why Anthropic?", visa sponsorship, the in-office %, relocation, an "AI Policy for Application" acknowledgement, and an arbitration agreement.
- **Ashby:** spam scoring "attempts to detect whether a real candidate is submitting the form or if it might be a bot" ([docs](https://docs.ashbyhq.com/job-board-application-spam-protection)).
- **Lever:** only name and email are required, plus any fields the employer adds.
- **Workday:** "Workday recommends selecting the Require Candidate Home Account check box" ([admin guide](https://doc.workday.com/admin-guide/en-us/human-capital-management/recruiting/career-sites/gtv1538650489786.html)), which means a separate account per employer.

**Terms on automation** (a research subagent pulled these quotes from the primary pages; I re-checked Indeed's myself):

- **MyGreenhouse User Agreement** (candidate accounts, eff. 2025-01-29, [link](https://my.greenhouse.io/users/agreement)): you may not "(z) use automated means, including spiders, robots, crawlers, or similar means or processes to access or use the Services."
- **Lever** ([terms](https://www.lever.co/legal/terms-of-service)) and **Ashby** ([terms](https://www.ashbyhq.com/resources/terms)): customer contracts with no candidate-side clause. In practice the anti-bot controls above are the barrier.
- **Workday site terms** ([link](https://www.workday.com/en-us/legal/site-terms.html), updated 2026-08-13) forbid "any data mining, robots or similar data gathering or extraction methods designed to scrape or extract data from our Sites". It's **unconfirmed** whether "Sites" covers customer `myworkdayjobs.com` boards. Treat cxs reads as a gray area and keep volume low.
- **LinkedIn User Agreement §8.2** ([link](https://www.linkedin.com/legal/user-agreement), eff. 2025-11-03) bans software "to scrape or copy the Services", and you may not "Use bots or other unauthorized automated methods to access the Services". A [help page](https://www.linkedin.com/help/linkedin/answer/a1341387) adds that extensions that "automate activity on LinkedIn's website" are not permitted. Easy Apply isn't named, but automating it falls under these clauses.
- **Indeed Terms** ([link](https://www.indeed.com/legal), updated 2026-09-29), re-fetched by me: "Use of any automation, scripting, or bots to automate the Indeed Apply process outside of Indeed's official vendors and tooling is prohibited." Indeed may limit "the number of daily job applications you can submit through Indeed." The subagent also quotes, and I did not re-check: "Use any automated system (bots, scrapers, spiders, AI or Agentic AI) to access, data-mine, or submit content to the Site ... without Indeed's express written permission".

**Implication for v1:** reading the Greenhouse, Lever, and Ashby GETs is sanctioned (documented public APIs). Submitting runs into employer-only keys, CAPTCHA or spam scoring, and per-employer accounts. LinkedIn and Indeed prohibit automation outright. Give the user the `applyUrl` plus drafted answers keyed to that form's question labels, and let them submit.

## 4. Other free sources

| Source | Access | Key? | Salary | Notes |
|---|---|---|---|---|
| HN "Who is hiring?" | Find the thread: `hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring`; comments: `hn.algolia.com/api/v1/search?tags=comment,story_{id}&hitsPerPage=1000` or Firebase `hacker-news.firebaseio.com/v0/item/{id}.json` → `kids` | No | Free text | tested: Oct 2026 thread `49922569` has 319 top-level comments. Posts use `Company \| Role \| Location \| REMOTE/ONSITE` by convention; parse them with an LLM. |
| YC Work at a Startup | No official public API. `workatastartup.com` returned 406 to curl; job pages need a YC login [untested beyond that] | — | — | [yc-oss hiring.json](https://yc-oss.github.io/api/companies/hiring.json) (community mirror, tested) lists 1,491 companies flagged `isHiring`. Feed them into ATS slug detection. Monid has `ycombinator /search_jobs`. |
| RemoteOK | `GET remoteok.com/api` (send a User-Agent) | No | `salary_min`/`salary_max` (19 of 99 non-zero) | tested. Terms are embedded in element `[0]`: "Please link back ... and mention Remote OK as a source ... If you do not we'll have to suspend API access." |
| We Work Remotely | RSS `weworkremotely.com/remote-jobs.rss` (also per-category feeds) | No | No | tested: 93 items (`title`, `region`, `country`, `skills`, `type`, `pubDate`). `/api` returned 403. |
| Remotive | `GET remotive.com/api/remote-jobs?search=` | No | `salary` free text | tested, but it returns only 10 jobs. "Jobs displayed are delayed by 24 hours"; "advise max. 4 times a day". Low value. |
| USAJOBS | `GET data.usajobs.gov/api/search` with headers `Host`, `User-Agent: {email}`, `Authorization-Key` | Yes, apply via form, emailed ([docs](https://developer.usajobs.gov/general/quick-start)) | Yes (federal pay is always posted) [untested] | Without a key: 403 (tested). "Maximum of 500 rows per page", "10,000 rows per query" ([rate limiting](https://developer.usajobs.gov/guides/rate-limiting)). |
| Adzuna | `GET api.adzuna.com/v1/api/jobs/{country}/search/{page}?app_id=&app_key=` | Yes, "free API key immediately, with no payment details" | `salary_min/max` [untested] | [ToS](https://developer.adzuna.com/docs/terms_of_service): "The free tier is intended for evaluating the API". Personal job search is permitted; commercial use needs a licence. The user has to register the key. |
| Jooble | `POST jooble.org/api/{key}` [untested] | Yes, via a request form ([about](https://jooble.org/api/about)) | Unknown | No key → Cloudflare challenge (tested). Built for republishing sites, a poor fit here. |

## 5. Pay transparency (US)

Flag a posting with no range if the job location (or a remote role hireable there) is in one of these jurisdictions and the employer is over the size threshold. Compiled by a research subagent; I re-checked Virginia's statute myself. **[s]** = based on a search excerpt of an official page that blocked fetching; **[u]** = unconfirmed.

| Where | In force | Employers covered | Source |
|---|---|---|---|
| Colorado | 2021-01-01 | 1+ | CDLE INFO #9A [s] |
| California | 2023-01-01; SB 642 amendment 2026 [u] | 15+ | Lab. Code §432.3 [s] |
| Washington | 2023-01-01 | 15+ | [RCW 49.58.110](https://app.leg.wa.gov/RCW/default.aspx?cite=49.58.110) |
| NYC / New York State | 2022-11-01 / 2023-09-17 [u] | 4+ | [NYC CCHR](https://www.nyc.gov/site/cchr/media/pay-transparency.page), [NY DOL](https://dol.ny.gov/pay-transparency) |
| Jersey City / Ithaca / Westchester | 2022 | 5+ / 4+ / 4+ | local ordinances [s] |
| Maryland / DC | 2024-10-01 / 2024-06-30 | all [s] | [MD FAQ](https://labor.maryland.gov/labor/wages/esswagerangefaq.shtml); D.C. Code §32-1453.01 |
| Hawaii | 2024-01-01 | 50+ (2026 bill to lower: [u]) | HRCC FAQ |
| Illinois | 2025-01-01 | 15+ | 820 ILCS 112/10 |
| Minnesota | 2025-01-01 [u] | 30+ in MN | [Minn. Stat. 181.173](https://www.revisor.mn.gov/statutes/cite/181.173) |
| New Jersey | 2025-06-01 | 10+ | [NJ DOL](https://www.nj.gov/labor/myworkrights/wages/pay-transparency/) |
| Vermont | 2025-07-01 | 5+ | 21 V.S.A. §495p |
| Massachusetts | 2025-10-29 | 25+ | M.G.L. c.149 §105F [s] |
| Cleveland / Columbus, OH | 2025-10-27 / enforced 2027-01-01 [s] | 15+ in city | city pages; Columbus Code ch. 2335 |
| Virginia | 2026-07-01 (statute cites 2026 Acts ch. 996/1063; date [s]) | no threshold | [Va. Code §40.1-28.7:12](https://law.lis.virginia.gov/vacode/title40.1/chapter3/section40.1-28.7:12/) |
| Maine | 2026-07-29 | 10+ | LD 54, MDOL release |
| Connecticut | 2026-10-01 | 1+ | PA 26-12 §2 |
| Delaware | ~2027-09 (2 yrs after enactment [u]) | 26+ | HB 105 (HS2) |

Disclosure only on request, so a missing range is normal here: Nevada (after an interview, NRS 613.133), Rhode Island (§28-6-22), Toledo (after a conditional offer) [s], Cincinnati [u]. Everywhere else, and below the size thresholds, a missing range is common and shouldn't count against a posting.

## 6. Recommendation

**Free core (direct, no Monid):**

1. **ATS feeds over a per-user target-company list:** Greenhouse, Lever, Ashby first, since they're documented, public, and carry structured salary when the employer fills it in. Then SmartRecruiters, Workable, Recruitee, Rippling, BambooHR, Gem. Workday only when the user supplies the careers URL. Seed the list from Riley-Dorrington's slug files and yc-oss `hiring.json`, and detect ATSs with the §2 flow. Poll daily, diff on job id, and flag new postings by `first_published`/`publishedAt`/`createdAt`.
2. **HN Who is hiring:** monthly thread plus daily new comments via Algolia.
3. **RemoteOK API and WWR RSS:** for users open to remote roles. Show attribution links.
4. **USAJOBS:** only for users who want federal jobs. The user registers the key.

**Where a paid aggregator through Monid adds coverage:** ATSs with no public feed or that can't be detected (iCIMS, Taleo, SuccessFactors, custom career sites), non-tech and staffing roles, and finding companies the user doesn't already know. From `monid-catalog-sample.jsonl` (assumed USD per call): `dataforseo /serp/google-jobs` $0.0012, `builtin /search_jobs` $0.01, `ycombinator /search_jobs` $0.01, `wellfound /search_jobs` $0.03, `indeed /search_jobs` $0.10. Google Jobs works best as a discovery layer: it aggregates employer and board postings (my understanding is that it reads `JobPosting` structured data [untested here]). Resolve each hit back to the employer's ATS feed for the full description, salary, and apply URL. Monid's Indeed endpoint presumably reads Indeed pages, so Indeed's anti-automation terms (§3) may still apply when the data comes through a third party. Consider that a risk, not a cleared path.
