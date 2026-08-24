import React, { memo } from "react";
import { Box, Text } from "ink";

import { MarkdownBlock } from "../../lib/markdown.js";
import { MESSAGE_THEME } from "../../lib/theme.js";

export const AgentMessageBubble = memo(function AgentMessageBubble({
	body,
	lineWidth,
	isFinalized
}: {
	body: string;
	lineWidth: number;
	isFinalized: boolean;
}): React.JSX.Element {
	const marker = "· ";
	const markerWidth = marker.length;
	const contentWidth = Math.max(1, lineWidth - markerWidth);

	return (
		<Box width={lineWidth} alignItems="flex-start">
			<Text color={MESSAGE_THEME.labelAccent}>{marker}</Text>
			<MarkdownBlock
				content={body}
				width={contentWidth}
				isFinalized={isFinalized}
			/>
		</Box>
	);
});
