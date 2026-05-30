import type { Virtualizer } from "@tanstack/react-virtual"
import { useVirtualizer } from "@tanstack/react-virtual"
import type { Dispatch, RefObject, SetStateAction } from "react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { Article, Selection } from "../api/client"
import { CARD_GAP } from "../components/articleListLayout"
import type { TimeRangeId } from "../utils/articleView"
import { smoothScrollTo } from "../utils/smoothScroll"
import { useFocusStabilizer } from "./useFocusStabilizer"

type CaughtUpHint = "jump" | "end" | null

interface UseArticleListControllerInput {
  filtered: Article[]
  focusIndex: number
  setFocusIndex: Dispatch<SetStateAction<number>>
  mainRef: RefObject<HTMLElement | null>
  stickyHeaderRef: RefObject<HTMLElement | null>
  headerHeight: number
  selection: Selection
  showUnreadOnly: boolean
  timeRangeId: TimeRangeId
  scheduleRead: (url: string) => void
  hasSessionRead: (url: string) => boolean
  /** Used by `onJAtEnd` to decide which sub-text hint to surface on the
   *  second consecutive j-at-end press. */
  nextUnreadFeed: number | null
}

interface UseArticleListControllerResult {
  virtualizer: Virtualizer<HTMLElement, Element>
  /** Virtual index used by the trailing "All caught up" sentinel row.
   *  Equals `filtered.length`; exposed so the render loop and the
   *  scroll detector agree on the same boundary. */
  allCaughtUpIndex: number
  /** Card ref setter forwarded to `<ArticleCard ref>`. */
  setRef: (index: number, el: HTMLDivElement | null) => void
  /** Click handler for the card wrapper — moves focus and marks the
   *  previously-focused card as read. */
  handleCardClick: (index: number) => void
  /** Marks the article at `index` as read if it is currently unread. */
  markFocusedAsRead: (index: number) => void
  /** j-at-end handler: smooth-scrolls to the bottom and pulses the
   *  caught-up indicator. */
  onJAtEnd: () => void
  /** k-before-move handler: re-aligns the focused card if it has
   *  scrolled out of view rather than moving focus to the previous one.
   *  Returns true when the realign happened (caller should swallow the
   *  k press). */
  onKBeforeMove: () => boolean
  caughtUpPulseKey: number
  caughtUpHint: CaughtUpHint
  /** True iff the caught-up sentinel is currently intersecting the
   *  scroll viewport. Combined with `caughtUpHint === "jump"` to gate
   *  the Space-key shortcut on actual on-screen hint visibility — so
   *  scrolling the sentinel out of view immediately disarms Space. */
  caughtUpVisible: boolean
  /** Ref-callback for the sentinel's outer wrapper; the controller
   *  attaches an `IntersectionObserver` to it to drive
   *  `caughtUpVisible`. */
  caughtUpSentinelRef: (el: HTMLDivElement | null) => void
}

/** Owns the article-list "control plane": virtualizer wiring, focus
 *  stabilisation, focus-driven smooth scroll, the scroll-based mark-as-read
 *  detector, and the caught-up indicator state. Returns descriptors the
 *  route component plugs into render and keyboard handlers. */
