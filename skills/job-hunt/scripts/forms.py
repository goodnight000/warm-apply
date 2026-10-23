"""Application forms: read the real questions from Greenhouse, Lever and Ashby, prefill what the
user's saved answers cover, and mark the rest as a draft for the agent or a decision for the user.

Used by jobhunt.py (`form`, `answer`, `packet`). Stdlib only.
"""

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (job-hunt skill)", "Accept": "application/json, text/html"}

# Field status: filled from saved answers, a draft for the agent to write, a decision only the user can make,
# or optional and left blank.
FILLED, DRAFT, USER, OPTIONAL = "filled", "draft", "user", "optional"


def _get(url, timeout=20, data=None, headers=None):
    if os.environ.get("JOBHUNT_OFFLINE"):  # tests: no network
        return None
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as e:
        return None


def _get_json(url, **kw):
    text = _get(url, **kw)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        return None


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# ---------- which form ----------

def detect(url):
    """ATS, board slug and job id from an apply or listing URL, or None."""
    if not url:
        return None
    u = urllib.parse.urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    q = urllib.parse.parse_qs(u.query)
    if "greenhouse.io" in host:
        if "embed" in parts and q.get("for") and q.get("token"):
            return ("greenhouse", q["for"][0], q["token"][0])
        if len(parts) >= 3 and parts[1] == "jobs":
            return ("greenhouse", parts[0], parts[2])
    if host == "jobs.lever.co" and len(parts) >= 2:
        return ("lever", parts[0], parts[1])
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return ("ashby", parts[0], parts[1])
    if q.get("gh_jid"):
        return ("greenhouse", None, q["gh_jid"][0])
    if q.get("ashby_jid"):
        return ("ashby", None, q["ashby_jid"][0])
    return None


def slug_candidates(company, domain=None):
    base = norm(company)
    words = base.split()
    out = []
    for c in ["".join(words), "-".join(words), words[0] if words else "",
              (domain or "").split(".")[0] if domain else ""]:
        if c and c not in out:
            out.append(c)
    return out


def resolve(company, title, domain=None, gh_job_id=None, ashby_job_id=None):
    """Find the company's own posting for a job seen on a board: (ats, slug, id, url) or None."""
    want = set(norm(title).split())

    def close(t):
        have = set(norm(t).split())
        return want and len(want & have) / len(want | have) >= 0.8

    for slug in slug_candidates(company, domain):
        if ashby_job_id:
            if fetch_ashby(slug, ashby_job_id):
                return ("ashby", slug, ashby_job_id, f"https://jobs.ashbyhq.com/{slug}/{ashby_job_id}/application")
            continue
        if gh_job_id:
            d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{gh_job_id}")
            if d and d.get("id"):
                return ("greenhouse", slug, str(d["id"]), d.get("absolute_url"))
            continue
        d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        for j in (d or {}).get("jobs", []) if isinstance(d, dict) else []:
            if close(j.get("title")):
                return ("greenhouse", slug, str(j["id"]), j.get("absolute_url"))
        d = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in d if isinstance(d, list) else []:
            if close(j.get("text")):
                return ("lever", slug, j["id"], j.get("applyUrl") or j.get("hostedUrl"))
        d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        for j in (d or {}).get("jobs", []) if isinstance(d, dict) else []:
            if close(j.get("title")):
                return ("ashby", slug, j.get("id") or j["jobUrl"].rstrip("/").split("/")[-1], j.get("applyUrl") or j.get("jobUrl"))
    return None


def find_board(company, domain=None):
    """The company's public job board: (ats, slug, [(title, location)]) or None. Free."""
    for slug in slug_candidates(company, domain):
        d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        if isinstance(d, dict) and d.get("jobs") is not None:
            return ("greenhouse", slug, [(j.get("title"), (j.get("location") or {}).get("name")) for j in d["jobs"]])
        d = _get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        if isinstance(d, list) and d:
            return ("lever", slug, [(j.get("text"), (j.get("categories") or {}).get("location")) for j in d])
        d = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        if isinstance(d, dict) and d.get("jobs") is not None:
            return ("ashby", slug, [(j.get("title"), j.get("location")) for j in d["jobs"]])
    return None


