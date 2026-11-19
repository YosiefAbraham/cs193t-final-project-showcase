#!/usr/bin/env python3
"""Install the finished showcase into the course's Hugo site (Python 3.9+, standard library only).

    python3 tools/integrate_hugo.py --hugo PATH [--section final_project] [--slug showcase]
        [--base-url URL] [--dry-run] [--force] [--no-menu] [--allow-demo] [--build]

Mirrors showcase/ to <hugo>/static/<section>/<slug>/, copies projects.json to
<hugo>/data/showcase/projects.json and installs the kit files from integration/hugo/.
Exit codes: 0 ok, 1 refused or validation error, 2 usage.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS_DIR)
DEFAULT_SHOWCASE = os.path.join(ROOT, "showcase")
DEFAULT_KIT = os.path.join(ROOT, "integration", "hugo")

COURSE_URL = "https://web.stanford.edu/class/archive/cs/cs193t/cs193t.1272/"
DEFAULT_SECTION = "final_project"
DEFAULT_SLUG = "showcase"
MARKER = "# installed by cs193t-showcase"
SHORTCODE_REL = "layouts/shortcodes/showcase-featured.html"
CONFIG_NAMES = [p + e for p in ("hugo", "config") for e in (".toml", ".yaml", ".yml", ".json")]
CONFIG_DIR_NAMES = [os.path.join("config", "_default", n) for n in CONFIG_NAMES]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class IntegrationError(Exception):
    """A refusal or validation error (exit code 1)."""


# ------------------------------------------------------------------ hugo site
def find_config(hugo):
    if not os.path.isdir(hugo):
        raise IntegrationError("%s is not a directory." % hugo)
    for rel in CONFIG_NAMES + CONFIG_DIR_NAMES:
        p = os.path.join(hugo, rel)
        if os.path.isfile(p):
            return p
    raise IntegrationError(
        "%s does not look like a Hugo site: no hugo.toml|yaml|yml|json, config.toml|yaml|yml|json "
        "or config/_default/{hugo,config}.* found." % hugo)


def parse_base_url(config_path):
    """Return baseURL from a config file, or None when it is not set."""
    with open(config_path, encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    ext = os.path.splitext(config_path)[1].lower()
    if ext == ".json":
        try:
            data = json.loads(text)
        except ValueError as e:
            raise IntegrationError("%s is not valid JSON (%s)." % (config_path, e))
        if isinstance(data, dict):
            for k, v in data.items():
                if str(k).lower() == "baseurl" and isinstance(v, str):
                    return v.strip() or None
        return None
    if ext == ".toml":
        # top-level keys only: stop at the first [table]
        head = re.split(r"(?m)^\s*\[", text, maxsplit=1)[0]
        m = re.search(r"""(?im)^\s*baseurl\s*=\s*(?:"([^"\n]*)"|'([^'\n]*)')""", head)
    else:
        m = re.search(r"""(?im)^baseurl\s*:\s*(?:"([^"\n]*)"|'([^'\n]*)'|([^\s#'"][^\s#]*))""", text)
    if not m:
        return None
    return next((g for g in m.groups() if g is not None), "").strip() or None


def normalize_base_url(url):
    u = (url or "").strip()
    if not u or re.search(r"[\s\"'<>\\]", u):
        raise IntegrationError("baseURL %r is empty or contains illegal characters." % url)
    local = re.match(r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)", u, re.I)
    if not (u.lower().startswith("https://") or local):
        raise IntegrationError("baseURL %r must start with https:// (http://localhost is allowed for tests)." % u)
    if not re.match(r"^https?://[^/]+", u):
        raise IntegrationError("baseURL %r has no host." % u)
    if "?" in u or "#" in u:
        raise IntegrationError("baseURL %r must not contain a query or fragment." % u)
    return u.rstrip("/") + "/"


def resolve_base_url(hugo, override=None):
    cfg = find_config(hugo)
    if override:
        return cfg, normalize_base_url(override)
    raw = parse_base_url(cfg)
    if not raw:
        raise IntegrationError("No baseURL found in %s; pass --base-url." % cfg)
    return cfg, normalize_base_url(raw)


# ------------------------------------------------------------------ demo guard
def load_projects(showcase):
    p = os.path.join(showcase, "data", "projects.json")
    if not os.path.isfile(p):
        raise IntegrationError("%s not found. Run tools/build_showcase.py first." % p)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as e:
        raise IntegrationError("%s is not valid JSON (%s)." % (p, e))
    return p, data


def check_demo(data, allow_demo):
    if isinstance(data, dict) and data.get("demo") is True and not allow_demo:
        raise IntegrationError(
            "showcase/data/projects.json is flagged \"demo\": true (sample data). The official site must never "
            "show placeholders as real student work. Build the real data first (tools/build_showcase.py), "
            "or pass --allow-demo for a private preview.")


