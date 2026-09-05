import '@testing-library/jest-dom/vitest'

// jsdom never actually lays elements out (every getBoundingClientRect()
// is 0x0), but @xyflow/react measures each node's DOM element via
// getBoundingClientRect() (triggered through a ResizeObserver callback)
// before it will render edges between nodes. Node positions themselves
// are always supplied explicitly by this app, never measured -- only
// the node *size* needs faking here, purely so edges render in tests.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    constructor(callback) {
      this.callback = callback
    }

    observe(target) {
      // Deferred: @xyflow/react's root wrapper registers its own DOM
      // node in the store from a *parent* effect that commits after this
      // node's *child* effect calls observe() -- firing synchronously
      // here would race ahead of that registration and be silently
      // dropped (updateNodeInternals bails out when it can't find the
      // viewport yet).
      setTimeout(() => this.callback([{ target, contentRect: target.getBoundingClientRect() }]), 0)
    }

    unobserve() {}
    disconnect() {}
  }
}

if (!Element.prototype.getBoundingClientRect.__purposeSealStub) {
  const fixedRect = () => ({ width: 150, height: 60, x: 0, y: 0, top: 0, left: 0, bottom: 60, right: 150, toJSON() {} })
  fixedRect.__purposeSealStub = true
  Element.prototype.getBoundingClientRect = fixedRect
}

// @xyflow/react reads node size via offsetWidth/offsetHeight (not
// getBoundingClientRect) before it will draw edges between nodes; jsdom
// always reports 0 for both, so nothing ever "renders" for xyflow's
// purposes without this.
Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 150 })
Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 60 })

// jsdom has no DOMMatrixReadOnly at all; @xyflow/react parses the
// viewport's CSS transform through it to read the current zoom level.
// Tests never pan/zoom, so a fixed identity-scale stub is sufficient.
if (typeof globalThis.DOMMatrixReadOnly === 'undefined') {
  globalThis.DOMMatrixReadOnly = class DOMMatrixReadOnly {
    constructor() {
      this.m22 = 1
    }
  }
}
