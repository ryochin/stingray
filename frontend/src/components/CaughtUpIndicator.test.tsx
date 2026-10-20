/**
 * Guards the caught-up sentinel: `<Key>` hint tokens and the in-place
 * pulse driven through the Web Animations API.
 */

import { cleanup, render, screen } from "@testing-library/react"
import { createRef, type RefObject } from "react"
import type { Mock } from "vitest"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import CaughtUpIndicator from "./CaughtUpIndicator"

interface FakeAnimation {
  cancel: Mock
}

// happy-dom has no `Element.prototype.animate`; install a fake per test
// that hands out a distinct Animation each call.
let animate: Mock
let animations: FakeAnimation[]

beforeEach((): void => {
  animations = []
  animate = vi.fn(function (this: Element): FakeAnimation {
    const animation: FakeAnimation = { cancel: vi.fn() }
    animations.push(animation)
    return animation
  })
  Object.defineProperty(Element.prototype, "animate", {
    value: animate,
    configurable: true,
    writable: true,
  })
})

afterEach((): void => {
  cleanup()
  delete (Element.prototype as { animate?: unknown }).animate
})

function kbdTexts(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll("kbd")).map(
    (el: Element): string => el.textContent ?? "",
  )
}

describe("CaughtUpIndicator hint", (): void => {
  it("renders the label", (): void => {
    render(<CaughtUpIndicator label="All caught up" />)
    expect(screen.getByText("All caught up")).toBeTruthy()
  })

  it("renders known key names as <kbd>", (): void => {
    const { container } = render(
      <CaughtUpIndicator
        label="x"
        subLabel="Press <Space>, <Enter> or <Esc> to go"
      />,
    )
    expect(kbdTexts(container)).toEqual(["Space", "Enter", "Esc"])
    const hint: Element | null =
      container.querySelector("kbd")?.parentElement ?? null
    expect(hint?.textContent).toBe("Press Space, Enter or Esc to go")
  })

  it("keeps unknown key names verbatim, brackets included", (): void => {
    const { container } = render(
      <CaughtUpIndicator label="x" subLabel="Press <Spcae> to go" />,
    )
    expect(kbdTexts(container)).toEqual([])
    expect(screen.getByText("Press <Spcae> to go")).toBeTruthy()
  })

  it("fades the hint in only when a sub-label is present", (): void => {
    const { container, rerender } = render(<CaughtUpIndicator label="x" />)
    const hint = (): Element =>
      container.firstElementChild?.lastElementChild as Element
    expect(hint().className).not.toContain("animate-caught-up-hint-fade")
    expect(hint().className).toContain("min-h-[1rem]")

    rerender(<CaughtUpIndicator label="x" subLabel="Press <Space>" />)
    expect(hint().className).toContain("animate-caught-up-hint-fade")
  })

  it("defaults the inner color to muted and lets className replace it", (): void => {
    const { container, rerender } = render(<CaughtUpIndicator label="x" />)
    const inner = (): Element =>
      container.firstElementChild?.firstElementChild as Element
    expect(inner().className).toContain("text-text-muted")

    rerender(<CaughtUpIndicator label="x" className="text-text" />)
    expect(inner().className).toContain("text-text")
    expect(inner().className).not.toContain("text-text-muted")
  })

  it("forwards ref to the outer wrapper", (): void => {
    const ref: RefObject<HTMLDivElement | null> = createRef<HTMLDivElement>()
    const { container } = render(<CaughtUpIndicator label="x" ref={ref} />)
    expect(ref.current).toBe(container.firstElementChild)
  })
})

describe("CaughtUpIndicator pulse", (): void => {
  it.each([
    ["undefined", undefined],
    ["0", 0],
  ])("does not pulse when pulseKey is %s", (_: string, pulseKey:
    | number
    | undefined): void => {
    render(<CaughtUpIndicator label="x" pulseKey={pulseKey} />)
    expect(animate).not.toHaveBeenCalled()
  })

  it("pulses the inner group, not the outer wrapper", (): void => {
    const { container } = render(<CaughtUpIndicator label="x" pulseKey={1} />)
    expect(animate).toHaveBeenCalledTimes(1)
    expect(animate.mock.contexts[0]).toBe(
      container.firstElementChild?.firstElementChild,
    )
  })

  it("cancels the previous pulse before starting the next", (): void => {
    const { rerender } = render(<CaughtUpIndicator label="x" pulseKey={1} />)
    rerender(<CaughtUpIndicator label="x" pulseKey={2} />)

    expect(animate).toHaveBeenCalledTimes(2)
    expect(animations[0].cancel.mock.invocationCallOrder[0]).toBeLessThan(
      animate.mock.invocationCallOrder[1],
    )
    expect(animations[1].cancel).not.toHaveBeenCalled()
  })

  it("cancels the last pulse on unmount", (): void => {
    const { unmount } = render(<CaughtUpIndicator label="x" pulseKey={1} />)
    unmount()
    expect(animations[0].cancel).toHaveBeenCalled()
  })

  it("does not replay the pulse when only other props change", (): void => {
    const { rerender } = render(<CaughtUpIndicator label="x" pulseKey={1} />)
    rerender(<CaughtUpIndicator label="y" pulseKey={1} />)
    expect(animate).toHaveBeenCalledTimes(1)
  })
})
