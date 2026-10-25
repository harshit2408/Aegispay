"use client";

// Adapted from the official starter's app/dashboard/dashboard.tsx and studio/ai.ts.
import { useState } from "react";
import {
  AgStudioAiModule,
  createAiHarness,
  enableStudioDevValidations,
  type AgAiHarnessSetup,
  type AgBaseRegistry,
  type AgDefaultRegistry,
  type AgReportState,
  type AgStudioMode,
} from "ag-studio";
import { AgStudio, AgStudioProvider, createWidgets, type AgWidgetDefinition } from "ag-studio-react";
import { helloAgent } from "./hello-agent";
import { ReactProbe, type ReactProbeData } from "./react-probe";
import { agStudioPayPalTheme } from "./theme";

if (process.env.NODE_ENV !== "production") enableStudioDevValidations();

type ReactProbeDefinition = AgWidgetDefinition<"react-probe", ReactProbeData>;

const reactWidget: ReactProbeDefinition = {
  id: "react-probe",
  label: "React integration probe",
  form: (params) => params.createDefaults({ dataMappingItems: [] }),
  comp: ReactProbe,
  defaultSize: { width: 500, height: 300 },
  minSize: { width: 280, height: 220 },
};

interface SpikeRegistry extends AgBaseRegistry {
  widgets: readonly (AgDefaultRegistry["widgets"][number] | ReactProbeDefinition)[];
}

const widgets = createWidgets<SpikeRegistry>({
  additionalTypes: [reactWidget],
  menu: [{ label: "AegisPay spike", widgetIds: ["react-probe"] }],
});

const data = {
  sources: [{ id: "probes", data: [
    { probe: "React custom widget", mode: "Interactive local probe" },
    { probe: "Agent Framework", mode: "Scripted hello-world; no LLM" },
    { probe: "Licence through 2026-12-15", mode: "Unconfirmed" },
  ] }],
};

const initialState: AgReportState<SpikeRegistry> = {
  pages: [{
    id: "spike",
    widgets: {
      react: { type: "react-probe", dataMapping: {} },
      probes: {
        type: "grid",
        dataMapping: { cols: [{ id: "probes.probe" }, { id: "probes.mode" }] },
        format: { title: { enabled: true, text: "Synthetic integration probes" } },
      },
    },
    widgetLayout: {
      react: { xTrack: 0, yTrack: 0, xSpan: 12, ySpan: 20 },
      probes: { xTrack: 12, yTrack: 0, xSpan: 12, ySpan: 20 },
    },
  }],
  selectedPageId: "spike",
  panels: { data: { collapsed: true }, filters: { collapsed: true } },
};

const ai: AgAiHarnessSetup = ({ api }) => createAiHarness(api, () => ({
  agents: [helloAgent],
  primary: helloAgent.id,
  promptStarters: [{ label: "Say hello (scripted)", prompt: "Hello" }],
}));

export function StudioSpike() {
  const [mode, setMode] = useState<AgStudioMode>("view");
  const [ready, setReady] = useState(false);
  return (
    <>
      <div className="studio-toolbar">
        <button type="button" aria-pressed={mode === "edit"} onClick={() => setMode(mode === "view" ? "edit" : "view")}>
          {mode === "view" ? "Edit Studio layout" : "View Studio layout"}
        </button>
        <span role="status">{ready ? "Studio ready" : "Loading Studio…"}</span>
        <span>Choose “Edit Studio layout”, then “Say hello (scripted)” in the AI panel.</span>
      </div>
      <div className="studio-container" aria-label="AG Studio integration spike">
        <AgStudioProvider licenseKey={process.env.NEXT_PUBLIC_AG_LICENSE_KEY} modules={[AgStudioAiModule]}>
          <AgStudio<SpikeRegistry>
            style={{ height: "100%", width: "100%" }}
            theme={agStudioPayPalTheme}
            layout={{ widgetBorderEnabled: true }}
            data={data}
            widgets={widgets}
            ai={ai}
            initialState={initialState}
            mode={mode}
            onStudioReady={() => setReady(true)}
          />
        </AgStudioProvider>
      </div>
    </>
  );
}
