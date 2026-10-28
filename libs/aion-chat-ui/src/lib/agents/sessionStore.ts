import { mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

import type { Message } from "@a2a-js/sdk";

import { resolveAionConfigDirectory } from "../chatSettings.js";
import type { AionEnvironmentId } from "../environment.js";
import {
	type AgentContextSessionFile,
	hashValue,
	slugKey
} from "./model.js";

export interface CompletedExchangeSnapshot {
	environment: AionEnvironmentId;
	agentKey: string;
	contextId: string;
	chatSessionId?: string;
	chatSessionLogPath?: string;
	lastTaskId?: string;
	messages: Message[];
}

export function resolveSessionsDirectory(
	env: NodeJS.ProcessEnv = process.env,
	homeDirectory?: string
): string {
	return path.join(resolveAionConfigDirectory(env, homeDirectory), "sessions");
}

export function safeSessionPathSegment(value: string): string {
	return `${slugKey(value)}-${hashValue(value).slice(0, 8)}`;
}

export function resolveAgentSessionsDirectory(
	environment: AionEnvironmentId,
	agentKey: string,
	sessionsDirectory = resolveSessionsDirectory()
): string {
	return path.join(
		sessionsDirectory,
		safeSessionPathSegment(environment),
		safeSessionPathSegment(agentKey)
	);
}

export function resolveSessionFilePath(
	environment: AionEnvironmentId,
	agentKey: string,
	contextId: string,
	sessionsDirectory = resolveSessionsDirectory()
): string {
	return path.join(
		resolveAgentSessionsDirectory(environment, agentKey, sessionsDirectory),
		`${safeSessionPathSegment(contextId)}.json`
	);
}

function readSessionFile(filePath: string): AgentContextSessionFile | undefined {
	try {
		return JSON.parse(readFileSync(filePath, "utf8")) as AgentContextSessionFile;
	} catch {
		return undefined;
	}
}

export function saveCompletedExchange(
	snapshot: CompletedExchangeSnapshot,
	sessionsDirectory = resolveSessionsDirectory()
): string | undefined {
	try {
		const filePath = resolveSessionFilePath(
			snapshot.environment,
			snapshot.agentKey,
			snapshot.contextId,
			sessionsDirectory
		);
		const existing = readSessionFile(filePath);
		const now = new Date().toISOString();
		const chatSessionId = snapshot.chatSessionId ?? existing?.chatSessionId;
		const chatSessionLogPath =
			snapshot.chatSessionLogPath ?? existing?.chatSessionLogPath;
		const nextSession: AgentContextSessionFile = {
			schemaVersion: 1,
			environment: snapshot.environment,
			agentKey: snapshot.agentKey,
			contextId: snapshot.contextId,
			...(chatSessionId ? { chatSessionId } : {}),
			...(chatSessionLogPath ? { chatSessionLogPath } : {}),
			createdAt: existing?.createdAt ?? now,
			lastUpdatedAt: now,
			localTurnCount: (existing?.localTurnCount ?? 0) + 1,
			...(snapshot.lastTaskId ? { lastTaskId: snapshot.lastTaskId } : {}),
			...(existing?.summary ? { summary: existing.summary } : {}),
			messages: [...new Map([
				...(existing?.messages ?? []), ...snapshot.messages
			].map((message) => [message.messageId, message])).values()]
		};
		mkdirSync(path.dirname(filePath), { recursive: true });
		writeFileSync(filePath, `${JSON.stringify(nextSession, null, 2)}\n`, "utf8");
		return undefined;
	} catch (error) {
		return `chat2 could not save session: ${
			error instanceof Error ? error.message : String(error)
		}`;
	}
}

export function loadMostRecentSession(
	environment: AionEnvironmentId,
	agentKey: string,
	sessionsDirectory = resolveSessionsDirectory()
): AgentContextSessionFile | undefined {
	const agentDirectory = resolveAgentSessionsDirectory(
		environment,
		agentKey,
		sessionsDirectory
	);
	try {
		return readdirSync(agentDirectory)
			.filter((fileName) => fileName.endsWith(".json"))
			.map((fileName) => readSessionFile(path.join(agentDirectory, fileName)))
			.filter((session): session is AgentContextSessionFile => Boolean(session))
			.sort((left, right) => right.lastUpdatedAt.localeCompare(left.lastUpdatedAt))[0];
	} catch {
		return undefined;
	}
}

export interface SessionLoadResult {
	session?: AgentContextSessionFile;
	warning?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return !!value && typeof value === "object" && !Array.isArray(value);
}

function isSavedMessage(value: unknown, contextId: string): value is Message {
	if (!isRecord(value) || typeof value.messageId !== "string" || !value.messageId ||
		![1, 2].includes(value.role as number) || (value.contextId !== undefined && value.contextId !== "" && value.contextId !== contextId) ||
		(value.extensions !== undefined && (!Array.isArray(value.extensions) || !value.extensions.every((item) => typeof item === "string"))) ||
		!Array.isArray(value.parts)) return false;
	return value.parts.every((part) => {
		if (!isRecord(part)) return false;
		if (part.content === undefined) return true;
		if (!isRecord(part.content)) return false;
		const content = part.content;
		if (content.$case === "text" || content.$case === "url") return typeof content.value === "string";
		if (content.$case === "data") return isRecord(content.value);
		// JSON.stringify stores a Uint8Array as an object of numeric indices.
		if (content.$case === "raw" && isRecord(content.value)) {
			const bytes = Object.values(content.value);
			if (!bytes.every((byte) => typeof byte === "number" && Number.isInteger(byte) && byte >= 0 && byte <= 255)) return false;
			content.value = Uint8Array.from(bytes as number[]);
			return true;
		}
		return false;
	});
}

/** Read only the validated active context; never substitute another recent conversation. */
export function loadContextSession(
	environment: AionEnvironmentId,
	agentKey: string,
	contextId: string,
	sessionsDirectory = resolveSessionsDirectory()
): SessionLoadResult {
	const session = readSessionFile(resolveSessionFilePath(environment, agentKey, contextId, sessionsDirectory));
	if (!session || session.schemaVersion !== 1 || session.environment !== environment ||
		session.agentKey !== agentKey || session.contextId !== contextId ||
		typeof session.createdAt !== "string" || typeof session.lastUpdatedAt !== "string" ||
		!Number.isInteger(session.localTurnCount) || session.localTurnCount < 0 ||
		!Array.isArray(session.messages) || !session.messages.every((message) => isSavedMessage(message, contextId))) {
		return { warning: "This conversation is still selected, but its saved history could not be loaded." };
	}
	return { session: { ...session, messages: [...new Map(session.messages.map((message) => [message.messageId, message])).values()] } };
}

/** Copy verified legacy home history without removing it or counting another turn. */
export function copyContextSession(
	session: AgentContextSessionFile,
	agentKey: string,
	sessionsDirectory = resolveSessionsDirectory()
): string | undefined {
	try {
		const filePath = resolveSessionFilePath(session.environment, agentKey, session.contextId, sessionsDirectory);
		mkdirSync(path.dirname(filePath), { recursive: true });
		writeFileSync(filePath, `${JSON.stringify({ ...session, agentKey }, null, 2)}\n`, "utf8");
		return undefined;
	} catch { return "The saved history could not be copied into this workspace."; }
}
