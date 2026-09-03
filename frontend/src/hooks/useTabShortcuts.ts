import { useEffect } from "react"
import { useNavigate } from "react-router-dom"
import type { NavItem } from "../components/navItems"
import { NAV_ITEMS } from "../components/navItems"
import { isModalOpen, isShortcutSuppressed } from "../utils/keyboardGuards"

export function useTabShortcuts(): void {
  const navigate = useNavigate()

  useEffect((): (() => void) => {
    const handler = (e: KeyboardEvent): void => {
      // Navigation has no business running behind an open modal, nor on an
      // event someone else already handled — so unlike useArticleKeyboard,
      // this hook has nothing to run before the guards.
      if (isShortcutSuppressed(e) || e.defaultPrevented || isModalOpen()) return

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
