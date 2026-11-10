// IDs only: importing the fixture here would ship it to every client bundle.
const PREVIEW_IDS = ["preview", "synthetic-s3"];

// Literal process.env.X reads: Next inlines these in client bundles; an env object would not be.
export function previewEnabled(): boolean {
  return process.env.NEXT_PUBLIC_CASE_PREVIEW === "1" || process.env.NODE_ENV !== "production";
}

export function isPreviewId(id: string, enabled = previewEnabled()): boolean {
  return enabled && PREVIEW_IDS.includes(id);
}
