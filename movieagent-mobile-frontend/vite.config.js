import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  base: '/mobile/',
  plugins: [react()],
  server: {
    port: 5176,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/outputs': 'http://127.0.0.1:8765',
    },
  },
})
