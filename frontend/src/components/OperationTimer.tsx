import { useCallback, useEffect, useState } from 'react'

export function durationLabel(seconds: number): string {
  const value = Math.max(0, Math.floor(seconds))
  return value < 60 ? `${value} 秒` : `${Math.floor(value / 60)} 分 ${value % 60} 秒`
}

/** Only this small display ticks; no query invalidation or parent rerender. */
export function OperationTimer({ startedAt, finishedAt, completed = 0, total = 0 }: {
  startedAt: number; finishedAt?: number | null; completed?: number; total?: number
}) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (!startedAt || finishedAt) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt, finishedAt])
  if (!Number.isFinite(startedAt) || startedAt <= 0) return null
  const elapsed = Math.max(0, ((finishedAt || now) - startedAt) / 1000)
  const remaining = completed > 0 && completed < total && elapsed >= 1
    ? elapsed * (total - completed) / completed : null
  return <p className="hint" data-testid="operation-timer">
    {finishedAt ? '用时' : '已等待'} {durationLabel(elapsed)}
    {!finishedAt && (remaining !== null
      ? ` · 预计还需约 ${durationLabel(remaining)}（按当前进度估算）`
      : ' · 暂无足够进度估算剩余时间')}
  </p>
}

export function useOperationClock() {
  const [clock, setClock] = useState({ startedAt: 0, finishedAt: 0 })
  const start = useCallback(() => setClock({ startedAt: Date.now(), finishedAt: 0 }), [])
  const finish = useCallback(() => setClock(current => ({ ...current, finishedAt: Date.now() })), [])
  return {
    clock, start, finish,
  }
}

export function useRequestClock(pending: boolean) {
  const { clock, start, finish } = useOperationClock()
  useEffect(() => { if (pending) start(); else finish() }, [pending, start, finish])
  return clock
}
