import type { RefObject } from "react"
import { useEffect, useState } from "react"

/** Reports whether a sticky header has become "stuck" to the top of its
 *  scroll root. Place a zero-height sentinel immediately above the sticky
 *  wrapper; the sentinel stops intersecting the root the moment the header
 *  pins, which is what flips the returned flag. */
export function useStickyHeader(
  sentinelRef: RefObject<HTMLElement | null>,
  scrollRootRef: RefObject<HTMLElement | null>,
): boolean {
  const [isStuck, setIsStuck] = useState<boolean>(false)

  // biome-ignore lint/correctness/useExhaustiveDependencies: refs are stable and only consulted once on mount, matching the original effect's intent.
  useEffect((): (() => void) | undefined => {
    const sentinel: HTMLElement | null = sentinelRef.current
    const root: HTMLElement | null = scrollRootRef.current
    if (!sentinel || !root) return
    const observer: IntersectionObserver = new IntersectionObserver(
      ([entry]: IntersectionObserverEntry[]): void =>
        setIsStuck(!entry.isIntersecting),
      { root, threshold: 0 },
    )
    observer.observe(sentinel)
    return (): void => observer.disconnect()
  }, [])

  return isStuck
}
