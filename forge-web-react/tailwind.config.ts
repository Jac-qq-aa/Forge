import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        primary: '#4CAF50',
        secondary: '#2196F3',
        warning: '#FF9800',
        error: '#F44336',
      },
    },
  },
  plugins: [],
}

export default config