import { useRef, useState } from 'react'

export interface ImportDropzoneProps {
  onFile: (file: File) => void
  disabled?: boolean
}

export const ACCEPTED_SUFFIXES = ['.txt', '.epub']

export function ImportDropzone({ onFile, disabled = false }: ImportDropzoneProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  function handleFiles(files: FileList | null) {
    const file = files?.[0]
    if (file) onFile(file)
  }

  return (
    <div
      className={dragging ? 'ndr-dropzone dragging' : 'ndr-dropzone'}
      data-testid="import-dropzone"
      onDragOver={(event) => {
        event.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault()
        setDragging(false)
        if (!disabled) handleFiles(event.dataTransfer.files)
      }}
    >
      <p>把 TXT 或 EPUB 拖到这里，或选择文件</p>
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
      >
        选择文件
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_SUFFIXES.join(',')}
        data-testid="import-file-input"
        aria-label="选择 TXT 或 EPUB 文件"
        hidden
        onChange={(event) => {
          handleFiles(event.target.files)
          event.target.value = ''
        }}
      />
    </div>
  )
}