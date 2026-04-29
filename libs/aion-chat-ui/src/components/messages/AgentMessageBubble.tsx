import React from "react";
import { Box, Text } from "ink";

import { MESSAGE_FOREGROUND, MESSAGE_MENU_ACCENT } from "./messageTheme.js";
import { wrapToWidth } from "./messageLayout.js";

export function AgentMessageBubble({
	body,
	lineWidth
}: {
	body: string;
	lineWidth: number;
}): React.JSX.Element {
	const marker = "· ";
	const markerWidth = marker.length;
	const contentWidth = Math.max(1, lineWidth - markerWidth);
	const lines = wrapToWidth(body, contentWidth);

	return (
		<Box flexDirection="column" width={lineWidth}>
			{lines.map((line, index) => (
				<Box key={`agent-${index}`}>
					{index === 0 ? (
						<Text color={MESSAGE_MENU_ACCENT}>{marker}</Text>
					) : (
						<Text>{" ".repeat(markerWidth)}</Text>
					)}
					<Text color={MESSAGE_FOREGROUND}>{line}</Text>
				</Box>
			))}
		</Box>
	);
}
