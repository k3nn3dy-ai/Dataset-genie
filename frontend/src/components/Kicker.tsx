import clsx from 'clsx'

/** `STAGE 03 · THE TEACHER ANSWERS`. */
export function Kicker({ text, className, tone = 'green' }: { text: string; className?: string; tone?: 'green' | 'steel' | 'muted' }) {
  const colour = tone === 'green' ? 'text-green' : tone === 'steel' ? 'text-steel' : 'text-muted'
  return (
    <div className={clsx('flex items-center gap-3', className)}>
      <span className={clsx('font-mono text-[11px] tracking-[.22em] uppercase', colour)}>{text}</span>
    </div>
  )
}
