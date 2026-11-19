# CS193T Final Project Showcase

A static page for the course site that presents every student's final project (thumbnail, names, summary, links) with a search bar that understands everyday interests ("work out", "cooking", "study tools", even typos like "cookng") and reshapes the homepage into a **For you** feed.

```
showcase/                  ← the page. Copy this folder onto the course site.
  index.html
  assets/                  showcase.js, search.js, theme-init.js, showcase.css, favicon.svg, fonts/
  data/projects.json       generated (currently 30 SAMPLE projects, flagged "demo": true)
  data/interests.json      topic taxonomy that powers search and auto-tagging (edit freely)
  thumbs/  files/          generated poster thumbnails and PDFs
  .htaccess                security headers for Apache (web.stanford.edu)
tools/                     Gradescope import pipeline, Python stdlib only. Never deploy this folder.
SPEC.md                    the data contract between tools/ and showcase/
```

## 1. Add it to the course site (Hugo)

The course site is Hugo 0.164 with the Beautiful Hugo theme, served as plain files by Apache on web.stanford.edu. There is no server code. [docs/COURSE_SITE.md](docs/COURSE_SITE.md) has the full analysis. The showcase plugs in without touching the theme or the site config:

```bash
python3 tools/integrate_hugo.py --hugo <hugo-site> --dry-run
```

```bash
python3 tools/integrate_hugo.py --hugo <hugo-site> --build
```

The installer:
- **Copies `showcase/`** to `static/final_project/showcase/`. Files of removed projects are deleted, `.htaccess` is kept, and the course URL is rewritten to the site's `baseURL`.
- **Copies the public `projects.json`** to `data/showcase/`.
- **Installs two small files** from [integration/hugo/](integration/hugo/):
  - `content/final_project/showcase.md`, a link-only page that adds **Project Showcase** to the Final Project menu and section list
  - the `showcase-featured` shortcode
- **Checks the result with a real Hugo build** (`--build`).
- **Never overwrites files you changed** without `--force`, and **refuses sample data**.

Then build and deploy the Hugo site as usual. The page lives at `…/cs193t.1272/final_project/showcase/`.

To list a few projects on other course pages, rendered at build time in the course's own style with no JavaScript, add this to any page, for example `content/final_project/_index.md`:

```
{{< showcase-featured count="3" >}}
```

After the first deploy, open the page once. If it shows a server error, the host does not allow `.htaccess` header overrides: delete `static/final_project/showcase/.htaccess`. The `<meta>` CSP in `index.html` still applies.

## 2. Load real projects from Gradescope

Gradescope has no public API, so the pipeline starts from the instructor export. Full details are in [tools/README.md](tools/README.md).

```bash
python3 tools/gradescope_import.py --export ~/Downloads/final_project_submissions.zip
```

Open `tools/work/review.csv`, set `publish` to `yes` **only for students who consented**, fix titles and summaries, then:

```bash
python3 tools/build_showcase.py
```

```bash
python3 tools/serve.py
```

The build validates every field and rejects anything that looks like an email, Stanford ID or Gradescope link. It renders each poster to a thumbnail plus a full-size, metadata-free poster image (`sips` and `cwebp` on macOS), then atomically replaces `showcase/data/projects.json` with `"demo": false`. Submitted PDFs are **not** published by default, because they can carry author metadata, embedded scripts and earlier revisions. `--publish-pdfs` publishes them only after a `qpdf` rewrite and an active-content scan. The sample-data notice then disappears on its own.

### Or use the admin editor

```bash
python3 tools/admin.py
```

This opens a private editor in your browser. Use it to import the Gradescope zip, turn **Publish** on for consenting students, fix titles, summaries, links and posters, edit the page's headline and intro (**Page copy**) and the search topics (**Topics**), then **Build**. It runs only on your computer (127.0.0.1, with a one-time link printed in the terminal), and edits the same `tools/work/review.csv` the commands above use. After building, copy `showcase/` to the Hugo site as in step 1. See [tools/README.md](tools/README.md#admin-editor).

## 3. Security model

- **Zero third-party requests.** No frameworks, analytics, trackers, CDNs or font services. Newsreader and Inter are self-hosted in `assets/fonts/` (SIL Open Font License, see `assets/fonts/LICENSE.txt`), so visitors' IP addresses never go to anyone but Stanford.
- **Strict CSP** (in a `<meta>` tag and `.htaccess`): everything is `'self'` only, including `script-src`, `style-src` and `font-src`, no inline script or style, `object-src 'none'`, `base-uri 'none'`, `form-action 'none'`, and **Trusted Types** set to `'none'`. The browser itself refuses any `innerHTML`-style injection. Every student-supplied value reaches the page through `textContent`.
- **URL allowlist, enforced twice** (build time in Python, run time in the browser): only `https://` links without embedded credentials, or relative `files/` and `thumbs/` paths. `javascript:`, `data:`, `http:`, protocol-relative, whitespace and bidi-control tricks are rejected. Tracking parameters are stripped. External links open with `rel="noopener noreferrer"`, and the destination's domain is shown before you click.
- **Images are self-hosted.** Remote thumbnails are never hot-linked, so a third party can't track visitors or swap an image after review.
- **Private data stays private.** Emails, SIDs, scores and submission IDs never reach `projects.json`. Every published text field is scanned for them, and posters are re-rendered rather than copied. `tools/work/` is git-ignored. Zip extraction blocks path traversal, symlinks and zip bombs. Spreadsheet cells are protected against formula injection.
- **Personalization is local.** Interests and the light/dark choice live only in the visitor's `localStorage`. The page explains this and offers **Forget them**. Nothing is sent anywhere.

## 4. Editing the topics

`showcase/data/interests.json` lists each topic with the words that should map to it (e.g. Fitness: workout, gym, running, yoga…). Add words students actually use, and the search, topic chips and importer auto-tagging all pick them up. Multi-word phrases ("work out", "meal prep") take priority over single words, so "work out" means Fitness, not Work & career.

## 5. Local preview

```bash
python3 tools/serve.py 4321
```

This serves `showcase/` on `127.0.0.1` with the same headers as `.htaccess`, so CSP problems show up locally.

Tests for the pipeline:

```bash
python3 -m unittest discover -s tools/tests
```
