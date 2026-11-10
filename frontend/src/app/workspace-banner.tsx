"use client";

import { usePathname } from "next/navigation";
import { previewCase } from "@/fixtures/case-preview";

export function WorkspaceBanner() {
  const pathname = usePathname();
  const preview = pathname === "/vendor" ||
    pathname === "/cases/preview" || pathname === `/cases/${previewCase.execution_id}`;
  return preview
    ? <div className="replay-banner">REPLAY · Development preview · Synthetic data only</div>
    : null;
}
