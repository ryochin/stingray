import { useEffect } from "react"
import { useNavigate } from "react-router-dom"
import type { NavItem } from "../components/navItems"
import { NAV_ITEMS } from "../components/navItems"

export function useTabShortcuts(): void {
  const navigate = useNavigate()

  useEffect((): (() => void) => {
    const handler = (e: KeyboardEvent): void => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      // Mid-IME-composition keystrokes belong to the input method, and an
      // event another handler already claimed is not ours to act on.
      if (e.isComposing || e.defaultPrevented) return
      const el = e.target as HTMLElement | null
      const tag: string = el?.tagName ?? ""
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return
      if (el?.isContentEditable) return
      // A modal (e.g. the ShortcutsHelp cheatsheet) owns the keyboard while
      // open. It only handles Enter/Space/Escape itself, so without this
      // guard every other key would bubble to window and navigate behind it —
      // exactly what a user trying out a key from the cheatsheet would hit.
      if (document.querySelector('[aria-modal="true"]')) return

      const target: NavItem | undefined = NAV_ITEMS.find(
        (item: NavItem): boolean => item.shortcut === e.key,
      )
      if (target) {
        e.preventDefault()
        navigate(target.path)
      }
    }

    window.addEventListener("keydown", handler)
    return (): void => window.removeEventListener("keydown", handler)
  }, [navigate])
}
