import { useEffect, useState } from "react";
import { useStdout } from "ink";

export function wrapToWidth(value: string, width: number): string[] {
	const safeWidth = Math.max(1, width);
	const sourceLines = value.split("\n");
	const rows: string[] = [];

	for (const line of sourceLines) {
		if (line.length === 0) {
			rows.push("");
			continue;
		}

		for (let index = 0; index < line.length; index += safeWidth) {
			rows.push(line.slice(index, index + safeWidth));
		}
	}

	return rows;
}

export function useTerminalWidth(): number {
	const { stdout } = useStdout();
	const [viewportWidth, setViewportWidth] = useState(
		stdout?.columns ?? process.stdout.columns ?? 80
	);

	useEffect(() => {
		const handleResize = (): void => {
			setViewportWidth(stdout?.columns ?? process.stdout.columns ?? 80);
		};

		handleResize();
		stdout?.on("resize", handleResize);

		return () => {
			stdout?.off("resize", handleResize);
		};
	}, [stdout]);

	return Math.max(24, viewportWidth);
}
