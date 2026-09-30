import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SpeakerLegend } from '../src/components/SpeakerLegend'

describe('SpeakerLegend tooltips', () => {
  it('显示姓名和人物描述，格式与正文提示一致', () => {
    render(<SpeakerLegend onFocus={vi.fn()} legend={[{
      group_id: 'g1', label: '浅村悠太', scene_id: 's1', color_index: 0,
      first_quote_id: 'q1', description: '本章第一人称叙述者，书店店员', quote_count: 104,
    }]} />)
    expect(screen.getByRole('button')).toHaveAttribute(
      'title', '浅村悠太：本章第一人称叙述者，书店店员',
    )
  })

  it('没有人物描述时回退到姓名，不显示内部引语ID', () => {
    render(<SpeakerLegend onFocus={vi.fn()} legend={[{
      group_id: 'g1', label: '轻浮男客', scene_id: 's1', color_index: 0,
      first_quote_id: 'q1', description: '', quote_count: 7,
    }]} />)
    expect(screen.getByRole('button')).toHaveAttribute('title', '轻浮男客')
  })
})
