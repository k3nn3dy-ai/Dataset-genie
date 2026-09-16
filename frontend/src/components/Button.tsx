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
  primary: 'bg-green text-white border border-green hover:bg-greenSoft active:translate-y-px',
  steel: 'bg-text text-white border border-text hover:bg-text/90 active:translate-y-px',
  ghost: 'bg-transparent text-text border border-transparent hover:bg-surface2',
  outline: 'bg-transparent text-green border border-green/30 hover:border-green hover:bg-green/8',
  danger: 'bg-transparent text-red border border-red/30 hover:bg-red/8 hover:border-red/60',
}
const SIZE: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-[12px] gap-1.5',
  md: 'h-8 px-3.5 text-[13px] gap-2',
  lg: 'h-9 px-4 text-[13.5px] gap-2',
}

export function Button({ variant = 'ghost', size = 'md', icon, iconRight, loading, className, children, disabled, ...rest }: Props) {
  return (
    <button
      type="button"
      disabled={disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center rounded-btn font-ui font-semibold tracking-[-0.01em] transition-colors duration-150 select-none whitespace-nowrap focus-ring',
        'disabled:cursor-not-allowed disabled:!bg-surface2 disabled:!text-dim disabled:!border-line2 disabled:hover:!bg-surface2',
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
      className={clsx('inline-flex items-center justify-center rounded-btn transition-colors focus-ring disabled:opacity-40', VARIANT[variant], size === 'sm' ? 'h-7 w-7' : 'h-8 w-8', className)}
      {...rest}
    >
      <Icon name={icon} size={size === 'sm' ? 13 : 15} />
    </button>
  )
}
