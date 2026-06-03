import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
	createChatSessionLogger,
	formatLogTimestamp,
	logLevelForEnvironment,
	resolveChatSessionLogFilePath,
	resolveChatSessionLogsDirectory,
	sanitizeLogPayload
} from "../src/lib/sessionLogger.js";

const tempDirectories: string[] = [];

afterEach(() => {
	for (const directory of tempDirectories.splice(0)) {
		rmSync(directory, { recursive: true, force: true });
	}
});

function makeTempDirectory(): string {
	const directory = mkdtempSync(path.join(os.tmpdir(), "aion-chat-logs-"));
	tempDirectories.push(directory);
	return directory;
}

function readJsonl(filePath: string): Array<Record<string, unknown>> {
	return readFileSync(filePath, "utf8")
		.trim()
		.split("\n")
		.filter(Boolean)
		.map((line) => JSON.parse(line) as Record<string, unknown>);
}

describe("sessionLogger", () => {
	it("resolves run-session logs into the Aion config directory", () => {
		expect(
			resolveChatSessionLogsDirectory(
				{ XDG_CONFIG_HOME: "/tmp/xdg" } as NodeJS.ProcessEnv,
				"/tmp/home"
			)
		).toBe("/tmp/xdg/aion/chat-session-logs");
		expect(
			resolveChatSessionLogsDirectory({} as NodeJS.ProcessEnv, "/tmp/home")
		).toBe("/tmp/home/.config/aion/chat-session-logs");
	});

	it("uses timestamp-first log file names that sort chronologically", () => {
		expect(formatLogTimestamp(new Date("2026-06-03T18:56:44.242Z"))).toBe(
			"2026-06-03T18-56-44-242Z"
		);
		const first = resolveChatSessionLogFilePath({
			env: { XDG_CONFIG_HOME: "/tmp/xdg" } as NodeJS.ProcessEnv,
			now: new Date("2026-06-03T18:56:44.242Z"),
			chatSessionId: "session-a"
		});
		const second = resolveChatSessionLogFilePath({
			env: { XDG_CONFIG_HOME: "/tmp/xdg" } as NodeJS.ProcessEnv,
			now: new Date("2026-06-03T18:57:01.903Z"),
			chatSessionId: "session-b"
		});

		expect(path.basename(first)).toBe(
			"2026-06-03T18-56-44-242Z_session-a.jsonl"
		);
		expect([second, first].sort()).toEqual([first, second]);
	});

	it("uses debug logging in development and warn logging elsewhere", () => {
		expect(logLevelForEnvironment("development")).toBe("debug");
		expect(logLevelForEnvironment("staging")).toBe("warn");
		expect(logLevelForEnvironment("production")).toBe("warn");
	});

	it("redacts credentials and token-like values before logging", () => {
		const circular: Record<string, unknown> = {};
		circular.self = circular;
		const sanitized = sanitizeLogPayload({
			Authorization: "Bearer secret-token",
			headers: new Headers({
				Authorization: "Bearer header-token",
				Cookie: "session=value"
			}),
			nested: {
				refreshToken: "refresh-token-value",
				url: "https://example.test/path?access_token=secret"
			},
			message: "Authorization: Bearer inline-token",
			circular
		});

		expect(sanitized.Authorization).toBe("[REDACTED]");
		expect(sanitized.headers).toEqual({
			authorization: "[REDACTED]",
			cookie: "[REDACTED]"
		});
		expect(sanitized.nested).toEqual({
			refreshToken: "[REDACTED]",
			url: "https://example.test/path?access_token=[REDACTED]"
		});
		expect(sanitized.message).toBe("Authorization: Bearer [REDACTED]");
		expect(sanitized.circular).toEqual({ self: "[Circular]" });
	});

	it("writes parseable JSONL through the Pino-backed logger", () => {
		const homeDirectory = makeTempDirectory();
		const { logger, warning } = createChatSessionLogger({
			environmentId: "development",
			env: {} as NodeJS.ProcessEnv,
			homeDirectory,
			now: new Date("2026-06-03T18:56:44.242Z"),
			chatSessionId: "chat-session-1",
			sync: true
		});

		expect(warning).toBeUndefined();
		logger.debug("source.discovery.started", {
			Authorization: "Bearer access-token",
			sourceKey: "aion-registry-development"
		});
		logger.flush();

		const events = readJsonl(logger.logFilePath);
		expect(events).toHaveLength(1);
		expect(events[0]).toMatchObject({
			level: "debug",
			chatSessionId: "chat-session-1",
			environmentId: "development",
			event: "source.discovery.started",
			data: {
				Authorization: "[REDACTED]",
				sourceKey: "aion-registry-development"
			}
		});
	});

	it("filters debug and info events outside development", () => {
		const homeDirectory = makeTempDirectory();
		const { logger } = createChatSessionLogger({
			environmentId: "production",
			env: {} as NodeJS.ProcessEnv,
			homeDirectory,
			now: new Date("2026-06-03T18:56:44.242Z"),
			chatSessionId: "chat-session-2",
			sync: true
		});

		logger.debug("debug.event", { value: 1 });
		logger.info("info.event", { value: 2 });
		logger.warn("warn.event", { value: 3 });
		logger.error("error.event", { value: 4 });
		logger.flush();

		const events = readJsonl(logger.logFilePath);
		expect(events.map((event) => event.event)).toEqual([
			"warn.event",
			"error.event"
		]);
	});
});
