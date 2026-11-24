import { Artifact, Message, Role, Task, TaskArtifactUpdateEvent, TaskState } from "@a2a-js/sdk";
import { describe, expect, it } from "vitest";
import { ResponseAnswer, responseMessages } from "../src/lib/responseAnswer.js";
import { formatMessageParts } from "../src/lib/messageDisplay.js";
import { restoredTranscriptEntries } from "../src/lib/restoredTranscript.js";
import { reconcileResponseTranscript, type TranscriptEntry } from "../src/lib/transcript.js";

const assistant = "aion:assistant-answer";
const stream = "aion:stream-delta";
const message = (id: string, text: string, role = Role.ROLE_AGENT) => Message.fromJSON({
	messageId: id, taskId: "task", contextId: "context", role, parts: [{ text }]
});
const artifact = (artifactId: string, text: string, source?: string) => Artifact.fromJSON({
	artifactId, parts: [{ text }], metadata: source ? { "aion:sourceMessageId": source } : undefined
});
const snapshot = (artifacts: Artifact[] = [], history: Message[] = [], state = TaskState.TASK_STATE_COMPLETED) =>
	({ ...Task.fromJSON({ id: "task", contextId: "context", status: { state } }), artifacts, history });
const update = (text: string, append = false, artifactId = assistant) => ({
	...TaskArtifactUpdateEvent.fromJSON({ taskId: "task", contextId: "context", append }),
	artifact: artifact(artifactId, text)
});
const body = (answer: ResponseAnswer) => {
	const reply = answer.message();
	return reply ? formatMessageParts(reply.parts) : undefined;
};

