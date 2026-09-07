import type { Config } from 'tailwindcss'

// Design tokens — copied verbatim from the brief §6. Do not invent new colours.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#06080b',
        surface1: 'rgba(12,17,23,.86)',
        surface2: 'rgba(17,26,34,.9)',
        line: '#1c2a33',
        line2: '#28404c',
        text: '#d9e6ec',
        muted: '#7e95a1',
        dim: '#465964',
        cyan: '#00f0ff',
        cyanSoft: '#7ff7ff',
        magenta: '#ff2bd6',
        acid: '#b6ff2e',
        amber: '#ffb020',
        red: '#ff3b5c',
      },
      borderRadius: { card: '10px', btn: '8px', chip: '4px' },
      fontFamily: {
        display: ['"Chakra Petch"', 'sans-serif'],
        ui: ['Rajdhani', 'sans-serif'],
        mono: ['"Share Tech Mono"', 'monospace'],
      },
      boxShadow: {
        glow: '0 0 18px rgba(0,240,255,.55)',
        glowMagenta: '0 0 18px rgba(255,43,214,.45)',
      },
      letterSpacing: { label: '.14em' },
      fontSize: { label: ['10px', { lineHeight: '14px' }] },
    },
  },
  plugins: [],
} satisfies Config
