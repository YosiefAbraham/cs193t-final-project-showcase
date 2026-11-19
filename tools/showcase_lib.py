"""Shared helpers for the CS193T showcase tools (Python 3.9+, standard library only).

Contents: URL validation/normalization, link-kind inference, slugify, interest
tagging, a minimal parser for the Gradescope metadata YAML subset, CSV safety
helpers, and optional-dependency PDF text / thumbnail helpers.
"""
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
DEFAULT_SITE = os.path.join(ROOT, "showcase")
DEFAULT_WORK = os.path.join(TOOLS_DIR, "work")
DEFAULT_REVIEW = os.path.join(DEFAULT_WORK, "review.csv")

# ---------------------------------------------------------------- SPEC limits
LINK_KINDS = ("website", "pdf", "image", "artifact", "dashboard", "git", "video", "doc", "other")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_TITLE = 90
MAX_STUDENT = 60
MAX_STUDENTS = 8
MAX_SUMMARY = 280
MAX_DESCRIPTION = 1500
MAX_LINKS = 6
MAX_LABEL = 60
MAX_TAGS = 4
MAX_TOOLS = 8
MAX_KEYWORDS = 12
MAX_KEYWORD_LEN = 40
MAX_URL = 2048

CSV_COLUMNS = [
    "submission_id", "publish", "id", "title", "students", "summary", "description",
    "links", "tags", "tools", "keywords", "featured", "poster_file", "thumbnail_file", "include_poster", "notes",
]

# ------------------------------------------------------------------ text utils
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
# bidi controls and zero-width characters (used for spoofing)
_INVISIBLE_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff]")


def clean_text(s, keep_newlines=False):
    """Normalize to NFC, drop control/invisible characters, trim."""
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    if keep_newlines:
        s = s.replace("\t", " ")
        s = _INVISIBLE_RE.sub("", _CTRL_RE.sub("", s.replace("\n", "\x01"))).replace("\x01", "\n")
        s = "\n".join(line.rstrip() for line in s.split("\n"))
        s = re.sub(r"\n{3,}", "\n\n", s)
    else:
        s = _INVISIBLE_RE.sub("", _CTRL_RE.sub("", s.replace("\n", " ").replace("\t", " ")))
        s = re.sub(r" {2,}", " ", s)
    return s.strip()


def slugify(text, maxlen=48):
    s = unicodedata.normalize("NFKD", text or "")
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = s[:maxlen].strip("-")
    return s


def unique_slug(base, taken):
    """Return a slug based on `base` not in `taken` (set). Does not mutate `taken`."""
    base = slugify(base) or "project"
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = "-%d" % n
        cand = base[: 64 - len(suffix)].rstrip("-") + suffix
        if cand not in taken:
            return cand
        n += 1


# --------------------------------------------------------------------- URLs
_TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_eid", "mc_cid", "igshid", "yclid"}
_WS_CTRL_RE = re.compile(r"[\s\x00-\x1f\x7f-\x9f\u2028\u2029]")
_REL_RE = re.compile(r"^(files|thumbs)/[a-z0-9][a-z0-9-]*\.(pdf|webp)$")


def _is_tracking(key):
    k = key.lower()
    return k.startswith("utm_") or k in _TRACKING_PARAMS


