import type { JSX } from "react"
import { Route, Routes } from "react-router-dom"
import { useTabShortcuts } from "./hooks/useTabShortcuts"
import Articles from "./routes/Articles"
import Feeds from "./routes/Feeds"
import Filters from "./routes/Filters"

export default function App(): JSX.Element {
  useTabShortcuts()
  return (
    <Routes>
      <Route path="/" element={<Articles />} />
      <Route path="/feeds" element={<Feeds />} />
      <Route path="/filters" element={<Filters />} />
    </Routes>
  )
}
