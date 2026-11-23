import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发端口固定 127.0.0.1:5173，/api 代理到手册 11 约定的后端 127.0.0.1:8000。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    // 部分编辑器/工具用临时目录写入文件，避免监视器误报 EBUSY
    watch: {
      ignored: ["**/*.tmpdir/**", "**/.*.tmpdir/**"],
    },
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  css: {
    modules: {
      localsConvention: "camelCaseOnly",
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 图表库体积较大，单独分包，首屏只加载需要的部分
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          charts: ["recharts"],
        },
      },
    },
  },
});
