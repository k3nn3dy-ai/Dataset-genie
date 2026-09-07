import clsx from 'clsx'

/** Large outlined stage numeral, cyan stroke with glow. Decorative. */
export function GhostNumeral({ value, className, size = 120 }: { value: string; className?: string; size?: number }) {
  return (
    <div
      aria-hidden
      className={clsx('font-display font-bold leading-none select-none pointer-events-none', className)}
      style={{
        fontSize: size,
        color: 'transparent',
        WebkitTextStroke: '1px #00f0ff',
        textShadow: '0 0 18px rgba(0,240,255,.35), 0 0 42px rgba(0,240,255,.18)',
        opacity: 0.75,
        letterSpacing: '-0.02em',
      }}
    >
      {value}
    </div>
  )
}
