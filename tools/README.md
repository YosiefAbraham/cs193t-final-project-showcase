# Showcase data pipeline

Turns a Gradescope submissions export into `showcase/data/projects.json` plus self-hosted thumbnails and poster PDFs.
Python 3.9+, standard library only. No network access. Optional: PyMuPDF/Pillow (used if installed); on macOS `sips`
and `cwebp` (`brew install webp`) are used for thumbnails.

## Workflow

1. **Export from Gradescope.** There is no API. Open the final-project assignment, go to **Review Grades**, and use the
   export button for submissions (labelled "Export Submissions" / "Download Submissions"; the exact label may vary).
   Gradescope emails or offers a `.zip`. Save it somewhere private (not in this repo).
2. **Import to a review sheet.**
   `python3 tools/gradescope_import.py --export ~/Downloads/final_project_export.zip`
   writes `tools/work/review.csv` (and poster copies in `tools/work/files/`). Re-running merges: your edits are kept,
   only blank cells and new submissions are filled.
3. **Review in a spreadsheet** (Excel/Numbers/Sheets; file is UTF-8 with BOM). For every row:
   - Set `publish` to `yes` **only if the students consented** to being featured publicly. Default is `no`.
   - Check `title`, `students` (display names only), `summary` (max 280 chars), `description` (optional, max 1500).
   - `links`: one per line, `kind | url | label`. Kinds: website, pdf, image, artifact, dashboard, git, video, doc, other.
     Only `https://` URLs. Tracking parameters are stripped automatically.
   - `tags`: up to 4 interest ids from `showcase/data/interests.json`, separated by `;`. `tools`: up to 8.
   - `include_poster`: `yes` (default when a poster exists) publishes a rasterized ~2400px image `files/<id>-poster.webp`
     with a "Poster" link. The submitted PDF itself is NOT published (PDFs can carry author metadata, JavaScript,
     attachments and old revisions). Set `no` for thumbnail/links only. Check `notes` for warnings. PPTX posters must be exported to PDF first and
     `poster_file` pointed at it (path relative to `tools/work/`).
   - `thumbnail_file`: the student's card image (path relative to `tools/work/`; blank = none). The importer picks an
     image named like `thumbnail`, `thumb`, `cover`, `card`, `preview` or `screenshot`; else, next to a PDF poster, the
     only image (noted "image used as card image; check"); else, with no PDF, the largest image. `poster_file` is PDF only.
     Without a card image the page shows its generated cover; the poster is not used unless you build with
     `--poster-thumbnails`. Images are not text-scanned: look at each one for emails, IDs or other private data.
     Review sheets from before this column existed still load and build.
   - Cells starting with `=`, `+`, `-`, `@` are saved with a leading `'` (formula-injection protection); the build
     tool removes it.
4. **Build.** `python3 tools/build_showcase.py` (add `--skip-invalid` to publish valid rows and skip broken ones,
   `--term "Autumn 2026"` to set the term). It validates everything against `SPEC.md`, generates
   `showcase/thumbs/<id>.webp`, copies PDFs, removes stale generated thumbs/PDFs, and writes `projects.json` atomically.
   Optional `--publish-pdfs` also publishes a sanitized PDF, only if `qpdf` is installed: it is rewritten, /Info and
   /Metadata are dropped, and the file is refused if it contains JavaScript, launch actions, attachments, etc.
   The build also rejects rows whose text/links contain emails, 8-digit numbers (student IDs), `sunet`/`SID` ids or
   gradescope.com. Card images are written at up to 1280px; a manifest `tools/work/.thumbcache.json` makes rebuilds
   regenerate whenever the source file or its kind changes. `--poster-thumbnails` falls back to the poster's first page
   for rows without a `thumbnail_file`. It refuses to overwrite `projects.json` when no row is `publish=yes` (use `--allow-empty` to force).
5. **Preview.** `python3 tools/serve.py` (or any static server on `showcase/`) and look at it.
6. **Deploy.** Upload the contents of `showcase/` (not `tools/`) to the course site.

Tests: `python3 -m unittest discover -s tools/tests`

## Recommended Gradescope questions for next term

Use an online-assignment question set alongside the poster upload so data maps cleanly:

1. Project title (short text)
2. 1-2 sentence summary (text, 280 chars max)
3. Links, one per line: live site, GitHub, Claude artifact, dashboard, video (text)
4. Names to display publicly (text; may differ from legal names)
5. Checkbox: "OK to feature this project on the public course site" (the importer does not read this; TAs must
   copy the consent into `publish` by hand)
6. Poster upload (PDF preferred; 36x24)
7. Card image (optional): a landscape screenshot or image of your project, at least 1200×800 px, PNG or JPG. Name it thumbnail.png.

## Privacy

- `tools/work/` holds names, submission IDs and student files. It is git-ignored (`*`); never commit, share, or deploy it.
- Treat the showcase like any student-work publication under FERPA: publish only with explicit consent, and honor
  removal requests by setting `publish=no` and rebuilding (stale thumbs/PDFs are deleted automatically).
- `projects.json` never receives emails, SIDs, scores or submission IDs; the build rejects names that look like
  emails/IDs, non-https URLs, credentials in URLs and Gradescope URLs.
