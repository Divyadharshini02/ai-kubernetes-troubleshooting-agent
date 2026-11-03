import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to FastAPI so the browser only talks to one origin.
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
  test: {
    // A simulated browser, so components render without a real one. Tests never reach the backend.
    environment: 'jsdom',
    include: ['src/**/*.test.jsx'],
    // The default 5 seconds was exceeded by the first render on this machine while other containers ran.
    testTimeout: 20000,
  },
})
