"""Static guard: no synchronous repo/cache call may run on the event loop.

`repo` (psycopg) and `cache` (file I/O) are fully synchronous. Calling them
from an `async def` blocks the whole loop, so every such call was moved behind
`asyncio.to_thread`. Nothing in the type system or the test suite notices when
one creeps back — the handler still returns the right answer, it just freezes
every other request while it does. Hence this check.

What it flags: a `Call` to one of those modules evaluated in the body of an
`async def`. Both import styles are resolved — `repo.list_feeds()` by its
attribute prefix, and `load_feed_cache()` by following `from cache import ...`
per module. Passing the function *reference* to `to_thread` is not a Call, so
`asyncio.to_thread(repo.list_feeds)` passes while the eager
`asyncio.to_thread(f, repo.list_feeds())` — which runs in the loop before
to_thread is ever entered — is flagged.

Known limit: blocking reached indirectly through a synchronous helper is
invisible here (`_get_feed_or_404` is the typical one). The dynamic checks in
test_handler_responsiveness.py do not close that gap either — they pin two
specific paths, not the property in general. Adding a helper that hides a repo
call from an async handler is still an unguarded regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent

# Modules that are synchronous end to end.
_BLOCKING_MODULES = {"repo", "cache"}

# Deliberate exceptions. Each one needs a reason, not just a name.
_ALLOWED = {
  # Pure function over two strings: normalizes a filter into its dedupe key.
  # Touches no database.
  "repo.filter_key",
  # Only assigns the module-level cache paths, once at startup. No file I/O.
  "cache.configure",
  # A context manager holding a session-scoped Postgres advisory lock, taken
  # once per refresh or background summarizer round (not per request). It stays synchronous because splitting
  # acquire and release into two to_thread calls opens a window where a
  # cancellation between them leaks both the connection and the lock. See
  # plans/done/plan-2026-09-19-async-blocking-io.md for the full rationale.
  "repo.advisory_lock",
}


def _imported_names(tree: ast.Module) -> dict[str, str]:
  """Local name -> qualified name, for `from repo import x` / `from cache import y`.

  feeds.py reaches the cache this way, so matching only the `cache.` prefix
  would leave that whole module unguarded.
  """
  names: dict[str, str] = {}
  for node in ast.walk(tree):
    if isinstance(node, ast.ImportFrom) and node.module in _BLOCKING_MODULES:
      for alias in node.names:
        names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
  return names


def _own_body(fn: ast.AsyncFunctionDef) -> list[ast.AST]:
  """Nodes evaluated when `fn` runs, excluding the bodies of nested functions.

  A nested def's *body* runs in whatever context calls it, so it is not this
  function's concern — and ast.walk() over the module reaches every nested
  `async def` on its own anyway. Its decorators and default arguments, though,
  are evaluated right here at definition time, so those stay in scope.

  `fn`'s own decorators are excluded for the same reason in reverse: they run
  where the function is defined, not when it is awaited.
  """
  own: list[ast.AST] = []
  stack: list[ast.AST] = [*fn.body]
  while stack:
    node = stack.pop()
    own.append(node)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
      stack.extend(node.decorator_list)
      stack.extend(d for d in [*node.args.defaults, *node.args.kw_defaults] if d is not None)
      continue
    stack.extend(ast.iter_child_nodes(node))
  return own


def _called_name(node: ast.Call, imported: dict[str, str]) -> str | None:
  func = node.func
  if (
    isinstance(func, ast.Attribute)
    and isinstance(func.value, ast.Name)
    and func.value.id in _BLOCKING_MODULES
  ):
    return f"{func.value.id}.{func.attr}"
  if isinstance(func, ast.Name):
    return imported.get(func.id)
  return None


def _blocking_calls(source: str, filename: str = "<test>") -> list[str]:
  """Every offending call, as a human-readable `file:line async_fn -> call`."""
  tree = ast.parse(source)
  imported = _imported_names(tree)
  found: list[str] = []
  for fn in ast.walk(tree):
    if not isinstance(fn, ast.AsyncFunctionDef):
      continue
    for node in _own_body(fn):
      if not isinstance(node, ast.Call):
        continue
      name = _called_name(node, imported)
      if name is None or name in _ALLOWED:
        continue
      found.append(f"{filename}:{node.lineno} {fn.name} -> {name}()")
  return found


def _backend_sources() -> list[Path]:
  # Every module, so a new one is covered the day it is added.
  return sorted(p for p in _BACKEND.glob("*.py") if p.name != "__init__.py")


class TestNoBlockingCallsOnTheEventLoop:
  def test_backend_modules_are_clean(self):
    offenders: list[str] = []
    for path in _backend_sources():
      offenders += _blocking_calls(path.read_text(encoding="utf-8"), path.name)
    assert offenders == [], "wrap these in asyncio.to_thread:\n" + "\n".join(offenders)

  def test_the_scan_actually_reaches_the_handlers(self):
    # A silently empty file list would make the check above pass forever.
    names = {p.name for p in _backend_sources()}
    assert {"app.py", "fetcher.py", "feeds.py"} <= names


class TestTheDetectorItself:
  """The check is only worth having if it still fires. Pin what it sees."""

  def test_a_bare_call_in_an_async_def_is_flagged(self):
    src = "async def h():\n  return repo.list_feeds()\n"
    assert len(_blocking_calls(src)) == 1

  def test_a_function_reference_passed_to_to_thread_is_not_flagged(self):
    src = "async def h():\n  return await asyncio.to_thread(repo.list_feeds)\n"
    assert _blocking_calls(src) == []

  def test_a_call_in_an_argument_of_to_thread_is_flagged(self):
    # Evaluated in the loop before to_thread even starts — awaiting it is no
    # protection at all, which is exactly why `await` cannot be trusted as a
    # signal here.
    src = "async def h():\n  return await asyncio.to_thread(f, repo.list_feeds())\n"
    assert len(_blocking_calls(src)) == 1

  def test_a_directly_imported_name_is_flagged(self):
    # The feeds.py import style. Without alias resolution this reads as an
    # ordinary local call and slips through.
    src = "from cache import load_feed_cache\nasync def h():\n  return load_feed_cache(url)\n"
    assert len(_blocking_calls(src)) == 1

  def test_a_directly_imported_name_behind_to_thread_is_not_flagged(self):
    src = (
      "from cache import load_feed_cache\n"
      "async def h():\n  return await asyncio.to_thread(load_feed_cache, url)\n"
    )
    assert _blocking_calls(src) == []

  def test_an_aliased_import_is_followed(self):
    src = "from cache import load_feed_cache as lfc\nasync def h():\n  return lfc(url)\n"
    assert len(_blocking_calls(src)) == 1

  def test_a_call_in_a_plain_def_is_not_flagged(self):
    # FastAPI already runs sync handlers in a threadpool.
    src = "def h():\n  return repo.list_feeds()\n"
    assert _blocking_calls(src) == []

  def test_a_nested_def_body_is_not_attributed_to_its_async_parent(self):
    src = "async def h():\n  def inner():\n    return repo.list_feeds()\n  return inner\n"
    assert _blocking_calls(src) == []

  def test_a_nested_def_default_argument_is_flagged(self):
    # Unlike the body, defaults are evaluated where the def statement runs.
    src = "async def h():\n  def inner(x=repo.list_feeds()):\n    return x\n  return inner\n"
    assert len(_blocking_calls(src)) == 1

  def test_the_decorators_of_the_async_def_itself_are_not_flagged(self):
    # They run at import time, not when the coroutine is awaited.
    src = "@wrap(repo.list_feeds())\nasync def h():\n  return 1\n"
    assert _blocking_calls(src) == []

  def test_a_nested_async_def_is_flagged_on_its_own(self):
    src = "async def h():\n  async def inner():\n    return repo.list_feeds()\n  return inner\n"
    assert len(_blocking_calls(src)) == 1

  def test_a_call_inside_a_comprehension_is_flagged(self):
    src = "async def h():\n  return [repo.get_feed_by_id(i) for i in ids]\n"
    assert len(_blocking_calls(src)) == 1

  def test_an_allowed_call_is_not_flagged(self):
    src = "async def h():\n  with repo.advisory_lock() as ok:\n    return ok\n"
    assert _blocking_calls(src) == []

  def test_every_allowed_name_still_exists(self):
    # A stale entry would silently widen the exception list.
    import importlib

    for name in _ALLOWED:
      module_name, attr = name.split(".")
      assert module_name in _BLOCKING_MODULES
      module = importlib.import_module(module_name)
      assert hasattr(module, attr), f"{name} is in the allowlist but no longer exists"
