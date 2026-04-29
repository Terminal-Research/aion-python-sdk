import type { PartExtractor } from "../types";

import { filePathExtractor } from "./filePathExtractor";

export const EXTRACTORS: PartExtractor[] = [filePathExtractor];
