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
		const nextSession: AgentContextSessionFile = {
			schemaVersion: 1,
			environment: snapshot.environment,
			agentKey: snapshot.agentKey,
			contextId: snapshot.contextId,
			createdAt: existing?.createdAt ?? now,
			lastUpdatedAt: now,
			localTurnCount: (existing?.localTurnCount ?? 0) + 1,
			...(snapshot.lastTaskId ? { lastTaskId: snapshot.lastTaskId } : {}),
			...(existing?.summary ? { summary: existing.summary } : {}),
			messages: snapshot.messages
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
