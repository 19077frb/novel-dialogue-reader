import './processingFlow.css'
import { Link } from 'react-router-dom'

export type ProcessingStep = 'config' | 'range' | 'roster' | 'run' | 'results'

interface ProcessingFlowProps {
  steps: { id: ProcessingStep; label: string }[]
  current: ProcessingStep
  onChange: (step: ProcessingStep) => void
  running?: boolean
}

export function ProcessingFlow({ steps, current, onChange, running }: ProcessingFlowProps) {
  const index = steps.findIndex(step => step.id === current)
  return (
    <div className="card ndr-processing-flow">
      <nav aria-label="处理步骤">
        <ol>
          {steps.map((step, position) => (
            <li key={step.id}>
              <button type="button" aria-current={current === step.id ? 'step' : undefined}
                className={current === step.id ? 'ndr-primary' : undefined}
                data-testid={`preview-step-${step.id}`} onClick={() => onChange(step.id)}>
                <span className="ndr-step-badge" aria-hidden="true">{position + 1}</span>
                {step.label}
              </button>
            </li>
          ))}
        </ol>
      </nav>
      <div className="ndr-form-actions">
        {running && <span className="hint" role="status">本书任务正在处理</span>}
        <Link className="ndr-button" to="/tasks">任务队列</Link>
        {index > 0 && <button type="button" data-testid="preview-step-back"
          onClick={() => onChange(steps[index - 1].id)}>上一步</button>}
        {index < steps.length - 1 && <button type="button" className="ndr-primary" data-testid="preview-step-next"
          onClick={() => onChange(steps[index + 1].id)}>下一步</button>}
      </div>
    </div>
  )
}
