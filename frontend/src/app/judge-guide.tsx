export function JudgeGuide() {
  return (
    <details className="judge-guide">
      <summary>Judge guide · Four scenarios and simulation boundary</summary>
      <div className="guide-content">
        <section aria-labelledby="scenario-guide">
          <h2 id="scenario-guide">The demo story</h2>
          <p>Inbox → Case → Decision → Settlement receipt → Supplier sees payment.</p>
          <ol>
            <li><strong>S1 · Clean invoice:</strong> match trusted records, settle, then confirm payment.</li>
            <li><strong>S2 · Short shipment:</strong> pay only the accepted value and explain the remaining balance.</li>
            <li><strong>S3 · Invoice injection:</strong> expose untrusted text, freeze the case and require eligible reviewers.</li>
            <li><strong>S4 · Duplicate invoice:</strong> block another payment and link the original case.</li>
          </ol>
        </section>
        <section aria-labelledby="simulation-guide">
          <h2 id="simulation-guide">What works in this preview</h2>
          <p>The cockpit contains synthetic Studio integration probes and a scripted hello-world agent. No PayPal or LLM requests run here.</p>
          <p>The case workspace includes synthetic Evidence, Security and Money panes with local decision rehearsal. Mandate changes, settlement and supplier actions are pending their backend integrations. The supplier area is a simulator, not an independent supplier service.</p>
          <h3>Operator access</h3>
          <p>Operator sign-in and judge credentials are not configured in this shell. The authenticated demo will provide named accounts for controller, CFO, AP clerk, receiver and admin roles.</p>
        </section>
      </div>
    </details>
  );
}
