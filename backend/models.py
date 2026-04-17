from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
  title: str
  url: str
  source: str
  published: datetime | None
  content_snippet: str
  title_translated: str = ""
  summary: str = ""
  content_html: str = ""
  content_translated: str = ""

  def to_dict(self) -> dict:
    return {
      "title": self.title,
      "url": self.url,
      "source": self.source,
      "published": self.published.isoformat() if self.published else None,
      "content_snippet": self.content_snippet,
      "title_translated": self.title_translated,
      "summary": self.summary,
      "content_html": self.content_html,
      "content_translated": self.content_translated,
    }

  @classmethod
  def from_dict(cls, d: dict) -> "Article":
    published = None
    if d.get("published"):
      published = datetime.fromisoformat(d["published"])
    return cls(
      title=d["title"],
      url=d["url"],
      source=d["source"],
      published=published,
      content_snippet=d.get("content_snippet", ""),
      title_translated=d.get("title_translated", ""),
      summary=d.get("summary", ""),
      content_html=d.get("content_html", ""),
      content_translated=d.get("content_translated", ""),
    )
