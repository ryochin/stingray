# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Entries are dated rather than versioned: there are no version numbers or releases.

## 2026-10-26

Range: (`99bf035`, 2026-09-14) – (`750d507`, 2026-10-26) / 56 commits

### Upgrade notes

- **Rebuild the images and recreate the containers** (`docker compose up -d --build`). Log rotation only applies to recreated containers, and the supercronic checksum check is baked into the image
- **Feeds on your own network now need `fetch.allow_private`.** Feed fetches refuse loopback, LAN and other non-public addresses (see Security below). List such hosts in `config.yml` as a hostname, IP address or CIDR range
- **Startup stops if two feeds share a URL.** A new unique index on feed URLs will not apply over existing duplicates; the error lists them so you can decide which copy to delete
- **Startup also stops if the database schema is newer than the code**, instead of running against a schema the code may not understand. Run the matching or a newer version

### Filter rules can match the article URL

Some noise is easiest to name by where it lives rather than what it says: a `/sponsored/` path, an ads subdomain. URL is a target of its own, so a body keyword never hits a URL that merely contains it. It sees the stored URL with tracking parameters already removed, the same one the article opens. The pattern syntax is unchanged, so `/pr/` is still a regex for `pr`; the README explains how to match a path segment. The existing **Both** target is now labeled **Title & Body**; stored rules and exported JSON keep working ([`c6af54c`](https://github.com/ryochin/stingray/commit/c6af54c))

### Security

- **Feed fetches refuse private destinations and oversized bodies.** Feed URLs come in through an unauthenticated API, and were fetched with redirects followed and no checks, so anyone who could reach the UI could make the server request cloud metadata, the database or any host on the LAN. Every fetch of a user-supplied URL now speaks only http(s), refuses non-public addresses on every redirect hop, and caps the decoded body (`fetch.max_response_bytes`, 10 MiB). Feed XML in the image pass is parsed with defusedxml. DNS rebinding between the check and the connect is not prevented ([`efb2b71`](https://github.com/ryochin/stingray/commit/efb2b71))
- **`file://` feed URLs are confined to the debug feeds directory**; absolute paths and `../` could read any file the server could open ([`dd839ce`](https://github.com/ryochin/stingray/commit/dd839ce))
- **The SPA fallback could serve files from a sibling directory** whose name starts with `dist`, such as `dist-old`, because the path check compared strings ([`53cd901`](https://github.com/ryochin/stingray/commit/53cd901))
- The supercronic download in the fetcher image is verified against the upstream checksum ([`fe18f0b`](https://github.com/ryochin/stingray/commit/fe18f0b))

### Fixed

- **Unread articles older than the newest 10,000 never appeared.** Unread mode picked unread rows out of the newest 10,000 articles on the client, so anything outside that window (about 45 days at current volume) was invisible. The backend now selects unread articles itself, while keeping recent reads in the response so an article does not vanish while you are reading it. The unread response also shrank from 30 MB to 3.6 MB ([`9089a82`](https://github.com/ryochin/stingray/commit/9089a82))
- **Relative links and images in article bodies pointed at Stingray itself.** URLs are resolved against the item link at ingest, including image map areas, table backgrounds and `cite`. Body links open in a new tab, clicking one no longer marks the article read, and <kbd>Enter</kbd> on a focused link opens that link rather than the article, while <kbd>j</kbd>, <kbd>k</kbd>, <kbd>m</kbd> and <kbd>?</kbd> keep working. Existing articles are repaired when they are fetched again ([`5db0c81`](https://github.com/ryochin/stingray/commit/5db0c81), [`f92cd78`](https://github.com/ryochin/stingray/commit/f92cd78))
- **An article marked unread kept being subtracted from the sidebar count** and could not be marked read again until the selection changed ([`1c2ce02`](https://github.com/ryochin/stingray/commit/1c2ce02))
- **Failed loads were silent.** Only the feeds and articles queries reported errors; a failed folders, stats, filters or status request left stale or empty data that looked real. Each screen now says what it failed to load, and the line clears on the next successful poll ([`3e2b5d2`](https://github.com/ryochin/stingray/commit/3e2b5d2))
- **Folder reordering failed silently** and kept the dropped order on screen. A failure now rolls the order back and shows an error. The folder list also stopped lighting up when a feed was dragged past it ([`ff87855`](https://github.com/ryochin/stingray/commit/ff87855), [`de614c8`](https://github.com/ryochin/stingray/commit/de614c8))
- **Refreshing a single feed behaved differently from the scheduled fetch**: rules without an item selector failed with a `KeyError` instead of being marked degraded, `max_items_per_feed` was ignored, ISO dates without an offset were stored 9 hours off, and a custom `cache_dir` was ignored. Both paths now share the same fetch and health classification ([`5cc55a9`](https://github.com/ryochin/stingray/commit/5cc55a9), [`4fd4a70`](https://github.com/ryochin/stingray/commit/4fd4a70), [`3cdc85f`](https://github.com/ryochin/stingray/commit/3cdc85f))
- **The refresh and the background summarizer could send the same articles to Ollama twice.** They now share a database lock. Pending articles of disabled feeds no longer fill the summarizer's batch ([`58b38bb`](https://github.com/ryochin/stingray/commit/58b38bb))
- **A rendering error blanked the whole screen.** An error boundary now shows a message with a **Reload** button and resets when you switch tabs; a rules editor that fails to load no longer takes the Feeds screen down with it ([`1cd1507`](https://github.com/ryochin/stingray/commit/1cd1507))
- **Restarting the web container mid-refresh left the refresh showing as running** until the next scheduled run ([`ea0604a`](https://github.com/ryochin/stingray/commit/ea0604a))
- The refresh lock no longer holds a transaction open for the whole refresh, which a database idle timeout would have ended, silently dropping the lock ([`8aa8df7`](https://github.com/ryochin/stingray/commit/8aa8df7))
- OPML import:
  - an error partway through no longer leaves a half-imported list; the import commits all at once or not at all ([`dcbe35d`](https://github.com/ryochin/stingray/commit/dcbe35d))
  - invalid extraction rules fall back to an unconfigured web feed instead of failing on every fetch ([`b8e9324`](https://github.com/ryochin/stingray/commit/b8e9324))
  - folder names are trimmed, so a padded name matches the existing folder, and a blank name becomes `Unnamed` ([`add6412`](https://github.com/ryochin/stingray/commit/add6412))
- Duplicates are rejected: the same feed URL twice (409, or counted as skipped on import), and filter rules that match the same articles, such as `apple` and ` Apple ` ([`7da1b9e`](https://github.com/ryochin/stingray/commit/7da1b9e), [`5c126d5`](https://github.com/ryochin/stingray/commit/5c126d5))
- Folders: blank names are rejected with 422 on create and rename, and a feed added from a discovered candidate goes into the folder chosen in the form ([`4fa11e2`](https://github.com/ryochin/stingray/commit/4fa11e2), [`9880416`](https://github.com/ryochin/stingray/commit/9880416))
- The `site_url` backfill during a refresh skips web feeds, whose page it re-fetched every time for nothing, and logs failures instead of swallowing them ([`f80e707`](https://github.com/ryochin/stingray/commit/f80e707), [`6dd1646`](https://github.com/ryochin/stingray/commit/6dd1646))

### Performance

- **Slow work no longer blocks the web server.** Database, cache and Ollama calls in request handlers ran on the event loop, so a slow Ollama or a large import froze every other request. They now run in worker threads, with a test that fails if a blocking call creeps back ([`5537121`](https://github.com/ryochin/stingray/commit/5537121), [`53cd901`](https://github.com/ryochin/stingray/commit/53cd901), [`3b1c7b7`](https://github.com/ryochin/stingray/commit/3b1c7b7), [`2045f88`](https://github.com/ryochin/stingray/commit/2045f88))
- **Keyword filters are grouped into one match per column.** The sidebar stats, polled every 15 seconds, went from 1.25 s to 0.62 s with 38k articles and 62 rules, and the 3-day list from 0.65 s to 0.02 s ([`9cd9e7f`](https://github.com/ryochin/stingray/commit/9cd9e7f))
- The schema is no longer re-applied on every web start and every fetcher run; a `schema_version` table records how far the database is ([`401bfa8`](https://github.com/ryochin/stingray/commit/401bfa8))

### Operations

- Container logs rotate at 3 × 10 MB per service, and warnings and errors are prefixed with `WARNING:` / `ERROR:` when color is off ([`750d507`](https://github.com/ryochin/stingray/commit/750d507))
- New tasks: `task backup` (a database dump written atomically), `task typecheck` and `task coverage` ([`750d507`](https://github.com/ryochin/stingray/commit/750d507), [`b72d9f7`](https://github.com/ryochin/stingray/commit/b72d9f7))
- The backend tests no longer run `docker compose up -d`, which could recreate the live containers, and refuse to run against a database not named `*_test` ([`750d507`](https://github.com/ryochin/stingray/commit/750d507), [`188d793`](https://github.com/ryochin/stingray/commit/188d793))

### Development

- CI is split into backend and frontend workflows by path, and builds and smoke-tests the Docker image ([`1990b6e`](https://github.com/ryochin/stingray/commit/1990b6e), [`63761ce`](https://github.com/ryochin/stingray/commit/63761ce))
- The backend is linted and formatted with ruff, and the feed, scraper and summarizer modules are type-checked under pyright strict ([`c5ff936`](https://github.com/ryochin/stingray/commit/c5ff936), [`361867c`](https://github.com/ryochin/stingray/commit/361867c), [`8962f5b`](https://github.com/ryochin/stingray/commit/8962f5b), [`19853fa`](https://github.com/ryochin/stingray/commit/19853fa))
- The Feeds screen was split into smaller components ([`1cd1507`](https://github.com/ryochin/stingray/commit/1cd1507))
- Test coverage rose from 91% to 96% on the backend and from 81% to 90% of lines on the frontend, each new test checked by breaking the code it guards

## 2026-09-14

Range: (`3e1228b`, 2026-09-03) – (`99bf035`, 2026-09-14) / 6 commits

### Fixed

- **The scheduler could never deliver its shortest fetch interval.** `schedule_next_at` ceils the next fetch to a `CRON_TICK_MIN` boundary so a feed lands on a tick rather than just after one, but cron fired every 15 minutes while that constant was 10. The two grids met only at :00 and :30, so a feed in the smallest bucket came back after 15 or 30 minutes instead of the 10 that `MIN_INTERVAL_MIN` advertises — the change that lowered the bucket from 15 to 10 moved the constant and left the crontab behind. A test now reads the crontab and compares all five cron fields against the constant, since nothing else tied the deployed file to the value it has to track. Rebuild the fetcher image to pick this up, as the crontab is baked in at build time ([`99bf035`](https://github.com/ryochin/stingray/commit/99bf035))
- **An article could swallow the rest of its own body into an editable box.** A feed entry wrote `textarea` as a bare unescaped tag in its prose; that element's content model is RCDATA, so the parser handed it everything that followed and the whole article rendered inside a control that reads as an embedded iframe from the outside. DOMPurify does not cover this on its own — it keeps `textarea`, and `FORBID_TAGS` removes only the outermost forbidden element of each subtree, so `select`/`option`, `form`/`input` and `fieldset`/`legend` all survive a sanitize pass. `unwrapFormControls` now does the whole job in a single walk, unwrapping rather than removing so the swallowed prose is rescued instead of deleted along with its container. DOMPurify still owns XSS; this pass is display hygiene only ([`f86a869`](https://github.com/ryochin/stingray/commit/f86a869))

### Changed

- **The article list opens on the last 3 days** rather than the entire history. The time filter only takes effect once you leave unread-only mode, so the old `all` default made that switch pull everything in one query. A wider range chosen per session still persists in `sessionStorage`, and the empty state now distinguishes a bounded range from a feed that genuinely has nothing ([`5a0c819`](https://github.com/ryochin/stingray/commit/5a0c819))

### Documentation

The README was checked line by line against the source. Several claims had drifted away from the code, and two shipped features had never been written down at all.

- **Corrected**: extraction rules are rejected when you save them, not at fetch time; filters match Title or Both rather than title or body; <kbd>Space</kbd> jumps to the next unread feed only while the caught-up hint is showing; drag and drop reorders folders as well as the feeds inside them; the age cutoff applies to the scheduled crawl and the bulk refresh but not to a single-feed refresh ([`ded2a0d`](https://github.com/ryochin/stingray/commit/ded2a0d))
- **Newly documented**: the LLM selector inference behind **✨ Infer with LLM**, which had left hand-written selectors looking like the only way to follow a page with no feed; the article time range, whose new 3-day default otherwise makes articles look lost; feed health labels, mark-all-unread, the unread/all toggle, and the `user_agent` and `selector_inference` keys in `config.yml` ([`ded2a0d`](https://github.com/ryochin/stingray/commit/ded2a0d))
- A CI badge, and the `git clone` step the quick start never had ([`c54a12c`](https://github.com/ryochin/stingray/commit/c54a12c))

## 2026-09-03

Range: (`d8f3d25`, 2026-08-20) – (`d3d123d`, 2026-09-03) / 7 commits

### Keyboard shortcuts

The tab bindings had drifted across three surfaces — hardcoded in the key handler, hand-written again in the cheatsheet, and missing from the nav itself. They now come from one table, and no key acts on the article list while the help overlay is up.

- **The header nav, the key handler and the `?` cheatsheet all derive from `NAV_ITEMS`**, so a binding and its documentation cannot disagree. Each nav link advertises its key through a tooltip and `aria-keyshortcuts` ([`6eef723`](https://github.com/ryochin/stingray/commit/6eef723))
- **Filters gained a binding**: <kbd>t</kbd>, the remaining letter of its label now that <kbd>f</kbd> belongs to Feeds ([`6eef723`](https://github.com/ryochin/stingray/commit/6eef723))
- **Global shortcuts no longer reach the UI behind an open modal** — the help overlay handles only <kbd>Enter</kbd>/<kbd>Space</kbd>/<kbd>Esc</kbd> itself, so every other key bubbled through and drove the article list underneath. Trying out a key read off the cheatsheet is the natural way to use it, and <kbd>Shift</kbd>+<kbd>A</kbd> marking everything read from there was the worst case. `utils/keyboardGuards` now answers "is this text entry" and "may I touch what is behind the dialog" for both window handlers, with <kbd>?</kbd> and <kbd>Esc</kbd> running between the two so the overlay keeps its exits. The mark-all-read dropdown also ignores <kbd>Esc</kbd> while a modal is open, so it no longer collapses on the same keypress that closes the overlay ([`d3d123d`](https://github.com/ryochin/stingray/commit/d3d123d))
- The article handler picked up the IME-composition and `contenteditable` guards it never had ([`d3d123d`](https://github.com/ryochin/stingray/commit/d3d123d))

### Fixed

- **<kbd>Space</kbd> no longer goes dead under the prompt telling you to press it** — the hint below "All caught up" froze its jump/end variant when a second j-at-end armed it. If the next unread feed disappeared afterwards, the sub-text kept advertising the jump while <kbd>Space</kbd> was still being `preventDefault`-ed, killing the native scroll too, only for the jump to be refused. Nothing moved, so pressing again never recovered. The variant is now derived on every render, so the prompt always matches what <kbd>Space</kbd> will do. Sentinel visibility is also seeded synchronously on ref attach, since an `IntersectionObserver` reports only asynchronously ([`6778381`](https://github.com/ryochin/stingray/commit/6778381))

### Changed

- Article titles drop from 16pt to 15pt ([`09b28a2`](https://github.com/ryochin/stingray/commit/09b28a2))
- Remaining Japanese assumptions are out of the repository: the CHANGELOG is rewritten in English, test fixtures use English placeholders, and the package description no longer describes a Japanese-only reader ([`d8f3d25`](https://github.com/ryochin/stingray/commit/d8f3d25), [`e36bc8b`](https://github.com/ryochin/stingray/commit/e36bc8b), [`0784892`](https://github.com/ryochin/stingray/commit/0784892))

## 2026-08-20

Range: (`7efde57`, 2026-07-07) – (`70ca7ed`, 2026-08-20) / 4 commits

### Locale-neutral defaults

Japanese assumptions were baked into three unrelated layers — the native language, the container timezone and the timestamp formatting. The defaults are now neutral and stay configurable, so cloning the repository from outside Japan no longer requires undoing anything.

- **`native_lang` no longer defaults to Japanese** — backend callers pass `native_lang` explicitly as a keyword argument, which turns a forgotten config value into a type error rather than silent Japanese output. `AppConfig` is the single place that holds the default, and it is now `"en"` ([`0597633`](https://github.com/ryochin/stingray/commit/0597633))
- **`should_translate` stops assuming that an undetectable feed is foreign** — it translates only when the native language is itself detectable. English has no script or TLD of its own, so the old rule would have queued every untagged feed for English-to-English translation forever, while the opposite mistake costs one toggle in the feeds view ([`0597633`](https://github.com/ryochin/stingray/commit/0597633))
- Containers read `TZ` from the environment and default to UTC, which only changes how log timestamps render since stored data was already `TIMESTAMPTZ`. The frontend formatters drop the fixed `Asia/Tokyo` zone and follow the browser instead ([`0597633`](https://github.com/ryochin/stingray/commit/0597633))
- `config.yml` becomes `config.yml.example` and is gitignored, so local settings survive a pull ([`0597633`](https://github.com/ryochin/stingray/commit/0597633))

### Fixed

- **Bulk read-state updates are scoped to the sidebar selection** — selecting a folder and choosing "Mark all as read" marked every article in the database rather than the ones in that folder. The read-all and unread-all endpoints now accept `folder_id` and resolve it through a subquery on feeds, and the API client takes the `Selection` value directly instead of an optional feed id, so the request scope always matches what the sidebar shows. "Mark all as unread" had the same scoping bug, and the <kbd>Shift</kbd>+<kbd>A</kbd> shortcut inherited it through the mark-all-read mutation ([`9fb4815`](https://github.com/ryochin/stingray/commit/9fb4815))

### Changed

- The README is split into an English version (`README.md`, canonical) and a Japanese one (`README.ja.md`), with a language selector at the top of both and separate screenshots per language ([`70ca7ed`](https://github.com/ryochin/stingray/commit/70ca7ed))
- Feed and folder input fields gained leading icons ([`db1e15b`](https://github.com/ryochin/stingray/commit/db1e15b))

## 2026-07-07

Range: (`98cfd0b`, 2026-06-21) – (`e7e7719`, 2026-07-07) / 4 commits

### Feed health state (Stale vs. Error)

Feed status is now cleanly separated into "degraded" (stale cache and similar) and actual failure, and the two are rendered differently in the list.

- **Persist a `health` state and expose it via the API** — feeds gained a `health` column (`ok` | `degraded` | `failing`). Degraded states (stale-cache / web-norules) render as a yellow "Stale" warning and real failures as a red "Error". This replaces the previous frontend heuristic (`consecutive_failures === 0 && last_error`), which mislabelled a failed manual fetch as "Stale" ([`2e05dce`](https://github.com/ryochin/stingray/commit/2e05dce))
- `record_feed_attempt` / `update_feed_fetch_status` classify each outcome into a health state, and the semantics of `last_fetched_at` are unified across paths. Scheduled and manual runs share the same stale-cache diagnosis. The `!!!` badge and the error filter stay on `consecutive_failures` as a severity axis ([`2e05dce`](https://github.com/ryochin/stingray/commit/2e05dce))
- Groundwork on the frontend for distinguishing stale-cache feeds from failures ([`64e28c0`](https://github.com/ryochin/stingray/commit/64e28c0))

### Browser User-Agent for feed and page fetches

- **Send a browser User-Agent** — some sites have a WAF that rejects non-browser agents with a 403 (for example brevis.exblog.jp). Feed and page fetches now send a browser-like User-Agent, configurable in `config.yml` ([`2297572`](https://github.com/ryochin/stingray/commit/2297572))

### Fixed

- Sanitize UTF-8 artifacts left by the LLM byte fallback ([`e7e7719`](https://github.com/ryochin/stingray/commit/e7e7719))