def posting(ats, slug, job_id, cache=None):
    """One live posting from the employer's own board (None if it's gone). Free."""
    if ats == "greenhouse":
        d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?pay_transparency=true")
        return d if isinstance(d, dict) and d.get("id") else None
    if ats == "lever":
        d = _get_json(f"https://api.lever.co/v0/postings/{slug}/{job_id}")
        return d if isinstance(d, dict) and d.get("id") else None
    if ats == "ashby":
        key = ("ashby", slug)
        board = (cache or {}).get(key)
        if board is None:
            board = _get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true") or {}
            if cache is not None:
                cache[key] = board
        return next((j for j in board.get("jobs") or [] if j.get("id") == job_id or (j.get("jobUrl") or "").endswith(job_id)), None)
    return None


def final_url(url, timeout=10):
    """Follow redirects (t.co, lnkd.in) to the real link. Free."""
    if os.environ.get("JOBHUNT_OFFLINE"):
        return url
    try:
        req = urllib.request.Request(url, headers=UA, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.geturl()
    except Exception:
        return url


def workday_jobs(url, search, limit=20):
    """Jobs on a Workday careers site via its own JSON API: [(title, location, posted_text, job_url, path)]. Free."""
    u = urllib.parse.urlparse(url)
    parts = [p for p in u.path.split("/") if p and not re.match(r"^[a-z]{2}-[A-Z]{2}$", p)]
    if not parts:
        return []
    tenant, site = u.netloc.split(".")[0], parts[0]
    body = json.dumps({"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": search}).encode()
    d = _get_json(f"https://{u.netloc}/wday/cxs/{tenant}/{site}/jobs", data=body, headers={"Content-Type": "application/json"}) or {}
    return [(j.get("title"), j.get("locationsText"), j.get("postedOn"), f"https://{u.netloc}/{site}{j.get('externalPath')}",
             f"https://{u.netloc}/wday/cxs/{tenant}/{site}{j.get('externalPath')}") for j in d.get("jobPostings") or []]


def workday_detail(api_url):
    d = _get_json(api_url) or {}
    return (d.get("jobPostingInfo") or {})


# ---------- read the form ----------

def field(key, label, ftype, required=False, options=None, section="question", description=None):
    return {"key": key, "label": re.sub(r"\s+", " ", html.unescape(label or "")).strip(), "type": ftype,
            "required": bool(required), "options": options or [], "section": section,
            "description": (description or "")[:600] or None, "answer": None, "status": None, "source": None}


GH_TYPES = {"input_text": "text", "textarea": "longtext", "input_file": "file", "multi_value_single_select": "select",
            "multi_value_multi_select": "multiselect", "input_hidden": "hidden"}


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def parse_greenhouse(d):
    out = []
    for q in d.get("questions") or []:
        f0 = (q.get("fields") or [{}])[0]
        ftype = GH_TYPES.get(f0.get("type"), "text")
        out.append(field(f0.get("name"), q.get("label"), ftype, q.get("required"),
                         [v.get("label") for v in f0.get("values") or []], description=strip_tags(q.get("description"))))
    for q in d.get("location_questions") or []:
        f0 = (q.get("fields") or [{}])[0]
        if f0.get("type") == "input_hidden" or re.search(r"latitude|longitude", q.get("label") or "", re.I):
            continue  # filled by the page when the location is picked
        out.append(field(f0.get("name"), q.get("label"), "location", q.get("required")))
    for block in d.get("compliance") or []:
        for q in block.get("questions") or []:
            f0 = (q.get("fields") or [{}])[0]
            out.append(field(f0.get("name"), q.get("label"), "select", q.get("required"),
                             [v.get("label") for v in f0.get("values") or []], section="self_id"))
    dq = d.get("demographic_questions") or {}
    for q in dq.get("questions") or []:
        out.append(field(f"demographic_{q.get('id')}", q.get("label"), "select", q.get("required"),
                         [a.get("label") for a in q.get("answer_options") or []], section="self_id"))
    return [f for f in out if f["type"] != "hidden"]


def fetch_greenhouse(slug, job_id):
    d = _get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?questions=true")
    return parse_greenhouse(d) if d and d.get("questions") is not None else None


ASHBY_TYPES = {"String": "text", "LongText": "longtext", "Email": "email", "Phone": "phone", "File": "file",
               "Location": "location", "Date": "date", "Boolean": "boolean", "ValueSelect": "select",
               "MultiValueSelect": "multiselect", "Number": "number", "SocialLink": "text"}
ASHBY_QUERY = ("query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) { "
               "jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) { "
               "id title applicationForm { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired } } } } "
               "surveyForms { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired } } } } } }")


def parse_ashby(d):
    jp = ((d or {}).get("data") or {}).get("jobPosting") or {}
    out = []
    forms = [("question", jp.get("applicationForm"))] + [("self_id", s) for s in jp.get("surveyForms") or []]
    for section, form in forms:
        for sec in (form or {}).get("sections") or []:
            for e in sec.get("fieldEntries") or []:
                f = e.get("field") or {}
                out.append(field(f.get("path"), f.get("title"), ASHBY_TYPES.get(f.get("type"), "text"), e.get("isRequired"),
                                 [v.get("label") for v in f.get("selectableValues") or []], section=section,
                                 description=f.get("descriptionPlain")))
    return out


def fetch_ashby(slug, job_id):
    body = json.dumps({"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
                       "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": job_id}}).encode()
    d = _get_json("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", data=body,
                  headers={"Content-Type": "application/json"})
    if d and d.get("errors") and "surveyForms" in json.dumps(d["errors"]):
        body = json.dumps({"operationName": "ApiJobPosting", "query": ASHBY_QUERY.replace(
            " surveyForms { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired } } } }", ""),
            "variables": {"organizationHostedJobsPageName": slug, "jobPostingId": job_id}}).encode()
        d = _get_json("https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting", data=body,
                      headers={"Content-Type": "application/json"})
    fields = parse_ashby(d)
    return fields or None


