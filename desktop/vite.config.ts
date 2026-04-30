import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 函数说明：导出 Vite 配置，Electron 开发模式固定使用 127.0.0.1。
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
});
