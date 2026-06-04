import type { Message, Part, Task } from "@a2a-js/sdk";
import { Role, TaskState } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";

import { makeTextPart } from "../src/lib/a2aProtocol.js";
import {
	formatMessageParts,
	getTaskMessages
} from "../src/lib/messageDisplay.js";

function buildMessage(
	messageId: string,
	role: "agent" | "user",
	text: string
): Message {
	return {
		messageId,
		contextId: "",
		taskId: "",
		role: role === "agent" ? Role.ROLE_AGENT : Role.ROLE_USER,
		parts: [makeTextPart(text)],
		metadata: undefined,
		extensions: [],
		referenceTaskIds: []
	};
}

function buildTask(overrides: Partial<Task> = {}): Task {
	return {
		id: "task-1",
		contextId: "context-1",
		status: {
			state: TaskState.TASK_STATE_COMPLETED,
			message: undefined,
			timestamp: undefined
		},
		artifacts: [],
		history: [],
		metadata: undefined,
		...overrides
	};
}

describe("message display helpers", () => {
	it("formats text, data, and file parts for display", () => {
		const output = formatMessageParts([
			makeTextPart("hello"),
			{
				content: { $case: "data", value: { answer: 42 } },
				metadata: undefined,
				filename: "",
				mediaType: "application/json"
			} satisfies Part,
			{
				content: { $case: "url", value: "https://example.test/report.pdf" },
				metadata: undefined,
				filename: "report.pdf",
				mediaType: "application/pdf"
			} satisfies Part
		]);

		expect(output).toContain("hello");
		expect(output).toContain('"answer": 42');
		expect(output).toContain("File part returned");
		expect(output).toContain('"name": "report.pdf"');
		expect(output).toContain('"uri": "https://example.test/report.pdf"');
	});

	it("uses task history for task output", () => {
		const task = buildTask({
			history: [
				buildMessage("user-1", "user", "question"),
				buildMessage("agent-1", "agent", "answer")
			]
		});

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});

	it("appends status message when history is present and does not already include it", () => {
		const task = buildTask({
			status: {
				state: TaskState.TASK_STATE_COMPLETED,
				message: buildMessage("agent-1", "agent", "answer"),
				timestamp: undefined
			},
			history: [buildMessage("user-1", "user", "question")]
		});

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});

	it("does not duplicate status message when history already includes it", () => {
		const statusMessage = buildMessage("agent-1", "agent", "answer");
		const task = buildTask({
			status: {
				state: TaskState.TASK_STATE_COMPLETED,
				message: statusMessage,
				timestamp: undefined
			},
			history: [buildMessage("user-1", "user", "question"), statusMessage]
		});

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});
});
