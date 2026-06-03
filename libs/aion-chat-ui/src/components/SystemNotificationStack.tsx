import React from "react";
import { Box } from "ink";

import { MessageBubble, type TranscriptEntry } from "./MessageBubble.js";

export interface SystemNotificationStackProps {
	notifications: readonly TranscriptEntry[];
}

export function SystemNotificationStack({
	notifications
}: SystemNotificationStackProps): React.JSX.Element | null {
	if (notifications.length === 0) {
		return null;
	}

	return (
		<Box flexDirection="column" marginBottom={1}>
			{notifications.map((notification) => (
				<MessageBubble key={notification.id} entry={notification} />
			))}
		</Box>
	);
}
