import React from "react";
import { Box, Text } from "ink";

import { MESSAGE_THEME } from "../../lib/theme.js";
import { wrapToWidth } from "./messageLayout.js";

export type SystemMessageKind = "system" | "status" | "protocol";

function labelForKind(kind: SystemMessageKind): string {
	switch (kind) {
		case "status":
			return "Status";
		case "protocol":
			return "Protocol";
		case "system":
		default:
			return "System";
	}
}

export function SystemMessageBubble({
	body,
	kind,
	lineWidth
}: {
	body: string;
	kind: SystemMessageKind;
	lineWidth: number;
}): React.JSX.Element {
	const marker = `· ${labelForKind(kind)} `;
	const markerWidth = marker.length;
	const contentWidth = Math.max(1, lineWidth - markerWidth);
	const lines = wrapToWidth(body, contentWidth);

	return (
		<Box flexDirection="column" width={lineWidth}>
			{lines.map((line, index) => (
				<Box key={`${kind}-${index}`}>
					{index === 0 ? (
						<Text color={MESSAGE_THEME.labelAccent}>{marker}</Text>
					) : (
						<Text>{" ".repeat(markerWidth)}</Text>
					)}
					<Text color={MESSAGE_THEME.foreground}>{line}</Text>
				</Box>
			))}
		</Box>
	);
}
