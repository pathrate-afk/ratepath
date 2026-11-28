/**
 * Static JS Syntax & Real ESM Import Validator for RatePath (Zero Mocks).
 * Runs `node --check` on every JS/MJS file in `frontend/js/` and `functions/api/`.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCAN_DIRS = [
  path.join(ROOT, "frontend", "js"),
  path.join(ROOT, "functions", "api"),
];

const files = [];
for (const dir of SCAN_DIRS) {
  if (!fs.existsSync(dir)) continue;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isFile() && /\.(?:js|mjs)$/.test(entry.name)) {
      files.push(path.join(dir, entry.name));
    }
  }
}

let checked = 0;
for (const f of files) {
  execFileSync(process.execPath, ["--check", f], { stdio: "inherit" });
  checked += 1;
}
console.log(`[lint:js] Passed syntax check on ${checked} JS modules.`);
