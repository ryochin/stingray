declare module "prismjs/components/prism-core" {
  export const languages: Record<string, unknown>
  export function highlight(code: string, grammar: unknown, language: string): string
}

declare module "prismjs/components/prism-clike"
declare module "prismjs/components/prism-javascript"
declare module "prismjs/components/prism-json"
