#!/usr/bin/env python3
"""Turn a Gradescope "Download Submissions" export into a staging CSV for TA review.

    python3 tools/gradescope_import.py --export PATH(.zip or dir) [--out tools/work/review.csv]

Python 3.9+, standard library only. Private data (names, IDs) stays under tools/work/.
"""
import argparse
import csv
import os
import re
import shutil
import stat
import sys
import tempfile
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import showcase_lib as L  # noqa: E402

MAX_TOTAL_BYTES = 2 * 1024 ** 3      # 2 GB uncompressed
MAX_FILES = 20000
MAX_FILE_BYTES = 300 * 1024 ** 2     # per file
MAX_TEXT_BYTES = 1024 * 1024
TEXT_EXTS = (".txt", ".md", ".url", ".webloc", ".html", ".htm", ".csv", ".json")
SKIP_KEYS = {"submitters", "score", "status", "created_at", "id", "sid", "email", "assignment_id"}


class UnsafeExport(Exception):
    pass


# ----------------------------------------------------------- safe extraction
def safe_extract_zip(zip_path, dest):
    """Extract zip_path into dest, blocking zip-slip, symlinks and zip bombs.

    Returns a list of warning strings (skipped entries). Raises UnsafeExport on caps exceeded.
    """
    warnings = []
    dest_real = os.path.realpath(dest)
    total = 0
    count = 0
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise UnsafeExport("zip has %d entries (limit %d)" % (len(infos), MAX_FILES))
        if sum(i.file_size for i in infos) > MAX_TOTAL_BYTES:
            raise UnsafeExport("zip declares more than %d bytes uncompressed" % MAX_TOTAL_BYTES)
        for info in infos:
            name = info.filename
            if "\x00" in name:
                warnings.append("skipped entry with NUL in name")
                continue
            norm = name.replace("\\", "/")
            parts = [p for p in norm.split("/") if p not in ("", ".")]
            if (norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in parts):
                warnings.append("blocked unsafe path in zip: %r" % name[:120])
                continue
            mode = (info.external_attr >> 16) & 0xFFFF
            if mode and stat.S_ISLNK(mode):
                warnings.append("skipped symlink in zip: %r" % name[:120])
                continue
            if mode and (stat.S_ISCHR(mode) or stat.S_ISBLK(mode) or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
                warnings.append("skipped special file in zip: %r" % name[:120])
                continue
            if "__MACOSX" in parts or (parts and parts[-1] == ".DS_Store"):
                continue
            if not parts:
                continue
            target = os.path.join(dest, *parts)
            if not os.path.realpath(target).startswith(dest_real + os.sep):
                warnings.append("blocked path escaping extraction dir: %r" % name[:120])
                continue
            if info.is_dir() or norm.endswith("/"):
                try:
                    os.makedirs(target, exist_ok=True)
                except OSError:
                    warnings.append("skipped directory clashing with existing file: %r" % name[:120])
                continue
            if info.file_size > MAX_FILE_BYTES:
                warnings.append("skipped oversized file (%d MB): %r" % (info.file_size // 1024 ** 2, name[:120]))
                continue
            count += 1
            if count > MAX_FILES:
                raise UnsafeExport("too many files")
            if os.path.lexists(target):
                warnings.append("skipped entry clashing with existing file/dir: %r" % name[:120])
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                out = open(target, "xb")
            except OSError:
                warnings.append("skipped entry clashing with existing file/dir: %r" % name[:120])
                continue
            written = 0
            with zf.open(info) as src, out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    total += len(chunk)
                    if written > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        out.close()
                        raise UnsafeExport("zip exceeds size limits while extracting (possible zip bomb)")
                    out.write(chunk)
    return warnings


# ------------------------------------------------------------ export scanning
def _walk_files(root):
    """Yield regular files under root, skipping symlinks and hidden/macOS junk."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__MACOSX"
                       and not os.path.islink(os.path.join(dirpath, d))]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            if fn.startswith(".") or os.path.islink(p) or not os.path.isfile(p):
                continue
            yield p


def find_metadata(root):
    best = None
    for p in _walk_files(root):
        if os.path.basename(p).lower() in ("submission_metadata.yml", "submission_metadata.yaml"):
            if best is None or p.count(os.sep) < best.count(os.sep):
                best = p
    return best


def collect_submissions(root, meta):
    """Return {submission_id: {"files": [...], "meta": {...}}}."""
    subs = {}
    for key, val in (meta or {}).items():
        m = re.match(r"^submission_(\w+)$", str(key))
        if m:
            subs[m.group(1)] = {"files": [], "meta": val if isinstance(val, dict) else {}}
    # attach files
    for p in _walk_files(root):
        rel = os.path.relpath(p, root).split(os.sep)
        sid = None
        for part in rel:
            m = re.match(r"^submission_(\w+?)(?:\.[A-Za-z0-9]+|_.*)?$", part)
            if m and part.lower() not in ("submission_metadata.yml", "submission_metadata.yaml"):
                sid = m.group(1)
                break
        if sid is None:
            continue
        subs.setdefault(sid, {"files": [], "meta": {}})["files"].append(p)
    return subs


def _read_text(path):
    try:
        with open(path, "rb") as f:
            return f.read(MAX_TEXT_BYTES).decode("utf-8", "replace")
    except OSError:
        return ""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _strip_html(s):
    """Text of an HTML string (script/style dropped). Input truncated; never rendered or fetched."""
    p = _TextExtractor()
    try:
        p.feed(s[:MAX_HTML_CHARS])
        p.close()
    except Exception:  # noqa: BLE001 - malformed markup must not abort the import
        pass
    return " ".join(p.parts)


MAX_HTML_CHARS = 200 * 1024
ANSWER_KEY_HINTS = ("answer", "response", "text", "summary")
DENY_KEY_HINTS = ("comment", "grader", "feedback", "rubric", "score", "evaluation")


def _meta_texts(meta):
    """Free-text answers under metadata keys that look like student answers.

    A string is used only if some ancestor key contains answer/response/text/summary and no
    ancestor key contains comment/grader/feedback/rubric/score/evaluation.
    """
    out = []

    def walk(v, allowed, depth=0):
        if depth > 6:
            return
        if isinstance(v, str):
            if allowed and len(v.strip()) > 1:
                out.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                kl = str(k).lower()
                if kl in SKIP_KEYS or any(d in kl for d in DENY_KEY_HINTS):
                    continue
                walk(x, allowed or any(h in kl for h in ANSWER_KEY_HINTS), depth + 1)
        elif isinstance(v, list):
            for x in v:
                walk(x, allowed, depth + 1)
    walk(meta, False)
    return out


def _submitter_names(meta):
    names = []
    subs = meta.get("submitters") if isinstance(meta, dict) else None
    if isinstance(subs, dict):
        subs = [subs]
    for s in subs or []:
        if isinstance(s, dict) and s.get("name"):
            n = L.clean_text(str(s["name"]))[:L.MAX_STUDENT]
            if n and "@" not in n:
                names.append(n)
        elif isinstance(s, str) and "@" not in s:
            names.append(L.clean_text(s)[:L.MAX_STUDENT])
    return names


def _generic_name(stem):
    return bool(re.match(r"^(submission[_ -]?\w*|poster|final[_ -]?project|project|untitled|document|slides?|scan|notes?|readme|links?)$", stem, re.I))


def guess_title(pdf_title, text, files):
    t = L.clean_text(pdf_title or "")
    if t and not re.match(r"(?i)^(untitled|microsoft|powerpoint|slide|document|canva|figma)", t) and 3 <= len(t) <= L.MAX_TITLE:
        return t
    for line in (text or "").splitlines():
        line = L.clean_text(line)
        if 4 <= len(line) <= L.MAX_TITLE and "http" not in line.lower() and "@" not in line and re.search(r"[A-Za-z]{3}", line):
            return line
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        if stem and not _generic_name(stem):
            return L.clean_text(re.sub(r"[_]+", " ", stem))[:L.MAX_TITLE]
    return ""


def analyze_submission(sid, info, interests, work_dir, files_dir):
    """Build one review row (dict of CSV columns) from a submission."""
    notes = []
    files = sorted(info["files"])
    meta = info.get("meta", {})
    names = _submitter_names(meta)
    if not names:
        notes.append("no student names found; fill in students")

    pdfs = [f for f in files if f.lower().endswith(".pdf") and L.is_pdf(f)]
    fake_pdfs = [f for f in files if f.lower().endswith(".pdf") and f not in pdfs]
    pptx = [f for f in files if f.lower().endswith((".pptx", ".ppt", ".key"))]
    imgs = [f for f in files if f.lower().endswith(L.IMAGE_EXTS)]
    for f in fake_pdfs:
        notes.append("%s has .pdf extension but is not a PDF; ignored" % os.path.basename(f)[:60])

    def poster_rank(p):
        low = os.path.basename(p).lower()
        return (0 if "poster" in low else 1, -os.path.getsize(p))
    poster = sorted(pdfs, key=poster_rank)[0] if pdfs else None
    if len(pdfs) > 1:
        notes.append("%d PDFs found; using %s" % (len(pdfs), os.path.basename(poster)[:60]))
    thumb_src = None
    named = [f for f in imgs if re.search(r"thumbnail|thumb|cover|card|preview|screenshot", os.path.basename(f), re.I)]
    if named:
        thumb_src = sorted(named, key=lambda p: (-os.path.getsize(p), p))[0]
    elif poster and len(imgs) == 1:
        thumb_src = imgs[0]
        notes.append("image used as card image; check")
    elif imgs and not poster:
        thumb_src = sorted(imgs, key=lambda p: (-os.path.getsize(p), p))[0]
    if not poster:
        if pptx:
            notes.append("poster is PPTX/Keynote: export to PDF and set poster_file manually")
        if not files:
            notes.append("no files in submission")
        elif not pptx and not imgs:
            notes.append("no PDF poster found")

    # text sources
    texts, urls = [], []
    pdf_text = ""
    pdf_title = ""
    if poster:
        pdf_text, warn = L.extract_pdf_text(poster)
        if warn:
            notes.append(warn)
        pdf_title = L.pdf_info_title(poster)
        urls += L.find_urls(pdf_text)
    answer_texts = []
    for f in files:
        if f.lower().endswith(TEXT_EXTS):
            t = _read_text(f)
            if f.lower().endswith((".html", ".htm")):
                urls += L.find_urls(t)      # URLs from raw text only; HTML is never rendered
                t = _strip_html(t)
            else:
                urls += L.find_urls(t)
            texts.append(t)
            if f.lower().endswith((".txt", ".md")) and not re.match(r"(?i)(links?|urls?)", os.path.basename(f)):
                answer_texts.append(t)
    meta_used = _meta_texts(meta)
    if meta_used:
        notes.append("text taken from metadata answers; verify it is the student's own")
    for t in meta_used:
        answer_texts.append(t)
        urls += L.find_urls(t)
        texts.append(t)

    # links
    links, seen, skipped = [], set(), 0
    for u in urls:
        ok, val = L.validate_url(u)
        if not ok:
            skipped += 1
            continue
        if val in seen:
            continue
        seen.add(val)
        kind = L.infer_kind(val)
        links.append({"kind": kind, "url": val, "label": L.default_label(kind, val)})
    if skipped:
        notes.append("%d URL(s) rejected (http/unsafe/Gradescope)" % skipped)
    if len(links) > L.MAX_LINKS:
        notes.append("%d links found; only first %d kept" % (len(links), L.MAX_LINKS))
        links = links[:L.MAX_LINKS]

    # summary from a student-written answer, if any
    summary = ""
    for t in answer_texts:
        body = "\n".join(l for l in t.splitlines()
                         if not re.search(r"(?i)https?:|javascript:|data:|www\.|\bfile:", l))
        para = L.clean_text(re.split(r"\n\s*\n", body.strip())[0] if body.strip() else "")
        if len(para) >= 20 and para.count(" ") >= 3:
            summary = para[:L.MAX_SUMMARY].rstrip()
            break

    title_files = [f for f in files if f.lower().endswith((".pdf", ".pptx", ".ppt", ".key") + L.IMAGE_EXTS)]
    title = guess_title(pdf_title, pdf_text, title_files)
    corpus = " ".join([pdf_text[:20000]] + [t[:5000] for t in texts])
    tags = L.suggest_tags(interests, title, summary, corpus, top=3)
    tools = L.detect_tools(interests, title, summary, corpus)[:L.MAX_TOOLS]

    def _copy_in(src, suffix):
        os.makedirs(files_dir, mode=0o700, exist_ok=True)
        os.chmod(files_dir, 0o700)
        dest_name = "%s%s%s" % (re.sub(r"[^A-Za-z0-9_-]", "", sid) or "x", suffix, os.path.splitext(src)[1].lower())
        dest = os.path.join(files_dir, dest_name)
        shutil.copyfile(src, dest)
        os.chmod(dest, 0o600)
        return os.path.relpath(dest, work_dir).replace(os.sep, "/")

    poster_cell = _copy_in(poster, "") if poster else ""
    thumb_cell = _copy_in(thumb_src, "-thumb") if thumb_src else ""

    return {
        "submission_id": sid, "publish": "no", "id": "", "title": title,
        "students": "; ".join(names), "summary": summary, "description": "",
        "links": L.format_links(links), "tags": "; ".join(tags), "tools": "; ".join(tools),
        "keywords": "", "featured": "no", "poster_file": poster_cell,
        "thumbnail_file": thumb_cell,
        "include_poster": "yes" if poster else "no", "notes": "; ".join(notes),
    }


# ------------------------------------------------------------------ CSV merge
def read_review(path):
    """Read an existing review CSV -> (fieldnames, rows) with csv_safe quotes removed."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        fields = list(rd.fieldnames or [])
        rows = [{k: L.csv_unsafe(v) for k, v in r.items() if k is not None} for r in rd]
    return fields, rows


def write_review(path, rows, extra_fields=()):
    fields = list(L.CSV_COLUMNS) + [f for f in extra_fields if f not in L.CSV_COLUMNS]
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".review_", suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
            w.writeheader()
            for r in rows:
                w.writerow({k: L.csv_safe(r.get(k, "")) for k in fields})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def merge_rows(existing, new):
    """Merge new auto rows into existing human-edited rows.

    Existing non-blank cells always win; blank cells are filled from the new row; unseen
    submission_ids are appended; existing rows missing from the new export are kept.
    Returns (merged_rows, added_count, updated_count).
    """
    by_id = {r.get("submission_id", ""): r for r in existing}
    merged = [dict(r) for r in existing]
    index = {r.get("submission_id", ""): r for r in merged}
    taken = {r.get("id", "") for r in merged if r.get("id")}
    added = updated = 0
    for n in new:
        sid = n["submission_id"]
        if sid in index:
            cur = index[sid]
            changed = False
            for k, v in n.items():
                if (cur.get(k) or "").strip() == "" and (v or "").strip() != "":
                    if k == "id":
                        continue  # assigned below
                    cur[k] = v
                    changed = True
            if (cur.get("publish") or "").strip() == "":
                cur["publish"] = "no"
            if not (cur.get("id") or "").strip() and (cur.get("title") or "").strip():
                cur["id"] = L.unique_slug(cur["title"], taken)
                taken.add(cur["id"])
                changed = True
            updated += 1 if changed else 0
        else:
            row = dict(n)
            if row.get("title"):
                row["id"] = L.unique_slug(row["title"], taken)
                taken.add(row["id"])
            merged.append(row)
            index[sid] = row
            added += 1
    del by_id
    return merged, added, updated


# ----------------------------------------------------------------------- main
def import_export(export, out_csv, interests_path=None, log=print):
    """Run the import. Returns a result dict: {rows, added, updated, warnings}."""
    interests = L.load_interests(interests_path or os.path.join(L.DEFAULT_SITE, "data", "interests.json"))
    out_csv = os.path.abspath(out_csv)
    work_dir = os.path.dirname(out_csv)
    files_dir = os.path.join(work_dir, "files")
    warnings = []
    tmp_root = None
    old_umask = os.umask(0o077)
    try:
        if not os.path.isdir(work_dir):
            os.makedirs(work_dir, mode=0o700)
        if os.path.realpath(work_dir) == os.path.realpath(L.DEFAULT_WORK):
            os.chmod(work_dir, 0o700)
        if os.path.isdir(export):
            root = os.path.abspath(export)
        elif zipfile.is_zipfile(export):
            tmp_root = tempfile.mkdtemp(prefix="gs_export_")
            warnings += safe_extract_zip(export, tmp_root)
            root = tmp_root
        else:
            raise UnsafeExport("--export must be a .zip file or an extracted directory")

        meta_path = find_metadata(root)
        meta = {}
        if meta_path:
            meta = L.parse_gradescope_yaml(_read_text(meta_path))
            if not meta:
                warnings.append("submission_metadata.yml found but could not be parsed")
        else:
            warnings.append("no submission_metadata.yml: student names unavailable; fill them in manually")
        subs = collect_submissions(root, meta)
        if not subs:
            warnings.append("no submissions found in export")
        new_rows = []
        for sid in sorted(subs):
            try:
                new_rows.append(analyze_submission(sid, subs[sid], interests, work_dir, files_dir))
            except Exception as e:  # noqa: BLE001 - one bad submission must not abort the batch
                warnings.append("submission %s skipped: %s" % (sid, type(e).__name__))
        if os.path.exists(out_csv):
            fields, existing = read_review(out_csv)
            rows, added, updated = merge_rows(existing, new_rows)
            extra = [f for f in fields if f not in L.CSV_COLUMNS]
        else:
            taken = set()
            rows = []
            for r in new_rows:
                if r.get("title"):
                    r["id"] = L.unique_slug(r["title"], taken)
                    taken.add(r["id"])
                rows.append(r)
            added, updated, extra = len(rows), 0, []
        write_review(out_csv, rows, extra)
        os.chmod(out_csv, 0o600)
    finally:
        os.umask(old_umask)
        if tmp_root:
            shutil.rmtree(tmp_root, ignore_errors=True)
    return {"rows": len(rows), "added": added, "updated": updated, "warnings": warnings, "csv": out_csv}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Stage a Gradescope export into a review CSV.")
    ap.add_argument("--export", required=True, help="Gradescope export .zip or extracted directory")
    ap.add_argument("--out", default=L.DEFAULT_REVIEW, help="review CSV (default tools/work/review.csv)")
    ap.add_argument("--interests", default=None, help="interests.json (default showcase/data/interests.json)")
    args = ap.parse_args(argv)
    try:
        res = import_export(args.export, args.out, args.interests)
    except (UnsafeExport, zipfile.BadZipFile, OSError) as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 2
    print("Review sheet: %s" % res["csv"])
    print("Rows: %d (new %d, filled-in %d existing)" % (res["rows"], res["added"], res["updated"]))
    for w in res["warnings"]:
        print("  warning: %s" % w)
    print("Next: open the CSV, set publish=yes ONLY for students who consented, then run tools/build_showcase.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
