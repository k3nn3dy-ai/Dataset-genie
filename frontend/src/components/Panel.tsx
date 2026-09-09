import clsx from 'clsx'
import type { ReactNode } from 'react'

interface Props {
  title?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  bodyClassName?: string
  padded?: boolean
  tone?: 'default' | 'green' | 'steel' | 'amber'
}

const TONE: Record<NonNullable<Props['tone']>, string> = {
  default: 'border-line',
  green: 'border-green/40 shadow-[0_0_24px_rgba(34,227,90,.08)]',
  steel: 'border-steel/40 shadow-[0_0_24px_rgba(179,179,179,.08)]',
  amber: 'border-amber/40',
}

export function Panel({ title, actions, children, className, bodyClassName, padded = true, tone = 'default' }: Props) {
  return (
    <section className={clsx('panel flex flex-col min-w-0', TONE[tone], className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 px-4 h-10 border-b border-line shrink-0">
          <div className="flex items-baseline gap-2 min-w-0">
            {title && <h3 className="label !text-text truncate">{title}</h3>}
          </div>
          {actions && <div className="flex items-center gap-1.5 shrink-0">{actions}</div>}
        </header>
      )}
      <div className={clsx('min-w-0 flex-1', padded && 'p-4', bodyClassName)}>{children}</div>
    </section>
  )
}
