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
          <h2 id="simulation-guide">Real and simulated</h2>
          <p>Sign in above to use the invoice inbox, live case actions and mandate compiler. Case amounts, payee and allowed actions come from the server. Decisions require an eligible reviewer and a reason.</p>
          <p>The Studio cockpit preview uses synthetic records; Follow execution uses authenticated case data and SSE. The scripted hello-world agent makes no model or payment requests.</p>
          <p>REPLAY identifies recorded responses with no live money movement. SANDBOX DEMO identifies demo mode. A simulated webhook is never proof of payment; settlement requires server confirmation.</p>
          <p>In this demo, the Vendor Simulator holds the supplier app credentials to issue invoices, provide read access and reconcile after payment. In production the supplier controls that account. Model tools have read access only. The supplier portal is a simulator.</p>
          <h3>Operator access</h3>
          <p>Use the named operator ID and password supplied by the demo owner. Passwords are never published here. A controller uploads; a different eligible controller or CFO reviews. Admin owns the demo toolbar: reset, scenario runs and detector state. Admin can never approve.</p>
          <p>To rehearse S1–S4, an admin resets the run, then runs each scenario from the demo toolbar; a controller or CFO reviews the cases. S4 links its original S1 case. Turn the detector OFF as admin before a new S3 run to see the independent payee lock.</p>
        </section>
      </div>
    </details>
  );
}
