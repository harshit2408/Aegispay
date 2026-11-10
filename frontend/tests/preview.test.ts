import assert from "node:assert/strict";
import { test } from "node:test";
import { previewCase } from "../src/fixtures/case-preview.ts";
import { isPreviewId, previewEnabled } from "../src/lib/preview.ts";

function withEnv(env: Record<string, string | undefined>, run: () => void) {
  const saved = { NODE_ENV: process.env.NODE_ENV, NEXT_PUBLIC_CASE_PREVIEW: process.env.NEXT_PUBLIC_CASE_PREVIEW };
  const set = (values: Record<string, string | undefined>) => {
    for (const [key, value] of Object.entries(values)) {
      if (value === undefined) delete process.env[key]; else process.env[key] = value;
    }
  };
  set({ NEXT_PUBLIC_CASE_PREVIEW: undefined, ...env });
  try { run(); } finally { set(saved); }
}

test("fixture renders only for preview ids and only when previews are enabled", () => {
  assert.equal(isPreviewId("preview", true), true);
  assert.equal(isPreviewId("synthetic-s3", true), true);
  assert.equal(isPreviewId("real-exec-1", true), false);
  assert.equal(isPreviewId("preview", false), false);
  assert.equal(isPreviewId("synthetic-s3", false), false);
});

test("preview id list matches the fixture execution id", () => {
  assert.equal(isPreviewId(previewCase.execution_id, true), true);
});

// Reads the live process.env, so it fails if the gate ever goes back to an injected/aliased env object.
test("previewEnabled reads process.env literals", () => {
  withEnv({ NODE_ENV: "production" }, () => assert.equal(previewEnabled(), false));
  withEnv({ NODE_ENV: "production", NEXT_PUBLIC_CASE_PREVIEW: "1" }, () => assert.equal(previewEnabled(), true));
  withEnv({ NODE_ENV: "development" }, () => assert.equal(previewEnabled(), true));
  withEnv({ NODE_ENV: "production" }, () => assert.equal(isPreviewId("preview"), false));
});
