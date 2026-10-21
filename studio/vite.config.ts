import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// `npm run dev` proxies /api to a running studio_server (spike/inngest_driver/run_local.sh starts one on 8790).
export default defineConfig({ base: "./", plugins: [react()], build: { outDir: "dist", assetsInlineLimit: 100000000 }, server: { proxy: { "/api": "http://127.0.0.1:8790" } } });
