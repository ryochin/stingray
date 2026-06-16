"""Guards for the app.py config wiring: config.yml is read once (at startup),
and the Request-injection refactor must not leak Request into the public API.

These are static/schema checks. They import the app module and inspect its
OpenAPI spec and AST only — no lifespan, no DB, no network probe.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import app


class TestOpenApiSchema:
  def _spec(self) -> dict[str, Any]:
    return app.app.openapi()

  def test_create_feed_keeps_request_body(self):
    # Adding `request: Request` must not drop the FeedCreate request body.
    post = self._spec()["paths"]["/api/feeds"]["post"]
    assert "requestBody" in post

  def test_status_has_no_query_params(self):
    # `request: Request` must not surface as a query parameter.
    get = self._spec()["paths"]["/api/status"]["get"]
    params = get.get("parameters", [])
    assert [p for p in params if p["in"] == "query"] == []

  def test_fetch_feed_exposes_only_feed_id_path_param(self):
    post = self._spec()["paths"]["/api/feeds/{feed_id}/fetch"]["post"]
    params = post.get("parameters", [])
    assert [(p["name"], p["in"]) for p in params] == [("feed_id", "path")]


class TestConfigLoadedOnce:
  """The whole point of the refactor: config.yml is read once, in lifespan,
  stashed on app.state.config, and never re-read per-request."""

  def _tree(self) -> ast.Module:
    return ast.parse(Path(app.__file__).read_text(encoding="utf-8"))

  def _appconfig_load_calls(self, node: ast.AST) -> list[ast.Call]:
    return [
      n
      for n in ast.walk(node)
      if isinstance(n, ast.Call)
      and isinstance(n.func, ast.Attribute)
      and n.func.attr == "load"
      and isinstance(n.func.value, ast.Name)
      and n.func.value.id == "AppConfig"
    ]

  def _lifespan(self) -> ast.AsyncFunctionDef:
    fn = next(
      (n for n in ast.walk(self._tree()) if isinstance(n, ast.AsyncFunctionDef) and n.name == "lifespan"),
      None,
    )
    assert fn is not None, "lifespan function not found in app.py"
    return fn

  def test_appconfig_load_called_once_module_wide(self):
    # Guard against a stray AppConfig.load() creeping back into a handler.
    calls = self._appconfig_load_calls(self._tree())
    assert len(calls) == 1, f"AppConfig.load() must be called once; found {len(calls)}"

  def test_the_single_load_is_inside_lifespan(self):
    # The one call must live in lifespan, not anywhere else.
    assert len(self._appconfig_load_calls(self._lifespan())) == 1

  def test_lifespan_assigns_app_state_config(self):
    # lifespan must stash the loaded config on app.state.config for handlers.
    found = False
    for n in ast.walk(self._lifespan()):
      if isinstance(n, ast.Assign):
        for t in n.targets:
          if (
            isinstance(t, ast.Attribute)
            and t.attr == "config"
            and isinstance(t.value, ast.Attribute)
            and t.value.attr == "state"
          ):
            found = True
    assert found, "lifespan must assign app.state.config"
