"""Exercise the offline documentation builder through its public command line."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "render_docs.py"


class RenderDocsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("pyproject.toml", '[project]\nname = "example"\nversion = "0.1.7"\n')
        self.write("docs/assets/site.css", "body { color: black; }\n")
        self.write(
            "docs/assets/terac-icon.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<path fill-rule="evenodd" clip-rule="evenodd" d="M0 0 L9 9"/></svg>\n',
        )
        self.write("docs/index.md", "# Cookbook\n\n## Start here\n\nWelcome.\n")
        self.write("docs/architecture.md", "# Architecture\n\n## Runner\n\nLocal replay.\n")
        self.write("docs/recipes/index.md", "# Recipes\n\n[Oracle](oracle.md)\n")
        self.write("docs/recipes/oracle.md", "# Oracle\n\n## Run\n\nReplay.\n")
        self.write("CONTRIBUTING.md", "# Contributing\n\n## Workflow\n\nReview changes.\n")

    def write(self, name, content):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def run_builder(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_generate_offline_reading_views_and_rebase_source_links(self):
        self.write("recipes/oracle/review-contract.md", "# Review contract\n")
        self.write("docs/assets/diagram.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>\n')
        self.write(
            "CONTRIBUTING.md",
            "# Contributing\n\n"
            "[Oracle](docs/recipes/oracle.md?view=full#run)\n\n"
            "[Contract](recipes/oracle/review-contract.md)\n\n"
            "![Diagram](docs/assets/diagram.svg)\n\n"
            "```python\nprint('replay')\n```\n\n"
            "| Mode | Effect |\n| --- | --- |\n| Replay | None |\n",
        )
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        page = (self.root / "docs/contributing.html").read_text(encoding="utf-8")
        self.assertIn('href="recipes/oracle.html?view=full#run"', page)
        self.assertIn('href="../recipes/oracle/review-contract.md"', page)
        self.assertIn('src="assets/diagram.svg"', page)
        self.assertIn('href="assets/site.css"', page)
        self.assertIn('src="assets/terac-icon.svg"', page)
        nested = (self.root / "docs/recipes/oracle.html").read_text(encoding="utf-8")
        self.assertIn('src="../assets/terac-icon.svg"', nested)
        self.assertIn('<code class="language-python">', page)
        self.assertIn("<table>", page)
        self.assertIn("0.1.7", page)
        self.assertIn("source-sha256:", page)
        self.assertNotIn(str(self.root), page)
        self.assertTrue((self.root / "docs/recipes/oracle.html").is_file())

    def test_brand_asset_rejects_nonstatic_fill_rules_before_writing(self):
        for attribute in ("fill-rule", "clip-rule"):
            with self.subTest(attribute=attribute):
                self.write(
                    "docs/assets/terac-icon.svg",
                    '<svg xmlns="http://www.w3.org/2000/svg">'
                    f'<path {attribute}="url(https://example.invalid/a)" d="M0 0"/>'
                    "</svg>",
                )
                result = self.run_builder()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("unsupported SVG fill rule", result.stdout)
                self.assertFalse((self.root / "docs/index.html").exists())

    def test_check_detects_drift_and_missing_output_without_writing(self):
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.write("docs/index.md", "# Cookbook\n\n## Start here\n\nRevised guide.\n")
        before = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("out of date: docs/index.html", result.stdout)
        after = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        self.assertEqual(after, before)

        (self.root / "docs/architecture.html").unlink()
        before = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing output: docs/architecture.html", result.stdout)
        after = {
            p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()
        }
        self.assertEqual(after, before)

    def test_rejects_missing_local_targets_fragments_and_assets(self):
        for label, destination, expected in [
            ("target", "absent.md", "missing local target"),
            ("fragment", "recipes/oracle.md#absent", "missing fragment"),
            ("asset", "assets/absent.svg", "missing local target"),
        ]:
            with self.subTest(label=label):
                syntax = "![Diagram]" if label == "asset" else "[Guide]"
                self.write("docs/index.md", f"# Cookbook\n\n{syntax}({destination})\n")
                before = {
                    p.relative_to(self.root): p.read_bytes()
                    for p in self.root.rglob("*")
                    if p.is_file()
                }
                result = self.run_builder("--check")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                after = {
                    p.relative_to(self.root): p.read_bytes()
                    for p in self.root.rglob("*")
                    if p.is_file()
                }
                self.assertEqual(after, before)

        self.write("docs/index.md", "# Cookbook\n\nWelcome.\n")
        (self.root / "docs/assets/site.css").unlink()
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("missing required source or asset: docs/assets/site.css", result.stdout)

    def test_unsupported_css_forms_and_svg_css_cannot_load_remote_assets(self):
        self.assertEqual(self.run_builder().returncode, 0)
        for css in [
            r'@im\70ort "https://example.invalid/style.css";',
            'body { background: image-set("https://example.invalid/a.png" 2x); }',
            'body { background: -webkit-image-set("https://example.invalid/a.png" 2x); }',
        ]:
            with self.subTest(css=css):
                self.write("docs/assets/site.css", css)
                before = (self.root / "docs/index.html").read_bytes()
                for arguments in [(), ("--check",)]:
                    result = self.run_builder(*arguments)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("unsupported CSS", result.stdout)
                self.assertEqual((self.root / "docs/index.html").read_bytes(), before)

        self.write("docs/assets/site.css", "body { color: black; }")
        self.write("docs/index.md", "# Cookbook\n\n![Diagram](assets/diagram.svg)\n")
        self.write(
            "docs/assets/diagram.svg",
            '<svg xmlns="http://www.w3.org/2000/svg"><style>'
            'rect { fill: url("https://example.invalid/a.svg"); }</style></svg>',
        )
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("external asset", result.stdout)

    def test_offline_assets_cannot_load_remote_content_or_missing_css_images(self):
        for css, expected in [
            ('body { background: url("https://example.com/image.png"); }', "external asset"),
            ('body { background: url("missing.png"); }', "missing local target"),
            ('@import "https://example.com/style.css";', "CSS @import is unsupported"),
        ]:
            with self.subTest(css=css):
                self.write("docs/assets/site.css", css)
                result = self.run_builder()
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertFalse((self.root / "docs/index.html").exists())

    def test_unsupported_html_cannot_bypass_the_offline_asset_check(self):
        self.write("docs/assets/diagram.svg", '<svg xmlns="http://www.w3.org/2000/svg"/>\n')
        self.assertEqual(self.run_builder().returncode, 0)
        for markup in [
            '<img src="assets/diagram.svg" srcset="https://example.invalid/a.svg 2x">',
            '<p style="background-image:url(https://example.invalid/a.svg)">Text</p>',
            '<video poster="https://example.invalid/a.svg"></video>',
            '<object data="https://example.invalid/a.svg"></object>',
            '<script>alert("test")</script>',
            '<iframe src="https://example.invalid/"></iframe>',
            '<form action="https://example.invalid/"><input></form>',
            '<img src="assets/diagram.svg" onerror="alert(1)">',
        ]:
            with self.subTest(markup=markup):
                self.write("docs/index.md", "# Cookbook\n\n" + markup + "\n")
                before = {
                    p.relative_to(self.root): p.read_bytes()
                    for p in self.root.rglob("*")
                    if p.is_file()
                }
                for arguments in [(), ("--check",)]:
                    result = self.run_builder(*arguments)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("unsupported document HTML", result.stdout)
                after = {
                    p.relative_to(self.root): p.read_bytes()
                    for p in self.root.rglob("*")
                    if p.is_file()
                }
                self.assertEqual(after, before)

    def test_static_markdown_local_css_and_svg_markers_still_render(self):
        self.write(
            "docs/index.md",
            "# Cookbook\n\nA static document.\n\n"
            "| Left | Right |\n| :--- | ---: |\n| Replay | Local |\n\n"
            "```text\n<script>shown as an example</script>\n```\n\n"
            "![Diagram](assets/diagram.svg)\n",
        )
        self.write("docs/assets/site.css", 'body { background: url("diagram.svg"); }')
        self.write(
            "docs/assets/diagram.svg",
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<defs><marker id="arrow"><path d="M0 0 L1 1"/></marker>'
            "<style>.line { marker-end: url(#arrow); }</style></defs>"
            '<path class="line" d="M0 0 L9 9"/></svg>',
        )
        result = self.run_builder()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        page = (self.root / "docs/index.html").read_text(encoding="utf-8")
        self.assertIn('<th align="left">', page)
        self.assertIn('<th align="right">', page)
        self.assertIn("&lt;script&gt;shown as an example&lt;/script&gt;", page)
        result = self.run_builder("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
