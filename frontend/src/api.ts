import type { ScreeningReport, UsPremarketReport } from "./types";

export async function fetchLatestReport(): Promise<ScreeningReport> {
  const response = await fetch("/api/report/latest", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法读取最新报告：${response.status}`);
  }
  return (await response.json()) as ScreeningReport;
}

export async function fetchUsPremarket(): Promise<UsPremarketReport | null> {
  const response = await fetch("/api/report/us", { cache: "no-store" });
  if (!response.ok) {
    return null;
  }
  return (await response.json()) as UsPremarketReport;
}
