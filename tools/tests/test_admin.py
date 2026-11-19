import http.client
import json
import os
import shutil
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import admin  # noqa: E402
import showcase_lib as L  # noqa: E402

REAL_SITE = os.path.join(os.path.dirname(TOOLS), "showcase")
HAVE_THUMBS = bool(L._which("sips", ["/usr/bin/sips"]) and L._which("cwebp", ["/opt/homebrew/bin/cwebp"]))


def png_bytes(w=8, h=8):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\xcc\x22\x22" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class AdminBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="admin_test_")
        self.site = os.path.join(self.tmp, "site")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(os.path.join(self.site, "data"))
        shutil.copy(os.path.join(REAL_SITE, "data", "interests.json"), os.path.join(self.site, "data"))
        with open(os.path.join(self.site, "index.html"), "w") as f:
            f.write("<!doctype html><title>x</title>")
        self.admin_dir = os.path.join(self.tmp, "ui")
        os.makedirs(self.admin_dir)
        with open(os.path.join(self.admin_dir, "index.html"), "w") as f:
            f.write("<!doctype html><title>admin</title>")
        self.token = "tok-" + "x" * 30
        self.srv = admin.make_server(self.site, self.work, 0, self.token, self.admin_dir, quiet=True)
        self.port = self.srv.server_address[1]
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def req(self, method, path, body=None, headers=None, token=True, ctype="json", raw=False):
        h = {"Host": "127.0.0.1:%d" % self.port}
        if token:
            h["X-Admin-Token"] = self.token
        data = None
        if body is not None:
            if ctype == "json" and not isinstance(body, bytes):
                data = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
            else:
                data = body
                h["Content-Type"] = "application/octet-stream" if ctype == "json" or ctype == "bin" else ctype
        elif method in ("POST", "PUT"):
            data = b""
        if headers:
            h.update(headers)
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            for k, v in h.items():
                c.putheader(k, v)
            if data is not None:
                c.putheader("Content-Length", str(len(data)))
            c.endheaders(data)
            r = c.getresponse()
            payload = r.read()
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            if raw:
                return r.status, payload, hdrs
            try:
                return r.status, json.loads(payload.decode()), hdrs
            except ValueError:
                return r.status, payload, hdrs
        finally:
            c.close()

    def state(self):
        return self.req("GET", "/api/state")[1]

    def new_row(self, **row):
        rev = self.state()["review"]["rev"]
        st, d, _ = self.req("POST", "/api/rows", {"rev": rev, "row": row})
        self.assertEqual(st, 200, d)
        return d

    def good_row(self, **extra):
        row = {"title": "Pantry Test", "students": "Alex Kim", "summary": "A thing.", "publish": "yes"}
        row.update(extra)
        return self.new_row(**row)