LEVER_STANDARD = {"resume": "file", "name": "text", "email": "email", "phone": "phone", "location": "location",
                  "org": "text", "comments": "longtext", "pronouns": "multiselect"}


def parse_lever(page):
    out = []
    for block in re.findall(r'<li class="application-question[^"]*"[\s\S]*?</li>', page):
        names = re.findall(r'name="([^"]+)"', block)
        name = next((n for n in names if not n.startswith("cards[")), None)
        if not name or name.startswith("eeo["):
            continue
        label_html = re.search(r'class="application-label[^"]*">([\s\S]*?)</div>', block)
        label = strip_tags(label_html.group(1) if label_html else name).replace("✱", "").strip()
        required = "✱" in block or 'required' in block
        ftype = LEVER_STANDARD.get(name, "text")
        if name.startswith("urls["):
            ftype = "text"
        out.append(field(name, label, ftype, required))
    cards = []
    for tag in re.findall(r"<input[^>]*\[baseTemplate\][^>]*>", page):
        cid = re.search(r'name="cards\[([0-9a-f-]+)\]\[baseTemplate\]"', tag)
        val = re.search(r'value="([^"]*)"', tag)
        if cid and val:
            cards.append((cid.group(1), val.group(1)))
    for cid, val in cards:
        try:
            card = json.loads(html.unescape(val))
        except json.JSONDecodeError:
            continue
        for i, f in enumerate(card.get("fields") or []):
            ftype = {"textarea": "longtext", "text": "text", "dropdown": "select", "multiple-choice": "select",
                     "multiple-select": "multiselect", "file-upload": "file"}.get(f.get("type"), "text")
            out.append(field(f"cards[{cid}][field{i}]", f.get("text"), ftype, f.get("required"),
                             [o.get("text") for o in f.get("options") or []], description=f.get("description")))
    for name in sorted(set(re.findall(r'name="(eeo\[[a-z]+\])"', page))):
        sel = re.search(rf'name="{re.escape(name)}"[\s\S]*?</select>', page)
        opts = [strip_tags(o) for o in re.findall(r"<option[^>]*>([\s\S]*?)</option>", sel.group(0))] if sel else []
        out.append(field(name, name[4:-1].capitalize(), "select", False, [o for o in opts if o and not o.lower().startswith("select")],
                         section="self_id"))
    return out


