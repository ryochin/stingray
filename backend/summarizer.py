import asyncio
import json

import httpx

import log
from models import Article

_JA_RULES = """\
Write Japanese output using ONLY Japanese scripts (hiragana, katakana, kanji) and standard ASCII punctuation.
Do NOT include Cyrillic, Greek, Arabic, Hangul, or any other non-Japanese script.
For foreign loanwords, always use katakana (e.g. "Molotov cocktail" → "モロトフカクテル", never "モロトフ коктейл").
Proper nouns that are well-known in their Latin form (company names, people, products like "OpenAI", "ChatGPT") may remain in Latin script."""

TRANSLATE_PROMPT = f"""\
You are a concise news summarizer.
Given a non-Japanese article, return a JSON object with two fields:
- "title_ja": The article title translated into Japanese.
- "summary": A 2-3 sentence summary of the article in Japanese, focusing on key facts and significance.

{_JA_RULES}

Return ONLY valid JSON, no markdown fences or extra text."""

SUMMARIZE_PROMPT = f"""\
You are a concise news summarizer.
Given a Japanese article, return a JSON object with one field:
- "summary": A 2-3 sentence summary of the article in Japanese, focusing on key facts and significance.

Do NOT translate or rewrite the title. Do NOT include a "title_ja" field.

{_JA_RULES}

Return ONLY valid JSON, no markdown fences or extra text."""


async def _call_llm(
  client: httpx.AsyncClient,
  model: str,
  system_prompt: str,
  user_prompt: str,
) -> dict:
  resp = await client.post(
    "/api/chat",
    json={
      "model": model,
      "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
      ],
      "stream": False,
      "format": "json",
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

  try:
    return json.loads(content)
  except json.JSONDecodeError:
    raise ValueError(f"LLM returned invalid JSON: {content[:200]}")


async def process_article(
  client: httpx.AsyncClient,
  article: Article,
  model: str,
) -> tuple[str, str]:
  user_prompt = f"Title: {article.title}\n\n{article.content_snippet}"

  if article.lang == "ja":
    data = await _call_llm(client, model, SUMMARIZE_PROMPT, user_prompt)
    summary = data.get("summary", "")
    if not isinstance(summary, str):
      summary = ""
    return "", summary

  data = await _call_llm(client, model, TRANSLATE_PROMPT, user_prompt)
  title_ja = data.get("title_ja", article.title)
  summary = data.get("summary", "")
  if not isinstance(title_ja, str):
    title_ja = article.title
  if not isinstance(summary, str):
    summary = ""
  return title_ja, summary


async def summarize_all(
  articles: list[Article],
  model: str = "gemma3",
  base_url: str = "http://localhost:11434",
  timeout: int = 120,
  concurrency: int = 3,
) -> int:
  """Summarize articles. Returns the number of failures."""
  sem = asyncio.Semaphore(concurrency)
  total = len(articles)
  failures = 0

  async with httpx.AsyncClient(
    base_url=base_url,
    timeout=timeout,
  ) as client:

    async def _process(i: int, article: Article) -> bool:
      nonlocal failures
      async with sem:
        log.dim(f"  [{i + 1}/{total}] {article.title[:60]}...")
        try:
          title_ja, summary = await process_article(client, article, model)
          article.title_ja = title_ja
          article.summary = summary
          return True
        except Exception as e:
          log.error(f"    Error: {e}")
          failures += 1
          return False

    await asyncio.gather(*[_process(i, a) for i, a in enumerate(articles)])
  return failures
