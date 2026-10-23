#!/usr/bin/env python3
"""Offline tests for jobhunt.py, replaying recorded Monid responses through fake_monid.py.

Run: python3 -m unittest test_jobhunt.py  (from this folder)
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import jobhunt  # noqa: E402
import forms  # noqa: E402


def cli(home, *args, env=None):
    proc = subprocess.run([sys.executable, str(HERE / "jobhunt.py"), "--home", str(home), *args],
                          capture_output=True, text=True, env={**os.environ, **(env or {})})
    return proc.returncode, json.loads(proc.stdout)


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "hunt"
        self.log = Path(self.tmp.name) / "calls.jsonl"
        os.environ["MONID_BIN"] = str(HERE / "fake_monid.py")
        os.environ["FAKE_MONID_LOG"] = str(self.log)
        os.environ["JOBHUNT_OFFLINE"] = "1"
        code, out = cli(self.home, "init")
        self.assertEqual(code, 0, out)
        cfg = json.loads((self.home / "config.json").read_text())
        cfg.update({"roles": ["AI engineer"], "locations": ["San Francisco"], "salary_min": 150000,
                    "sources": {"linkedin": True, "google_jobs": True, "ats_boards": False, "ats_web": False,
                                "glassdoor": False, "hackernews": False, "x": False}})
        (self.home / "config.json").write_text(json.dumps(cfg))

    def tearDown(self):
        os.environ.pop("FAKE_MONID_LOG", None)
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_search_dedupes_filters_and_logs_cost(self):
        code, out = cli(self.home, "search")
        self.assertEqual(code, 0, out)
        self.assertGreater(out["new"], 5)
        reasons = " ".join(f["reason"] for f in out["filtered_sample"])
        self.assertIn("staffing agency", reasons)  # Harnham, a recruitment firm, is in the LinkedIn fixture
        jobs = json.loads((self.home / "jobs.json").read_text())
        self.assertEqual(len(jobs), out["unique"])
        for j in jobs.values():
            if j["status"] == "new":
                top = jobhunt.annual(j, "salary_max")
                self.assertTrue(top is None or top >= 150000, j["title"])
        # San Francisco, plus a remote-anywhere LinkedIn search because the default workplace accepts remote
        self.assertAlmostEqual(out["monid"]["spent_usd"], 2 * 0.0286 + 0.0048, places=4)
        titles = [j["title"] for j in jobs.values() if j["status"] == "new"]
        self.assertTrue(all(jobhunt.role_matches(t, ["AI engineer"]) for t in titles), titles)
        code, again = cli(self.home, "search")
        self.assertEqual(again["new"], 0)
        self.assertEqual(again["already_seen"], again["unique"])

    def test_budget_stops_paid_calls(self):
        code, out = cli(self.home, "search", "--max-cost", "0.01")
        names = [c["endpoint"] for c in self.calls()]
        self.assertNotIn("/linkedin.jobs", names)
        self.assertIn("/serp/google-jobs", names)

    def test_enrich_finds_contact_and_keeps_only_matching_domain(self):
        cli(self.home, "search")
        jobs = json.loads((self.home / "jobs.json").read_text())
        jid = next(k for k, j in jobs.items() if j["company"] == "Fluidstack")
        jobs[jid]["company_domain"] = "fluidstack.io"
        (self.home / "jobs.json").write_text(json.dumps(jobs))
        code, out = cli(self.home, "enrich", jid, "--show-emails")
        self.assertEqual(code, 0, out)
        r = out["enriched"][0]
        person = r["contact"] or r["hiring_manager"]
        self.assertEqual(person["name"], "Jamie Doe")
        self.assertEqual(person["email"], "jamie.doe@fluidstack.io")
        self.assertIn("Hunter", person["email_status"])
        self.assertEqual(r["signals"]["funding"]["latest_round"]["type"], "DEBT_FINANCING")
        self.assertTrue((Path(r["folder"]) / "job.md").exists())
        steps = [c["endpoint"] for c in self.calls()]
        self.assertLess(steps.index("/mixed_people/api_search"), steps.index("/people/match"))
        self.assertLess(steps.index("/email_finding.quickenrich"), steps.index("/email-finder"))

    def test_status_digest_and_spend(self):
        cli(self.home, "search")
        _, lst = cli(self.home, "list", "--status", "new", "--top", "2")
        jid = lst["jobs"][0]["id"]
        code, _ = cli(self.home, "set", jid, "shortlisted", "--note", "strong fit")
        self.assertEqual(code, 0)
        code, bad = cli(self.home, "set", jid, "submitted")
        self.assertEqual(code, 1)
        self.assertIn("Unknown status", bad["error"])
        _, d = cli(self.home, "digest")
        self.assertEqual(d["queue"], 1)
        self.assertIn(lst["jobs"][0]["company"], Path(d["digest"]).read_text())
        _, s = cli(self.home, "spend")
        self.assertEqual(s["calls"], 3)


class Units(unittest.TestCase):
    def test_salary_text(self):
        self.assertEqual(jobhunt.parse_salary_text("173K–250K a year"), (173000, 250000, "year"))
        self.assertEqual(jobhunt.parse_salary_text("350,000 - 450,000 USD"), (350000, 450000, "year"))
        self.assertEqual(jobhunt.parse_salary_text("70 - 100 USD")[2], "hour")
        self.assertEqual(jobhunt.parse_salary_text(""), (None, None, None))

    def test_same_job_from_two_sources_merges(self):
        a = jobhunt.blank_job(title="AI Engineer", company="Acme", location="San Francisco, CA", sources=["linkedin"], applicants=12)
        b = jobhunt.blank_job(title="AI engineer", company="ACME", location="San Francisco", sources=["google_jobs"], salary_text="$200K")
        self.assertEqual(a["id"], b["id"])
        m = jobhunt.merge(a, b)
        self.assertEqual(m["sources"], ["google_jobs", "linkedin"])
        self.assertEqual((m["applicants"], m["salary_text"]), (12, "$200K"))
        c = jobhunt.blank_job(title="AI Engineer", company="Acme", location="San Francisco Bay Area")
        d = jobhunt.blank_job(title="AI Engineer", company="Acme", location="Greater San Francisco | Remote")
        self.assertEqual({a["id"], c["id"], d["id"]}, {a["id"]})

    def test_provider_marked_down_after_auth_failure(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["MONID_BIN"] = str(HERE / "fake_monid.py")
            os.environ.pop("FAKE_MONID_LOG", None)
            cli(t, "init")
            home = jobhunt.Home(t)
            m = jobhunt.Monid(home, 1.0)
            r = m.run("ycombinator", "/search_jobs", query={"role": "software-engineer"})
            self.assertFalse(r["ok"])
            self.assertEqual(r["http"], 401)
            self.assertTrue(jobhunt.Monid(home, 1.0).is_down("ycombinator"))
            again = jobhunt.Monid(home, 1.0).run("ycombinator", "/search_jobs")
            self.assertEqual(again["skipped"], "provider marked down")

    def test_email_at_other_company_is_discarded(self):
        class Stub:
            def run(self, provider, endpoint, **kw):
                outputs = {
                    "/mixed_people/api_search": {"people": [{"id": "1", "title": "Recruiter"}]},
                    "/people/match": {"person": {"name": "Pat Lee", "title": "Recruiter", "email": "pat@oldjob.com",
                                                 "email_status": "verified"}},
                }
                return {"ok": True, "output": outputs.get(endpoint, {})}
        job = {"company": "Acme", "company_domain": "acme.com", "location": "SF"}
        c = jobhunt.find_contact(Stub(), job, jobhunt.DEFAULT_CONFIG)
        self.assertNotIn("email", c)
        self.assertEqual(c["discarded_email"], "pat@oldjob.com")

    def test_role_and_place_matching(self):
        self.assertFalse(jobhunt.role_matches("Market Research Lead", ["research engineer"]))
        self.assertTrue(jobhunt.role_matches("Research Scientist / Research Engineer", ["research engineer"]))
        self.assertFalse(jobhunt.role_matches("Staff Software Engineer, Finance", ["machine learning engineer"]))
        cfg = {**jobhunt.DEFAULT_CONFIG, "locations": ["San Francisco"], "workplace": ["hybrid", "onsite"]}
        self.assertTrue(jobhunt.place_matches("San Francisco, CA | New York City, NY", cfg))
        self.assertFalse(jobhunt.place_matches("London, UK", cfg))
        self.assertFalse(jobhunt.place_matches("Remote - US", cfg))
        self.assertTrue(jobhunt.place_matches("Remote - US", {**cfg, "workplace": ["remote"]}))

    def test_scam_signals_sink_the_score(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["data entry"]}
        at = jobhunt.now()
        ok = jobhunt.blank_job(title="Data Entry", company="Acme", description="Enter invoices into our ERP.")
        bad = jobhunt.blank_job(title="Data Entry", company="Acme", description="Message us on Telegram, paid in USDT.")
        s_ok, _ = jobhunt.score(ok, cfg, at)
        s_bad, why = jobhunt.score(bad, cfg, at)
        self.assertLess(s_bad, s_ok - 30)
        self.assertTrue(any("possible scam" in w for w in why))

    def test_warm_paths_keep_current_employees_only(self):
        profiles = {
            "u1": {"currentPosition": [{"companyName": "Acme"}], "education": [{"school": "Stanford University"}]},
            "u3": {"currentPosition": [{"companyName": "Acme"}], "education": [{"school": "MIT"}]},
            "u4": {"currentPosition": [{"companyName": "Acme Staffing Software"}], "education": [{"school": "Stanford"}]},
        }

        class Stub:
            def run(self, provider, endpoint, body=None, **kw):
                if provider == "contactout":
                    return {"ok": False, "output": None}  # ContactOut unavailable: the LinkedIn-search fallback runs
                if endpoint == "/linkedin.profile":
                    return {"ok": True, "output": {"data": profiles[body["url"]]}}
                return {"ok": True, "output": {"data": {"items": [
                    {"fullName": "Ana Ruiz", "headline": "ML Engineer @ Acme", "profileUrl": "u1"},
                    {"fullName": "Bo Chen", "headline": "Engineer at Globex (ex-Acme)", "profileUrl": "u2"},
                    {"fullName": "Cy Dal", "headline": "Designer at Acme", "profileUrl": "u3"},
                    {"fullName": "Di Eng", "headline": "SDR at Acme - Staffing Software", "profileUrl": "u4"},
                ]}}}
        cfg = {**jobhunt.DEFAULT_CONFIG, "warm_paths": ["Stanford"]}
        paths = jobhunt.find_warm_paths(Stub(), {"company": "Acme"}, cfg)
        # Bo left Acme; Cy went to MIT, not Stanford; Di works at a different "Acme"
        self.assertEqual([p["name"] for p in paths], ["Ana Ruiz"])
        self.assertTrue(paths[0]["verified"])
        self.assertTrue(jobhunt.shares({"education": [{"school": "University of California, Berkeley, Haas"}]}, "UC Berkeley"))

    def test_works_at_reads_current_employer(self):
        self.assertTrue(jobhunt.works_at("Software @ Fluidstack — Fluidstack", "Fluidstack"))
        self.assertTrue(jobhunt.works_at("Talent at Fluidstack | Ex - Palantir", "Fluidstack"))
        self.assertFalse(jobhunt.works_at("Engineer at Globex (ex-Acme)", "Acme"))
        self.assertFalse(jobhunt.works_at("Acme alum, now building", "Acme"))
        self.assertFalse(jobhunt.works_at("Prev. @ OpenAI, Stripe, Cambridge", "OpenAI"))
        self.assertTrue(jobhunt.works_at("Comms @ OpenAI || UC Berkeley MBA", "OpenAI"))

    def test_same_org_across_domains(self):
        self.assertTrue(jobhunt.same_org("securityscorecard.io", "securityscorecard.com"))
        self.assertTrue(jobhunt.same_org("eng.acme.com", "acme.com"))
        self.assertTrue(jobhunt.same_org("acme.co.uk", "acme.com"))
        self.assertFalse(jobhunt.same_org("oldjob.com", "acme.com"))

    def test_works_at_needs_the_whole_company_name(self):
        self.assertFalse(jobhunt.works_at("Owner at Plaid Daddy Entertainment", "Plaid"))
        self.assertTrue(jobhunt.works_at("Designer @ Plaid | ex-Brex", "Plaid"))
        self.assertTrue(jobhunt.works_at("Recruiter at Ramp Inc", "Ramp"))

    def test_ashby_workplace_and_multi_city_dedupe(self):
        j = jobhunt.from_ashby({"title": "Product Designer", "location": "New York", "isRemote": True,
                                "workplaceType": "Hybrid"}, "Ramp")
        self.assertEqual(j["workplace"], "hybrid")
        a = jobhunt.blank_job(title="Senior Designer", company="Acme", location="New York, NY")
        b = jobhunt.blank_job(title="Senior Designer", company="Acme", location="Austin, TX")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(jobhunt.merge(a, b)["location"], "New York, NY; Austin, TX")

    def test_place_rules(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "locations": ["New York"], "workplace": ["remote", "hybrid", "onsite"]}
        self.assertTrue(jobhunt.place_matches("New York, NY", cfg, "onsite"))
        self.assertTrue(jobhunt.place_matches("United States", cfg, "remote"))
        self.assertFalse(jobhunt.place_matches("Seattle, WA", cfg, "onsite"))
        self.assertTrue(jobhunt.place_matches("Anywhere", cfg))
        self.assertTrue(jobhunt.place_matches("Manhattan, NY", cfg, "onsite"))
        self.assertTrue(jobhunt.place_matches("New York City Metropolitan Area", {**cfg, "locations": ["New York, NY"]}, "hybrid"))

    def test_mask_covers_discarded_email(self):
        m = jobhunt.mask_contact({"email": "jane.doe@acme.com", "discarded_email": "jane@old.com"})
        self.assertEqual((m["email"], m["discarded_email"]), ("ja***@acme.com", "ja***@old.com"))

    def test_title_synonyms(self):
        self.assertTrue(jobhunt.role_matches("Sr. ML Engineer", ["machine learning engineer"]))
        self.assertFalse(jobhunt.role_matches("Senior Product Manager", ["senior product designer"]))

    def test_contactout_email_when_apollo_has_none(self):
        class Stub:
            calls = []
            def run(self, provider, endpoint, body=None, query=None, **kw):
                self.calls.append(endpoint)
                out = {"/mixed_people/api_search": {"people": [{"id": "1", "title": "Recruiter"}]},
                       "/people/match": {"person": {"name": "Pat Lee", "title": "Recruiter", "linkedin_url": "http://www.linkedin.com/in/pat"}},
                       "/email_finding.quickenrich": {"found": False},
                       "/v1/people/enrich/work-email": {"profile": {"contact_info": {"work_emails": ["pat@oldjob.com", "pat@acme.com"]}}},
                       "/email.verify": {"data": {"status": "good"}}}
                return {"ok": True, "output": out.get(endpoint, {})}
        stub = Stub()
        c = jobhunt.find_contact(stub, {"company": "Acme", "company_domain": "acme.com", "location": "SF"}, jobhunt.DEFAULT_CONFIG)
        self.assertEqual(c["email"], "pat@acme.com")
        self.assertEqual(c["email_source"], "ContactOut")
        self.assertNotIn("/email-finder", stub.calls)

    def test_contactout_warm_paths_and_hiring_manager(self):
        class Stub:
            bodies = []
            def run(self, provider, endpoint, body=None, query=None, **kw):
                self.bodies.append((endpoint, body))
                if endpoint == "/v1/people/search/work-email" and "seniority" in body:
                    return {"ok": True, "output": {"profiles": {
                        "https://linkedin.com/in/mgr": {"full_name": "Mo Grant", "title": "Engineering Manager, ML Platform", "location": "San Francisco"}}}}
                if endpoint == "/v1/people/search/work-email" and body.get("job_function"):
                    return {"ok": True, "output": {"profiles": {
                        "https://linkedin.com/in/ana": {"full_name": "Ana Ruiz", "title": "ML Engineer", "job_function": "Engineering"}}}}
                if endpoint == "/v1/people/search/work-email":
                    return {"ok": True, "output": {"profiles": {}}}
                if endpoint == "/v1/people/enrich/work-email":
                    return {"ok": True, "output": {"profile": {"contact_info": {"work_emails": ["mo@acme.com"]}}}}
                return {"ok": True, "output": {}}
        cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["machine learning engineer"], "warm_paths": ["UC Berkeley", "Stripe"],
               "job_function": "Engineering"}
        job = {"company": "Acme", "company_domain": "acme.com", "location": "San Francisco, CA"}
        stub = Stub()
        paths = jobhunt.find_warm_paths(stub, job, cfg)
        self.assertEqual([p["name"] for p in paths], ["Ana Ruiz"])
        self.assertTrue(paths[0]["verified"] and paths[0]["same_function"])
        sent = [b for e, b in stub.bodies if e == "/v1/people/search/work-email"]
        self.assertEqual(sent[0]["education"], ["UC Berkeley"])
        self.assertEqual(sent[1]["past_company"], ["Stripe"])
        self.assertTrue(all(b["page_size"] == 2 for b in sent))
        self.assertEqual(sent[0]["domain"], ["acme.com"])

    def test_hiring_manager_from_apollo_needs_title_evidence(self):
        people = [{"id": "r", "title": "Technical Recruiter"}, {"id": "h", "title": "Head of Hardware"},
                  {"id": "m", "title": "Engineering Manager, ML Platform"}]
        reveal = {"r": {"name": "Rae Cruz", "title": "Technical Recruiter", "email": "rae@acme.com", "email_status": "verified"},
                  "h": {"name": "Hal Ward", "title": "Head of Hardware"},
                  "m": {"name": "Mo Grant", "title": "Engineering Manager, ML Platform", "email": "mo@acme.com", "email_status": "verified"}}

        class Stub:
            def __init__(self, ppl):
                self.ppl, self.calls = ppl, []
            def run(self, provider, endpoint, body=None, query=None, **kw):
                self.calls.append(endpoint)
                if endpoint == "/mixed_people/api_search":
                    return {"ok": True, "output": {"people": self.ppl}}
                if endpoint == "/people/match":
                    return {"ok": True, "output": {"person": reveal[query["id"]]}}
                return {"ok": True, "output": {}}
        cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["machine learning engineer"]}
        job = {"company": "Acme", "company_domain": "acme.com", "location": "SF", "title": "ML Engineer, ML Platform", "company_size": 5000}
        stub = Stub(people)
        recruiter = jobhunt.find_contact(stub, job, cfg)
        self.assertEqual(job["hiring_manager"]["name"], "Mo Grant")
        self.assertIsNone(recruiter)  # hiring manager has an email: no paid recruiter lookup
        # big company, only an unrelated manager: no hiring-manager guess, recruiter instead
        job2 = {"company": "Acme", "company_domain": "acme.com", "location": "SF", "title": "Backend Engineer, Payments", "company_size": 5000}
        cfg2 = {**cfg, "roles": ["backend engineer"]}
        recruiter = jobhunt.find_contact(Stub(people[:2]), job2, cfg2)
        self.assertNotIn("hiring_manager", job2)
        self.assertEqual(recruiter["name"], "Rae Cruz")

    def test_agency_detection(self):
        self.assertTrue(jobhunt.is_agency({"company": "TTN Talent", "company_about": ""}))
        self.assertTrue(jobhunt.is_agency({"company": "Harnham", "company_about": "specialist Data and AI recruitment and staffing services"}))
        self.assertFalse(jobhunt.is_agency({"company": "OpenAI", "company_about": "AI research and deployment company"}))
        self.assertTrue(jobhunt.is_agency({"company": "CyberCoders", "company_about": ""}))
        self.assertTrue(jobhunt.is_agency({"company": "Stellent IT", "description": "Role is C2C, end client in NYC"}))


ANSWERS = {"first_name": "Alex", "last_name": "Rivera", "email": "alex@example.com", "phone": "555-0100",
           "location": "San Francisco, CA", "country": "United States", "github": "github.com/x", "linkedin": "",
           "current_company": "Stripe", "years_experience": 4, "work_authorized": True, "needs_sponsorship": False,
           "willing_to_relocate": False, "in_office_ok": True, "earliest_start": "4 weeks after an offer",
           "how_heard_default": "Company website", "self_id": {"gender": "decline"}, "custom": {}}


class Forms(unittest.TestCase):
    def fields(self, name):
        src = HERE / "fixtures" / name
        parse = {"form_greenhouse_anthropic.json": forms.parse_greenhouse, "form_ashby_openai.json": forms.parse_ashby,
                 "form_lever_zoox.html": forms.parse_lever}[name]
        data = src.read_text() if name.endswith(".html") else json.loads(src.read_text())
        return {f["label"]: f for f in forms.prefill(parse(data), ANSWERS, {"sources": ["linkedin"]}, {"resume": "r.pdf"})}

    def test_detect_urls(self):
        self.assertEqual(forms.detect("https://jobs.ashbyhq.com/openai/abc-123/application?x=1"), ("ashby", "openai", "abc-123"))
        self.assertEqual(forms.detect("https://job-boards.greenhouse.io/anthropic/jobs/555"), ("greenhouse", "anthropic", "555"))
        self.assertEqual(forms.detect("https://jobs.lever.co/zoox/u-1/apply"), ("lever", "zoox", "u-1"))
        self.assertEqual(forms.detect("https://acme.com/careers?gh_jid=42"), ("greenhouse", None, "42"))
        self.assertIsNone(forms.detect("https://www.linkedin.com/jobs/view/1/"))

    def test_greenhouse_prefill(self):
        f = self.fields("form_greenhouse_anthropic.json")
        self.assertEqual(f["First Name"]["answer"], "Alex")
        self.assertEqual(f["Will you now or will you in the future require employment visa sponsorship to work in the country in which the job you're applying for is located?"]["answer"], "No")
        self.assertEqual(f["Why Anthropic?"]["status"], forms.DRAFT)
        for label in ("AI Policy for Application", "Agreement to Arbitrate", "Have you ever interviewed at Anthropic before?"):
            self.assertEqual(f[label]["status"], forms.USER, label)
        self.assertIn("Decline", f["Gender"]["answer"])

    def test_ashby_prefill(self):
        f = self.fields("form_ashby_openai.json")
        self.assertEqual(f["Legal Name"]["answer"], "Alex Rivera")
        self.assertEqual(f["Are you able to work from our US office three days per week?"]["answer"], "Yes")
        self.assertEqual(f["How many years of industry experience do you have?"]["answer"], "4")
        self.assertEqual(f["When can you start a new role?"]["status"], forms.USER)  # a date field needs a real date
        self.assertEqual(f["What percentage of time do you generally enjoy spending coding?"]["status"], forms.USER)

    def test_lever_prefill(self):
        f = self.fields("form_lever_zoox.html")
        self.assertEqual(f["Full name"]["answer"], "Alex Rivera")
        self.assertEqual(f["How did you hear about us?"]["answer"], "LinkedIn")
        spons = next(v for k, v in f.items() if k.startswith("Sponsorship"))
        self.assertEqual(spons["answer"], "No")
        export = next(v for k, v in f.items() if k.startswith("Export Control"))
        self.assertEqual(export["status"], forms.USER)
        self.assertEqual(f["LinkedIn URL"]["status"], forms.OPTIONAL)

    def test_saved_answers_are_reused(self):
        a = {**ANSWERS, "custom": {"Have you ever interviewed at Anthropic before?": "No"}}
        data = json.loads((HERE / "fixtures" / "form_greenhouse_anthropic.json").read_text())
        f = {x["label"]: x for x in forms.prefill(forms.parse_greenhouse(data), a, {}, {})}
        self.assertEqual(f["Have you ever interviewed at Anthropic before?"]["answer"], "No")


class Fit(unittest.TestCase):
    def test_years_and_levels(self):
        self.assertEqual(jobhunt.years_required("You have 5+ years of backend experience and 2 years with Go experience"), 5)
        self.assertEqual(jobhunt.years_required("Experience: 7+ years building systems"), 7)
        self.assertIsNone(jobhunt.years_required("Join a 10-year-old company"))
        self.assertEqual(jobhunt.title_level("Staff Software Engineer")[0], "staff")
        self.assertEqual(jobhunt.title_level("Sr. ML Engineer")[0], "senior")
        self.assertEqual(jobhunt.title_level("Machine Learning Engineer"), (None, None))

    def test_knockouts(self):
        job = {"description": "We are unable to sponsor visas for this role. 3+ years of experience."}
        self.assertEqual(jobhunt.knockout(job, {"needs_sponsorship": True}), "no visa sponsorship")
        self.assertIsNone(jobhunt.knockout(job, {"needs_sponsorship": False}))
        self.assertEqual(jobhunt.knockout({"description": "Active TS/SCI clearance required."}, {}), "security clearance required")
        self.assertEqual(jobhunt.knockout({"description": "10+ years of experience in distributed systems"},
                                          {"years_experience": 4}), "asks 10+ years (you have 4)")

    def test_scam_ignores_products_named_whatsapp(self):
        self.assertIsNone(jobhunt.SCAM_PATTERN.search("Build ranking for WhatsApp and Instagram feeds."))
        self.assertTrue(jobhunt.SCAM_PATTERN.search("Please contact us on WhatsApp for an interview."))

    def test_search_window_narrows_once_daily(self):
        self.assertEqual(jobhunt.search_window(None, "week"), "week")
        self.assertEqual(jobhunt.search_window(jobhunt.iso(jobhunt.now()), "week"), "24h")
        old = jobhunt.iso(jobhunt.now() - jobhunt.dt.timedelta(days=3))
        self.assertEqual(jobhunt.search_window(old, "month"), "week")

    def test_skills_raise_the_score(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["ML engineer"], "skills": ["Python", "PyTorch", "Spark"]}
        a = jobhunt.blank_job(title="ML Engineer", company="A", description="Python, PyTorch and Spark daily.")
        b = jobhunt.blank_job(title="ML Engineer", company="B", description="Java and C# daily.")
        self.assertGreater(jobhunt.score(a, cfg, jobhunt.now())[0], jobhunt.score(b, cfg, jobhunt.now())[0] + 20)


class Applications(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "hunt"
        cli(self.home, "init")
        (self.home / "answers.json").write_text(json.dumps(ANSWERS))
        (self.home / "profile.md").write_text("Cut handling time from 11 to 7 minutes for ~400 agents. 92% auto-match on 1.2M receipts.\n" * 10)
        jobs = {"abc1234567": {**jobhunt.blank_job(title="ML Engineer", company="Acme", description="Python. 3+ years experience."),
                               "id": "abc1234567", "status": "drafted"}}
        (self.home / "jobs.json").write_text(json.dumps(jobs))
        self.folder = jobhunt.app_folder(jobhunt.Home(self.home), jobs["abc1234567"])
        data = json.loads((HERE / "fixtures" / "form_greenhouse_anthropic.json").read_text())
        fields = forms.prefill(forms.parse_greenhouse(data), ANSWERS, {}, {})
        (self.folder / "application.json").write_text(json.dumps({"ats": "greenhouse", "apply_url": "u", "fields": fields, "warnings": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_answer_validates_options_and_remembers(self):
        code, out = cli(self.home, "answer", "abc123", "interviewed at Anthropic", "maybe")
        self.assertEqual(code, 1)
        code, out = cli(self.home, "answer", "abc123", "interviewed at Anthropic", "no", "--user", "--remember")
        self.assertEqual((code, out["answer"]), (0, "No"))
        saved = json.loads((self.home / "answers.json").read_text())["custom"]
        self.assertEqual(saved["Have you ever interviewed at Anthropic before?"], "No")
        code, out = cli(self.home, "answer", "abc123", "Agreement to Arbitrate", "I understand", "--remember")
        self.assertNotIn("Agreement to Arbitrate", json.loads((self.home / "answers.json").read_text())["custom"])

    def test_to_user_unblocks_a_draft_field(self):
        code, out = cli(self.home, "answer", "abc123", "Why Anthropic?", "--to-user")
        self.assertEqual((code, out["status"]), (0, "user"))
        self.assertNotIn("Why Anthropic?", out["required_unanswered"])

    def test_keyword_match_is_reported(self):
        cfg = json.loads((self.home / "config.json").read_text())
        cfg["skills"] = ["Python", "Kubernetes", "Spark"]
        (self.home / "config.json").write_text(json.dumps(cfg))
        jobs = json.loads((self.home / "jobs.json").read_text())
        jobs["abc1234567"]["description"] = "We use Python, Kubernetes and Spark."
        (self.home / "jobs.json").write_text(json.dumps(jobs))
        (self.folder / "resume.md").write_text("# Alex\n- Built Python services on Kubernetes.\n")
        code, out = cli(self.home, "lint", "abc123")
        self.assertEqual(out["keyword_match"], {"in_resume": 2, "of": 3, "missing": ["Spark"]})

    def test_lint_ignores_numbers_inside_links(self):
        (self.folder / "referral.md").write_text("Fellow alum here. Role: https://job-boards.greenhouse.io/acme/jobs/6143552004 Could you refer me?")
        code, out = cli(self.home, "lint", "abc123")
        self.assertFalse(any("6143552004" in e for e in out["errors"]))

    def test_lint_flags_untraceable_numbers_and_limits(self):
        (self.folder / "resume.md").write_text("# Alex\n- Cut handling time from 11 to 7 minutes for ~400 agents.\n- Saved $2.3M a year.\n")
        (self.folder / "referral.md").write_text("Hi! " + "word " * 120)
        code, out = cli(self.home, "lint", "abc123")
        errs = " ".join(out["errors"])
        self.assertIn("$2.3M", errs)
        self.assertNotIn("'400'", errs)
        self.assertIn("referral.md", errs)
        self.assertIn("Why Anthropic?", errs)  # required draft field still empty

    def test_stats_by_channel(self):
        h = jobhunt.Home(self.home)
        jobs = h.jobs()
        j = jobs["abc1234567"]
        j["status"], j["via"] = "interviewing", ["referral"]
        j["history"] = [{"ts": jobhunt.iso(jobhunt.now()), "from": "drafted", "to": "applied"}]
        h.save_jobs(jobs)
        code, out = cli(self.home, "stats")
        self.assertEqual(out["by_channel"]["referral"], {"applied": 1, "interviews": 1, "interview_rate": 1.0})


class Companies(unittest.TestCase):
    def test_recent_rounds_become_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"MONID_BIN": str(HERE / "fake_monid.py"), "JOBHUNT_OFFLINE": "1"}
            cli(tmp, "init", env=env)
            code, out = cli(tmp, "companies", "--industry", "fintech", "--location", "New York", "--limit", "3", env=env)
            self.assertEqual(code, 0, out)
            self.assertEqual([c["company"] for c in out["companies"]], ["Mural", "Prism Data", "Meow"])
            self.assertEqual(out["companies"][0]["round"], "SERIES_A")


class Round2(unittest.TestCase):
    def test_based_in_city_question(self):
        a = {**ANSWERS, "location": "Brooklyn, NY", "willing_to_relocate": False}
        f = forms.prefill([forms.field("q", "Are you based in NYC or open to relocating to NYC?", "select", True, ["Yes", "No"])], a, {}, {})
        self.assertEqual(f[0]["answer"], "Yes")
        f = forms.prefill([forms.field("q", "Are you based in Austin or open to relocating?", "select", True, ["Yes", "No"])], a, {}, {})
        self.assertEqual(f[0]["answer"], "No")

    def test_remote_abroad_is_filtered_for_us_users(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "locations": ["New York"], "workplace": ["remote", "hybrid"]}
        self.assertFalse(jobhunt.place_matches("Remote UK", cfg, "remote"))
        self.assertTrue(jobhunt.place_matches("Remote - US", cfg, "remote"))

    def test_clearance_none_means_no_clearance(self):
        job = {"description": "Active Secret clearance required."}
        self.assertEqual(jobhunt.knockout(job, {"security_clearance": "none"}), "security clearance required")
        self.assertIsNone(jobhunt.knockout(job, {"security_clearance": "Secret"}))

    def test_greenhouse_skips_hidden_geo_fields(self):
        d = {"questions": [], "location_questions": [
            {"label": "Location (City)", "required": True, "fields": [{"name": "location", "type": "input_text"}]},
            {"label": "Latitude", "required": True, "fields": [{"name": "latitude", "type": "input_hidden"}]}]}
        self.assertEqual([f["label"] for f in forms.parse_greenhouse(d)], ["Location (City)"])


class Round3(unittest.TestCase):
    def test_plurals_and_serp_titles(self):
        self.assertTrue(jobhunt.role_matches("We're hiring product designers and engineers", ["product designer"]))
        self.assertEqual(jobhunt.company_from_serp_title("Product Designer @ Numeric", "ashby", "numeric"), "Numeric")
        self.assertEqual(jobhunt.company_from_serp_title("Job Application for Designer at Acme Corp", "greenhouse", "acme"), "Acme Corp")
        self.assertEqual(jobhunt.company_from_serp_title("Zoox - Senior Designer", "lever", "zoox"), "Zoox")

    def test_hackernews_parsing(self):
        thread = {"hits": [{"objectID": "1", "title": "Ask HN: Who is hiring? (October 2026)"}]}
        comments = {"hits": [
            {"objectID": "2", "parent_id": 1, "story_id": 1, "created_at": "2026-10-02T00:00:00Z", "author": "founder1",
             "comment_text": "Coast | <a href=\"https:&#x2F;&#x2F;coastpay.com&#x2F;\">https://coastpay.com/</a> | Full-time | Hybrid in New York City, NY<p>We need product designers."},
            {"objectID": "3", "parent_id": 2, "story_id": 1, "comment_text": "a reply, not a post"},
            {"objectID": "4", "parent_id": 1, "story_id": 1, "comment_text": "Acme | Backend Engineer | Remote<p>Go and Postgres."}]}
        orig = forms._get_json
        forms._get_json = lambda url, **kw: thread if "search_by_date" in url else comments
        try:
            cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["product designer"]}
            jobs, notes = jobhunt.search_hackernews(cfg)
        finally:
            forms._get_json = orig
        self.assertEqual([(j["company"], j["title"]) for j in jobs], [("Coast", "Product Designer")])
        self.assertEqual(jobs[0]["apply_url"], "https://coastpay.com/")
        self.assertEqual(jobs[0]["hiring_team"][0]["name"], "founder1")

    def test_run_prepare_digest_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"MONID_BIN": str(HERE / "fake_monid.py"), "JOBHUNT_OFFLINE": "1"}
            cli(tmp, "init", env=env)
            cfg = json.loads((Path(tmp) / "config.json").read_text())
            cfg.update({"roles": ["AI engineer"], "locations": ["San Francisco"], "workplace": ["hybrid", "onsite"],
                        "sources": {"linkedin": True, "google_jobs": False, "ats_boards": False, "ats_web": False,
                                    "hackernews": False, "x": False}})
            (Path(tmp) / "config.json").write_text(json.dumps(cfg))
            (Path(tmp) / "answers.json").write_text(json.dumps({**ANSWERS, "years_experience": 12}))
            code, out = cli(tmp, "run", "--top", "5", env=env)
            self.assertEqual(code, 0, out)
            self.assertTrue(out["to_judge"] and "posting" in out["to_judge"][0])
            jid = out["to_judge"][0]["id"]
            cli(tmp, "set", jid, "shortlisted", "--note", "fits", env=env)
            code, out = cli(tmp, "prepare", jid, env=env)
            self.assertEqual(code, 0, out)
            self.assertEqual(out["prepared"][0]["id"], jid)
            code, out = cli(tmp, "digest", env=env)
            html = Path(out["inbox"]).read_text()
            self.assertIn("Open application", html)
            self.assertIn("fits", html)


class Round4(unittest.TestCase):
    def test_questions_starting_with_name_are_not_the_name_field(self):
        fields = [forms.field("q1", "Name a specific open-source ML framework you've used and one limitation you hit.", "longtext", True),
                  forms.field("q2", "Name", "text", True),
                  forms.field("q3", "Do you have unrestricted work authorization in the United States?", "boolean", True)]
        f = {x["key"]: x for x in forms.prefill(fields, ANSWERS, {}, {})}
        self.assertEqual(f["q1"]["status"], forms.DRAFT)
        self.assertIsNone(f["q1"]["answer"])
        self.assertEqual(f["q2"]["answer"], "Alex Rivera")
        self.assertEqual(f["q3"]["answer"], "Yes")

    def test_europe_hours_remote_is_filtered(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "locations": ["San Francisco"], "workplace": ["remote", "hybrid", "onsite"]}
        self.assertFalse(jobhunt.place_matches("HYBRID (Berlin) or REMOTE (CET ±2h)", cfg))
        self.assertTrue(jobhunt.place_matches("REMOTE (US time zones)", cfg))

    def test_hourly_and_gig_platforms_score_low(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "roles": ["ML engineer"], "salary_min": 180000}
        gig = jobhunt.blank_job(title="ML Engineer", company="Alignerr", salary_text="90 - 120 USD", salary_min=90, salary_max=120, salary_period="hour")
        job = jobhunt.blank_job(title="ML Engineer", company="Acme", salary_min=190000, salary_max=230000, salary_period="year")
        self.assertGreater(jobhunt.score(job, cfg, jobhunt.now())[0], jobhunt.score(gig, cfg, jobhunt.now())[0] + 30)

    def test_budget_skips_are_reported_and_setup_is_separate(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["MONID_BIN"] = str(HERE / "fake_monid.py")
            cli(t, "init")
            home = jobhunt.Home(t)
            with open(home.ledger_path, "w") as f:
                f.write(json.dumps({"ts": jobhunt.iso(jobhunt.now()), "provider": "fundable", "endpoint": "/deals",
                                    "purpose": "setup", "ok": True, "cost": 5.0}) + "\n")
            self.assertEqual(home.spent_today(), 0)
            m = jobhunt.Monid(home, 0.01)
            r = m.run("apollo", "/people/match", query={"id": "1"}, step="reveal contact: Acme")
            self.assertEqual(r["skipped"], "budget")
            self.assertEqual(m.summary()["skipped_for_budget"], ["reveal contact: Acme"])

    def test_schedule_dry_run_pins_paths_and_limits_the_shell(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp, self.home = tmp, Path(tmp.name) / "hunt"
        os.environ["MONID_BIN"] = str(HERE / "fake_monid.py")
        cli(self.home, "init")
        bindir = Path(self.tmp.name) / "bin"
        bindir.mkdir()
        (bindir / "claude").write_text("#!/bin/sh\n")
        (bindir / "claude").chmod(0o755)
        code, out = cli(self.home, "schedule", "--at", "9:07", "--dry-run", env={"PATH": f"{bindir}:{os.environ['PATH']}"})
        self.assertEqual(code, 0, out)
        self.assertEqual(out["at"], "09:07")
        script = str((HERE / "jobhunt.py").resolve())
        self.assertIn(f"'Bash(python3 {script}:*)'", out["wrapper_text"])
        self.assertIn("--permission-mode dontAsk", out["wrapper_text"])
        self.assertIn(f"'Edit(/{self.home.resolve()}/**)'", out["wrapper_text"])
        self.assertIn("JH_SCHEDULED_RUN=1", out["wrapper_text"])
        self.assertNotIn(str(self.home), out["wrapper"])  # what launchd runs lives outside the folder the agent writes
        self.assertIn(str(bindir), out["unit_text"])  # launchd/cron get the folder the agent was found in
        self.assertIn(str(self.home.resolve()), out["unit_text"])
        self.assertFalse((self.home / "scheduled-run.sh").exists())
        code, out = cli(self.home, "schedule", "--at", "25:00", "--dry-run", env={"PATH": f"{bindir}:{os.environ['PATH']}"})
        self.assertEqual(code, 1)
        code, out = cli(self.home, "schedule", "--dry-run", env={"PATH": f"{bindir}:{os.environ['PATH']}", "JH_SCHEDULED_RUN": "1"})
        self.assertEqual(code, 1)  # a scheduled run can't reschedule itself


class Round5(unittest.TestCase):
    """Defects found in the pre-publication review."""

    A = {"first_name": "Priya", "last_name": "Nair", "phone": "(555) 010-0142", "work_authorized": True,
         "needs_sponsorship": False, "country": "United States", "years_experience": 4, "current_company": "Datadog"}

    def answer(self, label, options=None, a=None):
        f = [{"label": label, "key": "q", "type": "select" if options else "text", "options": options or [],
              "required": True, "section": "main"}]
        forms.prefill(f, a or self.A, {}, {})
        return f[0].get("answer")

    def test_work_authorization_with_sponsorship_wording(self):
        self.assertEqual(self.answer("Are you legally authorized to work in the United States without the need for employer sponsorship?"), "Yes")
        self.assertEqual(self.answer("Will you now or in the future require visa sponsorship?"), "No")
        self.assertIsNone(self.answer("Are you legally authorized to work in the United Kingdom?"))
        self.assertIsNone(self.answer("Will you require sponsorship?", ["I will require sponsorship now", "I will not require sponsorship"]))
        self.assertEqual(self.answer("Will you require sponsorship?", ["Yes, now", "Yes, in the future", "No"]), "No")

    def test_name_phone_and_years_rules_stay_narrow(self):
        self.assertNotEqual(self.answer("Name of referrer"), "Priya Nair")
        self.assertNotEqual(self.answer("Describe your mobile development experience"), self.A["phone"])
        self.assertIsNone(self.answer("How many years of Python experience do you have?"))
        self.assertEqual(self.answer("Full name"), "Priya Nair")
        self.assertEqual(self.answer("Phone number"), self.A["phone"])
        self.assertEqual(self.answer("Name of current employer"), "Datadog")

    def test_first_word_slug_only_for_suffixes(self):
        self.assertNotIn("mercury", forms.slug_candidates("Mercury Insurance"))
        self.assertIn("anthropic", forms.slug_candidates("Anthropic PBC"))

    def test_us_wide_location_drops_onsite_abroad(self):
        cfg = {**jobhunt.DEFAULT_CONFIG, "locations": ["United States"], "workplace": ["onsite", "hybrid", "remote"]}
        self.assertFalse(jobhunt.place_matches("London, England, United Kingdom", cfg))
        self.assertFalse(jobhunt.place_matches("Bengaluru, India", cfg, "onsite"))
        self.assertTrue(jobhunt.place_matches("Austin, TX", cfg))
        la = {**cfg, "locations": ["LA"]}
        self.assertFalse(jobhunt.place_matches("Atlanta, GA", la))
        london = {**cfg, "locations": ["London"]}
        self.assertFalse(jobhunt.place_matches("Remote (US only)", london, "remote"))

    def test_compound_titles_match(self):
        self.assertTrue(jobhunt.role_matches("Senior Front-End Engineer", ["frontend engineer"]))
        self.assertTrue(jobhunt.role_matches("Full Stack Developer", ["fullstack engineer"]) or
                        jobhunt.role_matches("Full Stack Engineer", ["full-stack engineer"]))

    def test_hosting_links_are_not_the_employer(self):
        self.assertIsNone(jobhunt.employer_domain(["https://jobs.ashbyhq.com/acme/123", "https://omega.notion.site/jobs"]))
        self.assertEqual(jobhunt.employer_domain(["https://www.ycombinator.com/companies/acme", "https://acme.dev/careers"]), "acme.dev")

    def test_knockouts_and_clearance_flag(self):
        cand = {"needs_sponsorship": True}
        self.assertTrue(jobhunt.knockout({"description": "Candidates must not require visa sponsorship now or in the future."}, cand))
        self.assertTrue(jobhunt.knockout({"description": "This role is open to US citizens only."}, {"us_citizen": False}))
        self.assertIsNone(jobhunt.knockout({"description": "Python."}, {"security_clearance": True}))

    def test_ranges_links_location_and_self_id(self):
        a = {**self.A, "linkedin": "", "github": "https://github.com/p", "location": "Brooklyn, NY", "self_id": {"gender": "decline"}}
        self.assertEqual(forms.pick_range(["0-5 years", "5-10 years"], 4), "0-5 years")
        self.assertEqual(forms.pick_range(["Less than 1 year", "1-2 years", "2-3 years", "3+ years"], 4), "3+ years")
        self.assertEqual(self.answer("LinkedIn profile, personal website, or GitHub URL", a=a), "https://github.com/p")
        f = [{"label": "Your Location", "key": "l", "type": "location", "options": [], "required": True, "section": "main"},
             {"label": "What is your gender identity?", "key": "g", "type": "select", "required": True, "section": "main",
              "options": ["Man", "Woman", "I prefer not to answer"]}]
        forms.prefill(f, a, {}, {})
        self.assertEqual([x.get("answer") for x in f], ["Brooklyn, NY", "I prefer not to answer"])

    def test_lint_catches_counts_and_unconfirmed_tools(self):
        with tempfile.TemporaryDirectory() as t:
            cli(t, "init")
            home = jobhunt.Home(t)
            (home.root / "profile.md").write_text("# Profile\nBuilt dashboards in React at Datadog; cut load time from 4.1s to 1.8s.\n" * 20)
            job = jobhunt.blank_job(title="Frontend Engineer", company="Acme", description="We use Kubernetes and React.")
            home.save_jobs({job["id"]: job})
            folder = jobhunt.app_folder(home, job)
            (folder / "resume.md").write_text("# Priya\n- Built React dashboards at Datadog; led a team of 12 engineers using Kubernetes.\n")
            code, out = cli(t, "lint", job["id"])
            errs = " ".join(out["errors"])
            self.assertIn("12 engineers", errs)
            self.assertIn("kubernetes", errs)
            self.assertNotIn("react", errs)

    def test_multiselect_answers_and_apply_url(self):
        with tempfile.TemporaryDirectory() as t:
            cli(t, "init")
            home = jobhunt.Home(t)
            job = jobhunt.blank_job(title="Engineer", company="Acme")
            home.save_jobs({job["id"]: job})
            app = {"ats": "ashby", "apply_url": "https://x", "fields": [
                {"label": "Tools", "key": "t", "type": "multiselect", "options": ["TypeScript", "Node.js", "React"],
                 "required": True, "section": "main", "status": "user"}]}
            jobhunt.save_application(home, job, app)
            code, out = cli(t, "answer", job["id"], "Tools", "TypeScript; React")
            self.assertEqual((code, out["answer"]), (0, "TypeScript; React"))
            code, out = cli(t, "answer", job["id"], "Tools", "TypeScript; Rust")
            self.assertEqual(code, 1)
            code, out = cli(t, "set", job["id"], "--apply-url", "https://acme.dev/jobs/1")
            self.assertEqual(out["apply_url"], "https://acme.dev/jobs/1")

    def test_non_latin_names_get_distinct_ids(self):
        self.assertNotEqual(jobhunt.job_key("株式会社メルカリ", "ソフトウェアエンジニア"), jobhunt.job_key("楽天グループ株式会社", "ソフトウェアエンジニア"))


if __name__ == "__main__":
    unittest.main()
