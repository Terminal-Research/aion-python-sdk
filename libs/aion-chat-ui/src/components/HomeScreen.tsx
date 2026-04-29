import React, { useEffect, useMemo, useState } from "react";
import { Box, Text, useStdout } from "ink";

const COLORS = {
	lavender: "#C5AFFF",
	purple: "#816CFF",
	cream: "#FAF8F3",
	ink: "#05070C"
} as const;

interface LogoSegment {
	text: string;
	color?: string;
	backgroundColor?: string;
}

type LogoRow = LogoSegment[];

type LogoLayout = "compact" | "stacked" | "wide";

const XTERM_BASE_COLORS = [
	"#000000",
	"#800000",
	"#008000",
	"#808000",
	"#000080",
	"#800080",
	"#008080",
	"#c0c0c0",
	"#808080",
	"#ff0000",
	"#00ff00",
	"#ffff00",
	"#0000ff",
	"#ff00ff",
	"#00ffff",
	"#ffffff"
] as const;

const ANSI_LOGO_ART = String.raw`\e[49m       \e[38;5;183;49m▄▄▄\e[38;5;183;48;5;183m▄▄▄▄\e[38;5;183;49m▄▄▄\e[49m                               \e[38;5;99;48;5;99m▄\e[49m                                 \e[m
\e[49m   \e[38;5;183;49m▄▄\e[38;5;183;48;5;183m▄▄▄▄▄▄▄▄▄▄▄▄▄▄\e[38;5;183;49m▄\e[49m             \e[38;5;15;49m▄\e[38;5;255;49m▄▄▄\e[38;5;15;49m▄▄\e[49m      \e[38;5;99;49m▄▄\e[38;5;99;48;5;99m▄\e[48;5;99m \e[38;5;99;48;5;99m▄\e[38;5;99;49m▄\e[49m                               \e[m
\e[49m  \e[38;5;183;49m▄\e[38;5;183;48;5;183m▄▄▄\e[38;5;15;48;5;189m▄\e[38;5;15;48;5;255m▄\e[38;5;189;48;5;183m▄\e[38;5;183;48;5;183m▄▄▄▄▄▄\e[38;5;105;48;5;147m▄\e[38;5;99;48;5;141m▄\e[38;5;105;48;5;183m▄\e[38;5;183;48;5;183m▄▄▄\e[38;5;183;49m▄\e[49m        \e[38;5;255;49m▄\e[48;5;15m \e[38;5;15;48;5;15m▄\e[48;5;15m      \e[38;5;15;48;5;255m▄\e[38;5;15;48;5;15m▄\e[38;5;15;49m▄\e[49m     \e[49;38;5;99m▀\e[38;5;99;48;5;99m▄\e[49;38;5;99m▀\e[49m                                \e[m
\e[49m \e[38;5;189;48;5;183m▄▄\e[38;5;189;48;5;189m▄\e[38;5;189;48;5;183m▄\e[38;5;15;48;5;189m▄\e[48;5;15m  \e[38;5;189;48;5;15m▄\e[38;5;189;48;5;189m▄\e[38;5;147;48;5;183m▄\e[38;5;147;48;5;189m▄\e[38;5;147;48;5;183m▄\e[38;5;147;48;5;189m▄\e[38;5;99;48;5;147m▄\e[48;5;99m   \e[38;5;105;48;5;147m▄\e[38;5;189;48;5;189m▄\e[38;5;189;48;5;183m▄\e[38;5;189;48;5;189m▄\e[38;5;189;48;5;183m▄\e[49m     \e[38;5;15;49m▄\e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄▄▄▄▄\e[48;5;15m    \e[38;5;15;48;5;15m▄\e[49m   \e[38;5;15;49m▄▄\e[38;5;15;48;5;99m▄\e[38;5;15;49m▄▄\e[49m      \e[38;5;15;49m▄▄▄▄▄▄\e[49m          \e[38;5;15;49m▄▄\e[38;5;255;49m▄▄\e[38;5;15;49m▄\e[49m    \e[m
\e[38;5;189;48;5;189m▄▄▄▄\e[38;5;255;48;5;255m▄\e[38;5;105;48;5;15m▄\e[38;5;99;48;5;183m▄\e[38;5;99;48;5;105m▄\e[48;5;99m           \e[38;5;105;48;5;147m▄\e[38;5;189;48;5;189m▄▄▄\e[38;5;189;49m▄\e[49m    \e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[49;38;5;15m▀\e[49m    \e[49;38;5;255m▀\e[48;5;15m    \e[38;5;15;48;5;15m▄\e[49m  \e[48;5;15m     \e[49m   \e[38;5;15;49m▄\e[38;5;15;48;5;15m▄\e[48;5;15m       \e[38;5;15;48;5;15m▄▄\e[38;5;15;49m▄\e[49m    \e[38;5;15;49m▄\e[38;5;15;48;5;15m▄▄\e[48;5;15m     \e[38;5;15;48;5;255m▄\e[38;5;15;48;5;15m▄\e[38;5;15;49m▄\e[49m \e[m
\e[38;5;189;48;5;189m▄▄▄\e[38;5;105;48;5;189m▄\e[38;5;99;48;5;105m▄\e[48;5;99m \e[38;5;105;48;5;99m▄\e[38;5;189;48;5;99m▄\e[38;5;15;48;5;99m▄▄▄▄▄▄▄▄\e[38;5;189;48;5;99m▄\e[38;5;105;48;5;99m▄\e[48;5;99m  \e[38;5;141;48;5;183m▄\e[38;5;189;48;5;189m▄▄▄\e[49m    \e[38;5;255;48;5;15m▄\e[48;5;15m   \e[38;5;255;48;5;15m▄\e[49m      \e[38;5;15;48;5;255m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[49m  \e[48;5;15m     \e[49m  \e[38;5;15;49m▄\e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄▄▄▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[38;5;15;49m▄\e[49m  \e[38;5;255;48;5;15m▄\e[38;5;15;48;5;15m▄\e[48;5;15m  \e[38;5;15;48;5;15m▄\e[38;5;255;48;5;15m▄\e[38;5;15;48;5;15m▄▄\e[38;5;255;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[m
\e[38;5;189;48;5;189m▄▄\e[38;5;141;48;5;147m▄\e[48;5;99m  \e[38;5;183;48;5;105m▄\e[38;5;15;48;5;15m▄\e[48;5;15m          \e[38;5;15;48;5;15m▄\e[38;5;189;48;5;105m▄\e[48;5;99m  \e[38;5;147;48;5;189m▄\e[38;5;189;48;5;189m▄▄\e[49m    \e[48;5;255m \e[48;5;15m   \e[38;5;15;48;5;255m▄\e[38;5;15;48;5;15m▄▄▄▄▄▄\e[48;5;15m     \e[49m  \e[48;5;15m     \e[49m  \e[48;5;15m    \e[38;5;15;48;5;255m▄\e[49m    \e[38;5;15;48;5;255m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[49m  \e[38;5;255;48;5;255m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[49m   \e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[m
\e[38;5;189;48;5;189m▄▄\e[38;5;99;48;5;99m▄\e[48;5;99m  \e[38;5;255;48;5;189m▄\e[48;5;15m \e[38;5;243;48;5;252m▄\e[38;5;238;48;5;238m▄\e[38;5;238;48;5;8m▄\e[38;5;254;48;5;15m▄\e[48;5;15m  \e[38;5;240;48;5;253m▄\e[38;5;237;48;5;238m▄\e[38;5;238;48;5;243m▄\e[38;5;253;48;5;15m▄\e[48;5;15m \e[38;5;255;48;5;255m▄\e[48;5;99m  \e[38;5;99;48;5;105m▄\e[38;5;189;48;5;189m▄▄\e[49m    \e[48;5;255m \e[48;5;15m   \e[38;5;255;48;5;15m▄\e[38;5;15;48;5;15m▄▄▄▄▄▄\e[48;5;15m     \e[49m  \e[48;5;15m     \e[49m  \e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;49m▄\e[49m    \e[38;5;15;49m▄\e[48;5;15m   \e[38;5;255;48;5;15m▄\e[49m  \e[48;5;15m     \e[49m    \e[48;5;15m    \e[m
\e[49;38;5;225m▀\e[38;5;255;48;5;189m▄\e[38;5;105;48;5;99m▄\e[48;5;99m  \e[38;5;105;48;5;189m▄\e[38;5;15;48;5;15m▄\e[48;5;15m          \e[38;5;15;48;5;15m▄\e[38;5;105;48;5;189m▄\e[48;5;99m  \e[38;5;99;48;5;99m▄\e[38;5;255;48;5;189m▄\e[49m     \e[48;5;255m \e[48;5;15m   \e[48;5;255m \e[49m      \e[48;5;15m     \e[49m  \e[48;5;15m     \e[49m  \e[38;5;15;48;5;15m▄\e[48;5;15m    \e[38;5;255;48;5;15m▄\e[38;5;15;49m▄▄\e[38;5;255;48;5;15m▄\e[38;5;15;48;5;15m▄\e[48;5;15m   \e[38;5;15;48;5;15m▄\e[49m  \e[48;5;15m     \e[49m    \e[48;5;15m    \e[m
\e[49m  \e[38;5;183;48;5;141m▄\e[38;5;99;48;5;99m▄\e[48;5;99m  \e[38;5;99;48;5;141m▄\e[38;5;99;48;5;255m▄\e[38;5;105;48;5;15m▄\e[38;5;141;48;5;15m▄▄▄▄▄▄\e[38;5;105;48;5;15m▄\e[38;5;99;48;5;15m▄\e[38;5;99;48;5;141m▄\e[48;5;99m  \e[38;5;105;48;5;99m▄\e[38;5;189;48;5;147m▄\e[49m      \e[48;5;255m \e[48;5;15m   \e[48;5;255m \e[49m      \e[48;5;15m     \e[49m  \e[48;5;15m     \e[49m   \e[49;38;5;15m▀\e[48;5;15m          \e[49;38;5;15m▀\e[49m   \e[48;5;15m     \e[49m    \e[48;5;15m    \e[m
\e[49m   \e[49;38;5;183m▀\e[38;5;183;48;5;99m▄\e[38;5;141;48;5;99m▄\e[48;5;99m               \e[38;5;99;48;5;99m▄\e[49m      \e[49;38;5;255m▀▀▀▀\e[49;38;5;15m▀\e[49m      \e[49;38;5;15m▀\e[49;38;5;255m▀▀▀\e[49;38;5;15m▀\e[49m  \e[49;38;5;15m▀▀▀▀▀\e[49m     \e[49;38;5;15m▀▀▀▀▀▀▀▀\e[49m     \e[49;38;5;15m▀\e[49;38;5;255m▀▀▀\e[49m     \e[49;38;5;15m▀\e[49;38;5;255m▀▀\e[49;38;5;15m▀\e[m
\e[49m      \e[49;38;5;183m▀\e[49;38;5;105m▀\e[38;5;183;48;5;99m▄\e[38;5;147;48;5;99m▄\e[38;5;141;48;5;99m▄▄▄▄▄\e[38;5;105;48;5;99m▄\e[49;38;5;105m▀▀\e[38;5;99;48;5;99m▄▄▄▄\e[49m                                                            \e[m
`.replaceAll("\\e", "\u001b");

