import {
	type AionEnvironmentId,
	AION_ENVIRONMENT_IDS,
	isAionEnvironmentId,
	getControlPlaneApiBaseUrl
} from "./lib/environment.js";
import {
	type RequestMode,
	type ResponseMode
} from "./lib/slashCommands.js";
import { getPackageInfo } from "./packageInfo.js";

export interface ChatCliOptions {
	environmentId?: AionEnvironmentId;
	controlPlaneOrigin?: string;
	workspace?: string;
	newSession?: boolean;
	url?: string;
	agentId?: string;
	token?: string;
	headers: Record<string, string>;
	pushNotifications: boolean;
	pushReceiver: string;
}

export interface HeadlessRunOptions extends ChatCliOptions {
	agentSelector?: string;
	requestMode: RequestMode;
	responseMode: ResponseMode;
	message?: string;
	readMessageFromStdin: boolean;
}

export type CliCommand =
	| {
			kind: "chat";
			options: ChatCliOptions;
	  }
	| {
			kind: "run";
			options: HeadlessRunOptions;
	  }
	| {
			kind: "login";
	  }
	| {
			kind: "environment";
			environmentId: AionEnvironmentId;
			controlPlaneOrigin?: string;
	  };

const HELP_TEXT = `
Usage:
  aio [options]
  aio run [options] [message]
  aio login
  aion-chat [options]
  aion-chat run [options] [message]

Options:
      --env <environment> [origin]  production, staging, or development; saves selection
                                   Optional HTTP(S) API origin for development only
  -u, --url, --host <endpoint>   Agent or proxy URL to connect to
      --workspace <name-or-id>   Workspace whose registry catalog to search
      --agent-id <agent-id>      Agent identifier for proxy-aware routing
      --token <token>            Bearer token for the explicit --url endpoint
      --header <key=value>       Repeatable custom HTTP header for the explicit --url endpoint
      --push-notifications       Enable the local push notification receiver
      --push-receiver <url>      Push notification receiver URL
      --help                     Show this help text
      --version                  Print the package version

Composer controls:
  Enter          Send message or select the active menu item
  Shift+Enter    Insert newline
  Arrow keys     Move the cursor or navigate an open menu
  Home/End       Move to the start/end of the current composer row
  @              Open the agent picker
  /              Open the slash command picker
  Esc            Dismiss the active menu or clear the draft
  Ctrl+C         Clear the draft or exit when empty
`.trim();

const RUN_HELP_TEXT = `
Usage:
  aio run [options] [message]
  aion-chat run [options] [message]

Send one non-interactive message to an Aion agent without opening the terminal UI.

Message input:
  Pass the message as positional text, pass "-" to read stdin, or pipe stdin with no
  positional message.

Agent selection:
      --agent <selector>         Discovered agent handle, display id, identity id, or agent key
      --workspace <name-or-id>   Workspace whose registry catalog to search
      --agent-id <agent-id>      Agent identifier for proxy-aware routing with --url
  -u, --url, --host <endpoint>   Explicit A2A endpoint or proxy URL

Authentication:
      --env <environment> [origin]  production, staging, or development; saves selection
                                   Optional HTTP(S) API origin for development only
      --new-session              Replace guest identity for --url; prior history becomes inaccessible
      --token <token>            Bearer token for the explicit --url endpoint
      --header <key=value>       Repeatable custom HTTP header for the explicit --url endpoint

Modes:
      --request-mode <mode>      send-message or streaming-message (default: send-message)
      --response-mode <mode>     message or a2a (default: message)

Push notifications:
      --push-notifications       Include local push notification configuration
      --push-receiver <url>      Push notification receiver URL

Output:
  message mode writes rendered agent output to stdout and diagnostics to stderr.
  a2a mode writes raw A2A JSON to stdout. Streaming a2a mode writes JSONL events.
  If streaming is requested but unsupported, aio falls back to send-message and
  writes a notice to stderr.

Examples:
  aio run --agent @team-agent "Summarize the latest status"
  aio run --env development http://localhost:8081 --agent @team-agent "Hello"
  cat prompt.txt | aio run --agent @team-agent -
  aio run --url http://localhost:8000 --agent-id demo-agent "Hello"
  aio run --agent @team-agent --request-mode streaming-message "Hello"
  aio run --agent @team-agent --response-mode a2a "Hello"
`.trim();

