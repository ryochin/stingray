import { useEffect } from "react"
import { useNavigate } from "react-router-dom"

export function useTabShortcuts() {
  const navigate = useNavigate()

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const tag = (e.target as HTMLElement).tagName
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
    return () => window.removeEventListener("keydown", handler)
  }, [navigate])
}
