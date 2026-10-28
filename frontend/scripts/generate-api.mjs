import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

const source = new URL("../../backend/openapi.json", import.meta.url);
const target = new URL("../src/lib/api.ts", import.meta.url);
const generated = astToString(await openapiTS(source));
if (process.argv.includes("--check")) {
  if (await readFile(target, "utf8").catch(() => "") !== generated) {
    throw new Error("Generated API drift: run npm run api:generate");
  }
} else {
  await writeFile(target, generated);
}
