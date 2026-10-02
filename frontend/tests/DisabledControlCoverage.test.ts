import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import { expect, it } from 'vitest'

it('every explicitly disabled button provides a hover explanation', () => {
  const missing: string[] = []
  for (const directory of ['src/pages', 'src/components']) {
    for (const filename of readdirSync(resolve(directory)).filter(name => name.endsWith('.tsx'))) {
      const path = resolve(directory, filename)
      const source = ts.createSourceFile(path, readFileSync(path, 'utf8'),
        ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
      const visit = (node: ts.Node) => {
        if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
          const attributes = node.attributes.properties.filter(ts.isJsxAttribute)
            .map(attribute => attribute.name.getText(source))
          if (node.tagName.getText(source) === 'button' && attributes.includes('disabled')
              && !attributes.includes('title')) {
            missing.push(`${directory}/${filename}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1}`)
          }
        }
        ts.forEachChild(node, visit)
      }
      visit(source)
    }
  }
  expect(missing, '禁用按钮必须提供真实的锁定原因；主要操作还应在附近显示解锁步骤。').toEqual([])
})
