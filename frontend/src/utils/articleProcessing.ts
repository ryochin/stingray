import type { Article } from "../api/client"

/** Body-snippet length below which a foreign article is translated in full
 *  rather than summarized. Must mirror `SHORT_SNIPPET_CHARS` in
 *  `backend/fetcher.py` so the frontend's pending-state heuristic matches the
 *  backend's actual processing decision. */
export const SHORT_SNIPPET_CHARS: number = 300

/** What body-level LLM work an article is still waiting on, or `null` when its
 *  body needs no (further) processing. The title-translation case is
 *  intentionally excluded: the card always renders the original title, so a
 *  missing `title_translated` is not a body placeholder. */
export type PendingKind = "summary" | "translation" | null

/** Mirror of the *body* branch of `backend/fetcher.py:_needs_llm`, returning
 *  which placeholder (if any) the article card should show while the body is
 *  being processed. Snippet length drives the same short/long split as the
 *  backend, so already-complete bodies never show a stale "Awaiting…" label. */
export function pendingProcessing(
  article: Article,
  translate: boolean,
  summarize: boolean,
): PendingKind {
  const snippet: string = article.content_snippet ?? ""
  if (translate) {
    // No body to translate → nothing pending (title alone completes it).
    if (snippet.length === 0) return null
    // Short foreign article → full-body translation.
    if (snippet.length < SHORT_SNIPPET_CHARS) {
      return article.content_translated ? null : "translation"
    }
    // Long foreign article: translated summary if summarizing, else title-only.
    if (summarize) return article.summary ? null : "summary"
    return null
  }
  if (!summarize) return null
  // Native summarize: only bodies long enough to summarize.
  if (snippet.length < SHORT_SNIPPET_CHARS) return null
  return article.summary ? null : "summary"
}
