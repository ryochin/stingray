// Transforms `<blockquote class="twitter-tweet">` nodes (emitted by Twitter/X
// oEmbed) into self-styled cards so that we don't have to load the official
// widgets.js. Operates in-place on a parsed Document.

const TWEET_URL_RE = /https?:\/\/(?:twitter\.com|x\.com)\/[^/?#]+\/status\/\d+/
const META_RE = /[—–-]\s*(.+?)\s*\(@([A-Za-z0-9_]+)\)/

export function transformTwitterBlockquotes(doc: Document): void {
  for (const bq of Array.from(
    doc.querySelectorAll("blockquote.twitter-tweet"),
  )) {
    const card: Element | null = buildTweetCard(doc, bq)
    if (card) bq.replaceWith(card)
  }
}

function buildTweetCard(doc: Document, bq: Element): Element | null {
  const p: HTMLParagraphElement | null = bq.querySelector("p")
  if (!p) return null

  // Pick the last anchor whose href looks like a tweet status URL — that's
  // the one rendered as the date by Twitter's oEmbed snippet.
  let dateLink: Element | null = null
  for (const a of Array.from(bq.querySelectorAll("a[href]"))) {
    if (TWEET_URL_RE.test(a.getAttribute("href") ?? "")) dateLink = a
  }
  if (!dateLink) return null
  const tweetUrl: string = (dateLink.getAttribute("href") ?? "").split("?")[0]
  const dateText: string = dateLink.textContent?.trim() ?? ""

  let metaText: string = ""
  let node: Node | null = p.nextSibling
  while (node && node !== dateLink) {
    metaText += node.textContent ?? ""
    node = node.nextSibling
  }
  const match: RegExpMatchArray | null = metaText.match(META_RE)
  const author: string = match?.[1]?.trim() ?? ""
  const handle: string = match?.[2] ?? ""

  const card: HTMLDivElement = doc.createElement("div")
  card.className = "tweet-card"

  const header: HTMLDivElement = doc.createElement("div")
  header.className = "tweet-card-header"

  const icon: HTMLSpanElement = doc.createElement("span")
  icon.className = "tweet-card-icon"
  icon.textContent = "𝕏"
  header.appendChild(icon)

  if (author) {
    const authorEl: HTMLSpanElement = doc.createElement("span")
    authorEl.className = "tweet-card-author"
    authorEl.textContent = author
    header.appendChild(authorEl)
  }

  if (handle) {
    const handleEl: HTMLAnchorElement = doc.createElement("a")
    handleEl.className = "tweet-card-handle"
    handleEl.setAttribute("href", `https://x.com/${handle}`)
    handleEl.setAttribute("target", "_blank")
    handleEl.setAttribute("rel", "noopener noreferrer")
    handleEl.textContent = `@${handle}`
    header.appendChild(handleEl)
  }

  card.appendChild(header)

  const body: HTMLDivElement = doc.createElement("div")
  body.className = "tweet-card-body"
  body.innerHTML = p.innerHTML
  card.appendChild(body)

  if (dateText) {
    const dateEl: HTMLAnchorElement = doc.createElement("a")
    dateEl.className = "tweet-card-date"
    dateEl.setAttribute("href", tweetUrl)
    dateEl.setAttribute("target", "_blank")
    dateEl.setAttribute("rel", "noopener noreferrer")
    dateEl.textContent = dateText
    card.appendChild(dateEl)
  }

  return card
}