class SecurityTests(AdminBase):
    def test_token(self):
        self.assertEqual(self.req("GET", "/api/state", token=False)[0], 401)
        self.assertEqual(self.req("GET", "/api/state", headers={"X-Admin-Token": "nope"})[0], 401)
        self.assertEqual(self.req("GET", "/api/state")[0], 200)

    def test_query_token_only_for_preview(self):
        st, _, _ = self.req("GET", "/api/state?t=" + self.token, token=False)
        self.assertEqual(st, 401)
        st, _, _ = self.req("GET", "/api/rows/abc/preview?t=" + self.token, token=False)
        self.assertEqual(st, 404)  # authed, no such row
        st, _, _ = self.req("GET", "/api/rows/abc/preview?t=bad", token=False)
        self.assertEqual(st, 401)

    def test_host_origin_fetchsite(self):
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "evil.com"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "127.0.0.1"})[0], 403)
        self.assertEqual(self.req("GET", "/", headers={"Host": "evil.com:%d" % self.port})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Host": "localhost:%d" % self.port})[0], 200)
        self.assertEqual(self.req("GET", "/api/state", headers={"Origin": "http://evil.com"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Origin": "http://127.0.0.1:%d" % self.port})[0], 200)
        self.assertEqual(self.req("GET", "/api/state", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.req("GET", "/api/state", headers={"Sec-Fetch-Site": "same-origin"})[0], 200)

    def test_headers_and_options(self):
        st, _, h = self.req("GET", "/api/state")
        self.assertIn("frame-ancestors 'none'", h["content-security-policy"])
        self.assertNotIn("upgrade-insecure-requests", h["content-security-policy"])
        self.assertEqual(h["x-frame-options"], "DENY")
        self.assertEqual(h["cache-control"], "no-store")
        self.assertEqual(h["x-content-type-options"], "nosniff")
        self.assertNotIn("access-control-allow-origin", h)
        self.assertTrue(h["content-type"].startswith("application/json"))
        self.assertEqual(self.req("OPTIONS", "/api/state")[0], 405)
        st, _, h = self.req("GET", "/site/index.html", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("frame-ancestors 'self'", h["content-security-policy"])

    def test_content_types_and_size(self):
        st, d, _ = self.req("PUT", "/api/site", b"{}", ctype="text/plain")
        self.assertEqual(st, 415)
        st, d, _ = self.req("POST", "/api/rows/x/poster?rev=none", b"%PDF-1", ctype="application/json")
        self.assertEqual(st, 415)
        big = b'{"x":"' + b"a" * (1024 * 1024 + 10) + b'"}'
        st, d, _ = self.req("PUT", "/api/site", big, headers={"Content-Type": "application/json"}, ctype="x")
        self.assertEqual(st, 413)
        self.assertIn("error", d)

    def test_missing_length_411(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port)
        c.putrequest("PUT", "/api/site", skip_host=True)
        c.putheader("Host", "127.0.0.1:%d" % self.port)
        c.putheader("X-Admin-Token", self.token)
        c.putheader("Content-Type", "application/json")
        c.endheaders()
        r = c.getresponse()
        self.assertEqual(r.status, 411)
        c.close()

    def test_static_traversal(self):
        for p in ["/site/../tools/admin.py", "/site/%2e%2e/tools/admin.py", "/site/%2e%2e%2f%2e%2e/x",
                  "/..%2f..%2fetc/passwd", "/site/data/../../x", "/site/.hidden", "/site/data/", "/site"]:
            self.assertIn(self.req("GET", p, token=False, raw=True)[0], (403, 404), p)
        self.assertEqual(self.req("GET", "/site/data/interests.json", token=False, raw=True)[0], 200)
        self.assertEqual(self.req("GET", "/", token=False, raw=True)[0], 200)
        self.assertEqual(self.req("POST", "/site/index.html", b"x")[0], 405)

    def test_symlink_refused(self):
        secret = os.path.join(self.tmp, "secret.txt")
        with open(secret, "w") as f:
            f.write("s3cret")
        os.symlink(secret, os.path.join(self.site, "link.txt"))
        os.symlink(self.tmp, os.path.join(self.site, "linkdir"))
        self.assertEqual(self.req("GET", "/site/link.txt", raw=True)[0], 404)
        self.assertEqual(self.req("GET", "/site/linkdir/secret.txt", raw=True)[0], 404)

    def test_unknown_api(self):
        self.assertEqual(self.req("GET", "/api/nope")[0], 404)
        self.assertEqual(self.req("POST", "/api/state", {})[0], 405)


class RowTests(AdminBase):
    def test_empty_state(self):
        s = self.state()
        self.assertFalse(s["review"]["exists"])
        self.assertEqual(s["review"]["rev"], "none")
        self.assertEqual(s["site"]["term"], "Autumn 2026")
        self.assertEqual(s["published"]["count"], 0)
        self.assertIn("website", s["linkKinds"])
        self.assertEqual(s["limits"]["title"], 90)

    def test_crud_roundtrip_and_conflict(self):
        d = self.good_row()
        row = d["row"]
        self.assertTrue(row["submission_id"].startswith("manual-"))
        self.assertEqual(row["id"], "pantry-test")
        self.assertEqual(row["publish"], "yes")
        self.assertEqual(row["_check"]["errors"], [])
        self.assertEqual(oct(os.stat(os.path.join(self.work, "review.csv")).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(self.work).st_mode & 0o777), "0o700")
        sid = row["submission_id"]
        # update
        st, u, _ = self.req("PUT", "/api/rows/" + sid, {"rev": d["rev"], "row": {"summary": "New sum", "featured": "TRUE"}})
        self.assertEqual(st, 200, u)
        self.assertEqual(u["row"]["summary"], "New sum")
        self.assertEqual(u["row"]["featured"], "yes")
        # stale rev
        st, c, _ = self.req("PUT", "/api/rows/" + sid, {"rev": d["rev"], "row": {"summary": "x"}})
        self.assertEqual(st, 409)
        self.assertEqual(c["rev"], u["rev"])
        # bad inputs
        for bad in ({"nope": "x"}, {"title": 5}, {"title": "a" * 6001}, {"submission_id": "other"}, {"id": "Bad Id"}):
            st, e, _ = self.req("PUT", "/api/rows/" + sid, {"rev": u["rev"], "row": bad})
            self.assertEqual(st, 400, bad)
        st, _, _ = self.req("PUT", "/api/rows/missing", {"rev": u["rev"], "row": {}})
        self.assertEqual(st, 404)
        # duplicate id
        d2 = self.new_row(title="Other", id="other")
        st, e, _ = self.req("PUT", "/api/rows/" + d2["row"]["submission_id"],
                            {"rev": self.state()["review"]["rev"], "row": {"id": "pantry-test"}})
        self.assertEqual(st, 400)
        # external edit changes rev
        with open(os.path.join(self.work, "review.csv"), "ab") as f:
            f.write(b"")
        # delete (stale then fresh)
        st, _, _ = self.req("DELETE", "/api/rows/%s?rev=%s" % (sid, "deadbeef"), headers={"Content-Type": "application/json"})
        self.assertEqual(st, 409)
        rev = self.state()["review"]["rev"]
        st, dd, _ = self.req("DELETE", "/api/rows/%s?rev=%s" % (sid, rev), headers={"Content-Type": "application/json"})
        self.assertEqual(st, 200, dd)
        self.assertEqual([r["submission_id"] for r in self.state()["review"]["rows"]], [d2["row"]["submission_id"]])
        self.assertTrue(len(self.req("GET", "/api/backups")[1]) >= 2)

    def test_formula_protection(self):
        d = self.new_row(title='=HYPERLINK("http://evil","x")', summary="+cmd", notes="@x")
        with open(os.path.join(self.work, "review.csv"), encoding="utf-8-sig") as f:
            text = f.read()
        self.assertIn("'=HYPERLINK", text)
        self.assertNotIn(",=HYPERLINK", text)
        self.assertIn("'+cmd", text)
        again = self.state()["review"]["rows"][0]
        self.assertEqual(again["title"], '=HYPERLINK("http://evil","x")')

    def test_leading_apostrophe_roundtrip(self):
        self.new_row(title="'=1+1", summary="'tis")
        with open(os.path.join(self.work, "review.csv"), encoding="utf-8-sig") as f:
            text = f.read()
        self.assertIn("''=1+1", text)
        row = self.state()["review"]["rows"][0]
        self.assertEqual((row["title"], row["summary"]), ("'=1+1", "'tis"))

    def test_check_flags_problems(self):
        d = self.new_row(title="Bad", students="x", summary="mail me a@b.com", publish="yes", id="dup")
        self.new_row(title="Other", id="dup2")
        csv_path = os.path.join(self.work, "review.csv")  # simulate a spreadsheet edit creating a duplicate id
        with open(csv_path, "rb") as f:
            raw = f.read()
        with open(csv_path, "wb") as f:
            f.write(raw.replace(b"dup2", b"dup"))
        rows = self.state()["review"]["rows"]
        errs = " ".join(rows[0]["_check"]["errors"])
        self.assertIn("email", errs)
        self.assertIn("more than one row", errs)

    def test_uploads(self):
        d = self.good_row()
        sid, rev = d["row"]["submission_id"], d["rev"]
        st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, rev), b"MZ not a poster", ctype="bin")
        self.assertEqual(st, 415)
        st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, "stale"), png_bytes(), ctype="bin")
        self.assertEqual(st, 409)
        st, e, _ = self.req("POST", "/api/rows/nope/poster?rev=%s" % rev, png_bytes(), ctype="bin")
        self.assertEqual(st, 404)
        self.assertFalse(os.path.exists(self.work + "/uploads") and
                         [n for n in os.listdir(self.work + "/uploads")])
        st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, rev), png_bytes(), ctype="bin")
        self.assertEqual(st, 200, e)
        pf = e["row"]["poster_file"]
        self.assertRegex(pf, r"^uploads/manual-[0-9a-f]{8}-[0-9a-f]{8}\.png$")
        self.assertEqual(e["row"]["include_poster"], "yes")
        full = os.path.join(self.work, pf)
        self.assertEqual(oct(os.stat(full).st_mode & 0o777), "0o600")
        self.assertEqual(os.listdir(os.path.join(self.work, "uploads")), [os.path.basename(pf)])
        if HAVE_THUMBS:
            st, img, h = self.req("GET", "/api/rows/%s/preview" % sid, raw=True)
            self.assertEqual(st, 200)
            self.assertEqual(h["content-type"], "image/webp")
            self.assertTrue(img.startswith(b"RIFF"))

    def test_preview_rejects_escape(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        outside = os.path.join(self.tmp, "outside.png")
        with open(outside, "wb") as f:
            f.write(png_bytes())
        st, u, _ = self.req("PUT", "/api/rows/" + sid, {"rev": d["rev"], "row": {"poster_file": "../outside.png"}})
        self.assertEqual(st, 200)
        self.assertEqual(self.req("GET", "/api/rows/%s/preview" % sid, raw=True)[0], 404)
        self.assertEqual(self.req("PUT", "/api/rows/" + sid,
                                  {"rev": u["rev"], "row": {"poster_file": outside}})[0], 200)
        self.assertEqual(self.req("GET", "/api/rows/%s/preview" % sid, raw=True)[0], 404)

    def test_seed(self):
        proj = {"version": 1, "term": "T", "generated": "g", "demo": True, "projects": [
            {"id": "alpha", "title": "Alpha", "students": ["A B", "C D"], "summary": "S", "description": "D",
             "links": [{"kind": "website", "url": "https://example.com", "label": "Site"},
                       {"kind": "image", "url": "files/alpha-poster.webp", "label": "Poster"}],
             "tags": ["food"], "tools": ["Claude Code"], "keywords": ["k"], "featured": True}]}
        with open(os.path.join(self.site, "data", "projects.json"), "w") as f:
            json.dump(proj, f)
        self.assertEqual(self.state()["published"]["count"], 1)
        st, d, _ = self.req("POST", "/api/seed", {})
        self.assertEqual(st, 200, d)
        row = self.state()["review"]["rows"][0]
        self.assertEqual(row["submission_id"], "manual-alpha")
        self.assertEqual(row["publish"], "yes")
        self.assertEqual(row["notes"], "sample data")
        self.assertEqual(row["featured"], "yes")
        self.assertEqual(row["students"], "A B; C D")
        self.assertEqual(row["links"], "website | https://example.com | Site")
        self.assertEqual(row["_check"]["errors"], [])
        self.assertEqual(row["thumbnail_file"], "")
        self.assertEqual(self.req("POST", "/api/seed", {})[0], 409)

    def test_seed_keeps_live_card_images(self):
        # A live card image is copied into uploads/ so the next build keeps it rather than deleting it as stale.
        thumbs = os.path.join(self.site, "thumbs")
        os.makedirs(thumbs, exist_ok=True)
        webp = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
        with open(os.path.join(thumbs, "alpha.webp"), "wb") as f:
            f.write(webp)
        with open(os.path.join(thumbs, "fake.webp"), "wb") as f:
            f.write(b"not a webp at all")
        proj = {"version": 1, "demo": False, "projects": [
            {"id": "alpha", "title": "Alpha", "students": ["A"], "summary": "S", "thumbnail": "thumbs/alpha.webp"},
            {"id": "beta", "title": "Beta", "students": ["B"], "summary": "S", "thumbnail": "thumbs/../secret.webp"},
            {"id": "gamma", "title": "Gamma", "students": ["C"], "summary": "S", "thumbnail": "thumbs/fake.webp"}]}
        with open(os.path.join(self.site, "data", "projects.json"), "w") as f:
            json.dump(proj, f)
        st, d, _ = self.req("POST", "/api/seed", {})
        self.assertEqual(st, 200, d)
        rows = {r["id"]: r for r in self.state()["review"]["rows"]}
        tf = rows["alpha"]["thumbnail_file"]
        self.assertRegex(tf, r"^uploads/alpha-thumb-[0-9a-f]{8}\.webp$")
        copied = os.path.join(self.work, tf)
        with open(copied, "rb") as f:
            self.assertEqual(f.read(), webp)
        self.assertEqual(os.stat(copied).st_mode & 0o777, 0o600)
        self.assertEqual(rows["beta"]["thumbnail_file"], "")   # path tricks are ignored
        self.assertEqual(rows["gamma"]["thumbnail_file"], "")  # not a real WebP


class SiteInterestTests(AdminBase):
    def site_body(self, **over):
        s = dict(admin.DEFAULT_SITE_COPY)
        s.update(over)
        return {"rev": self.state()["siteRev"], "site": s}

    def test_site_ok_and_errors(self):
        st0 = self.state()
        self.assertEqual(st0["siteRev"], "none")
        st, d, _ = self.req("PUT", "/api/site", self.site_body(term="Spring 2027"))
        self.assertEqual(st, 200, d)
        self.assertEqual(d["site"]["term"], "Spring 2027")
        self.assertEqual(d["rev"], self.state()["siteRev"])
        with open(os.path.join(self.site, "data", "site.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["term"], "Spring 2027")
        self.assertEqual(self.state()["site"]["term"], "Spring 2027")
        for over, word in [({"headline": "x" * 91}, "Headline is 91 characters; the limit is 90"),
                           ({"headline": "*a* and *b*"}, "emphasis"),
                           ({"lede": "write to me@example.com"}, "Intro"),
                           ({"trySearches": ["a"] * 9}, "limit is 8"),
                           ({"trySearches": ["ok", ""]}, "\u201cTry\u201d search 2 must be 1 to 40"),
                           ({"eyebrow": 5}, "Label above the headline must be text")]:
            st, d, _ = self.req("PUT", "/api/site", self.site_body(**over))
            self.assertEqual(st, 400, over)
            self.assertIn(word, d["error"])
        st, d, _ = self.req("PUT", "/api/site", self.site_body(lede="x" * 300, headline="x" * 91))
        self.assertIn("Intro is 300 characters; the limit is 280.".rstrip("."), d["error"])
        self.assertIn(" \u2022 ", d["error"])
        self.assertNotIn("lede", d["error"])
        self.assertNotIn("trySearches", d["error"])

    def test_site_rev(self):
        body = self.site_body(term="One")
        st, d, _ = self.req("PUT", "/api/site", body)
        self.assertEqual(st, 200)
        st, e, _ = self.req("PUT", "/api/site", body)  # stale: still says "none"
        self.assertEqual(st, 409)
        self.assertIn("changed since you opened it", e["error"])
        self.assertEqual(e["rev"], d["rev"])
        self.assertEqual(self.req("PUT", "/api/site", {"site": body["site"]})[0], 400)

    def test_interests_labels_and_rev(self):
        st0 = self.state()
        rev = st0["interestsRev"]
        self.assertNotEqual(rev, "none")
        bad_cases = [
            {"interests": [], "tools": []},
            {"interests": [{"id": "Bad Id", "label": "x", "terms": ["a"]}], "tools": []},
            {"interests": [{"id": "a", "label": "x", "terms": ["a"]}, {"id": "a", "label": "y", "terms": ["b"]}]},
            {"interests": [{"id": "a", "label": "x" * 33, "terms": ["a"]}]},
            {"interests": [{"id": "a", "label": "x", "terms": []}]},
            {"interests": [{"id": "a", "label": "x", "terms": ["t" * 41]}]},
        ]
        for b in bad_cases:
            self.assertEqual(self.req("PUT", "/api/interests", {"rev": rev, "interests": b})[0], 400, b)
        st, d, _ = self.req("PUT", "/api/interests", {"rev": rev, "interests": {
            "interests": [{"id": "fit", "label": "Fitness", "terms": []}, {"id": "b", "label": "", "terms": ["x"]}]}})
        self.assertEqual(st, 400)
        self.assertIn("Fitness: needs 1 to 120 search words", d["error"])
        self.assertIn("Topic 2:", d["error"])
        self.assertIn(" \u2022 ", d["error"])
        self.assertEqual(self.req("PUT", "/api/interests", {"interests": bad_cases[0]})[0], 400)
        self.new_row(title="T", tags="fitness; food")
        new = {"interests": [{"id": "fitness", "label": "Fitness", "terms": ["Gym", "gym", " Run "]}],
               "tools": ["Claude", "claude", "Cursor"]}
        st, d, _ = self.req("PUT", "/api/interests", {"rev": rev, "interests": new})
        self.assertEqual(st, 200, d)
        self.assertEqual(d["rev"], self.state()["interestsRev"])
        self.assertEqual(d["interests"]["interests"][0]["terms"], ["gym", "run"])
        self.assertEqual(d["interests"]["tools"], ["Claude", "Cursor"])
        self.assertEqual(d["interests"]["version"], st0["interests"]["version"])
        self.assertEqual(len(d["warnings"]), 1)
        self.assertIn("food", d["warnings"][0])
        self.assertNotIn("fitness", d["warnings"][0])
        st, e, _ = self.req("PUT", "/api/interests", {"rev": rev, "interests": new})  # stale
        self.assertEqual(st, 409)
        self.assertEqual(e["rev"], d["rev"])
        self.assertIn("changed since you opened", e["error"])

    def test_row_id_messages(self):
        d = self.good_row()
        d2 = self.new_row(title="Other", id="other")
        rev = self.state()["review"]["rev"]
        st, e, _ = self.req("PUT", "/api/rows/" + d2["row"]["submission_id"], {"rev": rev, "row": {"id": "pantry-test"}})
        self.assertEqual((st, e["error"]), (400, "Link ID 'pantry-test' is already used by another project"))
        st, e, _ = self.req("PUT", "/api/rows/" + d2["row"]["submission_id"], {"rev": rev, "row": {"id": "Bad Id"}})
        self.assertEqual((st, e["error"]), (400, "Link ID must be lowercase letters, numbers and dashes"))


class HardeningTests(AdminBase):
    def test_launcher_file(self):
        url = "http://127.0.0.1:1/#t=SECRETTOKEN"
        with mock.patch.object(admin.webbrowser, "open") as op:
            cleanup = admin.launch_browser(url, ttl=0.3)
        self.assertEqual(op.call_count, 1)
        arg = op.call_args[0][0]
        self.assertTrue(arg.startswith("file://"))
        self.assertNotIn("SECRETTOKEN", arg)
        path = arg[len("file://"):]
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(path)).st_mode & 0o777), "0o700")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('http-equiv="refresh"', text)
        self.assertIn(url, text)
        for _ in range(50):
            if not os.path.exists(path):
                break
            time.sleep(0.1)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))
        cleanup()  # idempotent

    def test_launcher_cleanup_at_shutdown(self):
        with mock.patch.object(admin.webbrowser, "open") as op:
            cleanup = admin.launch_browser("http://127.0.0.1:1/#t=x", ttl=60)
        path = op.call_args[0][0][len("file://"):]
        self.assertTrue(os.path.exists(path))
        cleanup()
        self.assertFalse(os.path.exists(path))

    def test_connection_slots_released(self):
        for _ in range(admin.MAX_CONNECTIONS * 2 + 5):
            self.assertEqual(self.req("GET", "/api/state")[0], 200)
        self.assertEqual(self.srv._slots._value, admin.MAX_CONNECTIONS)

    def test_unicode_submission_id_preview(self):
        sid = "manual-\u00e9\u4e2d"
        self.assertEqual(self.req("GET", "/api/rows/%s/preview" % "manual-%C3%A9%E4%B8%AD")[0], 404)
        self.assertEqual(self.req("GET", "/api/rows/..%2fx/preview")[0], 404)
        self.assertNotIn("/", sid)

    def test_upload_removed_on_delete_and_shared_kept(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, d["rev"]), png_bytes(), ctype="bin")
        self.assertEqual(st, 200, e)
        pf = e["row"]["poster_file"]
        full = os.path.join(self.work, pf)
        d2 = self.new_row(title="Other", poster_file=pf)  # a second row shares the file
        sid2 = d2["row"]["submission_id"]
        rev = self.state()["review"]["rev"]
        st, _, _ = self.req("DELETE", "/api/rows/%s?rev=%s" % (sid, rev), headers={"Content-Type": "application/json"})
        self.assertEqual(st, 200)
        self.assertTrue(os.path.exists(full))  # still referenced
        rev = self.state()["review"]["rev"]
        st, _, _ = self.req("DELETE", "/api/rows/%s?rev=%s" % (sid2, rev), headers={"Content-Type": "application/json"})
        self.assertEqual(st, 200)
        self.assertFalse(os.path.exists(full))

    def test_replace_poster_keeps_shared_old(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, d["rev"]), png_bytes(), ctype="bin")
        pf = e["row"]["poster_file"]
        self.new_row(title="Sharer", poster_file=pf)
        st, e2, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, self.state()["review"]["rev"]), png_bytes(9, 9), ctype="bin")
        self.assertEqual(st, 200, e2)
        self.assertNotEqual(e2["row"]["poster_file"], pf)
        self.assertTrue(os.path.exists(os.path.join(self.work, pf)))

    def test_thumbnail_upload_preview_delete(self):
        d = self.good_row()
        sid, rev = d["row"]["submission_id"], d["rev"]
        url = "/api/rows/%s/thumbnail?rev=%s" % (sid, rev)
        st, e, _ = self.req("POST", url, b"%PDF-1.4 not an image", ctype="bin")
        self.assertEqual(st, 415)
        st, e, _ = self.req("POST", "/api/rows/%s/thumbnail?rev=stale" % sid, png_bytes(), ctype="bin")
        self.assertEqual(st, 409)
        st, e, _ = self.req("POST", "/api/rows/nope/thumbnail?rev=%s" % rev, png_bytes(), ctype="bin")
        self.assertEqual(st, 404)
        self.assertFalse(os.path.exists(self.work + "/uploads") and os.listdir(self.work + "/uploads"))
        st, e, _ = self.req("POST", url, png_bytes(), ctype="bin")
        self.assertEqual(st, 200, e)
        tf = e["row"]["thumbnail_file"]
        self.assertRegex(tf, r"^uploads/manual-[0-9a-f]{8}-thumb-[0-9a-f]{8}\.png$")
        self.assertEqual(e["row"]["poster_file"], "")
        self.assertEqual(e["row"]["include_poster"], d["row"]["include_poster"])
        full = os.path.join(self.work, tf)
        self.assertEqual(oct(os.stat(full).st_mode & 0o777), "0o600")
        # the same rev is now stale
        st, _, _ = self.req("POST", url, png_bytes(), ctype="bin")
        self.assertEqual(st, 409)
        if HAVE_THUMBS:
            st, img, h = self.req("GET", "/api/rows/%s/thumbnail-preview" % sid, raw=True)
            self.assertEqual(st, 200)
            self.assertEqual(h["content-type"], "image/webp")
            self.assertTrue(img.startswith(b"RIFF"))
            # poster preview is a separate thing: this row has no poster
            self.assertEqual(self.req("GET", "/api/rows/%s/preview" % sid, raw=True)[0], 404)
        # replacing removes the old upload
        rev = self.state()["review"]["rev"]
        st, e2, _ = self.req("POST", "/api/rows/%s/thumbnail?rev=%s" % (sid, rev), png_bytes(9, 9), ctype="bin")
        self.assertEqual(st, 200, e2)
        self.assertNotEqual(e2["row"]["thumbnail_file"], tf)
        self.assertFalse(os.path.exists(full))
        tf2 = e2["row"]["thumbnail_file"]
        # delete: stale rev 409, then clears and removes the file
        hdr = {"Content-Type": "application/json"}
        st, _, _ = self.req("DELETE", "/api/rows/%s/thumbnail?rev=stale" % sid, headers=hdr)
        self.assertEqual(st, 409)
        st, e3, _ = self.req("DELETE", "/api/rows/%s/thumbnail?rev=%s" % (sid, e2["rev"]), headers=hdr)
        self.assertEqual(st, 200, e3)
        self.assertEqual(e3["row"]["thumbnail_file"], "")
        self.assertFalse(os.path.exists(os.path.join(self.work, tf2)))
        self.assertEqual(self.req("GET", "/api/rows/%s/thumbnail-preview" % sid, raw=True)[0], 404)

    def test_thumbnail_delete_keeps_shared_upload_and_warns_when_unusable(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        st, e, _ = self.req("POST", "/api/rows/%s/thumbnail?rev=%s" % (sid, d["rev"]), png_bytes(), ctype="bin")
        tf = e["row"]["thumbnail_file"]
        d2 = self.new_row(title="Other", thumbnail_file=tf)
        sid2 = d2["row"]["submission_id"]
        st, e3, _ = self.req("DELETE", "/api/rows/%s/thumbnail?rev=%s" % (sid, self.state()["review"]["rev"]),
                             headers={"Content-Type": "application/json"})
        self.assertEqual(st, 200)
        self.assertTrue(os.path.exists(os.path.join(self.work, tf)))  # still used by the other row
        # a poster_file sharing the same upload also protects it
        st, u, _ = self.req("PUT", "/api/rows/" + sid, {"rev": e3["rev"], "row": {"poster_file": tf}})
        self.assertEqual(st, 200, u)
        st, e4, _ = self.req("DELETE", "/api/rows/%s/thumbnail?rev=%s" % (sid2, u["rev"]),
                             headers={"Content-Type": "application/json"})
        self.assertTrue(os.path.exists(os.path.join(self.work, tf)))
        # unusable thumbnail_file shows up in _check warnings
        st, u2, _ = self.req("PUT", "/api/rows/" + sid, {"rev": e4["rev"], "row": {"thumbnail_file": "uploads/missing.png"}})
        self.assertEqual(st, 200, u2)
        self.assertTrue(any("thumbnail_file" in w for w in u2["row"]["_check"]["warnings"]))

    def test_upload_deleted_if_save_fails(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        with mock.patch.object(admin, "save_rows", side_effect=RuntimeError("boom")):
            st, e, _ = self.req("POST", "/api/rows/%s/poster?rev=%s" % (sid, d["rev"]), png_bytes(), ctype="bin")
        self.assertEqual(st, 500)
        up = os.path.join(self.work, "uploads")
        self.assertEqual(os.listdir(up) if os.path.isdir(up) else [], [])


class BuildTests(AdminBase):
    def test_build_no_review(self):
        self.assertEqual(self.req("POST", "/api/build", {})[0], 400)

    def test_build_end_to_end_and_backups(self):
        d = self.good_row(summary="Works well.", tags="food", links="website | https://example.com | Demo")
        self.good_row(title="Broken", summary="", publish="yes")
        st, b, _ = self.req("POST", "/api/build", {"skipInvalid": False})
        self.assertEqual(st, 200)
        self.assertFalse(b["ok"])
        self.assertEqual(len(b["skipped"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.site, "data", "projects.json")))
        st, b, _ = self.req("POST", "/api/build", {"skipInvalid": True})
        self.assertTrue(b["ok"], b)
        self.assertEqual(b["published"], 1)
        with open(os.path.join(self.site, "data", "projects.json"), encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual([p["id"] for p in doc["projects"]], ["pantry-test"])
        self.assertEqual(doc["term"], "Autumn 2026")
        self.assertEqual(self.state()["published"]["count"], 1)

    def test_backups_restore(self):
        d = self.good_row()
        sid = d["row"]["submission_id"]
        st, u, _ = self.req("PUT", "/api/rows/" + sid, {"rev": d["rev"], "row": {"title": "Changed"}})
        backups = self.req("GET", "/api/backups")[1]
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.req("POST", "/api/backups/restore", {"name": "../x"})[0], 400)
        self.assertEqual(self.req("POST", "/api/backups/restore", {"name": "review-20200101T000000Z-abcdef01.csv"})[0], 404)
        st, r, _ = self.req("POST", "/api/backups/restore", {"name": backups[0]["name"]})
        self.assertEqual(st, 200, r)
        self.assertEqual(self.state()["review"]["rows"][0]["title"], "Pantry Test")
        self.assertEqual(len(self.req("GET", "/api/backups")[1]), 2)

    def test_backup_pruning(self):
        self.good_row()
        for i in range(35):
            self.new_row(title="t%d" % i)
        self.assertEqual(len(self.req("GET", "/api/backups")[1]), 30)

    def test_import_rejects_non_zip_and_bad_zip(self):
        st, e, _ = self.req("POST", "/api/import", b"not a zip", ctype="bin")
        self.assertEqual(st, 415)
        st, e, _ = self.req("POST", "/api/import", b"PK\x03\x04garbage", ctype="bin")
        self.assertEqual(st, 400, e)
        uploads = os.path.join(self.work, "uploads")
        self.assertEqual([n for n in os.listdir(uploads)] if os.path.isdir(uploads) else [], [])

    def test_import_zip(self):
        import zipfile
        zp = os.path.join(self.tmp, "export.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("submission_1/notes.txt", "A cooking assistant. https://example.com/demo")
        with open(zp, "rb") as f:
            data = f.read()
        st, d, _ = self.req("POST", "/api/import", data, ctype="bin")
        self.assertEqual(st, 200, d)
        self.assertTrue(d["ok"])
        self.assertIsInstance(d["log"], list)
        self.assertEqual(len(self.state()["review"]["rows"]), 1)
        self.assertEqual(os.listdir(os.path.join(self.work, "uploads")), [])


if __name__ == "__main__":
    unittest.main()
