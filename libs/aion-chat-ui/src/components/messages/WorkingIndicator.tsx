import React, { useEffect, useMemo, useState } from "react";
import { Box, Text } from "ink";

import {
	MESSAGE_ACCENT,
	MESSAGE_BACKGROUND,
	MESSAGE_MENU_ACCENT,
	MESSAGE_MUTED
} from "./messageTheme.js";

function formatElapsed(startedAt: number, now: number): string {
	const elapsedSeconds = Math.max(0, Math.floor((now - startedAt) / 1000));
	if (elapsedSeconds < 60) {
		return `${elapsedSeconds}s`;
	}

	const minutes = Math.floor(elapsedSeconds / 60);
	const seconds = elapsedSeconds % 60;
	return `${minutes}m ${seconds.toString().padStart(2, "0")}s`;
}

export function WorkingIndicator({
	startedAt
}: {
	startedAt: number;
}): React.JSX.Element {
	const [now, setNow] = useState(Date.now());
	const [frame, setFrame] = useState(0);
	const animatedLabel = "· Working";
	const elapsedLabel = useMemo(() => formatElapsed(startedAt, now), [now, startedAt]);

	useEffect(() => {
		const timer = setInterval(() => {
			setNow(Date.now());
			setFrame((current) => current + 1);
		}, 150);

		return () => {
			clearInterval(timer);
		};
	}, []);

	const activeIndex = frame % animatedLabel.length;

	return (
		<Box>
			{animatedLabel.split("").map((character, index) => (
				<Text
					key={`${character}-${index}`}
					backgroundColor={index === activeIndex ? MESSAGE_ACCENT : undefined}
					color={index === activeIndex ? MESSAGE_BACKGROUND : MESSAGE_MENU_ACCENT}
				>
					{character}
				</Text>
			))}
			<Text color={MESSAGE_MUTED}> ({elapsedLabel})</Text>
		</Box>
	);
}
