import type { RefObject } from "react"
import { useEffect, useState } from "react"

/** Tracks the pixel height of `ref.current` via ResizeObserver. Re-measures
 *  on every observed resize so layout shifts (font load, wrap changes,
 *  sticky-stuck transitions, etc.) propagate to consumers immediately. */
export function useElementHeight(ref: RefObject<HTMLElement | null>): number {
  const [height, setHeight] = useState<number>(0)

  // biome-ignore lint/correctness/useExhaustiveDependencies: `ref` is stable; the effect only consults `ref.current` on mount, matching the original behaviour.
  useEffect((): (() => void) | undefined => {
    const el: HTMLElement | null = ref.current
    if (!el) return
    const update = (): void => setHeight(el.getBoundingClientRect().height)
    update()
    const observer: ResizeObserver = new ResizeObserver(update)
    observer.observe(el)
    return (): void => observer.disconnect()
  }, [])

  return height
}
