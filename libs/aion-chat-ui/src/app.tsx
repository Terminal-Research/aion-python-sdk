import { randomUUID } from "node:crypto";

import type {
	Artifact,
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
import { HomeScreen } from "./components/HomeScreen.js";
import {
	type TranscriptEntry,
	MessageBubble
} from "./components/MessageBubble.js";
import {
	clearAgentMention,
	getAgentMentionMatch,
	parseAgentSelection
} from "./lib/agentSelection.js";
import { buildMessageParts } from "./lib/input/parser";
import {
	applyFileSuggestion,
	clearFileMention,
	getFileMentionMatch,
	getFileSuggestions,
	type FileSuggestion
} from "./lib/input/fileMention.js";
import { loadChatModeSettings, saveChatModeSettings } from "./lib/chatSettings.js";
import {
	buildMessageParams,
	connectClient,
	createPushNotificationConfig
} from "./lib/connection.js";
import { type DiscoveredAgent, discoverAgents } from "./lib/discovery.js";
import { formatProtocolPayload } from "./lib/protocolOutput.js";
import {
	EPHEMERAL_MESSAGE_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID
} from "./lib/a2aMetadata.js";
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

type ConnectionState = "connecting" | "connected" | "error";

interface TaskDisplayState {
	hasMessage: boolean;
	hasStreamDelta: boolean;
}

function extractText(parts: Message["parts"] | Artifact["parts"]): string {
	return parts
		.map((part) => {
			if (part.kind === "text") {
				return part.text;
			}
			if (part.kind === "file") {
				return `[Attached file: ${part.file.name ?? "unnamed"}]`;
			}
			if (part.kind === "data") {
				return JSON.stringify(part.data, null, 2);
			}
			return "";
		})
		.filter(Boolean)
		.join("\n");
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

export function ChatApp({ options }: { options: ChatCliOptions }): React.JSX.Element {
	const { exit } = useApp();
	const initialSettingsResult = useMemo(() => loadChatModeSettings(), []);
	const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
	const [connectionLabel, setConnectionLabel] = useState("Discovering agents...");
	const [discoveryLabel, setDiscoveryLabel] = useState("Scanning manifest...");
	const [pushLabel, setPushLabel] = useState(
		options.pushNotifications ? "Starting..." : "Disabled"
	);
	const [streamLabel, setStreamLabel] = useState("Idle");
	const [agentName, setAgentName] = useState("Unknown Agent");
	const [draft, setDraft] = useState("");
	const [entries, setEntries] = useState<TranscriptEntry[]>([]);
	const [contextId, setContextId] = useState<string>();
	const [taskId, setTaskId] = useState<string>();
	const [clientState, setClientState] = useState<Awaited<ReturnType<typeof connectClient>>>();
	const [pushConfig, setPushConfig] = useState<
		ReturnType<typeof createPushNotificationConfig> | undefined
	>();
	const [discoveredAgents, setDiscoveredAgents] = useState<DiscoveredAgent[]>([]);
	const [selectedAgentId, setSelectedAgentId] = useState<string | undefined>(
		options.agentId
	);
	const [selectedSuggestionIndex, setSelectedSuggestionIndex] = useState(0);
	const [selectedFileSuggestionIndex, setSelectedFileSuggestionIndex] = useState(0);
	const [selectedSlashIndex, setSelectedSlashIndex] = useState(0);
	const [selectedSlashSubmenuIndex, setSelectedSlashSubmenuIndex] = useState(0);
	const [slashSubmenuId, setSlashSubmenuId] = useState<SlashCommandId | undefined>();
	const [requestMode, setRequestMode] = useState<RequestMode>(
		initialSettingsResult.settings.requestMode
	);
	const [responseMode, setResponseMode] = useState<ResponseMode>(
		initialSettingsResult.settings.responseMode
	);
	const [directFallback, setDirectFallback] = useState(false);
	const taskDisplayState = useRef<Map<string, TaskDisplayState>>(new Map());

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

	const getTaskDisplayState = (nextTaskId: string): TaskDisplayState => {
		return taskDisplayState.current.get(nextTaskId) ?? {
			hasMessage: false,
			hasStreamDelta: false
		};
	};

	const updateTaskDisplayState = (
		nextTaskId: string | undefined,
		update: Partial<TaskDisplayState>
	): void => {
		if (!nextTaskId) {
			return;
		}

		taskDisplayState.current.set(nextTaskId, {
			...getTaskDisplayState(nextTaskId),
			...update
		});
	};

	const shouldRenderFinalTaskMessage = (nextTaskId: string | undefined): boolean => {
		if (!nextTaskId) {
			return true;
		}

		const state = getTaskDisplayState(nextTaskId);
		return !state.hasMessage && !state.hasStreamDelta;
	};

	const persistModes = (nextRequestMode: RequestMode, nextResponseMode: ResponseMode): void => {
		const warning = saveChatModeSettings({
			requestMode: nextRequestMode,
			responseMode: nextResponseMode
		});
		if (warning) {
			appendSystem(warning);
		}
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
		setContextId(undefined);
		setTaskId(undefined);
		taskDisplayState.current.clear();
		setStreamLabel("Idle");
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
				const discovery = await discoverAgents(options.url);
				if (closed) {
					return;
				}

				setDiscoveredAgents(discovery.agents);
				setDirectFallback(false);
				setDiscoveryLabel(
					`Discovered ${discovery.agents.length} agent${
						discovery.agents.length === 1 ? "" : "s"
					} from ${discovery.manifestUrl}`
				);
				if (options.agentId) {
					setSelectedAgentId(options.agentId);
					setConnectionLabel(`Connecting to @${options.agentId}...`);
				} else {
					setConnectionState("connecting");
					setConnectionLabel("Choose an agent with @");
				}
			} catch (error) {
				if (closed) {
					return;
				}
				setDiscoveredAgents([]);
				setDirectFallback(true);
				setDiscoveryLabel(
					error instanceof Error
						? `Manifest discovery failed, falling back to direct mode: ${error.message}`
						: `Manifest discovery failed, falling back to direct mode: ${String(error)}`
				);
				if (options.agentId) {
					setSelectedAgentId(options.agentId);
				}
				setConnectionLabel(
					options.agentId
						? `Connecting to @${options.agentId}...`
						: "Connecting directly..."
				);
			}
		};

		void discover();

		return () => {
			closed = true;
		};
	}, [options.agentId, options.url]);

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
			const shouldConnect = directFallback || Boolean(selectedAgentId);
			if (!shouldConnect) {
				setConnectionState("connecting");
				setConnectionLabel("Choose an agent with @");
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
				setContextId(undefined);
				setTaskId(undefined);
				taskDisplayState.current.clear();
				setConnectionState("connecting");
				setConnectionLabel(
					selectedAgentId ? `Connecting to @${selectedAgentId}...` : "Connecting directly..."
				);

				const connected = await connectClient({
					...options,
					agentId: directFallback ? undefined : selectedAgentId
				});
				if (closed) {
					return;
				}

				setClientState(connected);
				setAgentName(connected.agentCard.name);
				setConnectionState("connected");
				setConnectionLabel("Connected");
				appendSystem(
					`Connected to **${connected.agentCard.name}** via ${connected.endpoints.rpcUrl}`
				);
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
	}, [directFallback, options, selectedAgentId]);

	const handleMessage = (message: Message): void => {
		if (message.contextId) {
			setContextId(message.contextId);
		}
		if (message.taskId) {
			setTaskId(message.taskId);
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(message);
			return;
		}

		if (!shouldRenderFinalTaskMessage(message.taskId)) {
			return;
		}

		const text = extractText(message.parts);
		if (!text) {
			return;
		}

		if (message.taskId) {
			updateTaskDisplayState(message.taskId, { hasMessage: true });
		}

		setEntries((current) => [
			...current,
			{
				id: message.messageId,
				role: message.role === "user" ? "user" : "agent",
				body: text
			}
		]);
	};

	const handleTaskSnapshot = (task: Task): void => {
		setContextId(task.contextId);
		setTaskId(isTerminalTaskState(task.status.state) ? undefined : task.id);

		if (responseMode === "a2a-protocol") {
			appendProtocol(task);
			return;
		}

		if (task.status.message && shouldRenderFinalTaskMessage(task.id)) {
			const message = task.status.message;
			updateTaskDisplayState(task.id, { hasMessage: true });
			setEntries((current) => [
				...current,
				{
					id: message.messageId,
					role: message.role === "user" ? "user" : "agent",
					body: extractText(message.parts)
				}
			]);
		}
	};

	const handleStatusUpdate = (event: TaskStatusUpdateEvent): void => {
		setContextId(event.contextId);
		setTaskId(isTerminalTaskState(event.status.state) ? undefined : event.taskId);
		setStreamLabel(event.status.state);

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return;
		}

		if (isFinalStatusEvent(event) && event.status.message && shouldRenderFinalTaskMessage(event.taskId)) {
			const message = event.status.message;
			updateTaskDisplayState(event.taskId, { hasMessage: true });
			setEntries((current) => [
				...current,
				{
					id: message.messageId,
					role: message.role === "user" ? "user" : "agent",
					body: extractText(message.parts)
				}
			]);
		}
	};

	const handleArtifactUpdate = (event: TaskArtifactUpdateEvent): void => {
		setContextId(event.contextId);
		setTaskId(event.taskId);

		if (event.artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
			setStreamLabel("Streaming");
		}

		if (responseMode === "a2a-protocol") {
			appendProtocol(event);
			return;
		}

		if (event.artifact.artifactId !== STREAM_DELTA_ARTIFACT_ID) {
			return;
		}

		const artifactText = extractText(event.artifact.parts);
		if (!artifactText) {
			return;
		}

		updateTaskDisplayState(event.taskId, { hasStreamDelta: true });
		const entryId = `artifact:${event.taskId}:${event.artifact.artifactId}`;

		setEntries((current) => {
			const existing = current.find((item) => item.id === entryId);
			const nextBody =
				event.append && existing ? `${existing.body}${artifactText}` : artifactText;
			return upsertEntry(current, entryId, "agent", nextBody);
		});
	};

	const applySelectedFileSuggestion = (): void => {
		const suggestion = fileSuggestions[selectedFileSuggestionIndex];
		if (!suggestion) return;
		setDraft((current) => applyFileSuggestion(current, suggestion));
		setSelectedFileSuggestionIndex(0);
	};

	const applySelectedAgentSuggestion = (): void => {
		const suggestion = agentSuggestions[selectedSuggestionIndex];
		if (!suggestion) {
			return;
		}

		setDraft((current) => clearAgentMention(current));
		if (selectedAgentId !== suggestion) {
			setSelectedAgentId(suggestion);
			setDirectFallback(false);
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

		setSlashSubmenuId(command.id as SlashCommandId);
		setSelectedSlashSubmenuIndex(0);
		setDraft("");
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
			setSelectedAgentId(selection.agentId);
			setDirectFallback(false);
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

		taskDisplayState.current.clear();
		const params = buildMessageParams(parts, contextId, taskId, pushConfig);
		const canStream = Boolean(clientState.agentCard.capabilities.streaming);
		const useStreaming = requestMode === "streaming-message" && canStream;

		try {
			if (requestMode === "streaming-message" && !canStream) {
				appendSystem("Request mode fallback: agent does not support streaming, using Send message.");
			}

			if (useStreaming) {
				setStreamLabel("Streaming");
				for await (const event of clientState.client.sendMessageStream(params)) {
					switch (event.kind) {
						case "message":
							handleMessage(event);
							break;
						case "task":
							handleTaskSnapshot(event);
							break;
						case "status-update":
							handleStatusUpdate(event);
							break;
						case "artifact-update":
							handleArtifactUpdate(event);
							break;
						default:
							break;
					}
				}
				setStreamLabel("Idle");
			} else {
				setStreamLabel("Waiting");
				const response = await clientState.client.sendMessage(params);
				if (response.kind === "message") {
					handleMessage(response);
				} else {
					handleTaskSnapshot(response);
				}
				setStreamLabel("Idle");
			}
		} catch (error) {
			setStreamLabel("Error");
			appendStatus(`Request failed: ${error instanceof Error ? error.message : String(error)}`);
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
				borderColor={connectionState === "connected" ? "green" : "yellow"}
				paddingX={1}
			>
				<Text color={connectionState === "connected" ? "green" : "yellow"}>
					{selectedAgentId ? `@${selectedAgentId}` : agentName}
				</Text>
				<Text> • {connectionSummary}</Text>
			</Box>
			<Box flexDirection="column" flexGrow={1} marginY={1}>
				{entries.length === 0 ? (
					<HomeScreen
						discoveredCount={discoveredAgents.length}
						discoveryState={discoveryLabel}
						selectedAgentId={selectedAgentId}
					/>
				) : (
					<Box flexDirection="column">
						<HomeScreen
							discoveredCount={discoveredAgents.length}
							discoveryState={discoveryLabel}
							selectedAgentId={selectedAgentId}
							mode="inline"
						/>
						{entries.map((entry, index) => (
							<Box key={entry.id} marginBottom={index < entries.length - 1 ? 1 : 0}>
								<MessageBubble entry={entry} />
							</Box>
						))}
					</Box>
				)}
			</Box>
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
