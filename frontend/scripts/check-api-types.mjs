#!/usr/bin/env node
/**
 * T18：接口类型一致性检查。
 *
 * 用 `docs/openapi.json`（由后端导出、pytest 会校验它与应用一致）重新生成 API 类型，
 * 与提交在仓库里的 `src/api/schema.d.ts` 逐字节比对：
 * 只有重新生成得到同一份文件，才能说「前端用到的类型 = 当前后端契约」。
 *
 * 失败时提示运行 `npm --prefix frontend run generate:api`，不自动改写文件。
 */
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const projectRoot = path.resolve(here, '..')
const repoRoot = path.resolve(projectRoot, '..')
const committedPath = path.join(projectRoot, 'src', 'api', 'schema.d.ts')
const openapiPath = path.join(repoRoot, 'docs', 'openapi.json')
const cliPath = path.join(projectRoot, 'node_modules', 'openapi-typescript', 'bin', 'cli.js')

const normalize = (text) => text.replace(/\r\n/g, '\n').trimEnd()

const workDir = mkdtempSync(path.join(tmpdir(), 'ndr-api-types-'))
const generatedPath = path.join(workDir, 'schema.d.ts')

try {
  execFileSync(process.execPath, [cliPath, openapiPath, '-o', generatedPath], {
    cwd: projectRoot,
    stdio: ['ignore', 'ignore', 'inherit'],
  })
  const committed = normalize(readFileSync(committedPath, 'utf8'))
  const generated = normalize(readFileSync(generatedPath, 'utf8'))
  if (committed !== generated) {
    console.error(
      '前端 API 类型与 docs/openapi.json 不一致：请运行 `npm --prefix frontend run generate:api` 并提交结果。',
    )
    process.exit(1)
  }
  console.log(`前端 API 类型与 docs/openapi.json 一致（${path.relative(repoRoot, committedPath)}）。`)
} catch (error) {
  console.error(`接口类型一致性检查失败：${error instanceof Error ? error.message : String(error)}`)
  process.exit(1)
} finally {
  rmSync(workDir, { recursive: true, force: true })
}