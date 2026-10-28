import { createChatThread } from "./lib/welcomeMessage.js";
import { randomUUID } from "node:crypto";

import type {
	Message,
	Part,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import { Role, roleToJSON } from "@a2a-js/sdk";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text, useApp, useInput, useStdout } from "ink";

import type { ChatCliOptions } from "./args.js";
import {
	ChatComposer,
	type AgentSuggestionView
} from "./components/ChatComposer.js";
import { ChatSession } from "./components/ChatSession.js";
import { SystemNotificationStack } from "./components/SystemNotificationStack.js";
import {
	type TranscriptEntry,
	WorkingIndicator
} from "./components/MessageBubble.js";
import {
	clearAgentMention,
	getAgentMentionMatch,
	parseAgentSelection
} from "./lib/agentSelection.js";
import {
	selectChatEnvironment,
	loadChatSettings,
	saveChatSettings,
	type ChatSettings
} from "./lib/chatSettings.js";
import { openUrlInDefaultBrowser } from "./lib/browser.js";
import { writeClipboard } from "./lib/clipboard.js";
import {
	applyFileSuggestion,
	buildMessageParts,
	clearFileMention,
	createComposerInputState,
	deleteComposerTextBackward,
	getFileMentionMatch,
	getFileSuggestions,
	getComposerContentWidth,
	insertComposerText,
	isComposerBackwardDeleteKey,
	moveComposerCursorHorizontally,
	moveComposerCursorToRowBoundary,
	moveComposerCursorVertically,
	replaceComposerDraft,
	type ComposerDraftUpdate,
	type FileSuggestion
} from "./lib/input";
import { loadChatModeSettings, saveChatModeSettings } from "./lib/chatSettings.js";
import {
	buildMessageParams,
	connectClient,
	createPushNotificationConfig
} from "./lib/connection.js";
import {
	type AionEnvironmentId,
	AION_ENVIRONMENT_IDS,
	getControlPlaneApiBaseUrl,
	getGraphQLHttpUrl,
	isAionEnvironmentId
} from "./lib/environment.js";
import {
	buildWebAppRouteUrl,
	resolvePostAuthPath,
	runLoginBootstrap
} from "./lib/graphql/authBootstrap.js";
import {
	discoverAgentSources,
	selectDiscoveredAgent,
	type RegistrySelection,
	toPersistedAgents
} from "./lib/agents/discovery.js";
import {
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	createExplicitAgentSource,
	isTransientAgentSource,
	mergeAgentSources,
	createDefaultRegistryAgentSource
} from "./lib/agents/model.js";
import {
	resolveSessionFilePath,
	saveCompletedExchange, loadContextSession, copyContextSession
} from "./lib/agents/sessionStore.js";
import { type ChatOperation, ChatOperationGate } from "./lib/chatOperationGate.js";
import { eligibleLegacyRegistryAgent } from "./lib/workspaces.js";
import { fetchRegistryAccount, type RegistryAccount } from "./lib/graphql/registry.js";
import { restoredTranscriptEntries } from "./lib/restoredTranscript.js";
import { createChatSessionLogger } from "./lib/sessionLogger.js";
import {
	formatProtocolPayload,
	formatProtocolPayloadAsJson
} from "./lib/protocolOutput.js";
import {
	EPHEMERAL_MESSAGE_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID,
	THINKING_DELTA_ARTIFACT_ID
} from "./lib/a2aMetadata.js";
import {
	isMessage,
	isTask,
	isTaskArtifactUpdateEvent,
	isTaskStatusUpdateEvent,
	taskStateLabel,
	unwrapStreamResponse,
	type StreamEvent
} from "./lib/a2aProtocol.js";
import {
	formatMessageParts,
	getTaskMessages
} from "./lib/messageDisplay.js";
import {
	applyPreparedStreamTranscriptDelta,
	clearActiveStreamTranscriptSection,
	clearStreamTranscriptState,
	closeStreamTranscriptSections,
	createStreamTranscriptState,
	finalizeTranscriptEntries,
	getActiveStreamTranscriptSection,
	getLastStreamTranscriptSection,
	prepareStreamTranscriptDelta,
	replaceTranscriptEntryBody,
	type StreamTranscriptArtifactKind,
	upsertTranscriptEntry
} from "./lib/transcript.js";
import {
	getMessageTaskId,
	getShownMessageKey,
	getUnshownTaskAgentMessages,
	markShownMessage,
	shouldShowNoAgentMessageNotice,
	shouldRenderLiveStatusMessage,
	shouldRenderLiveResponseMessage
} from "./lib/chatSession.js";
import {
	type PushNotificationEvent,
	startPushNotificationServer
} from "./lib/pushListener.js";
import {
	clearLeadingSlashDraft,
	filterSlashCommands,
	formatAgentSourcesList,
	getLeadingSlashQuery,
	getRequestModeLabel,
	getResponseModeLabel,
	getSlashCommandById,
	type RequestMode,
	type ResponseMode,
	type SlashCommandId
} from "./lib/slashCommands.js";
import {
	isTaskContinuationState,
	isTerminalTaskState
} from "./lib/taskState.js";
import { requestTerminalClear } from "./lib/terminal.js";
import { getStoredAccessToken, loginWithWorkOS } from "./lib/workosAuth.js";
import { SourceCredentials } from "./lib/sourceCredentials.js";
import { GUEST_HISTORY_WARNING } from "./lib/anonymousSession.js";

const NO_AGENT_MESSAGE_NOTICE = "Task completed with no agent message.";
const NOTIFICATION_TTL_MS = 12_000;

function summarizeOptionsForLog(options: ChatCliOptions): Record<string, unknown> {
	return {
		hasAgentEndpointUrl: Boolean(options.url),
		agentEndpointUrl: options.url,
		agentId: options.agentId,
		pushNotifications: options.pushNotifications,
		pushReceiverConfigured: Boolean(options.pushReceiver),
		headers: Object.keys(options.headers ?? {}),
		hasToken: Boolean(options.token)
	};
}

function summarizePartForLog(part: Part): Record<string, unknown> {
	return {
		kind: part.content?.$case,
		text: part.content?.$case === "text" ? part.content.value : undefined,
		fileName: part.filename || undefined,
		fileMimeType: part.mediaType || undefined
	};
}

function roleLabel(role: Message["role"] | string | undefined): string {
	if (role === undefined) {
		return "ROLE_UNSPECIFIED";
	}
	if (typeof role === "string") {
		return role;
	}
	return roleToJSON(role);
}

function summarizeMessageForLog(message: Message): Record<string, unknown> {
	return {
		kind: "message",
		messageId: message.messageId,
		role: roleLabel(message.role),
		contextId: message.contextId,
		taskId: message.taskId,
		partCount: message.parts.length,
		parts: message.parts.map(summarizePartForLog)
	};
}

function summarizeTaskForLog(task: Task): Record<string, unknown> {
	const messages = getTaskMessages(task);
	return {
		kind: "task",
		taskId: task.id,
		contextId: task.contextId,
		status: taskStateLabel(task.status?.state),
		artifactCount: task.artifacts?.length ?? 0,
		messageCount: messages.length,
		messages: messages.map(summarizeMessageForLog)
	};
}

function summarizeProtocolEventForLog(event: StreamEvent): Record<string, unknown> {
	if (isMessage(event)) {
		return summarizeMessageForLog(event);
	}
	if (isTask(event)) {
		return summarizeTaskForLog(event);
	}
	if (isTaskStatusUpdateEvent(event)) {
		return {
			kind: "status-update",
			taskId: event.taskId,
			contextId: event.contextId,
			status: taskStateLabel(event.status?.state),
			final: isFinalStatusEvent(event),
			message: event.status?.message
				? summarizeMessageForLog(event.status.message)
				: undefined
		};
	}
	const artifactEvent = event as StreamEvent & {
		lastChunk?: unknown;
		last_chunk?: unknown;
	};
	return {
		kind: "artifact-update",
		taskId: event.taskId,
		contextId: event.contextId,
		artifactId: event.artifact?.artifactId,
		append: event.append,
		lastChunk: artifactEvent.lastChunk ?? artifactEvent.last_chunk,
		partCount: event.artifact?.parts.length ?? 0,
		parts: event.artifact?.parts.map(summarizePartForLog) ?? []
	};
}

function summarizeAgentForLog(
	agent: DiscoveredAgentRecord | undefined
): Record<string, unknown> | undefined {
	if (!agent) {
		return undefined;
	}
	return {
		agentKey: agent.agentKey,
		agentId: agent.agentId,
		id: agent.id,
		sourceKey: agent.sourceKey,
		agentCardName: agent.agentCardName,
		agentHandle: agent.agentHandle,
		connectionUrl: agent.connectionUrl,
		connectionAgentId: agent.connectionAgentId
	};
}

function isFinalStatusEvent(event: TaskStatusUpdateEvent): boolean {
	return Boolean((event as TaskStatusUpdateEvent & { final?: boolean }).final);
}

function getStreamArtifactKind(
	artifactId: string | undefined
): StreamTranscriptArtifactKind | undefined {
	if (artifactId === STREAM_DELTA_ARTIFACT_ID) {
		return "response";
	}
	if (artifactId === THINKING_DELTA_ARTIFACT_ID) {
		return "thinking";
	}
	return undefined;
}

function getMetadataString(
	metadata: Record<string, unknown> | undefined,
	key: string
): string | undefined {
	const value = metadata?.[key];
	return typeof value === "string" ? value : undefined;
}

function shouldReplaceCurrentStreamSection(
	event: TaskArtifactUpdateEvent,
	kind: StreamTranscriptArtifactKind
): boolean {
	if (event.append === true) {
		return false;
	}

	const artifactEvent = event as TaskArtifactUpdateEvent & {
		lastChunk?: boolean;
		last_chunk?: boolean;
	};
	if (artifactEvent.lastChunk === true || artifactEvent.last_chunk === true) {
		return true;
	}

	const metadata = event.artifact?.metadata as
		| Record<string, unknown>
		| undefined;
	const status = getMetadataString(metadata, "status");
	const statusReason =
		getMetadataString(metadata, "status_reason") ??
		getMetadataString(metadata, "statusReason");
	return (
		status === "finalized" ||
		statusReason === "complete_message" ||
		statusReason === "complete_task"
	);
}

interface CopyableResponse {
	kind: "message" | "protocol";
	content: string;
}

