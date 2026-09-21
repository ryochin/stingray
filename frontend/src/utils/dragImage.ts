import type { DragEvent } from "react"

/**
 * Use the whole card/row (found via `selector`) as the native DnD drag image,
 * so dragging by the small handle still previews the full item under the
 * cursor. The image offset keeps the grab point aligned with the pointer.
 */
export function setCardDragImage(event: DragEvent, selector: string): void {
  const card: Element | null = event.currentTarget.closest(selector)
  if (!card) return
  const rect: DOMRect = card.getBoundingClientRect()
  event.dataTransfer.setDragImage(
    card,
    event.clientX - rect.left,
    event.clientY - rect.top,
  )
}
