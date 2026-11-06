"use client";

import { usePathname } from "next/navigation";
import { isPreviewId } from "@/lib/preview";

export function WorkspaceBanner() {
  const pathname = usePathname();
  const path = pathname?.replace(/\/+$/, "") ?? "";
  const preview = !path.startsWith("/cases/") || isPreviewId(path.slice("/cases/".length));
  return preview
    ? <div className="replay-banner">REPLAY · Development preview · Synthetic data only</div>
    : <div className="preview-status">Development workspace · Case review is read-only</div>;
}
