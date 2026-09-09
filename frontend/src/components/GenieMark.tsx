/** The Dataset Genie mark: a terminal cursor, a rising stream of code, a hooded figure. Strokes use currentColor. */
export function GenieMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg
      viewBox="14 3 36 60"
      width={size * 0.6}
      height={size}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.4}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
    <path d="M28.5 16 C27.5 8 30 6 32 6 C34 6 36.5 8 35.5 16 C41 16.5 45 18.5 46.5 22 L47 30 C47 31.5 46 32.5 44.5 32.5 L19.5 32.5 C18 32.5 17 31.5 17 30 L17.5 22 C19 18.5 23 16.5 28.5 16 Z"/>
    <path d="M20 28 C26 25.5 38 25.5 44 28"/>
    <path d="M22 36.5h4M29 36.5h6M38 36.5h4"/>
    <path d="M25 40h3M31 40h2M36 40h3"/>
    <path d="M27 43.5h3M33 43.5h4"/>
    <path d="M29 47h2M34 47h1"/>
    <path d="M31 50h2"/>
    <path d="M32 53h.1"/>
    <rect x="29.8" y="56.5" width="4.4" height="4" rx=".6"/>
    </svg>
  )
}