# ------------------------------------------------------------------- mirroring
def _skip_name(name):
    return name == ".DS_Store" or name.endswith("~") or re.match(r"^\..*\.sw[a-p]$", name) is not None


def list_source(showcase, out=print):
    """Return {relpath(with '/'): abspath} for every regular file to publish."""
    files = {}
    for dp, dns, fns in os.walk(showcase):
        dns[:] = sorted(d for d in dns if not os.path.islink(os.path.join(dp, d)))
        for fn in sorted(fns):
            ap = os.path.join(dp, fn)
            rel = os.path.relpath(ap, showcase).replace(os.sep, "/")
            if _skip_name(fn):
                continue
            if os.path.islink(ap):
                out("  warning: skipping symlink %s" % rel)
                continue
            files[rel] = ap
    return files


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def render_file(rel, path, base_url):
    """Bytes to publish for one file (index.html at the root gets the course URL rewritten)."""
    data = _read(path)
    if rel == "index.html" and base_url != COURSE_URL:
        data = data.replace(COURSE_URL.encode(), base_url.encode())
    return data


def check_target(hugo, section, slug):
    """Refuse symlinked parents or targets that resolve outside <hugo>/static. Returns the target path."""
    static = os.path.join(hugo, "static")
    target = os.path.join(static, section, slug)
    cur = static
    for part in (section, slug):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise IntegrationError("Refusing: %s is a symlink." % cur)
        if os.path.lexists(cur) and not os.path.isdir(cur):
            raise IntegrationError("Refusing: %s exists and is not a directory." % cur)
    real_static = os.path.realpath(static)
    real_target = os.path.realpath(target)
    if os.path.commonpath([real_static, real_target]) != real_static or real_target == real_static:
        raise IntegrationError("Refusing: %s resolves outside %s." % (target, static))
    return target


def list_target(target):
    files = set()
    if os.path.isdir(target):
        for dp, _dns, fns in os.walk(target):
            for fn in fns:
                files.add(os.path.relpath(os.path.join(dp, fn), target).replace(os.sep, "/"))
    return files


def plan_mirror(source, target, base_url):
    """Return (added, changed, unchanged, removed) lists of relpaths."""
    existing = list_target(target)
    added, changed, same = [], [], []
    for rel, ap in source.items():
        if rel not in existing:
            added.append(rel)
        elif _read(os.path.join(target, *rel.split("/"))) == render_file(rel, ap, base_url):
            same.append(rel)
        else:
            changed.append(rel)
    removed = sorted(existing - set(source))
    return sorted(added), sorted(changed), sorted(same), removed


