# Design log

## Round 0: brief and references (2026-11-18)
- Target: a subpage for the CS193T Hugo site (`/final_project/showcase/`), static hosting on web.stanford.edu.
- References: Mirendil (editorial serif, whitespace, list rows with arrows, Mode pill), Tessera (dotted-grid hero, dark case-study cards, carousel with "1 of N"), Arcjet (dark theme, mono uppercase eyebrows, italic accent word).
- Result: Newsreader serif headlines, Inter UI, system mono labels. Cardinal `#8C1515` is the only accent, brightened to `#F07A72` on the course site's own dark background `#0B0D0F`. Light by default, dark follows the system or the toggle.

## Round 1: build (model split)
- Haiku: 30 sample projects (placeholder names, example.com links). Also the dev server and headers, at low effort.
- Sonnet: Gradescope pipeline (stdlib only, 19 tests).
- Opus (main): page, search engine, information architecture.
- Search: phrase-first topic detection ("work out" → Fitness before "work" → Career), typo tolerance against raw terms ("cookng", "fitnes"), and tool names protected from topic matching ("Claude Code" is not the Software topic). Results come from weighted-field ranking with IDF and a relative score floor.

## Round 2: independent reviews (Sonnet)
**UX/a11y fixes applied**
- Results changed below the fold. Added a "See N results" jump under the search box; Enter and Try chips scroll to the results.
- Learning was silent and fired on every typing pause. Interests are now learned only on Enter, suggestion picks and chip clicks, with an "Added X to your picks · Undo" toast. "Clear picks" is a real button with Undo.
- The mode toggle's accessible name now contains its visible label. Control borders meet 3:1 (`--control`). Coarse-pointer targets are 40–44px.
- Topic chips use a roving-tabindex toolbar (arrow keys). Suggestions are a grouped listbox with a live count. Stepping through the sheet is announced.
- Titles clamp to 3 lines and names to 2; long queries are truncated in the heading. Removed `content-visibility`, which caused scroll jumps and blank cards.

**Security fixes applied**
- `.htaccess` used `<DirectoryMatch>`, which 500s in .htaccess. Now only legal directives; dot-paths hidden via `RedirectMatch 404`. Empty `index.html` stubs replace `Options -Indexes`.
- Runtime allowlist: relative paths limited to `(files|thumbs)/<slug>.(pdf|webp)`; numeric, hex and IPv6 hosts rejected; URL updates safe on `//` paths. A 26-case probe passes.
- Takedowns: data, thumbs and posters are served `no-cache`.
- Pipeline (delegated back to Sonnet): HTML-stripping ReDoS, YAML and URI-scan ReDoS, posters published as rasterized WebP by default (sanitized PDFs only with qpdf and `--publish-pdfs`), a PII scan on every published text field, IP-host rejection, aligned path rules, owner-only work files.

## Round 3: self-hosted fonts (user approved)
- Downloaded the Inter and Newsreader variable WOFF2 files (Latin + Latin Extended, about 590 KB in total; about 330 KB per visit because unicode-range loads Latin Extended only on demand) and their OFL licenses into `assets/fonts/`.
- Removed the Google hosts from the CSP in `index.html`, `.htaccess` and `serve.py`. All three are now `'self'` only. The two main faces are preloaded, and fonts are cached for a year.
- Verified in the browser: three local font files loaded, 0 third-party requests, no CSP violations.

## Round 4: match the course site (2026-11-19)
- Goal: keep every feature, but make the page read as part of the CS193T site (Beautiful Hugo on Bootstrap 3). Colors and type were measured from the live course site in both themes.
- Type roles swapped to match: body copy (lede, summaries, sheet text, footer) uses the serif, and headings, nav, labels and buttons use a heavy sans. Nav and labels are 12px weight-800 uppercase with letter-spacing, like "TEACHING TEAM". Fonts stay self-hosted: Newsreader stands in for Lora and Inter (now declared 400–800) for Open Sans. Getting the exact Lora and Open Sans files would mean downloading them, which needs the user's OK.
- Palette:
  - Light: white page, #404040 text, #f8f9fb panels, #eaf2e9 announcement green, #f5f5f5 footer.
  - Dark: #0b0d0f page, #121417 panels, #1a2b1d green, #444 footer, #aaa nav rule, #b0b0b0 nav text.
  - Links are course blue. In light mode #008aff only reaches 3.45:1 on white, so links use #0066cc (5.6:1). Dark mode keeps #8ab4ff. Cardinal remains only as a brand accent (hero "with", eyebrow dot, sample-data tag).
