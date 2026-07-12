/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        bg: { primary: "#120e0a", card: "#1a1410", border: "#2d2018" },
        accent: { DEFAULT: "#f59e0b", light: "#fbbf24", dim: "#92400e" },
        status: { done: "#22c55e", running: "#3b82f6", pending: "#6b7280", fail: "#ef4444" },
      },
    },
  },
  plugins: [],
};
