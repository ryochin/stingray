from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader

from feeds import Article

TEMPLATE_DIR = Path(__file__).parent / "templates"
JST = timezone(timedelta(hours=9))


def _to_jst(dt: datetime) -> str:
  if dt.tzinfo is None:
    dt = dt.replace(tzinfo=timezone.utc)
  return dt.astimezone(JST).strftime("%Y-%m-%d %H:%M JST")


def render_report(
  articles: list[Article],
  output_path: Path,
  date: datetime | None = None,
) -> Path:
  date = date or datetime.now(tz=timezone.utc)

  grouped: dict[str, list[Article]] = {}
  for article in articles:
    grouped.setdefault(article.source, []).append(article)

  env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=True,
  )
  env.filters["jst"] = _to_jst
  env.filters["domain"] = lambda url: urlparse(url).hostname or ""
  template = env.get_template("report.html.j2")

  html = template.render(
    date=date,
    grouped=grouped,
    total=len(articles),
  )

  output_path.parent.mkdir(parents=True, exist_ok=True)
  output_path.write_text(html, encoding="utf-8")
  return output_path
