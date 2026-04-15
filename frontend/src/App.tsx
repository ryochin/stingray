import { Routes, Route } from "react-router-dom"
import Articles from "./routes/Articles"
import Feeds from "./routes/Feeds"
import Filters from "./routes/Filters"
import { useTabShortcuts } from "./hooks/useTabShortcuts"

export default function App() {
  useTabShortcuts()
  return (
    <Routes>
      <Route path="/" element={<Articles />} />
      <Route path="/feeds" element={<Feeds />} />
      <Route path="/filters" element={<Filters />} />
    </Routes>
  )
}
