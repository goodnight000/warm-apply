#!/usr/bin/env python3
"""Job-hunt pipeline: search, filter, score and enrich job postings through Monid.

Every command prints JSON to stdout and exits 1 with {"error", "hint"} on failure.
State lives in one folder (JOB_HUNT_HOME, else ./job-hunt if present, else ~/job-search/job-hunt). Python 3.9+, stdlib only.
Paid calls go through the `monid` CLI (override with MONID_BIN); free ATS feeds use HTTPS directly.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import shlex
import shutil
import tempfile
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import forms  # noqa: E402

VERSION = "0.1.0"

# Conservative per-call estimates in USD, used only to stop before a run would exceed its budget.
# The ledger records what Monid actually charged.
PRICE_ESTIMATE = {
    ("anyapi", "/linkedin.jobs"): 0.03,
    ("apify", "/harvestapi/linkedin-job-search"): 0.03,
    ("dataforseo", "/serp/google-jobs"): 0.005,
    ("dataforseo", "/serp/google-organic"): 0.02,  # billed $0.02 per query (2026-10-22)
    ("anyapi", "/glassdoor.jobs"): 0.055,  # billed per result (~$0.0052 x 10)
    ("anyapi", "/twitter.search"): 0.003,
    ("fundable", "/company"): 0.06,
    ("fundable", "/company/search"): 0.01,
    ("context.dev", "/news/search"): 0.003,
    ("fundable", "/industry/search"): 0.0,
    ("fundable", "/location/search"): 0.0,
    ("apollo", "/mixed_people/api_search"): 0.0,
    ("apollo", "/people/match"): 0.026,
    ("ploid", "/linkedin/search"): 0.01,
    ("anyapi", "/email_finding.quickenrich"): 0.0072,
    ("hunterio", "/email-finder"): 0.0245,
    ("contactout", "/v1/people/enrich/work-email"): 0.02,
    ("contactout", "/v1/people/search/work-email"): 0.06,  # $0.02 per profile returned; callers pass the real estimate
    ("anyapi", "/email.verify"): 0.0066,
    ("anyapi", "/linkedin.profile"): 0.0042,  # also verifies warm paths
    ("anyapi", "/github.user_repositories"): 0.0012,
}

LINKEDIN_SALARY_BANDS = [40, 60, 80, 100, 120, 140, 160, 180, 200]  # thousands, anyapi enum "Nk+"

# US places whose laws require a pay range in postings (as of 2026-10; small employers are often exempt),
# so a missing salary there is a mild red flag. Source: research/03-ats-and-sources.md.
PAY_TRANSPARENCY = ["california", ", ca", "new york", ", ny", "washington", ", wa", "colorado", ", co",
                    "illinois", ", il", "maryland", ", md", "hawaii", "minnesota", ", mn", "vermont", "massachusetts",
                    ", ma", "new jersey", ", nj", "district of columbia", "washington, dc", "virginia", ", va",
                    "maine", "connecticut", ", ct", "cleveland", "columbus", "san francisco", "bay area", "los angeles",
                    "seattle", "denver", "chicago", "boston", "new york city"]

AGENCY_PATTERN = re.compile(
    r"\b(staffing|recruit(ment|ing|ers)? (agency|firm|services|partners)|talent (partners|solutions|acquisition firm)"
    r"|executive search|search (and venture )?firm|headhunt|placement (agency|firm)|resourcing"
    r"|recruitment (specialists?|consultan\w+|company)|(tech|technology|ai|talent) recruiters?\b|specialist recruit\w*)\b", re.I)
AGENCY_NAME_PATTERN = re.compile(r"\b(recruit\w*|staffing|talent|resourcing|search partners|consultancy group)\b", re.I)

# Signals from FTC job-scam reports (research/01-what-gets-interviews.md): fees, off-platform chat, crypto, "task" jobs.
SCAM_PATTERN = re.compile(
    r"\b((contact|message|text|reach|chat with|add|interview)( us| me| the recruiter| hr)? (via|on|through|over) (whats\s?app|telegram|signal)"
    r"|(whats\s?app|telegram) (number|id|handle|interview)|upfront (fee|payment)|(training|equipment|starter) (fee|kit purchase)|pay (a|the) fee"
    r"|paid in (crypto|bitcoin|usdt)|crypto(currency)? wallet|complete (simple )?tasks|task[- ]based (job|work)"
    r"|no interview (needed|required)|send (us )?your (bank|ssn|social security))\b", re.I)

# Postings that ask candidates not to reach out directly; outreach drafts are skipped for these.
NO_OUTREACH_PATTERN = re.compile(
    r"(do not|don't|won't|will not|cannot) (accept|consider|review|respond to) [^.]{0,40}"
    r"(resumes?|cvs?|applications?|expressions? of interest)[^.]{0,60}(e-?mail|social media|linkedin|messages?|dms?)"
    r"|please (do not|don't|refrain from) (contact|e-?mail|message|reach out to) (our )?(recruiters?|employees|team members|hiring managers?)",
    re.I)

NO_SPONSOR = re.compile(
    r"\b(unable|not able|cannot|can.t|won.t|will not|do(es)? not|are not able to)\s+(to\s+)?(currently\s+)?(provide|offer|support)?\s*"
    r"(visa\s+|immigration\s+|h-?1b\s+)?sponsor|\bno (visa |immigration )?sponsorship|without (the need for )?(current or future |now or future )?"
    r"(visa |employer |immigration )?sponsorship|sponsorship (is )?not (available|offered|provided)"
    r"|not (be )?eligible for (visa |immigration )?sponsorship|must not (now or in the future )?require (visa |immigration |employer )?sponsorship", re.I)
CITIZEN_ONLY = re.compile(r"\b(must be|only) (a )?(u\.?s\.?|united states) citizens?\b|\b(u\.?s\.?|united states) citizenship (is )?required"
                          r"|\b(u\.?s\.?|united states) citizens? only\b", re.I)
CLEARANCE = re.compile(r"\b(active|current)\s+(ts/sci|top secret|secret|security|dod)\s+clearance|\bclearance (is )?required"
                       r"|\bmust (hold|have|possess|obtain) (an? )?(active )?(security|ts/sci|secret) clearance|\bts/sci\b"
                       r"|\bimpact level [456]\b|\bil[- ]?[456]\b|\bpublic trust\b|eligib\w+ (for|to obtain) (a )?(security|dod) clearance", re.I)
YEARS = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs)\b[^.\n]{0,60}?\bexperience"
                   r"|experience[^.\n]{0,25}?\b(\d{1,2})\s*\+?\s*(?:years?|yrs)\b", re.I)
LEVELS = [("principal", 4, r"\b(principal|distinguished|fellow)\b"), ("director", 4, r"\b(director|vp|vice president|head of)\b"),
          ("staff", 3, r"\b(staff|lead|architect)\b"), ("senior", 2, r"\b(senior|sr\.?|iii)\b"),
          ("entry", 0, r"\b(junior|jr\.?|entry|new grad|graduate|associate|intern)\b")]
LEVEL_RANK = {"entry": 0, "mid": 1, "senior": 2, "staff": 3, "principal": 4, "director": 4}

DOWN_HOURS = 24  # skip a provider for this long after an auth or server failure
STATUSES = ["new", "saved", "shortlisted", "drafted", "approved", "applied", "interviewing", "offer",
            "rejected", "ghosted", "skipped", "filtered"]

DEFAULT_CONFIG = {
    "roles": ["software engineer"],
    "locations": ["United States"],
    "workplace": ["remote", "hybrid", "onsite"],
    "salary_min": None,
    "posted_within": "week",
    "exclude_title_keywords": ["intern", "internship"],
    "exclude_companies": [],
    "target_companies": [],
    "dealbreaker_keywords": [],
    "skills": [],
    "level": None,
    "exclude_agencies": True,
    "daily_shortlist": 5,
    "warm_paths": [],
    "job_function": None,
    "contact_titles": ["technical recruiter", "recruiter", "talent acquisition"],
    "sources": {"linkedin": True, "google_jobs": True, "ats_boards": True, "ats_web": True, "hackernews": True,
                "x": False, "glassdoor": False},
    "budget": {"per_run_usd": 1.0, "per_day_usd": 1.5},
}


class Fail(Exception):
    def __init__(self, error, hint=""):
        super().__init__(error)
        self.error, self.hint = error, hint


# ---------- state ----------

def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def local_day(ts=None):
    """The user's calendar day (local time) for a UTC ISO timestamp, or today."""
    t = dt.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc) if ts else now()
    return t.astimezone().strftime("%Y-%m-%d")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise Fail(f"{path} is not valid JSON: {e}", "Fix or delete the file, then rerun.")


def write_json(path, data):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


class Home:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.config_path = self.root / "config.json"
        self.jobs_path = self.root / "jobs.json"
        self.ledger_path = self.root / "ledger.jsonl"
        self.providers_path = self.root / "providers.json"
        self.applications = self.root / "applications"
        self.digests = self.root / "digests"

    def require(self):
        if not self.config_path.exists():
            raise Fail(f"No job-hunt folder at {self.root}", "Run `jobhunt.py init` or set JOB_HUNT_HOME.")

    def config(self):
        self.require()
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(read_json(self.config_path, {}))
        cfg["budget"] = {**DEFAULT_CONFIG["budget"], **cfg.get("budget", {})}
        cfg["sources"] = {**DEFAULT_CONFIG["sources"], **cfg.get("sources", {})}
        return cfg

    def answers(self):
        return read_json(self.root / "answers.json", {})

    def state(self):
        return read_json(self.root / "state.json", {})

    def save_state(self, st):
        write_json(self.root / "state.json", st)

    def jobs(self):
        return read_json(self.jobs_path, {})

    def save_jobs(self, jobs):
        write_json(self.jobs_path, jobs)

    def ledger(self):
        if not self.ledger_path.exists():
            return []
        return [json.loads(line) for line in self.ledger_path.read_text().splitlines() if line.strip()]

    def spent_today(self):
        today = local_day()
        return sum(e.get("cost", 0) for e in self.ledger()
                   if e.get("ts") and local_day(e["ts"]) == today and e.get("purpose") != "setup")


# ---------- Monid ----------

def ensure_monid_key(bin_):
    """Unattended runs pass MONID_API_KEY; the CLI only reads its own keychain, so register the key once."""
    key = os.environ.get("MONID_API_KEY")
    if not key:
        return "cli"
    env = {**os.environ, "NO_COLOR": "1"}
    try:
        if subprocess.run([bin_, "whoami"], capture_output=True, text=True, timeout=60, env=env).returncode == 0:
            return "cli"
        subprocess.run([bin_, "keys", "add", "-k", key, "-l", "job-hunt"], capture_output=True, text=True, timeout=60, env=env)
        subprocess.run([bin_, "keys", "activate", "-l", "job-hunt"], capture_output=True, text=True, timeout=60, env=env)
    except FileNotFoundError:
        raise Fail("The monid CLI is not installed.", "npm install -g @monid-ai/cli")
    return "env"


def final_cost(bin_, run_id, env, tries=3):
    """The run's billed cost once Monid has recorded it, or None."""
    for i in range(tries if run_id else 0):
        time.sleep(1.5 * (i + 1))
        try:
            proc = subprocess.run([bin_, "runs", "get", "-r", run_id, "-j"], capture_output=True, text=True, timeout=60, env=env)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        value = ((parse_cli_json(proc.stdout) or {}).get("cost") or {}).get("value")
        if value is not None:
            return float(value)
    return None


