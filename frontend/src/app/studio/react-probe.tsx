"use client";

import { useState } from "react";
import type { AgWidgetData, AgWidgetDataFormat, AgWidgetParams } from "ag-studio";

export interface ReactProbeData extends AgWidgetData<Record<string, never>, AgWidgetDataFormat<unknown>> {
  type: "react-probe";
}

export function ReactProbe({ widgetId }: AgWidgetParams<ReactProbeData>) {
  const [count, setCount] = useState(0);
  return (
    <section className="react-probe" aria-label="Custom React widget">
      <h2>React is running inside Studio</h2>
      <p>This custom widget keeps React state inside the Studio layout.</p>
      <button type="button" onClick={() => setCount(count + 1)}>Test React state</button>
      <output aria-live="polite">React clicks: {count}</output>
      <p>Widget: {widgetId}</p>
      <p>Read-only spike. Payment actions and live execution data are not connected.</p>
    </section>
  );
}
