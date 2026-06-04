import type {
	Message,
	StreamResponse,
	Task,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import { Role, TaskState } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";

import { makeTextPart } from "../src/lib/a2aProtocol.js";
import {
	formatProtocolPayload,
	formatProtocolPayloadAsJson
} from "../src/lib/protocolOutput.js";

function message(overrides: Partial<Message> = {}): Message {
	return {
		messageId: "message-1",
		contextId: "context-1",
		taskId: "task-1",
		role: Role.ROLE_AGENT,
		parts: [makeTextPart("answer")],
		metadata: undefined,
		extensions: [],
		referenceTaskIds: [],
		...overrides
	};
}

function task(overrides: Partial<Task> = {}): Task {
	return {
		id: "task-1",
		contextId: "context-1",
		status: {
			state: TaskState.TASK_STATE_COMPLETED,
			message: message({ messageId: "status-message-1" }),
			timestamp: undefined
		},
		artifacts: [],
		history: [message({ messageId: "history-message-1" })],
		metadata: undefined,
		...overrides
	};
}

function statusUpdate(
	overrides: Partial<TaskStatusUpdateEvent> = {}
): TaskStatusUpdateEvent {
	return {
		taskId: "task-1",
		contextId: "context-1",
		status: {
			state: TaskState.TASK_STATE_WORKING,
			message: message({ messageId: "status-message-1" }),
			timestamp: undefined
		},
		metadata: undefined,
		...overrides
	};
}

describe("protocol output", () => {
	it("formats protocol payloads as display YAML and copyable JSON", () => {
		const payload = {
			kind: "message",
			role: "agent",
			parts: [{ kind: "text", text: "answer" }]
		};

		expect(formatProtocolPayload(payload)).toContain("```yaml");
		expect(JSON.parse(formatProtocolPayloadAsJson(payload))).toEqual(payload);
		expect(formatProtocolPayloadAsJson(payload)).toContain('\n  "role": "agent"');
	});

	it("uses SDK serializers for message enum fields", () => {
		const json = JSON.parse(formatProtocolPayloadAsJson(message())) as Record<
			string,
			unknown
		>;

		expect(json.role).toBe("ROLE_AGENT");
		expect(formatProtocolPayload(message())).toContain("role: ROLE_AGENT");
	});

	it("uses SDK serializers for task status enum fields", () => {
		const json = JSON.parse(formatProtocolPayloadAsJson(task())) as {
			status?: { state?: string; message?: { role?: string } };
			history?: Array<{ role?: string }>;
		};

		expect(json.status?.state).toBe("TASK_STATE_COMPLETED");
		expect(json.status?.message?.role).toBe("ROLE_AGENT");
		expect(json.history?.[0]?.role).toBe("ROLE_AGENT");
		expect(formatProtocolPayload(task())).toContain("state: TASK_STATE_COMPLETED");
	});

	it("uses SDK serializers for stream response wrappers", () => {
		const streamResponse: StreamResponse = {
			payload: {
				$case: "statusUpdate",
				value: statusUpdate()
			}
		};
		const json = JSON.parse(formatProtocolPayloadAsJson(streamResponse)) as {
			statusUpdate?: {
				status?: { state?: string; message?: { role?: string } };
			};
		};

		expect(json.statusUpdate?.status?.state).toBe("TASK_STATE_WORKING");
		expect(json.statusUpdate?.status?.message?.role).toBe("ROLE_AGENT");
		expect(formatProtocolPayload(streamResponse)).toContain(
			"state: TASK_STATE_WORKING"
		);
	});
});
