import { randomUUID } from "node:crypto";

import type {
	FilePart,
	Message,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import React, { useEffect, useMemo, useRef, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";

import type { ChatCliOptions } from "./args.js";
import { ChatComposer } from "./components/ChatComposer.js";
import { ChatSession } from "./components/ChatSession.js";
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
import { CONNECTION_THEME } from "./lib/theme.js";
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
	discoverAgentSources,
	selectDiscoveredAgent,
	toPersistedAgents
} from "./lib/agents/discovery.js";
import {
	type AgentSourceRecord,
	type DiscoveredAgentRecord,
	createExplicitAgentSource,
	isTransientAgentSource,
	mergeAgentSources
} from "./lib/agents/model.js";
import { saveCompletedExchange } from "./lib/agents/sessionStore.js";
import { formatProtocolPayload } from "./lib/protocolOutput.js";
import {
	EPHEMERAL_MESSAGE_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID
} from "./lib/a2aMetadata.js";
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
	getLeadingSlashQuery,
	getRequestModeLabel,
	getResponseModeLabel,
	getSlashCommandById,
	type RequestMode,
	type ResponseMode,
	type SlashCommandId
} from "./lib/slashCommands.js";
import { isTerminalTaskState } from "./lib/taskState.js";
import { loginWithWorkOS } from "./lib/workosAuth.js";

type ConnectionState = "connecting" | "connected" | "error";
const NO_AGENT_MESSAGE_NOTICE = "Task completed with no agent message.";

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

type ExactSlashCommand =
	| { kind: "login" }
	| { kind: "environment"; environmentId?: AionEnvironmentId };

