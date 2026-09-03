# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
