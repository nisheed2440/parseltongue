import path from "path";
import fse from "fs-extra";
import {
  synthesizeChapters,
  synthesizeViaServer,
  checkServer,
  parseEnunciatedChapter,
} from "@parseltongue/tts";

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

export const command = "speak <storyId> [chapter]";
export const describe =
  "Convert enunciated chapters to audio via Qwen3-TTS";

export function builder(yargs) {
  return yargs
    .positional("storyId", {
      describe: "Story ID (must have enunciated chapters)",
      type: "string",
    })
    .positional("chapter", {
      describe: "Chapter number (omit to speak all enunciated chapters)",
      type: "number",
    })
    .option("output", {
      alias: "o",
      type: "string",
      describe: "Output directory (default: data/stories under repo root)",
    })
    .option("mode", {
      alias: "m",
      type: "string",
      default: "design",
      describe:
        '"design" = per-segment voice expression via VoiceDesign (~4 GB VRAM); ' +
        '"clone" = consistent voice via voice-design-then-clone (~8 GB VRAM)',
      choices: ["clone", "design"],
    })
    .option("device", {
      alias: "d",
      type: "string",
      default: "cuda:0",
      describe: "Torch device for model loading",
    })
    .option("language", {
      alias: "l",
      type: "string",
      default: "English",
      describe: "Target language for TTS",
    })
    .option("force", {
      alias: "f",
      type: "boolean",
      default: false,
      describe: "Overwrite existing audio files",
    })
    .option("python", {
      type: "string",
      describe: "Python binary path (default: $PYTHON_BIN or python)",
    })
    .option("server", {
      type: "string",
      describe:
        "TTS server URL (default: $TTS_SERVER_URL or http://localhost:7860). " +
        "Set to use the warm Gradio server instead of one-shot Python.",
    })
    .option("no-server", {
      type: "boolean",
      default: false,
      describe: "Force one-shot Python mode even if a server URL is configured",
    })
    .example("$0 speak 12345", "Speak all enunciated chapters")
    .example("$0 speak 12345 1", "Speak only chapter 1")
    .example("$0 speak 12345 --mode clone", "Consistent voice via cloning")
    .example("$0 speak 12345 --server http://localhost:7860", "Use warm TTS server");
}

function enunciatedPath(storyDir, num) {
  return path.join(
    storyDir,
    "chapters",
    String(num).padStart(2, "0") + "-enunciated.md"
  );
}

function audioPath(storyDir, num) {
  return path.join(
    storyDir,
    "chapters",
    String(num).padStart(2, "0") + ".wav"
  );
}

function discoverEnunciated(storyDir) {
  const dir = path.join(storyDir, "chapters");
  if (!fse.pathExistsSync(dir)) return [];
  return fse
    .readdirSync(dir)
    .filter((f) => /^\d{2,}-enunciated\.md$/.test(f))
    .sort()
    .map((f) => parseInt(f, 10));
}

export async function handler(argv) {
  const { storyId, chapter, output, mode, device, language, force } = argv;
  const noServer = argv["no-server"] || argv.noServer;
  const serverUrl =
    argv.server || process.env.TTS_SERVER_URL || undefined;

  const root = findRepoRoot();
  const baseDir = path.resolve(output || path.join(root, "data", "stories"));
  const storyDir = path.join(baseDir, storyId);

  if (!fse.pathExistsSync(storyDir)) {
    console.error(
      `Story directory not found: ${storyDir}\n` +
        `Run "parseltongue scrape ${storyId}" first, then "parseltongue enunciate ${storyId}".`
    );
    process.exit(1);
  }

  const chapterNums = chapter ? [chapter] : discoverEnunciated(storyDir);

  if (!chapterNums.length) {
    console.error(
      `No enunciated chapters found in ${storyDir}/chapters/\n` +
        `Run "parseltongue enunciate ${storyId}" first.`
    );
    process.exit(1);
  }

  // Build the manifest — one entry per chapter that needs processing
  const chapters = [];
  for (const num of chapterNums) {
    const src = enunciatedPath(storyDir, num);
    const dest = audioPath(storyDir, num);

    if (!fse.pathExistsSync(src)) {
      console.error(`  ch ${num}: enunciated file not found (${src}), skipping`);
      continue;
    }

    if (!force && fse.pathExistsSync(dest)) {
      console.log(
        `  ch ${num}: audio already exists, skipping (use -f to overwrite)`
      );
      continue;
    }

    const markdown = fse.readFileSync(src, "utf-8");
    const { voice, segments } = parseEnunciatedChapter(markdown);

    if (!voice) {
      console.error(`  ch ${num}: no VOICE: block found, skipping`);
      continue;
    }

    chapters.push({ voice, segments, output: dest, _num: num });
  }

  if (!chapters.length) {
    console.log("Nothing to synthesise.");
    return;
  }

  // Decide backend: warm server vs one-shot Python
  let useServer = false;
  if (!noServer && serverUrl) {
    console.log(`Checking TTS server at ${serverUrl} …`);
    useServer = await checkServer(serverUrl);
    if (useServer) {
      console.log("  Server is up — using warm server mode.\n");
    } else {
      console.log("  Server not reachable — falling back to one-shot Python.\n");
    }
  }

  const segTotal = chapters.reduce((n, c) => n + c.segments.length, 0);
  const backendLabel = useServer ? `server ${serverUrl}` : `one-shot Python, ${device}`;
  console.log(
    `Synthesising ${chapters.length} chapter(s), ${segTotal} segments ` +
      `[${mode} mode, ${backendLabel}]\n`
  );

  const manifestChapters = chapters.map(({ voice, segments, output: out }) => ({
    voice,
    segments,
    output: out,
  }));

  const t0 = Date.now();

  try {
    const synthesize = useServer ? synthesizeViaServer : synthesizeChapters;
    const result = await synthesize(manifestChapters, {
      mode,
      device,
      language,
      python: argv.python,
      serverUrl,
      noFallback: useServer,
    });

    const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
    console.log("");

    for (const r of result.results || []) {
      const rel = path.relative(root, r.output);
      if (r.ok) {
        const stat = fse.statSync(r.output);
        const mb = (stat.size / 1_048_576).toFixed(1);
        console.log(`  ✓ ${rel}  (${mb} MB)`);
      } else {
        console.error(`  ✗ ${rel}: ${r.error}`);
      }
    }

    console.log(`\nDone in ${elapsed}s.`);
  } catch (err) {
    console.error(`\nTTS failed: ${err.message}`);
    process.exit(1);
  }
}
