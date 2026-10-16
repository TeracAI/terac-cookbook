"""Offline Markdown links and narrow credential-pattern checks for repo text."""

import argparse
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK_START = re.compile(r"!?\[[^\]\n]*\]\(")
REFERENCE = re.compile(r"^[ \t]{0,3}\[[^\]\n]+\]:\s*(.+)", re.MULTILINE)
SKIP_DIRS = {".git", ".tmp", ".venv", "node_modules", "__pycache__"}
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".env",
    ".pem",
    ".key",
    ".ps1",
    ".psm1",
    ".html",
    ".css",
    ".svg",
    ".sh",
    ".bash",
}
CREDENTIAL = re.compile(
    r"(?i)\b(?:terac_api_key|api_key|x-api-key|access_token|refresh_token|client_secret)"
    r"\b[\"'`]*\s*[:=]\s*(?:[|>][-+]?\s*)?[\"'`]?([a-z0-9_./+~=-]{20,})"
)
BEARER = re.compile(r"(?i)\bauthorization[\"'`]*\s*[:=]\s*[\"'`]?bearer\s+([a-z0-9_./+~=-]{20,})")
PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")


def text_files(root):
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0 and Path(result.stdout.strip()).resolve() == root:
        names = (
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
                capture_output=True,
                check=True,
            )
            .stdout.decode("utf-8")
            .split("\0")
        )
        return [root / name for name in sorted(set(names)) if name]
    return [p for p in root.rglob("*") if not SKIP_DIRS.intersection(p.relative_to(root).parts)]


def prose_lines(content):
    """Keep line locations while omitting fenced/inline examples from links."""
    fence = None
    for line in content.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker and fence is None and marker.group(1)[0] == "`" and "`" in marker.group(2):
            marker = None
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not marker.group(2).strip():
                fence = None
            yield ""
        elif fence:
            yield ""
        else:
            yield re.sub(r"(`+).*?\1", "", line)


def link_targets(line):
    for reference in REFERENCE.finditer(line):
        yield reference.start(), reference.group(1)
    for match in LINK_START.finditer(line):
        start = match.end()
        depth = 1
        escaped = False
        for position in range(start, len(line)):
            char = line[position]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    yield match.start(), line[start:position]
                    break


def exists_with_case(path):
    """Apply CI's case-sensitive link rules on Windows too."""
    if not path.exists():
        return False
    absolute = path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        if part == ".":
            continue
        if part == "..":
            current = current.parent
            continue
        if not current.is_dir() or part not in {p.name for p in current.iterdir()}:
            return False
        current = current / part
    return True


def check(root):
    failures = []
    checked = 0
    for path in text_files(root):
        env_file = path.name == ".env" or path.name.startswith(".env.")
        if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and not env_file):
            continue
        content = path.read_text(encoding="utf-8")
        for pattern, label in [
            (CREDENTIAL, "credential pattern"),
            (BEARER, "credential pattern"),
            (PRIVATE_KEY, "private key pattern"),
        ]:
            for match in pattern.finditer(content):
                number = content.count("\n", 0, match.start()) + 1
                failures.append(f"{path.relative_to(root)}:{number}: {label}")
        if path.suffix.lower() != ".md":
            continue
        checked += 1
        prose = "\n".join(prose_lines(content))
        for offset, target in link_targets(prose):
            number = prose.count("\n", 0, offset) + 1
            target = target.strip()
            if target.startswith("<"):
                target = target[1:].split(">", 1)[0]
            else:
                target = re.sub(r'\s+["\'][^"\']*["\']$', "", target)
            if not target or target.startswith(("#", "//")):
                continue
            parts = urlsplit(target)
            if parts.scheme:
                continue
            destination = re.sub(r"\\([()])", r"\1", unquote(parts.path))
            if destination and not exists_with_case(path.parent / destination):
                failures.append(f"{path.relative_to(root)}:{number}: missing relative link")
    return checked, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    root = parser.parse_args().root.resolve()
    if not root.is_dir():
        parser.error("check root must be an existing directory")
    checked, failures = check(root)
    for failure in failures:
        print(failure)
    print(f"Markdown checked: {checked}; failures: {len(failures)}")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