describe("canonical response selection", () => {
	it("uses the artifact's own parts without requiring its source message", () => {
		const answer = new ResponseAnswer();
		answer.observe(snapshot([artifact(stream, "draft"), artifact(assistant, "canonical", "absent")],
			[message("message", "fallback")]));
		expect(body(answer)).toBe("canonical");
	});

	it("appends, resets and accepts the latest complete snapshot", () => {
		const answer = new ResponseAnswer();
		answer.observe(update("first"));
		answer.observe(update("second", true));
		expect(body(answer)).toBe("first\nsecond");
		answer.observe(update("reset"));
		answer.observe(update("tail", true));
		expect(body(answer)).toBe("reset\ntail");
		answer.observe(snapshot([artifact(assistant, "snapshot")], [message("source", "different")]));
		answer.observe(snapshot([artifact(assistant, "snapshot")]));
		expect(body(answer)).toBe("snapshot");
		answer.observe(update("latest"));
		answer.observe(snapshot());
		expect(body(answer)).toBe("latest");
	});

	it("stops fallback after an empty answer replacement", () => {
		const answer = new ResponseAnswer();
		answer.observe(update("old"));
		answer.observe(update(""));
		answer.observe(update("Stream", false, stream));
		answer.observe(update("ed", true, stream));
		answer.observe(update("", false, stream));
		expect(body(answer)).toBe("");
	});

	it("keeps nonempty content across empty appends, but accepts blank snapshots", () => {
		const answer = new ResponseAnswer();
		answer.observe(update("First"));
		answer.observe(update("", true));
		expect(body(answer)).toBe("First");
		answer.observe(snapshot([artifact(assistant, " \t\n")], [message("fallback", "message")]));
		expect(body(answer)?.trim()).toBe("");
		answer.observe(snapshot());
		expect(body(answer)?.trim()).toBe("");
	});

	it("keeps the artifact's files without borrowing message files", () => {
		const answer = new ResponseAnswer();
		const ownFile = Artifact.fromJSON({ artifactId: assistant, parts: [{ url: "https://files.test/answer.pdf" }] });
		const fallback = Message.fromJSON({ ...message("source", "fallback"), parts: [{ url: "https://files.test/draft.pdf" }] });
		answer.observe(snapshot([ownFile], [fallback]));
		expect(body(answer)).toContain("answer.pdf");
		expect(body(answer)).not.toContain("draft.pdf");
		answer.observe(update(""));
		expect(body(answer)).toBe("");
	});

	it("removes this turn's draft for an empty final answer without an empty bubble", () => {
		const answer = new ResponseAnswer("request");
		const draftId = "artifact:task:aion:stream-delta:1";
		const entries: TranscriptEntry[] = [
			{ id: "previous", role: "agent", body: "earlier turn", isFinalized: true },
			{ id: draftId, role: "agent", body: "draft", isFinalized: false }
		];
		answer.observe(update(""));
		const final = reconcileResponseTranscript(entries, answer, body(answer)!, new Set([draftId]));
		expect(final.map((entry) => entry.body)).toEqual(["earlier turn"]);
		expect(restoredTranscriptEntries(responseMessages(snapshot([artifact(assistant, "")])))).toEqual([]);
	});

	it("prefers current-turn messages to aggregated stream text", () => {
		const answer = new ResponseAnswer("request");
		answer.observe(snapshot([artifact(stream, "draft")], [message("old", "previous"),
			message("request", "question", Role.ROLE_USER), message("a", "one"), message("b", "two")]));
		expect(body(answer)).toBe("one\n\ntwo");
	});

	it("does not use an older user turn when the requested turn is absent", () => {
		const answer = new ResponseAnswer("request");
		answer.observe(snapshot([], [message("older-request", "old", Role.ROLE_USER), message("old", "previous")]));
		expect(body(answer)).toBeUndefined();
	});

	it("does not reuse opening active-task history as a final answer", () => {
		const answer = new ResponseAnswer();
		const history = [message("old", "previous")];
		answer.observe(snapshot([], history, TaskState.TASK_STATE_WORKING));
		answer.observe(snapshot([], history));
		expect(body(answer)).toBeUndefined();
	});

	it("does not treat generic results, thoughts or file-only answers as text", () => {
		const answer = new ResponseAnswer();
		const fileOnly = Artifact.fromJSON({ artifactId: assistant, parts: [{ url: "https://files.test/a.pdf" }] });
		answer.observe(snapshot([artifact("report", "report"), artifact("aion:thinking-delta", "thought"), fileOnly]));
		expect(answer.message()?.parts.filter((part) => part.content?.$case === "text" && part.content.value)).toEqual([]);
		expect(body(answer)).toContain("https://files.test/a.pdf");
		answer.observe(snapshot([fileOnly], [message("fallback", "message")]));
		expect(body(answer)).not.toContain("message");
		expect(body(answer)).toContain("https://files.test/a.pdf");
	});

	it("keeps answers scoped to their task and each new response turn", () => {
		const answer = new ResponseAnswer();
		answer.observe(update("first task"));
		answer.observe({ ...snapshot(), id: "other-task" });
		expect(body(answer)).toBeUndefined();
		expect(body(new ResponseAnswer())).toBeUndefined();
	});

	it("does not replay an unchanged opening stream artifact from an earlier turn", () => {
		const answer = new ResponseAnswer();
		const old = artifact(stream, "old stream");
		answer.observe(snapshot([old], [], TaskState.TASK_STATE_WORKING));
		answer.observe(snapshot([old]));
		expect(body(answer)).toBeUndefined();
		answer.observe(update("new stream", false, stream));
		answer.observe(snapshot([old]));
		expect(body(answer)).toBe("new stream");
	});

	it.each([TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED,
		TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_CANCELED])(
		"keeps the task's prompt or failure explanation visible (%s)", (state) => {
			const answer = new ResponseAnswer();
			const task = snapshot([artifact(assistant, "earlier answer")], [], state);
			task.status!.message = message("notice", "Action required");
			answer.observe(task);
			expect(body(answer)).toBe("Action required");
		});

	it("saves artifact-only welcomes and restores the same canonical content once", () => {
		const finalTask = snapshot([artifact(assistant, "Welcome", "copy")], [message("copy", "obsolete")]);
		const saved = responseMessages(finalTask);
		const restored = restoredTranscriptEntries(saved);
		expect(saved).toHaveLength(1);
		expect(restored.map((entry) => entry.body)).toEqual(["Welcome"]);
	});

	it("does not erase a source message displayed in a previous response", () => {
		const answer = new ResponseAnswer("new-request");
		answer.observe(snapshot([artifact(assistant, "new answer", "old-source")]));
		const old: TranscriptEntry = { id: "message:task:old-source", role: "agent", body: "previous turn", isFinalized: true };
		expect(reconcileResponseTranscript([old], answer, body(answer)!, new Set()).map((entry) => entry.body))
			.toEqual(["previous turn", "new answer"]);
	});

	it("replaces streamed sections and the copied source without replacing thoughts or progress", () => {
		const answer = new ResponseAnswer("request");
		answer.observe(snapshot([artifact(assistant, "final", "copy")]));
		const entry = (id: string, body: string): TranscriptEntry => ({ id, body, role: "agent", isFinalized: false });
		const entries = [entry("artifact:task:aion:stream-delta:0", "earlier turn"),
			entry("message:task:progress", "working"),
			entry("artifact:task:aion:stream-delta:1", "draft"),
			entry("artifact:task:aion:thinking-delta:2", "thinking"),
			entry("artifact:task:aion:stream-delta:3", "more draft"),
			entry("message:task:copy", "obsolete")];
		const streams = new Set(["artifact:task:aion:stream-delta:1", "artifact:task:aion:stream-delta:3", "message:task:copy"]);
		const final = reconcileResponseTranscript(entries, answer, body(answer)!, streams);
		expect(final.map((entry) => entry.body)).toEqual(["earlier turn", "working", "final", "thinking"]);
		expect(reconcileResponseTranscript(final, answer, "replacement", streams).map((entry) => entry.body))
			.toEqual(["earlier turn", "working", "replacement", "thinking"]);
	});
});
