import { fetchJob } from '../api/books'
import { shouldRetryReadRequest } from '../api/client'
import type { JobDetailOut } from '../api/types'

export const TERMINAL_JOB_STATES = new Set([
  'COMPLETED', 'FAILED', 'BUDGET_EXHAUSTED', 'PAUSED', 'PARTIAL', 'NEEDS_RECONCILIATION',
])

/** 创建只代表派发成功；等待后台终态才能释放一个实际并发槽位。 */
export async function waitForJobCompletion(
  initial: JobDetailOut,
  onUpdate: (job: JobDetailOut) => void,
): Promise<JobDetailOut> {
  let current = initial
  onUpdate(current)
  while (!TERMINAL_JOB_STATES.has(current.state)) {
    await new Promise((resolve) => setTimeout(resolve, 800))
    let failures = 0
    while (true) {
      try {
        current = await fetchJob(current.id)
        break
      } catch (error) {
        if (!shouldRetryReadRequest(failures++, error)) throw error
        await new Promise((resolve) => setTimeout(resolve, 200 * failures))
      }
    }
    onUpdate(current)
  }
  return current
}
