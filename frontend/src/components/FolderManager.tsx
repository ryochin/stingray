import { useMutation, useQueryClient } from "@tanstack/react-query"
import type { JSX } from "react"
import { useState } from "react"
import type { Folder } from "../api/client"
import { api } from "../api/client"
import { useOptimisticReorder } from "../hooks/useOptimisticReorder"
import { useReorderDnD } from "../hooks/useReorderDnD"
import { setCardDragImage } from "../utils/dragImage"

/** Folders render sorted by `position`, not by array order, so the new order
 *  is written into `position` (index, as the backend stores it). */
function applyFolderPositions(prev: Folder[], ids: number[]): Folder[] {
  const indexById: Map<number, number> = new Map(
    ids.map((id: number, index: number): [number, number] => [id, index]),
  )
  return prev.map(
    (folder: Folder): Folder => ({
      ...folder,
      position: indexById.get(folder.id) ?? folder.position,
    }),
  )
}

function FolderManager({
  folders,
  onError,
}: {
  folders: Folder[]
  onError: (e: Error) => void
}): JSX.Element {
  const queryClient = useQueryClient()
  const [newName, setNewName] = useState<string>("")
  const [editingId, setEditingId] = useState<number | null>(null)
  const [editName, setEditName] = useState<string>("")

  const invalidate = (): void => {
    queryClient.invalidateQueries({ queryKey: ["folders"] })
    queryClient.invalidateQueries({ queryKey: ["feeds"] })
  }

  const createFolder = useMutation({
    mutationFn: (name: string) => api.createFolder(name),
    onSuccess: () => {
      invalidate()
      setNewName("")
    },
    onError,
  })
  const renameFolder = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api.renameFolder(id, name),
    onSuccess: () => {
      invalidate()
      setEditingId(null)
    },
    onError,
  })
  const deleteFolder = useMutation({
    mutationFn: api.deleteFolder,
    onSuccess: invalidate,
    onError,
  })
  const reorderFolders = useOptimisticReorder<Folder>({
    queryKey: ["folders"],
    mutationFn: (ids: number[]): Promise<void> => api.reorderFolders(ids),
    apply: applyFolderPositions,
    onError,
  })
  const { dragOverId, handlers: dnd } = useReorderDnD<Folder>({
    items: folders,
    onReorder: reorderFolders.mutate,
  })
  // A second drop while one is in flight could have its optimistic order
  // wiped by the first one's rollback or refetch, so dragging waits.
  const canDrag = (folder: Folder): boolean =>
    editingId !== folder.id && !reorderFolders.isPending

  const handleCreate = (event: React.FormEvent): void => {
    event.preventDefault()
    if (!newName.trim()) return
    createFolder.mutate(newName.trim())
  }

  const startEdit = (folder: Folder): void => {
    setEditingId(folder.id)
    setEditName(folder.name)
  }

  const commitEdit = (): void => {
    if (editingId == null || !editName.trim()) return
    renameFolder.mutate({ id: editingId, name: editName.trim() })
  }

  return (
    <div className="mb-6">
      <h3 className="text-sm font-semibold text-text-heading mb-3">Folders</h3>
      <form onSubmit={handleCreate} className="flex gap-2 mb-3">
        <div className="relative flex-1">
          <svg
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
            <line x1="12" y1="10" x2="12" y2="16" />
            <line x1="9" y1="13" x2="15" y2="13" />
          </svg>
          <input
            value={newName}
            onChange={(event: React.ChangeEvent<HTMLInputElement>): void =>
              setNewName(event.target.value)
            }
            className="w-full bg-bg-card text-text border border-border rounded pl-8 pr-2 py-1.5 text-sm"
            placeholder="New folder name"
          />
        </div>
        <button
          type="submit"
          disabled={createFolder.isPending || !newName.trim()}
          className="px-3 py-1.5 rounded bg-accent text-white text-sm hover:opacity-90 transition-opacity disabled:opacity-40"
        >
          Add
        </button>
      </form>
      {folders.length > 0 && (
        <ul className="space-y-1 list-none p-0 m-0">
          {folders.map((folder: Folder) => (
            <li
              key={folder.id}
              className={`flex items-center gap-2 p-2 bg-bg-secondary rounded border transition-colors ${
                dragOverId === folder.id
                  ? "border-accent border-solid"
                  : "border-border"
              }`}
              onDragOver={(event: React.DragEvent<HTMLLIElement>): void => {
                event.preventDefault()
                dnd.onDragOver(folder)
              }}
              onDragLeave={(): void => dnd.onDragLeave(folder)}
              onDrop={(): void => dnd.onDrop(folder)}
            >
              {/* biome-ignore lint/a11y/noStaticElementInteractions: drag handle relies on native HTML5 DnD, which has no standard ARIA role */}
              <span
                draggable={canDrag(folder)}
                onDragStart={(
                  event: React.DragEvent<HTMLSpanElement>,
                ): void => {
                  setCardDragImage(event, "li")
                  dnd.onDragStart(folder)
                }}
                onDragEnd={dnd.onDragEnd}
                className={`text-text-dim select-none inline-flex px-1 -mx-1 ${
                  canDrag(folder)
                    ? "cursor-grab active:cursor-grabbing"
                    : "cursor-default opacity-40"
                }`}
                title={canDrag(folder) ? "Drag to reorder" : undefined}
              >
                ⠿
              </span>
              {editingId === folder.id ? (
                <>
                  <input
                    value={editName}
                    onChange={(
                      event: React.ChangeEvent<HTMLInputElement>,
                    ): void => setEditName(event.target.value)}
                    onKeyDown={(
                      event: React.KeyboardEvent<HTMLInputElement>,
                    ): void => {
                      if (event.key === "Enter") commitEdit()
                      if (event.key === "Escape") setEditingId(null)
                    }}
                    className="bg-bg-card text-text border border-border rounded px-2 py-1 text-sm flex-1"
                  />
                  <button
                    type="button"
                    onClick={commitEdit}
                    className="px-2 py-1 rounded text-xs bg-accent text-white hover:opacity-90"
                  >
                    Save
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditingId(null)}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text"
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <span className="flex-1 text-sm text-text">
                    {folder.name}
                  </span>
                  <button
                    type="button"
                    onClick={(): void => startEdit(folder)}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-text-muted hover:text-text transition-colors"
                  >
                    Rename
                  </button>
                  <button
                    type="button"
                    onClick={(): void => {
                      if (
                        confirm(
                          `Delete folder "${folder.name}"? Feeds will be moved to uncategorized.`,
                        )
                      )
                        deleteFolder.mutate(folder.id)
                    }}
                    className="px-2 py-1 rounded text-xs bg-bg-card text-red-400 hover:bg-red-900 hover:text-red-200 transition-colors"
                  >
                    Delete
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default FolderManager
