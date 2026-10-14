import type { UseMutationResult } from "@tanstack/react-query"
import { useMutation, useQueryClient } from "@tanstack/react-query"

interface Options<T> {
  queryKey: readonly unknown[]
  mutationFn: (ids: number[]) => Promise<void>
  /** Writes the new order into the cached list. Feeds render in array order,
   *  folders by `position`, so each list says how its order is stored. */
  apply: (prev: T[], ids: number[]) => T[]
  onError: (err: Error) => void
}

interface Context<T> {
  prev: T[] | undefined
}

/**
 * Reorder mutation that shows the new order before the server answers. On
 * error the pre-mutation snapshot is restored; either way the list is
 * refetched afterwards.
 */
export function useOptimisticReorder<T>({
  queryKey,
  mutationFn,
  apply,
  onError,
}: Options<T>): UseMutationResult<void, Error, number[], Context<T>> {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn,
    onMutate: async (ids: number[]): Promise<Context<T>> => {
      // A refetch landing after the optimistic write would revert it.
      await queryClient.cancelQueries({ queryKey })
      const prev: T[] | undefined = queryClient.getQueryData<T[]>(queryKey)
      if (prev) queryClient.setQueryData<T[]>(queryKey, apply(prev, ids))
      return { prev }
    },
    onError: (
      err: Error,
      _ids: number[],
      ctx: Context<T> | undefined,
    ): void => {
      if (ctx?.prev) queryClient.setQueryData(queryKey, ctx.prev)
      onError(err)
    },
    onSettled: (): void => {
      queryClient.invalidateQueries({ queryKey })
    },
  })
}
