import fse from "fs-extra";
import path from "path";
import { scrapeAo3Work } from "./ao3-adapter.js";

function findRepoRoot(startDir = process.cwd()) {
  let dir = path.resolve(startDir);
  for (;;) {
    if (fse.pathExistsSync(path.join(dir, "turbo.json"))) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return path.resolve(startDir);
}

function parseArgs() {
  const args = process.argv.slice(2);
  const storyIds = [];
  let outputDir = null;
  let delay = 2;
  let headless = false;

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === "--output" || arg === "-o") {
      outputDir = args[++i];
    } else if (arg === "--delay") {
      delay = parseFloat(args[++i], 10) || 2;
    } else if (arg === "--headless") {
      headless = true;
    } else if (!arg.startsWith("-")) {
      storyIds.push(arg);
    }
  }

  return { storyIds, outputDir, delay, headless };
}

async function main() {
  const { storyIds, outputDir: outputOverride, delay, headless } = parseArgs();

  if (!storyIds.length) {
    console.error("Usage: node run.js [--output dir] [--delay sec] [--headless] <story_id> [story_id ...]");
    console.error("Provide at least one AO3 work ID (e.g. from URL .../works/<id>).");
    process.exit(1);
  }

  const root = findRepoRoot();
  const outputDir = path.resolve(
    outputOverride || path.join(root, "data", "stories")
  );
  const rateRpm = 30;
  const effectiveDelay = Math.max(delay, 60 / rateRpm);

  for (let i = 0; i < storyIds.length; i++) {
    if (i > 0) {
      await new Promise((r) => setTimeout(r, effectiveDelay * 1000));
    }
    const sid = storyIds[i];
    console.log(`Scraping ${sid}...`);
    try {
      const storyPath = await scrapeAo3Work(sid, outputDir, { headless });
      console.log(`  -> ${storyPath}`);
    } catch (err) {
      console.error(`  Error: ${err.message}`);
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