def validate_url(url, allow_relative=True):
    """Validate and normalize a URL per SPEC.md.

    Returns (ok, value_or_reason). On success value is the normalized URL.
    Allowed: absolute https URLs, or relative paths starting with files/ or thumbs/.
    """
    if not isinstance(url, str):
        return False, "not a string"
    u = url.strip()
    if not u:
        return False, "empty URL"
    if len(u) > MAX_URL:
        return False, "URL longer than %d chars" % MAX_URL
    if _WS_CTRL_RE.search(u):
        return False, "URL contains whitespace or control characters"
    if _INVISIBLE_RE.search(u):
        return False, "URL contains invisible/bidi characters"
    if "\\" in u:
        return False, "URL contains a backslash"
    if u.startswith("//"):
        return False, "protocol-relative URL"

    # relative (self-hosted) paths
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        if not allow_relative:
            return False, "relative URL not allowed here"
        if ".." in u.split("/") or "//" in u or "?" in u or "#" in u:
            return False, "unsafe relative path"
        if not _REL_RE.match(u):
            return False, "relative path must match ^(files|thumbs)/[a-z0-9][a-z0-9-]*\\.(pdf|webp)$"
        return True, u

    try:
        parts = urlsplit(u)
    except ValueError as e:
        return False, "unparseable URL (%s)" % e
    scheme = parts.scheme.lower()
    if scheme != "https":
        return False, "scheme %r not allowed (https only)" % scheme
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return False, "credentials in URL"
    try:
        host = parts.hostname
        port = parts.port
    except ValueError:
        return False, "bad host or port"
    if not host:
        return False, "missing host"
    if port not in (None, 443):
        return False, "non-standard port"
    # non-ASCII is only tolerated in the host, where it is converted to punycode
    rest = u[len("https://"):]
    rest_after_host = rest[len(parts.netloc):]
    if any(ord(c) > 127 for c in rest_after_host):
        return False, "non-ASCII characters in path/query (percent-encode them)"
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False, "invalid host name"
    if not re.match(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", ascii_host):
        return False, "invalid host name"
    if "[" in parts.netloc or ":" in host:
        return False, "IP literal hosts not allowed"
    labels = ascii_host.split(".")
    if all(re.fullmatch(r"0x[0-9a-f]*|\d+", lb) for lb in labels) or re.fullmatch(r"\d+|0x[0-9a-f]*", labels[-1]):
        return False, "numeric/IP-like host not allowed"
    try:
        ipaddress.ip_address(ascii_host)
        return False, "IP address hosts not allowed"
    except ValueError:
        pass
    try:
        socket.inet_aton(ascii_host)
        return False, "IP address hosts not allowed"
    except (OSError, UnicodeError):
        pass
    if ascii_host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
        return False, "non-public host"
    if len(ascii_host) > 253:
        return False, "host too long"
    if ascii_host in ("gradescope.com", "www.gradescope.com") or ascii_host.endswith(".gradescope.com"):
        return False, "Gradescope URLs must not be published"

    query = ""
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
        query = urlencode(kept)
    path = parts.path or ("/" if (query or parts.fragment) else "")
    out = urlunsplit(("https", ascii_host, path, query, parts.fragment))
    if len(out) > MAX_URL:
        return False, "URL longer than %d chars" % MAX_URL
    return True, out


def infer_kind(url):
    """Infer a link kind from a URL per SPEC.md."""
    u = (url or "").strip()
    low = u.lower()
    if low.startswith(("files/", "thumbs/")):
        path = low
        host = ""
    else:
        try:
            p = urlsplit(low)
        except ValueError:
            return "other"
        host = (p.hostname or "")
        path = p.path or ""
    def host_is(*names):
        return any(host == n or host.endswith("." + n) for n in names)
    if host_is("github.com", "gitlab.com"):
        return "git"
    if host_is("claude.site") or (host_is("claude.ai") and path.startswith("/artifact")):
        return "artifact"
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    if host_is("youtube.com", "youtu.be", "vimeo.com"):
        return "video"
    if host_is("docs.google.com", "notion.site"):
        return "doc"
    if host_is("lookerstudio.google.com", "streamlit.app", "observablehq.com") or "tableau" in host:
        return "dashboard"
    if low.startswith(("files/", "thumbs/")):
        return "other"
    return "website"


def default_label(kind, url):
    names = {"website": "Website", "pdf": "PDF", "image": "Image", "artifact": "Claude artifact",
             "dashboard": "Dashboard", "git": "Code", "video": "Video", "doc": "Document", "other": "Link"}
    return names.get(kind, "Link")


def format_links(links):
    """links: list of dicts -> one `kind | url | label` per line."""
    return "\n".join("%s | %s | %s" % (l["kind"], l["url"], l.get("label", "")) for l in links)


def parse_links(text):
    """Parse the `kind | url | label` cell. Returns (links, errors).

    Lines may also be just a URL (kind inferred) or `url | label`.
    URLs are validated and normalized; invalid lines are reported, not returned.
    """
    links, errors = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        kind = label = None
        if len(parts) >= 2 and parts[0].lower() in LINK_KINDS:
            kind, url = parts[0].lower(), parts[1]
            label = " | ".join(parts[2:]).strip() if len(parts) > 2 else ""
        elif len(parts) >= 2 and parts[0].lower() not in LINK_KINDS and not re.match(r"^[a-z][a-z0-9+.-]*:", parts[0].lower()) and len(parts[0]) < 15 and not parts[0].startswith(("files/", "thumbs/")):
            errors.append("unknown link kind %r in %r" % (parts[0], line[:80]))
            continue
        else:
            url = parts[0]
            label = " | ".join(parts[1:]).strip() if len(parts) > 1 else ""
        ok, val = validate_url(url)
        if not ok:
            errors.append("%s: %s" % (val, url[:80]))
            continue
        if not kind:
            kind = infer_kind(val)
        label = clean_text(label)[:MAX_LABEL] if label else default_label(kind, val)
        links.append({"kind": kind, "url": val, "label": label})
    return links, errors


_URL_FIND_RE = re.compile(r"https?://[^\s<>\"'\\^`{|}\x00-\x1f\x7f]+", re.I)


def find_urls(text):
    """Find candidate http(s) URLs in plain text (never fetched). Returns list, deduped, in order."""
    found, seen = [], set()
    for m in _URL_FIND_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)]}>*")
        # balance: drop trailing ')' only if unmatched handled above; good enough
        if u and u not in seen:
            seen.add(u)
            found.append(u)
    return found


