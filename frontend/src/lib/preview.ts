import { previewCase } from "../fixtures/case-preview.ts";

// Fixture data renders only when previews are enabled; production needs NEXT_PUBLIC_CASE_PREVIEW=1.
export function previewEnabled(env: Record<string, string | undefined> = process.env): boolean {
  return env.NEXT_PUBLIC_CASE_PREVIEW === "1" || env.NODE_ENV !== "production";
}

export function isPreviewId(id: string, enabled = previewEnabled()): boolean {
  return enabled && (id === "preview" || id === previewCase.execution_id);
}
