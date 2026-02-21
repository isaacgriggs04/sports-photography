/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Plus Jakarta Sans', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      colors: {
        sand: {
          50: '#faf8f4',
          100: '#f5f0e8',
          150: '#f0ebe0',
          200: '#ebe5d8',
          300: '#e0d9cc',
        },
      },
    },
  },
  plugins: [],
}
