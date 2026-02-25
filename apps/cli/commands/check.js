import { checkServer } from "@parseltongue/tts";

export const command = "check";
export const describe = "Verify that pipeline prerequisites are available";

export function builder(yargs) {
  return yargs
    .option("server", {
      type: "string",
      describe: "TTS server URL to check (default: $TTS_SERVER_URL or http://localhost:7860)",
    })
    .example("$0 check", "Check all prerequisites");
}

async function checkPython() {
  const python = process.env.PYTHON_BIN || "python";
  try {
    const { execSync } = await import("child_process");
    const version = execSync(`${python} --version`, { encoding: "utf-8" }).trim();
    return { ok: true, detail: version };
  } catch {
    return { ok: false, detail: `"${python}" not found` };
  }
}

async function checkQwenTts() {
  const python = process.env.PYTHON_BIN || "python";
  try {
    const { execSync } = await import("child_process");
    execSync(`${python} -c "import qwen_tts"`, { encoding: "utf-8", stdio: "pipe" });
    return { ok: true, detail: "installed" };
  } catch {
    return { ok: false, detail: "qwen-tts not installed (pip install qwen-tts)" };
  }
}

async function checkTtsServer(serverUrl) {
  const url = serverUrl || process.env.TTS_SERVER_URL || "http://localhost:7860";
  const reachable = await checkServer(url);
  if (reachable) {
    return { ok: true, detail: `reachable at ${url}` };
  }
  return { ok: false, detail: `not reachable at ${url} (start with: npm run tts:serve)` };
}

function line(label, result) {
  const icon = result.ok ? "\u2713" : "\u2717";
  console.log(`  ${icon} ${label}: ${result.detail}`);
}

export async function handler(argv) {
  console.log("Checking parseltongue prerequisites:\n");

  const [python, qwenTts, ttsServer] = await Promise.all([
    checkPython(),
    checkQwenTts(),
    checkTtsServer(argv.server),
  ]);

  console.log("Phase 2 (enunciate):");
  console.log("  \u2713 No external dependencies required.\n");

  console.log("Phase 3 (speak):");
  line("Python", python);
  line("qwen-tts package", qwenTts);
  line("TTS server", ttsServer);
  console.log("");

  const coreOk = python.ok && qwenTts.ok;
  if (coreOk && ttsServer.ok) {
    console.log("All checks passed. TTS server is running — speak will use warm server mode.");
  } else if (coreOk) {
    console.log(
      "Core checks passed. TTS server is not running — speak will use one-shot Python mode.\n" +
        "For faster synthesis, start the server: npm run tts:serve"
    );
  } else {
    console.log("Some checks failed. See above for details.");
    process.exit(1);
  }
}
