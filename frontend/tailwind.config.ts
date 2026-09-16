import type { Config } from 'tailwindcss'

// Design tokens — Apple-like light system. Token names stay stable; values changed.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#F5F5F7',
        surface1: '#FFFFFF',
        surface2: '#EFEFF4',
        line: '#E5E5EA',
        line2: '#D2D2D7',
        text: '#1D1D1F',
        muted: '#6E6E73',
        dim: '#8E8E93',
        green: '#0071E3',
        greenSoft: '#147CE5',
        steel: '#86868B',
        ok: '#34C759',
        amber: '#FF9F0A',
        red: '#FF3B30',
      },
      borderRadius: { card: '12px', btn: '10px', chip: '999px' },
      fontFamily: {
        display: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Display"', 'Inter', 'sans-serif'],
        ui: ['-apple-system', 'BlinkMacSystemFont', '"SF Pro Text"', 'Inter', 'sans-serif'],
        mono: ['"SF Mono"', 'ui-monospace', '"IBM Plex Mono"', 'Menlo', 'monospace'],
      },
      boxShadow: {
        glow: '0 1px 2px rgba(0,0,0,.04), 0 8px 24px rgba(0,0,0,.06)',
        glowSteel: '0 1px 2px rgba(0,0,0,.04), 0 4px 16px rgba(0,0,0,.05)',
      },
      letterSpacing: { label: '.01em' },
      fontSize: { label: ['11px', { lineHeight: '14px', fontWeight: '500' }] },
    },
  },
  plugins: [],
} satisfies Config
