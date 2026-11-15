import { CircleAlert, RotateCcw } from 'lucide-react'
import { Button } from './ui/button'
import '../styles/components.css'

interface ReadErrorNoticeProps {
  label: string
  error: unknown
  retrying?: boolean
  onRetry: () => void
  testId?: string
}

function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message
  return '未知错误'
}

export function ReadErrorNotice({ label, error, retrying = false, onRetry, testId }: ReadErrorNoticeProps) {
  return (
    <div className="status-error ndr-read-error" role="alert" data-testid={testId}>
      <CircleAlert size={17} aria-hidden="true" />
      <span>{label}：{errorMessage(error)}</span>
      <Button title={retrying ? '正在重新读取，请等待本次读取完成。' : undefined} type="button" onClick={onRetry} disabled={retrying}>
        <RotateCcw size={15} aria-hidden="true" />
        {retrying ? '正在重试…' : '重新读取'}
      </Button>
    </div>
  )
}
