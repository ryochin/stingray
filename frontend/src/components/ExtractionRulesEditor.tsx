import { highlight, languages } from "prismjs/components/prism-core"
import type { JSX } from "react"
import { useEffect, useState } from "react"
import Editor from "react-simple-code-editor"
import "prismjs/components/prism-clike"
import "prismjs/components/prism-javascript"
import "prismjs/components/prism-json"
import type { Feed, InferStatus, SampleArticle } from "../api/client"
import { api } from "../api/client"
import { formatRelativeShort } from "../utils/date"

function prettifyRules(raw: string | null | undefined): string {
  try {
    const parsed: Record<string, unknown> = JSON.parse(raw || "{}")
    return Object.keys(parsed).length > 0 ? JSON.stringify(parsed, null, 2) : ""
  } catch {
    return raw || ""
  }
}

// User-facing hint shown when inference returns rules that didn't fully work.
// "ok" needs no hint (the preview speaks for itself).
const INFER_STATUS_HINT: Record<InferStatus, string | null> = {
  ok: null,
  empty: "Selectors matched no articles — adjust them above, then save.",
  invalid:
    "The model returned invalid selectors — adjust them above, then save.",
  error: "Inference failed (LLM or page error) — try again or edit manually.",
}

export default function ExtractionRulesEditor({
  feed,
  onSaved,
}: {
  feed: Feed
  onSaved: () => void
}): JSX.Element {
  const [json, setJson] = useState<string>((): string =>
    prettifyRules(feed.extraction_rules),
  )
  const [saving, setSaving] = useState<boolean>(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [inferring, setInferring] = useState<boolean>(false)
  const [inferHint, setInferHint] = useState<string | null>(null)
  const [samples, setSamples] = useState<SampleArticle[]>([])

  // Keep editor in sync when the feed prop changes (e.g. after invalidation)
  useEffect((): void => {
    setJson(prettifyRules(feed.extraction_rules))
  }, [feed.extraction_rules])

  // Auto-hide the "Saved" indicator after a short delay
  useEffect((): (() => void) | undefined => {
    if (savedAt == null) return
    const timer: ReturnType<typeof setTimeout> = setTimeout(
      (): void => setSavedAt(null),
      2000,
    )
    return (): void => clearTimeout(timer)
  }, [savedAt])

  const runInference = async (): Promise<void> => {
    setInferring(true)
    setError(null)
    setInferHint(null)
    try {
      const result = await api.inferRules(feed.id)
      setJson(prettifyRules(JSON.stringify(result.rules)))
      setSamples(result.sample_articles)
      setInferHint(INFER_STATUS_HINT[result.status])
    } catch (caught) {
      setSamples([])
      setError(caught instanceof Error ? caught.message : "Inference failed")
    } finally {
      setInferring(false)
    }
  }

  const validate = (): Record<string, string | null> | null => {
    try {
      const parsed: Record<string, string | null> = JSON.parse(json)
      if (typeof parsed !== "object" || Array.isArray(parsed)) {
        setError("Must be a JSON object")
        return null
      }
      if (!parsed.item || !parsed.title || !parsed.link) {
        setError("item, title, link are required")
        return null
      }
      return parsed
    } catch {
      setError("Invalid JSON")
      return null
    }
  }

  const handleSave = async (): Promise<void> => {
    const parsed: Record<string, string | null> | null = validate()
    if (!parsed) return
    setSaving(true)
    setError(null)
    try {
      await api.updateFeedRules(feed.id, parsed)
      setSavedAt(Date.now())
      onSaved()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 p-2 bg-bg-card rounded border border-border">
      <div className="text-xs font-semibold text-text-heading mb-1">
        CSS Extraction Rules
      </div>
      <div className="text-xs text-text-dim mb-1.5">
        {
          "Keys: item*, title*, link*, link_attr, date, date_attr, thumbnail, thumbnail_attr"
        }
      </div>
      <div className="bg-bg-secondary border border-border rounded overflow-auto max-h-96 resize-y">
        <Editor
          value={json}
          onValueChange={setJson}
          highlight={(code: string): string =>
            highlight(code, languages.json, "json")
          }
          padding={8}
          textareaClassName="outline-none"
          className="text-xs font-mono text-text min-h-[180px]"
          placeholder='{"item": "p", "title": "b a", "link": "b a", "link_attr": "href"}'
          spellCheck={false}
        />
      </div>
      {error && <div className="text-xs text-red-400 mt-1">{error}</div>}
      {inferHint && (
        <div className="text-xs text-yellow-400 mt-1" aria-live="polite">
          <span aria-hidden="true">⚠</span> {inferHint}
        </div>
      )}
      {samples.length > 0 && (
        <div className="mt-2">
          <div className="text-xs font-semibold text-text-heading mb-1">
            Preview — {samples.length} article
            {samples.length === 1 ? "" : "s"} extracted
          </div>
          <ul className="space-y-0.5 list-none p-0 m-0">
            {samples.map((sample: SampleArticle) => (
              <li
                key={sample.url}
                className="text-xs text-text-muted flex items-baseline gap-2"
              >
                <a
                  href={sample.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-link hover:text-link-hover hover:underline truncate"
                >
                  {sample.title}
                </a>
                {sample.published && (
                  <span className="text-text-dim shrink-0">
                    {formatRelativeShort(sample.published)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex items-center justify-end gap-2 mt-1.5">
        {savedAt != null && (
          <span
            className="text-xs text-green-400 transition-opacity"
            aria-live="polite"
          >
            ✓ Saved
          </span>
        )}
        <button
          type="button"
          onClick={runInference}
          disabled={inferring}
          title="Ask the LLM to infer selectors from the page"
          className="px-3 py-1 rounded text-xs bg-bg-secondary text-text-muted border border-border hover:text-text transition-colors disabled:opacity-40"
        >
          <span className={`inline-block ${inferring ? "animate-spin" : ""}`}>
            {inferring ? "↻" : "✨"}
          </span>{" "}
          {inferring ? "Inferring..." : "Infer with LLM"}
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !json.trim()}
          className="px-3 py-1 rounded text-xs bg-accent text-white hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          {saving ? "Saving..." : "Save Rules"}
        </button>
      </div>
    </div>
  )
}
