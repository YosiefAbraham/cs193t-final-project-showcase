import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
sys.path.insert(0, TOOLS)

import integrate_hugo as I  # noqa: E402

STUB = ("---\ntitle: Showcase\nurl: /final_project/showcase/\nmenu:\n  main:\n    parent: final_project\n"
        "    weight: 4\n# installed by cs193t-showcase\n---\n{{< showcase-featured >}}\n")
SHORTCODE = "{{ range first 3 site.Data.showcase.projects.projects }}x{{ end }}\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="integrate_test_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.hugo = os.path.join(self.tmp, "hugo")
        os.makedirs(os.path.join(self.hugo, "static"))
        os.makedirs(os.path.join(self.hugo, "content"))
        self.write(self.hugo, "hugo.toml", 'baseURL = "https://example.edu/c/x/"\ntitle = "t"\n')
        self.sc = os.path.join(self.tmp, "showcase")
        self.write(self.sc, "index.html", "<a href='%s'>x</a>%s" % (I.COURSE_URL, I.COURSE_URL))
        self.write(self.sc, "app.js", "var u='%s';" % I.COURSE_URL)
        self.write(self.sc, ".htaccess", "Options -Indexes\n")
        self.write(self.sc, "data/projects.json", json.dumps({"demo": False, "projects": []}))
        self.write(self.sc, "assets/a.css", "body{}")
        self.kit = os.path.join(self.tmp, "kit")
        self.write(self.kit, "content/final_project/showcase.md", STUB)
        self.write(self.kit, "layouts/shortcodes/showcase-featured.html", SHORTCODE)
        self.log = []

    def write(self, base, rel, text):
        p = os.path.join(base, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)
        return p

    def read(self, *parts):
        with open(os.path.join(*parts)) as f:
            return f.read()

    def run_it(self, **kw):
        kw.setdefault("showcase_dir", self.sc)
        kw.setdefault("kit_dir", self.kit)
        kw.setdefault("out", self.log.append)
        return I.integrate(self.hugo, **kw)

    def text(self):
        return "\n".join(self.log)

    def static(self, *p):
        return os.path.join(self.hugo, "static", "final_project", "showcase", *p)

    def snapshot(self):
        snap = {}
        for dp, _d, fns in os.walk(self.hugo):
            for fn in fns:
                p = os.path.join(dp, fn)
                snap[os.path.relpath(p, self.hugo)] = open(p, "rb").read()
        return snap


class ConfigTests(Base):
    def test_toml(self):
        cfg, base = I.resolve_base_url(self.hugo)
        self.assertTrue(cfg.endswith("hugo.toml"))
        self.assertEqual(base, "https://example.edu/c/x/")

    def test_yaml_case_insensitive_and_comments(self):
        os.remove(os.path.join(self.hugo, "hugo.toml"))
        self.write(self.hugo, "config.yaml", "# baseURL: https://wrong.example/\nBaseUrl: 'https://y.example/a' # c\n")
        self.assertEqual(I.resolve_base_url(self.hugo)[1], "https://y.example/a/")

    def test_config_dir_and_json(self):
        os.remove(os.path.join(self.hugo, "hugo.toml"))
        self.write(self.hugo, "config/_default/hugo.json", '{"baseURL": "https://j.example//"}')
        cfg, base = I.resolve_base_url(self.hugo)
        self.assertIn("_default", cfg)
        self.assertEqual(base, "https://j.example/")

    def test_toml_comment_and_table_ignored(self):
        self.write(self.hugo, "hugo.toml", '# baseURL = "https://no/"\n[params]\nbaseURL = "https://no2/"\n')
        with self.assertRaises(I.IntegrationError):
            I.resolve_base_url(self.hugo)
        self.assertEqual(I.resolve_base_url(self.hugo, "https://ok.example")[1], "https://ok.example/")

    def test_not_a_hugo_site(self):
        os.remove(os.path.join(self.hugo, "hugo.toml"))
        self.assertEqual(self.run_it(), 1)
        self.assertIn("does not look like a Hugo site", self.text())

    def test_base_url_override_and_validation(self):
        self.assertEqual(self.run_it(base_url="https://o.example/z"), 0)
        self.assertIn("https://o.example/z/", self.read(self.static("index.html")))
        for bad in ("http://example.com/", "ftp://x/", "https://", "https://a b/"):
            with self.assertRaises(I.IntegrationError):
                I.normalize_base_url(bad)
        self.assertEqual(I.normalize_base_url("http://localhost:1313"), "http://localhost:1313/")


