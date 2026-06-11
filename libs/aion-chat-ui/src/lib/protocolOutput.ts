import type { StreamResponse } from "@a2a-js/sdk";
import {
	Message,
	StreamResponse as StreamResponseSerializer,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import { stringify as stringifyYaml } from "yaml";

import {
	isMessage,
	isTask,
	isTaskArtifactUpdateEvent,
	isTaskStatusUpdateEvent
} from "./a2aProtocol.js";

function isRecord(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isStreamResponse(value: unknown): value is StreamResponse {
	if (!isRecord(value) || !isRecord(value.payload)) {
		return false;
	}
	if (!("value" in value.payload)) {
		return false;
	}
	return ["task", "message", "statusUpdate", "artifactUpdate"].includes(
		String(value.payload.$case)
	);
}

function toProtocolJsonValue(value: unknown): unknown | undefined {
	if (isStreamResponse(value)) {
		return StreamResponseSerializer.toJSON(value);
	}
	if (isMessage(value)) {
		return Message.toJSON(value);
	}
	if (isTask(value)) {
		return Task.toJSON(value);
	}
	if (isTaskStatusUpdateEvent(value)) {
		return TaskStatusUpdateEvent.toJSON(value);
	}
	if (isTaskArtifactUpdateEvent(value)) {
		return TaskArtifactUpdateEvent.toJSON(value);
	}
	return undefined;
}

function toPlainValue(value: unknown): unknown {
	const protocolJson = toProtocolJsonValue(value);
	if (protocolJson !== undefined) {
		return protocolJson;
	}
	try {
		return JSON.parse(JSON.stringify(value)) as unknown;
	} catch {
		return String(value);
	}
}

export function formatProtocolPayloadAsJson(payload: unknown): string {
	return JSON.stringify(toPlainValue(payload), null, 2);
}

export function formatProtocolPayload(payload: unknown): string {
	const yaml = stringifyYaml(toPlainValue(payload)).trimEnd();
	return `\`\`\`yaml\n${yaml}\n\`\`\``;
}
