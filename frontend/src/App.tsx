import type { JSX } from "react"
import { Route, Routes, useLocation } from "react-router-dom"
import ErrorBoundary from "./components/ErrorBoundary"
import { useTabShortcuts } from "./hooks/useTabShortcuts"
import Articles from "./routes/Articles"
import Feeds from "./routes/Feeds"
import Filters from "./routes/Filters"

export default function App(): JSX.Element {
  useTabShortcuts()
  const { pathname } = useLocation()
  return (
    // Inside the router so the fallback stays on a valid route; one boundary
    // covers all three pages.
    //
    // Keyed on the path so leaving a broken page clears the error. The tab
    // shortcuts live outside this boundary and keep working while the
    // fallback is up — without the key the URL would change with nothing on
    // screen moving, which looks like the app has frozen.
    <ErrorBoundary key={pathname}>
      <Routes>
        <Route path="/" element={<Articles />} />
        <Route path="/feeds" element={<Feeds />} />
        <Route path="/filters" element={<Filters />} />
      </Routes>
    </ErrorBoundary>
  )
}
