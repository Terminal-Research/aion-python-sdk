import type { Message, Task } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";

import {
	getShownMessageKey,
	getUnshownTaskAgentMessages,
	markShownMessage,
	shouldShowNoAgentMessageNotice,
	shouldRenderLiveStatusMessage,
	shouldRenderLiveResponseMessage
} from "../src/lib/chatSession.js";

function buildMessage(
	messageId: string,
	role: "agent" | "user",
	text: string,
	taskId?: string
): Message {
	return {
		kind: "message",
		messageId,
		role,
		...(taskId ? { taskId } : {}),
		parts: [{ kind: "text", text }]
	};
}

describe("chat session message tracking", () => {
	it("keys shown messages by task id and message id", () => {
		const message = buildMessage("message-1", "agent", "hello");

		expect(getShownMessageKey(message, "task-1")).toBe("task-1:message-1");
		expect(getShownMessageKey(message)).toBe("no-task:message-1");
	});

	it("returns unseen agent messages from task history in history order", () => {
		const shown = new Set<string>();
		const task = {
			kind: "task",
			id: "task-1",
			contextId: "context-1",
			status: { state: "completed" },
			history: [
				buildMessage("user-1", "user", "question"),
				buildMessage("agent-1", "agent", "first"),
				buildMessage("agent-2", "agent", "second")
			]
		} as Task;

		markShownMessage(shown, buildMessage("agent-1", "agent", "first"), "task-1");

		expect(
			getUnshownTaskAgentMessages(task, shown).map((message) => message.messageId)
		).toEqual(["agent-2"]);
	});

	it("uses message task id before fallback task id", () => {
		const shown = new Set<string>();
		const message = buildMessage("agent-1", "agent", "first", "message-task");
		const task = {
			kind: "task",
			id: "fallback-task",
			contextId: "context-1",
			status: { state: "completed" },
			history: [message]
		} as Task;

		markShownMessage(shown, message, "fallback-task");

		expect(getUnshownTaskAgentMessages(task, shown)).toHaveLength(0);
	});

	it("only renders agent messages from live responses", () => {
		expect(shouldRenderLiveResponseMessage(buildMessage("agent-1", "agent", "answer"))).toBe(true);
		expect(shouldRenderLiveResponseMessage(buildMessage("user-1", "user", "echo"))).toBe(false);
	});

	it("does not render live status messages after stream output for that task", () => {
		expect(
			shouldRenderLiveStatusMessage({
				message: buildMessage("agent-1", "agent", "answer"),
				taskId: "task-1",
				streamedTaskIds: new Set(["task-1"])
			})
		).toBe(false);
		expect(
			shouldRenderLiveStatusMessage({
				message: buildMessage("agent-1", "agent", "answer"),
				taskId: "task-1",
				streamedTaskIds: new Set()
			})
		).toBe(true);
		expect(
			shouldRenderLiveStatusMessage({
				message: buildMessage("user-1", "user", "echo"),
				taskId: "task-1",
				streamedTaskIds: new Set()
			})
		).toBe(false);
	});

	it("shows a no-output notice only for terminal message output with no rendered agent output", () => {
		expect(
			shouldShowNoAgentMessageNotice({
				responseMode: "message-output",
				reachedTerminal: true,
				renderedAgentOutput: false
			})
		).toBe(true);
		expect(
			shouldShowNoAgentMessageNotice({
				responseMode: "message-output",
				reachedTerminal: false,
				renderedAgentOutput: false
			})
		).toBe(false);
		expect(
			shouldShowNoAgentMessageNotice({
				responseMode: "message-output",
				reachedTerminal: true,
				renderedAgentOutput: true
			})
		).toBe(false);
		expect(
			shouldShowNoAgentMessageNotice({
				responseMode: "a2a-protocol",
				reachedTerminal: true,
				renderedAgentOutput: false
			})
		).toBe(false);
	});
});
