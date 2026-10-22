# Spike probes (PRD §18)

Live probes against the PayPal **sandbox**. Throwaway tooling: product code never imports this folder.
They read `.env` at the repo root (git-ignored) and print statuses, ids and timings. They never print secrets.
Each probe exits 0 on pass. Results are recorded in PRD §18; rerun after any change to the sandbox apps.

| Script | Spike | Money moved |
|---|---|---|
| `spike1_invoice.py` | SPIKE-1 vendor creates + sends invoice | none |
| `spike2_treasury_read.py` | SPIKE-2 treasury reads vendor invoice | none |
| `spike3_payout.py` | SPIKE-3 payout status transitions | 1.00 USD sandbox |
| `spike4_record_payment.py` | SPIKE-4 partial then full payment | none (records only) |
| `spike5_unclaimed.py` | SPIKE-5 UNCLAIMED payout + cancel attempt | 1.00 USD sandbox |
| `spike6_toolkit.py` | SPIKE-6 Agent Toolkit in LangGraph, read-only | none |

```
python backend/scripts/spikes/spike1_invoice.py        # same for the others
python -m venv .spike6 && .spike6/Scripts/pip install -r backend/scripts/spikes/requirements-spike6.txt
.spike6/Scripts/python backend/scripts/spikes/spike6_toolkit.py
python -m pytest backend/tests/unit/test_spikes_common.py   # offline
```

Run them without `python -I` (they import `common.py` from their own folder).
Still open: webhook latency and event names (SPIKE-3/4, P0-14), SPIKE-2 retest once Invoicing is enabled on the treasury app.
