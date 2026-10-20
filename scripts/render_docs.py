"""Build the reviewed Markdown pages as deterministic, offline HTML views.

Only the fixed public documentation list below is rendered. Markdown remains the
source; this is not a renderer for runtime payloads or untrusted provider text.
"""

import argparse
import hashlib
import html
import os
import re
import tomllib
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import markdown

PAGES = {
    "docs/index.md": "docs/index.html",
    "docs/architecture.md": "docs/architecture.html",
    "docs/recipes/index.md": "docs/recipes/index.html",
    "docs/recipes/oracle.md": "docs/recipes/oracle.html",
    "CONTRIBUTING.md": "docs/contributing.html",
}
OPTIONAL_PAGES = {
    "docs/mcp.md": "docs/mcp.html",
    "docs/terac-access.md": "docs/terac-access.html",
}
MARKDOWN_ATTRIBUTES = {
    **{
        tag: set()
        for tag in "p pre strong em blockquote ul ol li hr br table thead tbody tr".split()
    },
    **{f"h{level}": {"id"} for level in range(1, 7)},
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "code": {"class"},
    "th": {"align"},
    "td": {"align"},
    "div": {"class"},
}
SVG_ATTRIBUTES = {
    "svg": {"viewBox", "role", "aria-labelledby"},
    **{tag: set() for tag in ("title", "desc", "defs", "style")},
    "marker": {"markerWidth", "markerHeight", "refX", "refY", "orient"},
    "path": {
        "d",
        "fill",
        "fill-rule",
        "clip-rule",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
        "marker-end",
    },
    "rect": {
        "x",
        "y",
        "width",
        "height",
        "rx",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-dasharray",
    },
    "text": {"x", "y"},
}


def relative_url(target, parent):
    return quote(Path(os.path.relpath(target, parent)).as_posix(), safe="/.-_")


class RebaseLinks(HTMLParser):
    """Preserve Markdown HTML while relocating its relative href/src values."""

    def __init__(self, root, source, output, mapping):
        super().__init__(convert_charrefs=False)
        self.root = root
        self.source = source
        self.output = output
        self.mapping = mapping
        self.parts = []

    def rebase(self, value):
        url = urlsplit(value)
        if url.scheme or url.netloc or not url.path:
            return value
        target = (self.root / self.source.parent / unquote(url.path)).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"{self.source.as_posix()}: link leaves the repository")
        target = self.root / self.mapping.get(target.relative_to(self.root).as_posix(), target)
        path = relative_url(target, self.root / self.output.parent)
        return urlunsplit(("", "", path, url.query, url.fragment))

    def start(self, tag, attrs, close):
        names = [name for name, _ in attrs]
        if tag not in MARKDOWN_ATTRIBUTES or len(set(names)) != len(names):
            raise ValueError(f"{self.source.as_posix()}: unsupported document HTML tag: {tag}")
        for name, value in attrs:
            if name not in MARKDOWN_ATTRIBUTES[tag]:
                raise ValueError(
                    f"{self.source.as_posix()}: unsupported document HTML attribute: {tag}.{name}"
                )
            if (name == "align" and value not in {"left", "center", "right"}) or (
                name == "class"
                and not (
                    (tag == "div" and value == "toc")
                    or (tag == "code" and re.fullmatch(r"language-[\w+-]+", value or ""))
                )
            ):
                raise ValueError(
                    f"{self.source.as_posix()}: unsupported document HTML attribute value"
                )
        rendered = []
        for name, value in attrs:
            if value is not None and name in {"href", "src"}:
                value = self.rebase(value)
            rendered.append(name if value is None else f'{name}="{html.escape(value, quote=True)}"')
        attributes = " " + " ".join(rendered) if rendered else ""
        self.parts.append(f"<{tag}{attributes}{close}>")

    def handle_starttag(self, tag, attrs):
        self.start(tag, attrs, "")

    def handle_startendtag(self, tag, attrs):
        self.start(tag, attrs, " /")

    def handle_endtag(self, tag):
        if tag not in MARKDOWN_ATTRIBUTES:
            raise ValueError(f"{self.source.as_posix()}: unsupported document HTML tag: {tag}")
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def handle_comment(self, data):
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        raise ValueError(f"{self.source.as_posix()}: unsupported document HTML declaration")

    handle_pi = handle_decl
    unknown_decl = handle_decl


