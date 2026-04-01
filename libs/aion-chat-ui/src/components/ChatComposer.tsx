import React from "react";
import { Box, Text } from "ink";

export interface ChatComposerProps {
	connected: boolean;
	draft: string;
	activeAgentId?: string;
	agentSuggestions: string[];
	selectedSuggestionIndex: number;
}

export function ChatComposer({
	connected,
	draft,
	activeAgentId,
	agentSuggestions,
	selectedSuggestionIndex
}: ChatComposerProps): React.JSX.Element {
	const controlHint = draft.length > 0 ? "Ctrl+C clears content" : "Ctrl+C exits";

	return (
		<Box
			borderStyle="round"
			borderColor={connected ? "cyan" : "gray"}
			paddingX={1}
			flexDirection="column"
		>
			<Text color={connected ? "cyan" : "gray"}>
				Composer{activeAgentId ? ` • @${activeAgentId}` : " • No agent selected"}
			</Text>
			{agentSuggestions.length > 0 ? (
				<Box flexDirection="column" marginBottom={1}>
					<Text dimColor>Available agents</Text>
					{agentSuggestions.map((suggestion, index) => (
						<Text
							key={suggestion}
							color={index === selectedSuggestionIndex ? "green" : "gray"}
						>
							{index === selectedSuggestionIndex ? "› " : "  "}@{suggestion}
						</Text>
					))}
				</Box>
			) : null}
			<Text>{draft || "Type @ to choose an agent, then write a message..."}</Text>
			<Text dimColor>{controlHint}</Text>
		</Box>
	);
}
