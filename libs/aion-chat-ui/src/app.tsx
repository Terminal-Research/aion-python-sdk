import { randomUUID } from "node:crypto";

import type {
	Message,
	Part,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, useApp, useInput } from "ink";

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
	getFileMentionMatch,
	getFileSuggestions,
	type FileSuggestion
} from "./lib/input";
import { loadChatModeSettings, saveChatModeSettings } from "./lib/chatSettings.js";
import {
	buildAuthenticatedFetch,
	buildMessageParams,
	connectClient,
	createPushNotificationConfig
} from "./lib/connection.js";
import {
	type AionEnvironmentId,
	AION_ENVIRONMENT_IDS,
	getControlPlaneApiBaseUrl,
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
	toPersistedAgents
} from "./lib/agents/discovery.js";
import {
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	createExplicitAgentSource,
	isTransientAgentSource,
	mergeAgentSources,
	normalizeSourceUrl
} from "./lib/agents/model.js";
import {
	resolveSessionFilePath,
	saveCompletedExchange
} from "./lib/agents/sessionStore.js";
import { createChatSessionLogger } from "./lib/sessionLogger.js";
import {
	formatProtocolPayload,
	formatProtocolPayloadAsJson
} from "./lib/protocolOutput.js";
import {
	EPHEMERAL_MESSAGE_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID
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
import { isTerminalTaskState } from "./lib/taskState.js";
import { getStoredAccessToken, loginWithWorkOS } from "./lib/workosAuth.js";

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

function summarizeMessageForLog(message: Message): Record<string, unknown> {
	return {
		kind: "message",
		messageId: message.messageId,
		role: message.role,
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
	return {
		kind: "artifact-update",
		taskId: event.taskId,
		contextId: event.contextId,
		artifactId: event.artifact?.artifactId,
		append: event.append,
		partCount: event.artifact?.parts.length ?? 0,
		parts: event.artifact?.parts.map(summarizePartForLog) ?? []
	};
}

function isAionControlPlaneRegistryAgent(
	agent: DiscoveredAgentRecord,
	environmentId: AionEnvironmentId
): boolean {
	return (
		agent.source.type === "registry" &&
		normalizeSourceUrl(agent.source.url) ===
			normalizeSourceUrl(getControlPlaneApiBaseUrl(environmentId))
	);
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

function upsertEntry(
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

function isFinalStatusEvent(event: TaskStatusUpdateEvent): boolean {
	return Boolean((event as TaskStatusUpdateEvent & { final?: boolean }).final);
}

interface CopyableResponse {
	kind: "message" | "protocol";
	content: string;
}

type ExactSlashCommand =
	| { kind: "login" }
	| { kind: "copy" }
	| { kind: "environment"; environmentId?: AionEnvironmentId };

function parseExactSlashCommand(value: string): ExactSlashCommand | undefined {
	const [command, environmentId, extra] = value.trim().split(/\s+/);
	if (command === "/login" && !environmentId) {
		return { kind: "login" };
	}
	if (command === "/copy" && !environmentId) {
		return { kind: "copy" };
	}
	if ((command === "/environment" || command === "/env") && !extra) {
		if (!environmentId) {
			return { kind: "environment" };
		}
		if (isAionEnvironmentId(environmentId)) {
			return { kind: "environment", environmentId };
		}
	}
	return undefined;
}

export function ChatApp({ options }: { options: ChatCliOptions }): React.JSX.Element {
	const { exit } = useApp();
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
	const [selectedEnvironment, setSelectedEnvironment] = useState<AionEnvironmentId>(
		initialSettingsResult.settings.selectedEnvironment
	);
	const activeEnvironmentSettings =
		chatSettings.environments[selectedEnvironment];
	const controlPlaneApiBaseUrl = getControlPlaneApiBaseUrl(selectedEnvironment);
	const agentEndpointUrl = options.url;
	const [pushLabel, setPushLabel] = useState(
		options.pushNotifications ? "Starting..." : "Disabled"
	);
	const [streamLabel, setStreamLabel] = useState("Idle");
	const [draft, setDraft] = useState("");
	const [entries, setEntries] = useState<TranscriptEntry[]>([]);
	const [notifications, setNotifications] = useState<TranscriptEntry[]>([]);
	const [contextId, setContextId] = useState<string>();
	const [taskId, setTaskId] = useState<string>();
	const [workingStartedAt, setWorkingStartedAt] = useState<number>();
	const [clientState, setClientState] = useState<Awaited<ReturnType<typeof connectClient>>>();
	const [pushConfig, setPushConfig] = useState<
		ReturnType<typeof createPushNotificationConfig> | undefined
	>();
	const [discoveredAgents, setDiscoveredAgents] = useState<DiscoveredAgentRecord[]>([]);
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
	const shownMessageKeysRef = useRef<Set<string>>(new Set());
	const streamedTaskIdsRef = useRef<Set<string>>(new Set());
	const lastCopyableResponseRef = useRef<CopyableResponse | undefined>(undefined);
	const lastConnectionNoticeRef = useRef<string | undefined>(undefined);
	const notificationTimersRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);

	const appendEntry = (role: TranscriptEntry["role"], body: string): void => {
		setEntries((current) => [
			...current,
			{
				id: randomUUID(),
				role,
				body
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
				body
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

	useEffect(() => {
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
		const current = chatSettings.environments[environmentId];
		persistSettings({
			...chatSettings,
			environments: {
				...chatSettings.environments,
				[environmentId]: {
					...current,
					...update
				}
			}
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
		setEntries([]);
		shownMessageKeysRef.current.clear();
		streamedTaskIdsRef.current.clear();
		lastCopyableResponseRef.current = undefined;
		setContextId(undefined);
		setTaskId(undefined);
		setStreamLabel("Idle");
		setWorkingStartedAt(undefined);
	};

	const reloadEnvironmentState = (environmentId: AionEnvironmentId): void => {
		const nextSettings: ChatSettings = {
			...chatSettings,
			selectedEnvironment: environmentId
		};
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

	const slashQuery = useMemo(() => {
		if (slashSubmenuId) {
			return undefined;
		}

		return getLeadingSlashQuery(draft);
	}, [draft, slashSubmenuId]);

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
			options: command.options,
			selectedIndex: selectedSlashSubmenuIndex
		};
	}, [selectedSlashSubmenuIndex, slashSubmenuId]);

	const agentSuggestions = useMemo<AgentSuggestionView[]>(() => {
		if (slashQuery !== undefined || slashSubmenuId) {
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
	}, [discoveredAgents, draft, slashQuery, slashSubmenuId]);

	const fileSuggestions = useMemo((): FileSuggestion[] => {
		if (slashQuery !== undefined || slashSubmenuId) {
			return [];
		}

		const match = getFileMentionMatch(draft);
		if (!match) return [];

		return getFileSuggestions(match.query);
	}, [draft, slashQuery, slashSubmenuId]);

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
		const optionCount = getSlashCommandById(slashSubmenuId)?.options.length ?? 0;
		setSelectedSlashSubmenuIndex((current) => {
			if (optionCount === 0) {
				return 0;
			}
			return Math.min(current, optionCount - 1);
		});
	}, [slashSubmenuId]);

	const refreshAgentDiscovery = async ({
		announceExplicitSourceErrors = true,
		isClosed = () => false
	}: {
		announceExplicitSourceErrors?: boolean;
		isClosed?: () => boolean;
	} = {}): Promise<AgentSourceRecord[] | undefined> => {
		try {
			const runtimeSources = mergeAgentSources(
				activeEnvironmentSettings.agentSources,
				selectedEnvironment,
				agentEndpointUrl
			);
			const explicitSourceKey = agentEndpointUrl
				? createExplicitAgentSource(agentEndpointUrl).sourceKey
				: undefined;
			const explicitSourceFetch = buildAuthenticatedFetch({
				headers: options.headers,
				token: options.token
			});
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
					controlPlaneAccessTokenProvider: (request) =>
						getStoredAccessToken(selectedEnvironment, {
							forceRefresh: request?.forceRefresh
						}),
					graphQLFetchImpl: fetch,
					sourceFetchImpl: (source) =>
						source.sourceKey === explicitSourceKey ? explicitSourceFetch : fetch,
					logger: chatSessionLogger
				}
			);
			chatSessionLogger.debug("agent.discovery.refresh.completed", {
				environmentId: selectedEnvironment,
				sourceCount: discovery.sources.length,
				agentCount: discovery.agents.length,
				errorCount: discovery.errors.length
			});
			if (isClosed()) {
				return undefined;
			}

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
				activeEnvironmentSettings.agents
			);
			setAgentSources(nextSources);
			setDiscoveredAgents(discovery.agents);
			persistEnvironmentSettings(selectedEnvironment, {
				agents: nextAgents,
				agentSources: persistentSources
			});

			if (announceExplicitSourceErrors) {
				for (const error of discovery.errors) {
					const shouldNotify =
						error.source.type === "registry" || !error.source.isDefault;
					if (shouldNotify && error.error) {
						appendNotification(
							`${error.source.description}: ${error.error}`
						);
					}
				}
			}

			const nextSelected = selectDiscoveredAgent(discovery.agents, {
				requestedAgentId: options.agentId,
				selectedAgentKey,
				selectedAgentId: options.agentId ? undefined : selectedAgentId,
				explicitSourceKey,
				autoSelectExplicit: Boolean(agentEndpointUrl)
			});

			if (nextSelected) {
				const shouldPersistSelection = !isTransientAgentSource(nextSelected.source);
				setSelectedAgentKey(nextSelected.agentKey);
				setSelectedAgentId(nextSelected.id);
				persistEnvironmentSettings(selectedEnvironment, {
					...(shouldPersistSelection
						? {
								selectedAgentKey: nextSelected.agentKey,
								selectedAgentId: nextSelected.id
							}
						: {}),
					agents: nextAgents,
					agentSources: persistentSources
				});
			} else if (selectedAgentKey || (!options.agentId && selectedAgentId)) {
				setSelectedAgentKey(undefined);
				setSelectedAgentId(undefined);
				persistEnvironmentSettings(selectedEnvironment, {
					selectedAgentKey: undefined,
					selectedAgentId: undefined,
					agents: nextAgents,
					agentSources: persistentSources
				});
				appendSystem("The selected agent is no longer available.");
			}

			return discovery.sources;
		} catch (error) {
			if (isClosed()) {
				return undefined;
			}
			setDiscoveredAgents([]);
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
		void refreshAgentDiscovery({ isClosed: () => closed });
		return () => {
			closed = true;
		};
	}, [
		agentEndpointUrl,
		controlPlaneApiBaseUrl,
		options.agentId,
		options.headers,
		options.token,
		reconnectNonce,
		selectedEnvironment
	]);

	useEffect(() => {
		let closed = false;
		let closePush: (() => Promise<void>) | undefined;

		const handlePushEvent = (event: PushNotificationEvent): void => {
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
					if (closed) {
						await server.close();
						return;
					}
					closePush = server.close;
					setPushConfig(createPushNotificationConfig(options.pushReceiver));
					setPushLabel(server.callbackUrl);
				}

				setClientState(undefined);
				setContextId(
					activeEnvironmentSettings.agents[selectedAgent.agentKey]?.activeContextId
				);
				setTaskId(undefined);

				const useCliEndpointAuth = isTransientAgentSource(selectedAgent.source);
				const useAionRegistryAuth = isAionControlPlaneRegistryAgent(
					selectedAgent,
					selectedEnvironment
				);
				const connected = await connectClient({
					...options,
					headers: useCliEndpointAuth ? options.headers : {},
					token: useCliEndpointAuth ? options.token : undefined,
					tokenProvider: useAionRegistryAuth
						? () => getStoredAccessToken(selectedEnvironment)
						: undefined,
					url: selectedAgent.connectionUrl,
					agentId: selectedAgent.connectionAgentId
				});
				if (closed) {
					return;
				}

				setClientState(connected);
				const connectionNoticeKey = `${selectedAgent.agentKey}:${connected.agentCard.name}:${connected.endpoints.rpcUrl}`;
				if (lastConnectionNoticeRef.current !== connectionNoticeKey) {
					lastConnectionNoticeRef.current = connectionNoticeKey;
					appendSystem(
						`Connected to **${connected.agentCard.name}** via ${connected.endpoints.rpcUrl}`
					);
				}
			} catch (error) {
				if (closed) {
					return;
				}
				appendSystem(
					`Connection failed: ${error instanceof Error ? error.message : String(error)}`
				);
			}
		};

		void connect();

		return () => {
			closed = true;
			if (closePush) {
				void closePush();
			}
		};
	}, [
		agentEndpointUrl,
		controlPlaneApiBaseUrl,
		discoveredAgents.length,
		options,
		reconnectNonce,
		selectedAgent,
		selectedEnvironment
	]);

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
			upsertEntry(current, `message:${shownMessageKey}`, "agent", body)
		);
		return true;
	};

	const handleMessage = (message: Message): boolean => {
		if (message.contextId) {
			setContextId(message.contextId);
		}
		if (message.taskId) {
			setTaskId(message.taskId);
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(message);
			return true;
		}

		return renderAgentResponseBubble(message);
	};

	const handleTaskSnapshot = (task: Task): boolean => {
		setContextId(task.contextId);
		const isTerminalTask = isTerminalTaskState(task.status?.state);
		setTaskId(isTerminalTask ? undefined : task.id);

		if (responseMode === "a2a-protocol") {
			appendProtocol(task);
			return true;
		}

		if (!isTerminalTask) {
			return false;
		}
		if (streamedTaskIdsRef.current.has(task.id)) {
			return false;
		}

		const messages = getUnshownTaskAgentMessages(task, shownMessageKeysRef.current);
		let renderedAgentOutput = false;
		for (const message of messages) {
			renderedAgentOutput = renderAgentResponseBubble(message, task.id) || renderedAgentOutput;
		}
		return renderedAgentOutput;
	};

	const handleStatusUpdate = (event: TaskStatusUpdateEvent): boolean => {
		setContextId(event.contextId);
		setTaskId(isTerminalTaskState(event.status?.state) ? undefined : event.taskId);
		setStreamLabel(taskStateLabel(event.status?.state));

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return true;
		}

		if (
			shouldRenderLiveStatusMessage({
				message: event.status?.message,
				taskId: event.taskId,
				streamedTaskIds: streamedTaskIdsRef.current
			})
		) {
			return event.status?.message ? renderAgentResponseBubble(event.status.message, event.taskId) : false;
		}
		return false;
	};

	const handleArtifactUpdate = (event: TaskArtifactUpdateEvent): boolean => {
		setContextId(event.contextId);
		setTaskId(event.taskId);

		const artifact = event.artifact;
		if (!artifact) {
			return false;
		}

		if (artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
			setStreamLabel("Streaming");
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return true;
		}

		if (artifact.artifactId !== STREAM_DELTA_ARTIFACT_ID) {
			return false;
		}

		const artifactText = formatMessageParts(artifact.parts);
		if (!artifactText) {
			return false;
		}

		streamedTaskIdsRef.current.add(event.taskId);
		const entryId = `artifact:${event.taskId}:${artifact.artifactId}`;

		setEntries((current) => {
			const existing = current.find((item) => item.id === entryId);
			const nextBody =
				event.append && existing ? `${existing.body}${artifactText}` : artifactText;
			lastCopyableResponseRef.current = { kind: "message", content: nextBody };
			return upsertEntry(current, entryId, "agent", nextBody);
		});
		return true;
	};

	const applySelectedFileSuggestion = (): void => {
		const suggestion = fileSuggestions[selectedFileSuggestionIndex];
		if (!suggestion) return;
		setDraft((current) => applyFileSuggestion(current, suggestion));
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
			chatSettings.environments[selectedEnvironment].agents[selectedAgentKey] ??
			selectedAgent;
		const shouldPersistAgentContext =
			!selectedAgent ||
			selectedAgent.agentKey !== selectedAgentKey ||
			!isTransientAgentSource(selectedAgent.source);
		if (currentAgent && shouldPersistAgentContext) {
			persistEnvironmentSettings(selectedEnvironment, {
				agents: {
					...chatSettings.environments[selectedEnvironment].agents,
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
						activeContextId: nextContextId
					}
				}
			});
		}
	};

	const applySelectedAgentSuggestion = (): void => {
		const suggestion = agentSuggestions[selectedSuggestionIndex];
		if (!suggestion) {
			return;
		}

		const agent = discoveredAgents.find(
			(item) => item.agentKey === suggestion.agentKey
		);
		const selectedId = agent?.id ?? suggestion.id;
		const selectedKey = agent?.agentKey ?? suggestion.agentKey;

		setDraft((current) => clearAgentMention(current));
		if (selectedAgentId !== selectedId || selectedAgentKey !== selectedKey) {
			setSelectedAgentId(selectedId);
			setSelectedAgentKey(selectedKey);
			if (!agent || !isTransientAgentSource(agent.source)) {
				persistEnvironmentSettings(selectedEnvironment, {
					selectedAgentId: selectedId,
					selectedAgentKey: selectedKey
				});
			}
		}
	};

	const dismissSlashDialog = (): void => {
		setSlashSubmenuId(undefined);
		setSelectedSlashIndex(0);
		setSelectedSlashSubmenuIndex(0);
		setDraft((current) => clearLeadingSlashDraft(current));
	};

	const resetSlashSelection = (): void => {
		setSlashSubmenuId(undefined);
		setSelectedSlashIndex(0);
		setSelectedSlashSubmenuIndex(0);
		setDraft("");
	};

	const openSelectedSlashCommand = (): void => {
		const command = slashCommands[selectedSlashIndex];
		if (!command) {
			return;
		}

		if (command.id === "clear") {
			clearTranscript();
			resetSlashSelection();
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
		setDraft("");
	};

	const runLoginSlashCommand = async (): Promise<void> => {
		appendSystem(`Starting Aion login for ${selectedEnvironment}.`);
		chatSessionLogger.info("auth.login.started", {
			environmentId: selectedEnvironment
		});
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
			});
			const bootstrap = await runLoginBootstrap({
				environmentId: selectedEnvironment,
				accessToken: session.accessToken,
				logger: chatSessionLogger
			});
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
		environmentId: AionEnvironmentId | undefined
	): void => {
		if (!environmentId) {
			appendSystem(
				`Current environment: ${selectedEnvironment}\nAvailable environments: ${AION_ENVIRONMENT_IDS.join(", ")}`
			);
			return;
		}

		if (environmentId === selectedEnvironment) {
			appendSystem(`Current environment: ${selectedEnvironment}`);
			return;
		}

		reloadEnvironmentState(environmentId);
		appendSystem(`Aion environment set to ${environmentId}.`);
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
		if (command.kind === "login") {
			void runLoginSlashCommand();
			return true;
		}
		if (command.kind === "copy") {
			void runCopySlashCommand();
			return true;
		}

		runEnvironmentSlashCommand(command.environmentId);
		return true;
	};

	const applySlashSubmenuSelection = (optionIndex = selectedSlashSubmenuIndex): void => {
		const command = getSlashCommandById(slashSubmenuId);
		const option = command?.options[optionIndex];
		if (!command || !option) {
			return;
		}

		if (command.id === "request") {
			applyRequestMode(option.value as RequestMode);
		} else if (command.id === "response") {
			applyResponseMode(option.value as ResponseMode);
		}

		resetSlashSelection();
	};

	const submitPrompt = async (): Promise<void> => {
		const selection = parseAgentSelection(
			draft,
			discoveredAgents.map((agent) => agent.id)
	);
		if (selection.agentId && selection.agentId !== selectedAgentId) {
			const agent = discoveredAgents.find((item) => item.id === selection.agentId);
			setSelectedAgentId(selection.agentId);
			setSelectedAgentKey(agent?.agentKey);
			if (!agent || !isTransientAgentSource(agent.source)) {
				persistEnvironmentSettings(selectedEnvironment, {
					selectedAgentId: selection.agentId,
					selectedAgentKey: agent?.agentKey
				});
			}
			appendStatus(`Active agent changed to @${selection.agentId}`);
		}

		const trimmed = selection.message.trim();
		if (!trimmed) {
			setDraft("");
			return;
		}
		if (!clientState) {
			appendStatus("No agent connection is active yet.");
			return;
		}

		setDraft("");

		const parts = await buildMessageParts(trimmed);
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
				body: displayBody
			}
		]);

		const params = buildMessageParams(parts, contextId, taskId, pushConfig);
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
				let finalStatusUpdate: TaskStatusUpdateEvent | undefined;
				let reachedTerminal = false;
				let renderedAgentOutput = false;
				for await (const streamResponse of clientState.client.sendMessageStream(params)) {
					const event = unwrapStreamResponse(streamResponse);
					if (!event) {
						continue;
					}
					chatSessionLogger.debug("a2a.stream.event", {
						event: summarizeProtocolEventForLog(event)
					});
					if (isMessage(event)) {
						renderedAgentOutput = handleMessage(event) || renderedAgentOutput;
					} else if (isTask(event)) {
						if (isTerminalTaskState(event.status?.state)) {
							completedTask = event;
							reachedTerminal = true;
						}
						renderedAgentOutput = handleTaskSnapshot(event) || renderedAgentOutput;
					} else if (isTaskStatusUpdateEvent(event)) {
						if (isTerminalTaskState(event.status?.state)) {
							reachedTerminal = true;
						}
						if (isFinalStatusEvent(event)) {
							finalStatusUpdate = event;
						}
						renderedAgentOutput = handleStatusUpdate(event) || renderedAgentOutput;
					} else {
						renderedAgentOutput = handleArtifactUpdate(event) || renderedAgentOutput;
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
				if (completedTask) {
					persistCompletedExchange(
						completedTask.contextId,
						getTaskMessages(completedTask),
						completedTask.id
					);
				} else if (finalStatusUpdate?.status?.message) {
					persistCompletedExchange(
						finalStatusUpdate.contextId,
						[outboundMessage, finalStatusUpdate.status.message],
						finalStatusUpdate.taskId
					);
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
				const response = await clientState.client.sendMessage(params);
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
							getTaskMessages(response),
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
			setStreamLabel("Error");
			chatSessionLogger.warn("a2a.request.failed", {
				durationMs: requestStartedAt ? Date.now() - requestStartedAt : undefined,
				selectedAgent: summarizeAgentForLog(selectedAgent),
				error
			});
			appendStatus(`Request failed: ${error instanceof Error ? error.message : String(error)}`);
		} finally {
			setWorkingStartedAt(undefined);
		}
	};

	useInput((input, key) => {
		const isSlashSubmenuOpen = Boolean(slashSubmenuId);
		const isSlashMenuOpen = slashQuery !== undefined && !slashSubmenuId;

		if (key.ctrl && input === "c") {
			if (draft.length > 0) {
				setDraft("");
				setSlashSubmenuId(undefined);
				return;
			}

			exit();
			return;
		}

		if (isSlashSubmenuOpen && key.upArrow) {
			const optionsCount = getSlashCommandById(slashSubmenuId)?.options.length ?? 0;
			if (optionsCount > 0) {
				setSelectedSlashSubmenuIndex((current) =>
					current === 0 ? optionsCount - 1 : current - 1
				);
			}
			return;
		}

		if (isSlashSubmenuOpen && key.downArrow) {
			const optionsCount = getSlashCommandById(slashSubmenuId)?.options.length ?? 0;
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

		if (key.escape) {
			if (isSlashSubmenuOpen || isSlashMenuOpen) {
				dismissSlashDialog();
				return;
			}

			if (fileSuggestions.length > 0) {
				setDraft((current) => clearFileMention(current));
				return;
			}

			setDraft("");
			return;
		}

		if (isSlashSubmenuOpen && input >= "1" && input <= "9") {
			const optionIndex = Number.parseInt(input, 10) - 1;
			const optionsCount = getSlashCommandById(slashSubmenuId)?.options.length ?? 0;
			if (optionIndex >= 0 && optionIndex < optionsCount) {
				applySlashSubmenuSelection(optionIndex);
			}
			return;
		}

		if (key.backspace || key.delete) {
			if (isSlashSubmenuOpen) {
				return;
			}

			setDraft((current) => current.slice(0, -1));
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
				setDraft((current) => `${current}\n`);
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
			setDraft((current) => `${current}${input}`);
		}
	});

	return (
		<Box flexDirection="column" height="100%">
			<Box flexDirection="column" flexGrow={1} marginY={1}>
				<ChatSession
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
			<ChatComposer
				draft={draft}
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