def do_mirror(source, target, base_url):
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    # Stage outside static/ (in the Hugo project root, same filesystem) so an interrupted run can never leave
    # a half-copied or old showcase where Hugo would publish it.
    hugo_root = os.path.dirname(os.path.dirname(parent))
    staging = os.path.join(hugo_root, ".cs193t-showcase-staging")
    os.makedirs(staging, mode=0o700, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="new_", dir=staging)
    old = None
    try:
        os.chmod(tmp, 0o755)
        for rel, ap in source.items():
            dest = os.path.join(tmp, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(render_file(rel, ap, base_url))
            os.chmod(dest, 0o644)
        for dp, dns, _f in os.walk(tmp):
            for d in dns:
                os.chmod(os.path.join(dp, d), 0o755)
        if os.path.lexists(target):
            old = tempfile.mkdtemp(prefix="old_", dir=staging)
            os.rmdir(old)
            os.rename(target, old)
        try:
            os.rename(tmp, target)
        except OSError:
            if old:
                os.rename(old, target)
                old = None
            raise
        tmp = None
    finally:
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
        if old and os.path.isdir(old):
            shutil.rmtree(old, ignore_errors=True)
        try:
            os.rmdir(staging)
        except OSError:
            pass  # not empty (another run, or leftovers to inspect); never inside static/


# ------------------------------------------------------------------- file copy
def atomic_write(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, t = tempfile.mkstemp(dir=d, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(t, 0o644)
        os.replace(t, path)
    finally:
        if os.path.exists(t):
            os.unlink(t)


def _check_no_symlinks(hugo, path):
    """Refuse when any existing component between hugo and path is a symlink."""
    rel = os.path.relpath(path, hugo)
    cur = hugo
    for part in rel.split(os.sep):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise IntegrationError("Refusing: %s is a symlink." % cur)


def rewrite_stub(text, section, slug):
    """Rewrite section/slug inside the url:, parent: and identifier: lines of the content stub."""
    if section == DEFAULT_SECTION and slug == DEFAULT_SLUG:
        return text
    out = []
    for line in text.split("\n"):
        m = re.match(r"^(\s*(?:-\s*)?)(url|parent|identifier)(\s*:\s*)(.*)$", line)
        if m:
            key, val = m.group(2), m.group(4)
            if key == "url":
                val = re.sub(r"(?<![\w-])%s(?![\w-])" % DEFAULT_SECTION, section, val)
                val = re.sub(r"(?<![\w-])%s(?![\w-])" % DEFAULT_SLUG, slug, val)
            elif key == "parent":
                val = re.sub(r"(?<![\w-])%s(?![\w-])" % DEFAULT_SECTION, section, val)
            else:
                val = re.sub(r"(?<![\w-])%s(?![\w-])" % DEFAULT_SLUG, slug, val)
            line = m.group(1) + key + m.group(3) + val
        out.append(line)
    return "\n".join(out)


def plan_install(dest, new_bytes, force, needs_marker):
    """Return (action, note). action in new|unchanged|update|skip."""
    if not os.path.lexists(dest):
        return "new", ""
    if os.path.islink(dest) or not os.path.isfile(dest):
        return "skip", "not a regular file"
    cur = _read(dest)
    if cur == new_bytes:
        return "unchanged", ""
    if needs_marker and MARKER.encode() not in cur and not force:
        return "skip", "no '%s' marker, so it looks hand-written; use --force to overwrite" % MARKER
    if force:
        return "update", ""
    return "skip", "differs from the kit version; use --force to overwrite"


# ------------------------------------------------------------------------ main
def integrate(hugo, section=DEFAULT_SECTION, slug=DEFAULT_SLUG, base_url=None, dry_run=False,
              force=False, no_menu=False, allow_demo=False, build=False,
              showcase_dir=DEFAULT_SHOWCASE, kit_dir=DEFAULT_KIT, out=print):
    """Run the installer. Returns an exit code (0 ok, 1 refused/error). Never raises IntegrationError."""
    try:
        return _integrate(hugo, section, slug, base_url, dry_run, force, no_menu, allow_demo, build,
                          showcase_dir, kit_dir, out)
    except IntegrationError as e:
        out("REFUSED: %s" % e)
        return 1


def _integrate(hugo, section, slug, base_url, dry_run, force, no_menu, allow_demo, build,
               showcase_dir, kit_dir, out):
    hugo = os.path.abspath(hugo)
    prefix = "[dry run] " if dry_run else ""
    # 1. Hugo site
    cfg, base = resolve_base_url(hugo, base_url)
    out("Hugo site : %s" % hugo)
    out("Config    : %s" % os.path.relpath(cfg, hugo))
    out("baseURL   : %s%s" % (base, " (from --base-url)" if base_url else ""))
    out("Page URL  : %s%s/%s/" % (base, section, slug))
    # 2. demo guard
    if not os.path.isfile(os.path.join(showcase_dir, "index.html")):
        raise IntegrationError("%s has no index.html." % showcase_dir)
    pj_path, data = load_projects(showcase_dir)
    check_demo(data, allow_demo)
    if isinstance(data, dict) and data.get("demo") is True:
        out("WARNING: publishing SAMPLE data (--allow-demo).")
    # 3. mirror plan
    target = check_target(hugo, section, slug)
    source = list_source(showcase_dir, out)
    added, changed, same, removed = plan_mirror(source, target, base)
    rel_target = os.path.relpath(target, hugo)
    out("")
    out("%sMirror showcase/ -> %s/: %d added, %d changed, %d unchanged, %d removed"
        % (prefix, rel_target, len(added), len(changed), len(same), len(removed)))
    for label, items in (("add", added), ("update", changed), ("delete", removed)):
        for r in items[:20]:
            out("    %s %s" % (label, r))
        if len(items) > 20:
            out("    ... and %d more to %s" % (len(items) - 20, label))
    if base != COURSE_URL and "index.html" in source and COURSE_URL.encode() in _read(source["index.html"]):
        out("    index.html: course URL rewritten to %s" % base)
    # 4. data
    data_dest = os.path.join(hugo, "data", "showcase", "projects.json")
    _check_no_symlinks(hugo, data_dest)
    data_bytes = _read(pj_path)
    data_state = ("new" if not os.path.exists(data_dest)
                  else "unchanged" if _read(data_dest) == data_bytes else "update")
    out("%sData: data/showcase/projects.json (%s)" % (prefix, data_state))
    # 5. kit
    installs = []  # (rel, dest, bytes, needs_marker)
    shortcode_src = os.path.join(kit_dir, *SHORTCODE_REL.split("/"))
    stub_rel = "content/%s/%s.md" % (section, slug)
    stub_src = os.path.join(kit_dir, "content", DEFAULT_SECTION, DEFAULT_SLUG + ".md")
    if not os.path.isfile(shortcode_src):
        raise IntegrationError("Kit file missing: %s" % shortcode_src)
    installs.append((SHORTCODE_REL, _read(shortcode_src), False))
    if not no_menu:
        if not os.path.isfile(stub_src):
            raise IntegrationError("Kit file missing: %s" % stub_src)
        text = _read(stub_src).decode("utf-8")
        installs.append((stub_rel, rewrite_stub(text, section, slug).encode("utf-8"), True))
    else:
        out("Menu entry skipped (--no-menu).")
    out("")
    out("%sKit files:" % prefix)
    todo = []
    for rel, nb, needs_marker in installs:
        dest = os.path.join(hugo, *rel.split("/"))
        _check_no_symlinks(hugo, dest)
        action, note = plan_install(dest, nb, force, needs_marker)
        shown = {"new": "install", "unchanged": "unchanged", "update": "overwrite (--force)", "skip": "SKIPPED"}[action]
        out("  %s: %s%s" % (rel, shown, " - " + note if note else ""))
        if action == "skip" and os.path.isfile(dest):
            out("    see the diff: diff %s <kit>/%s" % (dest, "content/%s/%s.md" % (DEFAULT_SECTION, DEFAULT_SLUG)
                                                       if needs_marker else rel))
        if action in ("new", "update"):
            todo.append((dest, nb))
    folder = os.path.join(hugo, "content", section, slug)
    if os.path.isdir(folder):
        out("  WARNING: %s/ exists as a folder; it would collide with the static page at %s%s/%s/."
            % (os.path.relpath(folder, hugo), base, section, slug))
    # apply
    if dry_run:
        out("")
        out("Dry run: nothing was changed.")
        if build:
            out("--build skipped (dry run).")
    else:
        do_mirror(source, target, base)
        atomic_write(data_dest, data_bytes)
        for dest, nb in todo:
            atomic_write(dest, nb)
        out("")
        out("Done: %d files mirrored, %d stale files removed." % (len(source), len(removed)))
        if build:
            if not _build(hugo, section, slug, target, source, out):
                return 1
    out("")
    out("Next steps:")
    out("  1. Review locally:  cd %s && hugo server   then open /%s/%s/" % (hugo, section, slug))
    out("  2. Deploy the Hugo site as usual.")
    return 0


def _build(hugo, section, slug, target, source, out):
    exe = shutil.which("hugo")
    if not exe:
        out("--build: hugo is not installed (not on PATH); skipping the build check.")
        return True
    dest = tempfile.mkdtemp(prefix="hugo_build_")
    try:
        out("Building with hugo ...")
        try:
            r = subprocess.run([exe, "--quiet", "--destination", dest], cwd=hugo, capture_output=True,
                               text=True, timeout=300)
        except subprocess.TimeoutExpired:
            out("--build FAILED: hugo timed out after 300 s.")
            return False
        if r.returncode != 0:
            out("--build FAILED: hugo exited with %d.\n%s" % (r.returncode, (r.stderr or "").strip()))
            return False
        base = os.path.join(dest, section, slug)
        problems = []
        built_index = os.path.join(base, "index.html")
        if not os.path.isfile(built_index):
            problems.append("%s/%s/index.html missing from the build" % (section, slug))
        elif _read(built_index) != _read(os.path.join(target, "index.html")):
            problems.append("built index.html differs from the installed static copy")
        if ".htaccess" in source and not os.path.isfile(os.path.join(base, ".htaccess")):
            problems.append(".htaccess missing from the build")
        if not os.path.isfile(os.path.join(base, "data", "projects.json")):
            problems.append("data/projects.json missing from the build")
        if problems:
            out("--build FAILED:")
            for p in problems:
                out("  - " + p)
            return False
        out("--build OK: index.html, data/projects.json%s present and correct." % (
            ", .htaccess" if ".htaccess" in source else ""))
        return True
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install the showcase into the course's Hugo site.")
    ap.add_argument("--hugo", required=True, help="path to the Hugo site")
    ap.add_argument("--section", default=DEFAULT_SECTION)
    ap.add_argument("--slug", default=DEFAULT_SLUG)
    ap.add_argument("--base-url", help="override the baseURL read from the Hugo config")
    ap.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    ap.add_argument("--force", action="store_true", help="overwrite differing or hand-written kit files")
    ap.add_argument("--no-menu", action="store_true", help="do not install the content stub (menu entry)")
    ap.add_argument("--allow-demo", action="store_true", help="allow publishing sample (demo) data")
    ap.add_argument("--build", action="store_true", help="run hugo afterwards and verify the output")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    for label, v in (("--section", a.section), ("--slug", a.slug)):
        if not NAME_RE.match(v):
            print("usage error: %s must match [A-Za-z0-9_-]+ (got %r)" % (label, v), file=sys.stderr)
            return 2
    return integrate(a.hugo, a.section, a.slug, a.base_url, a.dry_run, a.force, a.no_menu, a.allow_demo, a.build)


if __name__ == "__main__":
    sys.exit(main())
