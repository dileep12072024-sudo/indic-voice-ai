/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cyber: {
          50:  '#f0f9ff',
          100: '#e0f2fe',
          400: '#38bdf8',
          500: '#0ea5e9',
          600: '#0284c7',
          900: '#0c4a6e',
        },
        neon: {
          purple: '#a855f7',
          pink:   '#ec4899',
          cyan:   '#22d3ee',
          green:  '#4ade80',
        },
        dark: {
          900: '#020617',
          800: '#0f172a',
          700: '#1e293b',
          600: '#334155',
        }
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
        'float': 'float 6s ease-in-out infinite',
      },
      keyframes: {
        glow: {
          '0%':   { boxShadow: '0 0 5px #22d3ee, 0 0 10px #22d3ee' },
          '100%': { boxShadow: '0 0 20px #22d3ee, 0 0 40px #22d3ee, 0 0 60px #22d3ee' }
        },
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%':      { transform: 'translateY(-20px)' }
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
      backgroundImage: {
        'cyber-grid': "linear-gradient(rgba(34,211,238,0.05) 1px, transparent 1px), linear-gradient(90deg, rgba(34,211,238,0.05) 1px, transparent 1px)",
      },
      backgroundSize: {
        'grid': '50px 50px',
      }
    },
  },
  plugins: [],
}