- Components:
  - The topbar is a solid white (or #0b0d0f) bar with a rule. Cards and the featured carousel are 12px-radius panels with the course's soft shadow. Featured cards are light panels instead of black slabs.
  - "Picked for you" sits in a green announcement panel. The footer is the gray course footer. The hero's dotted grid and glow are gone, matching the course's plain header.
  - Active topic chips, the "See results" button and the "View project" button are course blue.
- Fixed in passing: the hero's `overflow: hidden` clipped the suggestion list, and the sticky toolbar covered it. The hero no longer clips, and it rises above the toolbar only while suggestions are open.
- Verified: an automated contrast audit of 323 text elements found 0 failures in light and dark (lowest dark ratio 7.0:1). Control borders are ≥3:1. No horizontal overflow at 375px, and no console or CSP errors. Search, suggestions, chips, carousel, sheet (deep link and Esc) and the Mode toggle all still work.

## Round 5: admin editor (2026-11-19)
- The user chose a **local** editor over a hosted admin. The course site is static Apache, so a public write endpoint would be the largest attack surface. `python3 tools/admin.py` serves `tools/admin/` on 127.0.0.1 and edits the same files as the CLI pipeline:
  - `tools/work/review.csv`: projects and consent
  - `showcase/data/site.json`: page copy (new, optional; the page keeps its built-in text if it is missing)
  - `showcase/data/interests.json`: topics
  It also runs import and build.
- Model split: Sonnet built the server and tests, Opus built the UI and the showcase's `site.json` support, and two Sonnet reviewers (security, UX) ran before any fixes.
- Security:
  - Each run gets a new token, passed in the URL fragment and the `X-Admin-Token` header.
  - Host, Origin and Sec-Fetch-Site checks; no CORS; strict content types; size caps.
  - Atomic writes, owner-only files, 30 rolling backups, and an optimistic `rev` on all three files (409 on outside edits).
  - Uploads are identified by magic bytes, and the import zip is deleted after the import.
  - Review fixes:
    - The browser is opened through a 0600 launcher file, so the token never appears in `ps`.
    - Poster previews no longer hold the global lock, and connections are capped at 32.
    - The CSV leading-apostrophe round trip is fixed.
    - Uploads that no row uses any more are cleaned up.
- UX review fixes:
  - Edits survive a 409, and a choice in a dialog is captured at click time.
  - A save in progress keeps anything typed meanwhile, and focus is restored after re-renders.
  - The Publish control is a labelled `role=switch`.
  - Pills say "Will publish" or "Not published", because nothing is live until it is built and copied.
  - Errors stay on screen and use field names, not keys.
  - Removed topics can't jam the 4-topic limit.
  - Deploying uses `rsync --delete`, so withdrawn students' images come down.
  - Focus outline is 2px; touch targets are larger on touch screens; dark-mode text contrast is fixed.
- Verified in the browser on a scratch copy:
  - seeding from the live projects, editing and saving with Ctrl+S
  - an invalid link flagged and then fixed
  - page-copy and topic validation
  - a conflict reload that keeps edits
  - a build that rewrote the scratch `projects.json`, and the showcase picking up the new headline
  - 375px and dark mode
- 68 tests pass.

## Round 6: shorter hero (2026-11-19)
- The user asked for less above the fold. At 1440×800 the hero shrank from 584px to 364px, and the first featured card now starts at 533px instead of 765px. On a 375px phone the first card's title shows on the first screen.
- Changes:
  - Tighter padding.
  - Headline at 28–42px (was 34–58px).
  - Intro at 16–18px, with balanced line breaks.
  - A one-line sample-data note.
  - A 52px search box.
  - Smaller "Try" chips; on phones they sit in one row you can swipe.
  - Stats are now one quiet line ("30 projects · 25 students · 16 topics") instead of large numbers.
  - Less space above the Featured and For-you sections.

## Round 7: cover initials always fit (2026-11-19)
- Bug: the two-letter initials on generated covers were sized from the window width (`vw`), so on narrow cards and the wide featured panel, wide pairs (QN, SW, CE, RT) ran past the right edge and Q's tail past the bottom.
- Fix: each `.cover` is a size container. The initials use `min(48cqh, 32cqi)` and sit at 7% / 8% offsets, with a line height that includes descenders and a little padding for the italic overhang. All per-variant font sizes were removed. The initials also take whole characters, so an emoji is never split.
- Verified by measuring painted glyph extents with canvas `measureText` for the worst pairs (WM, MW, WW, QW, QJ) on card, featured and sheet covers at 375px, 682px and 1440px. Every glyph stays inside its cover; the closest is 13px from an edge.

## Round 8: ready to connect to the course site (2026-11-19)
- Deep dive of the live site, in [docs/COURSE_SITE.md](docs/COURSE_SITE.md):
  - Hugo 0.164 with Beautiful Hugo on Bootstrap 3.
  - Apache static hosting with no back end and no security headers. `.htaccess` is protected (403), so overrides are likely honored.
  - The `/class/cs193t/` alias serves the same files.
  - The Final Project menu identifier is `final_project`.
  - Every page is listed in `sitemap.xml`.
- Showcase:
  - The nav now matches the course: Course home, Final Project, Rubric, Posters.
  - Added a canonical link plus `og:url`, `og:site_name` and `og:image` (the course seal URL; nothing is loaded).
  - The footer has the course credit line. Rubric, Posters and Contact stay relative, so both URL forms work.
- Integration kit in `integration/hugo/`:
  - A link-only content page (`build.render: link`) adds "Project Showcase" to the Final Project menu and section list without editing the site config or colliding with the static app.
  - The `showcase-featured` shortcode renders featured projects at build time from `data/showcase/projects.json`, styled by `showcase/assets/teaser.css` in the course's colors and fonts, with no JavaScript. It refuses sample data unless `show-demo="true"` and warns in the Hugo log.
- `tools/integrate_hugo.py` (Sonnet, 29 tests):
  - Mirrors `showcase/` into `static/final_project/showcase/`, deleting stale files and keeping `.htaccess`.
  - Rewrites the course URL to `baseURL` and copies the data.
  - Installs the kit without overwriting edits.
  - Refuses demo data and symlink escapes.
  - Checks the output with a real Hugo build (`--build`).
  - Fixed in review: staging now happens in the Hugo project root, not inside `static/`, so an interrupted run can't publish leftovers.
- Verified with Hugo 0.170.1, installed with the user's OK, on a mock course site using the course's real CSS:
  - The menu entry appears last under Final Project.
  - The section list includes it.
  - The teaser renders and links to `#p=<id>`, which opens the right project.
  - All relative links resolve to real course pages, and there are no console errors.
  - A rerun reports everything unchanged; a stale file is removed.
- 97 tests pass.

## Round 9: student-chosen card images (2026-11-19)
- Students can submit a card image (screenshot) in Gradescope. It shows on their card, the featured carousel, the project view and the course-site teaser. Without one, the card keeps the generated initials cover.
  - The poster is no longer used as the card image (`--poster-thumbnails` restores that).
  - The importer looks for a file named thumbnail, cover, card, preview or screenshot. Failing that, it takes a single image next to the poster PDF, or the largest image when there's no PDF.
  - The image is stored in the new `thumbnail_file` column, published at up to 1280px as metadata-free WebP, and checked by a content-keyed cache. There's a 12000px-per-side cap.
- Admin:
  - A **Card image** panel: a 3:2 preview cropped like the card, Upload/Replace and Remove. PDFs are refused.
  - The list shows the card image.
  - "Copy in the live projects" now carries existing card images, so a build keeps them.
- Examples: the user's Meridian and Cardinal Care Atlas join the sample data, both featured and credited to Yosief Abraham, using their own screenshots. The Atlas screenshot was recaptured from the live site at 2000px with headless Chrome, because the pasted image wasn't saved to disk.
- Found and fixed a long-standing bug: the build wrote `projects.json`, thumbnails and poster images as 0600 (via mkstemp), which a normal web server would refuse to serve. They are now 0644, with a test. The Hugo installer's 0644 copy had been hiding this.
- Verified:
  - 109 tests pass.
  - End to end with a synthetic export: the card image is imported and built; swapping it regenerates the thumbnail, and an unchanged image is cached.
  - The admin round trip on a scratch copy.
  - Browser checks at 1440px and 375px.
  - The Hugo teaser shows both screenshots.