export function useArticleListController({
  filtered,
  focusIndex,
  setFocusIndex,
  mainRef,
  stickyHeaderRef,
  headerHeight,
  selection,
  showUnreadOnly,
  timeRangeId,
  scheduleRead,
  hasSessionRead,
  nextUnreadFeed,
}: UseArticleListControllerInput): UseArticleListControllerResult {
  const articleRefs = useRef<Map<number, HTMLDivElement>>(new Map())
  // Tracks the rAF driving the focus-scroll animation so competing scroll
  // sources (e.g. onJAtEnd's scrollTo bottom) can cancel it before issuing
  // their own scroll — otherwise the rAF keeps writing main.scrollTop each
  // frame and overrides the new scroll intent.
  const focusScrollRafRef = useRef<number | null>(null)
  // Set by `useFocusStabilizer` whenever it shifts main.scrollTop to keep
  // the focused card visually pinned across a list rebind. The scroll-based
  // mark-as-read detector reads this and consumes it on the first event so
  // such hook-driven shifts don't masquerade as user-initiated down-scroll.
  const programmaticScrollRef = useRef<boolean>(false)

  const [caughtUpPulseKey, setCaughtUpPulseKey] = useState<number>(0)
  // Sub-text hint shown under "All caught up" on a second consecutive
  // j-at-end press. "jump" advertises the space shortcut to the next unread
  // feed; "end" reports that no further unread feed exists.
  const [caughtUpHint, setCaughtUpHint] = useState<CaughtUpHint>(null)
  // Whether the caught-up sentinel is intersecting the scroll viewport.
  // Driven by an IntersectionObserver attached via `caughtUpSentinelRef`
  // so the Space-key shortcut can be disarmed the moment the sentinel
  // (and the hint underneath it) leaves the visible area.
  const [caughtUpVisible, setCaughtUpVisible] = useState<boolean>(false)
  const caughtUpObserverRef = useRef<IntersectionObserver | null>(null)
  const caughtUpObservedRef = useRef<Element | null>(null)
  const caughtUpSentinelRef = useCallback(
    (el: HTMLDivElement | null): void => {
      // Drop the previous observation before swapping targets so a stale
      // entry from the recycled element can't flip visibility after the
      // new one is already wired up.
      if (caughtUpObservedRef.current && caughtUpObserverRef.current) {
        caughtUpObserverRef.current.unobserve(caughtUpObservedRef.current)
      }
      caughtUpObservedRef.current = el
      if (!el) {
        setCaughtUpVisible(false)
        return
      }
      if (caughtUpObserverRef.current == null) {
        caughtUpObserverRef.current = new IntersectionObserver(
          (entries: IntersectionObserverEntry[]): void => {
            for (const entry of entries) {
              if (entry.target === caughtUpObservedRef.current) {
                setCaughtUpVisible(entry.isIntersecting)
              }
            }
          },
          { root: mainRef.current, threshold: 0 },
        )
      }
      caughtUpObserverRef.current.observe(el)
    },
    [mainRef],
  )
  useEffect(
    (): (() => void) => (): void => {
      caughtUpObserverRef.current?.disconnect()
      caughtUpObserverRef.current = null
      caughtUpObservedRef.current = null
    },
    [],
  )

  // Any focus movement is treated as "user did something else", so the
  // caught-up pulse counter and hint are cleared. Repeated j-at-end keeps
  // focus pinned to the last article, so this reset doesn't fire then —
  // exactly the case where we want the hint to appear on the second press.
  // biome-ignore lint/correctness/useExhaustiveDependencies: `focusIndex` is a change trigger; the body only resets state and doesn't read it.
  useEffect((): void => {
    setCaughtUpPulseKey(0)
    setCaughtUpHint(null)
  }, [focusIndex])

  // Include the "All caught up" sentinel as the last virtual item so its
  // position is coordinated with the virtualizer's scrollAdjustments /
  // smooth-scroll state. Otherwise it sits in normal flow after the
  // container and jitters as items below the viewport get measured for
  // the first time (the classic dynamic-size virtualizer tail wobble).
  const allCaughtUpIndex: number = filtered.length
  const virtualizer = useVirtualizer({
    count: filtered.length + 1,
    getScrollElement: () => mainRef.current,
    // Most cards land around 180-220px tall; a closer estimate reduces
    // the delta applied when an overscan item is first measured, which
    // in turn dampens the jitter of totalSize-driven layout shifts.
    // The sentinel row is shorter (~100px icon+text+padding).
    estimateSize: (index: number): number =>
      index === allCaughtUpIndex ? 100 : 200 + CARD_GAP,
    // Wider overscan keeps more items measured before they reach the
    // viewport edge, further reducing first-measure churn.
    overscan: 12,
    scrollMargin: headerHeight,
    // Batch ResizeObserver callbacks into RAF to coalesce bursts of
    // measurements (e.g. images loading across several visible cards).
    useAnimationFrameWithResizeObserver: true,
    getItemKey: (index: number): string | number =>
      index === allCaughtUpIndex
        ? "__all_caught_up__"
        : (filtered[index]?.url ?? index),
  })

  const skipFocusScroll = useFocusStabilizer({
    filtered,
    focusIndex,
    setFocusIndex,
    virtualizer,
    mainRef,
    selection,
    showUnreadOnly,
    timeRangeId,
    programmaticScrollRef,
  })

  // Scroll focused article into view (custom rAF smooth scroll for tunable
  // duration).
  useEffect((): (() => void) | undefined => {
    if (focusIndex < 0) return
    if (skipFocusScroll.current) {
      skipFocusScroll.current = false
      return
    }
    const main: HTMLElement | null = mainRef.current
    if (!main) return
    const el: HTMLDivElement | undefined = articleRefs.current.get(focusIndex)
    // Out-of-range (virtualized away): jump with virtualizer and let the
    // next render place the card; the rAF smooth path below handles the
    // in-range case. For the first article, bypass the virtualizer —
    // its scrollMargin would land us at headerHeight instead of 0,
    // leaving the sticky header in its stuck (small) state.
    if (!el) {
      if (focusIndex === 0) {
        main.scrollTop = 0
      } else {
        virtualizer.scrollToIndex(focusIndex, { align: "start" })
      }
      return
    }
    // For the first article, scroll all the way to the top so the sticky
    // header releases and returns to its initial (full-size) state.
    // Otherwise, align the article's top with the sticky header's bottom
    // edge so the article is never occluded by the header.
    let target: number
    if (focusIndex === 0) {
      target = 0
    } else {
      const headerBottom: number = stickyHeaderRef.current
        ? stickyHeaderRef.current.getBoundingClientRect().bottom
        : main.getBoundingClientRect().top
      target = main.scrollTop + el.getBoundingClientRect().top - headerBottom
    }
    smoothScrollTo(main, target, { rafRef: focusScrollRafRef })
    return (): void => {
      if (focusScrollRafRef.current != null) {
        cancelAnimationFrame(focusScrollRafRef.current)
        focusScrollRafRef.current = null
      }
    }
  }, [
    focusIndex,
    virtualizer,
    skipFocusScroll.current,
    skipFocusScroll,
    mainRef,
    stickyHeaderRef,
  ])

  // Auto mark-as-read on down-scroll: when a card's bottom scrolls above the
  // sticky header, mark its article as read and advance focus to the first
  // card still in view. Up-scroll is intentionally inert (no rewinding of
  // read state). The effect is suppressed while a programmatic focus-scroll
  // is in flight so j/k driven snaps don't trip the detector.
  const lastScrollTopRef = useRef<number>(0)
  const scrollMarkRafRef = useRef<number | null>(null)
  useEffect((): (() => void) | undefined => {
    const main: HTMLElement | null = mainRef.current
    if (!main) return
    lastScrollTopRef.current = main.scrollTop
    const onScroll = (): void => {
      if (scrollMarkRafRef.current != null) return
      scrollMarkRafRef.current = requestAnimationFrame((): void => {
        scrollMarkRafRef.current = null
        const m: HTMLElement | null = mainRef.current
        if (!m) return
        const currentTop: number = m.scrollTop
        const prevTop: number = lastScrollTopRef.current
        lastScrollTopRef.current = currentTop
        if (currentTop <= prevTop) return
        if (focusScrollRafRef.current != null) return
        if (programmaticScrollRef.current) {
          // Hook-driven scrollTop shift (focus rebind). Consume the flag and
          // skip this frame; the next real user scroll will go through.
          programmaticScrollRef.current = false
          return
        }
        const headerBottom: number = stickyHeaderRef.current
          ? stickyHeaderRef.current.getBoundingClientRect().bottom
          : m.getBoundingClientRect().top
        let candidate: number = -1
        for (const vi of virtualizer.getVirtualItems()) {
          if (vi.index === allCaughtUpIndex) continue
          const el: HTMLDivElement | undefined = articleRefs.current.get(
            vi.index,
          )
          if (!el) continue
          const bottom: number = el.getBoundingClientRect().bottom
          if (bottom <= headerBottom) {
            const article = filtered[vi.index]
            if (
              article &&
              article.read_at == null &&
              !hasSessionRead(article.url)
            ) {
              scheduleRead(article.url)
            }
          } else if (candidate === -1) {
            candidate = vi.index
          }
        }
        if (candidate > focusIndex) {
          // Suppress the focus-scroll effect — the user is driving the
          // scroll manually, so snapping the new focus into view would
          // fight their input.
          skipFocusScroll.current = true
          setFocusIndex(candidate)
        }
      })
    }
    main.addEventListener("scroll", onScroll, { passive: true })
    return (): void => {
      main.removeEventListener("scroll", onScroll)
      if (scrollMarkRafRef.current != null) {
        cancelAnimationFrame(scrollMarkRafRef.current)
        scrollMarkRafRef.current = null
      }
    }
  }, [
    virtualizer,
    filtered,
    scheduleRead,
    hasSessionRead,
    focusIndex,
    allCaughtUpIndex,
    skipFocusScroll,
    mainRef,
    stickyHeaderRef,
    setFocusIndex,
  ])

  const markFocusedAsRead = useCallback(
    (index: number): void => {
      if (index < 0 || index >= filtered.length) return
      const article = filtered[index]
      if (article && article.read_at == null) {
        scheduleRead(article.url)
      }
    },
    [filtered, scheduleRead],
  )

  const onJAtEnd = useCallback((): void => {
    // Cancel any in-flight focus-scroll rAF. Without this, the still-running
    // rAF keeps writing main.scrollTop each frame and silently overrides the
    // smooth scroll-to-bottom we issue below.
    if (focusScrollRafRef.current != null) {
      cancelAnimationFrame(focusScrollRafRef.current)
      focusScrollRafRef.current = null
    }
    // Second+ consecutive j-at-end: surface the space-key hint. The first
    // press only pulses; pulseKey > 0 here means the user already saw the
    // pulse and pressed j again without moving focus.
    setCaughtUpPulseKey((key: number): number => {
      if (key > 0) {
        setCaughtUpHint(nextUnreadFeed != null ? "jump" : "end")
      }
      return key + 1
    })
    const main: HTMLElement | null = mainRef.current
    if (!main) return
    // Go through virtualizer.scrollToOffset (not main.scrollTo) so that
    // `scrollState.behavior === "smooth"` is set on the virtualizer.
    // While smooth scrolling, the virtualizer suppresses its scrollAdjust-
    // ment writes on item-size changes; bypassing the virtualizer causes
    // those writes to jump main.scrollTop mid-animation, producing a
    // visible up/down jitter instead of a clean scroll to the bottom.
    virtualizer.scrollToOffset(main.scrollHeight, { behavior: "smooth" })
  }, [virtualizer, nextUnreadFeed, mainRef])

  // When k is pressed while the focused card's top has scrolled above the
  // sticky header (e.g. after j-at-end scrolled to the bottom), re-align the
  // current card instead of moving focus to the previous one.
  const onKBeforeMove = useCallback((): boolean => {
    const main: HTMLElement | null = mainRef.current
    if (!main || focusIndex < 0) return false
    const el: HTMLDivElement | undefined = articleRefs.current.get(focusIndex)
    // Virtualized away: the card isn't in the DOM, so it's definitely not
    // aligned. Jump to it with the virtualizer and stay on this index.
    if (!el) {
      virtualizer.scrollToIndex(focusIndex, { align: "start" })
      return true
    }
    const headerBottom: number = stickyHeaderRef.current
      ? stickyHeaderRef.current.getBoundingClientRect().bottom
      : main.getBoundingClientRect().top
    const cardTop: number = el.getBoundingClientRect().top
    if (cardTop >= headerBottom - 4) return false
    const target: number =
      focusIndex === 0 ? 0 : main.scrollTop + cardTop - headerBottom
    // Shared ref means the focus-scroll effect and this realign can preempt
    // each other safely instead of leaking concurrent rAF loops.
    smoothScrollTo(main, target, { rafRef: focusScrollRafRef })
    return true
  }, [focusIndex, virtualizer, mainRef, stickyHeaderRef])

  const setRef = useCallback(
    (index: number, el: HTMLDivElement | null): void => {
      if (el) {
        articleRefs.current.set(index, el)
      } else {
        articleRefs.current.delete(index)
      }
    },
    [],
  )

  const handleCardClick = useCallback(
    (index: number): void => {
      setFocusIndex((prev: number): number => {
        if (prev !== index) markFocusedAsRead(prev)
        return index
      })
    },
    [markFocusedAsRead, setFocusIndex],
  )

  // A feed with zero unread renders no article cards, so the j-at-end flow
  // that surfaces the jump hint can't run. In the unread-only empty state,
  // derive it from whether another unread feed exists; the empty-state
  // indicator mounts the same sentinel, so `caughtUpVisible` still gates Space.
  const effectiveCaughtUpHint: CaughtUpHint =
    showUnreadOnly && filtered.length === 0
      ? nextUnreadFeed != null
        ? "jump"
        : "end"
      : caughtUpHint

  return {
    virtualizer,
    allCaughtUpIndex,
    setRef,
    handleCardClick,
    markFocusedAsRead,
    onJAtEnd,
    onKBeforeMove,
    caughtUpPulseKey,
    caughtUpHint: effectiveCaughtUpHint,
    caughtUpVisible,
    caughtUpSentinelRef,
  }
}
