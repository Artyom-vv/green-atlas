"""Checks for portability failures in documentation, not product behavior."""

import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from check_vault import check
from package_vault import package


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ga-docs-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def page(self, text):
        (self.vault / "README.md").write_text(
            "---\ntitle: Test\n---\n\n# Test\n\n" + text, encoding="utf-8"
        )

    def test_missing_image_rejected(self):
        self.page("![diagram](absent.svg)")
        with self.assertRaisesRegex(ValueError, "missing"):
            check(self.vault)

    def test_reference_outside_vault_rejected(self):
        (self.root / "outside.md").write_text("outside", encoding="utf-8")
        self.page("[outside](../outside.md)")
        with self.assertRaisesRegex(ValueError, "nonportable"):
            check(self.vault)

    def test_machine_specific_url_rejected(self):
        self.page("Project at http://127.0.0.1:5177/project")
        with self.assertRaisesRegex(ValueError, "session-specific"):
            check(self.vault)

    def test_svg_external_image_rejected(self):
        self.page("![diagram](diagram.svg)")
        (self.vault / "diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg">'
            '<image href="https://example.com/diagram.png"/></svg>', encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "external SVG"):
            check(self.vault)

    def test_unclosed_fence_rejected(self):
        self.page("```mermaid\ngraph LR\nA --> B")
        with self.assertRaisesRegex(ValueError, "unclosed"):
            check(self.vault)

    def test_unicode_links_assets_and_archive(self):
        self.page("[[Раздел/Страница|Описание]]\n![Схема](diagram.svg)")
        (self.vault / "Раздел").mkdir()
        (self.vault / "Раздел/Страница.md").write_text(
            "---\ntitle: Page\n---\n\n# Page\n[[../README]]", encoding="utf-8"
        )
        (self.vault / "diagram.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg"><title>Схема</title></svg>',
            encoding="utf-8"
        )
        archive_path = self.root / "portable.zip"
        result = package(self.vault, archive_path)
        self.assertEqual(result, {"pages": 2, "svg_assets": 1, "internal_links": 3})
        with ZipFile(archive_path) as archive:
            self.assertIn("obsidian/Раздел/Страница.md", archive.namelist())
            self.assertIn("obsidian/diagram.svg", archive.namelist())
            self.assertIn("obsidian/MANIFEST.sha256", archive.namelist())


if __name__ == "__main__":
    unittest.main()