function xtermToHex(index: number): string {
	if (index < 16) {
		return XTERM_BASE_COLORS[index] ?? COLORS.cream;
	}

	if (index >= 16 && index <= 231) {
		const colorIndex = index - 16;
		const red = Math.floor(colorIndex / 36);
		const green = Math.floor((colorIndex % 36) / 6);
		const blue = colorIndex % 6;
		const map = [0, 95, 135, 175, 215, 255];
		return `#${map[red].toString(16).padStart(2, "0")}${map[green]
			.toString(16)
			.padStart(2, "0")}${map[blue].toString(16).padStart(2, "0")}`;
	}

	const shade = 8 + (index - 232) * 10;
	const hex = shade.toString(16).padStart(2, "0");
	return `#${hex}${hex}${hex}`;
}

function parseAnsiArt(input: string): LogoRow[] {
	const rows: LogoRow[] = [];
	let currentRow: LogoRow = [];
	let buffer = "";
	let foreground: string | undefined;
	let background: string | undefined;

	const flush = (): void => {
		if (!buffer) {
			return;
		}

		currentRow.push({
			text: buffer,
			color: foreground ?? COLORS.cream,
			backgroundColor: background
		});
		buffer = "";
	};

	const applyCodes = (codes: number[]): void => {
		if (codes.length === 0) {
			foreground = undefined;
			background = undefined;
			return;
		}

		let index = 0;
		while (index < codes.length) {
			const code = codes[index];
			switch (code) {
				case 0:
					foreground = undefined;
					background = undefined;
					index += 1;
					break;
				case 39:
					foreground = undefined;
					index += 1;
					break;
				case 49:
					background = undefined;
					index += 1;
					break;
				case 38:
					if (codes[index + 1] === 5 && codes[index + 2] !== undefined) {
						foreground = xtermToHex(codes[index + 2]);
						index += 3;
						break;
					}
					index += 1;
					break;
				case 48:
					if (codes[index + 1] === 5 && codes[index + 2] !== undefined) {
						background = xtermToHex(codes[index + 2]);
						index += 3;
						break;
					}
					index += 1;
					break;
				default:
					index += 1;
					break;
			}
		}
	};

	for (let index = 0; index < input.length; index += 1) {
		const character = input[index];
		if (character === "\u001b" && input[index + 1] === "[") {
			flush();
			const endIndex = input.indexOf("m", index);
			if (endIndex === -1) {
				break;
			}

			const rawCodes = input.slice(index + 2, endIndex);
			const codes = rawCodes
				.split(";")
				.filter(Boolean)
				.map((value) => Number.parseInt(value, 10))
				.filter((value) => !Number.isNaN(value));
			applyCodes(codes);
			index = endIndex;
			continue;
		}

		if (character === "\n") {
			flush();
			rows.push(currentRow);
			currentRow = [];
			continue;
		}

		buffer += character;
	}

	flush();
	if (currentRow.length > 0) {
		rows.push(currentRow);
	}

	return rows;
}