def fetch_lever(slug, job_id):
    page = _get(f"https://jobs.lever.co/{slug}/{job_id}/apply")
    return parse_lever(page) if page and "application-question" in page else None


def fetch(ats, slug, job_id):
    return {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}[ats](slug, job_id)


# ---------- prefill ----------

LEGAL = re.compile(r"arbitrat|certif|acknowledg|attest|i (have )?read|i agree|i understand|consent|terms|privacy (notice|policy)"
                   r"|ai policy|use of ai|artificial intelligence|background check|non-?compete", re.I)
PREFERENCE = re.compile(r"\b(do|would) you (generally )?(enjoy|prefer|like|want)|which .* (do you|would you) prefer"
                        r"|what (percentage|%) of (your )?time|your (preferred|ideal)\b(?! (first )?name)", re.I)
ONLY_USER = re.compile(r"(interviewed|applied|worked) (at|with|for) .*before|previously (applied|employed|worked)"
                       r"|related to|relative|conflict of interest|export control|citizenship|criminal|convicted"
                       r"|deadlines?|timeline considerations|other (offers|processes)", re.I)


def yes_no(v):
    return None if v is None else ("Yes" if v else "No")


def pick(options, answer):
    """Map an answer onto one of a field's options, or None."""
    if not options:
        return answer
    if answer is None:
        return None
    a = norm(str(answer))
    for o in options:
        if norm(o) == a:
            return o
    for o in options:
        if norm(o).startswith(a) or (a and a in norm(o)):
            return o
    return None


CITY_ALIASES = {"nyc": ["new york", "brooklyn", "manhattan", "queens", "bronx", "staten island", "nyc"],
                "new york": ["new york", "brooklyn", "manhattan", "queens", "bronx", "nyc"],
                "sf": ["san francisco"], "san francisco": ["san francisco"],
                "bay area": ["san francisco", "oakland", "berkeley", "palo alto", "san jose", "mountain view", "menlo park"],
                "la": ["los angeles", "santa monica"], "dc": ["washington", "arlington"]}


def lives_in(question, location):
    """For 'Are you based in NYC (or open to relocating)?': Yes if the user's location is that place, else None."""
    m = re.search(r"\b(?:based|located|live|living|reside|residing) (?:in|near) (?:the )?([A-Za-z .]+?)(?:\s+(?:area|metro))?(?:\s+or\b|\?|,|$)",
                  question or "", re.I)
    if not m or not location:
        return None
    place, loc = norm(m.group(1)), norm(location)
    names = CITY_ALIASES.get(place, [place])
    return "Yes" if any(n in loc for n in names) else None


DECLINE = re.compile(r"decline|don.?t wish|do not wish|prefer not|not to (say|answer|disclose)|don.?t want|do not want", re.I)


IDENTITY = set()