class PageLinks(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.ids = set()
        self.references = []
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.add(attributes["id"])
        if tag == "a" and attributes.get("name"):
            self.ids.add(attributes["name"])
        for name, value in attrs:
            if name in {"href", "xlink:href", "src"} and value:
                self.references.append((tag, name, value))

    handle_startendtag = handle_starttag


def local_target(root, page, path):
    if path.startswith("/") or "\\" in path:
        raise ValueError(f"{page.as_posix()}: local links must use relative URL paths")
    target = (root / page.parent / unquote(path)).resolve() if path else root / page
    if not target.is_relative_to(root):
        raise ValueError(f"{page.as_posix()}: link leaves the repository")
    return target.relative_to(root)


def exact_file(root, name):
    """Check filename case on Windows too, so offline checks match Linux CI."""
    current = root
    for part in name.parts:
        if not current.is_dir() or part not in {entry.name for entry in current.iterdir()}:
            return False
        current /= part
    return current.is_file()


def css_references(text, source):
    """Accept only the literal static CSS forms used by these offline pages."""
    if "\\" in text:
        raise ValueError(f"{source.as_posix()}: unsupported CSS escape")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    for rule in re.findall(r"@([\w-]+)", text):
        if rule.lower() == "import":
            raise ValueError(f"{source.as_posix()}: CSS @import is unsupported")
        if rule.lower() != "media":
            raise ValueError(f"{source.as_posix()}: unsupported CSS at-rule")
    functions = {name.lower() for name in re.findall(r"([a-zA-Z_-][\w-]*)\(", text)}
    if functions - {"url", "var", "clamp", "minmax", "not", "translatey"}:
        raise ValueError(f"{source.as_posix()}: unsupported CSS function")
    urls = list(
        re.finditer(
            r"""url\(\s*(?:"([^"()\r\n]*)"|'([^'()\r\n]*)'|([^\s'"()]*))\s*\)""", text, re.I
        )
    )
    if len(urls) != len(re.findall(r"url\(", text, re.IGNORECASE)):
        raise ValueError(f"{source.as_posix()}: unsupported CSS URL form")
    return [
        ("asset", "src", next(value for value in match.groups() if value is not None))
        for match in urls
    ]


def svg_document(text, source):
    """Validate the small static SVG shape/text subset used by the diagrams."""
    if "<!DOCTYPE" in text.upper() or "<?" in text:
        raise ValueError(f"{source.as_posix()}: unsupported SVG declaration")
    try:
        tree = ET.fromstring(text)
    except ET.ParseError as error:
        raise ValueError(f"{source.as_posix()}: invalid SVG XML") from error
    namespace = "{http://www.w3.org/2000/svg}"
    if tree.tag != namespace + "svg":
        raise ValueError(f"{source.as_posix()}: unsupported SVG root")
    document = PageLinks("")
    for element in tree.iter():
        tag = element.tag.removeprefix(namespace)
        if tag not in SVG_ATTRIBUTES or not element.tag.startswith(namespace):
            raise ValueError(f"{source.as_posix()}: unsupported SVG element")
        if set(element.attrib) - SVG_ATTRIBUTES[tag] - {"id", "class"}:
            raise ValueError(f"{source.as_posix()}: unsupported SVG attribute")
        for name in ("fill-rule", "clip-rule"):
            if name in element.attrib and element.get(name) not in {"evenodd", "nonzero"}:
                raise ValueError(f"{source.as_posix()}: unsupported SVG fill rule")
        if element.get("id"):
            document.ids.add(element.get("id"))
        for value in element.attrib.values():
            document.references.extend(css_references(value, source))
        if tag == "style":
            document.references.extend(css_references("".join(element.itertext()), source))
    return document


def validate_pages(root, pages):
    parsed = {name: PageLinks(data.decode("utf-8")) for name, data in pages.items()}
    documents = list(parsed.items())
    for page, document in documents:
        for tag, attribute, value in document.references:
            url = urlsplit(value)
            if url.scheme or url.netloc:
                if tag == "a" and attribute == "href" and url.scheme in {"https", "http", "mailto"}:
                    continue
                raise ValueError(f"{page.as_posix()}: external asset or unsupported URL")
            target = local_target(root, page, url.path)
            if target not in pages and not exact_file(root, target):
                raise ValueError(f"{page.as_posix()}: missing local target: {target.as_posix()}")
            if target not in parsed and target.suffix.lower() in {".css", ".svg"}:
                text = (root / target).read_text(encoding="utf-8")
                if target.suffix.lower() == ".css":
                    asset = PageLinks("")
                    asset.references = css_references(text, target)
                else:
                    asset = svg_document(text, target)
                parsed[target] = asset
                documents.append((target, asset))
            if url.fragment:
                if target not in parsed:
                    text = (root / target).read_text(encoding="utf-8")
                    if target.suffix.lower() == ".md":
                        text = markdown.markdown(text, extensions=["fenced_code", "tables", "toc"])
                    parsed_target = PageLinks(text)
                else:
                    parsed_target = parsed[target]
                if unquote(url.fragment) not in parsed_target.ids:
                    raise ValueError(
                        f"{page.as_posix()}: missing fragment: {target.as_posix()}#{url.fragment}"
                    )


def render_page(root, source, output, mapping, version):
    source_bytes = (root / source).read_bytes()
    converter = markdown.Markdown(
        extensions=["fenced_code", "tables", "toc"],
        extension_configs={
            "toc": {"marker": "", "toc_depth": "2-3"},
            "tables": {"use_align_attribute": True},
        },
        output_format="html",
    )
    body = converter.convert(source_bytes.decode("utf-8"))
    links = RebaseLinks(root, source, output, mapping)
    links.feed(body)
    links.close()
    body = "".join(links.parts)
    heading = re.match(r"(<h1\b[^>]*>.*?</h1>)\s*(<p>.*?</p>)?", body, re.DOTALL)
    if heading is None:
        raise ValueError(f"{source.as_posix()}: begin the page with a level-one heading")
    title = html.unescape(re.sub(r"<[^>]+>", "", heading.group(1)))
    lead = (heading.group(2) or "").replace("<p>", '<p class="lead">', 1)
    content = body[heading.end() :].lstrip()
    navigation = [
        ("Home", "docs/index.html"),
        ("Architecture", "docs/architecture.html"),
        ("Recipes", "docs/recipes/index.html"),
        ("Contributing", "docs/contributing.html"),
    ]
    if "docs/mcp.md" in mapping:
        navigation.append(("MCP", mapping["docs/mcp.md"]))
    if "docs/terac-access.md" in mapping:
        navigation.append(("Terac access", mapping["docs/terac-access.md"]))
    nav = "\n".join(
        f'        <a href="{relative_url(root / target, root / output.parent)}"'
        + (' aria-current="page"' if target == output.as_posix() else "")
        + f">{label}</a>"
        for label, target in navigation
    )
    home = relative_url(root / "docs/index.html", root / output.parent)
    stylesheet = relative_url(root / "docs/assets/site.css", root / output.parent)
    brand_mark = relative_url(root / "docs/assets/terac-icon.svg", root / output.parent)
    source_url = relative_url(root / source, root / output.parent)
    toc = converter.toc.replace('<div class="toc">', "<div>", 1).strip()
    digest = hashlib.sha256(source_bytes).hexdigest()
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} | Terac Cookbook</title>
  <link rel="stylesheet" href="{stylesheet}">
