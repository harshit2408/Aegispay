"use client";

// Adapted from the official starter's app/dashboard/dashboard.tsx and studio/ai.ts.
import { useRef, useState } from "react";
import {
  AgStudioAiModule,
  createAiHarness,
  enableStudioDevValidations,
  type AgAiHarnessSetup,
  type AgBaseRegistry,
  type AgDefaultRegistry,
  type AgReportState,
  type AgStudioMode,
  type AgStudioApi,
} from "ag-studio";
import { AgStudio, AgStudioProvider, createWidgets, type AgWidgetDefinition } from "ag-studio-react";
import { helloAgent } from "./hello-agent";
import { ReactProbe, type ReactProbeData } from "./react-probe";
import { agStudioPayPalTheme } from "./theme";
import { cockpitPreview } from "../../fixtures/cockpit-preview";
import { LedgerWidget, GaugeWidget, ExposureWidget, type LedgerData, type GaugeData, type ExposureData } from "./cockpit-widgets";

if (process.env.NODE_ENV !== "production") enableStudioDevValidations();

type ReactProbeDefinition = AgWidgetDefinition<"react-probe", ReactProbeData>;
type LedgerDefinition = AgWidgetDefinition<"payables-ledger", LedgerData>;
type GaugeDefinition = AgWidgetDefinition<"mandate-gauge", GaugeData>;
type ExposureDefinition = AgWidgetDefinition<"frozen-exposure", ExposureData>;

const ledgerWidget: LedgerDefinition = {
  id: "payables-ledger", label: "Payables ledger", comp: LedgerWidget,
  form: (params) => params.createDefaults({ dataMappingItems: [] }),
  defaultSize: { width: 1000, height: 320 }, minSize: { width: 300, height: 220 },
};
const gaugeWidget: GaugeDefinition = {
  id: "mandate-gauge", label: "Mandate gauge", comp: GaugeWidget,
  form: (params) => params.createDefaults({ dataMappingItems: [] }),
  defaultSize: { width: 420, height: 320 }, minSize: { width: 280, height: 220 },
};
const exposureWidget: ExposureDefinition = {
  id: "frozen-exposure", label: "Exposure by reason", comp: ExposureWidget,
  form: (params) => params.createDefaults({ dataMappingItems: [] }),
  defaultSize: { width: 560, height: 320 }, minSize: { width: 280, height: 220 },
};

const reactWidget: ReactProbeDefinition = {
  id: "react-probe",
  label: "React integration probe",
  form: (params) => params.createDefaults({ dataMappingItems: [] }),
  comp: ReactProbe,
  defaultSize: { width: 500, height: 300 },
  minSize: { width: 280, height: 220 },
};

interface SpikeRegistry extends AgBaseRegistry {
  widgets: readonly (AgDefaultRegistry["widgets"][number] | ReactProbeDefinition | LedgerDefinition | GaugeDefinition | ExposureDefinition)[];
}

const widgets = createWidgets<SpikeRegistry>({
  additionalTypes: [reactWidget, ledgerWidget, gaugeWidget, exposureWidget],
  menu: [{ label: "AegisPay", widgetIds: ["payables-ledger", "mandate-gauge", "frozen-exposure", "react-probe"] }],
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
    id: "cockpit",
    widgets: {
      ledger: { type: "payables-ledger", dataMapping: {} },
      gauge: { type: "mandate-gauge", dataMapping: {} },
      exposure: { type: "frozen-exposure", dataMapping: {} },
    },
    widgetLayout: {
      ledger: { xTrack: 0, yTrack: 0, xSpan: 24, ySpan: 16 },
      gauge: { xTrack: 0, yTrack: 16, xSpan: 10, ySpan: 22 },
      exposure: { xTrack: 10, yTrack: 16, xSpan: 14, ySpan: 22 },
    },
  }, {
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
  selectedPageId: "cockpit",
  panels: { data: { collapsed: true }, filters: { collapsed: true } },
};

const ai: AgAiHarnessSetup = ({ api }) => createAiHarness(api, () => ({
  agents: [helloAgent],
  primary: helloAgent.id,
  promptStarters: [{ label: "Say hello (scripted)", prompt: "Hello" }],
}));

export function StudioSpike() {
  const studioApi = useRef<AgStudioApi | null>(null);
  const [mode, setMode] = useState<AgStudioMode>("view");
  const [ready, setReady] = useState(false);
  const [selectedPage, setSelectedPage] = useState("cockpit");

  function selectPage(page: string) {
    if (!studioApi.current) return;
    studioApi.current.setState({ ...studioApi.current.getState(), selectedPageId: page });
    setSelectedPage(page);
  }
  return (
    <>
      <div className="studio-toolbar">
        <button type="button" disabled={!ready} aria-pressed={selectedPage === "cockpit"} onClick={() => selectPage("cockpit")}>Cockpit widgets</button>
        <button type="button" disabled={!ready} aria-pressed={selectedPage === "spike"} onClick={() => selectPage("spike")}>Integration probes</button>
        <button type="button" aria-pressed={mode === "edit"} onClick={() => setMode(mode === "view" ? "edit" : "view")}>
          {mode === "view" ? "Edit Studio layout" : "View Studio layout"}
        </button>
        <span role="status">{ready ? "Studio ready" : "Loading Studio…"}</span>
        <span>Synthetic read-only snapshot · No live payments or API calls.</span>
      </div>
      <div className="studio-container" role="region" aria-label="AG Studio integration preview; scroll horizontally on narrow screens" tabIndex={0}>
        <AgStudioProvider licenseKey={process.env.NEXT_PUBLIC_AG_LICENSE_KEY} modules={[AgStudioAiModule]}>
          <AgStudio<SpikeRegistry>
            style={{ height: "100%", width: "100%" }}
            theme={agStudioPayPalTheme}
            layout={{ widgetBorderEnabled: true }}
            data={data}
            widgets={widgets}
            ai={ai}
            initialState={initialState}
            context={cockpitPreview}
            mode={mode}
            onStudioReady={({ api }) => { studioApi.current = api; setReady(true); }}
          />
        </AgStudioProvider>
      </div>
    </>
  );
}
