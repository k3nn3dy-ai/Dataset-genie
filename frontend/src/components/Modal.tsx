import clsx from 'clsx'
import { useEffect, type ReactNode } from 'react'
import { Button, IconButton } from './Button'

interface Props { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; width?: 'sm' | 'md' | 'lg'; tone?: 'default' | 'amber' | 'red' }

const W = { sm: 'max-w-[440px]', md: 'max-w-[600px]', lg: 'max-w-[820px]' }

export function Modal({ open, onClose, title, children, footer, width = 'md', tone = 'default' }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center p-6" role="dialog" aria-modal>
      <div className="absolute inset-0 bg-bg/70 backdrop-blur-sm" onClick={onClose} />
      <div className={clsx('panel relative w-full modal-in flex flex-col max-h-[86vh]', W[width], tone === 'amber' && 'border-amber/50', tone === 'red' && 'border-red/50')}>
        <header className="flex items-center justify-between px-5 h-12 border-b border-line">
          <div className="flex items-baseline gap-2">
            <h2 className="font-display font-bold uppercase text-[15px] tracking-[.06em]">{title}</h2>
          </div>
          <IconButton icon="x" label="Close" size="sm" onClick={onClose} />
        </header>
        <div className="p-5 overflow-y-auto">{children}</div>
        {footer && <footer className="flex items-center justify-end gap-2 px-5 h-14 border-t border-line">{footer}</footer>}
      </div>
    </div>
  )
}

export function ConfirmModal({ open, onClose, onConfirm, title, body, confirmLabel = 'Confirm', danger, loading }: { open: boolean; onClose: () => void; onConfirm: () => void; title: string; body: ReactNode; confirmLabel?: string; danger?: boolean; loading?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title={title} width="sm" tone={danger ? 'red' : 'default'}
      footer={(
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant={danger ? 'danger' : 'primary'} icon={danger ? 'trash' : 'check'} loading={loading} onClick={onConfirm}>{confirmLabel}</Button>
        </>
      )}
    >
      <div className="text-[14px] text-muted leading-relaxed">{body}</div>
    </Modal>
  )
}
