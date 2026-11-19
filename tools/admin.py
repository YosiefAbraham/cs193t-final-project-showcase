#!/usr/bin/env python3
"""LOCAL-ONLY admin editor backend for the CS193T showcase (Python 3.9+, standard library only).

    python3 tools/admin.py [--port 4322] [--no-browser]

Binds to 127.0.0.1 only, requires a per-run token on every API call, and serves the UI from
tools/admin/. NEVER deploy this. It reads and writes student data in tools/work/.
"""
import argparse
import csv  # noqa: F401  (kept for parity with the other tools)
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import tempfile
import threading
import traceback
import webbrowser
import html
from pathlib import Path
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_showcase as B  # noqa: E402
import gradescope_import as G  # noqa: E402
import showcase_lib as L  # noqa: E402
import serve as S  # noqa: E402

DEFAULT_PORT = 4322
ADMIN_DIR = os.path.join(HERE, "admin")

ADMIN_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
    "object-src 'none'; require-trusted-types-for 'script'; trusted-types 'none'"
)

MAX_JSON = 1024 * 1024
MAX_POSTER_UPLOAD = 100 * 1024 ** 2
MAX_THUMB_UPLOAD = 25 * 1024 ** 2
MAX_ZIP_UPLOAD = 2 * 1024 ** 3
MAX_CELL = 6000
MAX_DRAIN = 16 * 1024 ** 2
KEEP_BACKUPS = 30
BACKUP_RE = re.compile(r"^review-[0-9TZ-]+-[0-9a-f]{8}\.csv$")
SID_RE = r"[\w.-]{1,120}"  # Unicode word chars (submission ids come from \w+ matches); no slashes
YES = ("yes", "y", "true", "1")
BOOL_COLS = ("publish", "featured", "include_poster")

DEFAULT_SITE_COPY = {
    "version": 1,
    "term": "Autumn 2026",
    "eyebrow": "Final project showcase",
    "headline": "What students built *with* AI.",
    "lede": "Custom AI systems from students across every major. Search for what you care about and the page rearranges around it.",
    "searchPlaceholder": "Try “cooking” or “gym”",
    "trySearches": ["work out", "cooking", "games", "organization", "study", "health", "money", "climate"],
    "footerNote": "Projects appear with each student’s permission. Spot a problem or want yours changed?",
}


class ApiError(Exception):
    def __init__(self, status, message, **extra):
        Exception.__init__(self, message)
        self.status = status
        self.message = message
        self.extra = extra


# ------------------------------------------------------------------ config / files
class Config(object):
    def __init__(self, site_dir, work_dir, token, admin_dir, quiet):
        self.site_dir = os.path.abspath(site_dir)
        self.work_dir = os.path.abspath(work_dir)
        self.token = token
        self.admin_dir = os.path.abspath(admin_dir)
        self.quiet = quiet
        self.lock = threading.Lock()  # build / import / row writes
        self._preview_locks = {}
        self._preview_locks_guard = threading.Lock()
        self.review = os.path.join(self.work_dir, "review.csv")
        self.backups = os.path.join(self.work_dir, "backups")
        self.uploads = os.path.join(self.work_dir, "uploads")
        self.previews = os.path.join(self.work_dir, "previews")
        self.interests_path = os.path.join(self.site_dir, "data", "interests.json")
        self.site_path = os.path.join(self.site_dir, "data", "site.json")
        self.projects_path = os.path.join(self.site_dir, "data", "projects.json")


