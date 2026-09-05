from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "docs"


class Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.links: list[str] = []
        self.images: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"] or "")
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "link" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "img":
            self.images.append(values)


class SiteTests(unittest.TestCase):
    def test_page_links_assets_and_landmarks(self) -> None:
        source = (SITE / "index.html").read_text(encoding="utf-8")
        page = Page()
        page.feed(source)
        self.assertIn('<html lang="en">', source)
        self.assertIn('<meta name="viewport"', source)
        self.assertIn('<main id="main">', source)
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertTrue(page.images)
        self.assertTrue(all(image.get("src") and image.get("alt") for image in page.images))
        for link in page.links:
            parsed = urlparse(link)
            if link.startswith("#"):
                self.assertIn(link[1:], page.ids, link)
            elif not parsed.scheme:
                self.assertTrue((SITE / parsed.path).is_file(), link)

    def test_pages_workflow_publishes_docs(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        for required in (
            "actions/checkout@v6", "actions/configure-pages@v5",
            "actions/upload-pages-artifact@v5", "actions/deploy-pages@v5", "path: docs",
        ):
            self.assertIn(required, workflow)

    def test_motion_has_an_accessible_fallback(self) -> None:
        styles = (SITE / "styles.css").read_text(encoding="utf-8")
        for animation in ("cursor-blink", "logo-drift", "prompt-nudge", "enter", "signal"):
            self.assertIn(f"@keyframes {animation}", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("animation: none !important", styles)


if __name__ == "__main__":
    unittest.main()
