// Refresh the baked report snapshot under public/api/report/ from the repo's
// reports/ output. Runs before dev and build. The destination files are
// committed (they are the data the deployed static site serves), so a missing
// source — e.g. on Vercel, where the gitignored *-latest pointers aren't in the
// clone — is a soft warning that keeps the existing committed snapshot.
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const reportsDir = path.resolve(here, "..", "..", "reports");
const outDir = path.resolve(here, "..", "public", "api", "report");

// [source relative to reports/, destination filename under public/api/report/]
const targets = [
  ["ah-screening-report-latest.json", "latest"],
  [path.join("us-premarket", "us-premarket-latest.json"), "us"]
];

await fs.mkdir(outDir, { recursive: true });

for (const [src, dest] of targets) {
  const from = path.join(reportsDir, src);
  const to = path.join(outDir, dest);
  try {
    await fs.copyFile(from, to);
    console.log(`[copy-reports] ${src} -> public/api/report/${dest}`);
  } catch (error) {
    const exists = await fs
      .access(to)
      .then(() => true)
      .catch(() => false);
    const detail = error instanceof Error ? error.message : String(error);
    if (exists) {
      console.warn(`[copy-reports] source missing (${src}); keeping committed snapshot. ${detail}`);
    } else {
      console.error(`[copy-reports] source missing and no committed snapshot for ${dest}. ${detail}`);
      process.exitCode = 1;
    }
  }
}
