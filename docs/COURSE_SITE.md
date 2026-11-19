# The CS193T course site: how it's built, and how the showcase plugs in

Measured from the live site on 2026-11-19, using the public HTML, sitemap, assets and response headers. Nothing private was used.

## 1. Architecture

| Layer | What the course site uses | Evidence |
|---|---|---|
| Generator | **Hugo 0.164.0**, static output | `<meta name="generator">`, footer credit |
| Theme | **Beautiful Hugo** (from Beautiful Jekyll) on **Bootstrap 3.4.1**, plus course CSS (`main.css`, `custom.css`, `index.css`, `syntax.css`, `codeblock.css`) | `<link>` tags, footer credit |
| Front-end libraries | jQuery 3.7 slim, Bootstrap JS, KaTeX 0.16.7, PhotoSwipe 4.1.2, Font Awesome 6.6, Google Fonts (Lora, Open Sans), and the course's `nav-a11y.js`, `main.js`, `download-code.js` | `<script>` / `<link>` tags; most come from public CDNs |
| Hosting | **Apache on web.stanford.edu**, serving plain files from the class's web space | `Server: Apache` |
| URL | `baseURL = https://web.stanford.edu/class/archive/cs/cs193t/cs193t.1272/`. `/class/cs193t/` serves the same files, the usual alias to the current term | `og:url`; byte-identical home pages at both paths |
| Back end | **None.** No server code, database, login or forms. Anything dynamic happens in the browser. | Every page is static HTML |
| Headers | No CSP, no `X-Content-Type-Options`, and `Strict-Transport-Security: max-age=0` | Response headers |
| `.htaccess` | `/…/.htaccess` returns **403**, so Apache protects `.ht*` files. That usually means `.htaccess` overrides are enabled, but only an upload proves it. | `curl -I` |

Content layout, from `sitemap.xml` (about 50 pages):

```
/                     home (sidebars: teaching team, quick links)
/course/…             syllabus, faq, course_placement, getting_help, honor_code, about_*
/lectures/01…08/
/assignments/1…9/
/final_project/       section list: early_advice, rubric, posters   ← the showcase goes here
/resources/…  /schedule/  /staff/  /tutorials/  /page/about/
```

Menu: `[menu.main]` has parents Course, Resources, Lectures, Assignments, **Final Project**, Tutorials and Schedule. The submenu ids are generated from menu identifiers. The Final Project one is `submenu-final_project`, so its identifier is **`final_project`**. Children link with absolute URLs, e.g. `…/final_project/rubric`.

## 2. Design

- **Type:** Lora (serif) for body text at 18px/1.43. Open Sans 800 for headings, the brand and nav, and 12px uppercase nav with 1px letter-spacing. Section labels are 14px/800 uppercase ("TEACHING TEAM").
- **Light:** white page, `#404040` text, links `#008AFF`, panels `#f8f9fb` with `#e6e8eb` borders at 12px radius, green announcements `#eaf2e9` with a soft shadow, footer `#f5f5f5`.
- **Dark** (`prefers-color-scheme` only, no toggle): page `#0b0d0f`, text `#e6e6e6`, links `#8ab4ff`, panels `#121417`, announcements `#1a2b1d`, nav rule `#aaa`, footer `#444`.
- **Chrome:** fixed white navbar with a 30px red Stanford seal and "CS193T Thinking with AI" in Open Sans 800. Dropdown menus. Centered footer with an email icon, "© 2026 • CS193T Thinking with AI" and the theme credit.

The showcase follows the same palette and type roles (see DESIGN_LOG round 4). Because it has a strict CSP and makes no third-party requests, it uses self-hosted stand-ins: Newsreader for Lora, Inter for Open Sans. Self-hosting the real Lora and Open Sans files (both SIL OFL) would make it exact.

## 3. What this means for the showcase

1. **It must be a static app inside `static/`.** There's no server, so the page, its data and its images are files. Hugo copies `static/final_project/showcase/` byte for byte, `.htaccess` included (verified with a real Hugo build).
2. **Keep it out of Hugo's templates.** The course pages load CDN scripts and inline scripts, which the showcase's CSP would block, and they ship none of its security headers. A standalone page keeps its own CSP and Trusted Types.
3. **Join the menu through front matter, not the site config.** A *link-only* page (`build.render: link`) at `content/final_project/showcase.md` adds "Project Showcase" under Final Project and to the Final Project section list. Hugo writes no HTML for it, so it can't collide with the static app. The site's config file stays untouched.
4. **Show projects on other pages without JavaScript.** The `showcase-featured` shortcode reads `data/showcase/projects.json` at build time and renders a few cards in the course's own style (`assets/teaser.css`). Any page can include `{{< showcase-featured >}}`, for example `content/final_project/_index.md`.
5. **Portable links:**
   - Rubric, Posters and Contact are relative (`../rubric/`, `../posters/`, `../../course/getting_help`), so they work under both `/class/archive/…/cs193t.1272/` and `/class/cs193t/`.
   - Course home, the canonical link and the share tags use the term's absolute URL. The installer rewrites them to whatever `baseURL` the Hugo site has, so next term needs no hand edits.
6. **Security headers.** The rest of the course site sends none. The showcase sends its own through `.htaccess`, scoped to its folder, and the same policy is in a `<meta>` tag in case `.htaccess` is ignored.

## 4. Connecting it (once the projects are final)

```bash
python3 tools/integrate_hugo.py --hugo /path/to/the/course/hugo/site --dry-run   # see what will change
python3 tools/integrate_hugo.py --hugo /path/to/the/course/hugo/site --build     # install and check with a real Hugo build
```

The installer:
- copies `showcase/` to `static/final_project/showcase/`, deleting removed files so takedowns stick
- copies the public `projects.json` to `data/showcase/`
- installs the menu stub and the shortcode without overwriting anything you've changed
- rewrites the course URL to `baseURL`
- refuses to publish sample data

Then deploy the Hugo site the way the course already does. Details: [tools/README.md](../tools/README.md).

Optional: add `{{< showcase-featured count="3" >}}` to `content/final_project/_index.md`, or to the home page, to list projects there too.