class Monid:
    """Runs endpoints through the monid CLI under a per-run budget, logging every call."""

    def __init__(self, home, budget_usd, purpose=""):
        self.home = home
        self.bin = os.environ.get("MONID_BIN", "monid")
        ensure_monid_key(self.bin)
        cfg = home.config()
        self.per_day = cfg["budget"]["per_day_usd"]
        day_left = self.per_day - home.spent_today()
        self.remaining = budget_usd if purpose == "setup" else max(0.0, min(budget_usd, day_left))
        self.in_flight = 0.0
        self.skipped = []
        self.spent = 0.0
        self.calls = []
        self.purpose = purpose
        self.health = read_json(home.providers_path, {})
        self.lock = threading.Lock()

    def is_down(self, provider):
        h = self.health.get(provider)
        if not h or not h.get("down_since"):
            return False
        since = dt.datetime.strptime(h["down_since"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return now() - since < dt.timedelta(hours=DOWN_HOURS)

    def run(self, provider, endpoint, body=None, query=None, path=None, step="", estimate=None):
        if self.is_down(provider):
            return {"ok": False, "skipped": "provider marked down", "provider": provider, "endpoint": endpoint}
        est = estimate if estimate is not None else PRICE_ESTIMATE.get((provider, endpoint), 0.05)
        with self.lock:
            # Another process (a scheduled run beside a manual one) may have spent since this one started.
            day_left = float("inf") if self.purpose == "setup" else self.per_day - self.home.spent_today() - self.in_flight
            if est > min(self.remaining, day_left) + 1e-9:
                self.skipped.append(step or endpoint)
                return {"ok": False, "skipped": "budget", "provider": provider, "endpoint": endpoint}
            self.remaining -= est  # reserved; reconciled in _record
            self.in_flight += est
        cmd = [self.bin, "run", "-p", provider, "-e", endpoint, "-w", "120", "-j"]
        if body is not None:
            cmd += ["-i", json.dumps(body)]
        if query is not None:
            cmd += ["--query", json.dumps(query)]
        if path is not None:
            cmd += ["--path", json.dumps(path)]
        env = {**os.environ, "NO_COLOR": "1"}
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
        except FileNotFoundError:
            raise Fail("The monid CLI is not installed.", "npm install -g @monid-ai/cli, then `monid keys add -k <key> -l main`.")
        except subprocess.TimeoutExpired:
            return self._record(provider, endpoint, step, ok=False, http=None, cost=0.0, error="timeout", reserved=est)
        record = parse_cli_json(proc.stdout)
        if record is not None and "status" not in record:  # an error body, not a run record: retry once
            raw = json.dumps(record)[:200]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
            record = parse_cli_json(proc.stdout)
            if record is None or "status" not in record:
                return self._record(provider, endpoint, step, ok=False, http=None, cost=0.0, error=f"no run record: {raw}", reserved=est)
        if record is None:
            err = (proc.stderr or proc.stdout).strip()[-300:]
            if re.search(r"api key|unauthori[sz]ed|401", err, re.I):
                raise Fail("Monid rejected the API key.", "Check `monid keys list`; create a key at https://app.monid.ai/access/api-keys.")
            return self._record(provider, endpoint, step, ok=False, http=None, cost=0.0, error=err or "no JSON from monid", reserved=est)
        status = record.get("status")
        if status in ("RUNNING", "PENDING", "QUEUED") and record.get("runId"):
            try:
                again = subprocess.run([self.bin, "runs", "get", "-r", record["runId"], "-w", "60", "-j"],
                                       capture_output=True, text=True, timeout=90, env=env)
                record = parse_cli_json(again.stdout) or record
                status = record.get("status")
            except subprocess.TimeoutExpired:
                pass
        http = (record.get("providerResponse") or {}).get("httpStatus")
        reported = (record.get("cost") or {}).get("value")
        cost = float(reported or 0.0)
        if status == "BLOCKED":
            raise Fail("Monid blocked the run with a workspace control (budget or run cap).",
                       "Adjust or pause the control at https://app.monid.ai, then rerun.")
        ok = status == "COMPLETED" and http is not None and 200 <= int(http) < 300
        error = None
        if not ok:
            detail = (record.get("providerResponse") or {}).get("error")
            error = (json.dumps(detail) if detail else f"run status {status}, provider http {http}")[:300]
            if http in (401, 402, 403):
                with self.lock:
                    self.health[provider] = {"down_since": iso(now()), "last_error": f"{http} {error}", "endpoint": endpoint}
                    write_json(self.home.providers_path, self.health)
        elif provider in self.health:
            with self.lock:
                self.health.pop(provider, None)
                write_json(self.home.providers_path, self.health)
        # Monid fills in a run's cost a moment after it completes; ask again before falling back to the listed price.
        estimated = False
        if ok and reported is None:
            final = final_cost(self.bin, record.get("runId"), env)
            if final is None:
                cost, estimated = est, True
            else:
                cost = final
        return self._record(provider, endpoint, step, ok=ok, http=http, cost=cost, error=error,
                            output=record.get("output"), run_id=record.get("runId"), reserved=est, estimated=estimated)

    def _record(self, provider, endpoint, step, ok, http, cost, error=None, output=None, run_id=None, reserved=0.0, estimated=False):
        """estimated: Monid hadn't reported the cost, so the listed price is logged (`spend --reconcile` corrects it)."""
        entry = {"ts": iso(now()), "run_id": run_id, "provider": provider, "endpoint": endpoint, "purpose": self.purpose,
                 "step": step or self.purpose, "ok": ok, "http": http, "cost": round(cost, 6)}
        if estimated:
            entry["estimated"] = True
        if error:
            entry["error"] = error
        with self.lock:
            self.spent += cost
            self.remaining += reserved - cost
            self.in_flight -= reserved
            with self.home.ledger_path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
            self.calls.append(entry)
        return {"ok": ok, "http": http, "cost": cost, "error": error, "output": output,
                "provider": provider, "endpoint": endpoint}

    def summary(self):
        out = {"calls": len(self.calls), "spent_usd": round(self.spent, 4),
               "failed": [f"{c['provider']} {c['endpoint']}: {c.get('error')}" for c in self.calls if not c["ok"]]}
        if self.skipped:
            out["skipped_for_budget"] = self.skipped
            out["budget_note"] = ("Some calls were skipped to stay inside budget.per_run_usd / per_day_usd; "
                                  "results are incomplete. Raise the budget or rerun tomorrow.")
        return out


def parse_cli_json(text):
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            return None


def http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "job-hunt-skill/" + VERSION, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as e:
        return {"__error__": str(e)}


# ---------- normalization ----------

def norm_text(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def job_key(company, title, location=None):
    """One id per company + title: the same opening posted in several cities is one job."""
    key = lambda s: norm_text(s) or re.sub(r"\s+", " ", (s or "").strip().lower())  # 株式会社メルカリ has no a-z0-9
    raw = f"{key(company)}|{key(title)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:10]


# Hosts that carry other companies' pages: a link there says nothing about the employer's own domain.
HOSTING_DOMAINS = ("ashbyhq.com", "greenhouse.io", "lever.co", "workable.com", "ycombinator.com", "workatastartup.com",
                   "notion.site", "notion.so", "github.com", "github.io", "linkedin.com", "google.com", "forms.gle",
                   "typeform.com", "wellfound.com", "angel.co", "breezy.hr", "bamboohr.com", "recruitee.com",
                   "rippling.com", "myworkdayjobs.com", "smartrecruiters.com", "jobvite.com", "gem.com", "x.com",
                   "twitter.com", "medium.com", "substack.com", "airtable.com", "tally.so", "docs.google.com",
                   "apply.workable.com", "pinpointhq.com", "teamtailor.com", "dover.com", "jobs.gusto.com")


def employer_domain(urls):
    """The first link that is the employer's own site, or None (then research looks the company up by name)."""
    for u in urls or []:
        d = domain_of(u)
        if d and not any(d == h or d.endswith("." + h) for h in HOSTING_DOMAINS):
            return d
    return None


def domain_of(url):
    if not url:
        return None
    m = re.match(r"^(?:https?://)?(?:www\.)?([^/:?#]+)", url.strip().lower())
    return m.group(1) if m else None


def parse_salary_text(text):
    """'$173K–250K a year' / '350,000 - 450,000 USD' / '70 - 100 USD' -> (min, max, period)."""
    if not text:
        return None, None, None
    t = text.lower().replace(",", "")
    nums = [float(n) * (1000 if k == "k" else 1) for n, k in re.findall(r"(\d+(?:\.\d+)?)\s*(k?)", t)]
    if not nums:
        return None, None, None
    lo, hi = nums[0], nums[1] if len(nums) > 1 else nums[0]
    period = "hour" if ("hour" in t or hi < 500) else "year"
    return lo, hi, period


def blank_job(**kw):
    job = {
        "id": None, "title": None, "company": None, "company_domain": None, "company_linkedin": None,
        "company_size": None, "company_about": None, "location": None, "workplace": None,
        "salary_min": None, "salary_max": None, "salary_period": None, "salary_text": None,
        "posted_at": None, "applicants": None, "easy_apply": None, "url": None, "apply_url": None,
        "ats": None, "description": None, "hiring_team": [], "sources": [], "company_rating": None,
    }
    job.update(kw)
    job["id"] = job_key(job["company"], job["title"], job["location"])
    return job


def from_anyapi_linkedin(item):
    c = item.get("company") or {}
    sal = item.get("salary") or {}
    created = item.get("createdUtc")
    wp = (item.get("workplaceType") or "").replace("on_site", "onsite") or None
    return blank_job(
        title=item.get("title"), company=c.get("name"), company_domain=domain_of(c.get("website")),
        company_linkedin=c.get("linkedinUrl"), company_size=to_int(c.get("employeeCount")),
        company_about=(c.get("description") or "")[:400], location=item.get("location") or item.get("city"),
        workplace=wp, salary_min=sal.get("min"), salary_max=sal.get("max"), salary_text=sal.get("text") or None,
        salary_period=parse_salary_text(sal.get("text"))[2] if sal.get("text") else None,
        posted_at=iso(dt.datetime.fromtimestamp(int(created), dt.timezone.utc)) if created else None,
        applicants=to_int(item.get("applicants")), easy_apply=bool(item.get("easyApplyUrl")) and not item.get("applyUrl"),
        url=item.get("url"), apply_url=item.get("applyUrl") or item.get("easyApplyUrl") or item.get("url"),
        ats=item.get("applicantTrackingSystem") or None, description=item.get("descriptionText"),
        sources=["linkedin"],
    )


def from_apify_linkedin(item):
    c = item.get("company") or {}
    sal = item.get("salary") or {}
    team = [{"name": h.get("name"), "title": h.get("position"), "linkedin": h.get("linkedinUrl")}
            for h in (item.get("hiringTeam") or [])]
    loc = item.get("location")
    if isinstance(loc, dict):
        loc = loc.get("linkedinText") or loc.get("parsed", {}).get("text")
    lo, hi, period = parse_salary_text(sal.get("text")) if isinstance(sal, dict) else (None, None, None)
    return blank_job(
        title=item.get("title"), company=c.get("name"), company_domain=domain_of(c.get("website")),
        company_linkedin=c.get("linkedinUrl"), company_size=to_int(c.get("employeeCount")),
        company_about=(c.get("description") or "")[:400], location=loc,
        workplace=(item.get("workplaceType") or "").replace("on-site", "onsite") or None,
        salary_min=lo, salary_max=hi, salary_period=period, salary_text=(sal.get("text") if isinstance(sal, dict) else None),
        posted_at=item.get("postedDate"), applicants=to_int(item.get("applicants")),
        easy_apply=bool(item.get("easyApplyUrl")), url=item.get("linkedinUrl"),
        apply_url=(item.get("applyMethod") or {}).get("companyApplyUrl") or item.get("easyApplyUrl") or item.get("linkedinUrl"),
        ats=item.get("applicantTrackingSystem") or None, description=item.get("descriptionText"),
        hiring_team=team, sources=["linkedin"],
    )


def from_google_jobs(item, fetched_at):
    lo, hi, period = parse_salary_text(item.get("salary"))
    return blank_job(
        title=item.get("title"), company=item.get("employer_name"), location=item.get("location"),
        salary_min=lo, salary_max=hi, salary_period=period, salary_text=item.get("salary"),
        posted_at=posted_from_ago(item.get("time_ago"), fetched_at) or item.get("timestamp"),
        url=item.get("source_url"), apply_url=item.get("source_url"),
        ats=(item.get("source_name") or "").replace("via ", "") or None, sources=["google_jobs"],
    )


def from_greenhouse(item, company, slug):
    loc = (item.get("location") or {}).get("name")
    pay = next((r for r in item.get("pay_input_ranges") or [] if r.get("currency_type") == "USD"), None)
    lo = pay["min_cents"] / 100 if pay and pay.get("min_cents") else None
    hi = pay["max_cents"] / 100 if pay and pay.get("max_cents") else None
    return blank_job(
        title=item.get("title"), company=company, location=loc,
        posted_at=item.get("first_published") or item.get("updated_at"),
        salary_min=lo, salary_max=hi, salary_period="year" if lo else None,
        salary_text=f"${lo:,.0f} - ${hi:,.0f}" if lo and hi else None,
        url=item.get("absolute_url"), apply_url=item.get("absolute_url"), ats="Greenhouse",
        description=strip_html(item.get("content")), sources=["greenhouse"],
    )


def from_lever(item, company):
    cats = item.get("categories") or {}
    sr = item.get("salaryRange") or {}
    created = item.get("createdAt")
    return blank_job(
        title=item.get("text"), company=company, location=cats.get("location"),
        workplace=(item.get("workplaceType") or "").replace("on-site", "onsite") or None,
        salary_min=sr.get("min"), salary_max=sr.get("max"),
        salary_period={"per-year-salary": "year", "per-hour-wage": "hour"}.get(sr.get("interval"), None),
        posted_at=iso(dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc)) if created else None,
        url=item.get("hostedUrl"), apply_url=item.get("applyUrl") or item.get("hostedUrl"), ats="Lever",
        description=item.get("descriptionPlain"), sources=["lever"],
    )


def from_ashby(item, company):
    comp = item.get("compensation") or {}
    lo = hi = None
    components = [c for t in comp.get("compensationTiers") or [] for c in t.get("components") or []]
    components += comp.get("summaryComponents") or []
    for c in components:
        if c.get("compensationType") == "Salary" and c.get("currencyCode", "USD") == "USD" and c.get("minValue"):
            lo, hi = c.get("minValue"), c.get("maxValue")
            break
    wp = {"hybrid": "hybrid", "onsite": "onsite", "remote": "remote"}.get(
        norm_text(item.get("workplaceType")).replace(" ", ""), "remote" if item.get("isRemote") else None)
    return blank_job(
        title=item.get("title"), company=company, location=item.get("location"), workplace=wp,
        salary_min=lo, salary_max=hi, salary_period="year" if lo else None,
        salary_text=comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"), posted_at=item.get("publishedAt"),
        url=item.get("jobUrl"), apply_url=item.get("applyUrl") or item.get("jobUrl"), ats="Ashby",
        description=item.get("descriptionPlain"), sources=["ashby"],
    )


def to_int(v):
    try:
        return int(str(v).replace(",", "").replace("+", ""))
    except (TypeError, ValueError):
        return None


def strip_html(s):
    if not s:
        return None
    import html
    s = html.unescape(s)
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def posted_from_ago(ago, fetched_at):
    if not ago:
        return None
    m = re.match(r"(\d+)\s+(minute|hour|day|week|month)", ago)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    delta = {"minute": dt.timedelta(minutes=n), "hour": dt.timedelta(hours=n), "day": dt.timedelta(days=n),
             "week": dt.timedelta(weeks=n), "month": dt.timedelta(days=30 * n)}[unit]
    return iso(fetched_at - delta)


def merge(existing, new):
    """Keep the richer value for each field and union the sources."""
    for k, v in new.items():
        if k == "sources":
            existing["sources"] = sorted(set(existing.get("sources", [])) | set(v))
        elif k == "location" and v and existing.get("location") and norm_text(v) not in norm_text(existing["location"]):
            existing["location"] = f"{existing['location']}; {v}"
        elif k == "hiring_team":
            existing["hiring_team"] = existing.get("hiring_team") or v
        elif existing.get(k) in (None, "", []) and v not in (None, "", []):
            existing[k] = v
    return existing


# ---------- filters and score ----------

CLIENT_PATTERN = re.compile(r"\b(my|our) client\b|\bon behalf of (my|our|a) client\b"
                            r"|\bwe are (working|partnering|partnered) with an? [^.]{0,60}(company|startup|lab|client|business)\b", re.I)


KNOWN_AGENCIES = {"cybercoders", "robert half", "teksystems", "insight global", "randstad", "kforce", "aerotek",
                  "adecco", "hays", "apex systems", "motion recruitment", "jobot", "experis", "kelly services",
                  "manpower", "beacon hill", "vaco", "collabera", "harnham"}
STAFFING_TERMS = re.compile(r"\b(c2c|corp[- ]to[- ]corp|w2 only|w2 contract|contract[- ]to[- ]hire|staff augmentation"
                            r"|implementation partner|end client|prime vendor|companies (apply|reach out) to you"
                            r"|get (matched|vetted) (with|by) (top )?companies|join our (talent )?(network|marketplace))\b", re.I)


GIG_PLATFORMS = {"alignerr", "toloka", "crossing hurdles", "outlier", "data annotation", "dataannotation", "remotasks",
                 "appen", "telus international", "telus digital", "micro1", "invisible technologies", "turing", "mercor",
                 "prolific", "handshake ai", "snorkel ai experts"}


def is_agency(job):
    return bool(AGENCY_PATTERN.search(job.get("company_about") or "") or
                AGENCY_NAME_PATTERN.search(job.get("company") or "") or
                CLIENT_PATTERN.search((job.get("description") or "")[:1500]) or
                norm_text(job.get("company")) in KNOWN_AGENCIES or
                STAFFING_TERMS.search((job.get("description") or "")[:3000]))


def annual(job, which):
    v = job.get(which)
    if v is None:
        return None
    return v * 2080 if job.get("salary_period") == "hour" else v


def years_required(text):
    """Largest 'N+ years ... experience' in a posting, ignoring numbers over 20."""
    found = [int(a or b) for a, b in YEARS.findall(text or "") if (a or b)]
    found = [n for n in found if n <= 20]
    return max(found) if found else None


def title_level(title):
    t = (title or "").lower()
    for name, rank, pattern in LEVELS:
        if re.search(pattern, t):
            return name, rank
    return None, None


def skills_in(text, skills):
    t = " " + norm_text(text) + " "
    return [k for k in skills if f" {norm_text(k)} " in t]


def knockout(job, cand):
    """Hard requirements the user fails, from the posting text."""
    text = job.get("description") or ""
    if cand.get("needs_sponsorship") and NO_SPONSOR.search(text):
        return "no visa sponsorship"
    if (cand.get("needs_sponsorship") or cand.get("us_citizen") is False) and CITIZEN_ONLY.search(text):
        return "US citizens only"
    if norm_text(str(cand.get("security_clearance") or "")) in ("", "none", "no", "n a", "na") and CLEARANCE.search(text):
        return "security clearance required"
    yrs, have = years_required(text), cand.get("years_experience")
    if yrs and isinstance(have, (int, float)) and yrs >= have + 4:
        return f"asks {yrs}+ years (you have {have})"
    return None


def filter_reason(job, cfg, cand=None):
    k = knockout(job, cand or {})
    if k:
        return k
    if not role_matches(job.get("title"), cfg["roles"]):
        return "title doesn't match your roles"
    if not place_matches(job.get("location"), cfg, job.get("workplace")):
        return f"{job.get('workplace') or 'location'} doesn't fit: {(job.get('location') or '')[:40]}"
    title = norm_text(job.get("title"))
    for kw in cfg["exclude_title_keywords"]:
        if re.search(rf"\b{re.escape(norm_text(kw))}\b", title):
            return f"title has '{kw}'"
    if norm_text(job.get("company")) in {norm_text(c) for c in cfg["exclude_companies"]}:
        return "excluded company"
    if cfg["exclude_agencies"] and is_agency(job):
        return "staffing agency"
    top = annual(job, "salary_max")
    if cfg.get("salary_min") and top is not None and top < cfg["salary_min"]:
        return f"pays up to {int(top):,}, below {int(cfg['salary_min']):,}"
    return None


def score(job, cfg, at, cand=None):
    cand = cand or {}
    s, why = 35, []
    desc = job.get("description") or ""
    if cfg.get("skills") and desc:
        hit = skills_in(desc, cfg["skills"])
        job["skills_matched"] = hit
        if hit:
            s += min(25, 5 * len(hit)); why.append(f"{len(hit)} of your skills: {', '.join(hit[:5])}")
        else:
            s -= 10; why.append("none of your listed skills appear")
    yrs, have = years_required(desc), cand.get("years_experience")
    if yrs and isinstance(have, (int, float)):
        job["years_required"] = yrs
        if yrs > have + 1:
            s -= 15; why.append(f"asks {yrs}+ years (you have {have})")
        elif yrs <= have:
            s += 3
    name, rank = title_level(job.get("title"))
    lv = cfg.get("level")
    mine = [LEVEL_RANK[l.lower()] for l in (lv if isinstance(lv, list) else [lv]) if l and l.lower() in LEVEL_RANK]
    if rank is not None and mine:
        # A range ("mid" and "senior") counts both as on-level.
        gap = rank - max(mine) if rank > max(mine) else (rank - min(mine) if rank < min(mine) else 0)
        if gap >= 2: s -= 20; why.append(f"{name}-level title, above your level")
        elif gap == 1: s -= 4; why.append(f"{name}-level title: a stretch")
        elif gap <= -2: s -= 12; why.append(f"{name}-level title, below your level")
    top = annual(job, "salary_max")
    if top is not None and cfg.get("salary_min") and top >= cfg["salary_min"]:
        s += 6; why.append("pays above floor" if top > cfg["salary_min"] else "top of range equals your floor")
    elif top is None:
        loc = " " + (job.get("location") or "").lower()
        if any(p in loc for p in PAY_TRANSPARENCY):
            s -= 10; why.append("no salary posted where the law requires one")
        else:
            s -= 4; why.append("no salary posted")
    if job.get("posted_at"):
        try:
            age = (at - dt.datetime.strptime(job["posted_at"][:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc)).days
            if age <= 1: s += 8; why.append("posted in the last day: apply early")
            elif age <= 3: s += 5; why.append("posted this week")
            elif age > 60:
                live = set(job.get("sources") or []) & {"greenhouse", "lever", "ashby"}
                s -= 8 if live else 20
                why.append(f"posted {age} days ago" + (": still listed on the company's board" if live else ": likely evergreen or stale"))
            elif age > 21: s -= 10; why.append(f"posted {age} days ago")
        except ValueError:
            pass
    a = job.get("applicants")
    if a is not None:
        if a <= 25: s += 6; why.append("few applicants so far")
        elif a < 60: s += 2
        elif a >= 200: s -= 8; why.append("200+ applicants")
    title = set(norm_text(job.get("title")).split())
    best = max((len(title & set(norm_text(r).split())) / max(1, len(norm_text(r).split())) for r in cfg["roles"]), default=0)
    if best >= 0.99: s += 10; why.append("title matches a target role")
    elif best >= 0.5: s += 4
    else: s -= 8; why.append("title is a loose match")
    text = f"{job.get('company_about') or ''} {(job.get('description') or '')[:3000]}"
    hits = [k for k in cfg.get("dealbreaker_keywords", []) if re.search(rf"\b{re.escape(k)}\b", text, re.I)]
    if hits:
        s -= 25; why.append(f"mentions dealbreaker: {', '.join(hits)}")
    if job.get("salary_period") == "hour":
        s -= 12; why.append("hourly pay: likely contract")
    if norm_text(job.get("company")) in GIG_PLATFORMS:
        s -= 25; why.append("AI-training or gig marketplace, usually contract work")
    scam = SCAM_PATTERN.search(job.get("description") or "")
    if scam:
        s -= 40; why.append(f"possible scam: '{scam.group(0)}'")
    targets = {norm_text(t["name"] if isinstance(t, dict) else t) for t in cfg["target_companies"]}
    if norm_text(job.get("company")) in targets:
        s += 8; why.append("on your target list")
    return max(0, min(100, s)), why


# ---------- sources ----------

def linkedin_band(salary_min):
    if not salary_min:
        return None
    k = salary_min / 1000
    bands = [b for b in LINKEDIN_SALARY_BANDS if b <= k]
    return f"{bands[-1]}k+" if bands else None


# DataForSEO location codes for Google searches; other countries go by name.
SERP_COUNTRY = {"united states": 2840, "united kingdom": 2826, "canada": 2124, "germany": 2276, "france": 2250,
                "india": 2356, "australia": 2036, "ireland": 2372, "netherlands": 2528, "singapore": 2702,
                "spain": 2724, "israel": 2376}


def user_country(cfg):
    c = norm_text(cfg.get("_country") or "united states")
    return {"usa": "united states", "us": "united states", "uk": "united kingdom"}.get(c, c)


def serp_location(cfg):
    c = user_country(cfg)
    return {"location_code": SERP_COUNTRY[c]} if c in SERP_COUNTRY else {"location_name": c.title()}


def search_linkedin(m, cfg):
    jobs, notes = [], []
    places = [l for l in cfg["locations"] if norm_text(l) != "remote"]
    combos = [(r, l, None) for r in cfg["roles"] for l in places]
    country = user_country(cfg)
    if "remote" in cfg["workplace"] and not any(norm_text(l) in ("united states", "us", "usa", country) for l in places):
        combos += [(r, country.title(), "remote") for r in cfg["roles"]]
    window = cfg.get("_window") or cfg["posted_within"]
    for role, loc, only in combos[:6]:
        body = {"query": role, "location": loc, "postedLimit": window if window in ("24h", "week", "month") else "week",
                "sortBy": "date"}
        band = linkedin_band(cfg.get("salary_min"))
        if band:
            body["salary"] = band
        if only or len(cfg["workplace"]) == 1:
            body["workplaceType"] = only or cfg["workplace"][0]
        if len(cfg.get("experience_levels") or []) == 1:
            body["experienceLevel"] = cfg["experience_levels"][0]
        r = m.run("anyapi", "/linkedin.jobs", body=body, step=f"search linkedin: {role} / {loc}")
        if r["ok"]:
            items = ((r["output"] or {}).get("data") or {}).get("items") or []
            jobs += [from_anyapi_linkedin(i) for i in items]
            continue
        notes.append(f"anyapi linkedin failed ({r.get('error') or r.get('skipped')}); trying apify")
        body2 = {"jobTitles": [role], "locations": [loc], "maxItems": 25,
                 "postedLimit": {"24h": "24h", "week": "week", "month": "month"}.get(window, "week")}
        r2 = m.run("apify", "/harvestapi/linkedin-job-search", body=body2, step=f"search linkedin fallback: {role} / {loc}")
        if r2["ok"]:
            out = r2["output"] if isinstance(r2["output"], list) else (r2["output"] or {}).get("items", [])
            jobs += [from_apify_linkedin(i) for i in out]
    return jobs, notes


def search_google_jobs(m, cfg):
    jobs, at = [], now()
    for role in cfg["roles"][:3]:
        for loc in cfg["locations"][:2]:
            kw = role if loc.lower() in ("united states", "remote", "usa", "us") else f"{role} {loc}"
            r = m.run("dataforseo", "/serp/google-jobs",
                      body={"keyword": kw, **serp_location(cfg), "language_code": "en", "depth": 20},
                      step=f"search google jobs: {kw}")
            if r["ok"]:
                out = r["output"] or []
                items = (out[0].get("items") if isinstance(out, list) and out else None) or []
                jobs += [from_google_jobs(i, at) for i in items if i.get("type", "google_jobs_item").startswith("google_jobs")]
    return jobs, []


GENERIC_TITLE_WORDS = {"engineer", "engineering", "developer", "scientist", "senior", "staff", "principal", "lead", "junior",
                       "sr", "jr", "ii", "iii", "i", "specialist", "associate", "manager", "the", "of", "and"}


TITLE_FAMILIES = [{"engineer", "engineering", "developer", "scientist", "swe"}, {"designer", "design"},
                  {"manager", "lead", "head", "director"}, {"analyst"}, {"scientist", "researcher"}]


TITLE_SYNONYMS = {"ml": ["machine", "learning"], "sr": ["senior"], "swe": ["software", "engineer"],
                  "eng": ["engineer"], "pm": ["product", "manager"], "ux": ["ux", "user", "experience"]}


TITLE_COMPOUNDS = [(r"\bfront end\b", "frontend"), (r"\bback end\b", "backend"), (r"\bfull stack\b", "fullstack"),
                   (r"\bdev ops\b", "devops"), (r"\bdev rel\b", "devrel")]


def title_words(s):
    words = set()
    t = norm_text(s)
    for pat, word in TITLE_COMPOUNDS:  # Front-End, Front End and Frontend are one word
        t = re.sub(pat, word, t)
    for w in t.split():
        if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "us", "ics")):
            w = w[:-1]  # designers -> designer, engineers -> engineer
        words.update(TITLE_SYNONYMS.get(w, [w]))
    return words


def role_matches(title, roles):
    """A title matches a role when it has the role's distinctive words ('machine learning' in ML engineer)
    and a word from the same job family (an engineer role needs engineer, developer or scientist)."""
    t = title_words(title)
    for r in roles:
        words = title_words(r)
        key = words - GENERIC_TITLE_WORDS or words
        families = [f for f in TITLE_FAMILIES if words & f]
        if key <= t and all(t & f for f in families):
            return True
    return False


PLACE_ALIASES = {"new york": ["nyc", "manhattan", "brooklyn", "queens", "bronx", "new york city"],
                 "san francisco": ["sf bay", "bay area", "south san francisco"],
                 "washington": ["washington dc", "district of columbia"], "los angeles": ["santa monica", "culver city"]}


NON_US = re.compile(r"\b(uk|united kingdom|england|london|ireland|europe|emea|eu|cet|cest|gmt|bst|germany|berlin|munich|france|paris|netherlands|amsterdam"
                    r"|spain|portugal|poland|canada|toronto|vancouver|india|bangalore|bengaluru|apac|asia|singapore|japan|australia"
                    r"|latam|latin america|mexico|brazil|argentina|colombia|israel|tel aviv|philippines)\b", re.I)


def wants_us(cfg):
    places = [norm_text(l) for l in cfg["locations"]]
    return not any(NON_US.search(p) for p in places)


def place_matches(location, cfg, workplace=None):
    """Remote jobs pass when the user accepts remote; others must be in one of the user's locations
    with an arrangement the user accepts ("New York, any arrangement, or remote anywhere")."""
    loc = norm_text(location)
    remote_ok = "remote" in cfg["workplace"]
    us_named = re.search(r"\b(us|usa|united states|americas|north america)\b", loc)
    abroad = NON_US.search(loc) and not us_named
    if workplace == "remote" or (not workplace and (loc.startswith("remote") or loc in ("anywhere", "work from home"))):
        if wants_us(cfg) and abroad:
            return False
        if not wants_us(cfg) and us_named and not NON_US.search(loc):  # "Remote (US only)" for a London user
            return False
        return remote_ok
    if not loc:
        return True
    has = lambda name: re.search(rf"\b{re.escape(name)}\b", loc)  # "LA" isn't in "Atlanta"
    for want in cfg["locations"]:
        w = norm_text(want.split(",")[0])  # "New York, NY" matches "New York City Metropolitan Area"
        if w == "remote":
            continue
        if w in ("united states", "us", "usa"):
            if abroad:
                continue
            return workplace is None or workplace in cfg["workplace"]
        if has(w) or any(has(a) for a in PLACE_ALIASES.get(w, [])):
            return workplace is None or workplace in cfg["workplace"]
    if wants_us(cfg) and abroad:
        return False
    return remote_ok and "remote" in loc


def search_ats(cfg):
    jobs, notes = [], []

    def relevant(title, location):
        return role_matches(title, cfg["roles"]) and place_matches(location, cfg)

    for t in cfg["target_companies"]:
        if not isinstance(t, dict) or not t.get("ats") or not t.get("slug"):
            continue
        name, ats, slug = t["name"], t["ats"].lower(), t["slug"]
        if ats == "greenhouse":
            d = http_get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true&pay_transparency=true")
            items = d.get("jobs", []) if isinstance(d, dict) else []
            jobs += [from_greenhouse(i, name, slug) for i in items if relevant(i.get("title"), (i.get("location") or {}).get("name"))]
        elif ats == "lever":
            d = http_get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
            items = d if isinstance(d, list) else []
            jobs += [from_lever(i, name) for i in items if relevant(i.get("text"), (i.get("categories") or {}).get("location"))]
        elif ats == "ashby":
            d = http_get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
            items = d.get("jobs", []) if isinstance(d, dict) else []
            jobs += [from_ashby(i, name) for i in items if relevant(i.get("title"), i.get("location"))]
        else:
            notes.append(f"{name}: unknown ats '{ats}'")
            continue
        if isinstance(d, dict) and d.get("__error__"):
            notes.append(f"{name} ({ats}/{slug}): {d['__error__']}")
        for j in jobs:
            if j["company"] == name and t.get("domain"):
                j["company_domain"] = t["domain"]
    return jobs, notes



# ---------- more sources ----------

ATS_SITES = [("ashby", "site:jobs.ashbyhq.com"), ("greenhouse", "site:greenhouse.io"), ("lever", "site:jobs.lever.co")]


def company_from_serp_title(title, ats, slug):
    t = title or ""
    if ats == "ashby" and " @ " in t:
        return t.rsplit(" @ ", 1)[1].strip()
    m = re.search(r"Job Application for .+? at (.+)$", t)
    if ats == "greenhouse" and m:
        return m.group(1).strip()
    if ats == "lever" and " - " in t:
        return t.split(" - ", 1)[0].strip()
    return slug.replace("-", " ").title()


def search_places(cfg):
    places = [l for l in cfg["locations"] if norm_text(l) not in ("remote", "united states", "us", "usa")][:2]
    if "remote" in cfg["workplace"]:
        places.append("Remote")
    return places or ["United States"]


def job_from_ats(target, title_hint, cache, source):
    """A live posting from Greenhouse, Lever or Ashby as a job, or None if it has closed."""
    ats, slug, jid = target
    p = forms.posting(ats, slug, jid, cache)
    if not p:
        return None
    company = p.get("company_name") or company_from_serp_title(title_hint, ats, slug)
    j = {"greenhouse": lambda: from_greenhouse(p, company, slug), "lever": lambda: from_lever(p, company),
         "ashby": lambda: from_ashby(p, company)}[ats]()
    j["sources"] = [source]
    if p.get("include_ai_disclaimer") and p.get("ai_disclaimer"):
        j["ai_screening"] = strip_html(p.get("ai_disclaimer"))[:600]
        j["ai_opt_out_url"] = p.get("ai_opt_out_request_url")
    return j


def search_ats_web(m, cfg):
    """Google, restricted to Greenhouse, Lever and Ashby job pages: startups' postings even when they never reach
    LinkedIn, each one a form `form` can read. Every hit is checked live against the board's own API (free)."""
    queries = [(ats, f'{site} "{role}" "{place}"') for role in cfg["roles"][:2] for place in search_places(cfg)
               for ats, site in ATS_SITES][:9]

    def one(q):
        ats, kw = q
        r = m.run("dataforseo", "/serp/google-organic", body={"keyword": kw, **serp_location(cfg), "language_code": "en", "depth": 20},
                  step=f"ats web: {kw[:60]}")
        out = (r["output"] or []) if r["ok"] else []
        items = (out[0].get("items") if isinstance(out, list) and out else None) or []
        return [(i.get("url"), i.get("title")) for i in items if i.get("type") == "organic"]

    hits = {}
    with ThreadPoolExecutor(6) as ex:
        for found in ex.map(one, queries):
            for url, title in found:
                d = forms.detect(url)
                if d and d[1]:
                    hits[d] = title
    cache = {}
    with ThreadPoolExecutor(8) as ex:
        jobs = [j for j in ex.map(lambda it: job_from_ats(it[0], it[1], cache, "ats_web"), hits.items()) if j]
    return jobs, ([f"ats web: {len(hits)} postings found, {len(jobs)} still live"] if hits else [])


def from_glassdoor(item):
    sal = item.get("salary") or {}
    if isinstance(sal, str):
        try:
            sal = json.loads(sal)
        except json.JSONDecodeError:
            sal = {}
    age = to_int(item.get("ageInDays"))
    return blank_job(
        title=item.get("title"), company=item.get("company") if isinstance(item.get("company"), str) else (item.get("company") or {}).get("name"),
        location=item.get("location"), salary_min=sal.get("min"), salary_max=sal.get("max"), salary_period="year" if sal.get("min") else None,
        salary_text=(f"{int(sal['min']):,} - {int(sal['max']):,} {sal.get('currency', 'USD')} (Glassdoor)" if sal.get("min") and sal.get("max") else None),
        posted_at=iso(now() - dt.timedelta(days=age)) if age is not None else None, url=item.get("applyUrl"),
        apply_url=item.get("applyUrl"), description=strip_html(item.get("description")), sources=["glassdoor"],
        company_rating=item.get("rating"), easy_apply=str(item.get("easyApply")).lower() == "true",
    )


def search_glassdoor(m, cfg):
    window = cfg.get("_window") or cfg["posted_within"]
    combos = [(r, p) for r in cfg["roles"][:1] for p in search_places(cfg)][:2]

    def one(c):
        role, place = c
        body = {"query": role, "location": "United States" if place == "Remote" else place, "sortBy": "date", "limit": 10,
                "postedLimit": window if window in ("24h", "week", "month") else "week"}
        if place == "Remote":
            body["workplaceType"] = "remote"
        r = m.run("anyapi", "/glassdoor.jobs", body=body, step=f"glassdoor: {role} / {place}")
        return [from_glassdoor(i) for i in (((r["output"] or {}).get("data") or {}).get("items") or [])] if r["ok"] else []

    with ThreadPoolExecutor(4) as ex:
        return [j for batch in ex.map(one, combos) for j in batch], []


HN_LINK = re.compile(r"https?://[^\s<\"')\]]+[^\s<\"')\].,;:!?]")


def search_hackernews(cfg):
    """This month's 'Ask HN: Who is hiring?' thread: top-level posts mentioning a role's distinctive words. Free."""
    d = forms._get_json("https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring&hitsPerPage=5") or {}
    thread = next((h for h in d.get("hits") or [] if re.search(r"who is hiring", h.get("title") or "", re.I)), None)
    if not thread:
        return [], []
    jobs, seen = [], set()
    for role in cfg["roles"][:3]:
        words = title_words(role) - GENERIC_TITLE_WORDS or title_words(role)
        q = urllib.parse.quote(" ".join(sorted(words)))
        res = forms._get_json(f"https://hn.algolia.com/api/v1/search?tags=comment,story_{thread['objectID']}&query={q}&hitsPerPage=100") or {}
        for h in res.get("hits") or []:
            if h.get("parent_id") != h.get("story_id") or h["objectID"] in seen:
                continue
            seen.add(h["objectID"])
            text = strip_html((h.get("comment_text") or "").replace("<p>", "\n"))
            head = text.split("\n", 1)[0]
            parts = [x.strip() for x in head.split("|") if x.strip()]
            if len(parts) < 2:
                continue
            title = next((x for x in parts[1:] if role_matches(x, cfg["roles"])), None)
            if not title:
                roles_in_text = [r for r in cfg["roles"] if role_matches(text, [r])]
                if not roles_in_text:
                    continue
                title = roles_in_text[0].title()
            loc = next((x for x in parts[1:] if re.search(r"remote|onsite|on-site|hybrid|\b[A-Z][a-z]+,? [A-Z]{2}\b|new york|san francisco|london|nyc|sf\b", x, re.I)), None)
            links = HN_LINK.findall(text)
            jobs.append(blank_job(
                title=title, company=re.sub(r"\s*\(.*?\)", "", parts[0])[:80], location=loc,
                workplace="remote" if loc and re.search(r"remote", loc, re.I) and not re.search(r"hybrid|onsite", loc, re.I) else None,
                posted_at=h.get("created_at"), description=text[:6000], url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                apply_url=links[0] if links else f"https://news.ycombinator.com/item?id={h['objectID']}",
                company_domain=employer_domain(links), sources=["hackernews"],
                hiring_team=[{"name": h.get("author"), "title": "posted on Hacker News", "linkedin": None}]))
    return jobs, [f"hacker news: {thread.get('title')}"]


X_HIRING = re.compile(r"\b(?:([A-Z][\w&.\- ]{1,40}?) is hiring|we(?:'re| are) hiring|join (?:us at |the team at )?@?(\w+))", re.I)


def search_x(m, cfg):
    """Hiring posts on X that link to a posting; the link is resolved and, when it's an ATS page, read like any job."""
    since = (now() - dt.timedelta(days=7)).strftime("%Y-%m-%d")
    jobs, notes, cache = [], [], {}
    for role in cfg["roles"][:2]:
        q = f'"{role}" (hiring OR "join us" OR "join our team") filter:links -filter:replies -filter:retweets lang:en since:{since}'
        r = m.run("anyapi", "/twitter.search", body={"query": q, "queryType": "Latest", "limit": 40, "requireSinglePage": True},
                  step=f"x: {role}")
        items = (((r["output"] or {}).get("data") or {}).get("items") or []) if r["ok"] else []
        items = [t for t in items if role_matches(t.get("text") or "", [role])]
        with ThreadPoolExecutor(8) as ex:
            resolved = list(ex.map(lambda t: [forms.final_url(u) for u in HN_LINK.findall(t.get("text") or "")[:2]], items))
        for t, links in zip(items, resolved):
            text = t.get("text") or ""
            ats_link = next((u for u in links if forms.detect(u) and forms.detect(u)[1]), None)
            poster = {"name": t.get("authorName"), "title": f"posted on X (@{t.get('authorUsername')})", "linkedin": None,
                      "x": f"https://x.com/{t.get('authorUsername')}"}
            board = re.search(r"jobs?|careers|hiring|talent|recruit", (t.get("authorUsername") or "") + " " + (t.get("authorName") or ""), re.I)
            if ats_link:
                j = job_from_ats(forms.detect(ats_link), "", cache, "x")
                if j:
                    j["hiring_team"] = [] if board else [poster]
                    j["x_post"] = t.get("url")
                    jobs.append(j)
                continue
            m_co = X_HIRING.search(text)
            company = (m_co.group(1) or m_co.group(2)) if m_co and (m_co.group(1) or m_co.group(2)) else t.get("authorName")
            if board or not company:
                continue  # job-board bots without a readable posting add noise, not leads
            created = to_int(t.get("createdUtc"))
            jobs.append(blank_job(
                title=role.title(), company=(company or "?").strip()[:60],
                posted_at=iso(dt.datetime.fromtimestamp(created, dt.timezone.utc)) if created else None,
                description=text, url=t.get("url"), apply_url=links[0] if links else t.get("url"), sources=["x"],
                hiring_team=[] if board else [poster]))
    return jobs, notes


def search_workday(cfg):
    """Target companies on Workday (`{"ats": "workday", "url": "https://acme.wd5.myworkdayjobs.com/External"}`). Free."""
    jobs = []
    for t in cfg["target_companies"]:
        if not isinstance(t, dict) or (t.get("ats") or "").lower() != "workday" or not t.get("url"):
            continue
        for role in cfg["roles"][:2]:
            for title, loc, posted, url, api in forms.workday_jobs(t["url"], role):
                if not role_matches(title, cfg["roles"]) or not place_matches(loc, cfg):
                    continue
                info = forms.workday_detail(api)
                days = 0 if re.search(r"today", posted or "", re.I) else 1 if re.search(r"yesterday", posted or "", re.I) else to_int(re.sub(r"\D", "", posted or "") or None)
                jobs.append(blank_job(title=title, company=t["name"], location=info.get("location") or loc,
                                      posted_at=iso(now() - dt.timedelta(days=days)) if days is not None else None,
                                      description=strip_html(info.get("jobDescription")), url=info.get("externalUrl") or url,
                                      apply_url=info.get("externalUrl") or url, ats="Workday", company_domain=t.get("domain"),
                                      sources=["workday"]))
    return jobs, []


# ---------- enrichment ----------

def enrich_company(m, job):
    out = {}
    domain = job.get("company_domain")
    if not domain:
        r = m.run("fundable", "/company/search", query={"name": job["company"]}, step=f"company domain: {job['company']}")
        if r["ok"]:
            data = (r["output"] or {}).get("data") or {}
            cands = data.get("companies") or data.get("results") or ([data["company"]] if data.get("company") else [])
            if cands:
                domain = cands[0].get("domain")
                job["company_domain"] = domain
    if not domain:
        return {"note": "company domain unknown; skipped company checks"}
    size = job.get("company_size")
    if size is None or size < 5000:
        r = m.run("fundable", "/company", query={"domain": domain}, step=f"funding: {job['company']}")
        if r["ok"]:
            c = ((r["output"] or {}).get("data") or {}).get("company") or {}
            deal = c.get("latest_deal") or {}
            out["funding"] = {
                "total_raised_usd": c.get("total_raised"), "rounds": c.get("num_funding_rounds"),
                "employees": c.get("num_employees"), "valuation_usd": c.get("latest_valuation_usd"),
                "latest_round": {"type": deal.get("type"), "date": (deal.get("date") or "")[:10],
                                 "amount_usd": deal.get("total_round_raised"),
                                 "summary": ((deal.get("description") or {}).get("short_description"))},
                "founded": (c.get("founded_date") or "")[:10] or None,
                "operating_status": c.get("operating_status"),
                "source": "Fundable via Monid; verify figures against a news source before quoting",
            }
    else:
        out["funding"] = {"note": f"{size:,} employees; funding check skipped for a large company"}
    r = m.run("context.dev", "/news/search",
              body={"searchBy": {"type": "entity", "entity": {"type": "domain", "domain": domain}}, "limit": 5},
              step=f"news: {job['company']}")
    if r["ok"]:
        arts = (r["output"] or {}).get("data") or []
        name = norm_text(job["company"])
        seen, news = set(), []
        for a in arts:
            title = a.get("title") or ""
            if norm_text(title) in seen or name not in norm_text(title + " " + (a.get("summary") or a.get("description") or "")):
                continue
            seen.add(norm_text(title))
            news.append({"date": (a.get("published_at") or a.get("date") or "")[:10], "title": title, "url": a.get("url")})
        out["news"] = news[:5]
    return out


def same_org(email_domain, company_domain):
    """acme.io and acme.com, or eng.acme.com and acme.com, are one organization."""
    a, b = domain_of(email_domain) or "", domain_of(company_domain) or ""
    if a == b or a.endswith("." + b) or b.endswith("." + a):
        return True
    label = lambda d: (d.split(".")[-3] if re.search(r"\.(co|com|org|net|ac)\.[a-z]{2}$", d) else d.split(".")[-2]) if "." in d else d
    return bool(a and b) and label(a) == label(b)


def title_rank(title, prefs):
    t = (title or "").lower()
    for i, p in enumerate(prefs):
        if p.lower() in t:
            return i
    return len(prefs)


def find_email(m, contact, job, domain):
    """Work email for a person at the job's company: cheapest source first; kept only on the company's domain."""
    if not contact.get("email") and domain and contact.get("name") and " " in contact["name"].strip():
        first, last = contact["name"].strip().split(" ", 1)
        r = m.run("anyapi", "/email_finding.quickenrich",
                  body={"companyDomain": domain, "firstName": first, "lastName": last.split(" ")[-1]},
                  step=f"email: {job['company']}")
        email = ((r["output"] or {}).get("data") or {}).get("email") if r["ok"] else None
        if not email and contact.get("linkedin"):
            # ContactOut's API is normally sales-gated (Team/API plan); through Monid it's a per-call lookup
            r = m.run("contactout", "/v1/people/enrich/work-email",
                      body={"linkedin_url": contact["linkedin"].replace("http://", "https://"), "include": ["work_email"]},
                      step=f"email (ContactOut): {job['company']}")
            prof = ((r["output"] or {}).get("profile") or {}) if r["ok"] else {}
            found = (prof.get("contact_info") or {}).get("work_emails") or prof.get("work_email") or []
            found = [found] if isinstance(found, str) else found
            email = next((e for e in found if same_org(e.split("@")[-1], domain)), None)
            if email:
                contact["email_source"] = "ContactOut"
        if not email:
            r = m.run("hunterio", "/email-finder", query={"domain": domain, "first_name": first, "last_name": last.split(" ")[-1]},
                      step=f"email fallback: {job['company']}")
            d = ((r["output"] or {}).get("data") or r["output"] or {}) if r["ok"] else {}
            email = d.get("email")
            if email and (d.get("verification") or {}).get("status") == "valid":
                contact["email_status"] = f"valid (Hunter, score {d.get('score')})"
        if email:
            contact["email"] = email
    if contact.get("email"):
        if not same_org(contact["email"].split("@")[-1], domain):
            contact["discarded_email"] = contact.pop("email")
            contact["email_status"] = "discarded: address is at a different company"
        elif not contact.get("email_status"):
            r = m.run("anyapi", "/email.verify", body={"email": contact["email"]}, step=f"verify email: {job['company']}")
            d = ((r["output"] or {}).get("data") or r["output"] or {}) if r["ok"] else {}
            verdict = d.get("status") or d.get("result") or d.get("verdict")
            contact["email_status"] = f"{verdict} (verifier)" if verdict else "unverified"
            if verdict in ("bad", "invalid", "undeliverable"):
                contact["discarded_email"] = contact.pop("email")
    return contact


FAMILY_EXTRA = {"engineer": {"engineering", "software", "technical", "technology", "platform", "infrastructure", "ai", "ml", "machine", "learning", "data"},
                "designer": {"design", "ux", "product"}, "manager": {"product"}, "analyst": {"analytics", "data"},
                "scientist": {"science", "research", "ai", "ml", "data"}}


def family_words(cfg):
    """Words that put a manager's title in the user's job family (an AI engineer's lead has an engineering-ish title)."""
    out = set()
    for r in cfg["roles"]:
        for w in title_words(r):
            for fam in TITLE_FAMILIES:
                if w in fam:
                    out |= fam
                    out |= FAMILY_EXTRA.get(w, set())
    return out - {"manager", "lead", "head", "director"}


RECRUITER = re.compile(r"recruit|talent|sourcer|people partner|hr business partner", re.I)
RECRUITER_TITLES = ["technical recruiter", "recruiter", "talent acquisition", "recruiting manager"]


def reveal_apollo(m, p, job):
    r = m.run("apollo", "/people/match", query={"id": p["id"]}, step=f"reveal contact: {job['company']}")
    if not r["ok"]:
        return None
    pp = (r["output"] or {}).get("person") or {}
    c = {"name": pp.get("name"), "title": pp.get("title"), "linkedin": pp.get("linkedin_url"), "city": pp.get("city"),
         "source": "Apollo"}
    if pp.get("email") and pp.get("email_status") == "verified":
        c["email"], c["email_status"] = pp["email"], "verified (Apollo)"
    return c


def find_contact(m, job, cfg):
    domain = job.get("company_domain")
    for h in job.get("hiring_team") or []:
        if h.get("name"):
            contact = {"name": h["name"], "title": h.get("title"), "linkedin": h.get("linkedin"), "source": "job post hiring team"}
            break
    else:
        contact = None
    if not contact and domain:
        titles = list(dict.fromkeys(cfg["contact_titles"] + RECRUITER_TITLES))
        r = m.run("apollo", "/mixed_people/api_search",
                  query={"person_titles[]": titles, "q_organization_domains_list[]": [domain], "per_page": 25},
                  step=f"people search: {job['company']}")
        people = (r["output"] or {}).get("people") or [] if r["ok"] else []
        city = norm_text((job.get("location") or "").split(",")[0])
        want, headcount = team_words(job, cfg), company_headcount(job)

        def local(p):
            return 0 if city and city in norm_text(json.dumps(p.get("city") or p.get("state") or "")) else 1

        managers = [p for p in people if title_words(p.get("title")) & MANAGER_WORDS and not RECRUITER.search(p.get("title") or "")]
        managers.sort(key=lambda p: (-len(title_words(p.get("title")) & want), local(p)))
        fam = family_words(cfg)
        if managers and (title_words(managers[0].get("title")) & want or
                         (headcount and headcount <= 300 and title_words(managers[0].get("title")) & fam)):
            hm = reveal_apollo(m, managers[0], job)
            if hm:
                match = title_words(hm.get("title")) & want
                hm["role"] = (f"likely hiring manager (title matches: {', '.join(sorted(match))})" if match
                              else f"team lead at a {headcount}-person company")
                job["hiring_manager"] = find_email(m, hm, job, domain)
                if job["hiring_manager"].get("email"):
                    return None  # the recruiter is a backup channel; skip the paid lookup
        recruiters = [p for p in people if RECRUITER.search(p.get("title") or "")]
        recruiters.sort(key=lambda p: (title_rank(p.get("title"), titles), local(p)))
        if recruiters:
            contact = reveal_apollo(m, recruiters[0], job)
    if not contact and job.get("company"):
        r = m.run("ploid", "/linkedin/search", body={"filters": {"keywords": f"{job['company']} recruiter"}, "limit": 10},
                  step=f"linkedin people: {job['company']}")
        items = ((r["output"] or {}).get("data") or {}).get("items") or [] if r["ok"] else []
        # Keyword search matches other companies with the same name ("CEO at NANGO"); keep only recruiters.
        items = [i for i in items if works_at(i.get("headline"), job["company"]) and RECRUITER.search(i.get("headline") or "")]
        items.sort(key=lambda i: title_rank(i.get("headline"), cfg["contact_titles"]))
        if items:
            i = items[0]
            contact = {"name": i.get("fullName"), "title": i.get("headline"), "linkedin": i.get("profileUrl"),
                       "city": i.get("location"), "source": "LinkedIn search"}
    if not contact:
        return None
    find_email(m, contact, job, domain)
    return contact


def works_at(headline, company):
    """True when a LinkedIn headline names the company as the current employer ('ML Engineer @ Acme | ...')."""
    if not norm_text(company):
        return False
    pieces = re.split(r"\s(@|at)\s", headline or "", flags=re.I)
    # pieces = [before, sep, after, sep, after, ...]; skip "Prev. @ X", "ex-X", "former ... at X"
    for i in range(2, len(pieces), 2):
        before = pieces[i - 2].lower()
        if re.search(r"\b(prev(ious(ly)?)?\.?|ex|former(ly)?|past|alum(ni|nus)?)\s*[-:]?\s*\S*$", before):
            continue
        current = re.split(r"[|(\u2014\u2013,·]| - ", pieces[i])[0]
        if org_words(current) == org_words(company):
            return True
    return False


ORG_SUFFIXES = {"inc", "llc", "ltd", "corp", "corporation", "co", "company", "ai", "hq", "labs", "technologies",
                "technology", "the", "io", "app", "group", "plc", "gmbh"}


def org_words(name):
    words = set(norm_text(name).split())
    return (words - ORG_SUFFIXES) or words


SCHOOL_GENERIC = {"university", "of", "the", "college", "school", "institute", "at", "and"}


def school_words(s):
    t = norm_text(s)
    t = re.sub(r"^uc ", "university of california ", t)
    return set(t.split())


def shares(profile, term):
    """The shared school or past employer appears in the person's education or experience."""
    want = school_words(term) - SCHOOL_GENERIC or school_words(term)
    for e in profile.get("education") or []:
        name = e.get("school") if isinstance(e.get("school"), str) else (e.get("school") or {}).get("name") or e.get("schoolName")
        if want <= school_words(name):
            return True
    for e in profile.get("experience") or []:
        if org_words(term) <= org_words(e.get("companyName")):
            return True
    return False


def verify_warm_path(m, person, job):
    r = m.run("anyapi", "/linkedin.profile", body={"url": person["linkedin"]}, step=f"verify warm path: {job['company']}")
    if not r["ok"]:
        return None
    prof = (r["output"] or {}).get("data") or {}
    current = prof.get("currentPosition") or [e for e in prof.get("experience") or [] if (e.get("endDate") or "").lower() == "present"]
    at_company = any(org_words(c.get("companyName")) == org_words(job["company"]) or
                     (job.get("company_linkedin") and (c.get("companyLinkedinUrl") or "").rstrip("/").lower() ==
                      job["company_linkedin"].rstrip("/").lower().replace("http://", "https://"))
                     for c in current)
    return at_company and shares(prof, person["shared"])


SCHOOLISH = re.compile(r"\b(university|college|institute|school|academy|polytechnic|uc|mit|caltech|stanford|harvard|"
                       r"berkeley|cmu|carnegie|nyu|ucla|usc|parsons|princeton|yale|columbia|cornell|georgia tech)\b", re.I)


def contactout_people(m, job, filters, n, step):
    """ContactOut people search scoped to the job's company (its API normally requires a sales meeting for a key).
    Returns a list of profiles, or None when the call didn't run."""
    body = {"page_size": n, "reveal_info": False, **filters}
    if job.get("company_domain"):
        body["domain"] = [job["company_domain"]]
    else:
        body["company"] = [job["company"]]
    r = m.run("contactout", "/v1/people/search/work-email", body=body, step=step, estimate=0.02 * n)
    if not r["ok"]:
        return None
    profs = (r["output"] or {}).get("profiles") or {}
    if isinstance(profs, dict):
        return [{**v, "linkedin": k} for k, v in profs.items()]
    return list(profs)


def role_overlap(title, cfg):
    t = title_words(title)
    return max((len(t & title_words(r)) for r in cfg["roles"]), default=0)


def find_warm_paths(m, job, cfg):
    """Current employees who share a school or past employer with the user, in the user's own function first
    (same-function referrals reach interview more often: Ashby, 37% vs 26%)."""
    out, fn = [], cfg.get("job_function")
    schools, companies = [], []
    for wp in cfg.get("warm_paths", [])[:4]:
        if isinstance(wp, dict):
            (schools if "school" in wp else companies).append(wp.get("school") or wp.get("company"))
        else:
            (schools if SCHOOLISH.search(wp) else companies).append(wp)
    # one search per kind, terms OR-ed: ContactOut bills per profile returned, so two small searches cover every path
    for field, terms in (("education", schools), ("past_company", companies)):
        if not terms:
            continue
        filt = {field: [" OR ".join(terms)]}
        people = None
        for with_fn in ([True, False] if fn else [False]):
            people = contactout_people(m, job, {**filt, **({"job_function": [fn]} if with_fn else {})}, 2,
                                       f"warm path (ContactOut): {job['company']} + {' / '.join(terms)}")
            if people is None or people:
                break
        if people is None:
            for t in terms:
                out += legacy_warm_paths(m, job, t)
            continue
        for p in people:
            blob = json.dumps(p.get("education") if field == "education" else p.get("experience") or "").lower()
            shared = next((t for t in terms if all(w in blob for w in (school_words(t) - SCHOOL_GENERIC or school_words(t)))), terms[0])
            out.append({"name": p.get("full_name"), "headline": p.get("title") or p.get("headline"), "linkedin": p.get("linkedin"),
                        "shared": shared, "verified": True, "source": "ContactOut",
                        "same_function": bool(fn) and fn.lower() in json.dumps(p.get("job_function") or "").lower(),
                        "note": "works there now and lists this school or employer on their profile"})
    seen, ranked = set(), []
    for w in sorted(out, key=lambda w: (not w.get("verified"), not w.get("same_function"), -role_overlap(w.get("headline"), cfg))):
        if w["name"] and w["name"] not in seen:
            seen.add(w["name"]); ranked.append(w)
    return ranked[:3]


MANAGER_WORDS = {"manager", "head", "director", "lead", "vp", "chief", "cto", "founder", "of"}


def team_words(job, cfg):
    """Distinctive words of the role and the team named in the posting title ('…, Cooperative AI (Evals)')."""
    title = job.get("title") or ""
    team = " ".join(re.findall(r"[,(–—-]\s*([^,()–—-]+)", title))
    words = title_words(team) | set().union(*[title_words(r) - GENERIC_TITLE_WORDS for r in cfg["roles"]] or [set()])
    return words - GENERIC_TITLE_WORDS - MANAGER_WORDS


def company_headcount(job):
    size = job.get("company_size")
    if isinstance(size, int):
        return size
    emp = ((job.get("company_signals") or {}).get("funding") or {}).get("employees")
    nums = [int(n) for n in re.findall(r"\d+", str(emp or ""))]
    return max(nums) if nums else None


def legacy_warm_paths(m, job, wp):
    """Fallback when ContactOut is unavailable: LinkedIn keyword search, each match checked against the profile."""
    found = []
    r = m.run("ploid", "/linkedin/search", body={"filters": {"keywords": f"{job['company']} {wp}"}, "limit": 10},
              step=f"warm path (LinkedIn search): {job['company']} + {wp}")
    items = ((r["output"] or {}).get("data") or {}).get("items") or [] if r["ok"] else []
    for i in items:
        if works_at(i.get("headline"), job["company"]) and i.get("fullName"):
            found.append({"name": i["fullName"], "headline": i.get("headline"), "linkedin": i.get("profileUrl"),
                          "shared": wp, "note": "keyword match; confirm the shared school or employer on their profile"})
    seen, out = set(), []
    for f in found:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        if f.get("linkedin"):
            ok = verify_warm_path(m, f, job)
            if ok is False:
                continue
            f["verified"] = bool(ok)
            f["note"] = ("checked: works there now and shares this school or employer" if ok
                         else "unverified keyword match; confirm on their profile")
        out.append(f)
        if len(out) == 3:
            break
    return [o for o in out if o.get("verified")] + [o for o in out if not o.get("verified")]


# ---------- commands ----------

def cmd_init(home, args):
    home.root.mkdir(parents=True, exist_ok=True)
    created = []
    if not home.config_path.exists():
        write_json(home.config_path, DEFAULT_CONFIG)
        created.append("config.json")
    for name, text in TEMPLATES.items():
        p = home.root / name
        if not p.exists():
            p.write_text(text)
            created.append(name)
    if not (home.root / "answers.json").exists():
        write_json(home.root / "answers.json", ANSWERS_TEMPLATE)
        created.append("answers.json")
    home.applications.mkdir(exist_ok=True)
    home.digests.mkdir(exist_ok=True)
    return {"home": str(home.root), "created": created,
            "next": "Fill profile.md, answers.json and config.json from the user's materials, then run `check`."}


def cmd_check(home, args):
    cfg = home.config()
    out = {"home": str(home.root), "config_ok": True, "roles": cfg["roles"], "locations": cfg["locations"]}
    profile = (home.root / "profile.md").read_text() if (home.root / "profile.md").exists() else ""
    out["profile_filled"] = len(profile.strip()) > 400 and "TODO" not in profile
    ans = home.answers()
    out["answers_missing"] = [k for k in ("first_name", "last_name", "email", "phone", "location", "years_experience")
                              if ans.get(k) in (None, "", "City, ST")]
    out["answers_recommended"] = [k for k in ("linkedin",) if not ans.get(k)]
    out["skills_listed"] = len(cfg.get("skills") or [])
    try:
        out["monid_key_source"] = ensure_monid_key(os.environ.get("MONID_BIN", "monid"))
        proc = subprocess.run([os.environ.get("MONID_BIN", "monid"), "balance", "-j"], capture_output=True, text=True,
                              timeout=60, env={**os.environ, "NO_COLOR": "1"})
        rec = parse_cli_json(proc.stdout) or {}
        bal = rec.get("balance") or rec.get("amount") or rec
        out["monid"] = {"ok": proc.returncode == 0, "balance": bal if proc.returncode == 0 else None,
                        "error": None if proc.returncode == 0 else (proc.stderr or proc.stdout).strip()[-200:]}
    except FileNotFoundError:
        out["monid"] = {"ok": False, "error": "monid CLI not installed (npm install -g @monid-ai/cli)"}
    except Fail as f:
        out["monid"] = {"ok": False, "error": f.error, "hint": f.hint}
    out["spent_today_usd"] = round(home.spent_today(), 4)
    out["providers_down"] = read_json(home.providers_path, {})
    steps = []
    if not (out.get("monid") or {}).get("ok"):
        steps.append("Connect Monid: sign up at https://monid.ai?fpr=charles ($1 free credit), create a key at https://app.monid.ai/access/api-keys, then `monid keys add -k KEY -l main`.")
    if not out["profile_filled"]:
        steps.append("Fill profile.md from the user's resume and projects (facts only).")
    if out["answers_missing"]:
        steps.append("Fill answers.json: " + ", ".join(out["answers_missing"]) + ".")
    if not cfg.get("skills"):
        steps.append("List 10-25 skills in config.json `skills`.")
    if not cfg.get("level"):
        steps.append("Set config.json `level` (entry, mid, senior, staff, principal).")
    if cfg["roles"] == DEFAULT_CONFIG["roles"]:
        steps.append("Set config.json `roles` to the titles the user wants.")
    if not find_chrome():
        steps.append("Install Chrome, Chromium or Edge so resumes render to PDF (otherwise HTML is produced).")
    out["ready"] = not steps
    out["next_steps"] = steps or ["Ready: run `jobhunt.py run`."]
    return out


def search_window(last, configured):
    """Only the newest postings once the pipeline is running daily; early applicants get seen first."""
    if not last:
        return configured
    days = (now() - dt.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).total_seconds() / 86400
    return "24h" if days <= 1.5 else ("week" if days <= 6.5 else configured)


def cmd_search(home, args):
    cfg = home.config()
    cand = home.answers()
    st = home.state()
    cfg["_window"] = args.window or search_window(st.get("last_search"), cfg["posted_within"])
    cfg["_country"] = cand.get("country")
    m = Monid(home, args.max_cost if args.max_cost is not None else cfg["budget"]["per_run_usd"], "search")
    runners = {"linkedin": lambda: search_linkedin(m, cfg), "google_jobs": lambda: search_google_jobs(m, cfg),
               "ats_boards": lambda: search_ats(cfg), "ats_web": lambda: search_ats_web(m, cfg),
               "glassdoor": lambda: search_glassdoor(m, cfg), "hackernews": lambda: search_hackernews(cfg),
               "x": lambda: search_x(m, cfg), "workday": lambda: search_workday(cfg)}
    enabled = [k for k in runners if cfg["sources"].get(k, k == "workday")]
    found, notes, per_source = [], [], {}

    def go(name):
        try:
            return name, runners[name]()
        except Exception as e:  # one broken source never sinks the run
            return name, ([], [f"{name} failed: {type(e).__name__}: {str(e)[:120]}"])

    with ThreadPoolExecutor(len(enabled) or 1) as ex:
        for name, (j, n) in ex.map(go, enabled):
            found += j; notes += n
    for j in found:
        for src in j.get("sources") or []:
            per_source[src] = per_source.get(src, 0) + 1
    jobs, at = home.jobs(), now()
    batch = {}
    for j in found:
        if not j.get("title") or not j.get("company"):
            continue
        batch[j["id"]] = merge(batch[j["id"]], j) if j["id"] in batch else j
    new, updated, filtered = [], 0, []
    for jid, j in batch.items():
        if jid in jobs:
            old = dict(jobs[jid])  # merge() updates in place
            merged = merge(jobs[jid], j)
            merged["last_seen"] = iso(at)
            jobs[jid] = merged
            updated += 1
            changed = (merged.get("location") != old.get("location")
                       or len(merged.get("description") or "") > len(old.get("description") or ""))
            if changed and old.get("status") in ("filtered", "new"):
                # Seen again in another city, or now with a description: judge it again.
                reason = filter_reason(merged, cfg, cand)
                if reason:
                    if old.get("status") == "new":
                        merged["status"], merged["filtered_reason"] = "filtered", reason
                        filtered.append({"company": merged["company"], "title": merged["title"], "reason": reason})
                else:
                    merged["score"], merged["score_reasons"] = score(merged, cfg, at, cand)
                    if old.get("status") == "filtered":
                        merged["status"] = "new"
                        merged.pop("filtered_reason", None)
                        new.append(merged)
            continue
        j["first_seen"] = j["last_seen"] = iso(at)
        reason = filter_reason(j, cfg, cand)
        if reason:
            j["status"], j["filtered_reason"] = "filtered", reason
            filtered.append({"company": j["company"], "title": j["title"], "reason": reason})
        else:
            j["status"] = "new"
            j["score"], j["score_reasons"] = score(j, cfg, at, cand)
            new.append(j)
        jobs[jid] = j
    home.save_jobs(jobs)
    complete = not m.skipped and not any(not c["ok"] for c in m.calls)
    if found and complete:  # a failed or budget-skipped search is retried over the same window next time
        st["last_search"] = iso(at)
        home.save_state(st)
    new.sort(key=lambda j: -j["score"])
    by_source = {}
    for j in new:
        for src in j.get("sources") or []:
            by_source[src] = by_source.get(src, 0) + 1
    return {"window": cfg["_window"], "found": len(found), "found_by_source": per_source, "new_by_source": by_source,
            "unique": len(batch), "new": len(new), "already_seen": updated,
            "filtered": len(filtered), "filtered_sample": filtered[:10],
            "top_new": [brief(j) for j in new[:args.top]], "monid": m.summary(), "notes": notes}


def brief(j):
    sal = j.get("salary_text") or (f"{int(j['salary_min']):,}–{int(j['salary_max']):,}" if j.get("salary_min") and j.get("salary_max") else None)
    return {"id": j["id"], "score": j.get("score"), "company": j["company"], "title": j["title"], "sources": j.get("sources"),
            "location": j.get("location"), "workplace": j.get("workplace"), "salary": sal,
            "applicants": j.get("applicants"), "posted": (j.get("posted_at") or "")[:10], "status": j.get("status"),
            "why": j.get("score_reasons"), "url": j.get("url")}


def cmd_list(home, args):
    jobs = list(home.jobs().values())
    if args.status:
        jobs = [j for j in jobs if j.get("status") in args.status.split(",")]
    if args.source:
        jobs = [j for j in jobs if set(args.source.split(",")) & set(j.get("sources") or [])]
    jobs.sort(key=lambda j: (-(j.get("score") or 0), j.get("first_seen") or ""))
    return {"count": len(jobs), "jobs": [brief(j) for j in jobs[:args.top]]}


def get_job(home, jid):
    jobs = home.jobs()
    matches = [k for k in jobs if k.startswith(jid)]
    if len(matches) != 1:
        raise Fail(f"No unique job matches '{jid}'", "Use an id from `list`.")
    return jobs, matches[0]


def cmd_show(home, args):
    jobs, jid = get_job(home, args.id)
    j = dict(jobs[jid])
    if args.brief and j.get("description"):
        j["description"] = j["description"][:1500]
    return j


COMPANY_LOCK = threading.Lock()


def enrich_job(home, m, j, cfg):
    before = sum(c["cost"] for c in m.calls)
    if j.get("company_domain") and not employer_domain([j["company_domain"]]):
        j["company_domain"] = None  # a job board's or host's domain; research finds the company by name
    no_dm = NO_OUTREACH_PATTERN.search(j.get("description") or "")
    j["outreach_ok"] = not no_dm
    if no_dm:
        j["outreach_note"] = f"Posting says: \"{no_dm.group(0)}\". Apply through the careers page; skip email.md."
    cache_path = home.root / "companies.json"
    with COMPANY_LOCK:
        cache = read_json(cache_path, {})
    key = j.get("company_domain") or norm_text(j.get("company"))
    hit = cache.get(key)
    if hit and (now() - dt.datetime.strptime(hit["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).days < 30:
        j["company_signals"] = {**hit["signals"], "cached": hit["at"][:10]}
    else:
        j["company_signals"] = enrich_company(m, j)
        if j["company_signals"].get("funding") or j["company_signals"].get("news"):
            with COMPANY_LOCK:
                cache = read_json(cache_path, {})
                cache[j.get("company_domain") or key] = {"at": iso(now()), "signals": j["company_signals"]}
                write_json(cache_path, cache)
    j["hiring_manager"] = None
    j["contact"] = find_contact(m, j, cfg)
    j["warm_paths"] = find_warm_paths(m, j, cfg)
    j["enriched_at"] = iso(now())
    j["enrich_cost_usd"] = round(sum(c["cost"] for c in m.calls) - before, 4)
    (app_folder(home, j) / "job.md").write_text(render_job_md(j))


def cmd_enrich(home, args):
    cfg = home.config()
    m = Monid(home, args.max_cost if args.max_cost is not None else cfg["budget"]["per_run_usd"], "enrich")
    jobs = home.jobs()
    results = []
    for raw in args.ids:
        _, jid = get_job(home, raw)
        j = jobs[jid]
        if j.get("enriched_at") and not args.refresh:
            results.append({"id": jid, "company": j["company"], "title": j["title"], "cached": j["enriched_at"],
                            "folder": str(app_folder(home, j))})
            continue
        enrich_job(home, m, j, cfg)
        folder = app_folder(home, j)
        results.append({"id": jid, "company": j["company"], "title": j["title"], "folder": str(folder),
                        "signals": j["company_signals"], "contact": j["contact"] if args.show_emails else mask_contact(j["contact"]),
                        "hiring_manager": j.get("hiring_manager") if args.show_emails else mask_contact(j.get("hiring_manager")),
                        "warm_paths": j["warm_paths"], "outreach_ok": j["outreach_ok"],
                        "cost_usd": j["enrich_cost_usd"]})
        home.save_jobs(jobs)
    return {"enriched": results, "monid": m.summary()}


def mask_contact(c):
    if not c:
        return c
    c = dict(c)
    for k in ("email", "discarded_email"):
        if c.get(k):
            user, _, dom = c[k].partition("@")
            c[k] = user[:2] + "***@" + dom
    return c


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40]


def app_folder(home, j):
    folder = home.applications / f"{slug(j['company'])}-{slug(j['title'])[:30]}-{j['id'][:6]}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def render_job_md(j):
    lines = [f"# {j['title']} at {j['company']}", ""]
    b = brief(j)
    lines += [f"- Location: {b['location'] or '?'} ({b['workplace'] or 'workplace not stated'})",
              f"- Salary: {b['salary'] or 'not posted'}",
              f"- Posted: {b['posted'] or '?'}; applicants: {b['applicants'] if b['applicants'] is not None else '?'}",
              f"- Apply: {j.get('apply_url') or j.get('url')}",
              f"- Listing: {j.get('url')}", f"- Score: {j.get('score')} ({'; '.join(j.get('score_reasons') or [])})", ""]
    if j.get("outreach_ok") is False:
        lines += ["## Careers page only", f"- {j['outreach_note']}", ""]
    sig = j.get("company_signals") or {}
    f = sig.get("funding") or {}
    if f.get("total_raised_usd") or f.get("latest_round", {}).get("type"):
        lr = f.get("latest_round") or {}
        lines += ["## Company", f"- Latest round: {lr.get('type')} on {lr.get('date')}" + (f" — {lr.get('summary')}" if lr.get("summary") else ""),
                  f"- Total raised (Fundable): {money(f.get('total_raised_usd'))}; employees: {f.get('employees')}",
                  f"- {f.get('source')}", ""]
    elif f.get("note"):
        lines += ["## Company", f"- {f['note']}", ""]
    if sig.get("news"):
        lines += ["## Recent news"] + [f"- {n['date']}: [{n['title']}]({n['url']})" for n in sig["news"]] + [""]
    c = j.get("contact")
    if c:
        lines += ["## Contact", f"- {c.get('name')}, {c.get('title')} ({c.get('source')})",
                  f"- LinkedIn: {c.get('linkedin') or '?'}", f"- Email: {c.get('email') or 'none found'} {('— ' + c['email_status']) if c.get('email_status') else ''}", ""]
    hm = j.get("hiring_manager")
    if hm:
        lines += ["## Likely hiring manager", f"- {hm.get('name')}, {hm.get('title')} ({hm.get('source')})",
                  f"- LinkedIn: {hm.get('linkedin') or '?'}",
                  f"- Email: {hm.get('email') or 'none found'} {('— ' + hm['email_status']) if hm.get('email_status') else ''}", ""]
    if j.get("warm_paths"):
        lines += ["## Warm paths (ask for a referral first)"] + [
            f"- {w['name']} — {w['headline']} (shared: {w['shared']}) {w.get('linkedin') or ''}" for w in j["warm_paths"]] + [""]
    if j.get("description"):
        lines += ["## Description", j["description"][:6000]]
    return "\n".join(lines) + "\n"


def money(v):
    if not v:
        return "?"
    v = float(v)
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.1f}M" if v >= 1e6 else f"${v:,.0f}"


def cmd_set(home, args):
    if args.status is None and not (args.apply_url or args.note):
        raise Fail("Nothing to change.", "Give a status, --apply-url or --note.")
    if args.status is not None and args.status not in STATUSES:
        raise Fail(f"Unknown status '{args.status}'", "One of: " + ", ".join(STATUSES))
    jobs, jid = get_job(home, args.id)
    j = jobs[jid]
    if args.status is not None:
        j.setdefault("history", []).append({"ts": iso(now()), "from": j.get("status"), "to": args.status, "note": args.note})
        j["status"] = args.status
    if args.apply_url:
        if not re.match(r"^https?://", args.apply_url):
            raise Fail("--apply-url must be an http(s) link.", "Pass the full URL of the application page.")
        j["apply_url"] = args.apply_url
        if (app_folder(home, j) / "job.md").exists():
            (app_folder(home, j) / "job.md").write_text(render_job_md(j))
    if args.followed_up:
        j["followed_up"] = iso(now())
    if args.via:
        j["via"] = sorted(set(j.get("via", [])) | set(args.via.split(",")))
    if args.note:
        j["note"] = args.note
    home.save_jobs(jobs)
    return {"id": jid, "status": j.get("status"), "apply_url": j.get("apply_url")}


def warm_cell(j):
    w = j.get("warm_paths") or []
    v = sum(1 for x in w if x.get("verified"))
    return "—" if not w else (f"{v} verified" + (f", {len(w) - v} to check" if len(w) > v else ""))


def form_cell(j, home=None):
    f = j.get("form")
    if not f:
        return "by hand"
    app = read_json(app_folder(home, j) / "application.json", None) if home else None
    left = len(forms.summary(app["fields"]).get("for_user") or []) if app else len(f.get("for_user") or [])
    return f"prefilled; {left} for you"


def cmd_digest(home, args):
    jobs = list(home.jobs().values())
    day = args.date or local_day()
    queue = [j for j in jobs if j.get("status") in ("shortlisted", "drafted")]
    # Referral first: jobs with a referral note ready lead, then any verified warm path, then fit.
    queue.sort(key=lambda j: (not (app_folder(home, j) / "referral.md").exists(),
                              not any(w.get("verified") for w in j.get("warm_paths") or []), -(j.get("score") or 0)))
    applied = [j for j in jobs if j.get("status") == "applied"]
    stale, dead = [], []
    for j in applied:
        ts = next((h["ts"] for h in reversed(j.get("history", [])) if h["to"] == "applied"), None)
        if not ts:
            continue
        days = (now() - dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)).days
        if days >= 14:
            dead.append(j)
        elif days >= 5 and not j.get("followed_up"):
            stale.append(j)
    # The daily run's cost, same as check and the budget; one-off setup (company discovery) is excluded.
    spent = sum(e["cost"] for e in home.ledger() if local_day(e["ts"]) == day and e.get("purpose") != "setup")
    lines = [f"# Job hunt: {day}", "", f"{len(queue)} waiting for your approval. Monid data cost today: ${spent:.2f}.", ""]
    if queue:
        lines += ["| # | Role | Company | Pay | Applicants | Contact | Warm paths | Form | Why | Status |", "|---|---|---|---|---|---|---|---|---|---|"]
        for i, j in enumerate(queue, 1):
            b, c = brief(j), j.get("contact") or {}
            folder = app_folder(home, j).relative_to(home.root)
            hm = j.get("hiring_manager") or {}
            contact = "careers page only" if j.get("outreach_ok") is False else (hm.get("name") or c.get("name") or "—")
            lines.append(f"| {i} | [{j['title'].strip()}]({folder}/job.md) | {j['company']} | {b['salary'] or '—'} | "
                         f"{b['applicants'] if b['applicants'] is not None else '—'} | {contact} | {warm_cell(j)} | {form_cell(j, home)} | {(j.get('note') or '').replace('|', '/')} | {j['status']} |")
        lines.append("")
    if stale:
        lines += ["## Follow up once (applied 5+ days ago, no update)"] + [f"- {j['title']} at {j['company']}" for j in stale] + [""]
    if dead:
        lines += ["## Probably ghosted (14+ days, no update): mark `ghosted` unless you've heard back"] + [f"- {j['title']} at {j['company']}" for j in dead] + [""]
    counts = {}
    for j in jobs:
        counts[j.get("status")] = counts.get(j.get("status"), 0) + 1
    lines += ["## Pipeline", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: STATUSES.index(kv[0]) if kv[0] in STATUSES else 99))]
    path = home.digests / f"{day}.md"
    path.write_text("\n".join(lines) + "\n")
    inbox = render_inbox(home, queue, day, spent)
    (home.digests / f"{day}.html").write_text(inbox.replace('href="applications/', 'href="../applications/'))
    (home.root / "inbox.html").write_text(inbox)
    return {"digest": str(path), "inbox": str(home.root / "inbox.html"), "queue": len(queue), "follow_ups": len(stale), "likely_ghosted": len(dead),
            "spent_today_usd": round(spent, 4)}