# ------------------------------------------------------------------ interests
def load_interests(path):
    import json
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _term_re(term):
    return re.compile(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])")


def suggest_tags(interests, title="", summary="", text="", top=3):
    """Suggest interest ids by term matches. Title counts 3x, summary 2x, other text 1x (capped)."""
    scored = []
    t, s, x = (title or "").lower(), (summary or "").lower(), (text or "").lower()[:20000]
    for it in interests.get("interests", []):
        score = 0
        for term in it.get("terms", []):
            rx = _term_re(term)
            if rx.search(t):
                score += 3
            if rx.search(s):
                score += 2
            n = len(rx.findall(x))
            score += min(n, 3)
        if score:
            scored.append((-score, it["id"]))
    scored.sort()
    return [i for _, i in scored[:top]]


def detect_tools(interests, *texts):
    """Detect tool names mentioned (longest names first so 'Claude Code' hides 'Claude')."""
    blob = " \n ".join(t or "" for t in texts).lower()[:50000]
    names = sorted(interests.get("tools", []), key=len, reverse=True)
    found = []
    for name in names:
        rx = _term_re(name)
        if rx.search(blob):
            found.append(name)
            blob = rx.sub(" ", blob)
    order = {n: i for i, n in enumerate(interests.get("tools", []))}
    return sorted(found, key=lambda n: order[n])


