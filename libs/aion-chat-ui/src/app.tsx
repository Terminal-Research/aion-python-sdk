import { randomUUID } from "node:crypto";

import type {
	Artifact,
	Message,
	Task,
	TaskArtifactUpdateEvent,
	TaskStatusUpdateEvent
} from "@a2a-js/sdk";
import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useApp, useInput } from "ink";

import type { ChatCliOptions } from "./args.js";
import { ChatComposer } from "./components/ChatComposer.js";
import { HomeScreen } from "./components/HomeScreen.js";
import {
	type TranscriptEntry,
	MessageBubble
} from "./components/MessageBubble.js";
import { StatusBar } from "./components/StatusBar.js";
import {
	clearAgentMention,
	getAgentMentionMatch,
	parseAgentSelection
} from "./lib/agentSelection.js";
import {
	buildMessageParams,
	connectClient,
	createPushNotificationConfig
} from "./lib/connection.js";
import { type DiscoveredAgent, discoverAgents } from "./lib/discovery.js";
import {
	EPHEMERAL_MESSAGE_ARTIFACT_ID,
	STREAM_DELTA_ARTIFACT_ID
} from "./lib/a2aMetadata.js";
import {
	type PushNotificationEvent,
	startPushNotificationServer
} from "./lib/pushListener.js";

type ConnectionState = "connecting" | "connected" | "error";

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

