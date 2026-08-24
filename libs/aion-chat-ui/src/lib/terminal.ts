import ansiEscapes from "ansi-escapes";

export interface TerminalClearRequest {
	isTTY: boolean | undefined;
	terminalType: string | undefined;
	write: (data: string) => void;
}

/**
 * Ask a compatible terminal to erase its visible screen and saved scrollback.
 *
 * A true result means the clear sequence was handed to Ink's stdout writer;
 * terminal emulators may still ignore all or part of the request.
 */
export function requestTerminalClear({
	isTTY,
	terminalType,
	write
}: TerminalClearRequest): boolean {
	if (!isTTY || terminalType?.toLowerCase() === "dumb") {
		return false;
	}

	try {
		write(ansiEscapes.clearTerminal);
		return true;
	} catch {
		return false;
	}
}
