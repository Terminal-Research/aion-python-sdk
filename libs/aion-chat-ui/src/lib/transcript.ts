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
	isFinalized: boolean;
}

export interface TranscriptPartition {
	scrollbackEntries: TranscriptEntry[];
	dynamicEntries: TranscriptEntry[];
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
	finalizedEntryIds: string[];
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
	body: string,
	isFinalized = true
): TranscriptEntry[] {
	const existingIndex = entries.findIndex((item) => item.id === entryId);
	if (existingIndex === -1) {
		return [...entries, { id: entryId, role, body, isFinalized }];
	}

	const next = [...entries];
	next[existingIndex] = {
		...next[existingIndex],
		role,
		body,
		isFinalized
	};
	return next;
}

export function replaceTranscriptEntryBody(
	entries: TranscriptEntry[],
	entryId: string,
	role: TranscriptEntry["role"],
	body: string,
	isFinalized = true
): ReplaceTranscriptEntryResult {
	const existingIndex = entries.findIndex((item) => item.id === entryId);
	if (existingIndex === -1) {
		return { entries, replaced: false };
	}

	const next = [...entries];
	next[existingIndex] = {
		...next[existingIndex],
		role,
		body,
		isFinalized
	};
	return { entries: next, replaced: true };
}

export function finalizeTranscriptEntries(
	entries: TranscriptEntry[],
	entryIds?: ReadonlySet<string>
): TranscriptEntry[] {
	let changed = false;
	const next = entries.map((entry) => {
		if (
			entry.isFinalized ||
			(entryIds !== undefined && !entryIds.has(entry.id))
		) {
			return entry;
		}

		changed = true;
		return { ...entry, isFinalized: true };
	});
	return changed ? next : entries;
}

export function partitionTranscriptEntries(
	entries: TranscriptEntry[]
): TranscriptPartition {
	const firstDynamicIndex = entries.findIndex((entry) => !entry.isFinalized);
	if (firstDynamicIndex === -1) {
		return {
			scrollbackEntries: entries,
			dynamicEntries: []
		};
	}

	return {
		scrollbackEntries: entries.slice(0, firstDynamicIndex),
		dynamicEntries: entries.slice(firstDynamicIndex)
	};
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
}): {
	section: StreamTranscriptSection;
	insertDivider: boolean;
	supersededEntryId?: string;
} {
	const sectionKey = streamTranscriptSectionKey(taskId, artifactId);
	const supersededSection = state.activeSectionsByTaskAndArtifactId.get(sectionKey);
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
	if (supersededSection) {
		state.bodyByEntryId.delete(supersededSection.entryId);
	}
	state.activeSectionsByTaskAndArtifactId.set(
		sectionKey,
		section
	);
	state.lastSectionByTaskId.set(taskId, section);

	return {
		section,
		insertDivider: previousLastSection !== undefined || sectionIndex > 1,
		...(supersededSection
			? { supersededEntryId: supersededSection.entryId }
			: {})
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
			insertDivider: false,
			finalizedEntryIds: []
		};
	}

	if (replaceCurrentSection && activeSection !== undefined) {
		state.bodyByEntryId.set(activeSection.entryId, body);
		return {
			section: activeSection,
			body,
			appendToExistingSection: false,
			replaceExistingSection: true,
			insertDivider: false,
			finalizedEntryIds: []
		};
	}

	const { section, insertDivider, supersededEntryId } = createStreamTranscriptSection({
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
		insertDivider,
		finalizedEntryIds: supersededEntryId ? [supersededEntryId] : []
	};
}

export function applyPreparedStreamTranscriptDelta({
	entries,
	prepared
}: {
	entries: TranscriptEntry[];
	prepared: PreparedStreamTranscriptDelta;
}): ApplyStreamTranscriptDeltaResult {
	const finalizedEntryIds = new Set(prepared.finalizedEntryIds);
	let nextEntries = finalizeTranscriptEntries(entries, finalizedEntryIds);

	if (prepared.appendToExistingSection || prepared.replaceExistingSection) {
		return {
			entries: upsertTranscriptEntry(
				nextEntries,
				prepared.section.entryId,
				"agent",
				prepared.body,
				false
			),
			section: prepared.section,
			body: prepared.body,
			startedNewSection: false
		};
	}

	if (prepared.insertDivider) {
		nextEntries = [
			...nextEntries,
			{
				id: `artifact-divider:${prepared.section.taskId}:${prepared.section.sectionIndex}`,
				role: "divider",
				body: "",
				isFinalized: true
			}
		];
	}

	return {
		entries: upsertTranscriptEntry(
			nextEntries,
			prepared.section.entryId,
			"agent",
			prepared.body,
			false
		),
		section: prepared.section,
		body: prepared.body,
		startedNewSection: true
	};
}

export function closeStreamTranscriptSections({
	state,
	taskId
}: {
	state: StreamTranscriptState;
	taskId?: string;
}): Set<string> {
	const sections = [...state.activeSectionsByTaskAndArtifactId.values()].filter(
		(section) => taskId === undefined || section.taskId === taskId
	);
	const entryIds = new Set(sections.map((section) => section.entryId));
	for (const section of sections) {
		clearActiveStreamTranscriptSection(
			state,
			section.taskId,
			section.artifactId
		);
	}
	return entryIds;
}

export function finalizeStreamTranscriptSections({
	entries,
	state,
	taskId
}: {
	entries: TranscriptEntry[];
	state: StreamTranscriptState;
	taskId?: string;
}): TranscriptEntry[] {
	const entryIds = closeStreamTranscriptSections({ state, taskId });
	return entryIds.size > 0
		? finalizeTranscriptEntries(entries, entryIds)
		: entries;
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
