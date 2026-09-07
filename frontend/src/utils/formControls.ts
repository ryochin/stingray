// Feed bodies sometimes mention tag names in prose without escaping them
// (e.g. a guide writing "press Enter in a <textarea>"). The HTML parser turns
// those into real form controls, and a bare `<textarea>` is the worst case:
// its content model is RCDATA, so it swallows the rest of the article as its
// own value and renders it inside an editable box. Unwrap every form control
// back into its children so the prose reads as prose.
//
// This runs on the re-parsed Document rather than through DOMPurify's
// `FORBID_TAGS`, because DOMPurify only drops the outermost forbidden element
// per subtree. Under KEEP_CONTENT it deep-clones the children and inserts them
// as the removed node's next siblings, while its single NodeIterator walks on
// from the original (now detached) subtree — so the clones are never visited
// and nested controls (`<select><option>`, `<form><input>`) survive a sanitize
// pass. Walking a static NodeList here has no such blind spot.
const FORM_CONTROL_SELECTOR: string = [
  "button",
  "datalist",
  "fieldset",
  "form",
  "input",
  "label",
  "legend",
  "meter",
  "optgroup",
  "option",
  "output",
  "progress",
  "select",
  "textarea",
].join(",")

export function unwrapFormControls(doc: Document): void {
  // Unwrap rather than remove: the swallowed article body is the element's
  // child content, so dropping the subtree would delete the very text this is
  // meant to rescue. Void elements like `<input>` have no children and so
  // simply disappear.
  for (const element of Array.from(
    doc.querySelectorAll(FORM_CONTROL_SELECTOR),
  )) {
    element.replaceWith(...Array.from(element.childNodes))
  }
}