def preview_lock(cfg, key):
    with cfg._preview_locks_guard:
        return cfg._preview_locks.setdefault(key, threading.Lock())


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def atomic_write(path, data, mode):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json_file(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_public(path, doc):
    data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    atomic_write(path, data, 0o644)


def file_rev(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return "none"


def review_rev(cfg):
    return file_rev(cfg.review)


def load_review(cfg):
    """-> (fields, rows) where every row has all CSV_COLUMNS as str."""
    if not os.path.isfile(cfg.review):
        return list(L.CSV_COLUMNS), []
    fields, rows = G.read_review(cfg.review)
    out = []
    for r in rows:
        d = {k: (v or "") for k, v in r.items()}
        for c in L.CSV_COLUMNS:
            d.setdefault(c, "")
        out.append(d)
    return fields, out


def backup_review(cfg):
    if not os.path.isfile(cfg.review):
        return None
    private_dir(cfg.work_dir)
    private_dir(cfg.backups)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = "review-%s-%s.csv" % (stamp, secrets.token_hex(4))
    with open(cfg.review, "rb") as f:
        atomic_write(os.path.join(cfg.backups, name), f.read(), 0o600)
    names = sorted((n for n in os.listdir(cfg.backups) if BACKUP_RE.match(n)),
                   key=lambda n: (os.stat(os.path.join(cfg.backups, n)).st_mtime_ns, n), reverse=True)
    for n in names[KEEP_BACKUPS:]:
        try:
            os.unlink(os.path.join(cfg.backups, n))
        except OSError:
            pass
    return name


def save_rows(cfg, fields, rows):
    """Back up then write (caller holds the lock)."""
    private_dir(cfg.work_dir)
    backup_review(cfg)
    extras = [f for f in fields if f and f not in L.CSV_COLUMNS]
    G.write_review(cfg.review, rows, extra_fields=extras)
    try:
        os.chmod(cfg.review, 0o600)
    except OSError:
        pass


def is_yes(v):
    return (v or "").strip().lower() in YES


def split_cell(cell):
    return [x.strip() for x in re.split(r"[;\n]", cell or "") if x.strip()]


def check_row(cfg, row, rows, interests):
    errs, warns = [], []
    try:
        _p, e, w, _x = B.validate_row(row, interests, cfg.work_dir)
        errs += e
        warns += w
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        errs.append("could not validate this row")
    rid = (row.get("id") or "").strip()
    if rid and not L.SLUG_RE.match(rid):
        errs.append("Link ID must be lowercase letters, numbers and dashes")
    if rid and sum(1 for r in rows if (r.get("id") or "").strip() == rid) > 1:
        errs.append("Link ID '%s' is used by more than one row" % rid)
    return {"errors": errs, "warnings": warns}


def with_check(cfg, row, rows, interests):
    d = {c: row.get(c, "") for c in L.CSV_COLUMNS}
    d["_check"] = check_row(cfg, row, rows, interests)
    return d


def get_interests_doc(cfg):
    doc = read_json_file(cfg.interests_path, None)
    if not isinstance(doc, dict):
        doc = {"version": 1, "interests": [], "tools": []}
    doc.setdefault("interests", [])
    doc.setdefault("tools", [])
    return doc


def get_site_doc(cfg):
    doc = read_json_file(cfg.site_path, None)
    out = dict(DEFAULT_SITE_COPY)
    out["trySearches"] = list(DEFAULT_SITE_COPY["trySearches"])
    if isinstance(doc, dict):
        for k in DEFAULT_SITE_COPY:
            if k in doc:
                out[k] = doc[k]
    return out


# ------------------------------------------------------------------ validation
SITE_LIMITS = {"term": 40, "eyebrow": 60, "headline": 90, "lede": 280, "searchPlaceholder": 60, "footerNote": 200}
SITE_REQUIRED = ("term", "eyebrow", "headline", "lede", "searchPlaceholder", "footerNote")
SITE_LABELS = {"term": "Term", "eyebrow": "Label above the headline", "headline": "Headline", "lede": "Intro",
               "searchPlaceholder": "Search box hint", "footerNote": "Footer note"}
EMPH_RE = re.compile(r"^[^*]*(\*[^*\s][^*]*\*)?[^*]*$")
ERR_JOIN = " \u2022 "


def validate_site(site):
    errs, out = [], {"version": 1}
    if not isinstance(site, dict):
        raise ApiError(400, "Page copy must be an object")
    for k in SITE_REQUIRED:
        name = SITE_LABELS[k]
        v = site.get(k)
        if not isinstance(v, str):
            errs.append("%s must be text" % name)
            continue
        v = L.clean_text(v)
        if not v:
            errs.append("%s must not be empty" % name)
        elif len(v) > SITE_LIMITS[k]:
            errs.append("%s is %d characters; the limit is %d" % (name, len(v), SITE_LIMITS[k]))
        elif L.pii_problem(v):
            errs.append("%s %s" % (name, L.pii_problem(v)))
        elif k == "headline" and (v.count("*") not in (0, 2) or not EMPH_RE.match(v)):
            errs.append("Headline may contain at most one *emphasis* pair")
        out[k] = v
    ts = site.get("trySearches")
    clean_ts = []
    if not isinstance(ts, list):
        errs.append("The \u201cTry\u201d searches must be a list")
    else:
        if len(ts) > 8:
            errs.append("There are %d \u201cTry\u201d searches; the limit is 8" % len(ts))
        for i, t in enumerate(ts):
            name = "\u201cTry\u201d search %d" % (i + 1)
            if not isinstance(t, str):
                errs.append("%s must be text" % name)
                continue
            t = L.clean_text(t)
            if not t or len(t) > 40:
                errs.append("%s must be 1 to 40 characters" % name)
            elif L.pii_problem(t):
                errs.append("%s %s" % (name, L.pii_problem(t)))
            else:
                clean_ts.append(t)
    out["trySearches"] = clean_ts
    if errs:
        raise ApiError(400, ERR_JOIN.join(errs))
    return {k: out[k] for k in ("version", "term", "eyebrow", "headline", "lede", "searchPlaceholder",
                                "trySearches", "footerNote")}


ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


def validate_interests(doc, current_version=1):
    errs = []
    if not isinstance(doc, dict):
        raise ApiError(400, "Topics must be an object")
    items = doc.get("interests")
    tools_in = doc.get("tools", [])
    out_items, seen = [], set()
    if not isinstance(items, list) or not (1 <= len(items) <= 40):
        errs.append("There must be between 1 and 40 topics")
        items = []
    for i, it in enumerate(items):
        tag = "Topic %d" % (i + 1)
        if not isinstance(it, dict):
            errs.append("%s must be an object" % tag)
            continue
        iid, label, terms = it.get("id"), it.get("label"), it.get("terms")
        if isinstance(label, str) and L.clean_text(label):
            tag = L.clean_text(label)
        if not isinstance(iid, str) or not ID_RE.match(iid):
            errs.append("%s: internal id must be lowercase letters, numbers and dashes (max 32)" % tag)
            iid = None
        elif iid in seen:
            errs.append("%s: another topic already uses the id '%s'" % (tag, iid))
        else:
            seen.add(iid)
        if not isinstance(label, str) or not (1 <= len(L.clean_text(label)) <= 32):
            errs.append("%s: the name must be 1 to 32 characters" % tag)
            label = ""
        else:
            label = L.clean_text(label)
        clean_terms = []
        if not isinstance(terms, list):
            errs.append("%s: search words must be a list" % tag)
        else:
            for t in terms:
                if not isinstance(t, str):
                    errs.append("%s: each search word must be text" % tag)
                    continue
                t = L.clean_text(t).lower()
                if not (1 <= len(t) <= 40):
                    errs.append("%s: each search word must be 1 to 40 characters" % tag)
                elif t not in clean_terms:
                    clean_terms.append(t)
            if not (1 <= len(clean_terms) <= 120):
                errs.append("%s: needs 1 to 120 search words" % tag)
        out_items.append({"id": iid, "label": label, "terms": clean_terms})
    clean_tools = []
    if not isinstance(tools_in, list) or len(tools_in) > 80:
        errs.append("Tools must be a list of at most 80 names")
    else:
        for t in tools_in:
            if not isinstance(t, str):
                errs.append("Each tool must be text")
                continue
            t = L.clean_text(t)
            if not (1 <= len(t) <= 40):
                errs.append("Each tool name must be 1 to 40 characters")
            elif t.lower() not in [x.lower() for x in clean_tools]:
                clean_tools.append(t)
    if errs:
        raise ApiError(400, ERR_JOIN.join(errs))
    return {"version": current_version, "interests": out_items, "tools": clean_tools}


def norm_row_input(row, sid=None):
    """Validate a client-supplied partial row. Returns a dict of CSV columns."""
    if not isinstance(row, dict):
        raise ApiError(400, "row must be an object")
    out = {}
    for k, v in row.items():
        if k == "_check":
            continue
        if k == "submission_id":
            if sid is not None and v == sid:
                continue
            raise ApiError(400, "submission_id cannot be set")
        if k not in L.CSV_COLUMNS:
            raise ApiError(400, "unknown field %r" % str(k)[:40])
        if not isinstance(v, str):
            raise ApiError(400, "%s must be text" % k)
        if len(v) > MAX_CELL:
            raise ApiError(400, "%s is longer than %d characters" % (k, MAX_CELL))
        v = v.replace("\x00", "")
        if k in BOOL_COLS:
            v = "yes" if is_yes(v) else "no"
        out[k] = v
    return out


def check_id_unique(rid, rows, sid):
    rid = (rid or "").strip()
    if not rid:
        return
    if not L.SLUG_RE.match(rid):
        raise ApiError(400, "Link ID must be lowercase letters, numbers and dashes")
    for r in rows:
        if r.get("submission_id") != sid and (r.get("id") or "").strip() == rid:
            raise ApiError(400, "Link ID '%s' is already used by another project" % rid)


# ------------------------------------------------------------------ magic sniffing
def sniff_poster(head):
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    return None


# ------------------------------------------------------------------ handler
class Handler(BaseHTTPRequestHandler):
    server_version = "CS193TAdmin"
    sys_version = ""
    protocol_version = "HTTP/1.0"
    timeout = 30

    # ---- plumbing
    @property
    def cfg(self):
        return self.server.cfg

    def log_message(self, format, *args):  # noqa: A002
        pass

    def log_request(self, code="-", size="-"):
        if self.cfg.quiet:
            return
        path = urlsplit(self.path or "").path[:120]
        print("%s %s %s" % (code, getattr(self, "command", "-"), path), file=sys.stderr, flush=True)

    def _headers(self, site_mode=False):
        h = dict(S.SECURITY_HEADERS)
        if not site_mode:
            h["Content-Security-Policy"] = ADMIN_CSP
            h["X-Frame-Options"] = "DENY"
        h["Cache-Control"] = "no-store"
        return h

    def _send(self, status, body=b"", ctype="application/json; charset=utf-8", site_mode=False, extra=None):
        try:
            self.send_response(status)
            for k, v in self._headers(site_mode).items():
                self.send_header(k, v)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if getattr(self, "command", "") != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def send_error(self, code, message=None, explain=None):  # base class hook (bad request line etc.)
        self._json(code, {"error": message or "Request error"})

    def _api_error(self, e):
        body = {"error": e.message}
        body.update(e.extra)
        self._json(e.status, body)

    # ---- request body helpers
    def _begin(self):
        self._pending = 0
        try:
            self._pending = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            self._pending = 0

    def _drain(self):
        n = getattr(self, "_pending", 0)
        if 0 < n <= MAX_DRAIN:
            try:
                while n > 0:
                    chunk = self.rfile.read(min(n, 65536))
                    if not chunk:
                        break
                    n -= len(chunk)
            except OSError:
                pass
        self._pending = 0

    def _content_length(self, required=True):
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(411, "Chunked uploads are not supported; send Content-Length")
        raw = self.headers.get("Content-Length")
        if raw is None:
            if required:
                raise ApiError(411, "Content-Length is required")
            return 0
        try:
            n = int(raw)
        except ValueError:
            raise ApiError(400, "Bad Content-Length")
        if n < 0:
            raise ApiError(400, "Bad Content-Length")
        return n

    def _ctype(self):
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    def _read_json_body(self, required=True):
        n = self._content_length(required)
        if n > MAX_JSON:
            raise ApiError(413, "Request body is larger than 1 MB")
        data = self.rfile.read(n) if n else b""
        self._pending = max(0, self._pending - len(data))
        if len(data) != n:
            raise ApiError(400, "Request body was cut short")
        if not data:
            return {}
        try:
            obj = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, "Body is not valid JSON")
        if not isinstance(obj, dict):
            raise ApiError(400, "Body must be a JSON object")
        return obj

    def _stream_to(self, n, dest_path, max_bytes, sniff, what):
        """Stream exactly n body bytes to dest_path (0600). `sniff(head)` -> value or None (-> 415)."""
        if n > max_bytes:
            raise ApiError(413, "%s is larger than %d MB" % (what, max_bytes // 1024 ** 2))
        if n == 0:
            raise ApiError(400, "Empty upload")
        head = self.rfile.read(min(n, 16))
        self._pending = max(0, self._pending - len(head))
        kind = sniff(head)
        if kind is None:
            raise ApiError(415, "Unsupported file type")
        fd = os.open(dest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        ok = False
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(head)
                left = n - len(head)
                while left > 0:
                    chunk = self.rfile.read(min(left, 1024 * 1024))
                    if not chunk:
                        raise ApiError(400, "Upload was cut short")
                    self._pending = max(0, self._pending - len(chunk))
                    f.write(chunk)
                    left -= len(chunk)
            ok = True
        finally:
            if not ok and os.path.exists(dest_path):
                os.unlink(dest_path)
        return kind

    # ---- guards
    def _guard(self):
        port = self.server.server_address[1]
        allowed_hosts = ("127.0.0.1:%d" % port, "localhost:%d" % port)
        if self.headers.get("Host") not in allowed_hosts:
            raise ApiError(403, "Forbidden host")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in ("http://" + h for h in allowed_hosts):
            raise ApiError(403, "Forbidden origin")
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            raise ApiError(403, "Cross-site requests are not allowed")

    def _authed(self, allow_query=False):
        tok = self.headers.get("X-Admin-Token")
        if tok is None and allow_query:
            tok = (parse_qs(urlsplit(self.path).query).get("t") or [None])[0]
        if not tok or not hmac.compare_digest(tok.encode("utf-8"), self.cfg.token.encode("utf-8")):
            raise ApiError(401, "Missing or wrong admin token")

    # ---- dispatch
    def _handle(self):
        self._begin()
        try:
            self._guard()
            if self.command == "OPTIONS":
                raise ApiError(405, "Method not allowed")
            parts = urlsplit(self.path)
            path = parts.path
            if path.startswith("/api/"):
                self._api(path, parse_qs(parts.query))
            else:
                if self.command not in ("GET", "HEAD"):
                    raise ApiError(405, "Method not allowed")
                self._static(path)
        except ApiError as e:
            self._api_error(e)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            self._json(500, {"error": "Internal error (see the terminal running admin.py)"})
        finally:
            self._drain()

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_OPTIONS = _handle

    def _api(self, path, query):
        method = self.command
        path = unquote(path)  # decode exactly once; SID_RE has no "%" or "/" so nothing is decoded twice
        preview = re.fullmatch(r"/api/rows/(%s)/(preview|thumbnail-preview)" % SID_RE, path)
        self._authed(allow_query=bool(preview and method == "GET"))
        routes = [
            ("GET", r"/api/state", "none", self.r_state),
            ("PUT", r"/api/rows/(%s)" % SID_RE, "json", self.r_put_row),
            ("POST", r"/api/rows", "json", self.r_post_row),
            ("DELETE", r"/api/rows/(%s)" % SID_RE, "json0", self.r_delete_row),
            ("POST", r"/api/rows/(%s)/poster" % SID_RE, "bin", self.r_poster),
            ("GET", r"/api/rows/(%s)/preview" % SID_RE, "none", self.r_preview),
            ("POST", r"/api/rows/(%s)/thumbnail" % SID_RE, "bin", self.r_thumbnail),
            ("DELETE", r"/api/rows/(%s)/thumbnail" % SID_RE, "json0", self.r_thumbnail_delete),
            ("GET", r"/api/rows/(%s)/thumbnail-preview" % SID_RE, "none", self.r_thumbnail_preview),
            ("PUT", r"/api/site", "json", self.r_put_site),
            ("PUT", r"/api/interests", "json", self.r_put_interests),
            ("POST", r"/api/import", "bin", self.r_import),
            ("POST", r"/api/seed", "json", self.r_seed),
            ("POST", r"/api/build", "json", self.r_build),
            ("GET", r"/api/backups", "none", self.r_backups),
            ("POST", r"/api/backups/restore", "json", self.r_restore),
        ]
        matched_path = False
        for m_, rx, kind, fn in routes:
            m = re.fullmatch(rx, path)
            if not m:
                continue
            matched_path = True
            if m_ != method:
                continue
            if kind in ("json", "json0"):
                if self._ctype() != "application/json":
                    raise ApiError(415, "Content-Type must be application/json")
                body = self._read_json_body(required=(kind == "json"))
                self._json(*fn(m, query, body))
            elif kind == "bin":
                if self._ctype() != "application/octet-stream":
                    raise ApiError(415, "Content-Type must be application/octet-stream")
                self._json(*fn(m, query))
            else:
                res = fn(m, query)
                if res is not None:
                    self._json(*res)
            return
        raise ApiError(405 if matched_path else 404, "Method not allowed" if matched_path else "No such endpoint")

    # ---- static
    def _static(self, path):
        if path == "/site":
            raise ApiError(404, "Not found")
        if path.startswith("/site/"):
            root, rel, site_mode = self.cfg.site_dir, path[len("/site/"):], True
        else:
            root, rel, site_mode = self.cfg.admin_dir, path.lstrip("/"), False
        rel = unquote(rel)
        if "\x00" in rel or "\\" in rel:
            raise ApiError(404, "Not found")
        segs = rel.split("/")
        if segs[-1] == "":
            segs[-1] = "index.html"
        for s in segs:
            if s in ("", ".", "..") or s.startswith("."):
                raise ApiError(404, "Not found")
        full = root
        for s in segs:
            full = os.path.join(full, s)
            if os.path.islink(full):
                raise ApiError(404, "Not found")
        real, rroot = os.path.realpath(full), os.path.realpath(root)
        if not (real == rroot or real.startswith(rroot + os.sep)) or not os.path.isfile(real):
            raise ApiError(404, "Not found")
        with open(real, "rb") as f:
            data = f.read()
        self._send(200, data, self._mime(real), site_mode=site_mode)

    @staticmethod
    def _mime(path):
        ext = os.path.splitext(path)[1].lower()
        fixed = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml", ".webp": "image/webp",
                 ".woff2": "font/woff2", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}
        if ext in fixed:
            return fixed[ext]
        return S.MIME_TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"

    # ================================================================ endpoints
    def _lookup(self, rows, sid):
        for i, r in enumerate(rows):
            if r.get("submission_id") == sid:
                return i
        return -1

    def _check_rev(self, rev):
        if not isinstance(rev, str):
            raise ApiError(400, "rev is required")
        cur = review_rev(self.cfg)
        if rev != cur:
            raise ApiError(409, "review.csv changed on disk (edited elsewhere). Reload to see the latest.", rev=cur)

    def r_state(self, m, q):
        cfg = self.cfg
        # lock-free: files are replaced atomically; revs are read BEFORE the content so a racing write can
        # only make a rev stale (-> 409), never newer than the content the client sees.
        review_r, site_r, int_r = review_rev(cfg), file_rev(cfg.site_path), file_rev(cfg.interests_path)
        interests = get_interests_doc(cfg)
        exists = os.path.isfile(cfg.review)
        _f, rows = load_review(cfg)
        pub = read_json_file(cfg.projects_path, None)
        pub = pub if isinstance(pub, dict) else {}
        return 200, {
            "review": {"exists": exists, "rev": review_r,
                       "rows": [with_check(cfg, r, rows, interests) for r in rows]},
            "site": get_site_doc(cfg),
            "siteRev": site_r,
            "interests": interests,
            "interestsRev": int_r,
            "published": {"count": len(pub.get("projects") or []), "demo": bool(pub.get("demo")),
                          "term": pub.get("term") or "", "generated": pub.get("generated") or ""},
            "linkKinds": list(L.LINK_KINDS),
            "limits": {"title": L.MAX_TITLE, "summary": L.MAX_SUMMARY, "description": L.MAX_DESCRIPTION,
                       "students": L.MAX_STUDENTS, "student": L.MAX_STUDENT, "links": L.MAX_LINKS,
                       "tags": L.MAX_TAGS, "tools": L.MAX_TOOLS, "keywords": L.MAX_KEYWORDS,
                       "keywordLen": L.MAX_KEYWORD_LEN, "label": L.MAX_LABEL, "cell": MAX_CELL,
                       "site": dict(SITE_LIMITS, trySearches=8, trySearchLen=40)},
        }

    def r_put_row(self, m, q, body):
        sid = m.group(1)
        clean = norm_row_input(body.get("row"), sid)
        cfg = self.cfg
        with cfg.lock:
            self._check_rev(body.get("rev"))
            fields, rows = load_review(cfg)
            i = self._lookup(rows, sid)
            if i < 0:
                raise ApiError(404, "No such project row")
            if "id" in clean:
                clean["id"] = clean["id"].strip()
                check_id_unique(clean["id"], rows, sid)
            rows[i].update(clean)
            save_rows(cfg, fields, rows)
            return 200, {"rev": review_rev(cfg), "row": with_check(cfg, rows[i], rows, get_interests_doc(cfg))}

    def r_post_row(self, m, q, body):
        clean = norm_row_input(body.get("row", {}))
        cfg = self.cfg
        with cfg.lock:
            self._check_rev(body.get("rev"))
            fields, rows = load_review(cfg)
            row = {c: "" for c in L.CSV_COLUMNS}
            row.update({"publish": "no", "featured": "no", "include_poster": "no"})
            row.update(clean)
            taken_sids = {r["submission_id"] for r in rows}
            sid = "manual-" + secrets.token_hex(4)
            while sid in taken_sids:
                sid = "manual-" + secrets.token_hex(4)
            row["submission_id"] = sid
            row["id"] = (row.get("id") or "").strip()
            if row["id"]:
                check_id_unique(row["id"], rows, sid)
            elif row["title"].strip():
                taken = {(r.get("id") or "").strip() for r in rows}
                row["id"] = L.unique_slug(L.slugify(row["title"]), taken)
            rows.append(row)
            save_rows(cfg, fields, rows)
            return 200, {"rev": review_rev(cfg), "row": with_check(cfg, row, rows, get_interests_doc(cfg))}

    def r_delete_row(self, m, q, body):
        sid = m.group(1)
        cfg = self.cfg
        with cfg.lock:
            self._check_rev((q.get("rev") or [None])[0])
            fields, rows = load_review(cfg)
            i = self._lookup(rows, sid)
            if i < 0:
                raise ApiError(404, "No such project row")
            olds = [rows[i].get("poster_file", ""), rows[i].get("thumbnail_file", "")]
            del rows[i]
            save_rows(cfg, fields, rows)
            for old in olds:
                self._remove_old_upload(old, None, rows)
            return 200, {"rev": review_rev(cfg)}

    def _uploads_dir(self):
        private_dir(self.cfg.work_dir)
        return private_dir(self.cfg.uploads)

    def r_poster(self, m, q):
        return self._upload(m, q, "poster")

    def r_thumbnail(self, m, q):
        return self._upload(m, q, "thumbnail")

    def _upload(self, m, q, kind):
        sid = m.group(1)
        cfg = self.cfg
        col = "poster_file" if kind == "poster" else "thumbnail_file"
        rev = (q.get("rev") or [None])[0]
        n = self._content_length(True)
        # cheap pre-checks before streaming a big body
        self._check_rev(rev)
        if self._lookup(load_review(cfg)[1], sid) < 0:
            raise ApiError(404, "No such project row")
        ext_box = {}

        def sniff(head):
            ext = sniff_poster(head)
            if kind == "thumbnail" and ext == "pdf":
                ext = None  # card images must be PNG, JPEG, WEBP or GIF
            ext_box["ext"] = ext
            return ext

        updir = self._uploads_dir()
        base = L.slugify(sid) or "row"
        tmp_name = ".incoming-%s" % secrets.token_hex(8)
        tmp_path = os.path.join(updir, tmp_name)
        if kind == "poster":
            ext = self._stream_to(n, tmp_path, MAX_POSTER_UPLOAD, sniff, "Poster")
        else:
            ext = self._stream_to(n, tmp_path, MAX_THUMB_UPLOAD, sniff, "Card image")
        final = None
        try:
            final_name = "%s-%s%s.%s" % (base, "thumb-" if kind == "thumbnail" else "", secrets.token_hex(4), ext)
            final = os.path.join(updir, final_name)
            with cfg.lock:
                self._check_rev(rev)
                fields, rows = load_review(cfg)
                i = self._lookup(rows, sid)
                if i < 0:
                    raise ApiError(404, "No such project row")
                os.replace(tmp_path, final)
                old = rows[i].get(col, "")
                rows[i][col] = "uploads/" + final_name
                if kind == "poster":
                    rows[i]["include_poster"] = "yes"
                try:
                    save_rows(cfg, fields, rows)
                except BaseException:
                    try:
                        os.unlink(final)
                    except OSError:
                        pass
                    raise
                self._remove_old_upload(old, final_name, rows)
                return 200, {"rev": review_rev(cfg), "row": with_check(cfg, rows[i], rows, get_interests_doc(cfg))}
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def r_thumbnail_delete(self, m, q, body):
        sid = m.group(1)
        cfg = self.cfg
        with cfg.lock:
            self._check_rev((q.get("rev") or [None])[0])
            fields, rows = load_review(cfg)
            i = self._lookup(rows, sid)
            if i < 0:
                raise ApiError(404, "No such project row")
            old = rows[i].get("thumbnail_file", "")
            rows[i]["thumbnail_file"] = ""
            save_rows(cfg, fields, rows)
            self._remove_old_upload(old, None, rows)
            return 200, {"rev": review_rev(cfg), "row": with_check(cfg, rows[i], rows, get_interests_doc(cfg))}

    @staticmethod
    def _upload_name(cell):
        cell = (cell or "").strip().replace("\\", "/")
        if not cell.startswith("uploads/") or cell.count("/") != 1:
            return None
        name = cell.split("/", 1)[1]
        return None if not name or name.startswith(".") else name

    def _remove_old_upload(self, cell, keep_name, rows):
        """Delete uploads/<name> named by `cell` unless it is keep_name or any remaining row still uses it."""
        name = self._upload_name(cell)
        if not name or name == keep_name:
            return
        if any(self._upload_name(r.get(c)) == name for r in rows for c in ("poster_file", "thumbnail_file")):
            return
        p = os.path.join(self.cfg.uploads, name)
        if os.path.isfile(p) and not os.path.islink(p):
            try:
                os.unlink(p)
            except OSError:
                pass

    def r_preview(self, m, q):
        return self._preview(m, "poster")

    def r_thumbnail_preview(self, m, q):
        return self._preview(m, "thumbnail")

    def _preview(self, m, kind):
        sid = m.group(1)
        cfg = self.cfg
        col = "poster_file" if kind == "poster" else "thumbnail_file"
        _f, rows = load_review(cfg)
        i = self._lookup(rows, sid)
        if i < 0:
            raise ApiError(404, "No such project row")
        src, err = B.resolve_poster(rows[i].get(col), cfg.work_dir)
        if not src:
            raise ApiError(404, err or ("No poster" if kind == "poster" else "No card image"))
        digest = hashlib.sha1(sid.encode("utf-8")).hexdigest()
        if kind == "poster":
            dest = os.path.join(cfg.previews, digest + ".webp")
            max_side = 480
            stale = []
        else:
            st = os.stat(src)
            key = hashlib.sha1(("%s|%d|%d" % (src, st.st_size, st.st_mtime_ns)).encode("utf-8")).hexdigest()[:12]
            prefix = digest + "-thumb-"
            dest = os.path.join(cfg.previews, "%s%s.webp" % (prefix, key))
            max_side = 640
            stale = [prefix, dest]
        with preview_lock(cfg, dest):
            fresh = os.path.isfile(dest) and not os.path.islink(dest) and os.path.getmtime(dest) >= os.path.getmtime(src)
            if not fresh:
                private_dir(cfg.work_dir)
                private_dir(cfg.previews)
                tmp = os.path.join(cfg.previews, ".tmp-%s.webp" % secrets.token_hex(6))
                try:
                    ok, msg = L.make_thumbnail(src, tmp, max_side=max_side)
                    if not ok or not os.path.isfile(tmp):
                        raise ApiError(404, "Could not make a preview: %s" % msg if kind == "thumbnail" else "Could not make a preview")
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, dest)
                finally:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                if stale:  # drop previews of earlier sources for this row
                    for fn in os.listdir(cfg.previews):
                        full = os.path.join(cfg.previews, fn)
                        if fn.startswith(stale[0]) and full != stale[1] and os.path.isfile(full) and not os.path.islink(full):
                            try:
                                os.unlink(full)
                            except OSError:
                                pass
            with open(dest, "rb") as f:
                data = f.read()
        self._send(200, data, "image/webp")
        return None

    @staticmethod
    def _need_rev(body):
        rev = body.get("rev")
        if not isinstance(rev, str):
            raise ApiError(400, "rev is required")
        return rev

    def r_put_site(self, m, q, body):
        rev = self._need_rev(body)
        site = validate_site(body.get("site"))
        with self.cfg.lock:
            cur = file_rev(self.cfg.site_path)
            if rev != cur:
                raise ApiError(409, "The page copy changed since you opened it (edited elsewhere). "
                                    "Reload to see the latest.", rev=cur)
            write_json_public(self.cfg.site_path, site)
            return 200, {"rev": file_rev(self.cfg.site_path), "site": site}

    def r_put_interests(self, m, q, body):
        cfg = self.cfg
        rev = self._need_rev(body)
        with cfg.lock:
            cur = file_rev(cfg.interests_path)
            if rev != cur:
                raise ApiError(409, "The topics changed since you opened them (edited elsewhere). "
                                    "Reload to see the latest.", rev=cur)
            old = get_interests_doc(cfg)
            version = old.get("version", 1) if isinstance(old.get("version", 1), int) else 1
            new = validate_interests(body.get("interests"), version)
            ids = {i["id"] for i in new["interests"]}
            write_json_public(cfg.interests_path, new)
            new_rev = file_rev(cfg.interests_path)
            _f, rows = load_review(cfg)
        warnings = []
        for r in rows:
            gone = [t for t in (x.lower() for x in split_cell(r.get("tags"))) if t not in ids]
            if gone:
                label = r.get("title") or r.get("id") or r.get("submission_id")
                warnings.append("%s uses removed topic(s): %s" % (label, ", ".join(gone)))
        return 200, {"rev": new_rev, "interests": new, "warnings": warnings}

    def r_import(self, m, q):
        cfg = self.cfg
        n = self._content_length(True)
        updir = self._uploads_dir()
        path = os.path.join(updir, "export-%s.zip" % secrets.token_hex(8))
        self._stream_to(n, path, MAX_ZIP_UPLOAD,
                        lambda h: True if h[:4] == b"PK\x03\x04" else None, "Export zip")
        log = []
        try:
            with cfg.lock:
                backup_review(cfg)
                try:
                    res = G.import_export(path, cfg.review, interests_path=cfg.interests_path, log=log.append)
                except (G.UnsafeExport, ValueError, zipfile.BadZipFile) as e:
                    raise ApiError(400, "Import refused: %s" % (str(e) or type(e).__name__))
                rev = review_rev(cfg)
        finally:
            if os.path.exists(path):
                os.unlink(path)
        summary = {k: res.get(k) for k in ("rows", "added", "updated", "warnings")} if isinstance(res, dict) else {}
        return 200, {"rev": rev, "log": [str(x) for x in log], "ok": True, "summary": summary}

    def r_seed(self, m, q, body):
        cfg = self.cfg
        with cfg.lock:
            if os.path.exists(cfg.review):
                raise ApiError(409, "review.csv already exists; refusing to overwrite it")
            pub = read_json_file(cfg.projects_path, None)
            if not isinstance(pub, dict) or not isinstance(pub.get("projects"), list):
                raise ApiError(400, "No published projects.json to start from")
            rows = []
            for p in pub["projects"]:
                if not isinstance(p, dict) or not p.get("id"):
                    continue
                # generated files/ and thumbs/ links are rebuilt by the build; seeding them would leave dead links
                links = [l for l in (p.get("links") or [])
                         if isinstance(l, dict) and not str(l.get("url", "")).startswith(("files/", "thumbs/"))]
                row = {c: "" for c in L.CSV_COLUMNS}
                row.update({
                    "submission_id": "manual-" + str(p["id"]), "publish": "yes", "id": str(p["id"]),
                    "title": p.get("title", ""), "students": "; ".join(p.get("students") or []),
                    "summary": p.get("summary", ""), "description": p.get("description", "") or "",
                    "links": L.format_links([{"kind": l.get("kind", "other"), "url": l.get("url", ""),
                                              "label": l.get("label", "")} for l in links]),
                    "tags": "; ".join(p.get("tags") or []), "tools": "; ".join(p.get("tools") or []),
                    "keywords": "; ".join(p.get("keywords") or []),
                    "featured": "yes" if p.get("featured") else "no", "include_poster": "no",
                    "notes": "sample data" if pub.get("demo") else "",
                })
                row["thumbnail_file"] = self._seed_thumbnail(p)
                rows.append(row)
            save_rows(cfg, list(L.CSV_COLUMNS), rows)
            return 200, {"rev": review_rev(cfg), "count": len(rows)}

    SEED_THUMB_RE = re.compile(r"^thumbs/([a-z0-9][a-z0-9-]{0,80})\.webp$")

    def _seed_thumbnail(self, p):
        """Carry a live card image into the review sheet, so the next build keeps it instead of deleting it
        as a stale generated file. Only a regular, non-symlinked WebP under showcase/thumbs/ is copied."""
        m = self.SEED_THUMB_RE.match(str(p.get("thumbnail") or ""))
        if not m:
            return ""
        thumbs = os.path.realpath(os.path.join(self.cfg.site_dir, "thumbs"))
        src = os.path.join(thumbs, m.group(1) + ".webp")
        if os.path.islink(src) or not os.path.isfile(src) or os.path.dirname(os.path.realpath(src)) != thumbs:
            return ""
        if os.path.getsize(src) > 25 * 1024 * 1024:
            return ""
        with open(src, "rb") as f:
            data = f.read()
        if not (data[:4] == b"RIFF" and data[8:12] == b"WEBP"):
            return ""
        name = "%s-thumb-%s.webp" % (L.slugify(str(p["id"])) or "x", secrets.token_hex(4))
        atomic_write(os.path.join(self._uploads_dir(), name), data, 0o600)
        return "uploads/" + name

    def r_build(self, m, q, body):
        cfg = self.cfg
        skip = bool(body.get("skipInvalid"))
        pdfs = bool(body.get("publishPdfs"))
        log = []
        with cfg.lock:
            if not os.path.isfile(cfg.review):
                raise ApiError(400, "There is no review.csv yet; import or add projects first")
            term = get_site_doc(cfg).get("term") or "Autumn 2026"
            try:
                code, summ = B.build(cfg.review, cfg.site_dir, term=term, skip_invalid=skip, allow_empty=False,
                                     interests_path=cfg.interests_path, log=log.append, publish_pdfs=pdfs)
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                raise ApiError(500, "Build crashed (see the terminal running admin.py)", log=log)
        return 200, {"code": code, "ok": code == 0, "log": [str(x) for x in log],
                     "published": summ.get("published", 0),
                     "skipped": [list(x) for x in summ.get("skipped", [])],
                     "warnings": [list(x) for x in summ.get("warnings", [])]}

    def r_backups(self, m, q):
        out = []
        try:
            names = os.listdir(self.cfg.backups)
        except OSError:
            names = []
        for n in names:
            if BACKUP_RE.match(n):
                st = os.stat(os.path.join(self.cfg.backups, n))
                out.append({"name": n, "size": st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "_k": st.st_mtime_ns})
        out.sort(key=lambda d: (d["_k"], d["name"]), reverse=True)
        for d in out:
            del d["_k"]
        return 200, out

    def r_restore(self, m, q, body):
        cfg = self.cfg
        name = body.get("name")
        if not isinstance(name, str) or not BACKUP_RE.match(name):
            raise ApiError(400, "Bad backup name")
        p = os.path.join(cfg.backups, name)
        if not os.path.isfile(p) or os.path.islink(p):
            raise ApiError(404, "No such backup")
        with cfg.lock:
            with open(p, "rb") as f:
                data = f.read()
            backup_review(cfg)
            private_dir(cfg.work_dir)
            atomic_write(cfg.review, data, 0o600)
            return 200, {"rev": review_rev(cfg)}


# ------------------------------------------------------------------ factory / main
MAX_CONNECTIONS = 32


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        ThreadingHTTPServer.__init__(self, *args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)  # over the cap: drop the connection
            return
        try:
            ThreadingHTTPServer.process_request(self, request, client_address)
        except BaseException:
            self._slots.release()  # the worker thread never started, so it will not release
            raise

    def process_request_thread(self, request, client_address):
        try:
            ThreadingHTTPServer.process_request_thread(self, request, client_address)
        finally:
            self._slots.release()


def make_server(site_dir=None, work_dir=None, port=0, token=None, admin_dir=None, quiet=False):
    cfg = Config(site_dir or L.DEFAULT_SITE, work_dir or L.DEFAULT_WORK,
                 token or secrets.token_urlsafe(32), admin_dir or ADMIN_DIR, quiet)
    srv = AdminServer(("127.0.0.1", port), Handler)
    srv.cfg = cfg
    return srv


def launch_browser(url, ttl=20.0):
    """Open `url` via a one-shot 0600 launcher file so the token never appears in process arguments.
    Returns cleanup(); the file and its 0700 dir are also removed `ttl` seconds later."""
    d = tempfile.mkdtemp(prefix="cs193t-admin-")  # mkdtemp is 0700
    fd, path = tempfile.mkstemp(suffix=".html", dir=d)
    state = {"done": False}
    lock = threading.Lock()

    def cleanup():
        with lock:
            if state["done"]:
                return
            state["done"] = True
        shutil.rmtree(d, ignore_errors=True)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write('<!doctype html><meta charset="utf-8">'
                    '<meta http-equiv="refresh" content="0;url=%s">'
                    '<a href="%s">Open the admin editor</a>\n' % ((html.escape(url, quote=True),) * 2))
        os.chmod(path, 0o600)
        webbrowser.open(Path(path).as_uri())
    except BaseException:
        cleanup()
        raise
    t = threading.Timer(ttl, cleanup)
    t.daemon = True
    t.start()
    return cleanup


def main(argv=None):
    ap = argparse.ArgumentParser(description="Local-only admin editor for the showcase. Never deploy this.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    try:
        srv = make_server(port=args.port)
    except OSError as e:
        print("ERROR: cannot listen on 127.0.0.1:%d (%s)" % (args.port, e), file=sys.stderr)
        return 2
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/#t=%s" % (port, srv.cfg.token)
    print("Admin: %s" % url, flush=True)
    print("Local only. Press Ctrl+C to stop.", flush=True)
    cleanup = None
    if not args.no_browser:
        try:
            cleanup = launch_browser(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if cleanup:
            cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