def rules(a, job):
    first, last = a.get("first_name") or "", a.get("last_name") or ""
    via = "LinkedIn" if "linkedin" in (job.get("sources") or []) else a.get("how_heard_default") or "Company website"
    out = [
        (r"^(legal )?first name|given name", first),
        (r"^(legal )?last name|family name|surname", last),
        (r"^(full |legal )?name\b(?!.*(prefer|pronoun))|^full name|^legal name", f"{first} {last}".strip()),
        (r"preferred (first )?name.*last name|what is your preferred first name and last", f"{a.get('preferred_name') or first} {last}".strip()),
        (r"preferred (first )?name|name you go by|what is your preferred", a.get("preferred_name") or first),
        (r"e-?mail", a.get("email")),
        (r"phone|mobile", a.get("phone")),
        (r"linkedin", a.get("linkedin")),
        (r"github", a.get("github") or a.get("website")),
        (r"portfolio|website|personal (site|url)|other (website|url|link)|dribbble|behance", a.get("website") or a.get("github")),
        (r"current(ly)? (located|location)|where are you (currently )?(located|based)|^location$|^current location|city.*(live|reside)", a.get("location")),
        (r"address from which you plan|where (will|would) you (be )?work", a.get("location")),
        (r"which country|country (of residence|where you|will you)|country.*(based|reside|live)", a.get("country")),
        (r"current (company|employer)|^company$|^organization$|most recent (company|employer)", a.get("current_company")),
        (r"current (job )?title|current role", a.get("current_title")),
        (r"sponsor", yes_no(a.get("needs_sponsorship"))),
        (r"authori[sz]ed to work|work authori[sz]ation|legally (authorized|eligible|able)|eligible to work|right to work", yes_no(a.get("work_authorized"))),
        (r"(based|located|live|living|reside|residing) (in|near)", lives_in(job.get("_question"), a.get("location"))
         or (yes_no(a.get("willing_to_relocate")) if re.search(r"relocat", job.get("_question") or "", re.I) else None)),
        (r"relocat", yes_no(a.get("willing_to_relocate"))),
        (r"(in[- ]person|on-?site|in (the|our|one of our) offices?|from (our|the) .*office|hybrid).*(days|week|time|%)|commute", yes_no(a.get("in_office_ok"))),
        (r"(earliest|when).*(start)|start date|notice period|availability to start", a.get("earliest_start")),
        (r"salary|compensation|pay (expectation|range|requirement)|desired pay", a.get("salary_expectation") or None),
        (r"years of (\w+ )?experience|how many years", a.get("years_experience")),
        (r"pronoun", a.get("pronouns") or None),
        (r"how did you (hear|find|learn)|where did you (hear|see|find)|referral source|^source$", via),
    ]
    IDENTITY.update(p for p, _ in out[:10])  # names, email, phone, links, portfolio
    return out


def identity_label(label):
    """Name, email, phone and link rules apply only to short field labels, never to questions like
    'Name a framework you've used' or 'Which email client do you prefer?'."""
    words = norm(label).split()
    if len(words) > 8 or re.match(r"^name (a|an|the|one|two|three|some|your|any|something)\b", norm(label)):
        return False
    return not label.strip().endswith("?") or len(words) <= 6


def prefill(fields, answers, job, files):
    a = answers
    memory = {norm(k): v for k, v in (a.get("custom") or {}).items()}
    self_id = a.get("self_id") or {}
    for f in fields:
        label, opts = f["label"], f["options"]
        if f["type"] == "file":
            if re.search(r"cover", label, re.I):
                f.update(answer=files.get("cover"), status=FILLED if files.get("cover") else DRAFT, source="cover letter PDF")
            elif re.search(r"resume|cv", label, re.I) or f["key"] in ("resume", "_systemfield_resume"):
                f.update(answer=files.get("resume"), status=FILLED if files.get("resume") else DRAFT, source="resume PDF")
            elif f["required"]:
                f.update(status=USER, source="file only you can provide")
            else:
                f.update(status=OPTIONAL, source="optional upload")
            continue
        if f["section"] == "self_id":
            want = next((v for k, v in self_id.items() if norm(k) in norm(label)), None)
            choice = pick(opts, want) if want else next((o for o in opts if DECLINE.search(o)), None)
            f.update(answer=choice, status=FILLED if choice else USER, source="voluntary self-identification (your saved choice)")
            continue
        if LEGAL.search(label):
            f.update(status=USER, source="legal acknowledgement or policy: read it and decide yourself")
            continue
        if norm(label) in memory:
            choice = pick(opts, memory[norm(label)])
            f.update(answer=choice, status=FILLED if choice is not None else USER, source="saved answer")
            continue
        if ONLY_USER.search(label) or PREFERENCE.search(label):
            f.update(status=USER, source="only you know this")
            continue
        for pattern, value in rules(a, {**job, "_question": label}):
            if pattern in IDENTITY and not identity_label(label):
                continue
            if re.search(pattern, label, re.I):
                if value in (None, ""):
                    if f["required"]:
                        f.update(status=USER, source="add this to answers.json to fill it automatically next time")
                    else:
                        f.update(status=OPTIONAL, source="optional; left blank")
                else:
                    value = str(value)
                    choice = pick(opts, value) if f["type"] in ("select", "multiselect", "boolean") and opts else value
                    if f["type"] == "boolean":
                        choice = value if value in ("Yes", "No") else None
                    if f["type"] == "date" and not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                        f.update(status=USER, source=f"pick a date (your saved answer: {value})")
                    else:
                        f.update(answer=choice, status=FILLED if choice is not None else USER, source="answers.json")
                break
        else:
            if not f["required"]:
                f.update(status=OPTIONAL, source="optional; left blank unless it helps")
            elif f["type"] in ("longtext", "text"):
                f.update(status=DRAFT, source="draft from profile.md")
            else:
                f.update(status=USER, source="choose an option")
    return fields


