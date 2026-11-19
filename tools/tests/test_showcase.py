import csv
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)

import showcase_lib as L  # noqa: E402
import gradescope_import as G  # noqa: E402
import build_showcase as B  # noqa: E402
import synthetic  # noqa: E402

INTERESTS = L.load_interests(os.path.join(os.path.dirname(TOOLS), "showcase", "data", "interests.json"))
HAVE_THUMBS = bool(L._which("sips", ["/usr/bin/sips"]) and L._which("cwebp", ["/opt/homebrew/bin/cwebp"]))
quiet = lambda *a, **k: None  # noqa: E731


class UrlTests(unittest.TestCase):
    def bad(self, u):
        ok, why = L.validate_url(u)
        self.assertFalse(ok, "%r should be rejected" % (u[:60],))

    def test_rejected(self):
        for u in ["javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html,<script>", "vbscript:x", "file:///etc/passwd",
                  "http://example.com", "//evil.com/x", "https://user:pass@example.com/", "https://user@example.com/",
                  "https://example.com/a b", "https://example.com/\tx", "https://example.com/\nx", " ", "", "ftp://example.com",
                  "https://example.com/" + "a" * 2100, "https://exa\u200bmple.com/", "https://example.com/\u202eabc",
                  "https://example.com/caf\u00e9", "https://127.0.0.1/", "https://localhost/", "https://example.com:8443/",
                  "https://www.gradescope.com/x", "files/../secret.pdf", "/etc/passwd", "files//x.pdf", "files\\x.pdf",
                  "other/x.pdf", "https:/example.com", "https://", "https://example"]:
            self.bad(u)
        self.assertFalse(L.validate_url(None)[0])

    def test_accepted_and_normalized(self):
        self.assertEqual(L.validate_url("  https://Example.COM/Path?utm_source=a&x=1&fbclid=z&gclid=q&utm_medium=m  ")[1],
                         "https://example.com/Path?x=1")
        self.assertEqual(L.validate_url("https://example.com")[1], "https://example.com")
        self.assertEqual(L.validate_url("files/abc.pdf"), (True, "files/abc.pdf"))
        self.assertEqual(L.validate_url("thumbs/abc.webp"), (True, "thumbs/abc.webp"))
        self.assertEqual(L.validate_url("files/abc-poster.webp"), (True, "files/abc-poster.webp"))
        ok, v = L.validate_url("https://b\u00fccher.example/")  # IDN host converted to punycode
        self.assertTrue(ok)
        self.assertTrue(v.startswith("https://xn--"))
        self.assertFalse(L.validate_url("files/x.pdf", allow_relative=False)[0])

    def test_kinds(self):
        k = L.infer_kind
        self.assertEqual(k("https://github.com/a/b"), "git")
        self.assertEqual(k("https://claude.ai/artifact/x"), "artifact")
        self.assertEqual(k("https://claude.site/artifacts/x"), "artifact")
        self.assertEqual(k("https://claude.ai/chat/x"), "website")
        self.assertEqual(k("https://a.com/p.PDF"), "pdf")
        self.assertEqual(k("https://a.com/p.jpeg"), "image")
        self.assertEqual(k("https://youtu.be/x"), "video")
        self.assertEqual(k("https://docs.google.com/document/d/1"), "doc")
        self.assertEqual(k("https://x.streamlit.app/"), "dashboard")
        self.assertEqual(k("https://example.com/"), "website")
        self.assertEqual(k("https://notgithub.com/"), "website")

    def test_parse_links(self):
        links, errs = L.parse_links("git | https://github.com/a/b | Code\nhttps://example.com/x?utm_x=1\n"
                                    "javascript:alert(1)\nbogus | https://a.com | L\nwebsite | http://a.com | no")
        self.assertEqual([l["kind"] for l in links], ["git", "website"])
        self.assertEqual(links[1]["url"], "https://example.com/x")
        self.assertEqual(len(errs), 3)


