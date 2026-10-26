"""Tests for backend/log.py — level labels when color is off."""

from __future__ import annotations

from collections.abc import Callable

import pytest

import log


@pytest.mark.parametrize(
  ("emit", "expected"),
  [
    (log.warn, "WARNING: disk low\n"),
    (log.error, "ERROR: disk low\n"),
    (log.info, "disk low\n"),
    (log.success, "disk low\n"),
  ],
)
def test_without_color_only_warnings_and_errors_are_labeled(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  emit: Callable[[str], None],
  expected: str,
):
  monkeypatch.setattr(log, "_NO_COLOR", True)

  emit("disk low")

  assert capsys.readouterr().out == expected


@pytest.mark.parametrize("emit", [log.warn, log.error])
def test_with_color_the_color_stands_in_for_the_label(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  emit: Callable[[str], None],
):
  monkeypatch.setattr(log, "_NO_COLOR", False)

  emit("disk low")

  out = capsys.readouterr().out
  assert out.startswith("\033[")
  assert "disk low" in out
  assert "WARNING" not in out
  assert "ERROR" not in out


@pytest.mark.parametrize("no_color", [True, False])
def test_info_is_printed_as_is(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  no_color: bool,
):
  monkeypatch.setattr(log, "_NO_COLOR", no_color)

  log.info("disk low")

  assert capsys.readouterr().out == "disk low\n"
