import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * 每次 E2E 运行使用全新数据目录（由 playwright.config.ts 写入 NDR_E2E_DATA_DIR），
 * 并尽力清理更早的运行目录。只影响 frontend/.e2e 下的内容，绝不触碰用户书库 data/。
 */
const frontendDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function e2eRoot(): string {
  const root = path.resolve(frontendDir, '.e2e')
  if (!root.endsWith(`${path.sep}.e2e`)) {
    throw new Error(`拒绝在非 E2E 目录下操作：${root}`)
  }
  return root
}

function removeWithRetry(target: string, attempts = 5): boolean {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      fs.rmSync(target, { recursive: true, force: true })
      return true
    } catch {
      // 上一轮进程刚退出时文件句柄可能短暂占用：退避重试，仍失败就跳过（不影响本轮结果）。
      const wait = new Int32Array(new SharedArrayBuffer(4))
      Atomics.wait(wait, 0, 0, 150)
    }
  }
  return false
}

export default function globalSetup() {
  const root = e2eRoot()
  const configured = process.env.NDR_E2E_DATA_DIR ?? '.e2e/data'
  const dataDir = path.resolve(frontendDir, configured)
  if (!dataDir.startsWith(root + path.sep)) {
    throw new Error(`E2E 数据目录必须在 ${root} 内，实际为 ${dataDir}`)
  }

  // 清理旧的运行目录（本轮目录应当还不存在）。
  if (fs.existsSync(root)) {
    for (const entry of fs.readdirSync(root)) {
      const full = path.join(root, entry)
      if (full === dataDir) continue
      removeWithRetry(full)
    }
  }
  removeWithRetry(dataDir)
  fs.mkdirSync(dataDir, { recursive: true })
}