class DemoTests(Base):
    def test_demo_refused_then_allowed(self):
        self.write(self.sc, "data/projects.json", json.dumps({"demo": True, "projects": []}))
        self.assertEqual(self.run_it(), 1)
        self.assertIn("demo", self.text())
        self.assertFalse(os.path.exists(self.static()))
        self.assertEqual(self.run_it(allow_demo=True), 0)
        self.assertTrue(os.path.isfile(self.static("index.html")))


class MirrorTests(Base):
    def test_mirror_deletes_stale_and_keeps_htaccess(self):
        self.write(self.sc, "x.txt", "x")
        self.run_it()
        self.assertEqual(self.read(self.static(".htaccess")), "Options -Indexes\n")
        self.write(self.hugo, "static/final_project/showcase/stale.html", "old")
        self.write(self.hugo, "static/final_project/showcase/sub/old.js", "old")
        self.write(self.hugo, "static/final_project/other.html", "keep")
        os.remove(os.path.join(self.sc, "x.txt"))
        self.run_it()
        self.assertFalse(os.path.exists(self.static("stale.html")))
        self.assertFalse(os.path.exists(self.static("sub")))
        self.assertFalse(os.path.exists(self.static("x.txt")))
        self.assertTrue(os.path.exists(self.static(".htaccess")))
        self.assertEqual(self.read(self.hugo, "static", "final_project", "other.html"), "keep")
        leftovers = [n for n in os.listdir(os.path.dirname(self.static())) if n.startswith(".showcase_")]
        self.assertEqual(leftovers, [])

    def test_skips_junk(self):
        for n in (".DS_Store", "a.html~", ".a.html.swp"):
            self.write(self.sc, n, "j")
        self.run_it()
        for n in (".DS_Store", "a.html~", ".a.html.swp"):
            self.assertFalse(os.path.exists(self.static(n)))

    def test_course_url_rewritten_only_in_index(self):
        self.run_it()
        idx = self.read(self.static("index.html"))
        self.assertNotIn(I.COURSE_URL, idx)
        self.assertEqual(idx.count("https://example.edu/c/x/"), 2)
        self.assertIn(I.COURSE_URL, self.read(self.static("app.js")))

    def test_same_base_url_untouched(self):
        self.run_it(base_url=I.COURSE_URL)
        self.assertEqual(self.read(self.static("index.html")), self.read(self.sc, "index.html"))

    def test_symlink_target_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.makedirs(os.path.join(self.hugo, "static", "final_project"))
        os.symlink(outside, self.static())
        self.assertEqual(self.run_it(), 1)
        self.assertIn("symlink", self.text())
        self.assertEqual(os.listdir(outside), [])

    def test_symlink_parent_refused(self):
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(outside)
        os.symlink(outside, os.path.join(self.hugo, "static", "final_project"))
        self.assertEqual(self.run_it(), 1)
        self.assertEqual(os.listdir(outside), [])

    def test_tools_never_copied(self):
        self.run_it()
        self.assertFalse(os.path.exists(os.path.join(self.hugo, "static", "tools")))
        self.assertFalse(os.path.exists(os.path.join(self.hugo, "tools")))

    def test_data_copy(self):
        self.run_it()
        self.assertEqual(self.read(self.hugo, "data", "showcase", "projects.json"),
                         self.read(self.sc, "data", "projects.json"))
        self.assertTrue(os.path.isfile(self.static("data", "projects.json")))


