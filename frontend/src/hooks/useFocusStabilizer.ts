import { useLayoutEffect, useRef, type RefObject } from "react"
import type { Virtualizer } from "@tanstack/react-virtual"
import type { Article, Selection } from "../api/client"
import type { TimeRangeId } from "../utils/articleView"

interface Args {
  filtered: Article[]
  focusIndex: number
  setFocusIndex: (i: number) => void
  virtualizer: Virtualizer<HTMLElement, Element>
  mainRef: RefObject<HTMLElement | null>
  selection: Selection
  showUnreadOnly: boolean
  timeRangeId: TimeRangeId
}

// Preserve focus identity and visual position when `filtered` shifts (e.g.
// background refetch prepends new articles). Without this, the focused card
// slides out from under the user and the viewport appears to jump since
// scrollTop is preserved but cards are pushed down.
//
// Scope: only `filtered` changes caused by background data updates.
// `selection` / `showUnreadOnly` toggles trigger their own reset effect
// (focus → -1, scrollTo top); running compensation there would leak the
// skipFocusScroll signal into an unrelated scroll pass.
//
// Returns `skipFocusScrollRef`, which the focus-scroll effect must read and
// reset to `false` when set: it tells that effect "the rebind below already
// placed the card visually; do not animate over the result".
export function useFocusStabilizer({
  filtered,
  focusIndex,
  setFocusIndex,
  virtualizer,
  mainRef,
  selection,
  showUnreadOnly,
  timeRangeId,
}: Args): RefObject<boolean> {
  const prevFocusSnapshot = useRef<{
    filtered: Article[]
    index: number
    url: string
    offset: number
  } | null>(null)
  const prevSelectionRef = useRef(selection)
  const prevShowUnreadOnlyRef = useRef(showUnreadOnly)
  const prevTimeRangeIdRef = useRef(timeRangeId)
  const skipFocusScrollRef = useRef(false)

  useLayoutEffect(() => {
    const main = mainRef.current
    const prev = prevFocusSnapshot.current
    const selectionChanged = prevSelectionRef.current !== selection
    const filterToggled = prevShowUnreadOnlyRef.current !== showUnreadOnly
    const timeRangeChanged = prevTimeRangeIdRef.current !== timeRangeId
    prevSelectionRef.current = selection
    prevShowUnreadOnlyRef.current = showUnreadOnly
    prevTimeRangeIdRef.current = timeRangeId

    // User-initiated list reset is handled elsewhere; drop the snapshot so
    // the next refetch-driven change re-captures from the post-reset state.
    if (selectionChanged || filterToggled || timeRangeChanged) {
      prevFocusSnapshot.current = null
      return
    }

    // Compensation only applies when the user's focus stayed put but the
    // list shifted beneath it. If focusIndex changed from the snapshot,
    // the focus move itself was user- or program-initiated (j/k, click,
    // auto-focus) and no rebinding is needed — reverting to prev.url
    // would cancel that move (e.g. j-advance whose scheduleRead forces
    // `filtered` to re-memo into a new reference).
    if (
      main
      && prev
      && prev.filtered !== filtered
      && focusIndex === prev.index
      && focusIndex >= 0
      && filtered[focusIndex]?.url !== prev.url
    ) {
      const newIndex = filtered.findIndex((a) => a.url === prev.url)
      if (newIndex < 0) {
        // Focused article vanished (server-side delete, read state changed
        // outside this session, etc). Avoid silently inheriting whichever
        // article slid into the old index slot; clear the snapshot and let
        // the normal render continue with focusIndex at its numeric slot.
        prevFocusSnapshot.current = null
        return
      }
      const newOffset = virtualizer.getOffsetForIndex(newIndex, "start")?.[0]
      if (newOffset != null) {
        main.scrollTop += newOffset - prev.offset
      }
      skipFocusScrollRef.current = true
      setFocusIndex(newIndex)
      prevFocusSnapshot.current = {
        filtered,
        index: newIndex,
        url: prev.url,
        offset: newOffset ?? prev.offset,
      }
      return
    }

    const currentUrl = focusIndex >= 0 ? filtered[focusIndex]?.url ?? null : null
    const currentOffset = focusIndex >= 0
      ? virtualizer.getOffsetForIndex(focusIndex, "start")?.[0] ?? null
      : null
    prevFocusSnapshot.current = currentUrl != null && currentOffset != null
      ? { filtered, index: focusIndex, url: currentUrl, offset: currentOffset }
      : null
  }, [filtered, focusIndex, setFocusIndex, virtualizer, mainRef, selection, showUnreadOnly, timeRangeId])

  return skipFocusScrollRef
}