def cmd_spend(home, args):
    fixed = 0
    if args.reconcile:  # replace logged estimates with what Monid actually billed
        env = {**os.environ, "NO_COLOR": "1"}
        led = home.ledger()
        for e in led:
            if (e.get("estimated") or e.get("cost_unreported")) and e.get("run_id"):
                final = final_cost(os.environ.get("MONID_BIN", "monid"), e["run_id"], env, tries=1)
                if final is not None:
                    e["cost"] = round(final, 6)
                    e.pop("estimated", None); e.pop("cost_unreported", None)
                    fixed += 1
        fd, tmp = tempfile.mkstemp(dir=home.root, prefix="ledger.", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.writelines(json.dumps(e) + "\n" for e in led)
        os.replace(tmp, home.ledger_path)
    led = home.ledger()
    by_day, by_ep, failed = {}, {}, 0
    for e in led:
        d = local_day(e["ts"])
        by_day[d] = round(by_day.get(d, 0) + e["cost"], 4)
        k = f"{e['provider']} {e['endpoint']}"
        by_ep[k] = round(by_ep.get(k, 0) + e["cost"], 4)
        failed += 0 if e["ok"] else 1
    return {"total_usd": round(sum(e["cost"] for e in led), 4), "calls": len(led), "failed_calls": failed,
            **({"reconciled": fixed} if args.reconcile else {}),
            "estimated_calls": sum(1 for e in led if e.get("estimated") or e.get("cost_unreported")),
            "by_day": by_day, "by_endpoint": dict(sorted(by_ep.items(), key=lambda kv: -kv[1]))}


# ---------- applications ----------

def candidate_base(cand):
    return "-".join(w for w in [cand.get("first_name"), cand.get("last_name")] if w).replace(" ", "-") or "Applicant"


def doc_paths(home, j, cand):
    folder = app_folder(home, j)
    base = candidate_base(cand)
    return folder, folder / f"{base}-Resume.pdf", folder / f"{base}-Cover-Letter.pdf"


HOSTED_APPLY = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}/jobs/{id}",
    "lever": "https://jobs.lever.co/{slug}/{id}/apply",
    "ashby": "https://jobs.ashbyhq.com/{slug}/{id}/application",
}