class KitTests(Base):
    def stub(self):
        return os.path.join(self.hugo, "content", "final_project", "showcase.md")

    def sc_path(self):
        return os.path.join(self.hugo, "layouts", "shortcodes", "showcase-featured.html")

    def test_new_and_unchanged(self):
        self.run_it()
        self.assertEqual(self.read(self.stub()), STUB)
        self.assertEqual(self.read(self.sc_path()), SHORTCODE)
        self.log.clear()
        self.run_it()
        self.assertEqual(self.text().count(": unchanged"), 2)
        self.assertIn("projects.json (unchanged)", self.text())

    def test_differs_skipped_then_force(self):
        self.run_it()
        self.write(self.hugo, "layouts/shortcodes/showcase-featured.html", "mine")
        self.log.clear()
        self.assertEqual(self.run_it(), 0)
        self.assertEqual(self.read(self.sc_path()), "mine")
        self.assertIn("SKIPPED", self.text())
        self.assertIn("diff", self.text())
        self.run_it(force=True)
        self.assertEqual(self.read(self.sc_path()), SHORTCODE)

    def test_marker_protection(self):
        self.write(self.hugo, "content/final_project/showcase.md", "---\ntitle: Mine\n---\nhand written\n")
        self.run_it()
        self.assertIn("hand written", self.read(self.stub()))
        self.assertIn("marker", self.text())
        self.run_it(force=True)
        self.assertIn("installed by cs193t-showcase", self.read(self.stub()))

    def test_marked_stub_updates_only_with_force(self):
        self.write(self.hugo, "content/final_project/showcase.md", STUB + "extra\n")
        self.run_it()
        self.assertIn("extra", self.read(self.stub()))  # differs: still skipped
        self.run_it(force=True)
        self.assertNotIn("extra", self.read(self.stub()))

    def test_no_menu(self):
        self.run_it(no_menu=True)
        self.assertFalse(os.path.exists(self.stub()))
        self.assertTrue(os.path.exists(self.sc_path()))

    def test_section_slug_rewrite(self):
        self.assertEqual(self.run_it(section="projects", slug="gallery"), 0)
        p = os.path.join(self.hugo, "content", "projects", "gallery.md")
        t = self.read(p)
        self.assertIn("url: /projects/gallery/", t)
        self.assertIn("parent: projects", t)
        self.assertIn("# installed by cs193t-showcase", t)  # marker keeps 'showcase'
        self.assertTrue(os.path.isfile(os.path.join(self.hugo, "static", "projects", "gallery", "index.html")))

    def test_folder_collision_warning(self):
        os.makedirs(os.path.join(self.hugo, "content", "final_project", "showcase"))
        self.run_it()
        self.assertIn("collide", self.text())

    def test_missing_kit_refused(self):
        os.remove(os.path.join(self.kit, "layouts", "shortcodes", "showcase-featured.html"))
        self.assertEqual(self.run_it(), 1)


class DryRunAndBuild(Base):
    def test_dry_run_changes_nothing(self):
        self.write(self.hugo, "static/final_project/showcase/stale.html", "old")
        before = self.snapshot()
        self.assertEqual(self.run_it(dry_run=True, build=True), 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(os.path.exists(os.path.join(self.hugo, "data")))
        self.assertIn("nothing was changed", self.text())
        self.assertIn("delete stale.html", self.text())

    def test_build_skipped_without_hugo(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(self.run_it(build=True), 0)
        self.assertIn("hugo is not installed", self.text())

    def test_build_failure_reports_stderr(self):
        fake = mock.Mock(returncode=1, stderr="boom template error")
        with mock.patch("shutil.which", return_value="/x/hugo"), mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(self.run_it(build=True), 1)
        self.assertIn("boom template error", self.text())

    def test_build_success_checks_output(self):
        def fake_run(cmd, cwd=None, **kw):
            dest = cmd[cmd.index("--destination") + 1]
            shutil.copytree(os.path.join(cwd, "static"), dest, dirs_exist_ok=True)
            return mock.Mock(returncode=0, stderr="")
        with mock.patch("shutil.which", return_value="/x/hugo"), mock.patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(self.run_it(build=True), 0)
        self.assertIn("--build OK", self.text())


class CliTests(Base):
    def test_usage_errors(self):
        with mock.patch("sys.stderr", new=io.StringIO()):
            self.assertEqual(I.main([]), 2)
            self.assertEqual(I.main(["--hugo", self.hugo, "--slug", "../x"]), 2)

    def test_cli_validation_error_exit_1(self):
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(I.main(["--hugo", os.path.join(self.tmp, "nope")]), 1)


if __name__ == "__main__":
    unittest.main()
