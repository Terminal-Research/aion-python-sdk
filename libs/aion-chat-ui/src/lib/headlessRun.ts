import type {
	Artifact,
	AgentCard,
	Message,
	SendMessageRequest,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";

import type { HeadlessRunOptions } from "../args.js";
import { STREAM_DELTA_ARTIFACT_ID } from "./a2aMetadata.js";
import {
	isMessage,
	isTask,
	isTaskArtifactUpdateEvent,
	isTaskStatusUpdateEvent,
	unwrapStreamResponse,
	type StreamEvent
} from "./a2aProtocol.js";
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
	type TokenProvider
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
	normalizeSourceUrl,
	type DiscoveredAgentRecord
} from "./agents/model.js";
import { buildMessageParts } from "./input/index.js";
import {
	type AionEnvironmentId,
	getControlPlaneApiBaseUrl
} from "./environment.js";
import { formatMessageParts } from "./messageDisplay.js";
import {
	createChatSessionLogger,
	type CreateChatSessionLoggerResult
} from "./sessionLogger.js";
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
	createChatSessionLoggerImpl?: typeof createChatSessionLogger;
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
		if (!shouldWriteDiscoveryError(error, discovery)) {
			continue;
		}
		writeLine(
			stderr,
			`Failed to discover agents from ${error.source.description}: ${error.error ?? error.source.lastError ?? "unknown error"}`
		);
	}
}

function shouldWriteDiscoveryError(
	error: AgentDiscoveryResult["errors"][number],
	discovery: AgentDiscoveryResult
): boolean {
	if (error.source.type === "registry") {
		return true;
	}
	if (!error.source.isDefault) {
		return true;
	}
	return discovery.agents.length === 0;
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

function pluralizeAgents(count: number): string {
	return count === 1 ? "1 agent" : `${count} agents`;
}

function formatSourceStatuses(discovery: AgentDiscoveryResult): string {
	if (discovery.sources.length === 0) {
		return "none";
	}

	const errorsBySourceKey = new Map(
		discovery.errors.map((error) => [error.source.sourceKey, error])
	);
	const agentCountsBySourceKey = new Map<string, number>();
	for (const agent of discovery.agents) {
		agentCountsBySourceKey.set(
			agent.sourceKey,
			(agentCountsBySourceKey.get(agent.sourceKey) ?? 0) + 1
		);
	}

	return discovery.sources
		.map((source) => {
			const error = errorsBySourceKey.get(source.sourceKey);
			if (error) {
				return `${source.description}: unavailable (${error.error ?? source.lastError ?? "unknown error"})`;
			}
			const agentCount = agentCountsBySourceKey.get(source.sourceKey) ?? 0;
			const status =
				source.status === "unchecked" && agentCount > 0
					? "available"
					: source.status ?? "unknown";
			return `${source.description}: ${status} (${pluralizeAgents(agentCount)})`;
		})
		.join("; ");
}

function selectAgentBySelector(
	agents: DiscoveredAgentRecord[],
	selector: string,
	explicitSourceKey: string | undefined,
	discovery: AgentDiscoveryResult,
	environmentId: AionEnvironmentId
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
			`No discovered agent matched '${selector}' in ${environmentId}. Available agents: ${formatAgentCandidates(agents)}. Sources checked: ${formatSourceStatuses(discovery)}`
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
	discovery: AgentDiscoveryResult,
	options: HeadlessRunOptions,
	selectedAgentKey: string | undefined,
	selectedAgentId: string | undefined,
	explicitSourceKey: string | undefined,
	environmentId: AionEnvironmentId
): DiscoveredAgentRecord {
	const agents = discovery.agents;
	if (options.agentSelector) {
		return selectAgentBySelector(
			agents,
			options.agentSelector,
			explicitSourceKey,
			discovery,
			environmentId
		);
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
			`No agent selected in ${environmentId}. Use --agent with one of: ${formatAgentCandidates(agents)}. Sources checked: ${formatSourceStatuses(discovery)}`
		);
	}
	return selected;
}

function createHeadlessRunLogger(
	createLogger: typeof createChatSessionLogger,
	environmentId: AionEnvironmentId,
	stderr: WritableStreamLike
): CreateChatSessionLoggerResult {
	const result = createLogger({ environmentId });
	if (result.warning) {
		writeLine(stderr, result.warning);
	}
	return result;
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
	const isTerminalTask = isTerminalTaskState(task.status?.state);
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
	if (isTerminalTaskState(event.status?.state)) {
		state.reachedTerminal = true;
	}

	if (
		shouldRenderLiveStatusMessage({
			message: event.status?.message,
			taskId: event.taskId,
			streamedTaskIds: state.streamedTaskIds
		})
	) {
		return event.status?.message
			? renderAgentMessage(state, event.status.message, event.taskId)
			: false;
	}
	return false;
}

