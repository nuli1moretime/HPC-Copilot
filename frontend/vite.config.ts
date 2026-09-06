import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Agent 工具循环单轮可能跑 60~90s（等 LLM + 等集群命令），
// Vite 默认的 http-proxy 会因为 socket 空闲而悄悄断开 WS，
// 表现为「前端卡在某一轮，后端还在继续跑」。这里显式关掉超时。
const wsProxyOptions = {
  target: 'ws://localhost:8000',
  ws: true,
  changeOrigin: true,
  // 关键：把 proxyTimeout / timeout 都拉到 0（永不超时）
  proxyTimeout: 0,
  timeout: 0,
  configure: (proxy: any) => {
    proxy.on('proxyReqWs', (_proxyReq: any, _req: any, socket: any) => {
      // 底层 socket 也关掉超时，防止 TCP 层因空闲被回收
      socket.setTimeout(0)
      socket.setNoDelay(true)
      socket.setKeepAlive(true, 30_000)
    })
    proxy.on('error', (err: any) => {
      // 静默日志，避免 dev 控制台被 ECONNRESET 刷屏
      console.warn('[vite ws proxy]', err?.code || err?.message || err)
    })
  },
}

export default defineConfig({
  plugins: [react()],
  build: {
    // 把体积较大的稳定依赖拆开，浏览器可并行下载并长期缓存；
    // 业务代码更新时不再让终端和 Markdown 依赖一起失效。
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom'],
          'vendor-xterm': ['@xterm/xterm', '@xterm/addon-fit', '@xterm/addon-web-links'],
          'vendor-markdown': ['react-markdown'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        proxyTimeout: 0,
        timeout: 0,
      },
      '/ws/terminal': wsProxyOptions,
      '/ws/chat': wsProxyOptions,
    },
  },
})
