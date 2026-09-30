import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '../src/styles/global.css'), 'utf8')

describe('shared UI design rules', () => {
  it('keeps literal colors, shadows and corner values inside design tokens only', () => {
    const rules = css.replace(/:root\s*\{[^}]*\}/g, '')
    expect(rules).not.toMatch(/#[\da-f]{3,8}\b|rgba?\(/i)
    for (const value of rules.matchAll(/(?:border-radius|box-shadow)\s*:\s*([^;]+);/g)) {
      expect(value[1]).toMatch(/^var\(--ndr-/)
    }
    expect(css).toContain('color-scheme: light;')
    expect(css).toContain('color-scheme: dark;')
    for (const token of ['danger', 'warning', 'st-queued', 'st-roster', 'st-dialogue', 'st-processed', 'st-failed', 'on-solid']) {
      expect(css.split(`--ndr-${token}:`)).toHaveLength(3)
    }
  })

  it('uses shared controls, labels and book navigation instead of old page-specific styles', () => {
    expect(css).not.toContain('.ndr-preview-nav')
    expect(css).toContain('.ndr-book-nav a')
    expect(css).toContain('button,\n.ndr-button,\n.ndr-book-nav a {')
    expect(css).toContain('.ndr-book-nav a,\n.ndr-book-nav button {\n  padding: 6px 12px;')
    expect(css).toContain('.ndr-field,')
    expect(css).toContain("input[type='password'],")
    expect(css).toContain("input[type='number'],")
    expect(css).not.toMatch(/\.ndr-(?:profile-form|character-card|budget-form|correction-form)\s+(?:input|select|textarea)\s*\{/)
  })
})
