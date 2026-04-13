import { Routes, Route } from "react-router-dom"
import Articles from "./routes/Articles"
import Feeds from "./routes/Feeds"

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Articles />} />
      <Route path="/feeds" element={<Feeds />} />
    </Routes>
  )
}
