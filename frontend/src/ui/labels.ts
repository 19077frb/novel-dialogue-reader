/** Display only: keep API values and unknown provider identifiers intact. */
import type { AnnotationStateOut, SceneGroupRefOut } from '../api/types'

const internalSpeakerRef = /^(?:[scgp]\d+|(?:speaker|character|group|scene)[_-]?\d+|UNKNOWN|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/iu
const readableIdentity = (value?: string | null) => {
  const text = value?.trim() ?? ''
  return text && !internalSpeakerRef.test(text) ? text : ''
}

export function sceneSpeakerName(group: SceneGroupRefOut): string {
  return readableIdentity(group.canonical_name) || readableIdentity(group.description) || '尚未确定人物'
}

export function annotationSpeakerName(annotation: AnnotationStateOut, groups: SceneGroupRefOut[]): string {
  if (annotation.kind === 'other') return '无需归属'
  if (annotation.kind === 'group') return '集体声音'
  const group = groups.find(item => item.group_id === annotation.speaker_group_id)
  return group ? sceneSpeakerName(group) : readableIdentity(annotation.label) || '尚未确定人物'
}

export const gapDecisionLabel = (value: string): string => ({
  CONTINUE: '同一场对话继续', UPDATE: '场景内部推进', BREAK: '切换场景', UNCERTAIN: '场景待确认',
} as Record<string, string>)[value] ?? '场景待确认'

export const protocolLabel = (value: string): string => ({
  'chat-completions-compatible': '兼容聊天接口',
  'fake-provider': '模拟服务（仅测试，不调用模型）',
} as Record<string, string>)[value] ?? value

export const credentialLabel = (value: string): string => ({
  session: '仅本次会话', system: '系统凭据库', none: '不使用密钥',
} as Record<string, string>)[value] ?? value

export const annotationStatusLabel = (value: string): string => ({
  PROVISIONAL: '暂定，待确认', ACCEPTED: '已自动接受', USER_CONFIRMED: '已人工确认', UNKNOWN: '尚未确定',
} as Record<string, string>)[value] ?? value

export const quoteKindLabel = (value: string): string => ({
  speech: '对白（发声）', thought: '心声', quotation: '人物原话引用', group: '集体声音', other: '非人物文本（无需归属）', unknown: '表达类型待确认',
} as Record<string, string>)[value] ?? value

export const reviewReasonLabel = (value: string): string => ({
  MODEL_OUTPUT_WARNING: '模型结果校验警告',
  LOW_CONFIDENCE: '置信度低', AMBIGUOUS_SPEAKER: '说话人有歧义', UNKNOWN_SPEAKER: '无法确定说话人',
  UNKNOWN_QUOTE_KIND: '表达类型待确认', UNKNOWN_QUOTE_SOURCE: '引用来源待确认',
  POSSIBLE_NEW_SPEAKER: '可能是新说话人', SCENE_BOUNDARY: '场景边界待确认',
  STALE_DEPENDENCY: '人物或场景调整后需复核', USER_FLAGGED: '用户标记', OTHER: '其他',
} as Record<string, string>)[value] ?? value

export const queueStatusLabel = (value: string): string => ({
  PENDING: '待确认', DEFERRED: '已延后', RESOLVED: '已解决',
} as Record<string, string>)[value] ?? value

export const correctionActionLabel = (value: string): string => ({
  assign_existing: '指定已有说话人', create_speaker: '新建说话人', set_kind: '修改类型',
  mark_unknown: '锁定为未知', defer: '延后确认', undo: '撤销更正',
} as Record<string, string>)[value] ?? value