def find_form(j):
    for u in (j.get("apply_url"), j.get("url")):
        d = forms.detect(u)
        if d and d[1]:
            return d
        if d and not d[1]:  # a company careers page carrying a Greenhouse or Ashby job id
            r = forms.resolve(j["company"], j["title"], j.get("company_domain"),
                              gh_job_id=d[2] if d[0] == "greenhouse" else None,
                              ashby_job_id=d[2] if d[0] == "ashby" else None)
            if r:
                return r[:3]
    r = forms.resolve(j["company"], j["title"], j.get("company_domain"))
    return r[:3] if r else None


def save_application(home, j, app):
    folder = app_folder(home, j)
    write_json(folder / "application.json", app)
    (folder / "apply.md").write_text(forms.render_apply_md(j, app))


def cmd_form(home, args):
    jobs, jid = get_job(home, args.id)
    out = form_job(home, jobs[jid], home.answers())
    home.save_jobs(jobs)
    return {"id": jid, **out}


def form_job(home, j, cand):
    jid = j["id"]
    folder, resume_pdf, cover_pdf = doc_paths(home, j, cand)
    target = find_form(j)
    if not target:
        return {"form": None, "apply_url": j.get("apply_url"),
                "note": "Not a Greenhouse, Lever or Ashby form. Use answers.json and the drafts to fill it by hand."}
    ats, slug, ext = target
    fields = forms.fetch(ats, slug, ext)
    if not fields:
        return {"form": None, "ats": ats, "apply_url": j.get("apply_url"),
                "note": "Found the posting but couldn't read its form; it may have closed."}
    old = {f["key"]: f for f in read_json(folder / "application.json", {}).get("fields", [])
           if (f.get("source") or "").startswith(("drafted", "you"))}
    files = {"resume": resume_pdf.name if resume_pdf.exists() else None,
             "cover": cover_pdf.name if cover_pdf.exists() else None}
    fields = forms.prefill(fields, cand, j, files)
    for f in fields:
        if f["key"] in old:
            f.update({k: old[f["key"]][k] for k in ("answer", "status", "source")})
    apply_url = HOSTED_APPLY[ats].format(slug=slug, id=ext)
    warnings = []
    if any(re.search(r"ai policy|use of ai|artificial intelligence", f["label"], re.I) for f in fields):
        warnings.append("The form asks about an AI policy. Read it first: if it restricts AI-written answers, "
                        "rewrite the drafted answers yourself before submitting.")
    if any(f["status"] == forms.USER and forms.LEGAL.search(f["label"]) for f in fields):
        warnings.append("Read and check the legal acknowledgements yourself.")
    if j.get("outreach_ok") is False:
        warnings.append(j.get("outreach_note"))
    app = {"ats": ats, "slug": slug, "job_id": ext, "apply_url": apply_url, "fetched_at": iso(now()),
           "fields": fields, "warnings": warnings}
    j["apply_url"], j["careers_apply"] = apply_url, True
    save_application(home, j, app)
    j["form"] = {"ats": ats, "apply_url": apply_url, **{k: v for k, v in forms.summary(fields).items() if k != "to_draft"}}
    return {"ats": ats, "apply_url": apply_url, "folder": str(folder), **forms.summary(fields), "warnings": warnings}


