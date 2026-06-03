import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import path from "node:path";

import pino, { type DestinationStream, type Logger } from "pino";

import { resolveAionConfigDirectory } from "./chatSettings.js";
import type { AionEnvironmentId } from "./environment.js";

const LOG_DIRECTORY_NAME = "chat-session-logs";
const REDACTED = "[REDACTED]";
const MAX_STRING_LENGTH = 4_000;
const MAX_ARRAY_LENGTH = 50;
const MAX_OBJECT_DEPTH = 8;

export type ChatSessionLogLevel = "debug" | "info" | "warn" | "error";

export interface ChatSessionLogEvent {
	event: string;
	data?: Record<string, unknown>;
}

export interface ChatSessionLogger {
	chatSessionId: string;
	logFilePath: string;
	level: ChatSessionLogLevel;
	debug(event: string, data?: Record<string, unknown>): void;
	info(event: string, data?: Record<string, unknown>): void;
	warn(event: string, data?: Record<string, unknown>): void;
	error(event: string, data?: Record<string, unknown>): void;
	flush(): void;
}

export interface CreateChatSessionLoggerOptions {
	environmentId: AionEnvironmentId;
	env?: NodeJS.ProcessEnv;
	homeDirectory?: string;
	now?: Date;
	chatSessionId?: string;
	sync?: boolean;
}

export interface CreateChatSessionLoggerResult {
	logger: ChatSessionLogger;
	warning?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function resolveChatSessionLogsDirectory(
	env: NodeJS.ProcessEnv = process.env,
	homeDirectory?: string
): string {
	return path.join(resolveAionConfigDirectory(env, homeDirectory), LOG_DIRECTORY_NAME);
}

export function formatLogTimestamp(value: Date): string {
	return value.toISOString().replace(/[:.]/gu, "-");
}

export function resolveChatSessionLogFilePath({
	env = process.env,
	homeDirectory,
	now = new Date(),
	chatSessionId = randomUUID()
}: {
	env?: NodeJS.ProcessEnv;
	homeDirectory?: string;
	now?: Date;
	chatSessionId?: string;
}): string {
	return path.join(
		resolveChatSessionLogsDirectory(env, homeDirectory),
		`${formatLogTimestamp(now)}_${chatSessionId}.jsonl`
	);
}

export function logLevelForEnvironment(
	environmentId: AionEnvironmentId
): ChatSessionLogLevel {
	return environmentId === "development" ? "debug" : "warn";
}

function isSensitiveKey(key: string): boolean {
	return /authorization|cookie|token|secret|password|keyring/iu.test(key);
}

function redactString(value: string): string {
	const redacted = value
		.replace(/Bearer\s+[A-Za-z0-9._~+/-]+=*/giu, `Bearer ${REDACTED}`)
		.replace(/([?&](?:access|refresh)?_?token=)[^&\s]+/giu, `$1${REDACTED}`)
		.replace(/((?:access|refresh)_token=)[^&\s]+/giu, `$1${REDACTED}`);

	if (redacted.length <= MAX_STRING_LENGTH) {
		return redacted;
	}
	return `${redacted.slice(0, MAX_STRING_LENGTH)}...`;
}

function sanitizeValue(
	value: unknown,
	seen: WeakSet<object>,
	depth: number
): unknown {
	if (value === null || value === undefined) {
		return value;
	}
	if (typeof value === "string") {
		return redactString(value);
	}
	if (typeof value === "number" || typeof value === "boolean") {
		return value;
	}
	if (typeof value === "bigint") {
		return value.toString();
	}
	if (typeof value === "function" || typeof value === "symbol") {
		return `[${typeof value}]`;
	}
	if (value instanceof Error) {
		return {
			name: value.name,
			message: redactString(value.message)
		};
	}
	if (value instanceof URL) {
		return redactString(value.toString());
	}
	if (value instanceof Headers) {
		return sanitizeValue(Object.fromEntries(value.entries()), seen, depth + 1);
	}
	if (depth >= MAX_OBJECT_DEPTH) {
		return "[MaxDepth]";
	}
	if (seen.has(value)) {
		return "[Circular]";
	}
	seen.add(value);
	if (Array.isArray(value)) {
		const items = value
			.slice(0, MAX_ARRAY_LENGTH)
			.map((item) => sanitizeValue(item, seen, depth + 1));
		if (value.length > MAX_ARRAY_LENGTH) {
			items.push(`... ${value.length - MAX_ARRAY_LENGTH} more items`);
		}
		return items;
	}
	if (isRecord(value)) {
		return Object.fromEntries(
			Object.entries(value).map(([key, entry]) => [
				key,
				isSensitiveKey(key) ? REDACTED : sanitizeValue(entry, seen, depth + 1)
			])
		);
	}
	return String(value);
}

export function sanitizeLogPayload(
	payload: Record<string, unknown> = {}
): Record<string, unknown> {
	return sanitizeValue(payload, new WeakSet<object>(), 0) as Record<string, unknown>;
}

function createDisabledLogger(
	chatSessionId: string,
	logFilePath: string,
	level: ChatSessionLogLevel
): ChatSessionLogger {
	const noop = (): void => undefined;
	return {
		chatSessionId,
		logFilePath,
		level,
		debug: noop,
		info: noop,
		warn: noop,
		error: noop,
		flush: noop
	};
}

function writeEvent(
	logger: Logger,
	level: ChatSessionLogLevel,
	event: string,
	data?: Record<string, unknown>
): void {
	logger[level]({
		event,
		...(data ? { data: sanitizeLogPayload(data) } : {})
	});
}

export function createChatSessionLogger(
	options: CreateChatSessionLoggerOptions
): CreateChatSessionLoggerResult {
	const chatSessionId = options.chatSessionId ?? randomUUID();
	const logFilePath = resolveChatSessionLogFilePath({
		env: options.env,
		homeDirectory: options.homeDirectory,
		now: options.now,
		chatSessionId
	});
	const level = logLevelForEnvironment(options.environmentId);

	try {
		mkdirSync(path.dirname(logFilePath), { recursive: true });
		const destination = pino.destination({
			dest: logFilePath,
			sync: options.sync ?? false
		}) as DestinationStream & { flushSync?: () => void };
		const logger = pino(
			{
				level,
				base: {
					chatSessionId,
					environmentId: options.environmentId
				},
				timestamp: pino.stdTimeFunctions.isoTime,
				formatters: {
					level(label) {
						return { level: label };
					}
				}
			},
			destination
		);

		return {
			logger: {
				chatSessionId,
				logFilePath,
				level,
				debug: (event, data) => writeEvent(logger, "debug", event, data),
				info: (event, data) => writeEvent(logger, "info", event, data),
				warn: (event, data) => writeEvent(logger, "warn", event, data),
				error: (event, data) => writeEvent(logger, "error", event, data),
				flush: () => {
					destination.flushSync?.();
				}
			}
		};
	} catch (error) {
		const message = error instanceof Error ? error.message : String(error);
		return {
			logger: createDisabledLogger(chatSessionId, logFilePath, level),
			warning: `Aion Chat could not create run-session log: ${message}`
		};
	}
}
