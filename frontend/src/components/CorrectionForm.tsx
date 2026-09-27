/**
 * 共享的人工更正表单（T13）：阅读页抽屉与待确认队列抽屉用的是同一个表单。
 *
 * 四种动作都不调用模型；`expected_version` 取当前标注版本，服务端做乐观并发校验，
 * 旧页面提交会拿到 409（由调用方展示冲突并刷新）。
 */
import { useEffect, useState } from 'react'

import type { QuoteCorrectionInput } from '../api/review'
import type { AnnotationStateOut, QuoteKind, SceneGroupRefOut } from '../api/types'
import { labelText } from '../styles/palette'

const KINDS: { value: QuoteKind; label: string }[] = [
  { value: 'speech', label: '对白（有说话人）' },
  { value: 'thought', label: '心声' },
  { value: 'quotation', label: '引用' },
  { value: 'group', label: '集体声音' },
  { value: 'other', label: '其他' },
  { value: 'unknown', label: '未知类型' },
]

type Action = QuoteCorrectionInput['action']

const ACTIONS: { value: Action; label: string }[] = [
  { value: 'assign_existing', label: '指定已有说话人' },
  { value: 'create_speaker', label: '新建说话人' },
  { value: 'set_kind', label: '修改类型' },
  { value: 'mark_unknown', label: '锁定为未知' },
]

export interface CorrectionFormProps {
  annotation: AnnotationStateOut | null
  sceneGroups: SceneGroupRefOut[]
  sceneVersion: number | null
  busy?: boolean
  errorText?: string | null
  onSubmit: (input: QuoteCorrectionInput) => void
}

export function CorrectionForm({
  annotation,
  sceneGroups,
  sceneVersion,
  busy = false,
  errorText = null,
  onSubmit,
}: CorrectionFormProps) {
  const [action, setAction] = useState<Action>('assign_existing')
  const [speakerRef, setSpeakerRef] = useState('')
  const [description, setDescription] = useState('')
  const [kind, setKind] = useState<QuoteKind>(annotation?.kind ?? 'speech')

  // 没有已有分组时（空候选）默认切到「新建说话人」，避免用户先撞一次错误
  useEffect(() => {
    if (sceneGroups.length === 0) setAction('create_speaker')
  }, [sceneGroups.length])

  useEffect(() => {
    if (speakerRef === '' && sceneGroups.length > 0) setSpeakerRef(sceneGroups[0].group_id)
  }, [sceneGroups, speakerRef])

  const requiresSpeaker = action === 'assign_existing'
  const disabled =
    busy ||
    (action === 'assign_existing' && (!speakerRef || sceneGroups.length === 0)) ||
    (action === 'set_kind' && !kind)

  const submit = () => {
    onSubmit({
      action,
      speakerRef: action === 'assign_existing' ? speakerRef : null,
      kind: action === 'set_kind' ? kind : null,
      description: action === 'create_speaker' ? description : '',
      expectedVersion: annotation?.version ?? null,
      expectedSceneVersion: action === 'create_speaker' ? sceneVersion : null,
      note: 'user-correction',
    })
  }

  return (
    <div className="ndr-correction-form" data-testid="correction-form">
      <p className="hint" data-testid="correction-version">
        当前标注：
        {annotation
          ? `${annotation.status} · ${labelText(annotation.label) || '（无色/无编号）'} · 版本 ${annotation.version}`
          : '还没有标注（更正会建立一条人工标注）'}
      </p>
      <label>
        动作
        <select
          value={action}
          onChange={(event) => setAction(event.target.value as Action)}
          data-testid="correction-action"
        >
          {ACTIONS.filter(
            (item) => item.value !== 'assign_existing' || sceneGroups.length > 0,
          ).map((item) => (
            <option key={item.value} value={item.value}>
              {item.label}
            </option>
          ))}
        </select>
      </label>

      {requiresSpeaker && (
        <label>
          已有说话人（本场景）
          <select
            value={speakerRef}
            onChange={(event) => setSpeakerRef(event.target.value)}
            data-testid="correction-speaker"
          >
            {sceneGroups.map((group) => (
              <option key={group.group_id} value={group.group_id}>
                {labelText(group.label)} {group.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {sceneGroups.length === 0 && (
        <p className="hint" data-testid="correction-no-groups">
          本场景还没有已有说话人：只能用「新建说话人」或「锁定为未知」。
        </p>
      )}

      {action === 'create_speaker' && (
        <label>
          说明（可空）
          <input
            type="text"
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            data-testid="correction-description"
            placeholder="例如：自称「我」的女性声音"
          />
        </label>
      )}

      {action === 'set_kind' && (
        <label>
          类型
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value as QuoteKind)}
            data-testid="correction-kind"
          >
            {KINDS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {errorText && (
        <p className="status-error" data-testid="correction-error">
          {errorText}
        </p>
      )}

      <button
        type="button"
        className="ndr-primary"
        disabled={disabled}
        onClick={submit}
        data-testid="correction-submit"
      >
        {busy ? '提交中…' : '确认更正（不调用模型）'}
      </button>
    </div>
  )
}