function measureRows(rows: LogoRow[]): number {
	return rows.reduce((maxWidth, row) => {
		const rowWidth = row.reduce((total, segment) => total + segment.text.length, 0);
		return Math.max(maxWidth, rowWidth);
	}, 0);
}

function trimRowEnd(row: LogoRow): LogoRow {
	const trimmedRow: LogoRow = [];
	let trimming = true;

	for (let index = row.length - 1; index >= 0; index -= 1) {
		const segment = row[index];
		if (!trimming) {
			trimmedRow.unshift(segment);
			continue;
		}

		if (segment.backgroundColor) {
			trimmedRow.unshift(segment);
			trimming = false;
			continue;
		}

		const nextText = segment.text.replace(/\s+$/u, "");
		if (nextText.length === 0) {
			continue;
		}

		trimmedRow.unshift(
			nextText === segment.text
				? segment
				: {
						...segment,
						text: nextText
					}
		);
		trimming = false;
	}

	return trimmedRow;
}

function trimRowsEnd(rows: LogoRow[]): LogoRow[] {
	return rows.map(trimRowEnd);
}

const ANSI_LOGO_ROWS = parseAnsiArt(ANSI_LOGO_ART);
const DISPLAY_ANSI_LOGO_ROWS = trimRowsEnd(ANSI_LOGO_ROWS);
const ANSI_LOGO_WIDTH = measureRows(DISPLAY_ANSI_LOGO_ROWS);

