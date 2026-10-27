import assert from "node:assert/strict";
import { test } from "node:test";
import { previewCase, mockReviewers } from "../src/fixtures/case-preview.ts";
import { currentApprovals, decisionBlock, isEligible, usd } from "../src/lib/case-display.ts";

test("integer cents display exactly without allowing fractional or unsafe money", () => {
  assert.equal(usd(0), "$0.00");
  assert.equal(usd(1), "$0.01");
  assert.equal(usd(42500), "$425.00");
  assert.equal(usd(100009), "$1,000.09");
  for (const invalid of [-1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1]) assert.throws(() => usd(invalid));
});

test("reviewer eligibility excludes the uploader and non-approval roles", () => {
  assert.equal(isEligible(mockReviewers[0], previewCase.uploaded_by), true);
  assert.equal(isEligible(mockReviewers[1], previewCase.uploaded_by), true);
  assert.equal(isEligible(mockReviewers[2], previewCase.uploaded_by), false);
  assert.equal(isEligible(mockReviewers[3], previewCase.uploaded_by), false);
});

test("approval progress counts only distinct eligible humans bound to this gate", () => {
  const approval = { ...previewCase.approvals[0], void_reason: null, gate_hash: previewCase.gate.gate_hash };
  const controller = { ...approval, actor: mockReviewers[0] };
  const approvals = [approval, approval, controller,
    { ...approval, actor: mockReviewers[2] }, { ...approval, actor: mockReviewers[3] },
    { ...approval, gate_hash: "stale" }, { ...approval, void_reason: "Receipt changed" }];
  assert.equal(currentApprovals(previewCase, approvals).length, 2);
  assert.equal(currentApprovals(previewCase, previewCase.approvals).length, 0);
});

test("local decisions reject stale views, disallowed actions, short reasons and repeated reviewers", () => {
  const reason = "Receipt and invoice reviewed.";
  const viewer = mockReviewers[0];
  const hash = previewCase.gate.gate_hash;
  assert.equal(decisionBlock(previewCase, viewer, [], hash, "APPROVE", reason), null);
  assert.match(decisionBlock(previewCase, viewer, [], "stale", "APPROVE", reason)!, /Case changed/);
  assert.match(decisionBlock(previewCase, viewer, [], hash, "APPROVE", "  short  ")!, /10 characters/);
  assert.match(decisionBlock(previewCase, mockReviewers[2], [], hash, "APPROVE", reason)!, /did not upload/);
  const blockedGate = { ...previewCase, gate: { ...previewCase.gate, allowed_actions: [] } };
  assert.match(decisionBlock(blockedGate, viewer, [], hash, "APPROVE", reason)!, /does not allow/);
  const approval = { ...previewCase.approvals[0], actor: viewer, void_reason: null, gate_hash: hash };
  assert.match(decisionBlock(previewCase, viewer, [approval], hash, "APPROVE", reason)!, /already recorded/);
});
