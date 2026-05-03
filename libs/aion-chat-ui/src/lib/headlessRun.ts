import type {
	Artifact,
	AgentCard,
	Message,
	MessageSendParams,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";

import type { HeadlessRunOptions } from "../args.js";
import { STREAM_DELTA_ARTIFACT_ID } from "./a2aMetadata.js";
import {
	getUnshownTaskAgentMessages,
	markShownMessage,
	shouldRenderLiveResponseMessage,
	shouldRenderLiveStatusMessage,
	shouldShowNoAgentMessageNotice
} from "./chatSession.js";
import { loadChatSettings } from "./chatSettings.js";
import {
	buildAuthenticatedFetch,
	buildMessageParams,
	connectClient,
	createPushNotificationConfig,
	type ConnectedClient,
	type StreamEvent
} from "./connection.js";
import {
	discoverAgentSources,
	selectDiscoveredAgent,
	type AgentDiscoveryResult
} from "./agents/discovery.js";
import {
	createExplicitAgentSource,
	isTransientAgentSource,
	mergeAgentSources,
	type DiscoveredAgentRecord
} from "./agents/model.js";
import { buildMessageParts } from "./input/index.js";
import { formatMessageParts } from "./messageDisplay.js";
import type { ResponseMode } from "./slashCommands.js";
import { isTerminalTaskState } from "./taskState.js";
import { getStoredAccessToken } from "./workosAuth.js";

const NO_AGENT_MESSAGE_NOTICE = "Task completed with no agent message.";

interface WritableStreamLike {
	write(chunk: string): unknown;
}

export interface HeadlessRunDependencies {
	stdout?: WritableStreamLike;
	stderr?: WritableStreamLike;
	fetchImpl?: typeof fetch;
	loadChatSettingsImpl?: typeof loadChatSettings;
	discoverAgentSourcesImpl?: typeof discoverAgentSources;
	connectClientImpl?: typeof connectClient;
	buildMessagePartsImpl?: typeof buildMessageParts;
	getStoredAccessTokenImpl?: typeof getStoredAccessToken;
}

interface OutputEntry {
	key: string;
	body: string;
}

interface MessageOutputState {
	entries: OutputEntry[];
	shownMessageKeys: Set<string>;
	shownArtifactKeys: Set<string>;
	streamedTaskIds: Set<string>;
	reachedTerminal: boolean;
	renderedAgentOutput: boolean;
}

function writeLine(stream: WritableStreamLike, line: string): void {
	stream.write(`${line}\n`);
}

function writeDiscoveryErrors(
	discovery: AgentDiscoveryResult,
	stderr: WritableStreamLike
): void {
	for (const error of discovery.errors) {
		if (error.source.isDefault) {
			continue;
		}
		writeLine(
			stderr,
			`Failed to discover agents from ${error.source.description}: ${error.error ?? error.source.lastError ?? "unknown error"}`
		);
	}
}

function normalizeSelector(value: string): string {
	return value.trim().toLowerCase();
}

function withoutAtPrefix(value: string): string {
	return value.startsWith("@") ? value.slice(1) : value;
}

function getAgentSelectorValues(agent: DiscoveredAgentRecord): string[] {
	return [
		agent.agentHandle,
		agent.agentHandle ? withoutAtPrefix(agent.agentHandle) : undefined,
		agent.id,
		agent.agentId,
		agent.agentKey,
		agent.agentCardName
	]
		.filter((value): value is string => Boolean(value?.trim()))
		.map(normalizeSelector);
}

function formatAgentCandidate(agent: DiscoveredAgentRecord): string {
	const label = agent.agentHandle ?? agent.id;
	return `${label} (${agent.source.description})`;
}

function formatAgentCandidates(agents: DiscoveredAgentRecord[]): string {
	if (agents.length === 0) {
		return "none";
	}
	return agents.slice(0, 10).map(formatAgentCandidate).join(", ");
}

function selectAgentBySelector(
	agents: DiscoveredAgentRecord[],
	selector: string,
	explicitSourceKey: string | undefined
): DiscoveredAgentRecord {
	const normalizedSelector = normalizeSelector(selector);
	const normalizedWithoutAt = withoutAtPrefix(normalizedSelector);
	let matches = agents.filter((agent) => {
		const values = getAgentSelectorValues(agent);
		return (
			values.includes(normalizedSelector) ||
			values.includes(normalizedWithoutAt)
		);
	});

	if (explicitSourceKey) {
		const explicitMatches = matches.filter(
			(agent) => agent.sourceKey === explicitSourceKey
		);
		if (explicitMatches.length > 0) {
			matches = explicitMatches;
		}
	}

	if (matches.length === 0) {
		throw new Error(
			`No discovered agent matched '${selector}'. Available agents: ${formatAgentCandidates(agents)}`
		);
	}
	if (matches.length > 1) {
		throw new Error(
			`Agent selector '${selector}' matched multiple agents: ${formatAgentCandidates(matches)}`
		);
	}
	return matches[0];
}

function selectHeadlessAgent(
	agents: DiscoveredAgentRecord[],
	options: HeadlessRunOptions,
	selectedAgentKey: string | undefined,
	selectedAgentId: string | undefined,
	explicitSourceKey: string | undefined
): DiscoveredAgentRecord {
	if (options.agentSelector) {
		return selectAgentBySelector(agents, options.agentSelector, explicitSourceKey);
	}

	const shouldIgnoreSavedSelection = Boolean(explicitSourceKey && !options.agentId);
	const selected = selectDiscoveredAgent(agents, {
		requestedAgentId: options.agentId,
		selectedAgentKey: shouldIgnoreSavedSelection ? undefined : selectedAgentKey,
		selectedAgentId: shouldIgnoreSavedSelection ? undefined : selectedAgentId,
		explicitSourceKey,
		autoSelectExplicit: true
	});
	if (!selected) {
		throw new Error(
			`No agent selected. Use --agent with one of: ${formatAgentCandidates(agents)}`
		);
	}
	return selected;
}

function appendOutput(
	state: MessageOutputState,
	key: string,
	body: string,
	append = false
): boolean {
	if (!body) {
		return false;
	}

	const existing = state.entries.find((entry) => entry.key === key);
	if (existing) {
		existing.body = append ? `${existing.body}${body}` : body;
	} else {
		state.entries.push({ key, body });
	}
	state.renderedAgentOutput = true;
	return true;
}

function renderAgentMessage(
	state: MessageOutputState,
	message: Message,
	fallbackTaskId?: string
): boolean {
	if (!shouldRenderLiveResponseMessage(message)) {
		return false;
	}

	const body = formatMessageParts(message.parts);
	if (!body) {
		return false;
	}

	const shownMessageKey = markShownMessage(
		state.shownMessageKeys,
		message,
		fallbackTaskId
	);
	return appendOutput(state, `message:${shownMessageKey}`, body);
}

function renderArtifact(
	state: MessageOutputState,
	artifact: Artifact,
	taskId: string,
	append = false
): boolean {
	const artifactKey = `${taskId}:${artifact.artifactId}`;
	const body = formatMessageParts(artifact.parts);
	if (!body) {
		return false;
	}

	state.shownArtifactKeys.add(artifactKey);
	return appendOutput(state, `artifact:${artifactKey}`, body, append);
}

function renderTaskArtifacts(state: MessageOutputState, task: Task): boolean {
	let rendered = false;
	for (const artifact of task.artifacts ?? []) {
		const artifactKey = `${task.id}:${artifact.artifactId}`;
		if (state.shownArtifactKeys.has(artifactKey)) {
			continue;
		}
		rendered = renderArtifact(state, artifact, task.id) || rendered;
	}
	return rendered;
}

function handleMessageOutputMessage(
	state: MessageOutputState,
	message: Message,
	reachedTerminal = false
): boolean {
	if (reachedTerminal) {
		state.reachedTerminal = true;
	}
	return renderAgentMessage(state, message);
}

function handleMessageOutputTask(
	state: MessageOutputState,
	task: Task
): boolean {
	const isTerminalTask = isTerminalTaskState(task.status.state);
	if (isTerminalTask) {
		state.reachedTerminal = true;
	}
	if (!isTerminalTask || state.streamedTaskIds.has(task.id)) {
		return false;
	}

	let rendered = false;
	for (const message of getUnshownTaskAgentMessages(task, state.shownMessageKeys)) {
		rendered = renderAgentMessage(state, message, task.id) || rendered;
	}
	rendered = renderTaskArtifacts(state, task) || rendered;
	return rendered;
}

function handleMessageOutputStatusUpdate(
	state: MessageOutputState,
	event: TaskStatusUpdateEvent
): boolean {
	if (isTerminalTaskState(event.status.state)) {
		state.reachedTerminal = true;
	}

	if (
		shouldRenderLiveStatusMessage({
			message: event.status.message as Message | undefined,
			taskId: event.taskId,
			streamedTaskIds: state.streamedTaskIds
		})
	) {
		return renderAgentMessage(
			state,
			event.status.message as Message,
			event.taskId
		);
	}
	return false;
}

function handleMessageOutputArtifactUpdate(
	state: MessageOutputState,
	event: TaskArtifactUpdateEvent
): boolean {
	const rendered = renderArtifact(state, event.artifact, event.taskId, event.append);
	if (rendered && event.artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
		state.streamedTaskIds.add(event.taskId);
	}
	return rendered;
}

function createMessageOutputState(): MessageOutputState {
	return {
		entries: [],
		shownMessageKeys: new Set<string>(),
		shownArtifactKeys: new Set<string>(),
		streamedTaskIds: new Set<string>(),
		reachedTerminal: false,
		renderedAgentOutput: false
	};
}

function writeMessageOutput(
	state: MessageOutputState,
	responseMode: ResponseMode,
	stdout: WritableStreamLike,
	stderr: WritableStreamLike
): void {
	const output = state.entries
		.map((entry) => entry.body)
		.filter(Boolean)
		.join("\n\n");
	if (output) {
		writeLine(stdout, output);
	}

	if (
		shouldShowNoAgentMessageNotice({
			responseMode,
			reachedTerminal: state.reachedTerminal,
			renderedAgentOutput: state.renderedAgentOutput
		})
	) {
		writeLine(stderr, NO_AGENT_MESSAGE_NOTICE);
	}
}

function writeRawPayload(stream: WritableStreamLike, payload: unknown): void {
	writeLine(stream, JSON.stringify(payload));
}

async function sendSingleMessage({
	clientState,
	params,
	responseMode,
	stdout,
	stderr
}: {
	clientState: ConnectedClient;
	params: MessageSendParams;
	responseMode: ResponseMode;
	stdout: WritableStreamLike;
	stderr: WritableStreamLike;
}): Promise<void> {
	const response = await clientState.client.sendMessage(params);
	if (responseMode === "a2a-protocol") {
		writeRawPayload(stdout, response);
		return;
	}

	const state = createMessageOutputState();
	if (response.kind === "message") {
		handleMessageOutputMessage(state, response, true);
	} else {
		handleMessageOutputTask(state, response);
	}
	writeMessageOutput(state, responseMode, stdout, stderr);
}

async function sendStreamingMessage({
	clientState,
	params,
	responseMode,
	stdout,
	stderr
}: {
	clientState: ConnectedClient;
	params: MessageSendParams;
	responseMode: ResponseMode;
	stdout: WritableStreamLike;
	stderr: WritableStreamLike;
}): Promise<void> {
	const state = createMessageOutputState();

	for await (const event of clientState.client.sendMessageStream(params)) {
		if (responseMode === "a2a-protocol") {
			writeRawPayload(stdout, event);
			continue;
		}

		switch ((event as StreamEvent).kind) {
			case "message":
				handleMessageOutputMessage(state, event as Message);
				break;
			case "task":
				handleMessageOutputTask(state, event as Task);
				break;
			case "status-update":
				handleMessageOutputStatusUpdate(state, event as TaskStatusUpdateEvent);
				break;
			case "artifact-update":
				handleMessageOutputArtifactUpdate(state, event as TaskArtifactUpdateEvent);
				break;
			default:
				break;
		}
	}

	if (responseMode !== "a2a-protocol") {
		writeMessageOutput(state, responseMode, stdout, stderr);
	}
}

function canStream(agentCard: AgentCard): boolean {
	return Boolean(agentCard.capabilities.streaming);
}

function getConnectionOptions(
	options: HeadlessRunOptions,
	selectedAgent: DiscoveredAgentRecord
): HeadlessRunOptions & { url: string } {
	const useCliEndpointAuth = isTransientAgentSource(selectedAgent.source);
	return {
		...options,
		url: selectedAgent.connectionUrl,
		agentId: selectedAgent.connectionAgentId,
		token: useCliEndpointAuth ? options.token : undefined,
		headers: useCliEndpointAuth ? options.headers : {},
		pushNotifications: options.pushNotifications,
		pushReceiver: options.pushReceiver
	};
}

function getContextId(
	selectedAgent: DiscoveredAgentRecord,
	environmentAgents: Record<string, { activeContextId?: string }>
): string | undefined {
	return (
		environmentAgents[selectedAgent.agentKey]?.activeContextId ??
		selectedAgent.activeContextId
	);
}

export async function runHeadless(
	options: HeadlessRunOptions,
	dependencies: HeadlessRunDependencies = {}
): Promise<number> {
	const stdout = dependencies.stdout ?? process.stdout;
	const stderr = dependencies.stderr ?? process.stderr;
	const fetchImpl = dependencies.fetchImpl ?? fetch;
	const loadChatSettingsImpl =
		dependencies.loadChatSettingsImpl ?? loadChatSettings;
	const discoverAgentSourcesImpl =
		dependencies.discoverAgentSourcesImpl ?? discoverAgentSources;
	const connectClientImpl = dependencies.connectClientImpl ?? connectClient;
	const buildMessagePartsImpl =
		dependencies.buildMessagePartsImpl ?? buildMessageParts;
	const getStoredAccessTokenImpl =
		dependencies.getStoredAccessTokenImpl ?? getStoredAccessToken;

	const { settings, warning } = loadChatSettingsImpl();
	if (warning) {
		writeLine(stderr, warning);
	}
	const selectedEnvironment = settings.selectedEnvironment;
	const environmentSettings = settings.environments[selectedEnvironment];
	const explicitSourceKey = options.url
		? createExplicitAgentSource(options.url).sourceKey
		: undefined;
	const runtimeSources = mergeAgentSources(
		environmentSettings.agentSources,
		selectedEnvironment,
		options.url
	);
	const explicitSourceFetch = buildAuthenticatedFetch({
		token: options.token,
		headers: options.headers
	});
	const discovery = await discoverAgentSourcesImpl(runtimeSources, fetchImpl, {
		environmentId: selectedEnvironment,
		controlPlaneAccessTokenProvider: () =>
			getStoredAccessTokenImpl(selectedEnvironment),
		graphQLFetchImpl: fetchImpl,
		sourceFetchImpl: (source) =>
			source.sourceKey === explicitSourceKey ? explicitSourceFetch : fetchImpl
	});
	writeDiscoveryErrors(discovery, stderr);

	if (discovery.agents.length === 0) {
		throw new Error("No agents discovered for the selected environment.");
	}

	const selectedAgent = selectHeadlessAgent(
		discovery.agents,
		options,
		environmentSettings.selectedAgentKey,
		environmentSettings.selectedAgentId,
		explicitSourceKey
	);
	const clientState = await connectClientImpl(
		getConnectionOptions(options, selectedAgent)
	);
	const parts = await buildMessagePartsImpl(options.message ?? "");
	const pushConfig = options.pushNotifications
		? createPushNotificationConfig(options.pushReceiver)
		: undefined;
	const params = buildMessageParams(
		parts,
		getContextId(selectedAgent, environmentSettings.agents),
		undefined,
		pushConfig
	);

	if (options.requestMode === "streaming-message" && canStream(clientState.agentCard)) {
		await sendStreamingMessage({
			clientState,
			params,
			responseMode: options.responseMode,
			stdout,
			stderr
		});
		return 0;
	}

	if (options.requestMode === "streaming-message") {
		writeLine(
			stderr,
			"Request mode fallback: agent does not support streaming, using Send message."
		);
	}

	await sendSingleMessage({
		clientState,
		params,
		responseMode: options.responseMode,
		stdout,
		stderr
	});
	return 0;
}
