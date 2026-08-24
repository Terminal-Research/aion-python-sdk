import React, { useMemo } from "react";
import { Box, Static } from "ink";

import type { RequestMode, ResponseMode } from "../lib/slashCommands.js";
import { partitionTranscriptEntries } from "../lib/transcript.js";
import { HomeScreen } from "./HomeScreen.js";
import { MessageBubble, type TranscriptEntry } from "./MessageBubble.js";
import { useTerminalWidth } from "./messages/messageLayout.js";

type ScrollbackItem =
	| { id: "transcript-header"; kind: "header" }
	| { id: string; kind: "entry"; entry: TranscriptEntry };

export interface ChatSessionProps {
	entries: TranscriptEntry[];
	discoveredCount: number;
	sourceCount: number;
	selectedAgentId?: string;
	requestMode: RequestMode;
	responseMode: ResponseMode;
}

export function ChatSession({
	entries,
	discoveredCount,
	sourceCount,
	selectedAgentId,
	requestMode,
	responseMode
}: ChatSessionProps): React.JSX.Element {
	const lineWidth = useTerminalWidth();
	const { scrollbackEntries, dynamicEntries } = useMemo(
		() => partitionTranscriptEntries(entries),
		[entries]
	);
	const scrollbackItems = useMemo<ScrollbackItem[]>(
		() =>
			entries.length === 0
				? []
				: [
						{ id: "transcript-header", kind: "header" },
						...scrollbackEntries.map((entry) => ({
							id: entry.id,
							kind: "entry" as const,
							entry
						}))
					],
		[entries.length, scrollbackEntries]
	);

	if (entries.length === 0) {
		return (
			<HomeScreen
				discoveredCount={discoveredCount}
				sourceCount={sourceCount}
				selectedAgentId={selectedAgentId}
				requestMode={requestMode}
				responseMode={responseMode}
				terminalWidth={lineWidth}
			/>
		);
	}

	return (
		<Box flexDirection="column">
			<Static items={scrollbackItems}>
				{(item) =>
					item.kind === "header" ? (
						<HomeScreen
							key={item.id}
							discoveredCount={discoveredCount}
							sourceCount={sourceCount}
							selectedAgentId={selectedAgentId}
							requestMode={requestMode}
							responseMode={responseMode}
							terminalWidth={lineWidth}
							mode="inline"
						/>
					) : (
						<Box key={item.id} marginBottom={1}>
							<MessageBubble entry={item.entry} lineWidth={lineWidth} />
						</Box>
					)
				}
			</Static>
			{dynamicEntries.map((entry, index) => (
				<Box
					key={entry.id}
					marginBottom={index < dynamicEntries.length - 1 ? 1 : 0}
				>
					<MessageBubble entry={entry} lineWidth={lineWidth} />
				</Box>
			))}
		</Box>
	);
}
