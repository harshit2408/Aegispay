import assert from "node:assert/strict";
import { test } from "node:test";
import { isCurrentSection, navigation } from "../src/app/navigation.ts";

test("the active section follows case identifiers and never activates sibling prefixes", () => {
  assert.equal(isCurrentSection("/cases/invoice-42", "/cases"), true);
  assert.equal(isCurrentSection("/cases/invoice-42", "/"), false);
  assert.equal(isCurrentSection("/cases-archive", "/cases"), false);
  assert.equal(isCurrentSection("/mandate", "/mandate"), true);
  assert.equal(isCurrentSection("/vendor", "/mandate"), false);
  assert.equal(isCurrentSection("/", "/"), true);
});

test("each supported workspace route has exactly one active navigation item", () => {
  for (const pathname of ["/", "/cases/preview", "/cases/invoice-42", "/mandate", "/vendor"]) {
    assert.equal(navigation.filter(({ section }) => isCurrentSection(pathname, section)).length, 1, pathname);
  }
  assert.equal(navigation.filter(({ section }) => isCurrentSection("/missing", section)).length, 0);
});
