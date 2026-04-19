/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        surface: {
          50:  '#f8f9fa',
          100: '#1a1b1e',
          200: '#141517',
          300: '#0f1012',
        },
        brand: {
          DEFAULT: '#e8315a',
          dim:     '#c4254a',
          muted:   '#5c0e1e',
        },
        success: '#22c55e',
        warning: '#f59e0b',
        danger:  '#ef4444',
        info:    '#38bdf8',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      backgroundImage: {
        'gradient-brand': 'linear-gradient(135deg, #e8315a 0%, #f06080 100%)',
      },
    },
  },
  plugins: [],
}
