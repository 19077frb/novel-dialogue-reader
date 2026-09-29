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
      <span>{label}：{errorMessage(error)}</span>
      <button type="button" onClick={onRetry} disabled={retrying}>
        {retrying ? '正在重试…' : '重新读取'}
      </button>
    </div>
  )
}