</head>
<body>
<!-- source: {source.as_posix()}; source-sha256: {digest} -->
<a class="skip-link" href="#main">Skip to content</a>
<header class="site-header">
  <div class="header-inner">
    <a class="brand" href="{home}">
      <img class="brand-mark" src="{brand_mark}" width="24" height="24" alt="">Terac Cookbook
    </a>
    <nav class="site-nav" aria-label="Main navigation">
{nav}
    </nav>
  </div>
</header>
<main id="main" class="page-shell">
  <div class="page-layout">
    <article>
      <header class="page-header">
        <p class="eyebrow">Cookbook {html.escape(version)}</p>
        {heading.group(1)}
        {lead}
      </header>
      <div class="content">
{content}
      </div>
    </article>
    <nav class="toc" aria-label="On this page">
      <p class="toc-title">On this page</p>
{toc}
    </nav>
  </div>
</main>
<footer class="site-footer">
  <p>Terac Cookbook {html.escape(version)} · Local, offline reading view.</p>
  <p class="source-note">Markdown is canonical. <a href="{source_url}">Read the source</a>.</p>
</footer>
</body>
</html>
'''.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="verify outputs without writing files")
    arguments = parser.parse_args()
    root = arguments.root.resolve()
    try:
        mapping = dict(PAGES)
        for source, output in OPTIONAL_PAGES.items():
            if (root / source).is_file():
                mapping[source] = output
        for name in [
            *mapping,
            "pyproject.toml",
            "docs/assets/site.css",
            "docs/assets/terac-icon.svg",
        ]:
            if not (root / name).is_file():
                raise ValueError(f"missing required source or asset: {name}")
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        pages = {
            Path(output): render_page(root, Path(source), Path(output), mapping, version)
            for source, output in mapping.items()
        }
        validate_pages(root, pages)
        if arguments.check:
            problems = []
            for output, data in pages.items():
                if not (root / output).is_file():
                    problems.append(f"missing output: {output.as_posix()}")
                elif (root / output).read_bytes() != data:
                    problems.append(f"out of date: {output.as_posix()}")
            if problems:
                raise ValueError("; ".join(problems))
            print(f"Checked {len(pages)} offline documentation pages; no drift.")
            return 0
        for output, data in pages.items():
            (root / output).write_bytes(data)
    except (OSError, ValueError, KeyError) as error:
        print(f"Documentation build failed: {error}")
        return 1
    print(f"Rendered {len(pages)} offline documentation pages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
