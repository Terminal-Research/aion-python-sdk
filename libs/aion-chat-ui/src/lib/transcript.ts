export type TranscriptRole =
	| "agent"
	| "user"
	| "status"
	| "system"
	| "protocol"
	| "divider";

export interface TranscriptEntry {
	id: string;
	body: string;
	role: TranscriptRole;
}

export type StreamTranscriptArtifactKind = "response" | "thinking";

export interface StreamTranscriptSection {
	taskId: string;
	artifactId: string;
	kind: StreamTranscriptArtifactKind;
	entryId: string;
	sectionIndex: number;
}

export interface StreamTranscriptState {
	activeSectionsByTaskAndArtifactId: Map<string, StreamTranscriptSection>;
	lastSectionByTaskId: Map<string, StreamTranscriptSection>;
	nextSectionIndexByTaskId: Map<string, number>;
	bodyByEntryId: Map<string, string>;
}

export interface PreparedStreamTranscriptDelta {
	section: StreamTranscriptSection;
	body: string;
	appendToExistingSection: boolean;
	replaceExistingSection: boolean;
	insertDivider: boolean;
}

export interface ApplyStreamTranscriptDeltaResult {
	entries: TranscriptEntry[];
	section: StreamTranscriptSection;
	body: string;
	startedNewSection: boolean;
}

export interface ReplaceTranscriptEntryResult {
	entries: TranscriptEntry[];
	replaced: boolean;
}

export interface ReplaceActiveStreamTranscriptSectionResult {
	entries: TranscriptEntry[];
	replaced: boolean;
	section?: StreamTranscriptSection;
}

export function createStreamTranscriptState(): StreamTranscriptState {
	return {
		activeSectionsByTaskAndArtifactId: new Map<string, StreamTranscriptSection>(),
		lastSectionByTaskId: new Map<string, StreamTranscriptSection>(),
		nextSectionIndexByTaskId: new Map<string, number>(),
		bodyByEntryId: new Map<string, string>()
	};
}

export function clearStreamTranscriptState(state: StreamTranscriptState): void {
	state.activeSectionsByTaskAndArtifactId.clear();
	state.lastSectionByTaskId.clear();
	state.nextSectionIndexByTaskId.clear();
	state.bodyByEntryId.clear();
}

function streamTranscriptSectionKey(taskId: string, artifactId: string): string {
	return `${taskId}::${artifactId}`;
}

export function getActiveStreamTranscriptSection(
	state: StreamTranscriptState,
	taskId: string,
	artifactId: string
): StreamTranscriptSection | undefined {
	return state.activeSectionsByTaskAndArtifactId.get(
		streamTranscriptSectionKey(taskId, artifactId)
	);
}

export function getLastStreamTranscriptSection(
	state: StreamTranscriptState,
	taskId: string
): StreamTranscriptSection | undefined {
	return state.lastSectionByTaskId.get(taskId);
}

export function clearActiveStreamTranscriptSection(
	state: StreamTranscriptState,
	taskId: string,
	artifactId: string
): void {
	const activeSection = getActiveStreamTranscriptSection(
		state,
		taskId,
		artifactId
	);
	state.activeSectionsByTaskAndArtifactId.delete(
		streamTranscriptSectionKey(taskId, artifactId)
	);
	if (activeSection) {
		state.bodyByEntryId.delete(activeSection.entryId);
	}
	if (
		activeSection &&
		state.lastSectionByTaskId.get(taskId)?.entryId === activeSection.entryId
	) {
		state.lastSectionByTaskId.delete(taskId);
	}
}

export function upsertTranscriptEntry(
	entries: TranscriptEntry[],
	entryId: string,
	role: TranscriptEntry["role"],
	body: string
): TranscriptEntry[] {
	const existingIndex = entries.findIndex((item) => item.id === entryId);
	if (existingIndex === -1) {
		return [...entries, { id: entryId, role, body }];
	}

	const next = [...entries];
	next[existingIndex] = {
		...next[existingIndex],
		role,
		body
	};
	return next;
}

export function replaceTranscriptEntryBody(
	entries: TranscriptEntry[],
	entryId: string,
	role: TranscriptEntry["role"],
	body: string
): ReplaceTranscriptEntryResult {
	const existingIndex = entries.findIndex((item) => item.id === entryId);
	if (existingIndex === -1) {
		return { entries, replaced: false };
	}

	const next = [...entries];
	next[existingIndex] = {
		...next[existingIndex],
		role,
		body
	};
	return { entries: next, replaced: true };
}

