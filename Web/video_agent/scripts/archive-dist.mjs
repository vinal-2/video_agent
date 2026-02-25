import { cpSync, existsSync, mkdirSync } from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const distDir = path.join(projectRoot, "dist");
const buildsDir = path.join(projectRoot, "dist_builds");

if (!existsSync(distDir)) {
  console.error("dist/ not found. Run npm run build first.");
  process.exit(1);
}

mkdirSync(buildsDir, { recursive: true });
const stamp = new Date().toISOString().replace(/[-:]/g, "").split(".")[0];
const targetDir = path.join(buildsDir, `build_${stamp}`);
cpSync(distDir, targetDir, { recursive: true });
console.log(`Archived dist ? ${targetDir}`);