def summary(fields):
    req = [f for f in fields if f["required"]]
    return {"fields": len(fields), "required": len(req),
            "filled": sum(f["status"] == FILLED for f in fields),
            "to_draft": [f["label"][:80] for f in fields if f["status"] == DRAFT],
            "for_user": [f["label"][:80] for f in fields if f["status"] == USER],
            "required_unanswered": [f["label"][:80] for f in req if not f.get("answer") and f["status"] != USER]}


def render_apply_md(job, app):
    lines = [f"# Apply: {job['title']} at {job['company']}", "",
             f"Form: {app['ats']} · {app['apply_url']}", "",
             "Fields in form order. [you] = only you can answer or agree; [drafted: check] = written for you from your profile, read it; [optional] = left blank.", ""]
    for f in app["fields"]:
        tag = {"filled": "", "draft": " [draft]", "user": " [you]", "optional": " [optional]"}.get(f["status"], "")
        if f["status"] == "filled" and (f.get("source") or "").startswith("drafted"):
            tag = " [drafted: check]"
        req = " *" if f["required"] else ""
        ans = f.get("answer")
        if isinstance(ans, list):
            ans = "; ".join(map(str, ans))
        shown = (ans or f.get("source") or "").replace("\n", "\n  > ")
        lines.append(f"- **{f['label']}**{req}{tag}: {shown}")
        if f["options"] and f["status"] == "user":
            lines.append(f"  - options: {' / '.join(f['options'][:8])}")
    if app.get("warnings"):
        lines += ["", "## Before you submit"] + [f"- {w}" for w in app["warnings"]]
    return "\n".join(lines) + "\n"


# ---------- documents ----------

def md_to_html(md, title, letter=False):
    """Minimal Markdown (headings, bullets, bold, italics, links) to a print-ready, ATS-friendly page."""
    md = re.sub(r"<!--[\s\S]*?-->", "", md)
    out, in_list = [], False

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<!\*)\*(?!\s)(.+?)\*", r"<em>\1</em>", t)
        t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
        return t

    for line in md.splitlines():
        s = line.rstrip()
        if re.match(r"^\s*[-*] ", s):
            if not in_list:
                out.append("<ul>"); in_list = True
            item = re.sub(r"^\s*[-*] ", "", s)
            out.append(f"<li>{inline(item)}</li>")
            continue
        if in_list:
            out.append("</ul>"); in_list = False
        m = re.match(r"^(#{1,3}) (.*)", s)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{inline(m.group(2))}</h{n}>")
        elif s.strip():
            out.append(f"<p>{inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    css = ("@page{size:Letter;margin:0.55in 0.6in}body{font-family:Helvetica,Arial,sans-serif;font-size:10.5pt;"
           "line-height:1.32;color:#111}h1{font-size:18pt;margin:0 0 2pt}h2{font-size:11pt;text-transform:uppercase;"
           "letter-spacing:.06em;border-bottom:1px solid #999;margin:10pt 0 4pt;padding-bottom:1pt}h3{font-size:10.5pt;"
           "margin:6pt 0 2pt}p{margin:2pt 0}ul{margin:2pt 0 4pt 14pt;padding:0}li{margin:1pt 0}a{color:#111;text-decoration:none}")
    if letter:
        css += "body{font-size:11pt;line-height:1.45}h1{margin-bottom:4pt}p{margin:0 0 11pt}"
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>{''.join(out)}</body></html>"
