import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'
import ts from 'typescript'
import { expect, it } from 'vitest'

const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), '../src')
function sources(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const path = join(directory, entry.name)
    return entry.isDirectory() ? sources(path) : entry.name.endsWith('.tsx') ? [path] : []
  })
}
function classes(node: ts.JsxOpeningElement | ts.JsxSelfClosingElement): string[] {
  const attribute = node.attributes.properties.find(item => ts.isJsxAttribute(item) && item.name.getText() === 'className')
  return attribute && ts.isJsxAttribute(attribute) && attribute.initializer && ts.isStringLiteral(attribute.initializer)
    ? attribute.initializer.text.split(/\s+/) : []
}
function hasStyledNavigationParent(node: ts.Node): boolean {
  for (let parent = node.parent; parent; parent = parent.parent) {
    if (ts.isJsxElement(parent) && parent.openingElement.tagName.getText() === 'nav'
      && classes(parent.openingElement).some(name => ['ndr-book-nav', 'ndr-app-nav'].includes(name))) return true
  }
  return false
}

it('所有操作链接复用按钮样式，导航和无障碍入口保留专用样式', () => {
  const unstyled: string[] = []
  for (const path of sources(sourceRoot)) {
    const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    function visit(node: ts.Node) {
      if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
        && ['a', 'Link', 'NavLink'].includes(node.tagName.getText())
        && !classes(node).some(name => ['ndr-button', 'ndr-skip-link'].includes(name))
        && !hasStyledNavigationParent(node)) {
        const { line } = source.getLineAndCharacterOfPosition(node.getStart())
        unstyled.push(`${relative(sourceRoot, path)}:${line + 1}`)
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  }
  expect(unstyled).toEqual([])
})