def load_application(home, jid_prefix):
    jobs, jid = get_job(home, jid_prefix)
    j = jobs[jid]
    folder = app_folder(home, j)
    app = read_json(folder / "application.json", None)
    if not app:
        raise Fail("No application form saved for this job.", f"Run `jobhunt.py form {jid[:6]}` first.")
    return jobs, jid, j, folder, app


def match_field(fields, ref):
    if ref.isdigit() and 1 <= int(ref) <= len(fields):
        return fields[int(ref) - 1]
    exact = [f for f in fields if f["key"] == ref]
    if exact:
        return exact[0]
    hits = [f for f in fields if forms.norm(ref) in forms.norm(f["label"])]
    if len(hits) == 1:
        return hits[0]
    raise Fail(f"'{ref}' matches {len(hits)} fields", "Use the field number from apply.md or its key from application.json.")


def cmd_answer(home, args):
    jobs, jid, j, folder, app = load_application(home, args.id)
    f = match_field(app["fields"], args.field)
    if args.to_user:
        f.update(answer=None, status=forms.USER, source=args.text or "your profile doesn't cover this; answer it yourself")
        save_application(home, j, app)
        return {"id": jid, "field": f["label"], "status": "user", **forms.summary(app["fields"])}
    if args.text is None:
        raise Fail("No answer given.", "Pass the answer text, or --to-user to leave the field for the user.")
    value = args.text
    if f["options"] and f["type"] == "multiselect":
        parts = [p.strip() for p in re.split(r"\s*;\s*|\s*,\s*(?![^()]*\))", value) if p.strip()]
        picked = [forms.pick(f["options"], p) for p in parts]
        if not parts or None in picked:
            bad = [p for p, c in zip(parts, picked) if c is None]
            raise Fail(f"{bad or value} isn't among the options", "Separate several with '; '. Options: " + " / ".join(f["options"]))
        value = "; ".join(dict.fromkeys(picked))
    elif f["options"]:
        choice = forms.pick(f["options"], value)
        if choice is None:
            raise Fail(f"'{value}' isn't one of the options", "Options: " + " / ".join(f["options"]))
        value = choice
    f.update(answer=value, status=forms.FILLED, source="you" if args.user else "drafted from profile.md; check it")
    if args.remember and not forms.LEGAL.search(f["label"]):
        cand = home.answers()
        cand.setdefault("custom", {})[f["label"]] = value
        write_json(home.root / "answers.json", cand)
    save_application(home, j, app)
    return {"id": jid, "field": f["label"], "answer": value, "remembered": bool(args.remember),
            **forms.summary(app["fields"])}