export function ChatApp({ options }: { options: ChatCliOptions }): React.JSX.Element {
	const { exit } = useApp();
	const [connectionState, setConnectionState] = useState<ConnectionState>("connecting");
	const [connectionLabel, setConnectionLabel] = useState("Discovering agents...");
	const [discoveryLabel, setDiscoveryLabel] = useState("Scanning manifest...");
	const [pushLabel, setPushLabel] = useState(
		options.pushNotifications ? "Starting..." : "Disabled"
	);
	const [streamLabel, setStreamLabel] = useState(options.noStream ? "Disabled" : "Idle");
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
	const [directFallback, setDirectFallback] = useState(false);

	const connectionSummary = useMemo(() => {
		if (!clientState) {
			return connectionLabel;
		}

		return `${clientState.endpoints.rpcUrl}`;
	}, [clientState, connectionLabel]);

	const agentSuggestions = useMemo(() => {
		const mentionMatch = getAgentMentionMatch(draft);
		if (!mentionMatch) {
			return [];
		}

		return discoveredAgents
			.map((agent) => agent.id)
			.filter((agentId) => agentId.startsWith(mentionMatch.query))
			.slice(0, 6);
	}, [discoveredAgents, draft]);

	useEffect(() => {
		setSelectedSuggestionIndex((current) => {
			if (agentSuggestions.length === 0) {
				return 0;
			}
			return Math.min(current, agentSuggestions.length - 1);
		});
	}, [agentSuggestions]);

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
					`Discovered ${discovery.agents.length} agent${discovery.agents.length === 1 ? "" : "s"} from ${discovery.manifestUrl}`
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
				setConnectionLabel(options.agentId ? `Connecting to @${options.agentId}...` : "Connecting directly...");
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
				setEntries((current) => [
					...current,
					{
						id: randomUUID(),
						role: "system",
						body: `Push notification validation received: ${String(event.payload)}`
					}
				]);
				return;
			}

			setEntries((current) => [
				...current,
				{
					id: randomUUID(),
					role: "system",
					body: `Push notification payload:\n\n\`\`\`json\n${JSON.stringify(
						event.payload,
						null,
						2
					)}\n\`\`\``
				}
			]);
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
				setEntries((current) => [
					...current,
					{
						id: randomUUID(),
						role: "system",
						body: `Connected to **${connected.agentCard.name}** via ${connected.endpoints.rpcUrl}`
					}
				]);
			} catch (error) {
				if (closed) {
					return;
				}
				setConnectionState("error");
				setConnectionLabel(error instanceof Error ? error.message : String(error));
				setEntries((current) => [
					...current,
					{
						id: randomUUID(),
						role: "system",
						body: `Connection failed: ${error instanceof Error ? error.message : String(error)}`
					}
				]);
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

	const appendStatus = (body: string): void => {
		setEntries((current) => [
			...current,
			{
				id: randomUUID(),
				role: "status",
				body
			}
		]);
	};

	const handleMessage = (message: Message): void => {
		const text = extractText(message.parts);
		setEntries((current) => [
			...current,
			{
				id: message.messageId,
				role: message.role === "user" ? "user" : "agent",
				body: text
			}
		]);

		if (message.contextId) {
			setContextId(message.contextId);
		}
		if (message.taskId) {
			setTaskId(message.taskId);
		}
	};

	const handleTaskSnapshot = (task: Task): void => {
		setContextId(task.contextId);
		setTaskId(task.id);
		appendStatus(`Task ${task.id}: ${task.status.state}`);

		if (task.status.message) {
			handleMessage(task.status.message);
		}

		for (const artifact of task.artifacts ?? []) {
			const artifactText = extractText(artifact.parts);
			setEntries((current) =>
				upsertEntry(current, `artifact:${task.id}:${artifact.artifactId}`, "agent", artifactText)
			);
		}
	};

	const handleStatusUpdate = (event: TaskStatusUpdateEvent): void => {
		setContextId(event.contextId);
		setTaskId(event.taskId);
		setStreamLabel(event.status.state);

		if (event.metadata) {
			appendStatus(
				`Task ${event.taskId}: ${event.status.state}\n\n\`\`\`json\n${JSON.stringify(
					event.metadata,
					null,
					2
				)}\n\`\`\``
			);
		} else {
			appendStatus(`Task ${event.taskId}: ${event.status.state}`);
		}

		if (event.status.message) {
			handleMessage(event.status.message);
		}
	};

	const handleArtifactUpdate = (event: TaskArtifactUpdateEvent): void => {
		setContextId(event.contextId);
		setTaskId(event.taskId);
		const artifactText = extractText(event.artifact.parts);
		const role = event.artifact.artifactId === EPHEMERAL_MESSAGE_ARTIFACT_ID ? "status" : "agent";
		const entryId = `artifact:${event.taskId}:${event.artifact.artifactId}`;

		setEntries((current) => {
			const existing = current.find((item) => item.id === entryId);
			const nextBody =
				event.append && existing ? `${existing.body}${artifactText}` : artifactText;
			return upsertEntry(current, entryId, role, nextBody);
		});

		if (event.artifact.artifactId === STREAM_DELTA_ARTIFACT_ID) {
			setStreamLabel("Streaming");
		}
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
		setEntries((current) => [
			...current,
			{
				id: randomUUID(),
				role: "user",
				body: trimmed
			}
		]);

		const params = buildMessageParams(trimmed, contextId, taskId, pushConfig);

		try {
			if (!options.noStream && clientState.agentCard.capabilities.streaming) {
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
		if (key.ctrl && input === "c") {
			if (draft.length > 0) {
				setDraft("");
				return;
			}

			exit();
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

		if (key.escape) {
			setDraft("");
			return;
		}

		if (key.backspace || key.delete) {
			setDraft((current) => current.slice(0, -1));
			return;
		}

		if (key.return) {
			if (key.shift) {
				setDraft((current) => `${current}\n`);
				return;
			}

			if (agentSuggestions.length > 0) {
				applySelectedAgentSuggestion();
				return;
			}

			void submitPrompt();
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
					entries.map((entry, index) => (
						<Box key={entry.id} marginBottom={index < entries.length - 1 ? 1 : 0}>
							<MessageBubble entry={entry} />
						</Box>
					))
				)}
			</Box>
			<StatusBar
				connectionState={connectionLabel}
				pushState={pushLabel}
				streamState={streamLabel}
				discoveredAgents={discoveredAgents.length}
				activeAgentId={selectedAgentId}
			/>
			<ChatComposer
				connected={connectionState === "connected"}
				draft={draft}
				activeAgentId={selectedAgentId}
				agentSuggestions={agentSuggestions}
				selectedSuggestionIndex={selectedSuggestionIndex}
			/>
		</Box>
	);
}
