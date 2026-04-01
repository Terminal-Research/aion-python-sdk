import React from "react";
import { Box, Text } from "ink";

const AION_LOGO = [
	"╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦",
	"╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║",
	"╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩"
];

export interface HomeScreenProps {
	discoveredCount: number;
	discoveryState: string;
	selectedAgentId?: string;
}

export function HomeScreen({
	discoveredCount,
	discoveryState,
	selectedAgentId
}: HomeScreenProps): React.JSX.Element {
	const suffix = discoveredCount === 1 ? "" : "s";

	return (
		<Box flexGrow={1} justifyContent="center" alignItems="center">
			<Box flexDirection="column" alignItems="center">
				{AION_LOGO.map((line) => (
					<Text key={line} color="cyanBright">
						{line}
					</Text>
				))}
				<Box marginTop={1} flexDirection="column" alignItems="center">
					<Text>{discoveredCount} agent{suffix} discovered</Text>
					<Text dimColor>{discoveryState}</Text>
					<Text color={selectedAgentId ? "green" : "gray"}>
						{selectedAgentId
							? `Selected agent: ${selectedAgentId}`
							: "Type @ to select an agent"}
					</Text>
				</Box>
			</Box>
		</Box>
	);
}