function createStreamTranscriptSection({
	state,
	taskId,
	artifactId,
	kind
}: {
	state: StreamTranscriptState;
	taskId: string;
	artifactId: string;
	kind: StreamTranscriptArtifactKind;
}): { section: StreamTranscriptSection; insertDivider: boolean } {
	const sectionIndex = (state.nextSectionIndexByTaskId.get(taskId) ?? 0) + 1;
	state.nextSectionIndexByTaskId.set(taskId, sectionIndex);

	const section: StreamTranscriptSection = {
		taskId,
		artifactId,
		kind,
		entryId: `artifact:${taskId}:${artifactId}:${sectionIndex}`,
		sectionIndex
	};
	const previousLastSection = state.lastSectionByTaskId.get(taskId);
	state.activeSectionsByTaskAndArtifactId.set(
		streamTranscriptSectionKey(taskId, artifactId),
		section
	);
	state.lastSectionByTaskId.set(taskId, section);

	return {
		section,
		insertDivider: previousLastSection !== undefined || sectionIndex > 1
	};
}

export function prepareStreamTranscriptDelta({
	state,
	taskId,
	artifactId,
	kind,
	body,
	append,
	replaceCurrentSection = false
}: {
	state: StreamTranscriptState;
	taskId: string;
	artifactId: string;
	kind: StreamTranscriptArtifactKind;
	body: string;
	append: boolean;
	replaceCurrentSection?: boolean;
}): PreparedStreamTranscriptDelta {
	const sectionKey = streamTranscriptSectionKey(taskId, artifactId);
	const activeSection = state.activeSectionsByTaskAndArtifactId.get(sectionKey);
	if (append && activeSection !== undefined) {
		const nextBody = `${state.bodyByEntryId.get(activeSection.entryId) ?? ""}${body}`;
		state.bodyByEntryId.set(activeSection.entryId, nextBody);
		return {
			section: activeSection,
			body: nextBody,
			appendToExistingSection: true,
			replaceExistingSection: false,
			insertDivider: false
		};
	}

	if (replaceCurrentSection && activeSection !== undefined) {
		state.bodyByEntryId.set(activeSection.entryId, body);
		return {
			section: activeSection,
			body,
			appendToExistingSection: false,
			replaceExistingSection: true,
			insertDivider: false
		};
	}

	const { section, insertDivider } = createStreamTranscriptSection({
		state,
		taskId,
		artifactId,
		kind
	});
	state.bodyByEntryId.set(section.entryId, body);

	return {
		section,
		body,
		appendToExistingSection: false,
		replaceExistingSection: false,
		insertDivider
	};
}

export function applyPreparedStreamTranscriptDelta({
	entries,
	prepared
}: {
	entries: TranscriptEntry[];
	prepared: PreparedStreamTranscriptDelta;
}): ApplyStreamTranscriptDeltaResult {
	if (prepared.appendToExistingSection || prepared.replaceExistingSection) {
		return {
			entries: upsertTranscriptEntry(
				entries,
				prepared.section.entryId,
				"agent",
				prepared.body
			),
			section: prepared.section,
			body: prepared.body,
			startedNewSection: false
		};
	}

	let nextEntries = entries;
	if (prepared.insertDivider) {
		nextEntries = [
			...nextEntries,
			{
				id: `artifact-divider:${prepared.section.taskId}:${prepared.section.sectionIndex}`,
				role: "divider",
				body: ""
			}
		];
	}

	return {
		entries: upsertTranscriptEntry(
			nextEntries,
			prepared.section.entryId,
			"agent",
			prepared.body
		),
		section: prepared.section,
		body: prepared.body,
		startedNewSection: true
	};
}

export function applyStreamTranscriptDelta({
	entries,
	state,
	taskId,
	artifactId,
	kind,
	body,
	append,
	replaceCurrentSection
}: {
	entries: TranscriptEntry[];
	state: StreamTranscriptState;
	taskId: string;
	artifactId: string;
	kind: StreamTranscriptArtifactKind;
	body: string;
	append: boolean;
	replaceCurrentSection?: boolean;
}): ApplyStreamTranscriptDeltaResult {
	return applyPreparedStreamTranscriptDelta({
		entries,
		prepared: prepareStreamTranscriptDelta({
			state,
			taskId,
			artifactId,
			kind,
			body,
			append,
			replaceCurrentSection
		})
	});
}

export function replaceActiveStreamTranscriptSection({
	entries,
	state,
	taskId,
	artifactId,
	kind,
	body
}: {
	entries: TranscriptEntry[];
	state: StreamTranscriptState;
	taskId: string;
	artifactId: string;
	kind: StreamTranscriptArtifactKind;
	body: string;
}): ReplaceActiveStreamTranscriptSectionResult {
	const activeSection = getActiveStreamTranscriptSection(
		state,
		taskId,
		artifactId
	);
	if (activeSection?.kind !== kind) {
		return { entries, replaced: false };
	}

	state.bodyByEntryId.set(activeSection.entryId, body);
	const result = replaceTranscriptEntryBody(
		entries,
		activeSection.entryId,
		"agent",
		body
	);
	if (!result.replaced) {
		return { entries, replaced: false };
	}

	clearActiveStreamTranscriptSection(state, taskId, artifactId);
	return {
		entries: result.entries,
		replaced: true,
		section: activeSection
	};
}
