import clsx from 'clsx'

// Stroke SVG icon set. Never emoji. All paths on a 24×24 grid, currentColor stroke.
export type IconName =
  | 'play' | 'check' | 'sparkle' | 'settings' | 'folder' | 'search' | 'filter' | 'export' | 'warning' | 'refresh'
  | 'trash' | 'edit' | 'flag' | 'upload' | 'chevron' | 'x' | 'plus' | 'copy' | 'external' | 'pause' | 'stop'
  | 'grid' | 'list' | 'minus' | 'lock' | 'terminal' | 'dot' | 'arrowRight' | 'eye' | 'tree'

const PATHS: Record<IconName, string> = {
  play: 'M7 4.5v15l12-7.5z',
  check: 'M4.5 12.5l5 5 10-11',
  sparkle: 'M12 3v4M12 17v4M3 12h4M17 12h4M12 8.5 13.6 12 17 13.5 13.6 15 12 18.5 10.4 15 7 13.5 10.4 12z',
  settings: 'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  folder: 'M3 7.5A1.5 1.5 0 0 1 4.5 6H9l2 2.5h8.5A1.5 1.5 0 0 1 21 10v8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z',
  search: 'M10.5 17.5a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20.5 20.5l-5-5',
  filter: 'M3 5h18l-7 8.5V20l-4-2v-4.5z',
  export: 'M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v3.5A1.5 1.5 0 0 0 5.5 20h13a1.5 1.5 0 0 0 1.5-1.5V15',
  warning: 'M12 3.5 22 20H2zM12 10v4.5M12 17.2v.3',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
  trash: 'M4 7h16M9 7V4.5h6V7M6.5 7l1 13h9l1-13M10 11v6M14 11v6',
  edit: 'M4 20h4l11-11-4-4L4 16zM13.5 6.5l4 4',
  flag: 'M5 21V4M5 4h13l-2.5 4.5L18 13H5',
  upload: 'M12 4v11M7.5 8.5 12 4l4.5 4.5M4 20h16',
  chevron: 'M8 5l7 7-7 7',
  x: 'M6 6l12 12M18 6 6 18',
  plus: 'M12 5v14M5 12h14',
  copy: 'M9 9h11v11H9zM4 15V4h11',
  external: 'M14 4h6v6M20 4l-9 9M19 14v5.5A1.5 1.5 0 0 1 17.5 21h-13A1.5 1.5 0 0 1 3 19.5v-13A1.5 1.5 0 0 1 4.5 5H10',
  pause: 'M8 5v14M16 5v14',
  stop: 'M6 6h12v12H6z',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.5M3.5 12h.5M3.5 18h.5',
  minus: 'M5 12h14',
  lock: 'M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11',
  terminal: 'M4 5h16v14H4zM7.5 9l3 3-3 3M12.5 15h4',
  dot: 'M12 12h.01',
  arrowRight: 'M4 12h16M14 6l6 6-6 6',
  eye: 'M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  tree: 'M12 3v5M12 8H6v4M12 8h6v4M6 12v3M18 12v3M4 15h4v5H4zM16 15h4v5h-4zM10 15h4v5h-4zM12 8v7',
}

export function Icon({ name, size = 16, className, strokeWidth = 1.8, title }: { name: IconName; size?: number; className?: string; strokeWidth?: number; title?: string }) {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={strokeWidth}
      strokeLinecap="round" strokeLinejoin="round" className={clsx('shrink-0', className)} aria-hidden={title ? undefined : true} role={title ? 'img' : undefined}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  )
}

export const ICON_NAMES = Object.keys(PATHS) as IconName[]
