import { useEffect, useState } from "react";
import { useStdout } from "ink";

function findSoftBreakIndex(value: string, width: number): number | undefined {
	const searchEnd = Math.min(value.length - 1, width);
	for (let index = searchEnd; index > 0; index -= 1) {
		if (/\s/u.test(value[index])) {
			return index;
		}
	}
	return undefined;
}

function wrapLineToWidth(value: string, width: number): string[] {
	if (value.length <= width) {
		return [value];
	}

	const rows: string[] = [];
	let remaining = value;
	while (remaining.length > width) {
		const softBreakIndex = findSoftBreakIndex(remaining, width);
		if (softBreakIndex === undefined) {
			rows.push(remaining.slice(0, width));
			remaining = remaining.slice(width);
			continue;
		}

		rows.push(remaining.slice(0, softBreakIndex).replace(/\s+$/u, ""));
		remaining = remaining.slice(softBreakIndex).replace(/^\s+/u, "");
	}

	rows.push(remaining);
	return rows;
}

export function wrapToWidth(value: string, width: number): string[] {
	const safeWidth = Math.max(1, width);
	const sourceLines = value.split("\n");
	const rows: string[] = [];

	for (const line of sourceLines) {
		if (line.length === 0) {
			rows.push("");
			continue;
		}

		rows.push(...wrapLineToWidth(line, safeWidth));
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