const MASCOT_ROWS: LogoRow[] = [
	[
		{ text: "                 ▄▄▄▄▄▄                 ", color: COLORS.lavender }
	],
	[
		{ text: "            ▄▄████████▄▄             ", color: COLORS.lavender }
	],
	[
		{ text: "         ▄████████▀▀████████▄         ", color: COLORS.purple }
	],
	[
		{ text: "       ▄████▀            ▀████▄      ", color: COLORS.purple }
	],
	[
		{ text: "      ████    ▄▄▄▄▄▄▄▄▄▄    ████      ", color: COLORS.purple }
	],
	[
		{ text: "     ███   ▄██▀▀▀▀▀▀▀▀▀██▄   ███      ", color: COLORS.purple }
	],
	[
		{ text: "    ███   ██ ", color: COLORS.purple },
		{ text: "████████████", color: COLORS.cream },
		{ text: " ██   ███     ", color: COLORS.purple }
	],
	[
		{ text: "    ██▌  ██ ", color: COLORS.purple },
		{ text: "██ ", color: COLORS.cream },
		{ text: "▄▄", color: COLORS.ink },
		{ text: "      ", color: COLORS.cream },
		{ text: "▄▄", color: COLORS.ink },
		{ text: " ██", color: COLORS.cream },
		{ text: "  ██  ▐██     ", color: COLORS.purple }
	],
	[
		{ text: "    ██▌  ██ ", color: COLORS.purple },
		{ text: "██████████████", color: COLORS.cream },
		{ text: " ██  ▐██     ", color: COLORS.purple }
	],
	[
		{ text: "    ███▄   ▀██▄▄▄▄▄▄██▀   ▄███      ", color: COLORS.purple }
	],
	[
		{ text: "      ▀████▄   ▀████▀   ▄████▀       ", color: COLORS.purple }
	],
	[
		{ text: "         ▀████▄▄    ▄▄████▀   ▄▄     ", color: COLORS.purple }
	],
	[
		{ text: "             ▀██████▀      ▄████     ", color: COLORS.purple }
	],
	[
		{ text: "                 ▀▀▀▀        ▀██▀      ", color: COLORS.purple }
	]
];

