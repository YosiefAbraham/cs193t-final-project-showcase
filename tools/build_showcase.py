#!/usr/bin/env python3
"""Build showcase/data/projects.json (+ thumbs/files) from the reviewed CSV.

    python3 tools/build_showcase.py [--review tools/work/review.csv] [--site showcase] [--skip-invalid]

Python 3.9+, standard library only. Only rows with publish=yes are considered.
"""
import argparse
import csv
import datetime
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import showcase_lib as L  # noqa: E402

COURSE = "CS193T Thinking with AI"
MAX_POSTER_BYTES = 100 * 1024 ** 2
THUMB_MAX_SIDE = 1280
THUMBCACHE_NAME = ".thumbcache.json"
PUBLIC_MODE = 0o644  # everything written into showcase/ is public by design
GENERATED_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.(webp|pdf)$")
YES = ("yes", "y", "true", "1")


def _yes(v):
    return (v or "").strip().lower() in YES


def _split(cell, sep=";"):
    return [x.strip() for x in re.split(r"[;\n]", cell or "") if x.strip()]


def read_rows(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return [{(k or "").strip(): L.csv_unsafe(v or "") for k, v in r.items() if k is not None} for r in rd]


def resolve_poster(cell, review_dir, what="poster_file"):
    """Resolve poster_file (or thumbnail_file) inside the review directory only. Returns (path_or_None, error)."""
    cell = (cell or "").strip()
    if not cell:
        return None, None
    base = os.path.realpath(review_dir)
    p = cell if os.path.isabs(cell) else os.path.join(review_dir, cell)
    real = os.path.realpath(p)
    if real != base and not real.startswith(base + os.sep):
        return None, "%s is outside the review directory" % what
    if os.path.islink(p) or not os.path.isfile(real):
        return None, "%s not found" % what
    if os.path.getsize(real) > MAX_POSTER_BYTES:
        return None, "%s larger than %d MB" % (what, MAX_POSTER_BYTES // 1024 ** 2)
    return real, None


def _load_thumbcache(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_thumbcache(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".thumbcache_", suffix=".tmp")
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except OSError:
        pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def validate_row(row, interests, review_dir):
    """Validate one publish=yes row. Returns (project_dict_or_None, errors, warnings, extras)."""
    errs, warns = [], []
    pid = (row.get("id") or "").strip()
    title = L.clean_text(row.get("title"))
    if not title:
        errs.append("title is empty")
    elif len(title) > L.MAX_TITLE:
        errs.append("title longer than %d chars" % L.MAX_TITLE)

    students = []
    for n in _split(row.get("students")):
        n = L.clean_text(n)
        if not n:
            continue
        if "@" in n or re.search(r"\d{5,}", n) or "://" in n:
            errs.append("student name looks like an email/ID: %r" % n[:30])
        if len(n) > L.MAX_STUDENT:
            errs.append("student name longer than %d chars" % L.MAX_STUDENT)
        if n not in students:
            students.append(n)
    if not students:
        errs.append("students is empty")
    elif len(students) > L.MAX_STUDENTS:
        errs.append("more than %d students" % L.MAX_STUDENTS)

    summary = L.clean_text(row.get("summary"))
    if not summary:
        errs.append("summary is empty")
    elif len(summary) > L.MAX_SUMMARY:
        errs.append("summary longer than %d chars (%d)" % (L.MAX_SUMMARY, len(summary)))
    description = L.clean_text(row.get("description"), keep_newlines=True)
    if len(description) > L.MAX_DESCRIPTION:
        errs.append("description longer than %d chars" % L.MAX_DESCRIPTION)

    links, link_errs = L.parse_links(row.get("links"))
    for e in link_errs:
        errs.append("link: " + e)
    # drop duplicate URLs
    seen, uniq = set(), []
    for l in links:
        if l["url"] in seen:
            warns.append("duplicate link removed")
            continue
        seen.add(l["url"])
        uniq.append(l)
    links = uniq

    known = {i["id"] for i in interests.get("interests", [])}
    tags = []
    for t in _split(row.get("tags")):
        t = t.lower()
        if t not in known:
            errs.append("unknown tag %r" % t[:30])
        elif t not in tags:
            tags.append(t)
    if len(tags) > L.MAX_TAGS:
        errs.append("more than %d tags" % L.MAX_TAGS)

    tools = []
    canon = {t.lower(): t for t in interests.get("tools", [])}
    for t in _split(row.get("tools")):
        t = L.clean_text(t)
        t = canon.get(t.lower(), t)
        if len(t) > L.MAX_KEYWORD_LEN:
            errs.append("tool name longer than %d chars" % L.MAX_KEYWORD_LEN)
        elif t and t not in tools:
            tools.append(t)
    if len(tools) > L.MAX_TOOLS:
        errs.append("more than %d tools" % L.MAX_TOOLS)

    keywords = []
    for k in _split(row.get("keywords")):
        k = L.clean_text(k)
        if len(k) > L.MAX_KEYWORD_LEN:
            errs.append("keyword longer than %d chars" % L.MAX_KEYWORD_LEN)
        elif k and k.lower() not in [x.lower() for x in keywords]:
            keywords.append(k)
    if len(keywords) > L.MAX_KEYWORDS:
        errs.append("more than %d keywords" % L.MAX_KEYWORDS)

    texts = [("title", title), ("summary", summary), ("description", description)]
    texts += [("keyword", k) for k in keywords] + [("tool", t) for t in tools]
    for l in links:
        texts += [("link label", l["label"]), ("link url", l["url"])]
    for what, t in texts:
        why = L.pii_problem(t)
        if why:
            errs.append("%s %s" % (what, why))
    poster, perr = resolve_poster(row.get("poster_file"), review_dir)
    if perr:
        warns.append(perr + "; no thumbnail")
    thumb, terr = resolve_poster(row.get("thumbnail_file"), review_dir, "thumbnail_file")
    if not terr and thumb and L.is_pdf(thumb):
        thumb, terr = None, "thumbnail_file is a PDF; it must be an image"
    if terr:
        warns.append(terr + "; no card image")
    include_poster = _yes(row.get("include_poster"))
    if include_poster:
        if not poster:
            errs.append("include_poster=yes but poster_file is unusable")
    project = {
        "id": pid, "title": title, "students": students, "summary": summary,
        "links": links, "tags": tags, "tools": tools,
    }
    if description:
        project["description"] = description
    if keywords:
        project["keywords"] = keywords
    if _yes(row.get("featured")):
        project["featured"] = True
    return project, errs, warns, {"poster": poster, "thumbnail": thumb, "include_poster": include_poster}


def build(review, site, term="Autumn 2026", skip_invalid=False, allow_empty=False, interests_path=None, log=print,
          publish_pdfs=False, poster_thumbnails=False):
    """Returns (exit_code, summary dict)."""
    interests = L.load_interests(interests_path or os.path.join(site, "data", "interests.json"))
    rows = read_rows(review)
    review_dir = os.path.dirname(os.path.abspath(review))
    skipped = []   # (label, reason)
    candidates = []
    taken = set()
    # pre-claim explicit ids so derived slugs never collide with them
    for r in rows:
        if _yes(r.get("publish")) and (r.get("id") or "").strip():
            taken.add(r["id"].strip())
    not_published = 0
    seen_ids = set()
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        if not _yes(row.get("publish")):
            not_published += 1
            continue
        label = "row %d" % n
        pid = (row.get("id") or "").strip()
        derived = False
        if not pid:
            base = L.slugify(row.get("title") or "")
            if base:
                pid = L.unique_slug(base, taken)
                taken.add(pid)
                derived = True
            row["id"] = pid
        label = "row %d (%s)" % (n, pid or "no id")
        errs = []
        if not L.SLUG_RE.match(pid):
            errs.append("id %r is not a valid slug (^[a-z0-9][a-z0-9-]{0,63}$)" % pid[:70])
        elif pid in seen_ids:
            errs.append("duplicate id %r" % pid)
        project, verrs, warns, extras = validate_row(row, interests, review_dir)
        errs += verrs
        if errs:
            skipped.append((label, "; ".join(errs)))
            continue
        seen_ids.add(pid)
        if derived:
            warns.append("id derived from title")
        candidates.append((label, project, extras, warns))

    if skipped and not skip_invalid:
        log("Validation FAILED (nothing written). Fix these rows or use --skip-invalid:")
        for lab, why in skipped:
            log("  %s: %s" % (lab, why))
        return 1, {"published": 0, "skipped": skipped, "not_published": not_published}
    if not candidates and not allow_empty:
        log("No publishable rows (publish=yes). Nothing written; use --allow-empty to publish an empty list.")
        return (0 if not skipped else 1), {"published": 0, "skipped": skipped, "not_published": not_published}

    thumbs_dir = os.path.join(site, "thumbs")
    files_dir = os.path.join(site, "files")
    os.makedirs(thumbs_dir, exist_ok=True)
    os.makedirs(files_dir, exist_ok=True)
    projects, keep_thumbs, keep_files, all_warnings = [], set(), set(), []
    cache_path = os.path.join(review_dir, THUMBCACHE_NAME)
    cache, new_cache = _load_thumbcache(cache_path), {}
    for label, p, extras, warns in candidates:
        pid = p["id"]
        poster = extras["poster"]
        p["thumbnail"] = None
        thumb_src, thumb_kind = extras.get("thumbnail"), "thumbnail"
        if not thumb_src and poster_thumbnails and poster:
            thumb_src, thumb_kind = poster, "poster"
        if thumb_src:
            dest = os.path.join(thumbs_dir, pid + ".webp")
            st = os.stat(thumb_src)
            entry = {"src": os.path.realpath(thumb_src), "size": st.st_size, "mtime": st.st_mtime_ns,
                     "max_side": THUMB_MAX_SIDE, "kind": thumb_kind}
            if os.path.isfile(dest) and not os.path.islink(dest) and cache.get(pid) == entry:
                ok, msg = True, "cached"
            else:
                ok, msg = L.make_thumbnail(thumb_src, dest, max_side=THUMB_MAX_SIDE)
            if ok:
                new_cache[pid] = entry
                p["thumbnail"] = "thumbs/%s.webp" % pid
                p["thumbnailAlt"] = ("Preview image for %s" if thumb_kind == "thumbnail" else "Poster for %s") % p["title"]
                keep_thumbs.add(pid + ".webp")
            else:
                warns.append("no thumbnail: " + msg)
        extra_links = []
        if extras["include_poster"] and poster:
            # Publish a rasterized, metadata-free image, never the submitted PDF.
            name = pid + "-poster.webp"
            dest = os.path.join(files_dir, name)
            if os.path.isfile(dest) and not os.path.islink(dest) and os.path.getmtime(dest) >= os.path.getmtime(poster):
                ok, msg = True, "cached"
            else:
                ok, msg = L.make_thumbnail(poster, dest, max_side=2400)
            if ok:
                keep_files.add(name)
                extra_links.append({"kind": "image", "url": "files/" + name, "label": "Poster"})
            else:
                warns.append("poster image not published: " + msg)
            if publish_pdfs:
                if L.is_pdf(poster):
                    ok, msg = L.sanitize_pdf(poster, os.path.join(files_dir, pid + ".pdf"))
                    if ok:
                        keep_files.add(pid + ".pdf")
                        extra_links.append({"kind": "pdf", "url": "files/%s.pdf" % pid, "label": "Poster (PDF)"})
                    else:
                        warns.append("PDF not published: " + msg)
                else:
                    warns.append("PDF not published: poster_file is not a PDF")
        p["links"] = extra_links + p["links"]
        if len(p["links"]) > L.MAX_LINKS:
            warns.append("more than %d links; extra links dropped" % L.MAX_LINKS)
            p["links"] = p["links"][:L.MAX_LINKS]
        order = ["id", "title", "students", "summary", "description", "thumbnail", "thumbnailAlt",
                 "links", "tags", "tools", "keywords", "featured"]
        projects.append({k: p[k] for k in order if k in p})
        for w in warns:
            all_warnings.append((label, w))

    projects.sort(key=lambda p: (p["title"].lower(), p["id"]))
    doc = {
        "version": 1, "course": COURSE, "term": term,
        "generated": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "demo": False, "projects": projects,
    }
    data_dir = os.path.join(site, "data")
    os.makedirs(data_dir, exist_ok=True)
    out = os.path.join(data_dir, "projects.json")
    fd, tmp = tempfile.mkstemp(dir=data_dir, prefix=".projects_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, PUBLIC_MODE)  # mkstemp creates 0600; the web server must be able to read it
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

    _save_thumbcache(cache_path, new_cache)
    removed = []
    for d, keep in ((thumbs_dir, keep_thumbs), (files_dir, keep_files)):
        for fn in sorted(os.listdir(d)):
            full = os.path.join(d, fn)
            if GENERATED_RE.match(fn) and fn not in keep and os.path.isfile(full) and not os.path.islink(full):
                os.unlink(full)
                removed.append(os.path.relpath(full, site))

    log("Published %d project(s), skipped %d (+ %d not marked publish=yes)." % (len(projects), len(skipped), not_published))
    for lab, why in skipped:
        log("  skipped %s: %s" % (lab, why))
    for lab, w in all_warnings:
        log("  warning %s: %s" % (lab, w))
    for r in removed:
        log("  removed stale %s" % r)
    log("Wrote %s" % out)
    return 0, {"published": len(projects), "skipped": skipped, "not_published": not_published,
               "warnings": all_warnings, "removed": removed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build showcase/data/projects.json from the review CSV.")
    ap.add_argument("--review", default=L.DEFAULT_REVIEW)
    ap.add_argument("--site", default=L.DEFAULT_SITE)
    ap.add_argument("--term", default="Autumn 2026")
    ap.add_argument("--skip-invalid", action="store_true", help="publish valid rows, skip invalid ones")
    ap.add_argument("--allow-empty", action="store_true", help="write projects.json even if no rows are publish=yes")
    ap.add_argument("--publish-pdfs", action="store_true",
                    help="also publish sanitized original PDFs (requires qpdf); default is a rasterized poster image only")
    ap.add_argument("--poster-thumbnails", action="store_true",
                    help="use the poster's first page as the card image when a row has no thumbnail_file")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.review):
        print("ERROR: review CSV not found: %s (run gradescope_import.py first)" % args.review, file=sys.stderr)
        return 2
    code, _ = build(args.review, args.site, args.term, args.skip_invalid, args.allow_empty,
                    publish_pdfs=args.publish_pdfs, poster_thumbnails=args.poster_thumbnails)
    return code


if __name__ == "__main__":
    sys.exit(main())
