import type { ReactNode } from 'react'
import { BookOpen, Library, ListTodo, Settings2, SlidersHorizontal } from 'lucide-react'
import { MotionConfig } from 'motion/react'
import { NavLink } from 'react-router-dom'

const navigation = [
  { to: '/library', label: '书架', icon: Library },
  { to: '/tasks', label: '任务队列', icon: ListTodo },
  { to: '/settings/models', label: '模型配置', icon: SlidersHorizontal },
  { to: '/settings/general', label: '通用设置', icon: Settings2 },
]

export function AppShell({ children, status, recovery }: {
  children: ReactNode
  status: ReactNode
  recovery: ReactNode
}) {
  return <MotionConfig reducedMotion="user">
    <div className="app-shell ndr-shell">
      <a className="ndr-skip-link" href="#ndr-main">跳到主要内容</a>
      <header className="ndr-shell-header">
        <div className="ndr-shell-topbar">
          <div className="ndr-shell-brand">
            <span className="ndr-shell-mark"><BookOpen size={20} aria-hidden="true" /></span>
            <div><h1>轻小说对话辅助阅读器</h1><p>阅读原文，整理人物与对白。</p></div>
          </div>
          <nav className="ndr-shell-nav" aria-label="主导航">
            {navigation.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to}>
              <Icon size={16} aria-hidden="true" /><span>{label}</span>
            </NavLink>)}
          </nav>
        </div>
        <div className="ndr-shell-status">{status}</div>
        {recovery}
      </header>
      <main className="ndr-main" id="ndr-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  </MotionConfig>
}