function parseExactSlashCommand(value: string): ExactSlashCommand | undefined {
	const [command, environmentId, extra] = value.trim().split(/\s+/);
	if (command === "/login" && !environmentId) {
		return { kind: "login" };
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
	const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
	const [connectionLabel, setConnectionLabel] = useState("Discovering agents...");
	const [pushLabel, setPushLabel] = useState(
		options.pushNotifications ? "Starting..." : "Disabled"
	);
	const [streamLabel, setStreamLabel] = useState("Idle");
	const [agentName, setAgentName] = useState("Unknown Agent");
	const [draft, setDraft] = useState("");
	const [entries, setEntries] = useState<TranscriptEntry[]>([]);
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
	const lastConnectionNoticeRef = useRef<string | undefined>(undefined);

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

	const appendStatus = (body: string): void => {
		appendEntry("status", body);
	};

	const appendProtocol = (payload: unknown): void => {
		appendEntry("protocol", formatProtocolPayload(payload));
	};

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
		setAgentName("Unknown Agent");
		setConnectionLabel("Discovering agents...");
		setConnectionState("connecting");
		setReconnectNonce((current) => current + 1);
	};

	useEffect(() => {
		if (initialSettingsResult.warning) {
			appendSystem(initialSettingsResult.warning);
		}
	}, [initialSettingsResult.warning]);

	const connectionSummary = useMemo(() => {
		if (!clientState) {
			return connectionLabel;
		}

		return `${clientState.endpoints.rpcUrl}`;
	}, [clientState, connectionLabel]);
	const connectionColor = CONNECTION_THEME[connectionState];

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

	const agentSuggestions = useMemo(() => {
		if (slashQuery !== undefined || slashSubmenuId) {
			return [];
		}

		const mentionMatch = getAgentMentionMatch(draft);
		if (!mentionMatch) {
			return [];
		}

		return discoveredAgents
			.map((agent) => agent.id)
			.filter((agentId) => agentId.startsWith(mentionMatch.query))
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

	useEffect(() => {
		let closed = false;

		const discover = async (): Promise<void> => {
			try {
				const runtimeSources = mergeAgentSources(
					activeEnvironmentSettings.agentSources,
					agentEndpointUrl
				);
				const explicitSourceKey = agentEndpointUrl
					? createExplicitAgentSource(agentEndpointUrl).sourceKey
					: undefined;
				const discovery = await discoverAgentSources(
					runtimeSources,
					buildAuthenticatedFetch({
						headers: options.headers,
						token: options.token
					})
				);
				if (closed) {
					return;
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

				for (const error of discovery.errors) {
					if (!error.source.isDefault && error.error) {
						appendSystem(
							`No agents were found from the provided URL: ${error.source.url}\n${error.error}`
						);
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
					setConnectionState("connecting");
					setConnectionLabel(`Connecting to @${nextSelected.id}...`);
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
				} else {
					setConnectionState("connecting");
					setConnectionLabel(
						discovery.agents.length > 0
							? "Choose an agent with @"
							: `Using ${selectedEnvironment} control plane at ${controlPlaneApiBaseUrl}`
					);
				}
			} catch (error) {
				if (closed) {
					return;
				}
				setDiscoveredAgents([]);
				setConnectionState("connecting");
				setConnectionLabel(
					`Using ${selectedEnvironment} control plane at ${controlPlaneApiBaseUrl}`
				);
				const message = error instanceof Error ? error.message : String(error);
				if (agentEndpointUrl) {
					appendSystem(`No agents were found from the provided URL: ${agentEndpointUrl}\n${message}`);
				}
			}
		};

		void discover();

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
				setConnectionState("connecting");
				setConnectionLabel(
					discoveredAgents.length > 0
						? "Choose an agent with @"
						: `Using ${selectedEnvironment} control plane at ${controlPlaneApiBaseUrl}`
				);
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
				setConnectionState("connecting");
				setConnectionLabel(`Connecting to @${selectedAgent.id}...`);

				const connected = await connectClient({
					...options,
					url: selectedAgent.connectionUrl,
					agentId: selectedAgent.connectionAgentId
				});
				if (closed) {
					return;
				}

				setClientState(connected);
				setAgentName(connected.agentCard.name);
				setConnectionState("connected");
				setConnectionLabel("Connected");
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
				setConnectionState("error");
				setConnectionLabel(error instanceof Error ? error.message : String(error));
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
		const isTerminalTask = isTerminalTaskState(task.status.state);
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
		setTaskId(isTerminalTaskState(event.status.state) ? undefined : event.taskId);
		setStreamLabel(event.status.state);

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return true;
		}

		if (
			shouldRenderLiveStatusMessage({
				message: event.status.message as Message | undefined,
				taskId: event.taskId,
				streamedTaskIds: streamedTaskIdsRef.current
			})
		) {
			return renderAgentResponseBubble(event.status.message as Message, event.taskId);
		}
		return false;
	};

	const handleArtifactUpdate = (event: TaskArtifactUpdateEvent): boolean => {
		setContextId(event.contextId);
		setTaskId(event.taskId);

		if (event.artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
			setStreamLabel("Streaming");
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return true;
		}

		if (event.artifact.artifactId !== STREAM_DELTA_ARTIFACT_ID) {
			return false;
		}

		const artifactText = formatMessageParts(event.artifact.parts);
		if (!artifactText) {
			return false;
		}

		streamedTaskIdsRef.current.add(event.taskId);
		const entryId = `artifact:${event.taskId}:${event.artifact.artifactId}`;

		setEntries((current) => {
			const existing = current.find((item) => item.id === entryId);
			const nextBody =
				event.append && existing ? `${existing.body}${artifactText}` : artifactText;
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

		const warning = saveCompletedExchange({
			environment: selectedEnvironment,
			agentKey: selectedAgentKey,
			contextId: nextContextId,
			...(nextTaskId ? { lastTaskId: nextTaskId } : {}),
			messages
		});
		if (warning) {
			appendSystem(warning);
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

		setDraft((current) => clearAgentMention(current));
		if (selectedAgentId !== suggestion) {
			const agent = discoveredAgents.find((item) => item.id === suggestion);
			setSelectedAgentId(suggestion);
			setSelectedAgentKey(agent?.agentKey);
			if (!agent || !isTransientAgentSource(agent.source)) {
				persistEnvironmentSettings(selectedEnvironment, {
					selectedAgentId: suggestion,
					selectedAgentKey: agent?.agentKey
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
			runSourcesSlashCommand();
			return;
		}

		setSlashSubmenuId(command.id as SlashCommandId);
		setSelectedSlashSubmenuIndex(0);
		setDraft("");
	};

	const runLoginSlashCommand = async (): Promise<void> => {
		appendSystem(`Starting Aion login for ${selectedEnvironment}.`);
		try {
			await loginWithWorkOS(selectedEnvironment, {
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
			appendSystem(`Logged in to Aion ${selectedEnvironment}.`);
			setReconnectNonce((current) => current + 1);
		} catch (error) {
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

	const runSourcesSlashCommand = (): void => {
		const sources = Object.values(agentSources).sort((left, right) =>
			left.sourceKey.localeCompare(right.sourceKey)
		);
		if (sources.length === 0) {
			appendSystem("No agent sources configured.");
			return;
		}

		appendSystem(
			[
				"Agent sources",
				"",
				...sources.map((source) =>
					[
						source.sourceKey,
						`Type: ${source.type}`,
						`Description: ${source.description}`,
						`URL: ${source.url}`,
						`Status: ${source.status ?? "unchecked"}`,
						source.lastError && !source.isDefault ? `Last error: ${source.lastError}` : undefined
					]
						.filter(Boolean)
						.join("\n")
				)
			].join("\n\n")
		);
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
			.filter((p): p is FilePart => p.kind === "file")
			.map((p) => p.file.name ?? "unnamed");
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
		const canStream = Boolean(clientState.agentCard.capabilities.streaming);
		const useStreaming = requestMode === "streaming-message" && canStream;

		try {
			setWorkingStartedAt(Date.now());

			if (requestMode === "streaming-message" && !canStream) {
				appendSystem("Request mode fallback: agent does not support streaming, using Send message.");
			}

			if (useStreaming) {
				setStreamLabel("Streaming");
				let completedTask: Task | undefined;
				let finalStatusUpdate: TaskStatusUpdateEvent | undefined;
				let reachedTerminal = false;
				let renderedAgentOutput = false;
				for await (const event of clientState.client.sendMessageStream(params)) {
					switch (event.kind) {
						case "message":
							renderedAgentOutput = handleMessage(event) || renderedAgentOutput;
							break;
						case "task":
							if (isTerminalTaskState(event.status.state)) {
								completedTask = event;
								reachedTerminal = true;
							}
							renderedAgentOutput = handleTaskSnapshot(event) || renderedAgentOutput;
							break;
						case "status-update":
							if (isTerminalTaskState(event.status.state)) {
								reachedTerminal = true;
							}
							if (isFinalStatusEvent(event)) {
								finalStatusUpdate = event;
							}
							renderedAgentOutput = handleStatusUpdate(event) || renderedAgentOutput;
							break;
						case "artifact-update":
							renderedAgentOutput = handleArtifactUpdate(event) || renderedAgentOutput;
							break;
						default:
							break;
					}
				}
				if (
					shouldShowNoAgentMessageNotice({
						responseMode,
						reachedTerminal,
						renderedAgentOutput
					})
				) {
					appendSystem(NO_AGENT_MESSAGE_NOTICE);
				}
				if (completedTask) {
					persistCompletedExchange(
						completedTask.contextId,
						getTaskMessages(completedTask),
						completedTask.id
					);
				} else if (finalStatusUpdate?.status.message) {
					persistCompletedExchange(
						finalStatusUpdate.contextId,
						[params.message, finalStatusUpdate.status.message as Message],
						finalStatusUpdate.taskId
					);
				}
				setStreamLabel("Idle");
			} else {
				setStreamLabel("Waiting");
				const response = await clientState.client.sendMessage(params);
				let reachedTerminal = response.kind === "message";
				let renderedAgentOutput = false;
				if (response.kind === "message") {
					renderedAgentOutput = handleMessage(response);
					persistCompletedExchange(
						response.contextId ?? params.message.contextId,
						[params.message, response],
						response.taskId
					);
				} else {
					reachedTerminal = isTerminalTaskState(response.status.state);
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
					appendSystem(NO_AGENT_MESSAGE_NOTICE);
				}
				setStreamLabel("Idle");
			}
		} catch (error) {
			setStreamLabel("Error");
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
			<Box
				borderStyle="round"
				borderColor={connectionColor}
				paddingX={1}
			>
				<Text color={connectionColor}>
					{selectedAgentId ? `@${selectedAgentId}` : agentName}
				</Text>
				<Text> • {connectionSummary}</Text>
			</Box>
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
