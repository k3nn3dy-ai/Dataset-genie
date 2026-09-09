import clsx from 'clsx'
import { useMemo, useState, type ReactNode } from 'react'
import { Icon } from './Icon'

export interface Column<T> {
  key: string
  header: ReactNode
  render: (row: T) => ReactNode
  sortValue?: (row: T) => string | number
  width?: string
  align?: 'left' | 'right' | 'center'
  className?: string
  /** Allow wrapping (cells are nowrap by default). */
  wrap?: boolean
}

interface Props<T> {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  activeKey?: string | null
  selectable?: boolean
  selected?: Set<string>
  onSelectedChange?: (s: Set<string>) => void
  dense?: boolean
  maxHeight?: string
  empty?: ReactNode
  className?: string
  defaultSort?: { key: string; dir: 'asc' | 'desc' }
  rowTone?: (row: T) => 'default' | 'amber' | 'red' | 'dim'
}

export function MonoTable<T>({ rows, columns, rowKey, onRowClick, activeKey, selectable, selected, onSelectedChange, dense = true, maxHeight, empty, className, defaultSort, rowTone }: Props<T>) {
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(defaultSort ?? null)
  const sorted = useMemo(() => {
    if (!sort) return rows
    const col = columns.find((c) => c.key === sort.key)
    if (!col?.sortValue) return rows
    const sv = col.sortValue
    return [...rows].sort((a, b) => {
      const va = sv(a); const vb = sv(b)
      const r = typeof va === 'number' && typeof vb === 'number' ? va - vb : String(va).localeCompare(String(vb))
      return sort.dir === 'asc' ? r : -r
    })
  }, [rows, sort, columns])

  const allSelected = selectable && rows.length > 0 && rows.every((r) => selected?.has(rowKey(r)))
  const toggleAll = () => {
    if (!onSelectedChange) return
    onSelectedChange(allSelected ? new Set() : new Set(rows.map(rowKey)))
  }
  const toggleOne = (k: string) => {
    if (!onSelectedChange) return
    const next = new Set(selected)
    if (next.has(k)) next.delete(k); else next.add(k)
    onSelectedChange(next)
  }
  const cell = dense ? 'px-2.5 py-[5px]' : 'px-3 py-2'

  return (
    <div className={clsx('overflow-auto rounded-card border border-line bg-bg/40', className)} style={{ maxHeight }}>
      <table className="w-full border-collapse font-mono text-[12px] leading-tight">
        <thead className="sticky top-0 z-10 bg-surface2 backdrop-blur-md">
          <tr className="border-b border-line2">
            {selectable && (
              <th className={clsx(cell, 'w-8')}>
                <Check checked={!!allSelected} onChange={toggleAll} />
              </th>
            )}
            {columns.map((c) => {
              const active = sort?.key === c.key
              return (
                <th
                  key={c.key} style={{ width: c.width }}
                  className={clsx(cell, 'label !text-[10px] text-left font-normal whitespace-nowrap select-none', c.align === 'right' && 'text-right', c.align === 'center' && 'text-center', c.sortValue && 'cursor-pointer hover:text-text', active && '!text-green')}
                  onClick={c.sortValue ? () => setSort(active && sort?.dir === 'asc' ? { key: c.key, dir: 'desc' } : { key: c.key, dir: 'asc' }) : undefined}
                >
                  <span className="inline-flex items-center gap-1">
                    {c.header}
                    {c.sortValue && <Icon name="chevron" size={9} className={clsx('transition-transform', active ? (sort?.dir === 'asc' ? '-rotate-90' : 'rotate-90') : 'rotate-90 opacity-30')} />}
                  </span>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 && (
            <tr><td colSpan={columns.length + (selectable ? 1 : 0)} className="px-3 py-8 text-center text-dim">{empty ?? 'No rows'}</td></tr>
          )}
          {sorted.map((r) => {
            const k = rowKey(r)
            const isSel = selected?.has(k)
            const tone = rowTone?.(r) ?? 'default'
            return (
              <tr
                key={k} onClick={onRowClick ? () => onRowClick(r) : undefined}
                className={clsx(
                  'border-b border-line/70 last:border-b-0 transition-colors',
                  onRowClick && 'cursor-pointer hover:bg-green/5',
                  activeKey === k && 'bg-green/10 shadow-[inset_2px_0_0_#22e35a]',
                  isSel && 'bg-green/10',
                  tone === 'amber' && 'text-amber/90', tone === 'red' && 'text-red/90', tone === 'dim' && 'text-dim',
                )}
              >
                {selectable && (
                  <td className={cell} onClick={(e) => e.stopPropagation()}>
                    <Check checked={!!isSel} onChange={() => toggleOne(k)} />
                  </td>
                )}
                {columns.map((c) => (
                  <td key={c.key} className={clsx(cell, 'align-middle', !c.wrap && 'whitespace-nowrap', c.align === 'right' && 'text-right tabular-nums', c.align === 'center' && 'text-center', c.className)}>{c.render(r)}</td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

/** Truncating text cell (block + ellipsis) so wide tables fit their panel. */
export function TextCell({ text, max = 260, className }: { text: string; max?: number; className?: string }) {
  return <span className={clsx('block truncate', className)} style={{ maxWidth: max }} title={text}>{text}</span>
}

/** Truncating id cell with the full id on hover. */
export function IdCell({ id, max = 230, className }: { id: string; max?: number; className?: string }) {
  return <span className={clsx('block truncate text-green/90', className)} style={{ maxWidth: max }} title={id}>{id}</span>
}

export function Check({ checked, onChange, className }: { checked: boolean; onChange: () => void; className?: string }) {
  return (
    <button
      type="button" role="checkbox" aria-checked={checked} onClick={onChange}
      className={clsx('w-3.5 h-3.5 rounded-[3px] border flex items-center justify-center transition-colors focus-ring', checked ? 'bg-green border-green text-bg' : 'border-line2 bg-bg/60 hover:border-green/60', className)}
    >
      {checked && <Icon name="check" size={10} strokeWidth={3} />}
    </button>
  )
}
