import type { ErrorInfo, ReactNode } from "react"
import { Component } from "react"

interface Props {
  children: ReactNode
  /** Shown in place of `children` after a render error. Defaults to an inline
   *  message with a reload action. */
  fallback?: ReactNode
}

interface State {
  error: Error | null
}

/**
 * Stops a render-time exception from blanking the whole app.
 *
 * Only catches errors thrown *while rendering* — react-query failures are
 * rejected promises and never reach this. Those keep their existing inline
 * error banners.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // The one place a render error is observable. Route logging through here
    // if the app ever gains a reporting backend.
    console.error("Unhandled render error:", error, info.componentStack)
  }

  render(): ReactNode {
    const { error } = this.state
    if (!error) return this.props.children
    if (this.props.fallback !== undefined) return this.props.fallback
    return (
      <div className="p-6 text-sm text-red-400">
        <p className="mb-2">Something went wrong while rendering this page.</p>
        <button
          type="button"
          onClick={(): void => window.location.reload()}
          className="px-3 py-1.5 rounded bg-bg-card text-text border border-border hover:text-text-heading transition-colors"
        >
          Reload
        </button>
      </div>
    )
  }
}
