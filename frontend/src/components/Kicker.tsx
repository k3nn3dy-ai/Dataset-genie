import clsx from 'clsx'

/** `STAGE 03 · THE TEACHER ANSWERS` with katakana sub-label beside it. */
export function Kicker({ text, kana, className, tone = 'cyan' }: { text: string; kana?: string; className?: string; tone?: 'cyan' | 'magenta' | 'muted' }) {
  const colour = tone === 'cyan' ? 'text-cyan' : tone === 'magenta' ? 'text-magenta' : 'text-muted'
  return (
    <div className={clsx('flex items-center gap-3', className)}>
      <span className={clsx('font-mono text-[11px] tracking-[.22em] uppercase', colour)}>{text}</span>
      {kana && (
        <>
          <span className="h-3 w-px bg-line2" />
          <span className="font-mono text-[11px] text-muted tracking-[.1em]">{kana}</span>
        </>
      )}
    </div>
  )
}
