import clsx from 'clsx'

/** Large stage numeral, faint ink. Decorative. */
export function GhostNumeral({ value, className, size = 120 }: { value: string; className?: string; size?: number }) {
  return (
    <div
      aria-hidden
      className={clsx('font-display font-semibold leading-none select-none pointer-events-none text-text', className)}
      style={{
        fontSize: size,
        opacity: 0.06,
        letterSpacing: '-0.04em',
      }}
    >
      {value}
    </div>
  )
}
