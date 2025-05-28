/// <reference types="./global.d.ts" />

import { z } from "zod";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import {
  filterValidGraphSpecs,
  GraphSchema,
  resolveGraph,
  runGraphSchemaWorker,
} from "./src/graph.mts";
import { build } from "@langchain/langgraph-api/ui/bundler";

const __dirname = new URL(".", import.meta.url).pathname;

async function main() {
  const specs = filterValidGraphSpecs(
    z.record(z.string()).parse(JSON.parse(process.env.LANGSERVE_GRAPHS))
  );

  const GRAPH_SCHEMAS: Record<string, Record<string, GraphSchema> | false> = {};

  try {
    await Promise.all(
      specs.map(async ([graphId, rawSpec]) => {
        console.info(`[${graphId}]: Checking for source file existence`);
        const { resolved, ...spec } = await resolveGraph(rawSpec, {
          onlyFilePresence: true,
        });

        try {
          console.info(`[${graphId}]: Extracting schema`);
          GRAPH_SCHEMAS[graphId] = await runGraphSchemaWorker(spec, {
            timeoutMs: 120_000,
          });
        } catch (error) {
          console.error(`[${graphId}]: Error extracting schema: ${error}`);
          GRAPH_SCHEMAS[graphId] = false;
        }
      })
    );

    await fs.writeFile(
      path.resolve(__dirname, "client.schemas.json"),
      JSON.stringify(GRAPH_SCHEMAS),
      { encoding: "utf-8" }
    );
  } catch (error) {
    console.error(`Error resolving graphs: ${error}`);
    process.exit(1);
  }

  const uiSpecs = z
    .record(z.string())
    .parse(JSON.parse(process.env.LANGGRAPH_UI || "{}"));

  if (Object.keys(uiSpecs).length > 0) {
    try {
      const schemas: Record<string, { assets: string[]; name: string }> = {};
      await Promise.all(
        Object.entries(uiSpecs).map(async ([graphId, uiUserPath]) => {
          console.info(`[${graphId}]: Building UI`);
          const userPath = path.resolve(process.cwd(), uiUserPath);
          const files = await build(graphId, userPath);
          await Promise.all([
            ...files.map(async (item) => {
              const folder = path.resolve(__dirname, "ui", graphId);
              const source = path.resolve(folder, item.basename);

              await fs.mkdir(path.dirname(source), { recursive: true });
              await fs.writeFile(source, item.contents);

              schemas[graphId] ??= { assets: [], name: graphId };

              const relative = path.relative(
                path.resolve(__dirname, "ui", graphId),
                source
              );

              schemas[graphId].assets.push(relative);
            }),
          ]);
        })
      );

      await fs.writeFile(
        path.resolve(__dirname, "client.ui.schemas.json"),
        JSON.stringify(schemas),
        { encoding: "utf-8" }
      );
    } catch (error) {
      console.error(`Error building UI: ${error}`);
      process.exit(1);
    }
  }
}

main();
