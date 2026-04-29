import React from "react";
import { Box, Text } from "ink";

import { STATUS_BAR_THEME } from "../lib/theme.js";

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
		<Box borderStyle="single" borderColor={STATUS_BAR_THEME.border} paddingX={1}>
			<Text>
				Agent: {activeAgentId ? `@${activeAgentId}` : "none"} • Discovered: {discoveredAgents} • Connection: {connectionState} • Stream: {streamState} • Push: {pushState}
			</Text>
		</Box>
	);
}
