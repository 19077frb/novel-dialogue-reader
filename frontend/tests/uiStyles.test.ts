import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const css = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '../src/styles/global.css'), 'utf8')

describe('shared UI design rules', () => {
  it('keeps collapsed content hidden and adapts block summaries with theme tokens', () => {
    expect(css).toMatch(/\.ndr-collapsible-block > \[hidden\]\s*\{[^}]*display:\s*none/)
    expect(css).toMatch(/\.ndr-collapsible-summary\s*\{[^}]*color:\s*var\(--ndr-muted\)/)
    expect(css).toMatch(/\.ndr-collapsible-heading\s*\{[^}]*flex-wrap:\s*nowrap/)
    expect(css).toMatch(/\.ndr-collapse-toggle\s*\{[^}]*flex:\s*0 0 auto/)
    expect(css).toMatch(/\.ndr-volume-status\s*\{[^}]*flex-wrap:\s*nowrap[^}]*overflow-x:\s*auto[^}]*white-space:\s*nowrap/)
    expect(css).toMatch(/\.ndr-chapter-status-legend \.processed::before, \.ndr-volume-count\.processed::before\s*\{[^}]*var\(--ndr-st-processed\)/)
  })
  it('top-aligns batch configuration fields and limits desktop layout to three columns', () => {
    expect(css).toMatch(/\.ndr-range-grid\s*\{[^}]*align-items:\s*start/)
    expect(css).toMatch(/\.ndr-range-grid > label\s*\{[^}]*min-width:\s*0/)
    expect(css).toMatch(/\.ndr-batch-config-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3, minmax\(0, 1fr\)\)/)
    expect(css).toMatch(/@media \(max-width: 900px\)[\s\S]*?\.ndr-batch-config-grid[^}]*repeat\(2, minmax\(0, 1fr\)\)/)
    expect(css).toMatch(/@media \(max-width: 600px\)[\s\S]*?\.ndr-batch-config-grid[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/)
  })
  it('wraps multiline merge previews instead of using intrinsic-width columns', () => {
    expect(css).toMatch(/\.ndr-merge-people\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/)
    expect(css).toMatch(/\.ndr-merge-text\s*\{[^}]*white-space:\s*pre-wrap[^}]*overflow-wrap:\s*anywhere/)
  })
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