const REQUEST_MODE_ALIASES: Record<string, RequestMode> = {
	"send-message": "send-message",
	send: "send-message",
	message: "send-message",
	"streaming-message": "streaming-message",
	streaming: "streaming-message",
	stream: "streaming-message"
};

const RESPONSE_MODE_ALIASES: Record<string, ResponseMode> = {
	message: "message-output",
	"message-output": "message-output",
	a2a: "a2a-protocol",
	"a2a-protocol": "a2a-protocol"
};

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

function parseRequestMode(value: string): RequestMode {
	const mode = REQUEST_MODE_ALIASES[value];
	if (!mode) {
		throw new Error(
			"Invalid --request-mode value, expected send-message or streaming-message"
		);
	}
	return mode;
}

function parseResponseMode(value: string): ResponseMode {
	const mode = RESPONSE_MODE_ALIASES[value];
	if (!mode) {
		throw new Error("Invalid --response-mode value, expected message or a2a");
	}
	return mode;
}

function printVersion(): void {
	process.stdout.write(`${getPackageInfo().version}\n`);
}

export function printHelp(): void {
	process.stdout.write(`${HELP_TEXT}\n`);
}

export function printRunHelp(): void {
	process.stdout.write(`${RUN_HELP_TEXT}\n`);
}

function parseEnvironmentCommand(argv: string[]): CliCommand | undefined {
	const [command, environmentId, controlPlaneOrigin, extra] = argv;
	if (command !== "environment" && command !== "env") {
		return undefined;
	}
	if (!environmentId || extra) {
		throw new Error(
			`Expected an environment and optional development API origin: ${AION_ENVIRONMENT_IDS.join(", ")}`
		);
	}
	if (!isAionEnvironmentId(environmentId)) {
		throw new Error(
			`Unknown environment '${environmentId}', expected one of ${AION_ENVIRONMENT_IDS.join(", ")}`
		);
	}

	return {
		kind: "environment",
		environmentId,
		...(controlPlaneOrigin !== undefined ? { controlPlaneOrigin: getControlPlaneApiBaseUrl(environmentId, controlPlaneOrigin) } : {})
	};
}

function readEnvironmentFlag(argv: string[], index: number): {
	environmentId: AionEnvironmentId; controlPlaneOrigin?: string; consumed: number
} {
	const environmentId = requireValue(argv, index, "--env");
	if (!isAionEnvironmentId(environmentId)) throw new Error(`Unknown environment '${environmentId}'.`);
	const candidate = argv[index + 2];
	const hasOrigin = !!candidate && /^[a-z][a-z0-9+.-]*:\/\//i.test(candidate);
	return { environmentId, consumed: hasOrigin ? 2 : 1,
		...(hasOrigin ? { controlPlaneOrigin: getControlPlaneApiBaseUrl(environmentId, candidate) } : {}) };
}

export function parseArgs(argv: string[]): ChatCliOptions {
	let environment: Pick<ChatCliOptions, "environmentId" | "controlPlaneOrigin"> = {};
	let url: string | undefined;
	let workspace: string | undefined;
	let agentId: string | undefined;
	let token: string | undefined;
	let pushNotifications = false;
	let pushReceiver = "http://localhost:5000";
	const headers: Record<string, string> = {};

	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		switch (arg) {
			case "--env": {
				const { consumed, ...selection } = readEnvironmentFlag(argv, index);
				environment = selection;
				index += consumed;
				break;
			}
			case "--url":
			case "--host":
			case "-u":
				url = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--workspace":
				workspace = requireValue(argv, index, arg).trim();
				if (!workspace) throw new Error("--workspace requires a name or ID.");
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
			case "--version":
				printVersion();
				process.exit(0);
			default:
				throw new Error(`Unknown argument '${arg}'`);
		}
	}

	if (workspace !== undefined && url !== undefined) throw new Error("--workspace cannot be combined with --url; choose a registry workspace or a direct endpoint.");
	return {
		...environment,
		...(workspace !== undefined ? { workspace } : {}),
		...(url ? { url } : {}),
		agentId,
		token,
		headers,
		pushNotifications,
		pushReceiver
	};
}

