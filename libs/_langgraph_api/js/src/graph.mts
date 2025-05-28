import { Worker } from "node:worker_threads";
import { register } from "node:module";
import * as fs from "node:fs/promises";
import type { CompiledGraph, Graph } from "@langchain/langgraph";
import * as path from "node:path";
import type { JSONSchema7 } from "json-schema";

// enforce API @langchain/langgraph precedence
register("./hooks.mjs", import.meta.url);

export interface GraphSchema {
  state: JSONSchema7 | undefined;
  input: JSONSchema7 | undefined;
  output: JSONSchema7 | undefined;
  config: JSONSchema7 | undefined;
}

export interface GraphSpec {
  sourceFile: string;
  exportSymbol: string;
}

export function filterValidGraphSpecs(specs: Record<string, string>) {
  return Object.entries(specs).filter(
    ([_, spec]) => !spec.split(":")[0].endsWith(".py")
  );
}

export async function resolveGraph(
  spec: string,
  options?: { onlyFilePresence?: false }
): Promise<{
  sourceFile: string;
  exportSymbol: string;
  resolved: CompiledGraph<string>;
}>;

export async function resolveGraph(
  spec: string,
  options: { onlyFilePresence: true }
): Promise<{ sourceFile: string; exportSymbol: string; resolved: undefined }>;

export async function resolveGraph(
  spec: string,
  options?: { onlyFilePresence?: boolean }
) {
  const [userFile, exportSymbol] = spec.split(":", 2);

  // validate file exists
  await fs.stat(userFile);
  if (options?.onlyFilePresence) {
    return { sourceFile: userFile, exportSymbol, resolved: undefined };
  }

  const sourceFile = path.resolve(process.cwd(), userFile);
  type GraphLike = CompiledGraph<string> | Graph<string>;

  type GraphUnknown =
    | GraphLike
    | Promise<GraphLike>
    | (() => GraphLike | Promise<GraphLike>)
    | undefined;

  const isGraph = (graph: GraphLike): graph is Graph<string> => {
    if (typeof graph !== "object" || graph == null) return false;
    return "compile" in graph && typeof graph.compile === "function";
  };

  const graph: GraphUnknown = await import(sourceFile).then(
    (module) => module[exportSymbol || "default"]
  );

  // obtain the graph, and if not compiled, compile it
  const resolved: CompiledGraph<string> = await (async () => {
    if (!graph) throw new Error("Failed to load graph: graph is nullush");
    const graphLike = typeof graph === "function" ? await graph() : await graph;

    if (isGraph(graphLike)) return graphLike.compile();
    return graphLike;
  })();

  return { sourceFile, exportSymbol, resolved };
}

export async function runGraphSchemaWorker(
  spec: GraphSpec,
  options?: { timeoutMs?: number }
) {
  return await new Promise<Record<string, GraphSchema>>((resolve, reject) => {
    const worker = new Worker(
      new URL("./parser/parser.worker.mjs", import.meta.url).pathname
    );

    // Set a timeout to reject if the worker takes too long
    const timeoutId = setTimeout(() => {
      worker.terminate();
      reject(new Error("Schema extract worker timed out"));
    }, options?.timeoutMs ?? 30_000);

    worker.on("message", (result) => {
      worker.terminate();
      clearTimeout(timeoutId);
      resolve(result);
    });

    worker.on("error", reject);
    worker.postMessage(spec);
  });
}
