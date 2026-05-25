import type { ScreeningReport } from "./types";

export async function fetchLatestReport(): Promise<ScreeningReport> {
  const response = await fetch("/api/report/latest", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法读取最新报告：${response.status}`);
  }
  return (await response.json()) as ScreeningReport;
}

export async function fetchAppendix(): Promise<string> {
  const response = await fetch("/api/report/appendix", { cache: "no-store" });
  if (!response.ok) {
    return "";
  }
  return await response.text();
}