def find_chrome():
    env = os.environ.get("CHROME_BIN")
    cands = [env] if env else []
    cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/Applications/Chromium.app/Contents/MacOS/Chromium",
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
              "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"]
    cands += [shutil.which(n) for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")]
    return next((c for c in cands if c and os.path.exists(c)), None)


def render_pdf(md_path, pdf_path, title, letter=False):
    html_path = pdf_path.with_suffix(".html")
    html_path.write_text(forms.md_to_html(md_path.read_text(), title, letter=letter))
    chrome = find_chrome()
    if not chrome:
        return {"pdf": None, "html": str(html_path), "note": "No Chrome/Chromium/Edge found; open the HTML and print to PDF."}
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                        f"--user-data-dir={profile}", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                       capture_output=True, text=True, timeout=90)
    if pdf_path.exists() and pdf_path.stat().st_size > 1000:
        html_path.unlink()
        return {"pdf": str(pdf_path)}
    return {"pdf": None, "html": str(html_path), "note": "Chrome didn't produce a PDF; open the HTML and print to PDF."}


NUMBER = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:%|k\b|m\b|b\b|x\b|\+))?", re.I)
FILLER = ["i am writing to", "i hope this", "passionate about", "excited to apply", "dynamic", "synergy", "rockstar",
          "ninja", "results-driven", "detail-oriented", "team player", "go-getter", "thrilled", "delve", "leverage my"]
PLACEHOLDER = re.compile(r"\[(?:engine|n|company|name|role|todo|x)[^\]]*\]|\bTODO\b|\bXXX\b|lorem ipsum", re.I)


def norm_num(s):
    return re.sub(r"[\s,$]", "", s.lower())


URL = re.compile(r"(https?://\S+|\b[\w-]+(\.[\w-]+)+/\S*)")

# Small numbers are exempt from tracing ("2 years"), except counts like these, which are claims.
COUNT_CLAIM = re.compile(r"\b(\d{1,2})\s+(engineers|people|reports|direct reports|developers|designers|teams|customers|clients"
                         r"|countries|languages|services|microservices|products|launches|patents|papers|interns|hires)\b", re.I)
# Tools a resume can't claim unless profile.md does; config `skills` are added at lint time.
TECH_TERMS = ["python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#", "ruby", "php", "scala",
              "kotlin", "swift", "react", "vue", "angular", "svelte", "next.js", "node.js", "django", "flask", "fastapi",
              "rails", "spring", "graphql", "postgresql", "mysql", "mongodb", "redis", "kafka", "spark", "airflow",
              "snowflake", "dbt", "bigquery", "aws", "gcp", "azure", "kubernetes", "docker", "terraform", "pytorch",
              "tensorflow", "jax", "langchain", "elasticsearch", "figma", "tableau", "looker", "salesforce", "hubspot"]
EMPLOYER_CLAIM = re.compile(r"\b(?:at|for|with|joined)\s+((?:[A-Z][\w&.-]+)(?:\s+[A-Z][\w&.-]+){0,2})")


def body_text(path):
    text = re.sub(r"<!--[\s\S]*?-->", "", path.read_text())
    return "\n".join(l for l in text.splitlines() if not re.match(r"^(to|subject|cc):", l.strip(), re.I)).strip()


def cmd_lint(home, args):
    jobs, jid = get_job(home, args.id)
    j, cfg, cand = jobs[jid], home.config(), home.answers()
    folder = app_folder(home, j)
    profile_text = (home.root / "profile.md").read_text() if (home.root / "profile.md").exists() else ""
    source_text = " ".join([profile_text,
                            j.get("description") or "",
                            json.dumps({k: v for k, v in cand.items() if k not in ("phone", "location")})])
    found = {norm_num(n) for n in NUMBER.findall(URL.sub(" ", source_text))}
    # Whole numbers only: "55%" isn't traceable to a phone number containing 555.
    sources = found | {re.sub(r"[^0-9.]", "", v) for v in found}
    errors, warnings = [], []
    limits = {"referral.md": ("chars", 400), "email.md": ("words", 150), "note.md": ("words", 180)}
    for name in ("resume.md", "note.md", "email.md", "referral.md"):
        p = folder / name
        if not p.exists():
            continue
        text = body_text(p)
        for n in NUMBER.findall(URL.sub(" ", text)):
            v = norm_num(n)
            core = re.sub(r"[^0-9.]", "", v)
            small = core.replace(".", "").isdigit() and float(core or 0) < 20 and v == core
            if not small and v not in sources and core not in sources:
                errors.append(f"{name}: '{n.strip()}' isn't in profile.md, the posting or answers.json")
        for m_ in COUNT_CLAIM.finditer(text):
            if m_.group(1) not in sources:
                errors.append(f"{name}: '{m_.group(0)}' isn't in profile.md, the posting or answers.json")
        # Tools and employers must come from the user's own facts; for a resume, the posting doesn't count.
        profile_l = profile_text.lower()
        for term in dict.fromkeys(TECH_TERMS + [k.lower() for k in cfg.get("skills") or []]):
            forms_ = {term, term + "s"} | ({term[:-1]} if len(term) > 3 and term.endswith("s") and not term.endswith("ss") else set())  # API / APIs
            pat = r"(?<![\w.+#])(?:" + "|".join(re.escape(t) for t in sorted(forms_, key=len, reverse=True)) + r")(?![\w+#])"
            if re.search(pat, text, re.I) and not re.search(pat, profile_l):
                if name == "resume.md":
                    errors.append(f"resume.md: '{term}' isn't in profile.md; don't claim tools the user hasn't confirmed")
                elif not re.search(pat, (j.get("description") or "").lower()):
                    warnings.append(f"{name}: mentions '{term}', which isn't in profile.md or the posting")
        known = (profile_text + " " + (j.get("description") or "") + " " + j.get("company", "")).lower()
        for m_ in EMPLOYER_CLAIM.finditer(text):
            org = m_.group(1).rstrip(".")
            if org.split()[0].lower() not in known and len(org) > 2:
                warnings.append(f"{name}: '{m_.group(0)}': {org} isn't in profile.md or the posting; check it's true")
        if PLACEHOLDER.search(text):
            errors.append(f"{name}: unfilled placeholder '{PLACEHOLDER.search(text).group(0)}'")
        for phrase in FILLER:
            if phrase in text.lower():
                warnings.append(f"{name}: filler phrase '{phrase}'")
        if name in limits:
            unit, cap = limits[name]
            size = len(text) if unit == "chars" else len(text.split())
            if size > cap:
                errors.append(f"{name}: {size} {unit}, limit {cap}")
    resume = folder / "resume.md"
    if resume.exists():
        words = len(body_text(resume).split())
        if words > 750:
            warnings.append(f"resume.md: {words} words; likely over one page")
        wanted = skills_in(j.get("description") or "", cfg.get("skills") or [])
        covered = skills_in(body_text(resume), wanted)
        missing = [k for k in wanted if k not in covered]
        keyword_match = {"in_resume": len(covered), "of": len(wanted), "missing": missing}
        if missing:
            warnings.append("resume.md omits skills the posting asks for that you list: " + ", ".join(missing))
    app = read_json(folder / "application.json", None)
    if app:
        for f in app["fields"]:
            if (f.get("source") or "").startswith("drafted") and isinstance(f.get("answer"), str):
                for n in NUMBER.findall(f["answer"]):
                    v = norm_num(n)
                    core = re.sub(r"[^0-9.]", "", v)
                    if not (core.replace(".", "").isdigit() and float(core or 0) < 20 and v == core) and v not in sources and core not in sources:
                        errors.append(f"form answer '{f['label'][:40]}': '{n.strip()}' isn't traceable")
        empty = [f for f in app["fields"] if f["required"] and not f.get("answer") and f["status"] != forms.USER]
        files = [f["label"][:60] for f in empty if f["type"] == "file"]
        others = [f["label"][:80] for f in empty if f["type"] != "file"]
        if others:
            errors.append("required form fields still empty: " + "; ".join(others))
        if files:
            warnings.append("file fields wait for `packet` to render the PDFs: " + "; ".join(files))
    out = {"id": jid, "ok": not errors, "errors": errors, "warnings": warnings}
    if resume.exists():
        out["keyword_match"] = keyword_match
    return out


def cmd_packet(home, args):
    jobs, jid = get_job(home, args.id)
    j, cand = jobs[jid], home.answers()
    folder, resume_pdf, cover_pdf = doc_paths(home, j, cand)
    name = f"{cand.get('first_name', '')} {cand.get('last_name', '')}".strip() or "Applicant"
    docs = {}
    if (folder / "resume.md").exists():
        docs["resume"] = render_pdf(folder / "resume.md", resume_pdf, f"{name} — Resume")
    if (folder / "note.md").exists():
        body = body_text(folder / "note.md")
        contact = " · ".join(x for x in [cand.get("email"), cand.get("phone"), cand.get("location"),
                                           cand.get("linkedin") or cand.get("website") or cand.get("github")] if x)
        greeting = "" if re.match(r"^(dear|hi|hello)\b", body, re.I) else "Dear Hiring Team,\n\n"
        signoff = "" if name.split()[0].lower() in body[-80:].lower() else f"\n\nSincerely,\n\n{name}"
        letter = folder / ".cover-letter.md"
        letter.write_text(f"# {name}\n{contact}\n\n{dt.date.today().strftime('%B %-d, %Y')}\n\n"
                          f"Re: {j['title']}, {j['company']}\n\n{greeting}{body}{signoff}\n")
        docs["cover"] = render_pdf(letter, cover_pdf, f"{name} — Cover letter", letter=True)
        letter.unlink()
    app = read_json(folder / "application.json", None)
    if app:
        for f in app["fields"]:
            if f["type"] == "file" and re.search(r"resume|cv", f["label"], re.I) and resume_pdf.exists():
                f.update(answer=resume_pdf.name, status=forms.FILLED, source="resume PDF")
            if f["type"] == "file" and re.search(r"cover", f["label"], re.I) and cover_pdf.exists():
                f.update(answer=cover_pdf.name, status=forms.FILLED, source="cover letter PDF")
            if f["type"] == "longtext" and re.search(r"cover letter", f["label"], re.I) and (folder / "note.md").exists() and not f.get("answer"):
                f.update(answer=body_text(folder / "note.md"), status=forms.FILLED, source="drafted from profile.md; check it")
        save_application(home, j, app)
    lint = cmd_lint(home, argparse.Namespace(id=jid))
    if lint.get("keyword_match"):
        jobs_now = home.jobs()
        jobs_now[jid]["keyword_match"] = lint["keyword_match"]
        home.save_jobs(jobs_now)
        j = jobs_now[jid]
    summ = forms.summary(app["fields"]) if app else None
    # A job without a readable form (Workday, LinkedIn-only, a plain link) is ready by hand: PDFs and drafts done.
    blocking = (lint["errors"] + (["resume.md is missing"] if "resume" not in docs else [])
                + [f"{k} PDF didn't render: {d.get('note') or 'install Chrome'}" for k, d in docs.items() if not d.get("pdf")])
    ready = not blocking
    if ready and j.get("status") in ("new", "saved", "shortlisted"):
        jobs = home.jobs()
        jobs[jid].setdefault("history", []).append({"ts": iso(now()), "from": jobs[jid].get("status"), "to": "drafted", "note": "packet ready"})
        jobs[jid]["status"] = "drafted"
        home.save_jobs(jobs)
    required_for_user = [f["label"][:80] for f in (app or {}).get("fields", []) if f["required"] and f["status"] == forms.USER]
    return {"id": jid, "ready": ready, "blocking": blocking, "by_hand": not app,
            "apply_url": (app or {}).get("apply_url") or j.get("apply_url"),
            "documents": docs, "form": summ, "lint": lint, "for_you": (summ or {}).get("for_user", []),
            "required_for_user": required_for_user,
            "meaning": "ready = everything the agent can do is done; required_for_user must be answered by the user before submitting",
            "folder": str(folder)}


