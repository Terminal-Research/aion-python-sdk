import type { Message, Task } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";

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
		kind: "message",
		messageId,
		role,
		parts: [{ kind: "text", text }]
	};
}

describe("message display helpers", () => {
	it("formats text, data, and file parts for display", () => {
		const output = formatMessageParts([
			{ kind: "text", text: "hello" },
			{ kind: "data", data: { answer: 42 } },
			{
				kind: "file",
				file: {
					name: "report.pdf",
					mimeType: "application/pdf",
					uri: "https://example.test/report.pdf"
				}
			}
		]);

		expect(output).toContain("hello");
		expect(output).toContain('"answer": 42');
		expect(output).toContain("File part returned");
		expect(output).toContain('"name": "report.pdf"');
		expect(output).toContain('"uri": "https://example.test/report.pdf"');
	});

	it("uses task history for task output", () => {
		const task = {
			kind: "task",
			id: "task-1",
			contextId: "context-1",
			status: { state: "completed" },
			history: [
				buildMessage("user-1", "user", "question"),
				buildMessage("agent-1", "agent", "answer")
			]
		} as Task;

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});

	it("appends status message when history is present and does not already include it", () => {
		const task = {
			kind: "task",
			id: "task-1",
			contextId: "context-1",
			status: {
				state: "completed",
				message: buildMessage("agent-1", "agent", "answer")
			},
			history: [buildMessage("user-1", "user", "question")]
		} as Task;

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});

	it("does not duplicate status message when history already includes it", () => {
		const statusMessage = buildMessage("agent-1", "agent", "answer");
		const task = {
			kind: "task",
			id: "task-1",
			contextId: "context-1",
			status: {
				state: "completed",
				message: statusMessage
			},
			history: [buildMessage("user-1", "user", "question"), statusMessage]
		} as Task;

		expect(getTaskMessages(task).map((message) => message.messageId)).toEqual([
			"user-1",
			"agent-1"
		]);
	});
});
