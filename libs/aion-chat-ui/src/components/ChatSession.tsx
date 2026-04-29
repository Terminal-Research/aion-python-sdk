import React from "react";
import { Box } from "ink";

import type { RequestMode, ResponseMode } from "../lib/slashCommands.js";
import { HomeScreen } from "./HomeScreen.js";
import { MessageBubble, type TranscriptEntry } from "./MessageBubble.js";

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
	if (entries.length === 0) {
		return (
			<HomeScreen
				discoveredCount={discoveredCount}
				sourceCount={sourceCount}
				selectedAgentId={selectedAgentId}
				requestMode={requestMode}
				responseMode={responseMode}
			/>
		);
	}

	return (
		<Box flexDirection="column">
			<HomeScreen
				discoveredCount={discoveredCount}
				sourceCount={sourceCount}
				selectedAgentId={selectedAgentId}
				requestMode={requestMode}
				responseMode={responseMode}
				mode="inline"
			/>
			{entries.map((entry, index) => (
				<Box key={entry.id} marginBottom={index < entries.length - 1 ? 1 : 0}>
					<MessageBubble entry={entry} />
				</Box>
			))}
		</Box>
	);
}
