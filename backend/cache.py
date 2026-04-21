"""L1 feed response cache (by URL hash + ETag/content hash)."""

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import log

_cache_dir = Path(__file__).parent.parent / "cache"
_feed_cache_dir = _cache_dir / "feeds"


def configure(cache_dir: Path) -> None:
  global _cache_dir, _feed_cache_dir
  _cache_dir = cache_dir
  _feed_cache_dir = cache_dir / "feeds"


def _atomic_write(path: Path, data: str) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
  try:
    with open(fd, "w", encoding="utf-8") as f:
      f.write(data)
    Path(tmp).replace(path)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise


def _safe_load_json(path: Path) -> dict[str, object] | None:
  if not path.exists():
    return None
  try:
    result: object = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(result, dict):
      return result  # type: ignore[return-value]
    return None
  except (json.JSONDecodeError, OSError):
    log.warn(f"  Warning: corrupted cache {path.name}, ignoring.")
    return None


def _feed_key(url: str) -> str:
  return hashlib.sha256(url.encode()).hexdigest()


def load_feed_cache(url: str) -> dict[str, object] | None:
  path = _feed_cache_dir / f"{_feed_key(url)}.json"
  return _safe_load_json(path)


def save_feed_cache(url: str, etag: str | None, last_modified: str | None, body: str) -> None:
  content_hash = hashlib.sha256(body.encode()).hexdigest()
  path = _feed_cache_dir / f"{_feed_key(url)}.json"
  data = json.dumps({
    "url": url,
    "etag": etag,
    "last_modified": last_modified,
    "content_hash": content_hash,
    "body": body,
  }, ensure_ascii=False)
  _atomic_write(path, data)


def purge_feed_cache() -> int:
  """Remove every cached feed body. Returns the number of files deleted."""
  if not _feed_cache_dir.exists():
    return 0
  count = 0
  for child in _feed_cache_dir.iterdir():
    if child.is_file():
      child.unlink(missing_ok=True)
      count += 1
    elif child.is_dir():
      shutil.rmtree(child, ignore_errors=True)
  return count
