import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from models import Article


CACHE_DIR = Path(__file__).parent.parent / "cache"
FEED_CACHE_DIR = CACHE_DIR / "feeds"
ARTICLE_CACHE_PATH = CACHE_DIR / "articles.json"

ARTICLE_CACHE_MAX_AGE_DAYS = 30


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


def _safe_load_json(path: Path) -> dict | list | None:
  if not path.exists():
    return None
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (json.JSONDecodeError, OSError):
    print(f"  Warning: corrupted cache {path.name}, ignoring.")
    return None


# --- L1: Feed response cache (by URL hash + ETag/content hash) ---

def _feed_key(url: str) -> str:
  return hashlib.sha256(url.encode()).hexdigest()[:16]


def load_feed_cache(url: str) -> dict | None:
  path = FEED_CACHE_DIR / f"{_feed_key(url)}.json"
  return _safe_load_json(path)


def save_feed_cache(url: str, etag: str | None, last_modified: str | None, body: str) -> None:
  content_hash = hashlib.sha256(body.encode()).hexdigest()
  path = FEED_CACHE_DIR / f"{_feed_key(url)}.json"
  data = json.dumps({
    "url": url,
    "etag": etag,
    "last_modified": last_modified,
    "content_hash": content_hash,
    "body": body,
  }, ensure_ascii=False)
  _atomic_write(path, data)


# --- L2: Article-level cache (keyed by URL, stores title_ja + summary) ---

def _load_article_db() -> dict:
  data = _safe_load_json(ARTICLE_CACHE_PATH)
  if isinstance(data, dict):
    return data
  return {}


def _save_article_db(db: dict) -> None:
  _atomic_write(ARTICLE_CACHE_PATH, json.dumps(db, ensure_ascii=False, indent=2))


def _prune_article_db(db: dict) -> dict:
  cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=ARTICLE_CACHE_MAX_AGE_DAYS)).isoformat()
  return {
    url: entry for url, entry in db.items()
    if entry.get("cached_at", "") >= cutoff
  }


def restore_article_cache(articles: list[Article]) -> list[Article]:
  db = _load_article_db()
  cached_count = 0
  for article in articles:
    if not article.url:
      continue
    entry = db.get(article.url)
    if entry:
      article.title_ja = entry.get("title_ja", "")
      article.summary = entry.get("summary", "")
      cached_count += 1
  if cached_count:
    print(f"  {cached_count}/{len(articles)} articles restored from cache.")
  return articles


def save_article_cache(articles: list[Article]) -> None:
  db = _load_article_db()
  now = datetime.now(tz=timezone.utc).isoformat()
  for article in articles:
    if not article.url:
      continue
    if article.title_ja or article.summary:
      db[article.url] = {
        "title_ja": article.title_ja,
        "summary": article.summary,
        "cached_at": now,
      }
  db = _prune_article_db(db)
  _save_article_db(db)
