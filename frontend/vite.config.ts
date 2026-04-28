import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  resolve: {
    dedupe: ['viem'],
  },
  optimizeDeps: {
    exclude: ['@walletconnect/ethereum-provider'],
  },
  server: {
    port: 3010,
    proxy: {
      '/api': {
        target: 'http://localhost:8010',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://localhost:8010',
        ws: true,
        changeOrigin: true,
      },
    },
  },
})
