import React from "react";
import { Box, Text } from "ink";

export interface StatusBarProps {
	connectionState: string;
	pushState: string;
	streamState: string;
	discoveredAgents: number;
	activeAgentId?: string;
}

export function StatusBar({
	connectionState,
	pushState,
	streamState,
	discoveredAgents,
	activeAgentId
}: StatusBarProps): React.JSX.Element {
	return (
		<Box borderStyle="single" borderColor="gray" paddingX={1}>
			<Text>
				Agent: {activeAgentId ? `@${activeAgentId}` : "none"} • Discovered: {discoveredAgents} • Connection: {connectionState} • Stream: {streamState} • Push: {pushState}
			</Text>
		</Box>
	);
}
