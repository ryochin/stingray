"""Tests for the shared config loader (AppConfig.load) and main.load_config.

AppConfig.load is the single source of truth for reading config.yml across the
web app, the fetcher, and the healthcheck. main.load_config wraps it with the
fetcher's strict "missing/broken config is fatal" policy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import main
from schemas import AppConfig


class TestAppConfigLoad:
  def test_missing_required_raises(self, tmp_path: Path):
    with pytest.raises(FileNotFoundError):
      AppConfig.load(tmp_path / "nope.yml", required=True)

  def test_missing_optional_returns_defaults(self, tmp_path: Path):
    config = AppConfig.load(tmp_path / "nope.yml")
    assert config.ollama.enabled is True

  def test_non_mapping_yaml_raises(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid YAML mapping"):
      AppConfig.load(path)

  def test_empty_yaml_raises(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid YAML mapping"):
      AppConfig.load(path)

  def test_syntax_error_raises_value_error(self, tmp_path: Path):
    # A YAML syntax error is normalized to ValueError so callers see one
    # "invalid config" failure mode rather than a raw yaml.YAMLError.
    path = tmp_path / "config.yml"
    path.write_text("ollama: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid YAML"):
      AppConfig.load(path)

  def test_field_type_error_raises_validation_error(self, tmp_path: Path):
    # A mapping with a wrong field type fails model_validate. ValidationError
    # is a ValueError subclass, so it flows through the same handling.
    path = tmp_path / "config.yml"
    path.write_text("max_items_per_feed: not-an-int\n", encoding="utf-8")
    with pytest.raises(ValidationError):
      AppConfig.load(path)

  def test_reads_ollama_enabled_false(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("ollama:\n  enabled: false\n", encoding="utf-8")
    assert AppConfig.load(path).ollama.enabled is False


class TestAppConfigLoadCacheDir:
  """A relative cache_dir resolves against the config file, not the cwd, so the
  web app and the fetcher agree on it whatever directory they start in."""

  @pytest.fixture(autouse=True)
  def _cwd_elsewhere(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere: Path = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

  def test_relative_resolves_against_config_dir(self, tmp_path: Path):
    conf_dir: Path = tmp_path / "conf"
    conf_dir.mkdir()
    path: Path = conf_dir / "config.yml"
    path.write_text('cache_dir: "data/cache"\n', encoding="utf-8")
    assert AppConfig.load(path).cache_dir == str(conf_dir.resolve() / "data" / "cache")

  def test_absolute_is_kept(self, tmp_path: Path):
    target: Path = tmp_path / "abs-cache"
    path: Path = tmp_path / "config.yml"
    path.write_text(f'cache_dir: "{target}"\n', encoding="utf-8")
    assert AppConfig.load(path).cache_dir == str(target)

  def test_missing_file_resolves_default_against_its_dir(self, tmp_path: Path):
    path: Path = tmp_path / "nope.yml"
    assert AppConfig.load(path).cache_dir == str(tmp_path.resolve() / "cache")

  def test_default_path_relative_to_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Override with a non-default value so this actually proves the file is read.
    (tmp_path / "config.yml").write_text("native_lang: ja\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert AppConfig.load().native_lang == "ja"

  def test_native_lang_defaults_to_en(self, tmp_path: Path):
    config = AppConfig.load(tmp_path / "nope.yml")
    assert config.native_lang == "en"

  def test_article_order_defaults_to_oldest(self, tmp_path: Path):
    config = AppConfig.load(tmp_path / "nope.yml")
    assert config.article_order == "oldest"

  def test_reads_article_order_newest(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text('article_order: "newest"\n', encoding="utf-8")
    assert AppConfig.load(path).article_order == "newest"

  def test_invalid_article_order_raises(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text('article_order: "sideways"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
      AppConfig.load(path)


class TestMainLoadConfig:
  def test_valid_config_loaded(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("max_items_per_feed: 7\n", encoding="utf-8")
    config = main.load_config(path)
    assert config.max_items_per_feed == 7

  def test_missing_config_exits(self, tmp_path: Path):
    # The fetcher cannot run without a config: a missing file is fatal.
    with pytest.raises(SystemExit):
      main.load_config(tmp_path / "nope.yml")

  def test_broken_config_exits(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("just a string\n", encoding="utf-8")
    with pytest.raises(SystemExit):
      main.load_config(path)

  def test_syntax_error_exits_with_path(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    # A YAML syntax error must also be a clean exit(1), not a traceback, and
    # the message should name the offending file.
    path = tmp_path / "config.yml"
    path.write_text("ollama: [\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
      main.load_config(path)
    assert exc.value.code == 1
    assert str(path) in capsys.readouterr().out

  def test_field_type_error_exits(self, tmp_path: Path):
    path = tmp_path / "config.yml"
    path.write_text("max_items_per_feed: not-an-int\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
      main.load_config(path)
    assert exc.value.code == 1
