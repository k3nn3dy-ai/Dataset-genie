import type { Config } from 'tailwindcss'

// Design tokens — green / black / grey / white palette. Add colours here, not inline.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0a0a0a',
        surface1: 'rgba(18,18,18,.86)',
        surface2: 'rgba(26,26,26,.9)',
        line: '#262626',
        line2: '#3a3a3a',
        text: '#f0f0f0',
        muted: '#9a9a9a',
        dim: '#5c5c5c',
        green: '#22e35a',
        greenSoft: '#6ff59a',
        steel: '#b3b3b3',
        ok: '#f5f5f5',
        amber: '#ffa040',
        red: '#ff4a4a',
      },
      borderRadius: { card: '10px', btn: '8px', chip: '4px' },
      fontFamily: {
        display: ['"Chakra Petch"', 'sans-serif'],
        ui: ['Rajdhani', 'sans-serif'],
        mono: ['"Share Tech Mono"', 'monospace'],
      },
      boxShadow: {
        glow: '0 0 18px rgba(34,227,90,.55)',
        glowSteel: '0 0 18px rgba(179,179,179,.45)',
      },
      letterSpacing: { label: '.14em' },
      fontSize: { label: ['10px', { lineHeight: '14px' }] },
    },
  },
  plugins: [],
} satisfies Config
