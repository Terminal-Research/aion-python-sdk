#!/usr/bin/env node

import React from "react";
import { render } from "ink";

import { parseArgs, printHelp } from "./args.js";
import { ChatApp } from "./app.js";

function main(): void {
	try {
		const options = parseArgs(process.argv.slice(2));
		render(<ChatApp options={options} />, {
			exitOnCtrlC: false
		});
	} catch (error) {
		process.stderr.write(
			`${error instanceof Error ? error.message : String(error)}\n\n`
		);
		printHelp();
		process.exit(1);
	}
}

main();
