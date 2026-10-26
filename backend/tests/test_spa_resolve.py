"""app._resolve_spa_file maps a request path to a file inside frontend/dist.

Called directly rather than over HTTP: httpx normalizes "../" out of the URL
before it is sent, so a request could never reach the boundary check. The
targets outside dist exist on disk, so only the boundary check (not is_file())
can reject them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app as app_module


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
  dist_dir: Path = tmp_path / "dist"
  (dist_dir / "assets").mkdir(parents=True)
  (dist_dir / "index.html").write_text("<html></html>")
  (dist_dir / "assets" / "app.js").write_text("")
  (tmp_path / "secret.txt").write_text("secret")
  # A sibling whose name starts with "dist": a string-prefix check lets it through.
  (tmp_path / "dist-old").mkdir()
  (tmp_path / "dist-old" / "x").write_text("old")
  monkeypatch.setattr(app_module, "_frontend_dist", dist_dir)
  monkeypatch.setattr(app_module, "_frontend_root", dist_dir.resolve())
  return dist_dir


@pytest.mark.parametrize("path", ["index.html", "assets/app.js"])
def test_files_inside_dist_resolve(dist: Path, path: str) -> None:
  assert app_module._resolve_spa_file(path) == (dist / path).resolve()


@pytest.mark.parametrize(
  "path",
  ["../secret.txt", "../dist-old/x", "assets/../../secret.txt", "assets", "missing.js"],
  ids=["parent", "sibling-prefix", "nested-escape", "directory", "missing"],
)
def test_paths_outside_dist_or_not_files_are_rejected(dist: Path, path: str) -> None:
  assert app_module._resolve_spa_file(path) is None
