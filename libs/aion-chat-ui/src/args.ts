import { readFileSync } from "node:fs";

export const DEFAULT_PROXY_URL = "http://localhost:8000";

export interface ChatCliOptions {
	url: string;
	agentId?: string;
	token?: string;
	headers: Record<string, string>;
	pushNotifications: boolean;
	pushReceiver: string;
}

const HELP_TEXT = `
Usage:
  aio [options]
  aion-chat [options]

Options:
  -u, --url, --host <endpoint>   Agent or proxy URL to connect to (default: ${DEFAULT_PROXY_URL})
      --agent-id <agent-id>      Agent identifier for proxy-aware routing
      --token <token>            Bearer token for authenticated endpoints
      --header <key=value>       Repeatable custom HTTP header
      --push-notifications       Enable the local push notification receiver
      --push-receiver <url>      Push notification receiver URL
      --help                     Show this help text
      --version                  Print the package version

Composer controls:
  Enter          Send message or select the active menu item
  Shift+Enter    Insert newline
  @              Open the agent picker
  /              Open the slash command picker
  /clear         Clear the transcript
  Esc            Dismiss the active menu or clear the draft
  Ctrl+C         Clear the draft or exit when empty
`.trim();

function requireValue(argv: string[], index: number, option: string): string {
	const value = argv[index + 1];
	if (!value || value.startsWith("-")) {
		throw new Error(`Missing value for ${option}`);
	}

	return value;
}

function parseHeader(rawHeader: string): [string, string] {
	const separator = rawHeader.indexOf("=");
	if (separator <= 0) {
		throw new Error(`Invalid --header value '${rawHeader}', expected key=value`);
	}

	return [rawHeader.slice(0, separator), rawHeader.slice(separator + 1)];
}

export function printHelp(): void {
	process.stdout.write(`${HELP_TEXT}\n`);
}

export function parseArgs(argv: string[]): ChatCliOptions {
	let url: string | undefined;
	let agentId: string | undefined;
	let token: string | undefined;
	let pushNotifications = false;
	let pushReceiver = "http://localhost:5000";
	const headers: Record<string, string> = {};

	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		switch (arg) {
			case "--url":
			case "--host":
			case "-u":
				url = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--agent-id":
				agentId = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--token":
				token = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--header": {
				const headerValue = requireValue(argv, index, arg);
				const [key, value] = parseHeader(headerValue);
				headers[key] = value;
				index += 1;
				break;
			}
			case "--push-notifications":
				pushNotifications = true;
				break;
			case "--no-push-notifications":
				pushNotifications = false;
				break;
			case "--push-receiver":
				pushReceiver = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--help":
				printHelp();
				process.exit(0);
			case "--version": {
				const packagePath = new URL("../package.json", import.meta.url);
				const packageJson = JSON.parse(
					readFileSync(packagePath, "utf8")
				) as { version?: string };
				process.stdout.write(`${packageJson.version ?? "0.0.0"}\n`);
				process.exit(0);
			}
			default:
				throw new Error(`Unknown argument '${arg}'`);
		}
	}

	return {
		url: url ?? DEFAULT_PROXY_URL,
		agentId,
		token,
		headers,
		pushNotifications,
		pushReceiver
	};
}
