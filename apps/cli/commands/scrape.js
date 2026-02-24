import path from "path";
import fse from "fs-extra";
import { scrapeAo3Work } from "@parseltongue/scraper";

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

export const command = "scrape <storyIds..>";
export const describe = "Download AO3 stories as Markdown + metadata";

export function builder(yargs) {
  return yargs
    .positional("storyIds", {
      describe: "One or more AO3 work IDs (from URL .../works/<id>)",
      type: "string",
    })
    .option("output", {
      alias: "o",
      type: "string",
      describe: "Output directory (default: data/stories under repo root)",
    })
    .option("delay", {
      type: "number",
      default: 2,
      describe: "Seconds between stories (min 2 at 30 rpm)",
    })
    .option("headless", {
      type: "boolean",
      default: false,
      describe: "Run browser without a visible window",
    })
    .example("$0 scrape 12345 67890", "Scrape two stories")
    .example("$0 scrape --headless -o ./out 12345", "Headless with custom output dir");
}

export async function handler(argv) {
  const { storyIds, output, delay, headless } = argv;

  const root = findRepoRoot();
  const outputDir = path.resolve(
    output || path.join(root, "data", "stories")
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
