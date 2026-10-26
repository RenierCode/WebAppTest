/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#FFFFFF',
        canvas2: '#F8FAFC',
        accentDeep: '#1E3A8A',
        accentSky: '#0284C7',
        dark: '#0F172A',
        dark2: '#334155',
        border: '#E2E8F0',
        alert: '#DC2626',
      },
    },
  },
  plugins: [],
}
