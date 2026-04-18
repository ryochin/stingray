import re
from dataclasses import dataclass
from datetime import datetime

# Matches any hiragana or katakana character. Shared by feed/lang detection.
JA_KANA = re.compile(r"[\u3040-\u309F\u30A0-\u30FF]")


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
