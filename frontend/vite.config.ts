import react from "@vitejs/plugin-react";
import { promises as fs } from "node:fs";
import type { ServerResponse } from "node:http";
import path from "node:path";
import { defineConfig, type Connect, type Plugin } from "vite";

const repoRoot = path.resolve(__dirname, "..");
const reportsDir = path.resolve(repoRoot, "reports");

function send(res: ServerResponse, statusCode: number, contentType: string, body: string) {
  res.statusCode = statusCode;
  res.setHeader("Content-Type", contentType);
  res.end(body);
}

function registerReportRoutes(middlewares: Connect.Server) {
  middlewares.use("/api/report/latest", async (_req, res) => {
    try {
      const file = await fs.readFile(path.join(reportsDir, "ah-screening-report-latest.json"), "utf8");
      send(res, 200, "application/json; charset=utf-8", file);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send(res, 404, "application/json; charset=utf-8", JSON.stringify({ error: message }));
    }
  });

  middlewares.use("/api/report/us", async (_req, res) => {
    try {
      const file = await fs.readFile(path.join(reportsDir, "us-premarket", "us-premarket-latest.json"), "utf8");
      send(res, 200, "application/json; charset=utf-8", file);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      send(res, 404, "application/json; charset=utf-8", JSON.stringify({ error: message }));
    }
  });
}

function reportApi(): Plugin {
  return {
    name: "ah-report-api",
    configureServer(server) {
      registerReportRoutes(server.middlewares);
    },
    configurePreviewServer(server) {
      registerReportRoutes(server.middlewares);
    }
  };
}

export default defineConfig({
  plugins: [react(), reportApi()],
  server: {
    port: 5173,
    strictPort: false
  }
});