- `tools/work/` and its files are created owner-only (0700/0600). Summaries are taken only from metadata keys that look like
  student answers (never comment/grader/feedback/rubric/score/evaluation keys); the notes column says when that happened.
- Zip extraction blocks path traversal and symlinks and enforces size/count caps (2 GB / 20000 files / 300 MB each).
  Submitted HTML is only scanned as text, never rendered. URLs are never fetched.

## Admin editor

A local browser UI for everything above (editing the review sheet, page copy, topics, and running the build).
Standard library only.

    python3 tools/admin.py [--port 4322] [--no-browser]

It prints `Admin: http://127.0.0.1:4322/#t=<token>` and opens it in your browser. The token is generated fresh on every
run and lives in the URL fragment. It is printed in your terminal and kept out of server logs and process arguments (the browser
is opened through a short-lived, owner-only launcher file that is deleted after about 20 seconds); anyone who can read
your terminal or run code as you can use it. The page sends it as the `X-Admin-Token` header on every
API call. Use the printed link; a bare `http://127.0.0.1:4322/` will not be able to talk to the server.

**Local only. Never deploy it.** It binds to 127.0.0.1, rejects foreign `Host`/`Origin` headers and cross-site requests,
and edits student data in `tools/work/`. Do not put `tools/` on the course site, a shared server, or a tunnel.

Tabs:

- **Projects & consent**: edits `tools/work/review.csv` (the same file the CLI uses): `publish` consent, title, students,
  summary, links, tags, tools, poster and card-image upload (PNG/JPEG/WEBP/GIF only for card images). Each save is atomic and the previous file is copied to
  `tools/work/backups/` (newest 30 kept; restorable). If you edit the CSV in a spreadsheet at the same time, the editor
  detects it and asks you to reload instead of overwriting. You can also import a Gradescope zip, or "start from what is
  live" when no review sheet exists yet.
- **Page copy**: eyebrow, headline (one `*emphasis*` pair allowed), lede, search placeholder, suggested searches, footer
  note and term, saved to `showcase/data/site.json`.
- **Topics**: the interest topics and tool names in `showcase/data/interests.json`. Removing a topic warns about
  projects still tagged with it.
- **Publish**: runs the same build as `build_showcase.py` (optionally skipping invalid rows or publishing sanitized PDFs)
  and writes `showcase/data/projects.json`, thumbnails and posters.

After Publish you still copy the contents of `showcase/` to the Hugo site exactly as before (step 6 above). The editor
never uploads anything anywhere. Tests: `python3 -m unittest discover -s tools/tests` (includes `test_admin.py`).

## Install into the course's Hugo site

`tools/integrate_hugo.py` drops the finished showcase into the course's Hugo site (standard library only):

    python3 tools/integrate_hugo.py --hugo PATH [--section final_project] [--slug showcase] [--base-url URL]
        [--dry-run] [--force] [--no-menu] [--allow-demo] [--build]

Run it with `--dry-run` first. It then does, in order:

1. **Validates the Hugo site** (`hugo|config.toml|yaml|yml|json` or `config/_default/`) and reads `baseURL` from it
   (`--base-url` overrides). The URL must be `https://` and is normalized to one trailing slash.
2. **Refuses sample data**: if `showcase/data/projects.json` has `"demo": true` it stops (override with `--allow-demo`,
   for private previews only).
3. **Mirrors `showcase/`** to `<hugo>/static/<section>/<slug>/` (default `final_project/showcase`, served at
   `<baseURL>final_project/showcase/`). Files no longer in `showcase/` are deleted from that folder only, so takedowns
   propagate. `.htaccess` is kept; `.DS_Store` and editor temp files are skipped; `tools/` is never copied. It refuses
   symlinked targets/parents and targets that resolve outside `static/`. In the copied `index.html` only, the absolute
   course URL `https://web.stanford.edu/class/archive/cs/cs193t/cs193t.1272/` is replaced by the site's `baseURL`.
   The new copy is built beside the target and swapped in.
4. **Copies `projects.json`** to `<hugo>/data/showcase/projects.json` (`site.Data.showcase.projects`).
5. **Installs the kit** from `integration/hugo/`: `layouts/shortcodes/showcase-featured.html` and, unless `--no-menu`,
   `content/<section>/<slug>.md` (its `url:`, `parent:` and `identifier:` lines are rewritten for a custom
   `--section`/`--slug`). Identical files report "unchanged"; differing files are skipped (compare with `diff`) unless
   `--force`; a content file without the `# installed by cs193t-showcase` marker is never overwritten without `--force`.
   It warns if `content/<section>/<slug>/` exists as a folder (it would collide with the static page).
6. **`--build`**: if `hugo` is on PATH, builds into a temp dir (300 s limit) and checks that
   `<section>/<slug>/index.html` is byte-identical to the installed copy and that `.htaccess` and `data/projects.json`
   are present. Without hugo it says so and skips.

Exit codes: 0 ok, 1 refused or validation error, 2 usage. Afterwards review with `hugo server`, then deploy the Hugo
site as usual. Tests: `python3 -W ignore -m unittest discover -s tools/tests` (includes `test_integrate.py`).
