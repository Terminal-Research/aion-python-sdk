#!/usr/bin/env node

import { chmodSync, copyFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(here, "..");
const distDir = path.join(projectRoot, "dist");
const targetDir = path.resolve(projectRoot, "../../src/aion/cli/bin");

const artifacts = [
  "cli.mjs",
  "aion-chat-ui-darwin-arm64",
  "aion-chat-ui-darwin-x64"
];

if (!existsSync(path.join(targetDir, "__init__.py"))) {
  console.error(`Python SDK bundle directory not found: ${targetDir}`);
  process.exit(1);
}

let copied = 0;

for (const artifact of artifacts) {
  const source = path.join(distDir, artifact);
  if (!existsSync(source)) {
    continue;
  }

  copyFileSync(source, path.join(targetDir, artifact));
  chmodSync(path.join(targetDir, artifact), 0o755);
  copied += 1;
  console.log(`Copied ${artifact} to ${targetDir}`);
}

if (copied === 0) {
  console.error("No build artifacts were found in dist/. Run npm run build or npm run compile first.");
  process.exit(1);
}
