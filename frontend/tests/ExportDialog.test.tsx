import { screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as booksApi from '../src/api/books'
import * as exportsApi from '../src/api/exports'
import type { ExportArtifactOut, ExportPreviewOut } from '../src/api/types'
import { ExportDialog } from '../src/components/ExportDialog'
import { renderWithProviders } from './helpers'

vi.mock('../src/api/books', () => ({
  queryKeys: {
    chapters: (id: string) => ['chapters', id],
  },
  fetchChapters: vi.fn(),
}))

vi.mock('../src/api/exports', () => ({
  exportKeys: { artifact: (id: string) => ['export-artifact', id] },
  previewExport: vi.fn(),
  createExport: vi.fn(),
  exportDownloadUrl: (id: string) => `/api/exports/${id}/download`,
}))

const CHAPTERS = [
  { id: 'c1', ordinal: 0, title: '第一章', start_cp: 0, end_cp: 40, source_href: null },
  { id: 'c2', ordinal: 1, title: '第二章', start_cp: 41, end_cp: 90, source_href: null },
] as never

function preview(snapshotId = 's1'): ExportPreviewOut {
  return {
    snapshot_id: snapshotId,
    book_id: 'b1',
    book_version_id: 'v1',
    snapshot_hash: `hash-${snapshotId}`,
    source_revision: 'rev-1',
    visibility_policy: 'position_safe',
    selected_chapter_ids: [],
    counts: { total: 3, accepted: 2, provisional: 0, unknown: 1, stale: 0, withheld: 0, unprocessed_quotes: 1 },
    coverage: { paragraphs: 4 },
    warnings: ['1 条候选对白还没有标注，导出时保持原样。'],
    sample_html: '<!DOCTYPE html><html><body><p>样张正文</p></body></html>',
    sample_fragments: ['样张正文'],
    created_at: '2026-09-28T00:00:00+00:00',
  } as unknown as ExportPreviewOut
}

function artifact(overrides: Partial<ExportArtifactOut> = {}): ExportArtifactOut {
  return {
    id: 'a1',
    snapshot_id: 's1',
    format: 'epub',
    state: 'COMPLETED',
    filename: '雨夜（标注版）.epub',
    relative_path: 'exports/a1/雨夜（标注版）.epub',
    byte_size: 4096,
    file_sha256: 'f'.repeat(64),
    exporter_version: 'exporter-1',
    validation: {
      internal: { ok: true, checks: { mimetype_first: true, resource_closure: true } },
      standard: { state: 'NOT_RUN', detail: '未提供 --epubcheck-jar' },
    },
    download_available: true,
    created_at: '2026-09-28T00:00:00+00:00',
    updated_at: '2026-09-28T00:00:00+00:00',
    ...overrides,
  } as unknown as ExportArtifactOut
}

describe('ExportDialog', () => {
  beforeEach(() => {
    vi.mocked(booksApi.fetchChapters).mockReset()
    vi.mocked(exportsApi.previewExport).mockReset()
    vi.mocked(exportsApi.createExport).mockReset()
    vi.mocked(booksApi.fetchChapters).mockResolvedValue(CHAPTERS)
    vi.mocked(exportsApi.previewExport).mockResolvedValue(preview())
  })

  it('人物资料因节选遗漏时显示数量和完整迁移建议，仍允许下载正文', async () => {
    vi.mocked(exportsApi.createExport).mockResolvedValue(artifact({
      validation: {
        internal: { ok: true, checks: { text_consistency: true } },
        standard: { state: 'NOT_RUN' },
        identity_ledger: { characters: 3, omitted: 2 },
      },
    }))
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} readPositionCp={12} />,
    )
    await screen.findByTestId('export-preview')
    await userEvent.click(screen.getByTestId('export-generate'))
    expect(await screen.findByTestId('export-identity-omitted')).toHaveTextContent('有 2 位人物')
    expect(screen.getByTestId('export-identity-omitted')).toHaveTextContent('请导出整本')
    expect(screen.getByTestId('export-state')).toHaveTextContent('已完成')
    expect(screen.getByTestId('export-download-link')).toBeInTheDocument()
  })

  it('冻结快照、展示覆盖统计/警告/沙箱样张，并按所选格式生成', async () => {
    vi.mocked(exportsApi.createExport).mockResolvedValue(artifact())
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} readPositionCp={12} />,
    )

    await screen.findByTestId('export-preview')
    expect(exportsApi.previewExport).toHaveBeenCalledWith(
      'b1',
      expect.objectContaining({ chapterIds: null, visibilityPolicy: 'position_safe' }),
      expect.anything(),
    )
    expect(screen.getByTestId('export-coverage')).toHaveTextContent('未处理 1')
    expect(screen.getByTestId('export-warnings')).toHaveTextContent('还没有标注')
    // 样张放在沙箱 iframe 里，不是阅读页截图
    const sample = screen.getByTestId('export-sample') as HTMLIFrameElement
    expect(sample.getAttribute('sandbox')).toBe('')
    expect(sample.getAttribute('srcdoc')).toContain('样张正文')

    await userEvent.click(screen.getByTestId('export-format-html'))
    await userEvent.click(screen.getByTestId('export-generate'))
    await waitFor(() =>
      expect(exportsApi.createExport).toHaveBeenCalledWith(
        'b1',
        expect.objectContaining({ snapshotId: 's1', format: 'html' }),
      ),
    )

    expect(await screen.findByTestId('export-state')).toHaveTextContent('已完成')
    expect(screen.getByTestId('export-check-mimetype_first')).toHaveTextContent('✓')
    expect(screen.getByTestId('export-standard')).toHaveTextContent('NOT_RUN')
    const link = screen.getByTestId('export-download-link')
    expect(link).toHaveClass('ndr-button', 'ndr-primary')
    expect(link).toHaveAttribute('href', '/api/exports/a1/download')
  })

  it('样式切换只改显示，不重新冻结快照', async () => {
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} />,
    )
    await screen.findByTestId('export-preview')
    const quote = screen.getByTestId('style-sample-quote')
    expect(quote).toHaveTextContent('〔S1〕「雨停了。」')

    await userEvent.click(screen.getByTestId('export-style-color_only'))
    expect(screen.getByTestId('style-sample-quote')).toHaveTextContent('「雨停了。」')
    expect(screen.getByTestId('style-sample-quote').querySelector('.label')).toBeNull()

    await userEvent.click(screen.getByTestId('export-style-label_only'))
    expect(screen.getByTestId('style-sample-quote').querySelector('.label')).not.toBeNull()
    // 「仅编号」不写内联颜色：颜色被覆盖时仍靠编号辨认\n    expect(screen.getByTestId('style-sample-quote').getAttribute('style')).toBeNull()
  })

  it('指定章节需要至少选一章，并把章节 ID 传给快照', async () => {
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} />,
    )
    await screen.findByTestId('export-preview')

    await userEvent.click(screen.getByTestId('export-scope-chapters'))
    expect(await screen.findByTestId('export-scope-empty')).toBeInTheDocument()

    await userEvent.click(screen.getByTestId('export-chapter-0'))
    await waitFor(() =>
      expect(exportsApi.previewExport).toHaveBeenLastCalledWith(
        'b1',
        expect.objectContaining({ chapterIds: ['c1'] }),
        expect.anything(),
      ),
    )
  })

  it('快照过期：重新打开时样张来自新快照，提示旧文件仍用旧快照', async () => {
    vi.mocked(exportsApi.createExport).mockResolvedValue(artifact())
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} />,
    )
    await screen.findByTestId('export-preview')
    await userEvent.click(screen.getByTestId('export-generate'))
    await screen.findByTestId('export-download')

    // 之后的预览返回新快照（模拟期间发生了人工更正）
    vi.mocked(exportsApi.previewExport).mockResolvedValue(preview('s2'))
    await userEvent.click(screen.getByTestId('export-style-label_only'))
    expect(await screen.findByTestId('export-stale-snapshot')).toHaveTextContent('旧快照')
  })

  it('校验失败时不提供下载', async () => {
    vi.mocked(exportsApi.createExport).mockResolvedValue(
      artifact({
        state: 'FAILED',
        download_available: false,
        validation: {
          internal: { ok: false, checks: { resource_closure: false }, missing_resources: ['OEBPS/images/x.png'] },
          standard: { state: 'NOT_RUN' },
        },
      }),
    )
    renderWithProviders(
      <ExportDialog bookId="b1" open onClose={vi.fn()} chapters={CHAPTERS} />,
    )
    await screen.findByTestId('export-preview')
    await userEvent.click(screen.getByTestId('export-generate'))

    expect(await screen.findByTestId('export-failed')).toBeInTheDocument()
    expect(screen.getByTestId('export-missing-resources')).toHaveTextContent('x.png')
    expect(screen.queryByTestId('export-download-link')).toBeNull()
  })
})
describe('ExportDialog 可访问性', () => {
  beforeEach(() => {
    vi.mocked(exportsApi.previewExport).mockReset()
    vi.mocked(exportsApi.previewExport).mockResolvedValue(preview())
  })

  it('有 role/aria-modal 与标题关联，Escape 关闭并把焦点还给打开按钮', async () => {
    const onClose = vi.fn()
    function Host() {
      const [open, setOpen] = useState(false)
      return (
        <>
          <button type="button" data-testid="export-opener" onClick={() => setOpen(true)}>
            打开导出
          </button>
          <ExportDialog
            bookId="b1"
            open={open}
            chapters={CHAPTERS}
            onClose={() => {
              setOpen(false)
              onClose()
            }}
          />
        </>
      )
    }

    renderWithProviders(<Host />)
    const opener = screen.getByTestId('export-opener')
    opener.focus()
    await userEvent.click(opener)

    const dialog = await screen.findByRole('dialog', { name: '导出' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    const labelledBy = dialog.getAttribute('aria-labelledby')
    expect(labelledBy).toBeTruthy()
    expect(document.getElementById(labelledBy as string)).toHaveTextContent('导出')
    await waitFor(() => expect(dialog).toHaveFocus())

    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('dialog')).toBeNull()
    await waitFor(() => expect(opener).toHaveFocus())
  })
})
