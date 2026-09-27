"""Validate the portable Markdown/SVG documentation without repository context."""

from __future__ import annotations

import argparse
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, urlsplit

WIKI_LINK = re.compile(r"!?\[\[([^\]]+)\]\]")
MARKDOWN_LINK = re.compile(r"!?\[[^\]\n]*\]\(<?([^\n)>]+)>?\)")
FENCE = re.compile(r"^```.*?^```\s*$", re.MULTILINE | re.DOTALL)
SESSION_REFERENCE = re.compile(
    r"/Users/|/private/tmp/|127\.0\.0\.1|localhost:\d+|"
    r"\b[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\b"
)


def check(vault: Path) -> dict[str, int]:
    vault = vault.resolve()
    pages = sorted(vault.rglob("*.md"))
    assets = sorted(vault.rglob("*.svg"))
    errors: list[str] = []
    checked_links = 0

    def resolve(page: Path, target: str, wiki: bool) -> None:
        nonlocal checked_links
        href = target.split("|", 1)[0] if wiki else target
        href = unquote(href.strip()).split("#", 1)[0]
        if not href:
            return
        if urlsplit(href).scheme:
            errors.append(f"{page.relative_to(vault)}: external dependency {href}")
            return
        checked_links += 1
        candidates = [page.parent / href]
        if wiki:
            candidates = [vault / href, page.parent / href]
        if wiki and not Path(href).suffix:
            candidates = [Path(str(p) + ".md") for p in candidates]
        candidates = [p.resolve() for p in candidates]
        if not any(p.is_relative_to(vault) and p.is_file() for p in candidates):
            errors.append(f"{page.relative_to(vault)}: missing or nonportable link {href}")

    if not (vault / "README.md").is_file():
        errors.append("README.md missing")
    for page in pages:
        content = page.read_text(encoding="utf-8")
        if not content.startswith("---\n"):
            errors.append(f"{page.relative_to(vault)}: metadata missing")
        if len(re.findall(r"^```", content, re.MULTILINE)) % 2:
            errors.append(f"{page.relative_to(vault)}: unclosed code fence")
        if SESSION_REFERENCE.search(content):
            errors.append(f"{page.relative_to(vault)}: machine/session-specific reference")
        content = FENCE.sub("", content)
        for match in WIKI_LINK.finditer(content):
            resolve(page, match.group(1), True)
        for match in MARKDOWN_LINK.finditer(content):
            resolve(page, match.group(1), False)

    for asset in assets:
        tree = ET.parse(asset)
        if not tree.getroot().tag.endswith("svg"):
            errors.append(f"{asset.relative_to(vault)}: not an SVG")
        for element in tree.iter():
            if element.tag.endswith("script"):
                errors.append(f"{asset.relative_to(vault)}: script dependency")
            for key, value in element.attrib.items():
                if key.endswith("href") and not value.startswith("#"):
                    errors.append(f"{asset.relative_to(vault)}: external SVG resource")
    if errors:
        raise ValueError("\n".join(errors))
    return {"pages": len(pages), "svg_assets": len(assets), "internal_links": checked_links}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, nargs="?", default=Path("docs/obsidian"))
    args = parser.parse_args()
    print(check(args.vault))
