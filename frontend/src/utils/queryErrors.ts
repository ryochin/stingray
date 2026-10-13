/** Names of the entries whose flag is set, in declaration order. */
export function failedLoads(flags: Record<string, boolean>): string[] {
  return Object.entries(flags)
    .filter(([, isError]: [string, boolean]): boolean => isError)
    .map(([name]: [string, boolean]): string => name)
}
