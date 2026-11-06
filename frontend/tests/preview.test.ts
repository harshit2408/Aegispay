import assert from "node:assert/strict";
import { test } from "node:test";
import { isPreviewId, previewEnabled } from "../src/lib/preview.ts";

test("fixture renders only for preview ids and only when previews are enabled", () => {
  assert.equal(isPreviewId("preview", true), true);
  assert.equal(isPreviewId("synthetic-s3", true), true);
  assert.equal(isPreviewId("real-exec-1", true), false);
  assert.equal(isPreviewId("preview", false), false);
  assert.equal(isPreviewId("synthetic-s3", false), false);
  assert.equal(previewEnabled({ NODE_ENV: "production" }), false);
  assert.equal(previewEnabled({ NODE_ENV: "production", NEXT_PUBLIC_CASE_PREVIEW: "1" }), true);
  assert.equal(previewEnabled({ NODE_ENV: "development" }), true);
});
