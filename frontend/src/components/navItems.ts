/** Header navigation entries, shared with the keyboard shortcut handler so the
    tooltip and the actual binding can never drift apart. */
export interface NavItem {
  path: string
  label: string
  shortcut?: string
}

export const NAV_ITEMS: readonly NavItem[] = [
  { path: "/", label: "Articles", shortcut: "a" },
  { path: "/feeds", label: "Feeds", shortcut: "f" },
  { path: "/filters", label: "Filters", shortcut: "t" },
]
