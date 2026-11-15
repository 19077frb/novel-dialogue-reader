import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Validate before webServer startup; never delete another run's files or a running database. */
export function validateE2eDataDirectory(configured: string, baseDir = frontendDir, allowPopulated = false): string {
  const root = path.resolve(baseDir, '.e2e')
  const dataDir = path.resolve(baseDir, configured)
  const relative = path.relative(root, dataDir)
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`E2E 数据目录必须在 ${root} 内，实际为 ${dataDir}`)
  }
  // Junctions/symlinks could escape lexical containment, so do not follow them.
  let current = root
  for (const segment of ['', ...relative.split(path.sep)]) {
    current = path.join(current, segment)
    if (fs.existsSync(current) && fs.lstatSync(current).isSymbolicLink()) {
      throw new Error(`E2E 数据目录不能包含符号链接：${current}`)
    }
  }
  if (fs.existsSync(dataDir)) {
    if (!fs.statSync(dataDir).isDirectory()) throw new Error(`E2E 数据路径不是目录：${dataDir}`)
    if (!allowPopulated && fs.readdirSync(dataDir).length) {
      throw new Error(`拒绝重用非空 E2E 数据目录，请为本轮选择新目录：${dataDir}`)
    }
  }
  return dataDir
}

export function ensureRunningE2eDataDirectory(configured: string, baseDir = frontendDir) {
  // Playwright starts webServer plugins before globalSetup. The backend may already
  // have created its database here; checking emptiness or deleting files is unsafe.
  const dataDir = validateE2eDataDirectory(configured, baseDir, true)
  fs.mkdirSync(dataDir, { recursive: true })
}

export default function globalSetup() {
  ensureRunningE2eDataDirectory(process.env.NDR_E2E_DATA_DIR ?? '.e2e/data')
}
