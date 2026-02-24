import path from "path";
import fse from "fs-extra";
import { enunciateChapter } from "@parseltongue/enunciate";

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

export const command = "enunciate <storyId> [chapter]";
export const describe = "Voice-direct chapter Markdown for spoken delivery using Gemini";

export function builder(yargs) {
  return yargs
    .positional("storyId", {
      describe: "Story ID to enunciate (must already be scraped)",
      type: "string",
    })
    .positional("chapter", {
      describe: "Chapter number (omit to enunciate all chapters)",
      type: "number",
    })
    .option("output", {
      alias: "o",
      type: "string",
      describe: "Output directory (default: data/stories under repo root)",
    })
    .option("model", {
      alias: "m",
      type: "string",
      default: "gemini-2.5-flash",
      describe: "Gemini model name",
    })
    .option("force", {
      alias: "f",
      type: "boolean",
      default: false,
      describe: "Overwrite existing enunciated files",
    })
    .example("$0 enunciate 12345", "Enunciate all chapters")
    .example("$0 enunciate 12345 3", "Enunciate only chapter 3")
    .example("$0 enunciate 12345 -f", "Re-enunciate even if output files exist");
}

function chapterFilePath(storyDir, num) {
  return path.join(storyDir, "chapters", String(num).padStart(2, "0") + ".md");
}

function enunciatedFilePath(storyDir, num) {
  return path.join(
    storyDir,
    "chapters",
    String(num).padStart(2, "0") + "-enunciated.md"
  );
}

function discoverChapters(storyDir) {
  const chaptersDir = path.join(storyDir, "chapters");
  if (!fse.pathExistsSync(chaptersDir)) return [];
  return fse
    .readdirSync(chaptersDir)
    .filter((f) => /^\d{2,}\.md$/.test(f))
    .sort()
    .map((f) => parseInt(f, 10));
}

export async function handler(argv) {
  const { storyId, chapter, output, model, force } = argv;

  if (!process.env.GEMINI_API_KEY) {
    console.error(
      "GEMINI_API_KEY environment variable is required.\n" +
        "Get one at https://aistudio.google.com/app/apikey"
    );
    process.exit(1);
  }

  const root = findRepoRoot();
  const baseDir = path.resolve(output || path.join(root, "data", "stories"));
  const storyDir = path.join(baseDir, storyId);

  if (!fse.pathExistsSync(storyDir)) {
    console.error(
      `Story directory not found: ${storyDir}\n` +
        `Run "parseltongue scrape ${storyId}" first.`
    );
    process.exit(1);
  }

  const chapters = chapter ? [chapter] : discoverChapters(storyDir);

  if (!chapters.length) {
    console.error(`No chapter files found in ${storyDir}/chapters/`);
    process.exit(1);
  }

  console.log(
    `Enunciating ${chapters.length} chapter(s) of story ${storyId} with ${model}...`
  );

  for (const num of chapters) {
    const srcPath = chapterFilePath(storyDir, num);
    const destPath = enunciatedFilePath(storyDir, num);

    if (!fse.pathExistsSync(srcPath)) {
      console.error(`  ch ${num}: source not found (${srcPath}), skipping`);
      continue;
    }

    if (!force && fse.pathExistsSync(destPath)) {
      console.log(`  ch ${num}: already enunciated, skipping (use -f to overwrite)`);
      continue;
    }

    const chapterText = fse.readFileSync(srcPath, "utf-8");
    console.log(`  ch ${num}: sending to Gemini (${chapterText.length} chars)...`);

    try {
      const result = await enunciateChapter(chapterText, { model });
      fse.outputFileSync(destPath, result);
      console.log(`  ch ${num}: saved -> ${path.relative(root, destPath)}`);
    } catch (err) {
      console.error(`  ch ${num}: error — ${err.message}`);
    }
  }

  console.log("Done.");
}
