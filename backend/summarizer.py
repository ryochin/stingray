import asyncio

import httpx

import lang
import log
from llm import call_ollama
from models import Article


def _translate_and_summarize_prompt(native_lang: str) -> str:
  name = lang.display_name(native_lang)
  return f"""\
You are a concise news summarizer.
Given an article in a foreign language, return a JSON object with two fields:
- "title_translated": The article title translated into {name}.
- "summary": A 2-3 sentence summary of the article in {name}, focusing on key facts and significance.

Return ONLY valid JSON, no markdown fences or extra text."""


def _translate_full_prompt(native_lang: str) -> str:
  name = lang.display_name(native_lang)
  return f"""\
You are a professional translator.
Given a short article in a foreign language, return a JSON object with two fields:
- "title_translated": The article title translated into {name}.
- "content_translated": The full article content translated into {name}. Preserve the original structure and meaning.

Return ONLY valid JSON, no markdown fences or extra text."""


def _translate_title_only_prompt(native_lang: str) -> str:
  name = lang.display_name(native_lang)
  return f"""\
You are a professional translator.
Given an article in a foreign language, return a JSON object with one field:
- "title_translated": The article title translated into {name}.

Do NOT translate or summarize the body. Do NOT include any other field.

Return ONLY valid JSON, no markdown fences or extra text."""


def _summarize_prompt(native_lang: str) -> str:
  name = lang.display_name(native_lang)
  return f"""\
You are a concise news summarizer.
Given an article, return a JSON object with one field:
- "summary": A 2-3 sentence summary of the article in {name}, focusing on key facts and significance.

Do NOT translate or rewrite the title. Do NOT include a "title_translated" field.

Return ONLY valid JSON, no markdown fences or extra text."""


async def process_article(
  client: httpx.AsyncClient,
  article: Article,
  model: str,
  *,
  translate: bool,
  summarize: bool,
  native_lang: str,
  short: bool = False,
) -> tuple[str, str, str]:
  """Process a single article with LLM.

  Returns (title_translated, summary, content_translated). The processing mode
  is decided by (translate, summarize, short):
    - translate + short: full-body translation (title + content_translated).
    - translate + long + not summarize: title-only translation (body stays
      in the source language; no summary).
    - translate + long + summarize: title translation + summary.
    - not translate + summarize: native-language summary only.
  """
  user_prompt = f"Title: {article.title}\n\n{article.content_snippet}"

  if translate and short:
    prompt = _translate_full_prompt(native_lang)
    data = await call_ollama(client, model, prompt, user_prompt)
    title_translated = data.get("title_translated", article.title)
    content_translated = data.get("content_translated", "")
    if not isinstance(title_translated, str):
      title_translated = article.title
    if not isinstance(content_translated, str):
      content_translated = ""
    return title_translated, "", content_translated

  if translate and not summarize:
    # Long foreign article with summary disabled: translate only the title.
    prompt = _translate_title_only_prompt(native_lang)
    data = await call_ollama(client, model, prompt, user_prompt)
    title_translated = data.get("title_translated", article.title)
    if not isinstance(title_translated, str):
      title_translated = article.title
    return title_translated, "", ""

  if translate:
    prompt = _translate_and_summarize_prompt(native_lang)
    data = await call_ollama(client, model, prompt, user_prompt)
    title_translated = data.get("title_translated", article.title)
    summary = data.get("summary", "")
    if not isinstance(title_translated, str):
      title_translated = article.title
    if not isinstance(summary, str):
      summary = ""
    return title_translated, summary, ""

  # Native language, long content — summarize only
  prompt = _summarize_prompt(native_lang)
  data = await call_ollama(client, model, prompt, user_prompt)
  summary = data.get("summary", "")
  if not isinstance(summary, str):
    summary = ""
  return "", summary, ""


async def summarize_all(
  articles: list[Article],
  model: str = "gemma4:e4b",
  base_url: str = "http://localhost:11434",
  timeout: int = 120,
  concurrency: int = 3,
  *,
  translate_set: set[str] | None = None,
  summarize_set: set[str] | None = None,
  short_set: set[str] | None = None,
  native_lang: str = "ja",
) -> int:
  """Process articles with LLM. Returns the number of failures.

  translate_set: set of source names that need translation.
  summarize_set: set of source names whose feed has summarize enabled.
  short_set: set of article URLs that are short (full translation, no summary).
  """
  sem = asyncio.Semaphore(concurrency)
  total = len(articles)
  failures = 0
  _translate_set = translate_set or set()
  _summarize_set = summarize_set or set()
  _short_set = short_set or set()

  async with httpx.AsyncClient(
    base_url=base_url,
    timeout=timeout,
  ) as client:

    async def _process(idx: int, article: Article) -> bool:
      nonlocal failures
      async with sem:
        log.dim(f"  [{idx + 1}/{total}] {article.title[:60]}...")
        try:
          title_translated, summary, content_translated = await process_article(
            client, article, model,
            translate=article.source in _translate_set,
            summarize=article.source in _summarize_set,
            native_lang=native_lang,
            short=article.url in _short_set,
          )
          article.title_translated = title_translated
          article.summary = summary
          article.content_translated = content_translated
          return True
        except Exception as exc:
          log.error(f"    Error: {exc}")
          failures += 1
          return False

    await asyncio.gather(*[_process(idx, article) for idx, article in enumerate(articles)])
  return failures
