import type { ScreeningReport, UsPremarketReport } from "./types";

// When VITE_DATA_BASE_URL is set (production), fetch live JSON from that base —
// e.g. https://raw.githubusercontent.com/<owner>/<repo>/data-latest — so the
// pipeline can refresh data without redeploying. Otherwise fall back to the
// static snapshot baked into the build under /api/report/* (path A).
const DATA_BASE = (import.meta.env.VITE_DATA_BASE_URL ?? "").replace(/\/+$/, "");

const REPORT_URL = DATA_BASE ? `${DATA_BASE}/ah-screening-report-latest.json` : "/api/report/latest";
const US_URL = DATA_BASE ? `${DATA_BASE}/us-premarket-latest.json` : "/api/report/us";

// Cache-bust the CDN-cached remote source; no-op for the local static path.
function withBuster(url: string): string {
  return DATA_BASE ? `${url}?t=${Date.now()}` : url;
}

export async function fetchLatestReport(): Promise<ScreeningReport> {
  const response = await fetch(withBuster(REPORT_URL), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法读取最新报告：${response.status}`);
  }
  return (await response.json()) as ScreeningReport;
}

export async function fetchUsPremarket(): Promise<UsPremarketReport | null> {
  const response = await fetch(withBuster(US_URL), { cache: "no-store" });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as UsPremarketReport;
}