def cmd_stats(home, args):
    jobs = list(home.jobs().values())
    order = ["shortlisted", "drafted", "approved", "applied", "interviewing", "offer"]
    reached = {k: 0 for k in order}
    for j in jobs:
        st = j.get("status")
        hist = {h["to"] for h in j.get("history", [])} | {st}
        for k in order:
            if k in hist:
                reached[k] += 1
    applied = [j for j in jobs if {"applied", "interviewing", "offer", "rejected", "ghosted"} & ({h["to"] for h in j.get("history", [])} | {j.get("status")})]
    def responded(j):
        hist = {h["to"] for h in j.get("history", [])} | {j.get("status")}
        return bool({"interviewing", "offer"} & hist)
    by_via = {}
    for j in applied:
        for v in j.get("via") or ["form"]:
            b = by_via.setdefault(v, {"applied": 0, "interviews": 0})
            b["applied"] += 1
            b["interviews"] += responded(j)
    for b in by_via.values():
        b["interview_rate"] = round(b["interviews"] / b["applied"], 2) if b["applied"] else None
    skips = [j.get("note") for j in jobs if j.get("status") == "skipped" and j.get("note")]
    by_source = {}
    for j in jobs:
        hist = {h["to"] for h in j.get("history", [])} | {j.get("status")}
        for src in j.get("sources") or []:
            b = by_source.setdefault(src, {"found": 0, "passed_filters": 0, "shortlisted": 0, "applied": 0, "interviews": 0})
            b["found"] += 1
            b["passed_filters"] += j.get("status") != "filtered"
            b["shortlisted"] += bool(hist & {"shortlisted", "drafted", "approved", "applied", "interviewing", "offer"})
            b["applied"] += bool(hist & {"applied", "interviewing", "offer", "rejected", "ghosted"})
            b["interviews"] += bool(hist & {"interviewing", "offer"})
    return {"funnel": reached, "applied": len(applied), "interviews": sum(responded(j) for j in applied),
            "by_channel": by_via, "by_source": by_source, "recent_skip_reasons": skips[-12:]}


STAGES = {"seed": "SEED", "a": "SERIES_A", "b": "SERIES_B", "c": "SERIES_C", "d": "SERIES_D", "e": "SERIES_E"}


def cmd_companies(home, args):
    """Recently funded companies in the user's industry and city, with their job boards and matching openings."""
    cfg = home.config()
    m = Monid(home, args.max_cost, "setup")
    r = m.run("fundable", "/industry/search", query={"name": args.industry}, step=f"industry: {args.industry}")
    inds = (((r["output"] or {}).get("data") or {}).get("industries") or []) if r["ok"] else []
    if not inds:
        raise Fail(f"No Fundable industry matches '{args.industry}'", "Try a broader word, e.g. fintech, healthcare, developer tools.")
    company = {"industries": [inds[0]["permalink"]]}
    place = None
    if args.location:
        r = m.run("fundable", "/location/search", query={"name": args.location}, step=f"location: {args.location}")
        locs = (((r["output"] or {}).get("data") or {}).get("locations") or []) if r["ok"] else []
        locs.sort(key=lambda l: (l.get("location_type") != "CITY", "united-states" not in l.get("permalink", "") and
                                 not re.search(r"-(new-york|california|washington|texas|massachusetts|illinois|colorado|georgia)$", l.get("permalink", ""))))
        if locs:
            place = locs[0]
            company["locations"] = [place["permalink"]]
    since = (now() - dt.timedelta(days=args.since_days)).strftime("%Y-%m-%d")
    body = {"deal": {"financing_types": [{"type": STAGES[x.strip().lower()]} for x in args.stages.split(",")], "date_start": since},
            "company": company, "page_size": args.limit}
    r = m.run("fundable", "/deals", body=body, step="recent rounds", estimate=0.06 * args.limit)
    if not r["ok"]:
        raise Fail("The funding search failed or would exceed the budget.", f"{r.get('error') or r.get('skipped')}; try --limit 5 or --max-cost.")
    out = []
    for d in ((r["output"] or {}).get("data") or {}).get("deals") or []:
        desc = d.get("deal_descriptions") or {}
        if isinstance(desc, list):
            desc = desc[0] if desc else {}
        summary = desc.get("short_description") or ""
        name = re.split(r",| raised| has raised| secured| closed", summary)[0].strip()
        if not name:
            continue
        board = forms.find_board(name)
        matching = [t for t, loc in (board[2] if board else []) if role_matches(t, cfg["roles"])]
        out.append({"company": name, "round": d.get("round_type"), "date": (d.get("date") or "")[:10],
                    "amount_usd": d.get("total_round_raised"), "summary": summary[:200],
                    "ats": board[0] if board else None, "slug": board[1] if board else None,
                    "open_jobs": len(board[2]) if board else None, "matching_your_roles": matching[:5],
                    "check_board": bool(board) and board[1] not in forms.slug_candidates(name)[:2]})
    out.sort(key=lambda c: (-len(c["matching_your_roles"]), c["ats"] is None))
    return {"industry": inds[0]["name"], "location": (place or {}).get("name"), "since": since, "companies": out,
            "add_to_config": [{"name": c["company"], "ats": c["ats"], "slug": c["slug"]} for c in out if c["ats"]],
            "monid": m.summary()}


# ---------- one-call daily commands ----------

def excerpt(text, n=700):
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"(requirements|qualifications|what you.ll (bring|need)|you have|about you|you might be|we.re looking for)", t, re.I)
    start = max(0, m.start() - 40) if m and m.start() > 300 else 0
    return t[start:start + n]


def cmd_run(home, args):
    """Check, search, and return a compact sheet of the best new and saved jobs for the agent to judge."""
    chk = cmd_check(home, args)
    if not (chk.get("monid") or {}).get("ok"):
        raise Fail("Monid isn't reachable.", (chk.get("monid") or {}).get("error") or "Run `jobhunt.py check`.")
    cfg = home.config()
    if home.spent_today() >= cfg["budget"]["per_day_usd"]:
        raise Fail("Today's Monid budget is used up.", "Raise budget.per_day_usd in config.json or run tomorrow.")
    res = cmd_search(home, argparse.Namespace(max_cost=args.max_cost, top=0, window=args.window))
    jobs = [j for j in home.jobs().values() if j.get("status") in ("new", "saved")]
    jobs.sort(key=lambda j: -(j.get("score") or 0))
    sheet = []
    for j in jobs[:args.top]:
        b = brief(j)
        sheet.append({"id": j["id"], "score": j.get("score"), "status": j["status"], "company": j["company"], "title": j["title"],
                      "where": f"{b['location'] or '?'} ({b['workplace'] or '?'})", "pay": b["salary"], "posted": b["posted"],
                      "applicants": b["applicants"], "sources": j.get("sources"), "years_required": j.get("years_required"),
                      "skills_matched": j.get("skills_matched"), "why": j.get("score_reasons"),
                      "posting": excerpt(j.get("description"))})
    return {"search": {k: res[k] for k in ("window", "found", "found_by_source", "new", "new_by_source", "already_seen", "filtered", "monid", "notes")},
            "to_judge": sheet, "next": "Read each, `set ID shortlisted|saved|skipped --note`; `show ID` for a full posting."}


def cmd_prepare(home, args):
    """Research and read the form for each shortlisted job, in parallel; return what's left to write."""
    cfg, cand = home.config(), home.answers()
    m = Monid(home, args.max_cost if args.max_cost is not None else cfg["budget"]["per_run_usd"], "prepare")
    jobs = home.jobs()
    ids = [get_job(home, raw)[1] for raw in args.ids]

    save_lock = threading.Lock()

    def one(jid):
        j = jobs[jid]
        research_error = None
        if not j.get("enriched_at") or args.refresh:
            try:
                enrich_job(home, m, j, cfg)
            except Fail as e:  # BLOCKED or a rejected key: keep what was found, report it
                research_error = f"{e.error} {e.hint}"
            except Exception as e:
                research_error = f"research failed: {type(e).__name__}: {str(e)[:120]}"
        try:
            f = form_job(home, j, cand)
        except Exception as e:
            f = {"form": None, "note": f"form read failed: {type(e).__name__}"}
        with save_lock:  # paid research is kept even if a later job fails
            home.save_jobs(jobs)
        have = cand.get("years_experience")
        return {"id": jid, "company": j["company"], "title": j["title"], "folder": str(app_folder(home, j)),
                "apply_url": f.get("apply_url") or j.get("apply_url"), "ats": f.get("ats"),
                "fields_filled": f.get("filled"), "write_these": f.get("to_draft"), "for_user": f.get("for_user"),
                "form_note": f.get("note"), "warnings": f.get("warnings"),
                "contact": mask_contact(j.get("contact")), "hiring_manager": mask_contact(j.get("hiring_manager")), "warm_paths": [
                    {k: w.get(k) for k in ("name", "headline", "shared", "verified", "same_function", "linkedin")} for w in j.get("warm_paths") or []],
                "outreach_ok": j.get("outreach_ok"), "ai_screening": j.get("ai_screening"),
                "fit": {"skills_matched": j.get("skills_matched"), "years_required": j.get("years_required"), "you_have": have},
                **({"research_error": research_error} if research_error else {})}

    with ThreadPoolExecutor(min(4, len(ids)) or 1) as ex:
        out = list(ex.map(one, ids))
    home.save_jobs(jobs)
    return {"prepared": out, "monid": m.summary(),
            "next": "Per job: resume.md, `answer` each write_these field, referral.md/email.md when there's a warm path or contact, then `packet ID`."}


def html_escape(s):
    import html as _h
    return _h.escape(str(s if s is not None else ""))


def read_draft(path):
    if not path.exists():
        return None
    return re.sub(r"<!--[\s\S]*?-->", "", path.read_text()).strip()


def email_parts(text):
    to = re.search(r"^To:\s*(.+)$", text, re.M | re.I)
    subj = re.search(r"^Subject:\s*(.+)$", text, re.M | re.I)
    body = "\n".join(l for l in text.splitlines() if not re.match(r"^(to|subject|cc):", l.strip(), re.I)).strip()
    return (to.group(1).strip() if to else ""), (subj.group(1).strip() if subj else ""), body


def match_badge(j):
    km = j.get("keyword_match")
    if not km or not km.get("of"):
        return ""
    miss = f" · not in resume: {html_escape(', '.join(km['missing']))}" if km.get("missing") else ""
    return (f'<div class="km"><b>Keyword match {km["in_resume"]}/{km["of"]}</b> of the skills this posting asks for '
            f'that you have{miss}</div>')


def shorten(text, n):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def job_md_section(folder, name):
    p = folder / "job.md"
    if not p.exists():
        return []
    m = re.search(rf"^## {name}\s*\n([\s\S]*?)(?=^## |\Z)", p.read_text(), re.M)
    return [re.sub(r"^\s*[-*]\s*", "", l).strip() for l in (m.group(1).splitlines() if m else []) if l.strip()]


def render_inbox(home, queue, day, spent):
    cand = home.answers()
    cards = []
    for n, j in enumerate(queue, 1):
        folder = app_folder(home, j)
        rel = folder.relative_to(home.root).as_posix()
        app = read_json(folder / "application.json", None)
        b = brief(j)
        pdfs = [p.name for p in sorted(folder.glob("*.pdf"))]
        parts = [f'<section class="card"><header><span class="n">{n}</span><div><h2>{html_escape(j["title"])}</h2>'
                 f'<div class="co">{html_escape(j["company"])} · {html_escape(b["location"] or "")} · {html_escape(b["salary"] or "pay not posted")}'
                 f' · posted {html_escape(b["posted"] or "?")}{" · " + str(b["applicants"]) + " applicants" if b["applicants"] is not None else ""}</div>'
                 f'<div class="why">{html_escape(j.get("note") or "")}</div>{match_badge(j)}</div></header>']
        warn = list((app or {}).get("warnings") or [])
        if j.get("ai_screening"):
            warn.append("The employer discloses AI use in screening" + (f' (opt out: {j["ai_opt_out_url"]})' if j.get("ai_opt_out_url") else "") + ".")
        if warn:
            parts.append('<div class="warn">' + "<br>".join(html_escape(w) for w in warn if w) + "</div>")
        people = []
        if j.get("hiring_manager"):
            people.append(f"hiring manager {html_escape(j['hiring_manager'].get('name'))} ({html_escape(j['hiring_manager'].get('title'))})")
        if j.get("contact"):
            role = "recruiter" if RECRUITER.search(j["contact"].get("title") or "") else "contact"
            people.append(f"{role} {html_escape(j['contact'].get('name'))}")
        if j.get("warm_paths"):
            people.append(f"{len(j['warm_paths'])} referral path{'s' if len(j['warm_paths']) > 1 else ''}")
        if people:
            parts.append('<div class="people">People: ' + " · ".join(people) + "</div>")
        gaps, flags = job_md_section(folder, "Gaps"), job_md_section(folder, "Red flags")
        if gaps or flags:
            items = [f"<li><b>Gap:</b> {html_escape(g)}</li>" for g in gaps] + [f"<li><b>Flag:</b> {html_escape(f)}</li>" for f in flags]
            parts.append(f'<details class="gaps"><summary>Gaps and flags ({len(items)})</summary><ul>{"".join(items)}</ul></details>')
        steps = []
        ref = read_draft(folder / "referral.md")
        verified = [w for w in j.get("warm_paths") or [] if w.get("verified")]
        if ref and (verified or j.get("warm_paths")):
            w = (verified or j["warm_paths"])[0]
            steps.append(f'<li><b>Ask for a referral</b> from <a href="{html_escape(w.get("linkedin"))}" target="_blank">{html_escape(w["name"])}</a>'
                         f' ({html_escape(w.get("shared"))}{", verified" if w.get("verified") else ", check their profile"})'
                         f'<pre>{html_escape(ref)}</pre><button data-copy="{html_escape(ref)}">Copy note</button></li>')
        apply_url = (app or {}).get("apply_url") or j.get("apply_url") or j.get("url")
        field_rows = []
        for i, f in enumerate((app or {}).get("fields") or [], 1):
            ans = f.get("answer")
            if isinstance(ans, list):
                ans = "; ".join(map(str, ans))
            tag = {"user": "you", "optional": "optional"}.get(f["status"], "drafted" if (f.get("source") or "").startswith("drafted") else "")
            copy = f'<button data-copy="{html_escape(ans)}">Copy</button>' if ans and f["type"] != "file" else ""
            shown = html_escape(ans) if ans else f'<i>{html_escape(f.get("source") or "")}</i>'
            if f["type"] == "file" and ans:
                shown = f'<a href="{rel}/{html_escape(ans)}" target="_blank">{html_escape(ans)}</a>'
            field_rows.append(f'<tr class="{tag}"><td>{i}</td><td>{html_escape(f["label"])}{" *" if f["required"] else ""}</td>'
                              f'<td>{shown}</td><td class="tag">{tag}</td><td>{copy}</td></tr>')
        form_html = (f'<table>{"".join(field_rows)}</table>' if field_rows else
                     '<p>This form isn\'t one the skill can read; answers are in <code>apply.md</code> / answers.json.</p>')
        left = [f["label"] for f in (app or {}).get("fields") or [] if f["status"] == "user"]
        left_html = (f'<div class="left"><b>Left for you ({len(left)}):</b> ' + "; ".join(html_escape(shorten(x, 90)) for x in left) + "</div>") if left else \
            ('<div class="left">Nothing left to answer: review and submit.</div>' if app else "")
        pdf_links = " ".join('<a href="%s/%s" target="_blank">%s</a>' % (rel, html_escape(p), html_escape(p)) for p in pdfs)
        opened = " open" if n == 1 else ""
        steps.append(f'<li><b>Apply</b> <a class="btn" href="{html_escape(apply_url)}" target="_blank">Open application</a> '
                     f'{pdf_links}{left_html}<details{opened}><summary>Form, field by field</summary>{form_html}</details></li>')
        em = read_draft(folder / "email.md")
        if em and j.get("outreach_ok") is not False:
            to, subj, body = email_parts(em)
            mailto = "mailto:" + urllib.parse.quote(to) + "?subject=" + urllib.parse.quote(subj) + "&body=" + urllib.parse.quote(body)
            to_name = ((j.get("hiring_manager") or {}).get("name") or (j.get("contact") or {}).get("name") or "the recruiter")
            steps.append(f'<li><b>Email {html_escape(to_name)}</b> after applying '
                         f'<a class="btn" href="{html_escape(mailto)}">Open in mail</a><pre>{html_escape(body)}</pre></li>')
        steps.append(f'<li><b>Done?</b> Tell your agent "applied to {n}", or run <code>jobhunt.py set {j["id"][:6]} applied --via form</code></li>')
        parts.append("<ol>" + "".join(steps) + "</ol></section>")
        cards.append("".join(parts))
    css = ("body{font:15px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:920px;margin:32px auto;padding:0 16px;color:#1d1d1f;background:#f6f6f4}"
           "h1{font-size:24px;margin:0}.sub{color:#666;margin:4px 0 24px}.card{background:#fff;border:1px solid #e3e3e0;border-radius:12px;padding:20px 22px;margin:0 0 18px}"
           "header{display:flex;gap:14px}.n{background:#1d1d1f;color:#fff;border-radius:50%;width:28px;height:28px;display:grid;place-items:center;font-weight:600;flex:none}"
           "h2{font-size:18px;margin:0}.co{color:#555;font-size:14px}.why{margin-top:6px}.warn{background:#fff6e5;border:1px solid #f1d39b;border-radius:8px;padding:8px 12px;margin:12px 0;font-size:14px}"
           "ol{padding-left:20px;margin:14px 0 0}li{margin:0 0 12px}pre{white-space:pre-wrap;background:#f6f6f4;border-radius:8px;padding:10px 12px;font-family:inherit;font-size:14px;line-height:1.5;margin:8px 0}.left{margin:6px 0 0;font-size:14px}.left b{color:#9a5b00}.people{font-size:13px;color:#444;margin-top:8px}.km{margin-top:6px;font-size:13px;color:#14532d}.km b{background:#dcfce7;border-radius:6px;padding:1px 7px}"
           "button,.btn{font:inherit;font-size:13px;border:1px solid #1d1d1f;background:#fff;border-radius:6px;padding:3px 10px;cursor:pointer;text-decoration:none;color:#1d1d1f;margin-left:6px}"
           ".btn{background:#1d1d1f;color:#fff}table{border-collapse:collapse;width:100%;font-size:13px;margin-top:8px}td{border-top:1px solid #eee;padding:5px 6px;vertical-align:top}"
           "tr.you td{background:#fff6e5}.tag{color:#888;white-space:nowrap}summary{cursor:pointer;margin-top:8px;color:#444}a{color:#1d4ed8}")
    js = ("document.addEventListener('click',e=>{const b=e.target.closest('button[data-copy]');if(!b)return;"
          "navigator.clipboard.writeText(b.dataset.copy).then(()=>{const t=b.textContent;b.textContent='Copied';setTimeout(()=>b.textContent=t,1200)})});")
    name = f"{cand.get('first_name', '')}".strip()
    head = (f"<h1>{html_escape(name + ', y' if name else 'Y')}our job hunt · {html_escape(day)}</h1>"
            f"<p class='sub'>{len(queue)} application{'s' if len(queue) != 1 else ''} ready. Highlighted rows are yours to answer. "
            f"Monid data cost today: ${spent:.2f}.</p>")
    body = head + ("".join(cards) or "<p>Nothing queued today.</p>")
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Job hunt {html_escape(day)}</title><style>{css}</style></head><body>{body}<script>{js}</script></body></html>"


