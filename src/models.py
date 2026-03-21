from dataclasses import dataclass
from datetime import datetime


@dataclass
class Article:
  title: str
  url: str
  source: str
  published: datetime | None
  content_snippet: str
  title_ja: str = ""
  summary: str = ""

  def to_dict(self) -> dict:
    return {
      "title": self.title,
      "url": self.url,
      "source": self.source,
      "published": self.published.isoformat() if self.published else None,
      "content_snippet": self.content_snippet,
      "title_ja": self.title_ja,
      "summary": self.summary,
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
      title_ja=d.get("title_ja", ""),
      summary=d.get("summary", ""),
    )
