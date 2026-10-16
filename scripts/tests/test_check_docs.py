"""Exercise repository documentation checks through their command interface."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / "check_docs.py"


class DocumentationChecks(unittest.TestCase):
    def run_check(self, files):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), "--root", str(root)],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_missing_relative_link_is_rejected(self):
        result = self.run_check({"README.md": "[Missing](missing.md)\n"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing relative link", result.stdout)

    def test_quoted_credential_is_rejected_without_echoing_value(self):
        value = "fixture" + "abcdef0123456789" * 2
        result = self.run_check({"fixture.json": '{"TERAC_API_KEY": "' + value + '"}'})
        self.assertEqual(result.returncode, 1)
        self.assertIn("credential pattern", result.stdout)
        self.assertNotIn(value, result.stdout + result.stderr)

    def test_private_key_marker_is_rejected(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        result = self.run_check({"fixture.txt": marker + "\nfixture bytes\n"})
        self.assertEqual(result.returncode, 1)
        self.assertIn("private key pattern", result.stdout)

    def test_bearer_and_header_variants_are_rejected(self):
        value = "fixture" + "abcdef0123456789" * 2
        for content in [
            "authorization: Bearer " + value,
            '  "x-api-key" : "' + value + '"',
            'client_secret = "' + value + '"',
        ]:
            with self.subTest(content=content.split(":")[0]):
                result = self.run_check({"fixture.yaml": content})
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(value, result.stdout + result.stderr)

    def test_valid_links_and_fenced_examples_are_accepted(self):
        result = self.run_check(
            {
                "README.md": "[Guide](<docs/a b.md>)\n```text\n[Example](absent.md)\n```\n"
                "[Web](https://example.com)\n[Self](#heading)\n",
                "docs/a b.md": "# Guide\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_env_and_multiline_credentials_are_rejected(self):
        value = "fixture" + "abcdef0123456789" * 2
        for name, content in [
            (".env", "TERAC_API_KEY=" + value),
            (".env.example", "TERAC_API_KEY=" + value),
            ("fixture.json", '{"TERAC_API_KEY":\n"' + value + '"}'),
            ("fixture.yml", "TERAC_API_KEY: |\n  " + value),
        ]:
            with self.subTest(name=name):
                result = self.run_check({name: content})
                self.assertEqual(result.returncode, 1)
                self.assertNotIn(value, result.stdout + result.stderr)

    def test_reference_link_to_missing_file_is_rejected(self):
        result = self.run_check({"README.md": "[Guide][guide]\n[guide]: missing.md\n"})
        self.assertEqual(result.returncode, 1)

    def test_parenthesized_destination_is_accepted(self):
        result = self.run_check(
            {
                "README.md": "[Guide](docs/a(b).md)\n",
                "docs/a(b).md": "# Guide\n",
            }
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_link_case_is_checked_consistently(self):
        result = self.run_check({"README.md": "[Guide](guide.md)\n", "Guide.md": "# Guide\n"})
        self.assertEqual(result.returncode, 1)

    def test_multiline_destination_is_checked(self):
        result = self.run_check({"README.md": "[Guide](\nmissing.md\n)\n"})
        self.assertEqual(result.returncode, 1)

    def test_inline_or_indented_backticks_do_not_hide_links(self):
        for content in ["    ```\n\n[x](missing.md)\n", "```inline```\n\n[x](missing.md)\n"]:
            with self.subTest(content=content.splitlines()[0]):
                result = self.run_check({"README.md": content})
                self.assertEqual(result.returncode, 1)

    def test_key_files_and_shipped_text_formats_are_scanned(self):
        marker = "-----BEGIN " + "PRIVATE KEY-----"
        value = "fixture" + "abcdef0123456789" * 2
        fixtures = [("fixture.pem", marker)] + [
            ("fixture" + suffix, "TERAC_API_KEY=" + value)
            for suffix in [".sh", ".psm1", ".html", ".css", ".svg"]
        ]
        for name, content in fixtures:
            with self.subTest(name=name):
                result = self.run_check({name: content})
                self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
