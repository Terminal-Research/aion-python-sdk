import React from "react";
import { Box, Text } from "ink";

import {
	MESSAGE_ACCENT,
	MESSAGE_BACKGROUND,
	MESSAGE_FOREGROUND
} from "./messageTheme.js";
import { wrapToWidth } from "./messageLayout.js";

export function UserMessageBubble({
	body,
	lineWidth
}: {
	body: string;
	lineWidth: number;
}): React.JSX.Element {
	const contentWidth = Math.max(1, lineWidth - 2);
	const lines = wrapToWidth(body, contentWidth);
	const fillerRow = " ".repeat(lineWidth);

	return (
		<Box flexDirection="column" width={lineWidth}>
			<Text backgroundColor={MESSAGE_BACKGROUND}>{fillerRow}</Text>
			{lines.map((line, index) => {
				const prefix = index === 0 ? "› " : "  ";
				const padding = " ".repeat(Math.max(0, contentWidth - line.length));
				return (
					<Box key={`user-${index}`}>
						<Text backgroundColor={MESSAGE_BACKGROUND} color={MESSAGE_ACCENT}>
							{prefix}
						</Text>
						<Text backgroundColor={MESSAGE_BACKGROUND} color={MESSAGE_FOREGROUND}>
							{line}
						</Text>
						{padding.length > 0 ? (
							<Text backgroundColor={MESSAGE_BACKGROUND}>{padding}</Text>
						) : null}
					</Box>
				);
			})}
			<Text backgroundColor={MESSAGE_BACKGROUND}>{fillerRow}</Text>
		</Box>
	);
}
