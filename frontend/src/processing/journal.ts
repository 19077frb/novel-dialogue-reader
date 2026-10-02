/** Durable browser queue records: never store credentials or the novel's full text. */
export function readJournal<T>(key: string): T | null {
  const raw = localStorage.getItem(`ndr:tasks:v1:${key}`)
  if (!raw) return null
  try { return JSON.parse(raw) as T } catch { throw new Error('保存的任务记录无法读取，请勿重复启动模型任务。') }
}

export function writeJournal(key: string, value: unknown) {
  try { localStorage.setItem(`ndr:tasks:v1:${key}`, JSON.stringify(value)) }
  catch { throw new Error('无法保存任务队列，请检查浏览器存储空间后重试；未派发的新任务不会继续。') }
}

export function removeJournal(key: string) { localStorage.removeItem(`ndr:tasks:v1:${key}`) }
const ownedLeases = new Map<string, string>()
export function assertWorkflowOwnership(key: string) {
  const owner = ownedLeases.get(key)
  if (owner && readJournal<{ owner: string }>(`lease:${key}`)?.owner !== owner) {
    throw new Error('另一页面已接管本书处理，当前页面不再派发新任务，请刷新进度。')
  }
}

/** Keep one scheduler per book across tabs. Reload releases the old lock. */
export async function withWorkflowLock<T>(key: string, work: () => Promise<T>): Promise<T> {
  if (navigator.locks) return navigator.locks.request(`ndr:${key}`, work)
  const leaseKey = `lease:${key}`
  const owner = crypto.randomUUID()
  while (true) {
    const lease = readJournal<{ owner: string; expires: number }>(leaseKey)
    if (!lease || lease.expires < Date.now()) {
      writeJournal(leaseKey, { owner, expires: Date.now() + 15000 })
      await new Promise(resolve => setTimeout(resolve, 30))
      if (readJournal<{ owner: string }>(leaseKey)?.owner === owner) break
    }
    await new Promise(resolve => setTimeout(resolve, 300))
  }
  ownedLeases.set(key, owner)
  const timer = setInterval(() => {
    if (readJournal<{ owner: string }>(leaseKey)?.owner !== owner) { clearInterval(timer); return }
    writeJournal(leaseKey, { owner, expires: Date.now() + 15000 })
  }, 4000)
  try { return await work() } finally {
    clearInterval(timer)
    if (ownedLeases.get(key) === owner) ownedLeases.delete(key)
    if (readJournal<{ owner: string }>(leaseKey)?.owner === owner) removeJournal(leaseKey)
  }
}
