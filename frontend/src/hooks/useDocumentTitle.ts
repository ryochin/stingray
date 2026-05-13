import { useEffect, useRef } from "react"

// Syncs `document.title` to the given string while the component is mounted,
// and restores whatever the title was at mount time when it unmounts.
//
// Two effects are used on purpose:
//   - mount/unmount-only: capture the original title and restore on unmount.
//   - value-driven: apply the current title on every change, without a
//     cleanup that would flash the original between consecutive updates.
//
// Single-owner assumption: only one component should call this hook at a
// time. Multiple concurrent users would conflict — each captures its own
// "original" and unconditionally restores on unmount, so a later-unmounting
// instance would overwrite an earlier-mounted one's title. Today the only
// caller is the Articles route, and React Router mounts one route at a
// time, so this is safe. If reuse expands, switch to a stack-based scheme.
export function useDocumentTitle(title: string): void {
  const originalRef = useRef<string | null>(null)

  useEffect((): (() => void) => {
    originalRef.current = document.title
    return (): void => {
      if (originalRef.current !== null) {
        document.title = originalRef.current
      }
    }
  }, [])

  useEffect((): void => {
    document.title = title
  }, [title])
}
