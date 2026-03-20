import json

import httpx

from feeds import Article

SYSTEM_PROMPT = """\
You are a concise news summarizer.
Given an article, return a JSON object with two fields:
- "title_ja": The article title translated into Japanese. If the title is already in Japanese, return it as-is.
- "summary": A 2-3 sentence summary of the article in Japanese, focusing on key facts and significance.

Return ONLY valid JSON, no markdown fences or extra text."""


async def process_article(
  client: httpx.AsyncClient,
  article: Article,
  model: str,
) -> tuple[str, str]:
  user_prompt = f"Title: {article.title}\n\n{article.content_snippet}"

  resp = await client.post(
    "/api/chat",
    json={
      "model": model,
      "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
      ],
      "stream": False,
    },
  )
  resp.raise_for_status()
  content = resp.json()["message"]["content"].strip()

  # Strip markdown fences if present
  if content.startswith("```"):
    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
      content = content[:-3]
    content = content.strip()

  data = json.loads(content)
  return data.get("title_ja", article.title), data.get("summary", "")


async def summarize_all(
  articles: list[Article],
  model: str = "gemma3",
  base_url: str = "http://localhost:11434",
  timeout: int = 120,
) -> list[Article]:
  async with httpx.AsyncClient(
    base_url=base_url,
    timeout=timeout,
  ) as client:
    total = len(articles)
    for i, article in enumerate(articles):
      print(f"  [{i + 1}/{total}] {article.title[:60]}...")
      try:
        title_ja, summary = await process_article(client, article, model)
        article.title_ja = title_ja
        article.summary = summary
      except Exception as e:
        print(f"    Error: {e}")
        article.title_ja = article.title
        article.summary = "(要約を生成できませんでした)"

  return articles
