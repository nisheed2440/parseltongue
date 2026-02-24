#!/usr/bin/env node
import yargs from "yargs";
import { hideBin } from "yargs/helpers";

yargs(hideBin(process.argv))
  .scriptName("parseltongue")
  .usage("$0 <command> [options]")
  .commandDir("commands", { extensions: ["js"] })
  .demandCommand(1, "Specify a command. Run with --help to see available commands.")
  .strict()
  .alias("h", "help")
  .alias("v", "version")
  .parse();
