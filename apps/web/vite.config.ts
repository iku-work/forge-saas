import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // tanstackRouter must run before react() — it generates routeTree.gen.ts
  // and splits each route's component into its own chunk automatically.
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
  ],
  // @forge/api-client's getApiBaseUrl() reads this instead of
  // import.meta.env.VITE_API_URL -- see that file's comment for why
  // (import.meta is unusable in the same package's Metro/Hermes consumers).
  define: {
    __VITE_API_URL_OVERRIDE__: JSON.stringify(process.env.VITE_API_URL),
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
