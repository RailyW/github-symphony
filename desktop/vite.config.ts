import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 函数说明：导出 Vite 配置，Electron 开发模式固定使用 127.0.0.1。
export default defineConfig({
  // 逻辑说明：Electron 生产环境通过 loadFile 读取 index.html。
  // 如果沿用 Vite 默认的绝对路径 `/assets/...`，资源会被 file:// 解析到磁盘根目录，
  // 造成 JS/CSS 加载失败并出现白屏；相对 base 能同时兼容 DMG 安装后的 .app 路径。
  base: "./",
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
  },
});
