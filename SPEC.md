# CS193T Project Showcase: data contract

The page in `showcase/` is fully static. It reads three JSON files and self-hosted images. Nothing else.

## `showcase/data/projects.json`

```jsonc
{
  "version": 1,
  "course": "CS193T Thinking with AI",
  "term": "Autumn 2026",
  "generated": "2026-12-10T18:00:00Z",   // ISO 8601
  "demo": false,                          // true only for the sample dataset; the page shows a "sample data" notice
  "projects": [
    {
      "id": "meal-planner-ai",            // ^[a-z0-9][a-z0-9-]{0,63}$, unique
      "title": "Pantry Pilot",            // 1..90 chars
      "students": ["Jordan Lee"],         // 1..8 display names, each 1..60 chars. NEVER emails or SUNet IDs
      "summary": "One or two sentences.", // 1..280 chars, plain text
      "description": "Longer text.",      // optional, 0..1500 chars, plain text, \n\n separates paragraphs
      "thumbnail": "thumbs/meal-planner-ai.webp", // optional; RELATIVE path inside showcase/ only, or null
      "thumbnailAlt": "Poster for Pantry Pilot",  // optional
      "links": [                          // 0..6
        { "kind": "website", "url": "https://example.com", "label": "Live site" }
      ],
      "tags": ["food", "productivity"],   // interest ids from data/interests.json, 0..4
      "tools": ["Claude Code", "MCP"],    // names from interests.json "tools" (free text tolerated), 0..8
      "keywords": ["meal prep", "grocery"], // optional extra search terms, 0..12, each <= 40 chars
      "featured": false                    // optional
    }
  ]
}
```

### Link kinds
`website | pdf | image | artifact | dashboard | git | video | doc | other`

Inference when a kind is missing: `github.com`/`gitlab.com` → git; `claude.ai/artifact`, `claude.site` → artifact;
`*.pdf` → pdf; `.png/.jpg/.jpeg/.webp/.gif` → image; `youtube.com`/`youtu.be`/`vimeo.com` → video;
`docs.google.com`, `notion.site` → doc; `lookerstudio`/`tableau`/`streamlit.app`/`observablehq` → dashboard; else website.

### URL rules (enforced at build time AND at runtime)
- Allowed: `https://` absolute URLs, or relative paths matching exactly `^(files|thumbs)/[a-z0-9][a-z0-9-]*\.(pdf|webp)$`.
- Rejected: IP-address hosts (IPv6 literals, dotted/short/hex/octal/decimal IPv4 such as `127.1`, `0x7f.1`), non-443 ports, `http:`, `javascript:`, `data:`, `vbscript:`, `file:`, protocol-relative `//`, anything with whitespace or control chars, anything > 2048 chars. Link labels are at most 60 chars.
- Thumbnails must be relative (self-hosted). Never hotlink remote images: it leaks visitor IPs and lets a third party swap the image.

## `showcase/data/site.json` (optional page copy)

Written by the admin editor (`tools/admin.py`). If it is missing or a field is invalid, the page keeps the text built into `index.html`.

```jsonc
{
  "version": 1,
  "term": "Autumn 2026",                       // <= 40; passed to the build as the term
  "eyebrow": "Final project showcase",         // <= 60
  "headline": "What students built *with* AI.", // <= 90; at most one *pair* becomes <em>
  "lede": "…",                                 // <= 280
  "searchPlaceholder": "Try “cooking” or “gym”", // <= 60
  "trySearches": ["work out", "cooking"],      // 0..8, each <= 40; only ones with results are shown
  "footerNote": "Projects appear with …"       // <= 200; the "Contact course staff" link follows it
}
```
Plain text only. The page inserts it with `textContent`, so markup is shown as literal text. The admin rejects emails, IDs and Gradescope links here too.

### Privacy
- Only projects whose students opted in are published (`publish` = yes in the review sheet).
- Posters are published as a rasterized, metadata-free image `files/<id>-poster.webp` (link kind `image`, label `Poster`), never the submitted PDF. The original PDF is published (`files/<id>.pdf`, label `Poster (PDF)`) only when the build is run with `--publish-pdfs`, qpdf is installed, and the sanitized file contains no active content or metadata.
- Card images (`thumbs/<id>.webp`, max side 1280 px, metadata stripped) come from the review sheet's `thumbnail_file` column. Fallback order: `thumbnail_file`, then (only with `build_showcase.py --poster-thumbnails`) the poster's first page, then no image: the page shows its generated cover. Source images over 12000 px per side are refused. Images are NOT text-scanned, so staff must look at each card image for emails, IDs, grades or other private data before publishing. `thumbnailAlt` is "Preview image for <title>" (or "Poster for <title>" when made from the poster).
- The build rejects any title/summary/description/keyword/link label/URL (including decoded query strings) containing an email-like token, an 8-digit number, a `sunet`/`SID` identifier, or `gradescope.com`.
- Emails, SIDs, SUNet IDs, scores, grades, submission IDs and Gradescope URLs never reach `projects.json`.