# ----------------------------------------------------------------- CSV safety
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Prefix a single quote to cells that spreadsheets would interpret as formulas."""
    v = "" if value is None else str(value)
    if v.startswith(_FORMULA_START) or v.startswith("'"):
        return "'" + v
    return v


def csv_unsafe(value):
    """Undo csv_safe: strip one leading single quote if a formula char or another quote follows it."""
    v = "" if value is None else str(value)
    if len(v) > 1 and v[0] == "'" and v[1] in "=+-@\t\r'":
        return v[1:]
    return v


MAX_YAML_LINE = 4096


# ------------------------------------------------- minimal Gradescope YAML subset
def _scalar(tok):
    t = tok.strip()
    # strip YAML tags like !ruby/object:Foo or !!str
    while t.startswith("!"):
        parts = t.split(None, 1)
        t = parts[1] if len(parts) > 1 else ""
    if t == "" or t in ("~", "null", "Null", "NULL"):
        return None
    if t[0] == '"':
        end = t.rfind('"')
        body = t[1:end] if end > 0 else t[1:]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}.get(m.group(1), m.group(1)), body)
    if t[0] == "'":
        end = t.rfind("'")
        body = t[1:end] if end > 0 else t[1:]
        return body.replace("''", "'")
    # strip trailing comment
    t = re.sub(r"\s+#.*$", "", t)
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if re.fullmatch(r"[-+]?\d+", t):
        try:
            return int(t)
        except ValueError:
            return t
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+)([eE][-+]?\d+)?", t):
        try:
            return float(t)
        except ValueError:
            return t
    if t == "[]":
        return []
    if t == "{}":
        return {}
    if t.startswith("[") and t.endswith("]"):
        return [_scalar(p) for p in t[1:-1].split(",") if p.strip()]
    if t.startswith(":") and re.fullmatch(r":[A-Za-z_][A-Za-z0-9_]*", t):
        return t[1:]  # Ruby symbol value
    return t


def _split_key(text):
    """Split `key: value` honoring quoted keys and leading-colon symbol keys. Returns (key, rest) or None."""
    i = 0
    if text.startswith(":"):
        i = 1
    if i < len(text) and text[i] in "\"'":
        q = text[i]
        end = text.find(q, i + 1)
        if end == -1:
            return None
        key = text[i + 1:end]
        after = text[end + 1:]
        if after.startswith(":") and (len(after) == 1 or after[1] in " \t"):
            return key, after[1:].strip()
        return None
    if text.startswith(("[", "{")) or i >= len(text) or text[i] in " \t:#":
        return None
    pos = i + 1
    while True:
        c = text.find(":", pos)
        if c == -1:
            return None
        if c == len(text) - 1 or text[c + 1] in " \t":
            return text[i:c].rstrip(), text[c + 1:].strip()
        pos = c + 1


def parse_gradescope_yaml(text, max_depth=40):
    """Parse the YAML subset emitted by Gradescope's submission_metadata.yml.

    Handles nested mappings, sequences (including `- ` at the same indent as the parent key),
    quoted/plain scalars, Ruby symbol keys (`:name:` -> "name"), block scalars (| and >),
    and ignores unknown tags. Never raises on malformed input; returns {} on failure.
    """
    try:
        return _parse_yaml(text, max_depth)
    except (RecursionError, IndexError, ValueError):
        return {}


def _parse_yaml(text, max_depth):
    raw_lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = []  # (indent, text, rawindent)
    for raw in raw_lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith(("---", "...")) and not raw.strip("-. "):
            continue
        raw = raw[:MAX_YAML_LINE].replace("\t", "  ")
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        # explode "- rest" into marker + child so mappings in sequences parse uniformly
        while body.startswith("- ") or body == "-":
            lines.append((indent, "-"))
            rest = body[1:].lstrip(" ")
            consumed = len(body) - len(rest)
            indent += consumed
            body = rest
            if not body:
                break
        if body:
            lines.append((indent, body))
    pos = [0]

    def peek():
        return lines[pos[0]] if pos[0] < len(lines) else None

    def parse_node(indent, depth):
        if depth > max_depth:
            raise ValueError("too deep")
        cur = peek()
        if cur is None:
            return None
        if cur[1] == "-":
            return parse_seq(cur[0], depth)
        if _split_key(cur[1]) is None:
            pos[0] += 1
            return _scalar(cur[1])
        return parse_map(cur[0], depth)

    def parse_value(rest, key_indent, depth):
        if rest in ("|", ">", "|-", ">-", "|+", ">+"):
            buf = []
            while peek() and peek()[0] > key_indent:
                buf.append(peek()[1])
                pos[0] += 1
            return ("\n" if rest[0] == "|" else " ").join(buf)
        if rest:
            return _scalar(rest)
        nxt = peek()
        if nxt and nxt[0] > key_indent:
            return parse_node(nxt[0], depth + 1)
        if nxt and nxt[0] == key_indent and nxt[1] == "-":
            return parse_seq(key_indent, depth + 1)
        return None

    def parse_map(indent, depth):
        out = {}
        while True:
            cur = peek()
            if cur is None or cur[0] != indent or cur[1] == "-":
                break
            kv = _split_key(cur[1])
            pos[0] += 1
            if kv is None:
                continue
            key, rest = kv
            out[key] = parse_value(rest, indent, depth)
        return out

    def parse_seq(indent, depth):
        out = []
        while True:
            cur = peek()
            if cur is None or cur[0] != indent or cur[1] != "-":
                break
            pos[0] += 1
            nxt = peek()
            if nxt and nxt[0] > indent and nxt[1] != "-" or (nxt and nxt[0] > indent):
                out.append(parse_node(nxt[0], depth + 1))
            else:
                out.append(None)
        return out

    result = parse_node(lines[0][0], 0) if lines else {}
    return result if isinstance(result, dict) else {}


# --------------------------------------------------- external-tool helpers
def _which(name, fallbacks=()):
    p = shutil.which(name)
    if p:
        return p
    for f in fallbacks:
        if os.path.isfile(f) and os.access(f, os.X_OK):
            return f
    return None


def have_pymupdf():
    try:
        import fitz  # noqa: F401
        return True
    except Exception:
        return False


_JXA = (
    'ObjC.import("PDFKit"); function run(argv){'
    ' var d=$.PDFDocument.alloc.initWithURL($.NSURL.fileURLWithPath(argv[0]));'
    ' if(!d||d.isNil()) return ""; var s=d.string; if(!s||s.isNil()) return ""; return ObjC.unwrap(s); }'
)


def extract_pdf_text(path, max_chars=200000, timeout=30):
    """Best-effort text of a PDF. Returns (text, warning_or_None). Never raises."""
    text, warn = "", None
    try:
        try:
            import fitz  # PyMuPDF, optional
            doc = fitz.open(path)
            try:
                parts = []
                for i, page in enumerate(doc):
                    if i >= 5:
                        break
                    parts.append(page.get_text())
                    for l in page.get_links():
                        if l.get("uri"):
                            parts.append(l["uri"])
                text = "\n".join(parts)
            finally:
                doc.close()
        except ImportError:
            osa = _which("osascript", ["/usr/bin/osascript"]) if sys.platform == "darwin" else None
            if osa:
                r = subprocess.run([osa, "-l", "JavaScript", "-e", _JXA, path], capture_output=True,
                                   timeout=timeout, stdin=subprocess.DEVNULL)
                if r.returncode == 0:
                    text = r.stdout.decode("utf-8", "replace")
                else:
                    warn = "PDF text extraction failed"
            else:
                warn = "no PDF text extractor available"
    except Exception as e:  # noqa: BLE001 - extraction must never abort an import
        warn = "PDF text extraction failed (%s)" % type(e).__name__
    # raw scan for link annotations (uncompressed objects only); bounded and linear
    try:
        with open(path, "rb") as f:
            raw = f.read(RAW_SCAN_BYTES)
        uris, pos, seen = [], 0, 0
        while seen < 500:
            i = raw.find(b"/URI", pos)
            if i == -1:
                break
            seen += 1
            pos = i + 4
            j = pos
            while j < len(raw) and j < pos + 8 and raw[j] in b" \t\r\n":
                j += 1
            if j < len(raw) and raw[j:j + 1] == b"(":
                k = raw.find(b")", j + 1, j + 2050)
                if k != -1 and b"\\" not in raw[j + 1:k]:
                    uris.append(raw[j + 1:k].decode("latin-1"))
        if uris:
            text += "\n" + "\n".join(uris)
    except OSError:
        pass
    return text[:max_chars], warn


RAW_SCAN_BYTES = 4 * 1024 * 1024


def pdf_info_title(path):
    """Read /Title from the PDF Info dictionary if stored uncompressed. Returns str or ''."""
    try:
        with open(path, "rb") as f:
            raw = f.read(RAW_SCAN_BYTES)
    except OSError:
        return ""
    pos = 0
    for _ in range(20):
        i = raw.find(b"/Title", pos)
        if i == -1:
            return ""
        pos = i + 6
        win = raw[pos:pos + 700].lstrip(b" \t\r\n")
        b = None
        if win.startswith(b"("):
            m = re.match(rb"\(((?:[^()\\]|\\.){1,300})\)", win)
            if m:
                b = re.sub(rb"\\(.)", lambda mm: {b"n": b" ", b"r": b" ", b"t": b" "}.get(mm.group(1), mm.group(1)), m.group(1))
        elif win.startswith(b"<"):
            m = re.match(rb"<([0-9A-Fa-f\s]{2,600})>", win)
            if m:
                try:
                    b = bytes.fromhex(re.sub(rb"\s", b"", m.group(1)).decode())
                except ValueError:
                    b = None
        if b is None:
            continue
        s = b[2:].decode("utf-16-be", "replace") if b.startswith(b"\xfe\xff") else b.decode("latin-1", "replace")
        s = clean_text(s)
        if s:
            return s
    return ""


def is_pdf(path):
    try:
        with open(path, "rb") as f:
            return b"%PDF-" in f.read(1024)
    except OSError:
        return False


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff", ".bmp")


MAX_SOURCE_PIXELS_SIDE = 12000


def _image_pixel_size(path, sips, timeout=30):
    """Return (width, height) from image headers without decoding, or None if unknown."""
    if sips:
        try:
            r = subprocess.run([sips, "-g", "pixelWidth", "-g", "pixelHeight", path],
                               capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            out = r.stdout.decode("utf-8", "replace")
            w = re.search(r"pixelWidth:\s*(\d+)", out)
            h = re.search(r"pixelHeight:\s*(\d+)", out)
            if r.returncode == 0 and w and h:
                return int(w.group(1)), int(h.group(1))
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        from PIL import Image
        with Image.open(path) as im:  # reads the header only
            return im.size
    except Exception:
        return None


def make_thumbnail(src, dest_webp, max_side=960, timeout=90):
    """Render page 1 of a PDF (or convert an image) to a metadata-free WebP whose longest side is
    at most `max_side` pixels (bounds both width and height; never above 4000 with PyMuPDF).

    Returns (ok, message). Tries PyMuPDF/Pillow if importable, then macOS `sips` + `cwebp`.
    Only paths created by the caller are passed; subprocess uses argument lists.
    """
    max_side = max(16, min(int(max_side), 4000))
    tmp = tempfile.mkdtemp(prefix="thumb_")
    try:
        ext = os.path.splitext(src)[1].lower()
        pdf = ext == ".pdf" or is_pdf(src)
        png = os.path.join(tmp, "page.png")
        sips = _which("sips", ["/usr/bin/sips"])
        rendered = False
        if pdf:
            try:
                import fitz
                doc = fitz.open(src)
                try:
                    page = doc[0]
                    zoom = float(max_side) / max(page.rect.width, page.rect.height, 1)
                    page.get_pixmap(matrix=fitz.Matrix(zoom, zoom)).save(png)
                    rendered = True
                finally:
                    doc.close()
            except Exception:
                rendered = False
            if not rendered:
                if not sips:
                    return False, "no PDF renderer available (need macOS sips or PyMuPDF)"
                inp = os.path.join(tmp, "in.pdf")
                shutil.copyfile(src, inp)
                r = subprocess.run([sips, "-s", "format", "png", "-Z", str(max_side), inp, "--out", png],
                                   capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
                rendered = r.returncode == 0 and os.path.isfile(png) and os.path.getsize(png) > 0
                if not rendered:
                    return False, "sips could not render the PDF"
            size = _image_pixel_size(png, sips)
            if size and max(size) > MAX_SOURCE_PIXELS_SIDE:
                return False, "rendered PDF page is too large (%dx%d px; limit %d per side)" % (size[0], size[1], MAX_SOURCE_PIXELS_SIDE)
        else:
            if ext not in IMAGE_EXTS:
                return False, "poster is neither a PDF nor a known image type"
            inp = os.path.join(tmp, "in" + ext)
            shutil.copyfile(src, inp)
            size = _image_pixel_size(inp, sips)
            if size and max(size) > MAX_SOURCE_PIXELS_SIDE:
                return False, "image is too large (%dx%d px; limit %d per side)" % (size[0], size[1], MAX_SOURCE_PIXELS_SIDE)
            if sips:
                r = subprocess.run([sips, "-s", "format", "png", "-Z", str(max_side), inp, "--out", png],
                                   capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
                rendered = r.returncode == 0 and os.path.isfile(png) and os.path.getsize(png) > 0
            if not rendered:
                try:
                    from PIL import Image
                    with Image.open(inp) as im:
                        im.thumbnail((max_side, max_side))
                        im.convert("RGB").save(png, "PNG")
                    rendered = True
                except Exception:
                    return False, "could not bound image size (need sips or Pillow)"
        out = os.path.join(tmp, "out.webp")
        cwebp = _which("cwebp", ["/opt/homebrew/bin/cwebp", "/usr/local/bin/cwebp"])
        if cwebp:
            r = subprocess.run([cwebp, "-quiet", "-q", "80", "-metadata", "none", png, "-o", out],
                               capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                _move(out, dest_webp)
                return True, "ok"
        try:
            from PIL import Image
            with Image.open(png) as im:
                im = im.convert("RGB")
                im.thumbnail((max_side, max_side))
                im.save(out, "WEBP", quality=80)
            _move(out, dest_webp)
            return True, "ok (Pillow)"
        except Exception:
            pass
        return False, "no WebP encoder available (need cwebp or Pillow)"
    except subprocess.TimeoutExpired:
        return False, "thumbnail tool timed out"
    except Exception as e:  # noqa: BLE001
        return False, "thumbnail failed (%s)" % type(e).__name__
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ PII scan
_EMAIL_RE = re.compile(r"[^\s@<>]{1,64}@[^\s@<>]{1,255}\.[^\s@<>]{2,}")
_ID8_RE = re.compile(r"(?<!\d)\d{8}(?!\d)")
_SUNET_RE = re.compile(r"(?i)\b(sunet|sid)\b(\s*id)?\s{0,3}[:#=]?\s{0,3}[a-z0-9]{3,}")


def pii_problem(text):
    """Return a short description if text looks like it contains PII, else None."""
    from urllib.parse import unquote, unquote_plus
    for t in {(text or "")[:6000], unquote((text or "")[:6000]), unquote_plus((text or "")[:6000])}:
        if _EMAIL_RE.search(t):
            return "contains an email-like token"
        if _ID8_RE.search(t):
            return "contains an 8-digit number (possible student ID)"
        if _SUNET_RE.search(t):
            return "mentions a SUNet/SID identifier"
        if "gradescope.com" in t.lower():
            return "mentions gradescope.com"
    return None


PDF_FORBIDDEN_RE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|OpenAction|AA|RichMedia|SubmitForm|Info|Metadata)(?![A-Za-z0-9])")


def pdf_forbidden_names(data):
    """Return sorted names of active-content / metadata keys found in (decompressed) PDF bytes."""
    return sorted({m.group(1).decode() for m in PDF_FORBIDDEN_RE.finditer(data)})


def sanitize_pdf(src, dest, timeout=120):
    """Rewrite a PDF with qpdf, drop /Info and /Metadata, refuse if active content is present.

    Returns (ok, message). Requires qpdf on PATH; never raises.
    """
    qpdf = shutil.which("qpdf")
    if not qpdf:
        return False, "qpdf not found on PATH; PDF not published"
    tmp = tempfile.mkdtemp(prefix="pdfsan_")
    try:
        a, b, c, d = (os.path.join(tmp, n) for n in ("a.qdf", "b.pdf", "c.pdf", "d.qdf"))
        inp = os.path.join(tmp, "in.pdf")
        shutil.copyfile(src, inp)

        def run(args):
            r = subprocess.run([qpdf] + args, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL)
            return r.returncode in (0, 3)
        if not run(["--qdf", "--object-streams=disable", "--remove-restrictions", inp, a]):
            return False, "qpdf could not read the PDF"
        with open(a, "rb") as f:
            data = f.read(300 * 1024 * 1024)
        bad = [n for n in pdf_forbidden_names(data) if n not in ("Info", "Metadata")]
        if bad:
            return False, "PDF contains active content/attachments (%s); not published" % ", ".join(bad)
        data = re.sub(rb"/(Info|Metadata)\s+\d+\s+\d+\s+R", b"", data)
        with open(b, "wb") as f:
            f.write(data)
        if not run(["--linearize", "--object-streams=disable", "--remove-restrictions", b, c]):
            return False, "qpdf could not rewrite the PDF"
        if not run(["--qdf", "--object-streams=disable", c, d]):
            return False, "qpdf could not verify the rewritten PDF"
        with open(d, "rb") as f:
            bad = pdf_forbidden_names(f.read(300 * 1024 * 1024))
        if bad:
            return False, "sanitized PDF still contains %s; not published" % ", ".join(bad)
        _move(c, dest)
        return True, "ok"
    except subprocess.TimeoutExpired:
        return False, "qpdf timed out"
    except Exception as e:  # noqa: BLE001
        return False, "PDF sanitize failed (%s)" % type(e).__name__
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _move(src, dest, mode=0o644):
    """Copy to a temp name beside dest then atomically replace. Outputs are published files, so they are
    world-readable (mkstemp alone would leave them 0600 and the web server would answer 403). Private
    callers keep them private through their 0700 directories."""
    d = os.path.dirname(dest)
    fd, t = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=os.path.splitext(dest)[1])
    os.close(fd)
    try:
        shutil.copyfile(src, t)
        os.chmod(t, mode)
        os.replace(t, dest)
    finally:
        if os.path.exists(t):
            os.unlink(t)
