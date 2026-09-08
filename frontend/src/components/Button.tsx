import clsx from 'clsx'
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

export type ButtonVariant = 'primary' | 'ghost' | 'danger' | 'outline' | 'steel'
export type ButtonSize = 'sm' | 'md' | 'lg'

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  variant?: ButtonVariant
  size?: ButtonSize
  icon?: IconName
  iconRight?: IconName
  loading?: boolean
  children?: ReactNode
}

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-orange text-bg border border-orange hover:shadow-glow hover:bg-orangeSoft active:translate-y-px',
  steel: 'bg-steel text-bg border border-steel hover:shadow-glowSteel active:translate-y-px',
  ghost: 'bg-transparent text-text border border-transparent hover:bg-surface2 hover:border-line2',
  outline: 'bg-transparent text-orange border border-orange/50 hover:border-orange hover:bg-orange/10',
  danger: 'bg-transparent text-red border border-red/50 hover:bg-red/10 hover:border-red',
}
const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-[12px] gap-1.5',
  md: 'h-9 px-3.5 text-[13px] gap-2',
  lg: 'h-11 px-5 text-[14px] gap-2.5',
}

export function Button({ variant = 'ghost', size = 'md', icon, iconRight, loading, className, children, disabled, ...rest }: Props) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center rounded-btn font-display font-bold uppercase tracking-[.08em] transition-all duration-150 select-none whitespace-nowrap focus-ring',
        'disabled:cursor-not-allowed disabled:hover:shadow-none disabled:!bg-surface2 disabled:!text-dim disabled:!border-line2 disabled:hover:!bg-surface2',
        VARIANT[variant], SIZE[size], className,
      )}
      {...rest}
    >
      {loading ? <Icon name="refresh" size={size === 'sm' ? 12 : 14} className="animate-spin" /> : icon && <Icon name={icon} size={size === 'sm' ? 12 : 14} />}
      {children}
      {iconRight && <Icon name={iconRight} size={size === 'sm' ? 12 : 14} />}
    </button>
  )
}

/** Square icon-only button. */
export function IconButton({ icon, label, size = 'md', variant = 'ghost', className, ...rest }: { icon: IconName; label: string } & Omit<Props, 'icon' | 'children'>) {
  return (
    <button
      type="button" aria-label={label} title={label}
      className={clsx('inline-flex items-center justify-center rounded-btn transition-colors focus-ring disabled:opacity-40', VARIANT[variant], size === 'sm' ? 'h-7 w-7' : 'h-9 w-9', className)}
      {...rest}
    >
      <Icon name={icon} size={size === 'sm' ? 13 : 15} />
    </button>
  )
}