const COMPACT_ROWS: LogoRow[] = [
	[{ text: "╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦", color: COLORS.purple }],
	[{ text: "╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║", color: COLORS.cream }],
	[{ text: "╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩", color: COLORS.cream }]
];

function renderRows(rows: LogoRow[], keyPrefix: string): React.JSX.Element {
	return (
		<Box flexDirection="column">
			{rows.map((row, rowIndex) => (
				<Box key={`${keyPrefix}-${rowIndex}`}>
					{row.map((segment, segmentIndex) => (
						<Text
							key={`${keyPrefix}-${rowIndex}-${segmentIndex}`}
							color={segment.color}
							backgroundColor={segment.backgroundColor}
						>
							{segment.text}
						</Text>
					))}
				</Box>
			))}
		</Box>
	);
}

function getLayout(width: number): LogoLayout {
	if (width >= ANSI_LOGO_WIDTH + 4) {
		return "wide";
	}

	if (width >= 88) {
		return "stacked";
	}

	return "compact";
}

export interface HomeScreenProps {
	discoveredCount: number;
	sourceCount?: number;
	selectedAgentId?: string;
	terminalWidth?: number;
	mode?: "standalone" | "inline";
}

export function HomeScreen({
	discoveredCount,
	sourceCount = 0,
	selectedAgentId,
	terminalWidth,
	mode = "standalone"
}: HomeScreenProps): React.JSX.Element {
	const { stdout } = useStdout();
	const [liveWidth, setLiveWidth] = useState<number>(
		terminalWidth ?? stdout?.columns ?? process.stdout.columns ?? 120
	);
	const suffix = discoveredCount === 1 ? "" : "s";

	useEffect(() => {
		if (terminalWidth !== undefined) {
			setLiveWidth(terminalWidth);
			return;
		}

		const handleResize = (): void => {
			setLiveWidth(stdout?.columns ?? process.stdout.columns ?? 120);
		};

		handleResize();
		stdout?.on("resize", handleResize);

		return () => {
			stdout?.off("resize", handleResize);
		};
	}, [stdout, terminalWidth]);

	const layout = useMemo(() => getLayout(liveWidth), [liveWidth]);
	const sourceSuffix = sourceCount === 1 ? "" : "s";
	const outerProps =
		mode === "standalone"
			? { flexGrow: 1, justifyContent: "center" as const, alignItems: "center" as const }
			: { alignItems: "center" as const };

	return (
		<Box {...outerProps}>
			<Box
				flexDirection="column"
				alignItems="center"
				marginTop={mode === "standalone" ? 4 : 1}
				marginBottom={mode === "standalone" ? 4 : 1}
			>
				{layout === "wide" ? (
					<Box width={liveWidth} justifyContent="center">
						{renderRows(DISPLAY_ANSI_LOGO_ROWS, "ansi-logo")}
					</Box>
				) : layout === "stacked" ? (
					<Box flexDirection="column" alignItems="center">
						{renderRows(MASCOT_ROWS, "mascot")}
					</Box>
				) : (
					renderRows(COMPACT_ROWS, "compact")
				)}
				<Box marginTop={2} flexDirection="column" alignItems="center">
					<Text color={COLORS.cream}>
						{discoveredCount} agent{suffix} discovered from {sourceCount} source{sourceSuffix}
					</Text>
					<Text color={selectedAgentId ? "green" : COLORS.lavender}>
						{selectedAgentId
							? `Selected agent: ${selectedAgentId}`
							: "Type @ to select an agent"}
					</Text>
				</Box>
			</Box>
		</Box>
	);
}
