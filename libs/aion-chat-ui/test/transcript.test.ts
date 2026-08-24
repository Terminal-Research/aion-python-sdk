import { describe, expect, it } from "vitest";

import {
	applyStreamTranscriptDelta,
	createStreamTranscriptState,
	finalizeStreamTranscriptSections,
	getActiveStreamTranscriptSection,
	partitionTranscriptEntries,
	prepareStreamTranscriptDelta,
	replaceActiveStreamTranscriptSection,
	type TranscriptEntry
} from "../src/lib/transcript.js";

describe("stream transcript sections", () => {
	it("appends stream delta text within the active section", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		let result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "hel",
			append: false
		});
		entries = result.entries;

		expect(result.startedNewSection).toBe(true);
		expect(entries).toEqual([
			expect.objectContaining({
				role: "agent",
				body: "hel",
				isFinalized: false
			})
		]);

		result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "lo",
			append: true
		});

		expect(result.startedNewSection).toBe(false);
		expect(result.body).toBe("hello");
		expect(result.entries).toEqual([
			expect.objectContaining({
				role: "agent",
				body: "hello",
				isFinalized: false
			})
		]);
	});

	it("starts a new same-artifact section when append is false", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "first thought",
			append: false
		}).entries;

		const result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "second thought",
			append: false
		});

		expect(result.startedNewSection).toBe(true);
		expect(result.entries.map((entry) => entry.role)).toEqual([
			"agent",
			"divider",
			"agent"
		]);
		expect(result.entries[0]?.body).toBe("first thought");
		expect(result.entries[0]?.isFinalized).toBe(true);
		expect(result.entries[2]?.body).toBe("second thought");
		expect(result.entries[2]?.isFinalized).toBe(false);
	});

	it("replaces the active section when explicitly requested", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "partial",
			append: false
		}).entries;

		const result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "final",
			append: false,
			replaceCurrentSection: true
		});

		expect(result.startedNewSection).toBe(false);
		expect(result.entries).toEqual([
			expect.objectContaining({
				role: "agent",
				body: "final",
				isFinalized: false
			})
		]);
	});

	it("starts a divided section when the stream artifact kind changes", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "thinking",
			append: false
		}).entries;

		const result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "answer",
			append: true
		});

		expect(result.entries.map((entry) => entry.role)).toEqual([
			"agent",
			"divider",
			"agent"
		]);
		expect(result.section.kind).toBe("response");
		expect(result.entries[2]?.body).toBe("answer");
	});

	it("preserves prior thoughts when a later same-artifact thought starts", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "partial thought",
			append: false
		}).entries;
		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "answer",
			append: true
		}).entries;

		const result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "next thought",
			append: false
		});

		expect(result.startedNewSection).toBe(true);
		expect(result.entries.map((entry) => entry.role)).toEqual([
			"agent",
			"divider",
			"agent",
			"divider",
			"agent"
		]);
		expect(result.entries[0]?.body).toBe("partial thought");
		expect(result.entries[2]?.body).toBe("answer");
		expect(result.entries[4]?.body).toBe("next thought");
	});

	it("replaces a finalized thinking section after an interleaved response", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "partial thought",
			append: false
		}).entries;
		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "answer",
			append: true
		}).entries;

		const result = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "final thought",
			append: false,
			replaceCurrentSection: true
		});

		expect(result.startedNewSection).toBe(false);
		expect(result.entries.map((entry) => entry.role)).toEqual([
			"agent",
			"divider",
			"agent"
		]);
		expect(result.entries[0]?.body).toBe("final thought");
		expect(result.entries[2]?.body).toBe("answer");
	});

	it("replaces the response section after an interleaved thinking segment", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "partial thought",
			append: false
		}).entries;
		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "partial answer",
			append: true
		}).entries;
		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "next thought",
			append: false
		}).entries;

		const result = replaceActiveStreamTranscriptSection({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "final answer"
		});

		expect(result.replaced).toBe(true);
		expect(result.entries.map((entry) => entry.role)).toEqual([
			"agent",
			"divider",
			"agent",
			"divider",
			"agent"
		]);
		expect(result.entries[0]?.body).toBe("partial thought");
		expect(result.entries[2]?.body).toBe("final answer");
		expect(result.entries[4]?.body).toBe("next thought");
	});

	it("prepares the active stream section before entries are rendered", () => {
		const state = createStreamTranscriptState();

		const prepared = prepareStreamTranscriptDelta({
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "response",
			append: false
		});

		expect(prepared.section.kind).toBe("response");
		expect(
			getActiveStreamTranscriptSection(
				state,
				"task-1",
				"aion:stream-delta"
			)
		).toBe(prepared.section);
	});

	it("replaces the active response section with a final message", () => {
		const state = createStreamTranscriptState();
		let entries: TranscriptEntry[] = [];

		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "partial",
			append: false
		}).entries;

		const result = replaceActiveStreamTranscriptSection({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "final"
		});

		expect(result.replaced).toBe(true);
		expect(result.entries).toEqual([
			expect.objectContaining({
				role: "agent",
				body: "final",
				isFinalized: true
			})
		]);
		expect(
			getActiveStreamTranscriptSection(
				state,
				"task-1",
				"aion:stream-delta"
			)
		).toBeUndefined();
	});

	it("does not replace a thinking section with a final response", () => {
		const state = createStreamTranscriptState();
		const entries = applyStreamTranscriptDelta({
			entries: [],
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "thinking",
			append: false
		}).entries;

		const result = replaceActiveStreamTranscriptSection({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "final"
		});

		expect(result.replaced).toBe(false);
		expect(result.entries).toEqual(entries);
	});

	it("finalizes every open section for a completed task", () => {
		const state = createStreamTranscriptState();
		let entries = applyStreamTranscriptDelta({
			entries: [],
			state,
			taskId: "task-1",
			artifactId: "aion:thinking-delta",
			kind: "thinking",
			body: "thinking",
			append: false
		}).entries;
		entries = applyStreamTranscriptDelta({
			entries,
			state,
			taskId: "task-1",
			artifactId: "aion:stream-delta",
			kind: "response",
			body: "answer",
			append: false
		}).entries;

		const finalized = finalizeStreamTranscriptSections({
			entries,
			state,
			taskId: "task-1"
		});

		expect(finalized.filter((entry) => entry.role === "agent"))
			.toHaveLength(2);
		expect(finalized.every((entry) => entry.isFinalized)).toBe(true);
		expect(
			getActiveStreamTranscriptSection(
				state,
				"task-1",
				"aion:stream-delta"
			)
		).toBeUndefined();
	});

	it("keeps entries after the first mutable section in the dynamic partition", () => {
		const entries: TranscriptEntry[] = [
			{ id: "user", role: "user", body: "question", isFinalized: true },
			{ id: "stream", role: "agent", body: "partial", isFinalized: false },
			{ id: "status", role: "status", body: "waiting", isFinalized: true }
		];

		expect(partitionTranscriptEntries(entries)).toEqual({
			scrollbackEntries: [entries[0]],
			dynamicEntries: [entries[1], entries[2]]
		});
	});
});
