#!/usr/bin/env node

import React from "react";
import { render } from "ink";

import { parseCliArgs, printHelp } from "./args.js";
import { ChatApp } from "./app.js";
import { openUrlInDefaultBrowser } from "./lib/browser.js";
import { loadChatSettings, saveSelectedEnvironment } from "./lib/chatSettings.js";
import {
	buildWebAppRouteUrl,
	resolvePostAuthPath,
	runLoginBootstrap
} from "./lib/graphql/authBootstrap.js";
import {
	detectPackageUpdate,
	formatInstallCommand,
	getUpdateInstallCommand,
	promptForUpdate,
	runUpdateInstall
} from "./lib/updateCheck.js";
import { runHeadless } from "./lib/headlessRun.js";
import { loginWithWorkOS } from "./lib/workosAuth.js";

async function runLoginCommand(): Promise<void> {
	const { settings } = loadChatSettings();
	const environmentId = settings.selectedEnvironment;
	process.stdout.write(`Starting Aion login for ${environmentId}...\n`);
	const session = await loginWithWorkOS(environmentId, {
		onDeviceAuthorization: async (prompt) => {
			const url = prompt.verificationUriComplete ?? prompt.verificationUri;
			if (await openUrlInDefaultBrowser(url)) {
				process.stdout.write("Opening login screen in default browser.\n");
				process.stdout.write(`Code: ${prompt.userCode}\n`);
				return;
			}

			process.stdout.write(`Open this URL to continue login:\n${url}\n`);
			process.stdout.write(`Code: ${prompt.userCode}\n`);
		},
		onPending: () => {
			process.stdout.write(".");
		},
		onSlowDown: (intervalSeconds) => {
			process.stdout.write(`\nPolling slowed to every ${intervalSeconds}s.\n`);
		}
	});
	const bootstrap = await runLoginBootstrap({
		environmentId,
		accessToken: session.accessToken
	});
	const postAuthPath = resolvePostAuthPath(bootstrap);
	if (postAuthPath) {
		const postAuthUrl = buildWebAppRouteUrl(environmentId, postAuthPath);
		if (await openUrlInDefaultBrowser(postAuthUrl)) {
			process.stdout.write("\nOpening Aion app in default browser.\n");
		} else {
			process.stdout.write(`\nOpen this URL to continue:\n${postAuthUrl}\n`);
		}
	}
	process.stdout.write(`\nLogged in to Aion ${environmentId}.\n`);
}

function runEnvironmentCommand(environmentId: Parameters<typeof saveSelectedEnvironment>[0]): void {
	const warning = saveSelectedEnvironment(environmentId);
	if (warning) {
		throw new Error(warning);
	}
	process.stdout.write(`Aion environment set to ${environmentId}.\n`);
}

async function continueAfterUpdateCheck(): Promise<boolean> {
	if (process.env.AION_CHAT_SKIP_UPDATE_CHECK === "1") {
		return true;
	}
	if (!process.stdin.isTTY || !process.stdout.isTTY) {
		return true;
	}

	const update = await detectPackageUpdate();
	if (!update) {
		return true;
	}

	const choice = await promptForUpdate({ update });
	if (choice === "skip") {
		return true;
	}

	const command = getUpdateInstallCommand(choice, update.packageName);
	process.stdout.write(`\nRunning ${formatInstallCommand(command)}\n`);
	const exitCode = await runUpdateInstall(command);
	process.exitCode = exitCode;
	return false;
}

async function readStdin(): Promise<string> {
	process.stdin.setEncoding("utf8");
	let input = "";
	for await (const chunk of process.stdin) {
		input += String(chunk);
	}
	return input;
}

async function runHeadlessCommand(
	options: Parameters<typeof runHeadless>[0]
): Promise<void> {
	const shouldReadStdin =
		options.readMessageFromStdin ||
		(!options.message && !process.stdin.isTTY);
	const message = shouldReadStdin ? await readStdin() : options.message;
	if (!message?.trim()) {
		throw new Error("Missing message for run command.");
	}

	process.exitCode = await runHeadless({
		...options,
		message
	});
}

async function main(): Promise<void> {
	let parsedCommand: ReturnType<typeof parseCliArgs> | undefined;
	try {
		const command = parseCliArgs(process.argv.slice(2));
		parsedCommand = command;
		if (command.kind === "login") {
			await runLoginCommand();
			return;
		}
		if (command.kind === "environment") {
			runEnvironmentCommand(command.environmentId);
			return;
		}
		if (command.kind === "run") {
			await runHeadlessCommand(command.options);
			return;
		}

		if (!(await continueAfterUpdateCheck())) {
			return;
		}

		render(<ChatApp options={command.options} />, {
			exitOnCtrlC: false
		});
	} catch (error) {
		process.stderr.write(
			`${error instanceof Error ? error.message : String(error)}\n\n`
		);
		if (!parsedCommand) {
			printHelp();
		}
		process.exit(1);
	}
}

void main();
