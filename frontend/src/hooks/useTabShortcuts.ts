import { useEffect } from "react"
import { useNavigate } from "react-router-dom"

export function useTabShortcuts(): void {
  const navigate = useNavigate()

  useEffect((): () => void => {
    const handler = (e: KeyboardEvent): void => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag: string = (e.target as HTMLElement).tagName
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return

      if (e.key === "a") {
        e.preventDefault()
        navigate("/")
      } else if (e.key === "f") {
        e.preventDefault()
        navigate("/feeds")
      }
    }

    window.addEventListener("keydown", handler)
    return (): void => window.removeEventListener("keydown", handler)
  }, [navigate])
}
