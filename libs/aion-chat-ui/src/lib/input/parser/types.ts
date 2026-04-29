import type { Part } from "@a2a-js/sdk";

export interface DetectedSpan {
	start: number;
	end: number;
	raw: string;
}

export interface PartExtractor<T extends Part = Part> {
	detect(text: string): DetectedSpan[];
	parse(span: DetectedSpan): Promise<T | null>;
}
