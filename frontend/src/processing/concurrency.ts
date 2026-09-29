/** 在固定并发上限内运行任务，并保持返回结果与输入顺序一致。 */
export async function mapWithConcurrency<T, R>(
  items: readonly T[],
  concurrency: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) return []
  const limit = Math.max(1, Math.floor(concurrency) || 1)
  const results = new Array<R>(items.length)
  let cursor = 0

  const runNext = async () => {
    while (cursor < items.length) {
      const index = cursor
      cursor += 1
      results[index] = await worker(items[index], index)
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, () => runNext()),
  )
  return results
}

export interface TaskLimiter {
  run<T>(task: () => Promise<T>): Promise<T>
}

/** 共享任务池；批量人物识别与对白窗口使用同一个并发额度。 */
export function createTaskLimiter(concurrency: number): TaskLimiter {
  const limit = Math.max(1, Math.floor(concurrency) || 1)
  let active = 0
  const queue: Array<() => void> = []

  const enter = () =>
    new Promise<void>((resolve) => {
      if (active < limit) {
        active += 1
        resolve()
        return
      }
      queue.push(() => {
        active += 1
        resolve()
      })
    })

  const leave = () => {
    active = Math.max(0, active - 1)
    queue.shift()?.()
  }

  return {
    async run<T>(task: () => Promise<T>): Promise<T> {
      await enter()
      try {
        return await task()
      } finally {
        leave()
      }
    },
  }
}
