"""Границы нового полного Mac builder до создания файлов и запуска компиляторов."""

from pathlib import Path

import pytest

from scripts.desktop import build_macos_product as builder


def test_output_rejects_traversal_outside_or_through_allowed_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder, "REPOSITORY", tmp_path)
    for target in (tmp_path / "output/../../Library/live", tmp_path / "output/../output/ambiguous", tmp_path / "Library/live"):
        with pytest.raises(ValueError):
            builder.new_output(target)
    assert not (tmp_path / "Library").exists()


def test_output_preserves_existing_directory_and_refuses_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder, "REPOSITORY", tmp_path)
    existing = tmp_path / "output/existing"
    existing.mkdir(parents=True)
    preserved = existing / "user-data.txt"
    preserved.write_bytes(b"keep")
    with pytest.raises(ValueError):
        builder.new_output(existing)
    link = tmp_path / "output/redirect"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        builder.new_output(link / "new")
    assert preserved.read_bytes() == b"keep"


def test_public_web_resource_must_match_source_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(builder, "REPOSITORY", tmp_path)
    public = tmp_path / "apps/web/public/assets"
    public.mkdir(parents=True)
    (public / "font.ttf").write_bytes(b"owned-asset")
    web = tmp_path / "built-web"
    (web / "assets").mkdir(parents=True)
    target = web / "assets/font.ttf"
    target.write_bytes(b"substituted")
    with pytest.raises(ValueError, match="Ресурс Web"):
        builder.public_assets(web)
    target.write_bytes(b"owned-asset")
    assert builder.public_assets(web)["passed"] is True
