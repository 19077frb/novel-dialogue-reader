import '@testing-library/jest-dom/vitest'

// JSDOM has no top layer. Browser E2E verifies native inert/focus/scroll behavior.
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
}
