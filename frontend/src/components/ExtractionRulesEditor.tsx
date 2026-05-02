import { useState, useEffect } from "react"
import Editor from "react-simple-code-editor"
import { highlight, languages } from "prismjs/components/prism-core"
import "prismjs/components/prism-clike"
import "prismjs/components/prism-javascript"
import "prismjs/components/prism-json"
import { api } from "../api/client"
import type { Feed } from "../api/client"

function prettifyRules(raw: string | null | undefined): string {
  try {
    const parsed = JSON.parse(raw || "{}")
    return Object.keys(parsed).length > 0 ? JSON.stringify(parsed, null, 2) : ""
  } catch { return raw || "" }
}

export default function ExtractionRulesEditor({
  feed,
  onSaved,
}: {
  feed: Feed,
  onSaved: () => void,
}) {
  const [json, setJson] = useState(() => prettifyRules(feed.extraction_rules))
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Keep editor in sync when the feed prop changes (e.g. after invalidation)
  useEffect(() => {
    setJson(prettifyRules(feed.extraction_rules))
  }, [feed.extraction_rules])

  // Auto-hide the "Saved" indicator after a short delay
  useEffect(() => {
    if (savedAt == null) return
    const timer = setTimeout(() => setSavedAt(null), 2000)
    return () => clearTimeout(timer)
  }, [savedAt])

  const validate = (): Record<string, string | null> | null => {
    try {
      const parsed = JSON.parse(json)
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

  const handleSave = async () => {
    const parsed = validate()
    if (!parsed) return
    setSaving(true)
    setError(null)
    try {
      await api.updateFeedRules(feed.id, parsed)
      setSavedAt(Date.now())
      onSaved()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mt-2 p-2 bg-bg-card rounded border border-border">
      <div className="text-xs font-semibold text-text-heading mb-1">CSS Extraction Rules</div>
      <div className="text-xs text-text-dim mb-1.5">{"Keys: item*, title*, link*, link_attr, date, date_attr, thumbnail, thumbnail_attr"}</div>
      <div className="bg-bg-secondary border border-border rounded overflow-auto max-h-96 resize-y">
        <Editor
          value={json}
          onValueChange={setJson}
          highlight={(code) => highlight(code, languages.json, "json")}
          padding={8}
          textareaClassName="outline-none"
          className="text-xs font-mono text-text min-h-[180px]"
          placeholder='{"item": "p", "title": "b a", "link": "b a", "link_attr": "href"}'
          spellCheck={false}
        />
      </div>
      {error && <div className="text-xs text-red-400 mt-1">{error}</div>}
      <div className="flex items-center justify-end gap-2 mt-1.5">
        {savedAt != null && (
          <span className="text-xs text-green-400 transition-opacity" aria-live="polite">
            ✓ Saved
          </span>
        )}
        <button
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