type ExactSlashCommand =
	| { kind: "session"; action: "new" | "retry"; sourceKey?: string }
	| { kind: "workspace"; selector?: string }
	| { kind: "login" }
	| { kind: "copy" }
	| { kind: "clear" }
	| { kind: "environment"; environmentId?: AionEnvironmentId; controlPlaneOrigin?: string }
	| { kind: "invalid"; message: string };

function parseExactSlashCommand(value: string): ExactSlashCommand | undefined {
	const [command, environmentId, extra, trailing] = value.trim().split(/\s+/);
	if (command === "/workspace") {
		const raw = value.trim().slice("/workspace".length).trim();
		if (!raw) return { kind: "workspace" };
		const quote = raw[0];
		if (quote === '"' || quote === "'") {
			if (raw.length < 2 || raw.at(-1) !== quote) return { kind: "invalid", message: "Close the workspace name's quotation marks." };
			return { kind: "workspace", selector: raw.slice(1, -1) };
		}
		return { kind: "workspace", selector: raw };
	}
	if (command === "/session" && (environmentId === "new" || environmentId === "retry")) {
		return { kind: "session", action: environmentId, sourceKey: extra };
	}
	if (command === "/login" && !environmentId) {
		return { kind: "login" };
	}
	if (command === "/copy" && !environmentId) {
		return { kind: "copy" };
	}
	if (command === "/clear" && !environmentId) {
		return { kind: "clear" };
	}
	if (command === "/environment" || command === "/env") {
		if (!environmentId) return { kind: "environment" };
		if (!isAionEnvironmentId(environmentId) || trailing) {
			return { kind: "invalid", message: "Use /env <production|staging|development> [development-api-origin]." };
		}
		try {
			return { kind: "environment", environmentId,
				...(extra !== undefined ? { controlPlaneOrigin: getControlPlaneApiBaseUrl(environmentId, extra) } : {}) };
		} catch (error) {
			return { kind: "invalid", message: error instanceof Error ? error.message : "Invalid API origin." };
		}
	}
	return undefined;
}