class MiscLibTests(unittest.TestCase):
    def test_slug_collisions(self):
        taken = set()
        got = []
        for _ in range(4):
            s = L.unique_slug("Pantry Pilot!", taken)
            taken.add(s)
            got.append(s)
        self.assertEqual(got, ["pantry-pilot", "pantry-pilot-2", "pantry-pilot-3", "pantry-pilot-4"])
        long = L.unique_slug("x" * 200, {"x" * 48})
        self.assertTrue(L.SLUG_RE.match(long))
        self.assertTrue(L.SLUG_RE.match(L.unique_slug("\u2603\u2603", set())))
        self.assertEqual(L.slugify("Caf\u00e9 R\u00e9sum\u00e9"), "cafe-resume")

    def test_csv_leading_apostrophe_roundtrip(self):
        for v in ("'=1+1", "'abc", "''", "'", "=1+1", "'-x", "plain"):
            self.assertEqual(L.csv_unsafe(L.csv_safe(v)), v, v)
        self.assertEqual(L.csv_safe("=1+1"), "'=1+1")
        self.assertEqual(L.csv_safe("'=1+1"), "''=1+1")
        self.assertEqual(L.csv_unsafe("'=1+1"), "=1+1")
        self.assertEqual(L.csv_unsafe("''=1+1"), "'=1+1")

    def test_csv_safety(self):
        for s in ["=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx"]:
            self.assertEqual(L.csv_safe(s), "'" + s)
            self.assertEqual(L.csv_unsafe(L.csv_safe(s)), s)
        self.assertEqual(L.csv_safe("hello"), "hello")
        self.assertEqual(L.csv_unsafe("'quoted"), "'quoted")  # not ours; leave alone
        self.assertEqual(L.csv_safe(None), "")

    def test_yaml_parser(self):
        d = L.parse_gradescope_yaml(synthetic.METADATA)
        self.assertEqual(sorted(d), ["submission_1001", "submission_1002", "submission_1003"])
        subs = d["submission_1001"]["submitters"]
        self.assertEqual([s["name"] for s in subs], ["Test Student One", "Test Student Two"])
        self.assertEqual(subs[0]["sid"], "99990001")
        self.assertEqual(d["submission_1001"]["score"], 10.0)
        self.assertIn("running coach", d["submission_1002"]["answers"]["q1"])

    def test_yaml_tolerant(self):
        for junk in ["", "::::", "- - - -", "a: [", "\x00\x01", "k: |\n", "x:\n  - \n  -\n", "a:\n" + "  " * 200 + "b: 1",
                     "!!ruby/object:Foo\n  :x: 1", "key: 'unterminated", ":a: !ruby/hash:X\n  :b: 2"]:
            self.assertIsInstance(L.parse_gradescope_yaml(junk), dict)
        d = L.parse_gradescope_yaml("a:\n- 1\n- two\nb: \"q\\\"x\"\nc: ~\nd:\n  e: true\n")
        self.assertEqual(d, {"a": [1, "two"], "b": 'q"x', "c": None, "d": {"e": True}})

    def test_tags_and_tools(self):
        tags = L.suggest_tags(INTERESTS, "Marathon Coach", "Plans workouts for runners", "gym training")
        self.assertEqual(tags[0], "fitness")
        self.assertLessEqual(len(tags), 3)
        self.assertEqual(L.suggest_tags(INTERESTS, "zzz"), [])
        tools = L.detect_tools(INTERESTS, "Built with Claude Code and MCP; also ChatGPT")
        self.assertEqual(tools, ["Claude Code", "ChatGPT", "MCP"])
        self.assertNotIn("Claude", tools)


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_zip_slip_and_symlink(self):
        z = synthetic.build_export_zip(os.path.join(self.tmp, "e.zip"))
        dest = os.path.join(self.tmp, "out")
        os.makedirs(dest)
        warns = G.safe_extract_zip(z, dest)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil_zipslip.txt")))
        self.assertFalse(os.path.exists("/tmp/evil_abs_zipslip.txt"))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil_backslash.txt")))
        self.assertFalse(os.path.lexists(os.path.join(dest, "export", "submission_1003", "link_to_etc")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "export", "submission_1001", "poster.pdf")))
        text = " ".join(warns)
        self.assertIn("unsafe path", text)
        self.assertIn("symlink", text)

    def test_size_caps(self):
        import zipfile
        z = os.path.join(self.tmp, "big.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.bin", b"\0" * (2 * 1024 * 1024))
        old = G.MAX_FILE_BYTES
        G.MAX_FILE_BYTES = 1024 * 1024
        try:
            os.makedirs(os.path.join(self.tmp, "o1"))
            w = G.safe_extract_zip(z, os.path.join(self.tmp, "o1"))
            self.assertTrue(any("oversized" in x for x in w))
        finally:
            G.MAX_FILE_BYTES = old
        old = G.MAX_TOTAL_BYTES
        G.MAX_TOTAL_BYTES = 1024
        os.makedirs(os.path.join(self.tmp, "o2"))
        try:
            with self.assertRaises(G.UnsafeExport):
                G.safe_extract_zip(z, os.path.join(self.tmp, "o2"))
        finally:
            G.MAX_TOTAL_BYTES = old


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.zip = synthetic.build_export_zip(os.path.join(self.tmp, "export.zip"))
        self.csv = os.path.join(self.tmp, "work", "review.csv")
        self.site = os.path.join(self.tmp, "site")
        for d in ("data", "thumbs", "files"):
            os.makedirs(os.path.join(self.site, d))
        shutil.copy(os.path.join(os.path.dirname(TOOLS), "showcase", "data", "interests.json"),
                    os.path.join(self.site, "data", "interests.json"))

    def run_import(self):
        return G.import_export(self.zip, self.csv, log=quiet)

    def test_import_rows(self):
        res = self.run_import()
        self.assertEqual(res["rows"], 3)
        fields, rows = G.read_review(self.csv)
        self.assertEqual(fields[:len(L.CSV_COLUMNS)], L.CSV_COLUMNS)
        with open(self.csv, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        for needle in (b"example.edu", b"99990001", b"test.one"):
            self.assertNotIn(needle, raw)
        by = {r["submission_id"]: r for r in rows}
        r1 = by["1001"]
        self.assertEqual(r1["publish"], "no")
        self.assertEqual(r1["students"], "Test Student One; Test Student Two")
        self.assertEqual(r1["include_poster"], "yes")
        self.assertTrue(r1["title"])
        self.assertIn("git | https://github.com/example/pantry-pilot", r1["links"])
        self.assertNotIn("javascript", r1["summary"])
        self.assertEqual(by["1003"]["title"], "")
        self.assertIn("artifact | https://claude.ai/artifact/abc123", r1["links"])
        self.assertIn("website | https://example.com/live-demo?id=7", r1["links"])
        self.assertNotIn("http://insecure", r1["links"])
        self.assertNotIn("gradescope", r1["links"])
        self.assertIn("rejected", r1["notes"])
        self.assertEqual(by["1002"]["include_poster"], "yes")
        self.assertIn("workouts", by["1002"]["summary"])
        self.assertIn("fitness", by["1002"]["tags"])
        self.assertEqual(by["1003"]["include_poster"], "no")
        self.assertIn("no PDF poster", by["1003"]["notes"])
        # formula-injection name: stored with a quote prefix on disk, original after read
        self.assertTrue(by["1002"]["students"].startswith("="))
        self.assertIn(b"'=HYPERLINK", raw)
        ids = [r["id"] for r in rows if r["id"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_merge_preserves_edits(self):
        self.run_import()
        fields, rows = G.read_review(self.csv)
        for r in rows:
            if r["submission_id"] == "1001":
                r["publish"] = "yes"
                r["title"] = "My Edited Title"
                r["summary"] = "Human summary"
                r["tags"] = ""      # blank cell may be refilled
        G.write_review(self.csv, rows)
        res = self.run_import()
        self.assertEqual(res["rows"], 3)
        _, rows2 = G.read_review(self.csv)
        r = {x["submission_id"]: x for x in rows2}["1001"]
        self.assertEqual(r["publish"], "yes")
        self.assertEqual(r["title"], "My Edited Title")
        self.assertEqual(r["summary"], "Human summary")
        # merge function directly: new row appended, old unseen row kept, blank filled
        existing = [{"submission_id": "a", "title": "Keep", "summary": "", "publish": "yes", "id": "keep"}]
        new = [{"submission_id": "a", "title": "Other", "summary": "filled", "publish": "no", "id": ""},
               {"submission_id": "b", "title": "Keep", "summary": "x", "publish": "no", "id": ""}]
        merged, added, _ = G.merge_rows(existing, new)
        self.assertEqual(added, 1)
        self.assertEqual(merged[0]["title"], "Keep")
        self.assertEqual(merged[0]["summary"], "filled")
        self.assertEqual(merged[0]["publish"], "yes")
        self.assertEqual(merged[1]["id"], "keep-2")
        merged2, added2, _ = G.merge_rows(merged, [])
        self.assertEqual(len(merged2), 2)

    def edit_publish(self, which=("1001", "1002"), **extra):
        _, rows = G.read_review(self.csv)
        for r in rows:
            if r["submission_id"] in which:
                r["publish"] = "yes"
                r["summary"] = r["summary"] or "A short summary of the project."
                r["students"] = r["students"] or "Test Student"
                for k, v in extra.items():
                    r[k] = v
        G.write_review(self.csv, rows)

    def test_end_to_end(self):
        self.run_import()
        _, rows = G.read_review(self.csv)
        for r in rows:  # make the injected name safe for publishing
            if r["submission_id"] == "1002":
                r["students"] = "Test Student Three"
        G.write_review(self.csv, rows)
        self.edit_publish()
        # stale generated files + an unrelated file
        open(os.path.join(self.site, "thumbs", "old-one.webp"), "wb").write(b"x")
        open(os.path.join(self.site, "files", "old-one.pdf"), "wb").write(b"x")
        open(os.path.join(self.site, "files", "old-one-poster.webp"), "wb").write(b"x")
        open(os.path.join(self.site, "thumbs", "README.txt"), "w").write("keep")
        code, summ = B.build(self.csv, self.site, log=quiet, poster_thumbnails=True)
        self.assertEqual(code, 0, summ)
        self.assertEqual(summ["published"], 2)
        self.assertFalse(os.path.exists(os.path.join(self.site, "thumbs", "old-one.webp")))
        self.assertFalse(os.path.exists(os.path.join(self.site, "files", "old-one.pdf")))
        self.assertFalse(os.path.exists(os.path.join(self.site, "files", "old-one-poster.webp")))
        self.assertTrue(os.path.exists(os.path.join(self.site, "thumbs", "README.txt")))
        with open(os.path.join(self.site, "data", "projects.json"), encoding="utf-8") as f:
            raw = f.read()
        doc = json.loads(raw)
        self.assert_valid_spec(doc)
        self.assertFalse(doc["demo"])
        # Everything the build publishes must be readable by the web server (mkstemp alone makes 0600).
        public = [os.path.join(self.site, "data", "projects.json")]
        for p in doc["projects"]:
            if p.get("thumbnail"):
                public.append(os.path.join(self.site, p["thumbnail"]))
            public += [os.path.join(self.site, l["url"]) for l in p["links"] if l["url"].startswith("files/")]
        for path in public:
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o644, path)
        self.assertEqual(len(doc["projects"]), 2)
        for needle in ("example.edu", "9999000", "submission_", "1001", "gradescope", "score"):
            self.assertNotIn(needle, raw)
        titles = [p["title"].lower() for p in doc["projects"]]
        self.assertEqual(titles, sorted(titles))
        for p in doc["projects"]:
            self.assertFalse(os.path.exists(os.path.join(self.site, "files", p["id"] + ".pdf")))
            if HAVE_THUMBS:
                self.assertEqual(p["links"][0], {"kind": "image", "url": "files/%s-poster.webp" % p["id"], "label": "Poster"})
                self.assertTrue(os.path.isfile(os.path.join(self.site, "files", p["id"] + "-poster.webp")))
            self.assertNotIn("pdf", [l["kind"] for l in p["links"]])
            if HAVE_THUMBS:
                self.assertEqual(p["thumbnail"], "thumbs/%s.webp" % p["id"])
                with open(os.path.join(self.site, p["thumbnail"]), "rb") as f:
                    head = f.read(12)
                self.assertEqual(head[:4], b"RIFF")
                self.assertEqual(head[8:12], b"WEBP")
                self.assertEqual(max(webp_size(os.path.join(self.site, p["thumbnail"]))), 1280)
                self.assertEqual(max(webp_size(os.path.join(self.site, "files", p["id"] + "-poster.webp"))), 2400)
        # not published rows never appear
        self.assertNotIn("1003", raw)

    def test_build_validation_errors(self):
        self.run_import()
        self.edit_publish(which=("1001", "1003"))
        _, rows = G.read_review(self.csv)
        for r in rows:
            if r["submission_id"] == "1003":
                r.update({"title": "Bad One", "students": "x@stanford.edu", "tags": "nonsense", "links": "javascript:alert(1)",
                          "summary": "s" * 400, "id": "Bad ID"})
            if r["submission_id"] == "1001":
                r["include_poster"] = "no"
        G.write_review(self.csv, rows)
        code, summ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.site, "data", "projects.json")))
        reason = summ["skipped"][0][1]
        for frag in ("slug", "email", "unknown tag", "link:", "summary longer"):
            self.assertIn(frag, reason)
        code, summ = B.build(self.csv, self.site, skip_invalid=True, log=quiet)
        self.assertEqual(code, 0)
        self.assertEqual(summ["published"], 1)

    def test_duplicate_ids_and_empty(self):
        self.run_import()
        self.edit_publish(id="same-id")
        code, summ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(code, 1)
        self.assertIn("duplicate id", summ["skipped"][0][1])
        # nothing published: refuses to overwrite
        _, rows = G.read_review(self.csv)
        for r in rows:
            r["publish"] = "no"
        G.write_review(self.csv, rows)
        marker = os.path.join(self.site, "data", "projects.json")
        open(marker, "w").write("{\"demo\": true}")
        code, _ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(open(marker).read(), "{\"demo\": true}")

    def test_poster_path_confinement(self):
        self.run_import()
        outside = os.path.join(self.tmp, "outside.pdf")
        open(outside, "wb").write(synthetic.make_pdf())
        self.edit_publish(which=("1001",), poster_file="../outside.pdf", include_poster="yes")
        code, summ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(code, 1)
        self.assertIn("poster_file", summ["skipped"][0][1])

    def test_formula_quote_stripped_in_build(self):
        self.run_import()
        self.edit_publish(which=("1001",), title="=Evil Title", include_poster="no", poster_file="")
        # Build reads the CSV, which stores "'=Evil Title"
        with open(self.csv, "rb") as f:
            self.assertIn(b"'=Evil Title", f.read())
        code, _ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(code, 0)
        doc = json.load(open(os.path.join(self.site, "data", "projects.json"), encoding="utf-8"))
        self.assertEqual(doc["projects"][0]["title"], "=Evil Title")

    def assert_valid_spec(self, doc):
        self.assertEqual(doc["version"], 1)
        self.assertIsInstance(doc["demo"], bool)
        self.assertRegex(doc["generated"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        ids = set()
        known = {i["id"] for i in INTERESTS["interests"]}
        for p in doc["projects"]:
            self.assertRegex(p["id"], r"^[a-z0-9][a-z0-9-]{0,63}$")
            self.assertNotIn(p["id"], ids)
            ids.add(p["id"])
            self.assertTrue(1 <= len(p["title"]) <= 90)
            self.assertTrue(1 <= len(p["students"]) <= 8)
            for s in p["students"]:
                self.assertTrue(1 <= len(s) <= 60)
                self.assertNotIn("@", s)
            self.assertTrue(1 <= len(p["summary"]) <= 280)
            self.assertLessEqual(len(p.get("description", "")), 1500)
            self.assertLessEqual(len(p["links"]), 6)
            for l in p["links"]:
                self.assertIn(l["kind"], L.LINK_KINDS)
                self.assertTrue(L.validate_url(l["url"])[0])
                self.assertTrue(l["url"].startswith(("https://", "files/", "thumbs/")))
            if p.get("thumbnail"):
                self.assertTrue(L.validate_url(p["thumbnail"])[0])
                self.assertFalse(p["thumbnail"].startswith("https"))
            self.assertLessEqual(len(p["tags"]), 4)
            self.assertTrue(set(p["tags"]) <= known)
            self.assertLessEqual(len(p["tools"]), 8)
            self.assertLessEqual(len(p.get("keywords", [])), 12)


def webp_size(path):
    """(width, height) of a lossy/lossless/extended WebP."""
    with open(path, "rb") as f:
        d = f.read(40)
    kind = d[12:16]
    if kind == b"VP8 ":
        return (int.from_bytes(d[26:28], "little") & 0x3FFF, int.from_bytes(d[28:30], "little") & 0x3FFF)
    if kind == b"VP8X":
        return (1 + int.from_bytes(d[24:27], "little"), 1 + int.from_bytes(d[27:30], "little"))
    if kind == b"VP8L":
        b = int.from_bytes(d[21:25], "little")
        return ((b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1)
    raise ValueError("not webp")


class SecurityRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def timed(self, fn, limit=1.0):
        import time
        t0 = time.time()
        fn()
        self.assertLess(time.time() - t0, limit)

    # 1
    def test_strip_html_redos(self):
        self.timed(lambda: G._strip_html("<script>" * 5000))
        self.timed(lambda: G._strip_html("<" * 200000))
        self.assertEqual(G._strip_html("<p>Hi <script>evil()</script>there</p><style>x{}</style>").split(), ["Hi", "there"])

    # 2
    def test_pdf_forbidden_scan(self):
        self.assertEqual(L.pdf_forbidden_names(b"/Type /Page /Contents 1 0 R"), [])
        for name in ("JavaScript", "JS", "Launch", "EmbeddedFile", "EmbeddedFiles", "OpenAction", "AA", "RichMedia", "SubmitForm"):
            self.assertEqual(L.pdf_forbidden_names(b"<< /" + name.encode() + b" 5 0 R >>"), [name])
        self.assertEqual(L.pdf_forbidden_names(b"/JSON /AAA /Annots"), [])

    def test_pdf_not_published_by_default_and_qpdf_gating(self):
        pdf = os.path.join(self.tmp, "p.pdf")
        with open(pdf, "wb") as f:
            f.write(synthetic.make_pdf(title="Author test.one@example.edu"))
        real_which = shutil.which
        try:
            shutil.which = lambda n, *a, **k: None if n == "qpdf" else real_which(n, *a, **k)
            ok, msg = L.sanitize_pdf(pdf, os.path.join(self.tmp, "o.pdf"))
        finally:
            shutil.which = real_which
        self.assertFalse(ok)
        self.assertIn("qpdf", msg)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o.pdf")))

    def test_publish_pdfs_without_qpdf_skips_pdf(self):
        zip_ = synthetic.build_export_zip(os.path.join(self.tmp, "e.zip"), with_malicious=False)
        csvp = os.path.join(self.tmp, "w", "review.csv")
        G.import_export(zip_, csvp)
        _, rows = G.read_review(csvp)
        for r in rows:
            if r["submission_id"] == "1001":
                r["publish"] = "yes"
                r["summary"] = "A summary."
        G.write_review(csvp, rows)
        site = os.path.join(self.tmp, "site")
        os.makedirs(os.path.join(site, "data"))
        shutil.copy(os.path.join(os.path.dirname(TOOLS), "showcase", "data", "interests.json"), os.path.join(site, "data"))
        real_which = shutil.which
        try:
            shutil.which = lambda n, *a, **k: None if n == "qpdf" else real_which(n, *a, **k)
            code, summ = B.build(csvp, site, log=quiet, publish_pdfs=True)
        finally:
            shutil.which = real_which
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(site, "files", "pantry-pilot-test-poster.pdf")))
        self.assertTrue(any("qpdf" in w for _, w in summ["warnings"]))

    # 3
    def test_pii_detection(self):
        for t in ["mail me a@b.edu", "ID 12345678 here", "sunet: jdoe42", "SID 1234", "see gradescope.com/x",
                  "https://x.com/?e=a%40b.edu", "https://x.com/?e=a%2540b.edu"[:0] or "a%40b.edu"]:
            self.assertIsNotNone(L.pii_problem(t), t)
        for t in ["A running coach", "Phone 1234567", "Inside sidewalk", "version 2026"]:
            self.assertIsNone(L.pii_problem(t), t)

    def test_build_rejects_pii_fields(self):
        base = {"submission_id": "1", "publish": "yes", "id": "p1", "title": "T", "students": "Test Student",
                "summary": "ok summary", "include_poster": "no"}
        interests = INTERESTS
        for field, val in [("title", "Mail test@example.edu"), ("summary", "My SID is 12345678"),
                           ("description", "sunet: abc123"), ("keywords", "gradescope.com"),
                           ("links", "website | https://example.com/?u=a%40b.edu | x"),
                           ("links", "website | https://example.com/ | id 20202020")]:
            row = dict(base, **{field: val})
            p, errs, _, _ = B.validate_row(row, interests, self.tmp)
            self.assertTrue(errs, (field, val))

    def test_metadata_answer_keys(self):
        meta = {"submitters": [{"name": "A"}], "grader_comment": "Great job, email me@x.edu",
                "answers": {"q1": "my answer", "feedback_text": "bad", "rubric": {"answer": "bad"}},
                "notes": "unknown key text", "student_response": "resp", "evaluation_summary": "no"}
        self.assertEqual(sorted(G._meta_texts(meta)), ["my answer", "resp"])

    def test_import_notes_metadata_source(self):
        zip_ = synthetic.build_export_zip(os.path.join(self.tmp, "e.zip"), with_malicious=False)
        csvp = os.path.join(self.tmp, "w", "review.csv")
        G.import_export(zip_, csvp)
        _, rows = G.read_review(csvp)
        r = {x["submission_id"]: x for x in rows}["1002"]
        self.assertIn("metadata", r["notes"])

    # 4
    def test_yaml_and_pdf_scan_redos(self):
        self.timed(lambda: L.parse_gradescope_yaml("- a" + " " * 80000 + "b"))
        self.timed(lambda: L.parse_gradescope_yaml("a: " + ":" * 80000))
        p = os.path.join(self.tmp, "bomb.pdf")
        with open(p, "wb") as f:
            f.write(b"%PDF-1.4\n" + b"/URI(" * 1000000 + b"/Title(" * 100000)
        self.timed(lambda: L.extract_pdf_text(p), 5.0)
        self.timed(lambda: L.pdf_info_title(p), 2.0)
        good = os.path.join(self.tmp, "g.pdf")
        with open(good, "wb") as f:
            f.write(synthetic.make_pdf(link="https://example.com/a"))
        self.assertIn("https://example.com/a", L.extract_pdf_text(good)[0])
        self.assertEqual(L.pdf_info_title(good), "Pantry Pilot Test Poster")

    # 5
    def test_ip_like_hosts(self):
        for u in ["https://127.1/", "https://0x7f.1/", "https://0177.0.0.1/", "https://10.1/", "https://[::1]/",
                  "https://[2001:db8::1]/", "https://2130706433/", "https://1.2.3.4/", "https://example.123/",
                  "https://0x7f.0x0.0x0.0x1/"]:
            self.assertFalse(L.validate_url(u)[0], u)
        self.assertTrue(L.validate_url("https://123.example.com/")[0])
        self.assertTrue(L.validate_url("https://x0x.example.com/")[0])

    # 6
    def test_relative_path_rule(self):
        for u in ["files/a%20b.pdf", "files/A.pdf", "files/a_b.pdf", "files/a.png", "files/-a.pdf", "files/a/b.pdf",
                  "files/a.pdf?x=1", "thumbs/a.pdf.exe", "files/.pdf", "files/a..pdf"]:
            self.assertFalse(L.validate_url(u)[0], u)
        for u in ["files/a.pdf", "thumbs/a-b-2.webp", "files/x-poster.webp"]:
            self.assertTrue(L.validate_url(u)[0], u)
        self.assertEqual(len(L.parse_links("website | https://example.com | " + "x" * 100)[0][0]["label"]), 60)

    # 7
    def test_private_permissions(self):
        zip_ = synthetic.build_export_zip(os.path.join(self.tmp, "e.zip"), with_malicious=False)
        csvp = os.path.join(self.tmp, "newwork", "review.csv")
        old = os.umask(0o022)
        try:
            G.import_export(zip_, csvp)
        finally:
            os.umask(old)
        mode = lambda p: os.stat(p).st_mode & 0o777  # noqa: E731
        self.assertEqual(mode(os.path.dirname(csvp)), 0o700)
        self.assertEqual(mode(os.path.join(os.path.dirname(csvp), "files")), 0o700)
        self.assertEqual(mode(csvp), 0o600)
        for fn in os.listdir(os.path.join(os.path.dirname(csvp), "files")):
            self.assertEqual(mode(os.path.join(os.path.dirname(csvp), "files", fn)), 0o600)

    @unittest.skipUnless(HAVE_THUMBS, "needs sips+cwebp")
    def test_thumbnail_bounds_tall_page(self):
        tall = synthetic.make_pdf().replace(b"/MediaBox[0 0 720 480]", b"/MediaBox[0 0 100 5000]")
        src = os.path.join(self.tmp, "tall.pdf")
        with open(src, "wb") as f:
            f.write(tall)
        dest = os.path.join(self.tmp, "t.webp")
        ok, msg = L.make_thumbnail(src, dest, max_side=960)
        self.assertTrue(ok, msg)
        self.assertLessEqual(max(webp_size(dest)), 960)

    def test_zip_name_clash_does_not_abort(self):
        import zipfile
        z = os.path.join(self.tmp, "c.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("ok", "file")
            zf.writestr("ok/x", "under file")
            zf.writestr("d/y", "dir first")
            zf.writestr("d", "file over dir")
            zf.writestr("dup.txt", "1")
            zf.writestr("dup.txt", "2")
            zf.writestr("fine.txt", "fine")
        out = os.path.join(self.tmp, "o")
        os.makedirs(out)
        warns = G.safe_extract_zip(z, out)
        self.assertEqual(open(os.path.join(out, "fine.txt")).read(), "fine")
        self.assertEqual(open(os.path.join(out, "dup.txt")).read(), "1")
        self.assertGreaterEqual(sum("clash" in w for w in warns), 3)


class ThumbnailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.work = os.path.join(self.tmp, "work")
        self.files = os.path.join(self.work, "files")
        self.csv = os.path.join(self.work, "review.csv")
        self.site = os.path.join(self.tmp, "site")
        for d in ("data", "thumbs", "files"):
            os.makedirs(os.path.join(self.site, d))
        os.makedirs(self.files)
        shutil.copy(os.path.join(os.path.dirname(TOOLS), "showcase", "data", "interests.json"),
                    os.path.join(self.site, "data", "interests.json"))
        self.src = os.path.join(self.tmp, "src")
        os.makedirs(self.src)

    def write(self, name, data):
        p = os.path.join(self.src, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def analyze(self, paths):
        return G.analyze_submission("77", {"files": sorted(paths), "meta": {}}, INTERESTS, self.work, self.files)

    # ---- importer
    def test_importer_named_thumbnail(self):
        pdf = self.write("poster.pdf", synthetic.make_pdf())
        a = self.write("a.png", synthetic.png_bytes(30, 30))
        t = self.write("My_Thumbnail.png", synthetic.png_bytes(4, 4))
        r = self.analyze([pdf, a, t])
        self.assertEqual(r["thumbnail_file"], "files/77-thumb.png")
        self.assertEqual(r["poster_file"], "files/77.pdf")
        self.assertEqual(os.stat(os.path.join(self.work, r["thumbnail_file"])).st_mode & 0o777, 0o600)
        self.assertNotIn("card image", r["notes"])

    def test_importer_single_image_next_to_poster(self):
        pdf = self.write("poster.pdf", synthetic.make_pdf())
        a = self.write("diagram.jpg", synthetic.png_bytes(5, 5))
        r = self.analyze([pdf, a])
        self.assertEqual(r["thumbnail_file"], "files/77-thumb.jpg")
        self.assertIn("image used as card image; check", r["notes"])
        # two unnamed images next to a poster: no guess
        b = self.write("other.png", synthetic.png_bytes(6, 6))
        r = self.analyze([pdf, a, b])
        self.assertEqual(r["thumbnail_file"], "")

    def test_importer_image_only(self):
        small = self.write("one.png", synthetic.png_bytes(4, 4))
        big = self.write("two.png", synthetic.png_bytes(64, 64))
        r = self.analyze([small, big])
        self.assertEqual(r["poster_file"], "")
        self.assertEqual(r["include_poster"], "no")
        self.assertEqual(r["thumbnail_file"], "files/77-thumb.png")
        with open(os.path.join(self.work, r["thumbnail_file"]), "rb") as f:
            self.assertEqual(f.read(), open(big, "rb").read())

    def test_importer_none(self):
        pdf = self.write("poster.pdf", synthetic.make_pdf())
        r = self.analyze([pdf])
        self.assertEqual(r["thumbnail_file"], "")
        self.assertTrue(r["poster_file"])
        self.assertEqual(self.analyze([])["thumbnail_file"], "")

    # ---- CSV / build
    def row(self, **kw):
        r = {c: "" for c in L.CSV_COLUMNS}
        r.update({"submission_id": "77", "publish": "yes", "id": "proj", "title": "Proj Title", "students": "Test Student",
                  "summary": "A short summary of the project.", "featured": "no", "include_poster": "no"})
        r.update(kw)
        return r

    def copy_in(self, name, data):
        p = os.path.join(self.files, name)
        with open(p, "wb") as f:
            f.write(data)
        return "files/" + name

    def build(self, rows, **kw):
        G.write_review(self.csv, rows)
        return B.build(self.csv, self.site, log=quiet, **kw)

    def project(self):
        with open(os.path.join(self.site, "data", "projects.json"), encoding="utf-8") as f:
            return json.load(f)["projects"][0]

    def test_old_csv_without_column_loads_and_builds(self):
        os.makedirs(self.work, exist_ok=True)
        cols = [c for c in L.CSV_COLUMNS if c != "thumbnail_file"]
        with open(self.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            r = self.row()
            w.writerow({c: r[c] for c in cols})
        code, summ = B.build(self.csv, self.site, log=quiet)
        self.assertEqual(code, 0, summ)
        self.assertIsNone(self.project().get("thumbnail"))
        _, rows = G.read_review(self.csv)
        self.assertEqual(rows[0].get("thumbnail_file", ""), "")

    @unittest.skipUnless(HAVE_THUMBS, "needs sips+cwebp")
    def test_build_uses_thumbnail_file_and_poster_fallback(self):
        pdf = self.copy_in("77.pdf", synthetic.make_pdf())
        img = self.copy_in("77-thumb.png", synthetic.png_bytes(40, 20))
        # no thumbnail_file: no thumbnail, even with a poster
        code, summ = self.build([self.row(poster_file=pdf)])
        self.assertEqual(code, 0, summ)
        self.assertIsNone(self.project().get("thumbnail"))
        self.assertFalse(os.path.exists(os.path.join(self.site, "thumbs", "proj.webp")))
        # poster fallback only with the flag
        code, _ = self.build([self.row(poster_file=pdf)], poster_thumbnails=True)
        p = self.project()
        self.assertEqual(p["thumbnail"], "thumbs/proj.webp")
        self.assertEqual(p["thumbnailAlt"], "Poster for Proj Title")
        # thumbnail_file wins over the poster, with or without the flag
        code, _ = self.build([self.row(poster_file=pdf, thumbnail_file=img)], poster_thumbnails=True)
        p = self.project()
        self.assertEqual(p["thumbnail"], "thumbs/proj.webp")
        self.assertEqual(p["thumbnailAlt"], "Preview image for Proj Title")
        self.assertEqual(max(webp_size(os.path.join(self.site, p["thumbnail"]))), 1280)

    @unittest.skipUnless(HAVE_THUMBS, "needs sips+cwebp")
    def test_cache_regenerates_on_source_switch_and_change(self):
        a = self.copy_in("a.png", synthetic.png_bytes(40, 20))
        b = self.copy_in("b.png", synthetic.png_bytes(20, 40))
        dest = os.path.join(self.site, "thumbs", "proj.webp")
        self.build([self.row(thumbnail_file=a)])
        cache = os.path.join(self.work, ".thumbcache.json")
        self.assertEqual(os.stat(cache).st_mode & 0o777, 0o600)
        with open(cache) as f:
            entry = json.load(f)["proj"]
        self.assertEqual(entry["kind"], "thumbnail")
        self.assertEqual(entry["max_side"], 1280)
        first = open(dest, "rb").read()
        # same inputs: cached, file untouched
        mt = os.path.getmtime(dest)
        self.build([self.row(thumbnail_file=a)])
        self.assertEqual(os.path.getmtime(dest), mt)
        # switch source to a file that is OLDER than the existing output: must still regenerate
        old = os.path.getmtime(dest) - 1000
        os.utime(os.path.join(self.work, b), (old, old))
        self.build([self.row(thumbnail_file=b)])
        second = open(dest, "rb").read()
        self.assertNotEqual(first, second)
        # same path, new content/size, older mtime: regenerate
        self.copy_in("b.png", synthetic.png_bytes(50, 10, rgb=b"\x00\xff\x00"))
        os.utime(os.path.join(self.work, b), (old, old))
        self.build([self.row(thumbnail_file=b)])
        self.assertNotEqual(second, open(dest, "rb").read())

    def test_thumbnail_problems_are_warnings(self):
        for cell in ("files/missing.png", "../outside.png"):
            code, summ = self.build([self.row(thumbnail_file=cell)])
            self.assertEqual(code, 0, summ)
            self.assertTrue(any("thumbnail_file" in w for _l, w in summ["warnings"]), summ["warnings"])
            self.assertIsNone(self.project().get("thumbnail"))
        pdf = self.copy_in("77.pdf", synthetic.make_pdf())
        code, summ = self.build([self.row(thumbnail_file=pdf)])
        self.assertEqual(code, 0, summ)
        self.assertTrue(any("must be an image" in w for _l, w in summ["warnings"]))

    # ---- pixel cap
    @unittest.skipUnless(HAVE_THUMBS, "needs sips+cwebp")
    def test_pixel_cap(self):
        wide = self.write("wide.png", synthetic.png_bytes(13000, 1))
        ok, msg = L.make_thumbnail(wide, os.path.join(self.tmp, "o.webp"), max_side=640)
        self.assertFalse(ok)
        self.assertIn("too large", msg)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o.webp")))
        fine = self.write("fine.png", synthetic.png_bytes(12000, 100))
        ok, msg = L.make_thumbnail(fine, os.path.join(self.tmp, "o2.webp"), max_side=640)
        self.assertTrue(ok, msg)
        # surfaced as a build warning, not an error
        self.copy_in("w.png", synthetic.png_bytes(13000, 1))
        code, summ = self.build([self.row(thumbnail_file="files/w.png")])
        self.assertEqual(code, 0, summ)
        self.assertTrue(any("too large" in w for _l, w in summ["warnings"]))


class CliTests(unittest.TestCase):
    def test_cli_exit_codes(self):
        self.assertEqual(G.main(["--export", "/nonexistent/path.zip", "--out", os.path.join(tempfile.gettempdir(), "x_review.csv")]), 2)
        self.assertEqual(B.main(["--review", "/nonexistent/review.csv"]), 2)


if __name__ == "__main__":
    unittest.main()