export function parseRunArgs(argv: string[]): HeadlessRunOptions {
	let newSession = false;
	let environment: Pick<ChatCliOptions, "environmentId" | "controlPlaneOrigin"> = {};
	let url: string | undefined;
	let workspace: string | undefined;
	let agentId: string | undefined;
	let agentSelector: string | undefined;
	let token: string | undefined;
	let pushNotifications = false;
	let pushReceiver = "http://localhost:5000";
	let requestMode: RequestMode = "send-message";
	let responseMode: ResponseMode = "message-output";
	let readMessageFromStdin = false;
	const headers: Record<string, string> = {};
	const messageArgs: string[] = [];

	for (let index = 0; index < argv.length; index += 1) {
		const arg = argv[index];
		switch (arg) {
			case "--env": {
				const { consumed, ...selection } = readEnvironmentFlag(argv, index);
				environment = selection;
				index += consumed;
				break;
			}
			case "--url":
			case "--host":
			case "-u":
				url = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--workspace":
				workspace = requireValue(argv, index, arg).trim();
				if (!workspace) throw new Error("--workspace requires a name or ID.");
				index += 1;
				break;
			case "--agent-id":
				agentId = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--agent":
				agentSelector = requireValue(argv, index, arg);
				index += 1;
				break;
			case "--new-session":
				newSession = true;
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
			case "--request-mode":
				requestMode = parseRequestMode(requireValue(argv, index, arg));
				index += 1;
				break;
			case "--response-mode":
				responseMode = parseResponseMode(requireValue(argv, index, arg));
				index += 1;
				break;
			case "--help":
				printRunHelp();
				process.exit(0);
			case "--version":
				printVersion();
				process.exit(0);
			case "--":
				messageArgs.push(...argv.slice(index + 1));
				index = argv.length;
				break;
			case "-":
				readMessageFromStdin = true;
				break;
			default:
				if (arg.startsWith("-")) {
					throw new Error(`Unknown argument '${arg}'`);
				}
				messageArgs.push(arg);
		}
	}

	if (workspace !== undefined && url !== undefined) throw new Error("--workspace cannot be combined with --url; choose a registry workspace or a direct endpoint.");
	return {
		...environment,
		...(workspace !== undefined ? { workspace } : {}),
		...(url ? { url } : {}),
		agentId,
		agentSelector,
		...(newSession ? { newSession: true } : {}),
		token,
		headers,
		pushNotifications,
		pushReceiver,
		requestMode,
		responseMode,
		readMessageFromStdin,
		...(messageArgs.length > 0 ? { message: messageArgs.join(" ") } : {})
	};
}

export function parseCliArgs(argv: string[]): CliCommand {
	const normalized: string[] = [];
	for (let index = 0; index < argv.length; index++) {
		const arg = argv[index];
		if (arg === "--") { normalized.push(...argv.slice(index)); break; }
		if (arg.startsWith("--workspace=")) normalized.push("--workspace", arg.slice(12));
		else if (arg.startsWith("--env=")) normalized.push("--env", arg.slice(6));
		else {
			normalized.push(arg);
			if (["--workspace", "--url", "--host", "-u", "--agent-id", "--agent", "--token", "--header", "--push-receiver", "--request-mode", "--response-mode", "--env"].includes(arg) && index + 1 < argv.length) {
				normalized.push(argv[++index]);
			}
		}
	}
	argv = normalized;
	if (argv[0] === "run") {
		return {
			kind: "run",
			options: parseRunArgs(argv.slice(1))
		};
	}

	if (argv[0] === "login") {
		if (argv.length > 1) {
			throw new Error("The login command does not accept arguments.");
		}
		return { kind: "login" };
	}

	const environmentCommand = parseEnvironmentCommand(argv);
	if (environmentCommand) {
		return environmentCommand;
	}

	return {
		kind: "chat",
		options: parseArgs(argv)
	};
}