export function ChatApp({ options }: { options: ChatCliOptions }): React.JSX.Element {
	const { exit } = useApp();
	const { stdout, write: writeToStdout } = useStdout();
	const initialSettingsResult = useMemo(() => loadChatSettings(), []);
	const chatSessionLoggerResult = useMemo(
		() =>
			createChatSessionLogger({
				environmentId: initialSettingsResult.settings.selectedEnvironment
			}),
		[initialSettingsResult.settings.selectedEnvironment]
	);
	const chatSessionLogger = chatSessionLoggerResult.logger;
	const [chatSettings, setChatSettings] = useState<ChatSettings>(
		initialSettingsResult.settings
	);
	const chatSettingsRef = useRef(chatSettings);
	const operationGate = useRef(new ChatOperationGate()).current;
	const [selectedEnvironment, setSelectedEnvironment] = useState<AionEnvironmentId>(
		initialSettingsResult.settings.selectedEnvironment
	);
	const activeEnvironmentSettings =
		chatSettings.environments[selectedEnvironment];
	const controlPlaneOrigin = activeEnvironmentSettings.controlPlaneOrigin;
	const controlPlaneApiBaseUrl = getControlPlaneApiBaseUrl(selectedEnvironment, controlPlaneOrigin);
	const agentEndpointUrl = options.url;
	const [pushLabel, setPushLabel] = useState(
		options.pushNotifications ? "Starting..." : "Disabled"
	);
	const [streamLabel, setStreamLabel] = useState("Idle");
	const [composerInput, setComposerInput] = useState(
		createComposerInputState
	);
	const { draft, cursor: draftCursor } = composerInput;
	const replaceDraft = (update: ComposerDraftUpdate): void => {
		setComposerInput((current) => replaceComposerDraft(current, update));
	};
	const [entries, setEntries] = useState<TranscriptEntry[]>([]);
	const [transcriptGeneration, setTranscriptGeneration] = useState(0);
	const [notifications, setNotifications] = useState<TranscriptEntry[]>([]);
	const contextIdRef = useRef<string | undefined>(undefined);
	const contextScopeRef = useRef("");
	const createdContextsRef = useRef(new Map<string, string>());
	const connectedAgentKeyRef = useRef<string | undefined>(undefined);
	const currentThreadScopeRef = useRef("");
	const welcomeLifetimeRef = useRef(new AbortController());
	const foregroundLifetimeRef = useRef(new AbortController());
	const setCurrentContextId = (value: string | undefined): void => {
		contextIdRef.current = value;
		contextScopeRef.current = `${selectedEnvironment}:${selectedAgentKey ?? ""}`;
	};
	const [taskId, setTaskId] = useState<string>();
	const [workingStartedAt, setWorkingStartedAt] = useState<number>();
	const [clientState, setClientState] = useState<Awaited<ReturnType<typeof connectClient>>>();
	const [pushConfig, setPushConfig] = useState<
		ReturnType<typeof createPushNotificationConfig> | undefined
	>();
	const [discoveredAgents, setDiscoveredAgents] = useState<DiscoveredAgentRecord[]>([]);
	const [registrySelection, setRegistrySelection] = useState<RegistrySelection>();
	const [workspaceAccount, setWorkspaceAccount] = useState<RegistryAccount>();
	const workspaceSelectorRef = useRef(options.workspace);
	const discoveryLifetimeRef = useRef(new AbortController());
	const connectionAbortRef = useRef<AbortController | undefined>(undefined);
	const workspaceConnectionRef = useRef<{ agentKey: string; operation: ChatOperation; finish(): void } | undefined>(undefined);
	const [workspaceConnectionNonce, setWorkspaceConnectionNonce] = useState(0);
	const [agentSources, setAgentSources] = useState<Record<string, AgentSourceRecord>>(
		activeEnvironmentSettings.agentSources
	);
	const [selectedAgentKey, setSelectedAgentKey] = useState<string | undefined>(
		options.agentId ? undefined : activeEnvironmentSettings.selectedAgentKey
	);
	const [selectedAgentId, setSelectedAgentId] = useState<string | undefined>(
		options.agentId ?? activeEnvironmentSettings.selectedAgentId
	);
	const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(0);
	const [selectedFileSuggestionIndex, setSelectedFileSuggestionIndex] = useState(0);
	const [selectedSlashIndex, setSelectedSlashIndex] = useState(0);
	const [selectedSlashSubmenuIndex, setSelectedSlashSubmenuIndex] = useState(0);
	const [slashSubmenuId, setSlashSubmenuId] = useState<SlashCommandId | undefined>();
	const [requestMode, setRequestMode] = useState<RequestMode>(
		activeEnvironmentSettings.requestMode
	);
	const [responseMode, setResponseMode] = useState<ResponseMode>(
		activeEnvironmentSettings.responseMode
	);
	const [reconnectNonce, setReconnectNonce] = useState(0);
	const credentials = useMemo(() => new SourceCredentials({
		environmentId: selectedEnvironment, controlPlaneOrigin, cli: options,
		accountToken: () => getStoredAccessToken(selectedEnvironment, { controlPlaneOrigin })
	}), [selectedEnvironment, controlPlaneOrigin, options.token, options.headers]);
	useEffect(() => () => credentials.dispose(), [credentials]);
	const credentialScopeRef = useRef<string | undefined>(undefined);
	const connectionSignalRef = useRef<AbortSignal | undefined>(undefined);
	currentThreadScopeRef.current = `${selectedEnvironment}:${selectedAgentKey ?? ""}`;
	const shownMessageKeysRef = useRef<Set<string>>(new Set());
	const streamedTaskIdsRef = useRef<Set<string>>(new Set());
	const streamTranscriptStateRef = useRef(createStreamTranscriptState());
	const lastCopyableResponseRef = useRef<CopyableResponse | undefined>(undefined);
	const lastConnectionNoticeRef = useRef<string | undefined>(undefined);
	const notificationTimersRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);

	const appendEntry = (role: TranscriptEntry["role"], body: string): void => {
		setEntries((current) => [
			...current,
			{
				id: randomUUID(),
				role,
				body,
				isFinalized: true
			}
		]);
	};

	const appendSystem = (body: string): void => {
		appendEntry("system", body);
	};

	const appendNotification = (body: string): void => {
		const id = randomUUID();
		chatSessionLogger.info("system.notification.shown", {
			notificationId: id,
			body
		});
		setNotifications((current) => [
			...current.slice(-2),
			{
				id,
				role: "system",
				body,
				isFinalized: true
			}
		]);
		const timer = setTimeout(() => {
			chatSessionLogger.debug("system.notification.dismissed", {
				notificationId: id
			});
			setNotifications((current) => current.filter((item) => item.id !== id));
		}, NOTIFICATION_TTL_MS);
		notificationTimersRef.current.push(timer);
	};

	const appendStatus = (body: string): void => {
		appendEntry("status", body);
	};

	const appendProtocol = (payload: unknown): void => {
		const json = formatProtocolPayloadAsJson(payload);
		lastCopyableResponseRef.current = {
			kind: "protocol",
			content: json
		};
		chatSessionLogger.debug("a2a.protocol_payload.rendered", {
			payload: json
		});
		appendEntry("protocol", formatProtocolPayload(payload));
	};

	const finalizeOpenStreamEntries = (streamTaskId?: string): void => {
		const entryIds = closeStreamTranscriptSections({
			state: streamTranscriptStateRef.current,
			taskId: streamTaskId
		});
		if (entryIds.size > 0) {
			setEntries((current) => finalizeTranscriptEntries(current, entryIds));
		}
	};

	useEffect(() => {
		welcomeLifetimeRef.current = new AbortController();
		const environmentAtStart = selectedEnvironment;
		chatSessionLogger.info("chat.session.started", {
			mode: "interactive",
			environmentId: environmentAtStart,
			logFilePath: chatSessionLogger.logFilePath,
			requestMode,
			responseMode,
			options: summarizeOptionsForLog(options)
		});
		if (chatSessionLoggerResult.warning) {
			appendNotification(chatSessionLoggerResult.warning);
		}

		return () => {
			welcomeLifetimeRef.current.abort();
			foregroundLifetimeRef.current.abort();
			operationGate.cancel();
			discoveryLifetimeRef.current.abort();
			chatSessionLogger.info("chat.session.ended", {
				mode: "interactive",
				environmentId: environmentAtStart
			});
			for (const timer of notificationTimersRef.current) {
				clearTimeout(timer);
			}
			chatSessionLogger.flush();
		};
	}, []);

	const persistSettings = (nextSettings: ChatSettings): void => {
		chatSettingsRef.current = nextSettings;
		setChatSettings(nextSettings);
		const warning = saveChatSettings(nextSettings);
		if (warning) {
			appendSystem(warning);
		}
	};

	const persistEnvironmentSettings = (
		environmentId: AionEnvironmentId,
		update: Partial<(typeof chatSettings.environments)[AionEnvironmentId]>
	): void => {
		const latest = chatSettingsRef.current;
		const current = latest.environments[environmentId];
		persistSettings({
			...latest,
			environments: {
				...latest.environments,
				[environmentId]: {
					...current,
					...update
				}
			}
		});
	};

	const persistAgentSelection = (agent: DiscoveredAgentRecord): void => {
		if (isTransientAgentSource(agent.source)) return;
		const environment = chatSettingsRef.current.environments[selectedEnvironment];
		const registry = agent.registry;
		persistEnvironmentSettings(selectedEnvironment, {
			selectedAgentKey: agent.agentKey, selectedAgentId: agent.id,
			...(registry ? { registryAccounts: {
				...environment.registryAccounts,
				[registry.accountScope]: {
					...environment.registryAccounts?.[registry.accountScope],
					workspaceId: registry.workspace.id,
					selectedAgentKeys: {
						...environment.registryAccounts?.[registry.accountScope]?.selectedAgentKeys,
						[registry.workspace.id]: agent.agentKey
					}
				}
			} } : {})
		});
	};

	const persistModes = (nextRequestMode: RequestMode, nextResponseMode: ResponseMode): void => {
		persistEnvironmentSettings(selectedEnvironment, {
			requestMode: nextRequestMode,
			responseMode: nextResponseMode
		});
	};

	const applyRequestMode = (mode: RequestMode): void => {
		setRequestMode(mode);
		persistModes(mode, responseMode);
		appendSystem(`Request mode: ${getRequestModeLabel(mode)}`);
	};

	const applyResponseMode = (mode: ResponseMode): void => {
		setResponseMode(mode);
		persistModes(requestMode, mode);
		appendSystem(`Response mode: ${getResponseModeLabel(mode)}`);
	};

	const clearTranscript = (): void => {
		// Retire the old conversation's responses, not the reusable connection
		// or independent welcomes whose results belong to their original thread.
		foregroundLifetimeRef.current.abort();
		if (operationGate.kind === "reply") operationGate.cancel();
		foregroundLifetimeRef.current = new AbortController();
		setEntries([]);
		setTranscriptGeneration((current) => current + 1);
		shownMessageKeysRef.current.clear();
		streamedTaskIdsRef.current.clear();
		clearStreamTranscriptState(streamTranscriptStateRef.current);
		lastCopyableResponseRef.current = undefined;
		setCurrentContextId(undefined);
		setTaskId(undefined);
		setStreamLabel("Idle");
		setWorkingStartedAt(undefined);
	};

	const reloadEnvironmentState = (environmentId: AionEnvironmentId, origin?: string): void => {
		operationGate.cancel();
		discoveryLifetimeRef.current.abort();
		workspaceSelectorRef.current = undefined;
		setRegistrySelection(undefined);
		setWorkspaceAccount(undefined);
		setSlashSubmenuId(undefined);
		credentials.cancelPending();
		welcomeLifetimeRef.current.abort();
		welcomeLifetimeRef.current = new AbortController();
		createdContextsRef.current.clear();
		lastConnectionNoticeRef.current = undefined;
		connectedAgentKeyRef.current = undefined;
		credentialScopeRef.current = undefined;
		const nextSettings = selectChatEnvironment(chatSettingsRef.current, environmentId, origin);
		const nextEnvironmentSettings = nextSettings.environments[environmentId];
		persistSettings(nextSettings);
		setSelectedEnvironment(environmentId);
		setRequestMode(nextEnvironmentSettings.requestMode);
		setResponseMode(nextEnvironmentSettings.responseMode);
		setAgentSources(nextEnvironmentSettings.agentSources);
		setSelectedAgentKey(options.agentId ? undefined : nextEnvironmentSettings.selectedAgentKey);
		setSelectedAgentId(options.agentId ?? nextEnvironmentSettings.selectedAgentId);
		setDiscoveredAgents([]);
		clearTranscript();
		setClientState(undefined);
		setReconnectNonce((current) => current + 1);
	};

	useEffect(() => {
		if (initialSettingsResult.warning) {
			appendSystem(initialSettingsResult.warning);
		}
	}, [initialSettingsResult.warning]);

	const isCursorAtDraftEnd = draftCursor === draft.length;

	const slashQuery = useMemo(() => {
		if (slashSubmenuId || !isCursorAtDraftEnd) {
			return undefined;
		}

		return getLeadingSlashQuery(draft);
	}, [draft, isCursorAtDraftEnd, slashSubmenuId]);

	const slashCommands = useMemo(() => {
		return filterSlashCommands(slashQuery);
	}, [slashQuery]);

	const slashSubmenu = useMemo(() => {
		const command = getSlashCommandById(slashSubmenuId);
		if (!command) {
			return undefined;
		}

		return {
			title: command.title,
			subtitle: command.subtitle,
			options: command.id === "workspace" ? (workspaceAccount?.workspaces ?? []).map((workspace) => ({
				value: workspace.id,
				label: workspace.name + (registrySelection?.workspace.id === workspace.id ? " (current)" : ""),
				description: workspace.id
			})) : command.options,
			selectedIndex: selectedSlashSubmenuIndex
		};
	}, [selectedSlashSubmenuIndex, slashSubmenuId, workspaceAccount, registrySelection]);

	const agentSuggestions = useMemo<AgentSuggestionView[]>(() => {
		if (slashQuery !== undefined || slashSubmenuId || !isCursorAtDraftEnd) {
			return [];
		}

		const mentionMatch = getAgentMentionMatch(draft);
		if (!mentionMatch) {
			return [];
		}

		return discoveredAgents
			.filter((agent) => agent.id.startsWith(mentionMatch.query))
			.map((agent) => ({
				agentKey: agent.agentKey,
				id: agent.id,
				sourceName: agent.source.sourceKey,
				description: agent.agentCard?.description?.trim() ?? ""
			}))
			.slice(0, 6);
	}, [discoveredAgents, draft, isCursorAtDraftEnd, slashQuery, slashSubmenuId]);

	const fileSuggestions = useMemo((): FileSuggestion[] => {
		if (slashQuery !== undefined || slashSubmenuId || !isCursorAtDraftEnd) {
			return [];
		}

		const match = getFileMentionMatch(draft);
		if (!match) return [];

		return getFileSuggestions(match.query);
	}, [draft, isCursorAtDraftEnd, slashQuery, slashSubmenuId]);

	const selectedAgent = useMemo(() => {
		return discoveredAgents.find((agent) =>
			selectedAgentKey
				? agent.agentKey === selectedAgentKey
				: selectedAgentId
					? agent.id === selectedAgentId
					: false
		);
	}, [discoveredAgents, selectedAgentId, selectedAgentKey]);

	useEffect(() => {
		setSelectedSuggestionIndex((current) => {
			if (agentSuggestions.length === 0) {
				return 0;
			}
			return Math.min(current, agentSuggestions.length - 1);
		});
	}, [agentSuggestions]);

	useEffect(() => {
		setSelectedFileSuggestionIndex((current) => {
			if (fileSuggestions.length === 0) return 0;
			return Math.min(current, fileSuggestions.length - 1);
		});
	}, [fileSuggestions]);

	useEffect(() => {
		setSelectedSlashIndex((current) => {
			if (slashCommands.length === 0) {
				return 0;
			}
			return Math.min(current, slashCommands.length - 1);
		});
	}, [slashCommands]);

	useEffect(() => {
		const optionCount = slashSubmenu?.options.length ?? 0;
		setSelectedSlashSubmenuIndex((current) => {
			if (optionCount === 0) {
				return 0;
			}
			return Math.min(current, optionCount - 1);
		});
	}, [slashSubmenuId, workspaceAccount]);

	const refreshAgentDiscovery = async ({
		announceExplicitSourceErrors = true,
		isClosed = () => false,
		workspaceSelector = workspaceSelectorRef.current,
		workspaceChange
	}: {
		announceExplicitSourceErrors?: boolean;
		isClosed?: () => boolean;
		workspaceSelector?: string;
		workspaceChange?: { operation: ChatOperation; finish(): void; requireRegistry?: boolean };
	} = {}): Promise<AgentSourceRecord[] | undefined> => {
		discoveryLifetimeRef.current.abort();
		const discoveryLifetime = new AbortController();
		discoveryLifetimeRef.current = discoveryLifetime;
		const signal = AbortSignal.any([credentials.signal, discoveryLifetime.signal,
			...(workspaceChange ? [workspaceChange.operation.signal] : [])]);
		const obsolete = () => isClosed() || signal.aborted;
		try {
			const latestEnvironment = chatSettingsRef.current.environments[selectedEnvironment];
			const runtimeSources = agentEndpointUrl ? [createExplicitAgentSource(agentEndpointUrl)] : mergeAgentSources(
				latestEnvironment.agentSources,
				selectedEnvironment,
				agentEndpointUrl,
				controlPlaneOrigin
			);
			const explicitSourceKey = agentEndpointUrl
				? createExplicitAgentSource(agentEndpointUrl).sourceKey
				: undefined;
			chatSessionLogger.debug("agent.discovery.refresh.started", {
				environmentId: selectedEnvironment,
				sourceCount: runtimeSources.length,
				explicitSourceKey
			});
			const discovery = await discoverAgentSources(
				runtimeSources,
				fetch,
				{
					environmentId: selectedEnvironment,
					controlPlaneOrigin,
					workspaceSelector,
					registryPreferences: latestEnvironment.registryAccounts,
					controlPlaneAccessTokenProvider: (request) =>
						getStoredAccessToken(selectedEnvironment, {
							forceRefresh: request?.forceRefresh,
							controlPlaneOrigin
						}),
					graphQLFetchImpl: credentials.fetch(createDefaultRegistryAgentSource(selectedEnvironment, controlPlaneOrigin), signal),
					sourceFetchImpl: (source) => credentials.fetch(source, signal),
					logger: chatSessionLogger
				}
			);
			chatSessionLogger.debug("agent.discovery.refresh.completed", {
				environmentId: selectedEnvironment,
				sourceCount: discovery.sources.length,
				agentCount: discovery.agents.length,
				errorCount: discovery.errors.length
			});
			if (obsolete()) {
				return undefined;
			}

			if (workspaceChange && workspaceChange.requireRegistry !== false && !discovery.registry) {
				appendNotification(discovery.errors.find((error) => error.source.type === "registry")?.error ?? "Workspace lookup failed. Please try again.");
				return undefined;
			}
			const switchingScope = !!(workspaceChange || registrySelection) && (registrySelection?.accountScope !== discovery.registry?.accountScope ||
				registrySelection?.workspace.id !== discovery.registry?.workspace.id);
			if (switchingScope) {
				welcomeLifetimeRef.current.abort();
				welcomeLifetimeRef.current = new AbortController();
				connectionAbortRef.current?.abort();
				setClientState(undefined);
				connectedAgentKeyRef.current = undefined;
				clearTranscript();
			}
			setRegistrySelection(discovery.registry);
			setWorkspaceAccount(discovery.workspaceAccount);
			if (discovery.workspaceAccount && !discovery.registry) setSlashSubmenuId("workspace");
			if (discovery.registry) workspaceSelectorRef.current = undefined;
			const currentEnvironment = chatSettingsRef.current.environments[selectedEnvironment];
			const nextSources = Object.fromEntries(
				discovery.sources.map((source) => [source.sourceKey, source])
			);
			const persistentSources = Object.fromEntries(
				discovery.sources
					.filter(
						(source) =>
							!isTransientAgentSource(source) &&
							source.sourceKey !== explicitSourceKey
					)
					.map((source) => [source.sourceKey, source])
			);
			const nextAgents = toPersistedAgents(
				discovery.agents.filter(
					(agent) =>
						!isTransientAgentSource(agent.source) &&
						agent.sourceKey !== explicitSourceKey
				),
				chatSettingsRef.current.environments[selectedEnvironment].agents
			);
			setAgentSources(nextSources);
			setDiscoveredAgents(discovery.agents);
			persistEnvironmentSettings(selectedEnvironment, {
				agents: nextAgents,
				agentSources: { ...currentEnvironment.agentSources, ...persistentSources },
				...(discovery.registry ? { registryAccounts: {
					...currentEnvironment.registryAccounts,
					[discovery.registry.accountScope]: {
						...currentEnvironment.registryAccounts?.[discovery.registry.accountScope],
						workspaceId: discovery.registry.workspace.id
					}
				} } : {})
			});

			if (announceExplicitSourceErrors) {
				for (const error of discovery.errors) {
					const shouldNotify =
						error.source.type === "registry" || !error.source.isDefault ||
						/guest session/i.test(error.error ?? "");
					if (shouldNotify && error.error) {
						appendNotification(
							`${error.source.description}: ${error.error}`
						);
					}
				}
			}

			const registry = discovery.registry;
			const savedRegistryKey = registry
				? currentEnvironment.registryAccounts?.[registry.accountScope]?.selectedAgentKeys?.[registry.workspace.id]
				: undefined;
			const legacySelected = registry && currentEnvironment.selectedAgentKey ? discovery.agents.find((agent) => agent.registry &&
				eligibleLegacyRegistryAgent({ agents: currentEnvironment.agents,
					sourceKey: agent.sourceKey, identityId: agent.agentId!,
					workspaceId: registry.workspace.id, homeWorkspaceId: registry.account.homeWorkspaceId,
					credentialScope: credentials.owner(agent.source)
				})?.agentKey === currentEnvironment.selectedAgentKey) : undefined;
			const savedDirect = discovery.agents.find((agent) => agent.source.type !== "registry" &&
				agent.agentKey === currentEnvironment.selectedAgentKey);
			const registryOnly = (workspaceChange && workspaceChange.requireRegistry !== false) ||
				workspaceSelector !== undefined || (discovery.workspaceAccount && !discovery.registry);
			const candidates = registryOnly
				? discovery.agents.filter((agent) => agent.source.type === "registry")
				: discovery.agents;
			const nextSelected = selectDiscoveredAgent(candidates, {
				requestedAgentId: options.agentId,
				selectedAgentKey: savedRegistryKey ?? legacySelected?.agentKey ?? savedDirect?.agentKey,
				explicitSourceKey,
				autoSelectExplicit: Boolean(agentEndpointUrl)
			});
			if (workspaceChange && nextSelected && (switchingScope || nextSelected.agentKey !== connectedAgentKeyRef.current || !clientState)) {
				workspaceConnectionRef.current = { ...workspaceChange, agentKey: nextSelected.agentKey };
				setWorkspaceConnectionNonce((value) => value + 1);
			}
			if (discovery.registry && !discovery.agents.some((agent) => agent.registry) && !discovery.errors.some((error) => error.source.type === "registry")) {
				appendNotification(`No agents are available in workspace "${discovery.registry.workspace.name}". Use /workspace to choose another.`);
			}
			if (nextSelected) {
				setSelectedAgentKey(nextSelected.agentKey);
				setSelectedAgentId(nextSelected.id);
				persistAgentSelection(nextSelected);
			} else {
				setSelectedAgentKey(undefined);
				setSelectedAgentId(undefined);
				setClientState(undefined);
				connectedAgentKeyRef.current = undefined;
				clearTranscript();
				persistEnvironmentSettings(selectedEnvironment, { selectedAgentKey: undefined, selectedAgentId: undefined });
				if (selectedAgentKey || selectedAgentId) appendSystem("The selected agent is no longer available in this workspace. Choose an agent to continue.");
			}

			return discovery.sources;
		} catch (error) {
			if (obsolete()) {
				return undefined;
			}
			if (!workspaceChange) setDiscoveredAgents([]);
			const message = error instanceof Error ? error.message : String(error);
			if (announceExplicitSourceErrors) {
				appendNotification(
					agentEndpointUrl
						? `No agents were found from the provided URL: ${agentEndpointUrl}\n${message}`
						: `Agent source discovery failed: ${message}`
				);
			}
			return undefined;
		}
	};

	useEffect(() => {
		let closed = false;
		const transition = !agentEndpointUrl && !operationGate.busy ? startWorkspaceOperation() : undefined;
		// Startup has no current conversation to preserve on a selection error.
		// Publish the available memberships so an invalid choice can open the picker.
		void refreshAgentDiscovery({
			isClosed: () => closed,
			...(transition ? { workspaceChange: { ...transition, requireRegistry: false } } : {})
		}).finally(() => {
			if (transition && workspaceConnectionRef.current?.operation !== transition.operation) transition.finish();
		});
		return () => {
			closed = true;
			transition?.operation.cancel();
		};
	}, [
		agentEndpointUrl,
		credentials,
		controlPlaneApiBaseUrl,
		options.agentId,
		options.workspace,
		options.headers,
		options.token,
		reconnectNonce,
		selectedEnvironment
	]);

	useEffect(() => {
		let closed = false;
		const connectionLifetime = new AbortController();
		connectionAbortRef.current = connectionLifetime;
		const workspaceConnection = workspaceConnectionRef.current?.agentKey === selectedAgent?.agentKey ? workspaceConnectionRef.current : undefined;
		const connectionSignal = AbortSignal.any([connectionLifetime.signal, ...(workspaceConnection ? [workspaceConnection.operation.signal] : [])]);
		connectionSignalRef.current = connectionSignal;
		setWorkingStartedAt(undefined);
		setStreamLabel("Idle");
		finalizeOpenStreamEntries();
		let closePush: (() => Promise<void>) | undefined;

		const handlePushEvent = (event: PushNotificationEvent): void => {
			if (closed || connectionSignal.aborted) return;
			if (event.kind === "validation") {
				appendSystem(`Push notification validation received: ${String(event.payload)}`);
				return;
			}

			appendSystem(
				`Push notification payload:\n\n\`\`\`json\n${JSON.stringify(
					event.payload,
					null,
					2
				)}\n\`\`\``
			);
		};

		const connect = async (): Promise<void> => {
			if (!selectedAgent) {
				return;
			}

			try {
				if (options.pushNotifications) {
					const server = await startPushNotificationServer(
						options.pushReceiver,
						handlePushEvent
					);
					if (closed || connectionSignal.aborted) {
						await server.close();
						return;
					}
					closePush = server.close;
					setPushConfig(createPushNotificationConfig(options.pushReceiver));
					setPushLabel(server.callbackUrl);
				}

				setClientState(undefined);
				connectedAgentKeyRef.current = undefined;
				setTaskId(undefined);
				const connected = await connectClient({
					...options,
					headers: {}, token: undefined,
					fetchImpl: credentials.fetch(selectedAgent.source, connectionSignal),
					url: selectedAgent.connectionUrl,
					agentId: selectedAgent.connectionAgentId
				});
				if (closed || connectionSignal.aborted) {
					return;
				}

				const owner = credentials.owner(selectedAgent.source);
				const scope = `${selectedEnvironment}:${selectedAgent.agentKey}`;
				const environment = chatSettingsRef.current.environments[selectedEnvironment];
				let stored = environment.agents[selectedAgent.agentKey];
				let historyAgentKey = selectedAgent.agentKey;
				const registry = selectedAgent.registry;
				if (registry && !stored?.activeContextId) {
					const legacy = eligibleLegacyRegistryAgent({ agents: environment.agents,
						sourceKey: selectedAgent.sourceKey, identityId: selectedAgent.agentId!,
						workspaceId: registry.workspace.id, homeWorkspaceId: registry.account.homeWorkspaceId,
						credentialScope: owner });
					if (legacy?.activeContextId) {
						historyAgentKey = legacy.agentKey;
						stored = { ...stored, ...selectedAgent, activeContextId: legacy.activeContextId, credentialScope: owner };
						persistEnvironmentSettings(selectedEnvironment, { agents: {
							...environment.agents, [selectedAgent.agentKey]: stored
						} });
					}
				}
				const sameOwner = !!owner && owner === credentialScopeRef.current &&
					contextScopeRef.current === scope;
				if (!sameOwner) clearTranscript();
				setCurrentContextId(
					sameOwner && contextScopeRef.current === scope && contextIdRef.current
						? contextIdRef.current
						: owner ? createdContextsRef.current.get(JSON.stringify([scope, owner]))
							?? (stored?.credentialScope === owner ? stored.activeContextId : undefined) : undefined
				);
				if (registry && !sameOwner && contextIdRef.current) {
					const restored = loadContextSession(selectedEnvironment, historyAgentKey, contextIdRef.current);
					if (restored.session) {
						if (historyAgentKey !== selectedAgent.agentKey) {
							const warning = copyContextSession(restored.session, selectedAgent.agentKey);
							if (warning) appendNotification(warning);
						}
						setEntries(restoredTranscriptEntries(restored.session.messages));
						for (const message of restored.session.messages) markShownMessage(shownMessageKeysRef.current, message);
					} else if (restored.warning) appendNotification(restored.warning);
				}
				credentialScopeRef.current = owner;
				setClientState(connected);
				connectedAgentKeyRef.current = selectedAgent.agentKey;
				if (!contextIdRef.current) startNewContext(connected);
				const connectionNoticeKey = `${selectedAgent.agentKey}:${connected.agentCard.name}:${connected.endpoints.rpcUrl}`;
				if (lastConnectionNoticeRef.current !== connectionNoticeKey) {
					lastConnectionNoticeRef.current = connectionNoticeKey;
					appendSystem(
						`Connected to **${connected.agentCard.name}** via ${connected.endpoints.rpcUrl}`
					);
				}
			} catch (error) {
				if (closed || connectionSignal.aborted) {
					return;
				}
				appendSystem(
					`Connection failed: ${error instanceof Error ? error.message : String(error)}`
				);
			} finally {
				workspaceConnection?.finish();
			}
		};

		void connect();

		return () => {
			closed = true;
			connectionLifetime.abort();
			if (selectedAgent) credentials.cancelSource(selectedAgent.source);
			if (closePush) {
				void closePush();
			}
		};
	}, [
		credentials,
		options.pushNotifications,
		options.pushReceiver,
		reconnectNonce,
		workspaceConnectionNonce,
		// Discovery refreshes replace records without changing the connection.
		selectedAgent?.agentKey,
		selectedAgent?.connectionUrl,
		selectedAgent?.connectionAgentId,
		selectedAgent?.source.sourceKey,
		selectedAgent?.source.url,
		selectedAgent?.source.type,
		selectedAgent && isTransientAgentSource(selectedAgent.source),
		selectedEnvironment
	]);

	const replaceLastResponseStreamSection = (
		message: Message,
		fallbackTaskId?: string
	): boolean => {
		if (!shouldRenderLiveResponseMessage(message)) {
			return false;
		}

		const streamTaskId = getMessageTaskId(message, fallbackTaskId);
		if (!streamTaskId) {
			return false;
		}

		const activeSection = getActiveStreamTranscriptSection(
			streamTranscriptStateRef.current,
			streamTaskId,
			STREAM_DELTA_ARTIFACT_ID
		);
		if (activeSection?.kind !== "response") {
			return false;
		}

		if (shownMessageKeysRef.current.has(getShownMessageKey(message, fallbackTaskId))) {
			return false;
		}

		const body = formatMessageParts(message.parts);
		if (!body) {
			return false;
		}

		const shownMessageKey = markShownMessage(
			shownMessageKeysRef.current,
			message,
			fallbackTaskId
		);
		lastCopyableResponseRef.current = { kind: "message", content: body };
		chatSessionLogger.debug("chat.agent_message.stream_replaced", {
			fallbackTaskId,
			streamTaskId,
			streamEntryId: activeSection.entryId,
			body,
			message: summarizeMessageForLog(message)
		});
		setEntries((current) => {
			lastCopyableResponseRef.current = { kind: "message", content: body };
			return replaceTranscriptEntryBody(
				current,
				activeSection.entryId,
				"agent",
				body
			).entries;
		});
		clearActiveStreamTranscriptSection(
			streamTranscriptStateRef.current,
			streamTaskId,
			STREAM_DELTA_ARTIFACT_ID
		);
		return true;
	};

	const renderAgentResponseBubble = (
		message: Message,
		fallbackTaskId?: string
	): boolean => {
		if (!shouldRenderLiveResponseMessage(message)) {
			return false;
		}
		if (shownMessageKeysRef.current.has(getShownMessageKey(message, fallbackTaskId))) {
			return false;
		}

		const body = formatMessageParts(message.parts);
		if (!body) {
			return false;
		}

		const shownMessageKey = markShownMessage(
			shownMessageKeysRef.current,
			message,
			fallbackTaskId
		);
		lastCopyableResponseRef.current = { kind: "message", content: body };
		chatSessionLogger.debug("chat.agent_message.rendered", {
			fallbackTaskId,
			body,
			message: summarizeMessageForLog(message)
		});
		setEntries((current) =>
			upsertTranscriptEntry(current, `message:${shownMessageKey}`, "agent", body)
		);
		return true;
	};

	const handleMessage = (message: Message, protocolPayload: unknown = message): boolean => {
		if (message.contextId) {
			setCurrentContextId(message.contextId);
		}
		setTaskId(undefined);

		if (responseMode === "a2a-protocol") {
			appendProtocol(protocolPayload);
			return true;
		}

		return replaceLastResponseStreamSection(message) || renderAgentResponseBubble(message);
	};

	const handleTaskSnapshot = (task: Task, protocolPayload: unknown = task): boolean => {
		setCurrentContextId(task.contextId);
		const isTerminalTask = isTerminalTaskState(task.status?.state);
		setTaskId(isTaskContinuationState(task.status?.state) ? task.id : undefined);

		if (responseMode === "a2a-protocol") {
			appendProtocol(protocolPayload);
			return true;
		}

		if (!isTerminalTask) {
			return false;
		}

		const messages = getUnshownTaskAgentMessages(task, shownMessageKeysRef.current);
		let renderedAgentOutput = false;
		for (const message of messages) {
			renderedAgentOutput =
				replaceLastResponseStreamSection(message, task.id) ||
				renderAgentResponseBubble(message, task.id) ||
				renderedAgentOutput;
		}
		finalizeOpenStreamEntries(task.id);
		return renderedAgentOutput;
	};

	const handleStatusUpdate = (
		event: TaskStatusUpdateEvent,
		protocolPayload: unknown = event
	): boolean => {
		setCurrentContextId(event.contextId);
		setTaskId(isTaskContinuationState(event.status?.state) ? event.taskId : undefined);
		setStreamLabel(taskStateLabel(event.status?.state));

		if (responseMode === "a2a-protocol") {
			appendProtocol(protocolPayload);
			return true;
		}

		let renderedAgentOutput = false;
		if (
			event.status?.message &&
			(isTerminalTaskState(event.status.state) || isFinalStatusEvent(event)) &&
			replaceLastResponseStreamSection(event.status.message, event.taskId)
		) {
			renderedAgentOutput = true;
		}

		const activeStreamSection = getLastStreamTranscriptSection(
			streamTranscriptStateRef.current,
			event.taskId
		);
		if (
			!renderedAgentOutput &&
			shouldRenderLiveStatusMessage({
				message: event.status?.message,
				taskId: event.taskId,
				streamedTaskIds: streamedTaskIdsRef.current,
				activeStreamSectionKind: activeStreamSection?.kind
			})
		) {
			renderedAgentOutput = event.status?.message
				? renderAgentResponseBubble(event.status.message, event.taskId)
				: false;
		}
		if (isTerminalTaskState(event.status?.state) || isFinalStatusEvent(event)) {
			finalizeOpenStreamEntries(event.taskId);
		}
		return renderedAgentOutput;
	};

	const handleArtifactUpdate = (
		event: TaskArtifactUpdateEvent,
		protocolPayload: unknown = event
	): boolean => {
		setCurrentContextId(event.contextId);
		setTaskId(undefined);

		const artifact = event.artifact;
		if (!artifact) {
			return false;
		}

		const streamArtifactKind = getStreamArtifactKind(artifact.artifactId);
		if (streamArtifactKind === "response") {
			setStreamLabel("Streaming");
		} else if (streamArtifactKind === "thinking") {
			setStreamLabel("Thinking");
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(protocolPayload);
			return true;
		}

		if (!streamArtifactKind) {
			return false;
		}

		const artifactText = formatMessageParts(artifact.parts);
		if (!artifactText) {
			return false;
		}

		if (streamArtifactKind === "response") {
			streamedTaskIdsRef.current.add(event.taskId);
		}

		const replaceCurrentSection = shouldReplaceCurrentStreamSection(
			event,
			streamArtifactKind
		);
		const preparedDelta = prepareStreamTranscriptDelta({
			state: streamTranscriptStateRef.current,
			taskId: event.taskId,
			artifactId: artifact.artifactId,
			kind: streamArtifactKind,
			body: artifactText,
			append: event.append === true,
			replaceCurrentSection
		});
		chatSessionLogger.debug("chat.stream_delta.prepared", {
			taskId: event.taskId,
			contextId: event.contextId,
			artifactId: artifact.artifactId,
			kind: streamArtifactKind,
			append: event.append,
			replaceCurrentSection,
			entryId: preparedDelta.section.entryId,
			sectionIndex: preparedDelta.section.sectionIndex,
			appendToExistingSection: preparedDelta.appendToExistingSection,
			replaceExistingSection: preparedDelta.replaceExistingSection,
			insertDivider: preparedDelta.insertDivider
		});

		setEntries((current) => {
			const result = applyPreparedStreamTranscriptDelta({
				entries: current,
				prepared: preparedDelta
			});
			if (streamArtifactKind === "response") {
				lastCopyableResponseRef.current = {
					kind: "message",
					content: result.body
				};
			}
			return result.entries;
		});
		return true;
	};

	const applySelectedFileSuggestion = (): void => {
		const suggestion = fileSuggestions[selectedFileSuggestionIndex];
		if (!suggestion) return;
		replaceDraft((current) => applyFileSuggestion(current, suggestion));
		setSelectedFileSuggestionIndex(0);
	};

	const persistCompletedExchange = (
		nextContextId: string | undefined,
		messages: Message[],
		nextTaskId?: string
	): void => {
		if (!selectedAgentKey || !nextContextId || messages.length === 0) {
			return;
		}

		const sessionFilePath = resolveSessionFilePath(
			selectedEnvironment,
			selectedAgentKey,
			nextContextId
		);
		const warning = saveCompletedExchange({
			environment: selectedEnvironment,
			agentKey: selectedAgentKey,
			contextId: nextContextId,
			chatSessionId: chatSessionLogger.chatSessionId,
			chatSessionLogPath: chatSessionLogger.logFilePath,
			...(nextTaskId ? { lastTaskId: nextTaskId } : {}),
			messages
		});
		if (warning) {
			chatSessionLogger.warn("conversation.session.save_failed", {
				environmentId: selectedEnvironment,
				agentKey: selectedAgentKey,
				contextId: nextContextId,
				lastTaskId: nextTaskId,
				warning
			});
			appendSystem(warning);
		} else {
			chatSessionLogger.debug("conversation.session.saved", {
				environmentId: selectedEnvironment,
				agentKey: selectedAgentKey,
				contextId: nextContextId,
				lastTaskId: nextTaskId,
				sessionFilePath,
				chatSessionId: chatSessionLogger.chatSessionId,
				chatSessionLogPath: chatSessionLogger.logFilePath
			});
		}

		const currentAgent =
			chatSettingsRef.current.environments[selectedEnvironment].agents[selectedAgentKey] ??
			selectedAgent;
		const shouldPersistAgentContext =
			!selectedAgent ||
			selectedAgent.agentKey !== selectedAgentKey ||
			!isTransientAgentSource(selectedAgent.source);
		if (currentAgent && shouldPersistAgentContext) {
			persistEnvironmentSettings(selectedEnvironment, {
				agents: {
					...chatSettingsRef.current.environments[selectedEnvironment].agents,
					[selectedAgentKey]: {
						agentKey: currentAgent.agentKey,
						agentId: currentAgent.agentId,
						sourceKey: currentAgent.sourceKey,
						agentCardUrl: currentAgent.agentCardUrl,
						agentCardName: currentAgent.agentCardName,
						agentHandle: currentAgent.agentHandle,
						lastSeenAt: currentAgent.lastSeenAt,
						lastLoadedAt: currentAgent.lastLoadedAt,
						status: currentAgent.status,
						activeContextId: nextContextId,
						credentialScope: credentialScopeRef.current
					}
				}
			});
		}
	};

	const applySelectedAgentSuggestion = (): void => {
		if (operationGate.kind === "workspace") { appendNotification(operationGate.reason!); return; }
		const suggestion = agentSuggestions[selectedSuggestionIndex];
		if (!suggestion) {
			return;
		}

		const agent = discoveredAgents.find(
			(item) => item.agentKey === suggestion.agentKey
		);
		const selectedId = agent?.id ?? suggestion.id;
		const selectedKey = agent?.agentKey ?? suggestion.agentKey;

		replaceDraft((current) => clearAgentMention(current));
		if (selectedAgentId !== selectedId || selectedAgentKey !== selectedKey) {
			setSelectedAgentId(selectedId);
			setSelectedAgentKey(selectedKey);
			if (agent) persistAgentSelection(agent);
		}
	};

	const dismissSlashDialog = (): void => {
		setSlashSubmenuId(undefined);
		setSelectedSlashIndex(0);
		setSelectedSlashSubmenuIndex(0);
		replaceDraft((current) => clearLeadingSlashDraft(current));
	};

	const resetSlashSelection = (): void => {
		setSlashSubmenuId(undefined);
		setSelectedSlashIndex(0);
		setSelectedSlashSubmenuIndex(0);
		replaceDraft("");
	};

	const openSelectedSlashCommand = (): void => {
		const command = slashCommands[selectedSlashIndex];
		if (!command) {
			return;
		}

		if (command.id === "clear") {
			resetSlashSelection();
			runClearSlashCommand();
			return;
		}
		if (command.id === "copy") {
			resetSlashSelection();
			void runCopySlashCommand();
			return;
		}
		if (command.id === "login") {
			resetSlashSelection();
			void runLoginSlashCommand();
			return;
		}
		if (command.id === "workspace") {
			resetSlashSelection();
			void runWorkspaceSlashCommand();
			return;
		}
		if (command.id === "session") {
			resetSlashSelection();
			appendSystem("Use /session retry <source-key> or /session new <source-key>. " + GUEST_HISTORY_WARNING);
			return;
		}

		if (command.id === "exit") {
			exit();
			return;
		}
		if (command.id === "sources") {
			resetSlashSelection();
			void runSourcesSlashCommand();
			return;
		}

		setSlashSubmenuId(command.id as SlashCommandId);
		setSelectedSlashSubmenuIndex(0);
		replaceDraft("");
	};

	const startWorkspaceOperation = (): { operation: ChatOperation; finish(): void } | undefined => {
		const operation = operationGate.begin("workspace");
		if (!operation) { appendNotification(operationGate.reason!); return undefined; }
		const finish = (): void => {
			clearTimeout(timer);
			if (workspaceConnectionRef.current?.operation === operation) workspaceConnectionRef.current = undefined;
			operation.finish();
		};
		const timer = setTimeout(() => {
			operation.cancel();
			appendNotification("Workspace selection timed out. Please try /workspace again.");
		}, 30_000);
		operation.signal.addEventListener("abort", finish, { once: true });
		return { operation, finish };
	};

	const runWorkspaceSlashCommand = async (selector?: string): Promise<void> => {
		if (agentEndpointUrl) { appendNotification("Workspace selection is unavailable with --url. Restart Chat without --url to browse the registry."); return; }
		const transition = startWorkspaceOperation();
		if (!transition) return;
		const { operation, finish } = transition;
		try {
			if (selector !== undefined) {
				await refreshAgentDiscovery({ workspaceSelector: selector, workspaceChange: { operation, finish } });
			} else {
				const signal = AbortSignal.any([operation.signal, credentials.signal]);
				const token = await getStoredAccessToken(selectedEnvironment, { controlPlaneOrigin });
				if (signal.aborted) return;
				if (!token) { appendNotification("Use /login before choosing a workspace."); return; }
				const account = await fetchRegistryAccount({ environmentId: selectedEnvironment, accessToken: token,
					graphQLUrl: getGraphQLHttpUrl(selectedEnvironment, controlPlaneOrigin),
					fetchImpl: credentials.fetch(createDefaultRegistryAgentSource(selectedEnvironment, controlPlaneOrigin), signal),
					logger: chatSessionLogger });
				if (signal.aborted) return;
				setWorkspaceAccount(account);
				setSelectedSlashSubmenuIndex(0);
				setSlashSubmenuId("workspace");
				if (!account.workspaces.length) appendNotification("No accessible workspaces were returned for this account.");
			}
		} catch (error) {
			if (!operation.signal.aborted) appendNotification(`Workspace lookup failed: ${error instanceof Error ? error.message : String(error)}`);
		} finally {
			if (workspaceConnectionRef.current?.operation !== operation) finish();
		}
	};

	const runLoginSlashCommand = async (): Promise<void> => {
		appendSystem(`Starting Aion login for ${selectedEnvironment}.`);
		chatSessionLogger.info("auth.login.started", {
			environmentId: selectedEnvironment
		});
		const loginSignal = credentials.signal;
		try {
			const session = await loginWithWorkOS(selectedEnvironment, {
				onDeviceAuthorization: async (prompt) => {
					const url = prompt.verificationUriComplete ?? prompt.verificationUri;
					if (await openUrlInDefaultBrowser(url)) {
						appendSystem(
							`Opening login screen in default browser.\n\nCode: ${prompt.userCode}`
						);
						return;
					}

					appendSystem(
						`Open this URL to continue login:\n${url}\n\nCode: ${prompt.userCode}`
					);
				},
				onSlowDown: (intervalSeconds) => {
					appendStatus(`Login polling slowed to every ${intervalSeconds}s.`);
				}
			}, { controlPlaneOrigin });
			if (loginSignal.aborted) return;
			const bootstrap = await runLoginBootstrap({
				environmentId: selectedEnvironment,
				accessToken: session.accessToken,
				graphQLUrl: getGraphQLHttpUrl(selectedEnvironment, controlPlaneOrigin),
				logger: chatSessionLogger
			});
			if (loginSignal.aborted) return;
			const postAuthPath = resolvePostAuthPath(bootstrap);
			if (postAuthPath) {
				const postAuthUrl = buildWebAppRouteUrl(selectedEnvironment, postAuthPath);
				if (await openUrlInDefaultBrowser(postAuthUrl)) {
					appendSystem("Opening Aion app in default browser.");
				} else {
					appendSystem(`Open this URL to continue:\n${postAuthUrl}`);
				}
			}
			appendSystem(`Logged in to Aion ${selectedEnvironment}.`);
			operationGate.cancel();
			discoveryLifetimeRef.current.abort();
			setRegistrySelection(undefined);
			setWorkspaceAccount(undefined);
			credentials.cancelPending();
			welcomeLifetimeRef.current.abort();
			welcomeLifetimeRef.current = new AbortController();
			setDiscoveredAgents([]);
			setClientState(undefined);
			clearTranscript();
			chatSessionLogger.info("auth.login.completed", {
				environmentId: selectedEnvironment,
				postAuthPath
			});
			setReconnectNonce((current) => current + 1);
		} catch (error) {
			chatSessionLogger.warn("auth.login.failed", {
				environmentId: selectedEnvironment,
				error
			});
			appendSystem(
				`Login failed: ${error instanceof Error ? error.message : String(error)}`
			);
		}
	};

	const runEnvironmentSlashCommand = (
		environmentId: AionEnvironmentId | undefined,
		origin?: string
	): void => {
		if (!environmentId) {
			appendSystem(
				`Current environment: ${selectedEnvironment} (${controlPlaneApiBaseUrl})\nAvailable environments: ${AION_ENVIRONMENT_IDS.join(", ")}`
			);
			return;
		}

		const effectiveOrigin = getControlPlaneApiBaseUrl(environmentId,
			origin ?? chatSettings.environments[environmentId].controlPlaneOrigin);
		if (environmentId === selectedEnvironment && effectiveOrigin === controlPlaneApiBaseUrl) {
			appendSystem(`Current environment: ${selectedEnvironment} (${effectiveOrigin})`);
			return;
		}

		reloadEnvironmentState(environmentId, origin);
		appendSystem(`Aion environment set to ${environmentId} (${effectiveOrigin}).`);
	};

	const activateNewContext = (nextContextId: string): void => {
		setCurrentContextId(nextContextId);
		const key = selectedAgent?.agentKey ?? selectedAgentKey;
		if (key && credentialScopeRef.current) {
			createdContextsRef.current.set(
				JSON.stringify([`${selectedEnvironment}:${key}`, credentialScopeRef.current]), nextContextId
			);
		}
		const existing = key ? chatSettingsRef.current.environments[selectedEnvironment].agents[key] : undefined;
		if (key && existing) {
			persistEnvironmentSettings(selectedEnvironment, {
				agents: {
					...chatSettingsRef.current.environments[selectedEnvironment].agents,
					[key]: { ...existing, activeContextId: nextContextId, credentialScope: credentialScopeRef.current }
				}
			});
		}
	};

	/** Allocate and retain the context before its single welcome attempt. */
	const startNewContext = (connected?: Awaited<ReturnType<typeof connectClient>>): void => {
		const selectedContextAgentKey = selectedAgent?.agentKey ?? selectedAgentKey;
		if (selectedContextAgentKey) {
			const scope = `${selectedEnvironment}:${selectedContextAgentKey}`;
			createChatThread({
				signal: welcomeLifetimeRef.current.signal,
				connected,
				onCreated: activateNewContext,
				onWelcome: (request, response) => {
					const originalContext = request.message!.contextId;
					const messages = isMessage(response)
						? [request.message!, response]
						: [request.message!, ...getTaskMessages(response)];
					const warning = saveCompletedExchange({
						environment: selectedEnvironment,
						agentKey: selectedContextAgentKey,
						contextId: originalContext,
						chatSessionId: chatSessionLogger.chatSessionId,
						chatSessionLogPath: chatSessionLogger.logFilePath,
						messages
					});
					if (warning) chatSessionLogger.warn("chat.welcome.save_failed", { warning });
					if (currentThreadScopeRef.current !== scope
						|| contextIdRef.current !== originalContext) return;
					// Rendering does not mutate the foreground task or busy state.
					if (responseMode === "a2a-protocol") appendProtocol(response);
					else for (const message of messages) {
						renderAgentResponseBubble(message, isMessage(response) ? undefined : response.id);
					}
				},
				onError: (error, originalContext) => {
					chatSessionLogger.warn("chat.welcome.failed", { contextId: originalContext, error });
					if (currentThreadScopeRef.current === scope
						&& contextIdRef.current === originalContext) {
						appendSystem(`Welcome failed: ${error instanceof Error ? error.message : String(error)}`);
					}
				}
			});
		}
	};

	const runClearSlashCommand = (): void => {
		if (operationGate.kind === "workspace") { appendNotification(operationGate.reason!); return; }
		const selectedContextAgentKey = selectedAgent?.agentKey ?? selectedAgentKey;
		clearTranscript();
		startNewContext(connectedAgentKeyRef.current === selectedContextAgentKey ? clientState : undefined);
		const terminalClearRequested = requestTerminalClear({
			isTTY: stdout.isTTY,
			terminalType: process.env.TERM,
			write: writeToStdout
		});
		chatSessionLogger.info("chat.clear", {
			environmentId: selectedEnvironment,
			agentKey: selectedContextAgentKey,
			terminalClearRequested
		});
	};

	const runCopySlashCommand = async (): Promise<void> => {
		const response = lastCopyableResponseRef.current;
		if (!response) {
			appendSystem("No agent response to copy.");
			return;
		}

		try {
			await writeClipboard(response.content);
			appendStatus(
				response.kind === "protocol"
					? "Copied last A2A response JSON to clipboard."
					: "Copied last response to clipboard."
			);
		} catch (error) {
			appendSystem(
				`Copy failed: ${error instanceof Error ? error.message : String(error)}`
			);
		}
	};

	const runSourcesSlashCommand = async (): Promise<void> => {
		if (operationGate.kind === "workspace") { appendNotification(operationGate.reason!); return; }
		const refreshedSources = await refreshAgentDiscovery({
			announceExplicitSourceErrors: false
		});
		const sourceRecords = refreshedSources ?? Object.values(agentSources);
		const sources = [...sourceRecords].sort((left, right) =>
			left.sourceKey.localeCompare(right.sourceKey)
		);
		if (sources.length === 0) {
			appendSystem("No agent sources configured.");
			return;
		}

		appendSystem(formatAgentSourcesList(sources));
	};

	const runExactSlashCommand = (): boolean => {
		const command = parseExactSlashCommand(draft);
		if (!command) {
			return false;
		}

		resetSlashSelection();
		if (command.kind === "invalid") { appendSystem(command.message); return true; }
		if (command.kind === "workspace") { void runWorkspaceSlashCommand(command.selector); return true; }
		if (operationGate.kind === "workspace" && ["session", "clear"].includes(command.kind)) { appendNotification(operationGate.reason!); return true; }
		if (command.kind === "session") {
			const source = command.sourceKey ? agentSources[command.sourceKey] : selectedAgent?.source;
			if (!source) {
				appendSystem("Choose a source with /session new <source-key> or /session retry <source-key>. Use /sources to list keys.");
				return true;
			}
			if (command.action === "new") {
				appendSystem(GUEST_HISTORY_WARNING);
				setClientState(undefined);
			}
			void (command.action === "new" ? credentials.startNew(source) : credentials.retry(source))
				.then(() => { if (!credentials.signal.aborted) setReconnectNonce((value) => value + 1); })
				.catch((error: unknown) => { if (!credentials.signal.aborted) appendSystem(error instanceof Error ? error.message : "Guest session unavailable."); });
			return true;
		}
		if (command.kind === "login") {
			void runLoginSlashCommand();
			return true;
		}
		if (command.kind === "copy") {
			void runCopySlashCommand();
			return true;
		}
		if (command.kind === "clear") {
			runClearSlashCommand();
			return true;
		}

		runEnvironmentSlashCommand(command.environmentId, command.controlPlaneOrigin);
		return true;
	};

	const applySlashSubmenuSelection = (optionIndex = selectedSlashSubmenuIndex): void => {
		const command = getSlashCommandById(slashSubmenuId);
		const option = slashSubmenu?.options[optionIndex];
		if (!command || !option) {
			return;
		}

		if (command.id === "workspace") {
			resetSlashSelection();
			void runWorkspaceSlashCommand(option.value);
			return;
		} else if (command.id === "request") {
			applyRequestMode(option.value as RequestMode);
		} else if (command.id === "response") {
			applyResponseMode(option.value as ResponseMode);
		}

		resetSlashSelection();
	};

	const submitPrompt = async (): Promise<void> => {
		const selection = parseAgentSelection(draft, discoveredAgents.map((agent) => agent.id));
		if (operationGate.kind === "reply" && selection.agentId && !selection.message.trim()) operationGate.cancel();
		const operation = operationGate.begin("reply");
		if (!operation) { appendNotification(operationGate.reason!); return; }
		try { await submitPromptImpl(operation.signal); }
		catch (error) {
			if (!operation.signal.aborted) appendStatus(`Request preparation failed: ${error instanceof Error ? error.message : String(error)}`);
		} finally { operation.finish(); }
	};

	const submitPromptImpl = async (operationSignal: AbortSignal): Promise<void> => {
		const selection = parseAgentSelection(
			draft,
			discoveredAgents.map((agent) => agent.id)
	);
		if (selection.agentId && selection.agentId !== selectedAgentId) {
			const agent = discoveredAgents.find((item) => item.id === selection.agentId);
			setSelectedAgentId(selection.agentId);
			setSelectedAgentKey(agent?.agentKey);
			if (agent) persistAgentSelection(agent);
			appendStatus(`Active agent changed to @${selection.agentId}`);
			replaceDraft(selection.message);
			return;
		}

		const trimmed = selection.message.trim();
		if (!trimmed) {
			replaceDraft("");
			return;
		}
		if (!clientState) {
			appendStatus("No agent connection is active yet.");
			return;
		}

		replaceDraft("");

		const requestSignal = AbortSignal.any([
			credentials.signal, connectionSignalRef.current ?? credentials.signal,
			foregroundLifetimeRef.current.signal, operationSignal
		]);
		const parts = await buildMessageParts(trimmed);
		if (requestSignal.aborted) return;
		const attachedFiles = parts
			.filter((part) => part.content?.$case === "raw" || part.content?.$case === "url")
			.map((part) => part.filename || "unnamed");
		const displayBody =
			attachedFiles.length > 0
				? `${trimmed}\n\n*Attached: ${attachedFiles.join(", ")}*`
				: trimmed;

		setEntries((current) => [
			...current,
			{
				id: randomUUID(),
				role: "user",
				body: displayBody,
				isFinalized: true
			}
		]);

		const requestContextId = contextIdRef.current ?? randomUUID();
		if (!contextIdRef.current) activateNewContext(requestContextId);
		const params = buildMessageParams(parts, requestContextId, taskId, pushConfig);
		const outboundMessage = params.message;
		if (!outboundMessage) {
			appendStatus("Unable to build outbound A2A message.");
			return;
		}
		chatSessionLogger.debug("chat.user_message.submitted", {
			body: displayBody,
			attachedFiles,
			selectedAgent: summarizeAgentForLog(selectedAgent),
			message: summarizeMessageForLog(outboundMessage)
		});
		const canStream = Boolean(clientState.agentCard.capabilities?.streaming);
		const useStreaming = requestMode === "streaming-message" && canStream;
		let requestStartedAt: number | undefined;

		try {
			requestStartedAt = Date.now();
			setWorkingStartedAt(requestStartedAt);
			chatSessionLogger.debug("a2a.request.started", {
				requestMode,
				responseMode,
				useStreaming,
				canStream,
				selectedAgent: summarizeAgentForLog(selectedAgent),
				message: summarizeMessageForLog(outboundMessage),
				hasPushNotificationConfig: Boolean(pushConfig)
			});

			if (requestMode === "streaming-message" && !canStream) {
				chatSessionLogger.debug("a2a.request.streaming_fallback", {
					selectedAgent: summarizeAgentForLog(selectedAgent)
				});
				appendSystem("Request mode fallback: agent does not support streaming, using Send message.");
			}

			if (useStreaming) {
				setStreamLabel("Streaming");
				let completedTask: Task | undefined;
				const completedMessages: Message[] = [];
				let finalStatusUpdate: TaskStatusUpdateEvent | undefined;
				let reachedTerminal = false;
				let renderedAgentOutput = false;
				for await (const streamResponse of clientState.client.sendMessageStream(params, { signal: requestSignal })) {
					if (requestSignal.aborted) return;
					const event = unwrapStreamResponse(streamResponse);
					if (!event) {
						continue;
					}
					chatSessionLogger.debug("a2a.stream.event", {
						event: summarizeProtocolEventForLog(event)
					});
					if (isMessage(event)) {
						if (event.role === Role.ROLE_AGENT && shouldRenderLiveResponseMessage(event)) completedMessages.push(event);
						renderedAgentOutput =
							handleMessage(event, streamResponse) || renderedAgentOutput;
					} else if (isTask(event)) {
						if (isTerminalTaskState(event.status?.state)) {
							completedTask = event;
							reachedTerminal = true;
						}
						renderedAgentOutput =
							handleTaskSnapshot(event, streamResponse) || renderedAgentOutput;
					} else if (isTaskStatusUpdateEvent(event)) {
						if (isTerminalTaskState(event.status?.state)) {
							reachedTerminal = true;
						}
						if (isFinalStatusEvent(event)) {
							finalStatusUpdate = event;
						}
						renderedAgentOutput =
							handleStatusUpdate(event, streamResponse) || renderedAgentOutput;
					} else {
						renderedAgentOutput =
							handleArtifactUpdate(event, streamResponse) || renderedAgentOutput;
					}
				}
				if (requestSignal.aborted) return;
				if (
					shouldShowNoAgentMessageNotice({
						responseMode,
						reachedTerminal,
						renderedAgentOutput
					})
				) {
					chatSessionLogger.debug("a2a.no_agent_message", {
						responseMode,
						reachedTerminal
					});
					appendSystem(NO_AGENT_MESSAGE_NOTICE);
				}
				if (completedTask) {
					persistCompletedExchange(
						completedTask.contextId,
						[outboundMessage, ...getTaskMessages(completedTask)],
						completedTask.id
					);
				} else if (finalStatusUpdate?.status?.message) {
					persistCompletedExchange(
						finalStatusUpdate.contextId,
						[outboundMessage, finalStatusUpdate.status.message],
						finalStatusUpdate.taskId
					);
				} else if (completedMessages.length) {
					persistCompletedExchange(completedMessages.at(-1)?.contextId || requestContextId, [outboundMessage, ...completedMessages]);
				}
				chatSessionLogger.debug("a2a.request.completed", {
					transport: "stream",
					durationMs: Date.now() - requestStartedAt,
					reachedTerminal,
					renderedAgentOutput,
					completedTaskId: completedTask?.id,
					finalStatusTaskId: finalStatusUpdate?.taskId
				});
				setStreamLabel("Idle");
			} else {
				setStreamLabel("Waiting");
				const response = await clientState.client.sendMessage(params, { signal: requestSignal });
				if (requestSignal.aborted) return;
				chatSessionLogger.debug("a2a.response.received", {
					durationMs: Date.now() - requestStartedAt,
					response: summarizeProtocolEventForLog(response)
				});
				let reachedTerminal = isMessage(response);
				let renderedAgentOutput = false;
				if (isMessage(response)) {
					renderedAgentOutput = handleMessage(response);
					persistCompletedExchange(
						response.contextId || outboundMessage.contextId,
						[outboundMessage, response],
						response.taskId
					);
				} else {
					reachedTerminal = isTerminalTaskState(response.status?.state);
					renderedAgentOutput = handleTaskSnapshot(response);
					if (reachedTerminal) {
						persistCompletedExchange(
							response.contextId,
							[outboundMessage, ...getTaskMessages(response)],
							response.id
						);
					}
				}
				if (
					shouldShowNoAgentMessageNotice({
						responseMode,
						reachedTerminal,
						renderedAgentOutput
					})
				) {
					chatSessionLogger.debug("a2a.no_agent_message", {
						responseMode,
						reachedTerminal
					});
					appendSystem(NO_AGENT_MESSAGE_NOTICE);
				}
				chatSessionLogger.debug("a2a.request.completed", {
					transport: "send",
					durationMs: Date.now() - requestStartedAt,
					reachedTerminal,
					renderedAgentOutput,
					responseKind: isMessage(response) ? "message" : "task"
				});
				setStreamLabel("Idle");
			}
		} catch (error) {
			if (requestSignal.aborted) return;
			setStreamLabel("Error");
			chatSessionLogger.warn("a2a.request.failed", {
				durationMs: requestStartedAt ? Date.now() - requestStartedAt : undefined,
				selectedAgent: summarizeAgentForLog(selectedAgent),
				error
			});
			appendStatus(`Request failed: ${error instanceof Error ? error.message : String(error)}`);
		} finally {
			if (useStreaming && !requestSignal.aborted) {
				finalizeOpenStreamEntries();
			}
			if (!requestSignal.aborted) setWorkingStartedAt(undefined);
		}
	};

	useInput((input, key) => {
		const isSlashSubmenuOpen = Boolean(slashSubmenuId);
		const isSlashMenuOpen = slashQuery !== undefined && !slashSubmenuId;
		const composerContentWidth = getComposerContentWidth(
			stdout?.columns ?? process.stdout.columns ?? 80
		);

		if (key.ctrl && input === "c") {
			if (draft.length > 0) {
				replaceDraft("");
				setSlashSubmenuId(undefined);
				return;
			}

			exit();
			return;
		}

		if (isSlashSubmenuOpen && key.upArrow) {
			const optionsCount = slashSubmenu?.options.length ?? 0;
			if (optionsCount > 0) {
				setSelectedSlashSubmenuIndex((current) =>
					current === 0 ? optionsCount - 1 : current - 1
				);
			}
			return;
		}

		if (isSlashSubmenuOpen && key.downArrow) {
			const optionsCount = slashSubmenu?.options.length ?? 0;
			if (optionsCount > 0) {
				setSelectedSlashSubmenuIndex((current) =>
					current === optionsCount - 1 ? 0 : current + 1
				);
			}
			return;
		}

		if (isSlashMenuOpen && key.upArrow) {
			if (slashCommands.length > 0) {
				setSelectedSlashIndex((current) =>
					current === 0 ? slashCommands.length - 1 : current - 1
				);
			}
			return;
		}

		if (isSlashMenuOpen && key.downArrow) {
			if (slashCommands.length > 0) {
				setSelectedSlashIndex((current) =>
					current === slashCommands.length - 1 ? 0 : current + 1
				);
			}
			return;
		}

		if (agentSuggestions.length > 0 && key.upArrow) {
			setSelectedSuggestionIndex((current) =>
				current === 0 ? agentSuggestions.length - 1 : current - 1
			);
			return;
		}

		if (agentSuggestions.length > 0 && key.downArrow) {
			setSelectedSuggestionIndex((current) =>
				current === agentSuggestions.length - 1 ? 0 : current + 1
			);
			return;
		}

		if (fileSuggestions.length > 0 && key.upArrow) {
			setSelectedFileSuggestionIndex((current) =>
				current === 0 ? fileSuggestions.length - 1 : current - 1
			);
			return;
		}

		if (fileSuggestions.length > 0 && key.downArrow) {
			setSelectedFileSuggestionIndex((current) =>
				current === fileSuggestions.length - 1 ? 0 : current + 1
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.leftArrow) {
			setComposerInput((current) =>
				moveComposerCursorHorizontally(current, -1)
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.rightArrow) {
			setComposerInput((current) =>
				moveComposerCursorHorizontally(current, 1)
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.upArrow) {
			setComposerInput((current) =>
				moveComposerCursorVertically(current, -1, composerContentWidth)
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.downArrow) {
			setComposerInput((current) =>
				moveComposerCursorVertically(current, 1, composerContentWidth)
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.home) {
			setComposerInput((current) =>
				moveComposerCursorToRowBoundary(current, "start", composerContentWidth)
			);
			return;
		}

		if (!isSlashSubmenuOpen && key.end) {
			setComposerInput((current) =>
				moveComposerCursorToRowBoundary(current, "end", composerContentWidth)
			);
			return;
		}

		if (key.escape) {
			if (isSlashSubmenuOpen || isSlashMenuOpen) {
				dismissSlashDialog();
				return;
			}

			if (fileSuggestions.length > 0) {
				replaceDraft((current) => clearFileMention(current));
				return;
			}

			replaceDraft("");
			return;
		}

		if (isSlashSubmenuOpen && input >= "1" && input <= "9") {
			const optionIndex = Number.parseInt(input, 10) - 1;
			const optionsCount = slashSubmenu?.options.length ?? 0;
			if (optionIndex >= 0 && optionIndex < optionsCount) {
				applySlashSubmenuSelection(optionIndex);
			}
			return;
		}

		if (isComposerBackwardDeleteKey(key)) {
			if (isSlashSubmenuOpen) {
				return;
			}

			setComposerInput(deleteComposerTextBackward);
			return;
		}

		if (key.return) {
			if (isSlashSubmenuOpen) {
				applySlashSubmenuSelection();
				return;
			}

			if (isSlashMenuOpen) {
				if (runExactSlashCommand()) {
					return;
				}
				openSelectedSlashCommand();
				return;
			}

			if (key.shift) {
				setComposerInput((current) => insertComposerText(current, "\n"));
				return;
			}

			if (agentSuggestions.length > 0) {
				applySelectedAgentSuggestion();
				return;
			}

			if (fileSuggestions.length > 0) {
				applySelectedFileSuggestion();
				return;
			}

			void submitPrompt();
			return;
		}

		if (isSlashSubmenuOpen) {
			return;
		}

		if (!key.ctrl && !key.meta && input) {
			setComposerInput((current) => insertComposerText(current, input));
		}
	});

	return (
		<Box flexDirection="column" height="100%">
			<Box flexDirection="column" flexGrow={1}>
				<ChatSession
					key={transcriptGeneration}
					entries={entries}
					discoveredCount={discoveredAgents.length}
					sourceCount={Object.keys(agentSources).length}
					selectedAgentId={selectedAgentId}
					requestMode={requestMode}
					responseMode={responseMode}
				/>
			</Box>
			<SystemNotificationStack notifications={notifications} />
			{workingStartedAt ? (
				<Box marginBottom={1}>
					<WorkingIndicator startedAt={workingStartedAt} />
				</Box>
			) : null}
			<Box><Text dimColor>{selectedEnvironment} · Workspace: {registrySelection?.workspace.name ?? (agentEndpointUrl ? "direct endpoint" : "not selected")} · /workspace</Text></Box>
			<ChatComposer
				draft={draft}
				cursorOffset={draftCursor}
				activeAgentId={selectedAgentId}
				discoveredCount={discoveredAgents.length}
				pushState={pushLabel}
				streamState={streamLabel}
				agentSuggestions={agentSuggestions}
				selectedSuggestionIndex={selectedSuggestionIndex}
				fileSuggestions={fileSuggestions.map((s) => s.label)}
				selectedFileSuggestionIndex={selectedFileSuggestionIndex}
				slashCommands={slashCommands.map((command) => ({
					label: command.label,
					description: command.description
				}))}
				selectedSlashCommandIndex={selectedSlashIndex}
				slashMenuVisible={slashQuery !== undefined}
				slashSubmenu={slashSubmenu}
			/>
		</Box>
	);
}
