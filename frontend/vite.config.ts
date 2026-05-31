import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Report JSON is served as static files from public/api/report/ (latest, us),
// refreshed by the copy-reports script before dev/build. This keeps a single
// code path across dev, preview, and a static production host (e.g. Vercel).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false
  }
});