def cmd_open(home, args):
    path = home.root / "inbox.html"
    if not path.exists():
        cmd_digest(home, argparse.Namespace(date=None))
    if os.environ.get("JH_SCHEDULED_RUN"):
        return {"inbox": str(path), "opened": False, "note": "scheduled run: not opening a browser"}
    opener = "open" if sys.platform == "darwin" else ("start" if os.name == "nt" else "xdg-open")
    try:
        subprocess.run([opener, str(path)], check=False, capture_output=True, timeout=20, shell=os.name == "nt")
    except FileNotFoundError:
        pass
    return {"inbox": str(path)}


SCHEDULE_LABEL = "com.warm-apply.job-hunt"
SCHEDULE_PROMPT = ("Use the job-hunt skill to do today's daily run, all five steps, unattended: run, judge, prepare, "
                   "draft, packet and digest. Nobody is watching, so don't ask questions: leave anything only the user "
                   "can answer for the inbox. Never submit an application or send a message. Write every jobhunt.py "
                   "command out in full as `python3 {script} --home {home} ...`. End with three lines: how many "
                   "applications are ready, the best one and why, today's Monid cost.")


def schedule_wrapper(home, agent_bin, agent, script):
    """The script launchd or cron runs: the agent's daily run, then a desktop notification (macOS)."""
    prompt = SCHEDULE_PROMPT.format(script=script, home=home.root)
    if agent == "claude":
        # Postings are untrusted text read by an agent nobody is watching: the shell may run only this skill's
        # script, and files may be written only inside the state folder.
        # No MCP servers (browser control and the rest stay off); the user's settings and login still load.
        cmd = [agent_bin, "-p", prompt, "--permission-mode", "dontAsk", "--strict-mcp-config", "--allowedTools",
               f"Bash(python3 {script}:*)", "Read", "Glob", "Grep", "Skill",
               f"Edit(/{home.root}/**)"]  # Edit rules cover every file-writing tool
    else:
        # Codex needs network for Monid, which its workspace-write sandbox blocks.
        cmd = [agent_bin, "exec", "--skip-git-repo-check", "-s", "danger-full-access", "-C", str(home.root.parent), prompt]
    log = home.root / "schedule.log"
    notify = ""
    if sys.platform == "darwin":
        notify = ("osascript -e \"display notification \\\"$msg\\\" with title \\\"Job hunt\\\"\" >/dev/null 2>&1")
    return f"""#!/bin/sh
# Daily job-hunt run, installed by `jobhunt.py schedule`. Rerun that command to change it, or `--remove`.
export JH_SCHEDULED_RUN=1
cd {shlex.quote(str(home.root.parent))} || exit 1
echo "=== $(date)"
{" ".join(shlex.quote(c) for c in cmd)}
if [ $? -eq 0 ]; then msg="Today's applications are ready in inbox.html"; else msg={shlex.quote("The daily run failed; see " + str(log))}; fi
{notify}
"""


def schedule_path_env(bins):
    """launchd and cron start with a bare PATH; keep the folders the tools were found in (nvm, Homebrew, ~/.local/bin)."""
    dirs = []
    for b in bins:
        if b:
            d = str(Path(b).parent)  # not resolved: npm's bin entries are links into node_modules
            if d not in dirs:
                dirs.append(d)
    for d in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        if d not in dirs:
            dirs.append(d)
    return ":".join(dirs)


def launchd_plist(wrapper, log, path_env, home, hour, minute, weekdays):
    times = "".join(f"<dict><key>Weekday</key><integer>{d}</integer><key>Hour</key><integer>{hour}</integer>"
                    f"<key>Minute</key><integer>{minute}</integer></dict>" for d in weekdays)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{SCHEDULE_LABEL}</string>
<key>ProgramArguments</key><array><string>/bin/sh</string><string>{html_escape(str(wrapper))}</string></array>
<key>StartCalendarInterval</key><array>{times}</array>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>{html_escape(path_env)}</string><key>JOB_HUNT_HOME</key><string>{html_escape(str(home.root))}</string></dict>
<key>StandardOutPath</key><string>{html_escape(str(log))}</string>
<key>StandardErrorPath</key><string>{html_escape(str(log))}</string>
</dict></plist>
"""


def cmd_schedule(home, args):
    if os.environ.get("JH_SCHEDULED_RUN"):
        raise Fail("A scheduled run can't change the schedule.", "Ask the user to run `schedule` themselves.")
    plist = Path.home() / "Library/LaunchAgents" / f"{SCHEDULE_LABEL}.plist"
    # Outside the state folder, read-only: the unattended agent writes in JH_HOME and must not edit what launchd runs.
    wrapper = Path.home() / ".local/share/warm-apply/scheduled-run.sh"
    marker = "# job-hunt daily run"
    mac = sys.platform == "darwin"
    if os.name == "nt":
        raise Fail("Scheduling isn't automated on Windows.",
                   "Use the Claude or Codex desktop app's scheduled tasks (references/scheduling.md), or Task Scheduler.")

    def current_crontab():
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        return proc.stdout if proc.returncode == 0 else ""

    def set_crontab(text):
        subprocess.run(["crontab", "-"], input=text, text=True, check=True, capture_output=True)

    if args.remove:
        wrapper.unlink(missing_ok=True)
        if mac:
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
            existed = plist.exists()
            plist.unlink(missing_ok=True)
            return {"removed": existed, "kind": "launchd"}
        lines = current_crontab().splitlines()
        kept = [l for l in lines if marker not in l]
        set_crontab("\n".join(kept) + ("\n" if kept else ""))
        return {"removed": len(kept) != len(lines), "kind": "cron"}

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", args.at or "")
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise Fail("--at must be a 24-hour time like 09:07.", "An odd minute avoids the top-of-hour rush.")
    hour, minute = int(m.group(1)), int(m.group(2))
    agent_bin = shutil.which(args.agent)
    if not agent_bin:
        raise Fail(f"`{args.agent}` isn't on PATH.", "Install it, or pass --agent claude|codex for the one you use.")
    monid_bin = shutil.which(os.environ.get("MONID_BIN", "monid"))
    if not monid_bin:
        raise Fail("The monid CLI is not installed.", "npm install -g @monid-ai/cli")
    script = Path(__file__).resolve()
    log = home.root / "schedule.log"
    path_env = schedule_path_env([agent_bin, monid_bin, shutil.which("node"), sys.executable, shutil.which("python3")])
    weekdays = [1, 2, 3, 4, 5] if args.days == "weekdays" else [0, 1, 2, 3, 4, 5, 6]
    out = {"agent": args.agent, "at": f"{hour:02d}:{minute:02d}", "days": args.days, "wrapper": str(wrapper),
           "log": str(log), "run_now": f"sh {shlex.quote(str(wrapper))} >> {shlex.quote(str(log))} 2>&1"}
    wrapper_text = schedule_wrapper(home, agent_bin, args.agent, script)
    if mac:
        unit = launchd_plist(wrapper, log, path_env, home, hour, minute, weekdays)
        out.update(kind="launchd", file=str(plist),
                   note="Runs while you're logged in; if the Mac was asleep at that time, it runs when it wakes.")
    else:
        dow = "1-5" if args.days == "weekdays" else "*"
        q = lambda v: shlex.quote(str(v)).replace("%", "\\%")  # cron reads a bare % as a newline
        unit = (f"{minute} {hour} * * {dow} PATH={q(path_env)} JOB_HUNT_HOME={q(home.root)} "
                f"/bin/sh {q(wrapper)} >> {q(log)} 2>&1 {marker}")
        out.update(kind="cron", line=unit, note="Runs only if the machine is on at that time.")
    if args.dry_run:
        out.update(dry_run=True, wrapper_text=wrapper_text, unit_text=unit)
        return out
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    if wrapper.exists():
        wrapper.chmod(0o755)
    wrapper.write_text(wrapper_text)
    wrapper.chmod(0o555)
    if mac:
        plist.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
        plist.write_text(unit)
        proc = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise Fail("launchctl couldn't load the schedule: " + (proc.stderr or proc.stdout).strip()[-200:],
                       f"Check {plist}, then rerun `schedule`.")
    else:
        kept = [l for l in current_crontab().splitlines() if marker not in l]
        set_crontab("\n".join(kept + [unit]) + "\n")
    out["installed"] = True
    return out


ANSWERS_TEMPLATE = {
    "first_name": "", "last_name": "", "preferred_name": "", "email": "", "phone": "",
    "location": "City, ST", "country": "United States", "linkedin": "", "github": "", "website": "",
    "current_company": "", "current_title": "", "years_experience": None,
    "work_authorized": True, "needs_sponsorship": False, "us_citizen": None, "security_clearance": "",
    "willing_to_relocate": False, "in_office_ok": True, "earliest_start": "2 weeks after an offer",
    "salary_expectation": "", "pronouns": "", "how_heard_default": "Company website",
    "self_id": {"gender": "decline", "race": "decline", "veteran": "decline", "disability": "decline"},
    "custom": {},
}

TEMPLATES = {
    "profile.md": """# Profile

TODO: the agent fills this from the user's resume, project links and answers. Facts only; every
claim in a tailored resume or note must trace back to a line here.

## Headline

## Experience
<!-- role, company, dates, then bullets with numbers the user confirmed -->

## Projects
<!-- name, link, one line on what it does, the user's part, any measurable result -->

## Skills

## Education

## Stories
<!-- short, true anecdotes the user is happy to reuse in notes and interviews -->

## Dealbreakers and preferences
""",
}


# Commands that write state take the folder's lock, so a scheduled run and a manual one never overwrite each other.
WRITES_STATE = {"spend", "init", "search", "run", "enrich", "prepare", "set", "digest", "form", "answer", "packet", "companies", "open"}


class StateLock:
    def __init__(self, root):
        self.path, self.f = Path(root) / ".lock", None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # Windows: no lock
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.path, "w")
        waited = 0
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if waited == 0:
                    print("Another job-hunt command is running (maybe the scheduled run); waiting for it.", file=sys.stderr)
                if waited >= 1800:
                    raise Fail("Another job-hunt command has held the folder for 30 minutes.",
                               f"Check for a stuck run (schedule.log), then rerun.")
                time.sleep(2)
                waited += 2

    def __exit__(self, *exc):
        if self.f:
            self.f.close()


def default_home():
    """One state folder however the agent was started: the env var, a ./job-hunt here, or ~/job-search/job-hunt."""
    if os.environ.get("JOB_HUNT_HOME"):
        return os.environ["JOB_HUNT_HOME"]
    if (Path("job-hunt") / "config.json").exists():
        return "job-hunt"
    return str(Path.home() / "job-search" / "job-hunt")


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobhunt.py", description=__doc__.splitlines()[0])
    p.add_argument("--home", default=default_home(), help="state folder (default $JOB_HUNT_HOME, else ./job-hunt if it exists, else ~/job-search/job-hunt)")
    p.add_argument("--version", action="version", version=VERSION)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the state folder with config and templates")
    sub.add_parser("check", help="verify config, profile and the Monid CLI")
    s = sub.add_parser("search", help="find, dedupe, filter and score new jobs")
    s.add_argument("--max-cost", type=float, help="USD cap for this run (default config budget.per_run_usd)")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--window", choices=["24h", "week", "month"], help="override the posting-age window")
    l = sub.add_parser("list", help="list jobs by score")
    l.add_argument("--status", help="comma-separated statuses, e.g. new,shortlisted")
    l.add_argument("--top", type=int, default=30)
    l.add_argument("--source", help="comma-separated sources, e.g. hackernews,ats_web")
    sh = sub.add_parser("show", help="one job's full record")
    sh.add_argument("id")
    sh.add_argument("--brief", action="store_true", help="cut the description to 1,500 characters")
    e = sub.add_parser("enrich", help="company signals, contact and email for chosen jobs")
    e.add_argument("ids", nargs="+")
    e.add_argument("--max-cost", type=float)
    e.add_argument("--mask", action="store_true", help=argparse.SUPPRESS)  # masking is the default now
    e.add_argument("--show-emails", action="store_true", help="print full emails (job.md always has them)")
    e.add_argument("--refresh", action="store_true", help="research again even if already enriched")
    st = sub.add_parser("set", help="change a job's status")
    st.add_argument("id")
    st.add_argument("status", nargs="?", help="new status; omit to only change --apply-url or --note")
    st.add_argument("--note")
    st.add_argument("--followed-up", action="store_true", help="record that the one follow-up was sent")
    st.add_argument("--via", help="channels used, comma-separated: referral,email,form")
    st.add_argument("--apply-url", help="correct the job's application link (e.g. an HN post that linked the homepage)")
    d = sub.add_parser("digest", help="write the approval queue for a day")
    d.add_argument("--date")
    sp = sub.add_parser("spend", help="Monid spend from the ledger")
    sp.add_argument("--reconcile", action="store_true", help="look up what Monid billed for calls logged at the listed price")
    fo = sub.add_parser("form", help="read the job's real application form and prefill it from answers.json")
    fo.add_argument("id")
    an = sub.add_parser("answer", help="set one form field's answer")
    an.add_argument("id")
    an.add_argument("field", help="field number from apply.md, key, or a unique part of its label")
    an.add_argument("text", nargs="?")
    an.add_argument("--user", action="store_true", help="the user gave this answer (not a draft)")
    an.add_argument("--remember", action="store_true", help="save it in answers.json for the same question elsewhere")
    an.add_argument("--to-user", action="store_true", help="hand the field to the user (the profile can't support an answer); text becomes the note")
    li = sub.add_parser("lint", help="check drafts: traceable numbers, length limits, filler, keyword coverage")
    li.add_argument("id")
    pk = sub.add_parser("packet", help="render resume and cover letter PDFs, fill file fields, lint, report readiness")
    pk.add_argument("id")
    sub.add_parser("stats", help="funnel, interview rate by channel, and yield by source")
    rn = sub.add_parser("run", help="daily: check, search, and a compact sheet of jobs to judge")
    rn.add_argument("--top", type=int, default=15)
    rn.add_argument("--max-cost", type=float)
    rn.add_argument("--window", choices=["24h", "week", "month"])
    pr = sub.add_parser("prepare", help="research + read the form for shortlisted jobs, in parallel")
    pr.add_argument("ids", nargs="+")
    pr.add_argument("--max-cost", type=float)
    pr.add_argument("--refresh", action="store_true")
    sub.add_parser("open", help="open the inbox page (writes it first if needed)")
    co = sub.add_parser("companies", help="recently funded companies in an industry and city, with their job boards")
    co.add_argument("--industry", required=True, help="e.g. fintech, healthcare, developer tools")
    co.add_argument("--location", help="city, e.g. New York")
    co.add_argument("--stages", default="seed,a,b,c", help="comma-separated: seed,a,b,c,d,e")
    co.add_argument("--since-days", type=int, default=120)
    co.add_argument("--limit", type=int, default=10, help="funding rounds to fetch (billed per row, about $0.06 each)")
    co.add_argument("--max-cost", type=float, default=0.7)
    sc = sub.add_parser("schedule", help="run the daily run automatically (launchd on macOS, cron on Linux)")
    sc.add_argument("--at", default="09:07", help="24-hour local time (default 09:07)")
    sc.add_argument("--days", choices=["weekdays", "daily"], default="weekdays")
    sc.add_argument("--agent", choices=["claude", "codex"], default="claude", help="the agent CLI that runs it")
    sc.add_argument("--remove", action="store_true", help="remove the schedule")
    sc.add_argument("--dry-run", action="store_true", help="show what would be installed")
    args = p.parse_args(argv)
    home = Home(args.home)
    try:
        fn = {"init": cmd_init, "check": cmd_check, "search": cmd_search, "list": cmd_list, "show": cmd_show,
              "enrich": cmd_enrich, "set": cmd_set, "digest": cmd_digest, "spend": cmd_spend, "form": cmd_form,
              "answer": cmd_answer, "lint": cmd_lint, "packet": cmd_packet, "stats": cmd_stats,
              "companies": cmd_companies, "run": cmd_run, "prepare": cmd_prepare, "open": cmd_open,
              "schedule": cmd_schedule}[args.cmd]
        if args.cmd != "init":
            home.require()
        if args.cmd in WRITES_STATE:
            with StateLock(home.root):
                out = fn(home, args)
        else:
            out = fn(home, args)
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    except Fail as f:
        print(json.dumps({"error": f.error, "hint": f.hint}, indent=2))
        return 1
    except Exception as e:  # still JSON, so the agent can read it; the traceback goes to stderr
        traceback.print_exc()
        print(json.dumps({"error": f"{type(e).__name__}: {str(e)[:300]}",
                          "hint": "Unexpected error in jobhunt.py; rerun once, and if it repeats, skip this step and tell the user."}, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
