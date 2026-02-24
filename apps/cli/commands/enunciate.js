export const command = "enunciate [storyId] [chapter]";
export const describe = "Voice-direct chapter Markdown for spoken delivery";

export function builder(yargs) {
  return yargs
    .positional("storyId", {
      describe: "Story ID to enunciate",
      type: "string",
    })
    .positional("chapter", {
      describe: "Chapter number (default: all chapters)",
      type: "number",
    })
    .example("$0 enunciate 12345", "Enunciate all chapters of story 12345")
    .example("$0 enunciate 12345 3", "Enunciate only chapter 3");
}

export async function handler(argv) {
  console.error(
    "enunciate is not yet implemented.\n" +
      "It will use the voice-direction prompt from @parseltongue/enunciate\n" +
      "to reshape chapter Markdown files for natural vocal performance."
  );
  process.exit(1);
}
