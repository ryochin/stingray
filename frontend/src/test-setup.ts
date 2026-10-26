import "@testing-library/jest-dom/vitest"
import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

// happy-dom stubs Node.prototype.nodeName to return "" and overrides it in each
// subclass, whereas the spec has the one getter on Node answer for every node
// type. DOMPurify 3.4.8+ reads the getter off Node.prototype, so under happy-dom
// every element looked nameless and was stripped. Dispatch to the subclass
// getter instead. This must run before dompurify is imported, as DOMPurify
// captures the getter when it is created.
Object.defineProperty(Node.prototype, "nodeName", {
  configurable: true,
  get(this: Node): string {
    let proto: object | null = Object.getPrototypeOf(this)
    while (proto !== null && proto !== Node.prototype) {
      const getter: (() => string) | undefined =
        Object.getOwnPropertyDescriptor(proto, "nodeName")?.get
      if (getter) {
        return getter.call(this)
      }
      proto = Object.getPrototypeOf(proto)
    }
    return ""
  },
})

afterEach((): void => {
  cleanup()
})