function handleMessageOutputArtifactUpdate(
	state: MessageOutputState,
	event: TaskArtifactUpdateEvent
): boolean {
	if (!event.artifact) {
		return false;
	}
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
	params: SendMessageRequest;
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
	if (isMessage(response)) {
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
	params: SendMessageRequest;
	responseMode: ResponseMode;
	stdout: WritableStreamLike;
	stderr: WritableStreamLike;
}): Promise<void> {
	const state = createMessageOutputState();

	for await (const streamResponse of clientState.client.sendMessageStream(params)) {
		if (responseMode === "a2a-protocol") {
			writeRawPayload(stdout, streamResponse);
			continue;
		}

		const event = unwrapStreamResponse(streamResponse);
		if (!event) {
			continue;
		}
		if (isMessage(event)) {
			handleMessageOutputMessage(state, event);
		} else if (isTask(event)) {
			handleMessageOutputTask(state, event);
		} else if (isTaskStatusUpdateEvent(event)) {
			handleMessageOutputStatusUpdate(state, event);
		} else if (isTaskArtifactUpdateEvent(event)) {
			handleMessageOutputArtifactUpdate(state, event);
		}
	}

	if (responseMode !== "a2a-protocol") {
		writeMessageOutput(state, responseMode, stdout, stderr);
	}
}

function canStream(agentCard: AgentCard): boolean {
	return Boolean(agentCard.capabilities?.streaming);
}

function isAionControlPlaneRegistryAgent(
	selectedAgent: DiscoveredAgentRecord,
	environmentId: AionEnvironmentId
): boolean {
	return (
		selectedAgent.source.type === "registry" &&
		normalizeSourceUrl(selectedAgent.source.url) ===
			normalizeSourceUrl(getControlPlaneApiBaseUrl(environmentId))
	);
}

function getConnectionOptions(
	options: HeadlessRunOptions,
	selectedAgent: DiscoveredAgentRecord,
	environmentId: AionEnvironmentId,
	registryTokenProvider: TokenProvider
): HeadlessRunOptions & { url: string; tokenProvider?: TokenProvider } {
	const useCliEndpointAuth = isTransientAgentSource(selectedAgent.source);
	const useAionRegistryAuth = isAionControlPlaneRegistryAgent(
		selectedAgent,
		environmentId
	);
	return {
		...options,
		url: selectedAgent.connectionUrl,
		agentId: selectedAgent.connectionAgentId,
		token: useCliEndpointAuth ? options.token : undefined,
		tokenProvider: useAionRegistryAuth ? registryTokenProvider : undefined,
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
	const createChatSessionLoggerImpl =
		dependencies.createChatSessionLoggerImpl ?? createChatSessionLogger;

	const { settings, warning } = loadChatSettingsImpl();
	if (warning) {
		writeLine(stderr, warning);
	}
	const selectedEnvironment = settings.selectedEnvironment;
	const { logger } = createHeadlessRunLogger(
		createChatSessionLoggerImpl,
		selectedEnvironment,
		stderr
	);
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

	logger.info("headless.run.started", {
		environmentId: selectedEnvironment,
		requestMode: options.requestMode,
		responseMode: options.responseMode,
		agentSelector: options.agentSelector,
		agentId: options.agentId,
		hasExplicitUrl: Boolean(options.url),
		sourceCount: runtimeSources.length
	});

	try {
		const discovery = await discoverAgentSourcesImpl(runtimeSources, fetchImpl, {
			environmentId: selectedEnvironment,
			controlPlaneAccessTokenProvider: () =>
				getStoredAccessTokenImpl(selectedEnvironment),
			graphQLFetchImpl: fetchImpl,
			sourceFetchImpl: (source) =>
				source.sourceKey === explicitSourceKey ? explicitSourceFetch : fetchImpl,
			logger
		});
		writeDiscoveryErrors(discovery, stderr);

		if (discovery.agents.length === 0) {
			throw new Error(
				`No agents discovered for ${selectedEnvironment}. Sources checked: ${formatSourceStatuses(discovery)}`
			);
		}

		const selectedAgent = selectHeadlessAgent(
			discovery,
			options,
			environmentSettings.selectedAgentKey,
			environmentSettings.selectedAgentId,
			explicitSourceKey,
			selectedEnvironment
		);
		const clientState = await connectClientImpl(
			getConnectionOptions(
				options,
				selectedAgent,
				selectedEnvironment,
				() => getStoredAccessTokenImpl(selectedEnvironment)
			)
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

		if (
			options.requestMode === "streaming-message" &&
			canStream(clientState.agentCard)
		) {
			await sendStreamingMessage({
				clientState,
				params,
				responseMode: options.responseMode,
				stdout,
				stderr
			});
			logger.info("headless.run.completed", {
				environmentId: selectedEnvironment,
				selectedAgentKey: selectedAgent.agentKey,
				selectedAgentId: selectedAgent.agentId,
				selectedAgentHandle: selectedAgent.agentHandle,
				requestMode: options.requestMode,
				responseMode: options.responseMode
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
		logger.info("headless.run.completed", {
			environmentId: selectedEnvironment,
			selectedAgentKey: selectedAgent.agentKey,
			selectedAgentId: selectedAgent.agentId,
			selectedAgentHandle: selectedAgent.agentHandle,
			requestMode: options.requestMode,
			responseMode: options.responseMode
		});
		return 0;
	} catch (error) {
		logger.error("headless.run.failed", { error });
		throw error;
	} finally {
		logger.flush();
	}
}
