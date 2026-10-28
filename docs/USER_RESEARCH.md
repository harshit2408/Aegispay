# P1-16 — AP and finance desk research

Research date: **2026-10-27**. Final editorial review: **2026-10-28**. Method: three published customer cases and a separate primary source on payment-change controls.

The owner replaced the original three 15-minute conversations with internet research on this date because interviews were unavailable. This document completes the revised research deliverable. **We conducted zero interviews and zero usability sessions.** The people below are speakers identified by publishers, not AegisPay participants, customers or endorsers. First-hand validation remains deferred.

## Method and evidence quality

We selected public accounts with named AP/finance practitioners and concrete workflows near the hardware-SMB persona. We read the full publisher pages, looking for manual work, approval evidence, payment visibility and supplier controls. Reported evidence and our product interpretation are separated below.

These commercially selected success stories are a convenience sample, not a representative survey or independent evaluation. We did not contact the speakers or inspect their business records. The case pages displayed no clear publication date in the reviewed text; roles, headcounts and workflows describe the published accounts, not verified current facts. Customer-since dates are not publication dates. All sources were accessed **2026-10-27**.

## Three published practitioner cases

### SRC-1 — Teguar Computers: a controller and a small finance team

**Source:** [BILL customer story][s1]. It identifies **Melissa Skalla, Controller**, and reports 55 employees and a finance team of two: a close contextual fit to the target persona.

**Reported evidence:** Staff handle international supplier payments. Bills and PDFs move between NetSuite and BILL without uploading the document again. Payment trace details help answer supplier payment questions; visibility of upcoming bills supports cash planning.

**Published voice:** “My team is myself and an accounting clerk” — Melissa Skalla, as quoted by BILL.

**Our interpretation:** Keep source documents and authoritative payment receipts attached to the case. Make confirmation status easy to find. This supports a workflow hypothesis; it does not establish demand for PayPal or autonomous settlement.

### SRC-2 — Amigo Mobility: matching invoice lines to receipts

**Source:** [Zone & Co customer story][s2]. It identifies **Ryan Maher, Director of Finance and Administration**, and reports over 140 employees. The upper bound is unspecified, so an exact fit to the 20–200-person segment is unconfirmed.

**Reported evidence:** The prior capture tool could not adequately handle invoice line details and PO item receipts. Staff manually posted bills and switched between systems; approvals outside NetSuite caused delays. The replacement displays the PDF alongside its corresponding transaction.

**Our interpretation:** Put billed quantity, PO price and accepted receipt quantity together, with provenance and explicit exceptions. Extraction alone is insufficient. This account does not establish willingness to authorize short payment after damaged goods.

### SRC-3 — JLab: shared-inbox approvals and payment visibility

**Source:** [Tipalti customer story][s3]. It identifies **Kelley Wos, Accounting Manager**, and **Diana Ramos, Staff Accountant**; company size is reported as fewer than 100 employees. Two speakers still represent one company case.

**Reported evidence:** Staff chased approvals through shared inboxes, manually checked and coded invoices, and uploaded PDFs into NetSuite. Fragmentation created month-end bottlenecks. The account emphasizes centralized paid/pending visibility and synchronizing payment records with the ERP.

**Published voice:** “approvers can easily check if something has been paid or is still pending” — Diana Ramos, as quoted by Tipalti.

**Our interpretation:** Show the current case state and next eligible action without an email chase. Distinguish a submitted payout from confirmed payment. No speaker evaluated AegisPay.

## Payment-control corroboration

**SRC-4:** The [FBI's February 27, 2017 BEC guidance][s4] describes attacks on finance staff and recommends secondary sign-off for changes to vendor payment locations, plus phone verification using previously known numbers. This is older control guidance, not a current fraud-loss estimate.

**Our interpretation:** Retain the vendor-master payee lock and independent vendor-change verification (R1/R15/R18). That is consistent with the guidance; it does not prove that a PayPal payer ID verifies a business identity or that a classifier detects prompt injection.

## Product decisions and tests still needed

This is our synthesis, not feature requests made to us. It prioritizes existing PRD requirements and does not mark implementation tasks complete.

| Evidence | Design decision | Future user test |
|---|---|---|
| SRC-1: document continuity and payment traceability | Retain PDF provenance, payment IDs and confirmation state in the case (R6/R7, §14.3). | Can a controller explain whether and how much was paid? |
| SRC-2: line and receipt matching | Prioritize PO/PDF/invoice/GRN evidence and accepted-value checks (R3/R16/R17, P2-06). | Can an AP user explain a quantity discrepancy? |
| SRC-3: approval chasing and fragmented status | Show actionable exceptions and eligible actions together (§14.3, P2-19). | Are the hold reason and next action understandable without coaching? |
| SRC-4: independently verify payment changes | Preserve locked payees and separate vendor-change controls (P2-22). | Can users distinguish vendor assertions from verified master data? |

## Unvalidated assumptions

- No willingness-to-pay, purchasing intent, conversion or product preference was measured. These NetSuite-linked examples do not validate PayPal Invoicing adoption. ERP integration remains outside the current demo scope.
- The sources do not directly validate autonomous short payment, the cost assumptions in PRD §12, or suppliers' dispute responses. Those remain hypotheses.
- Nobody tested our three-pane layout, risk score, reason field, two-human approval flow or receiver/supplier experience. No task completion rates, error rates or interview frequency were collected.
- No source evaluates our extractor, classifier, prompt-injection defenses or payment controls. Technical evidence must come from the PRD tests and eval set.
- Publisher-reported savings are not AegisPay results. We do not transfer their percentages, forecast our savings from them or claim to have reproduced their outcomes.

## Wording for the demo and submission

Suggested wording: **“We reviewed three published AP and finance case studies to inform the workflow. Direct user interviews and usability testing are still pending.”**

Do not describe these as our interviews, say these companies validated AegisPay or imply endorsement. Attribute any public quotation to its original speaker and publisher. Publication is not consent to an AegisPay testimonial; there is no permissioned participant quote for the video.

## Follow-up when interviews become possible

Recruit a controller, an AP practitioner and a receiver or supplier. Ask about a recent invoice discrepancy, the records trusted, payee-change verification and override authority. Then have them use the explicitly mocked case to locate accepted quantity, locked payee, hold reason and next action without coaching. Ask whether they would authorize partial payment and what evidence is missing.

Record actual date, role, method, observed behavior, requested change and resulting product decision. Obtain permission before using participant quotations. Leave findings blank until sessions happen; public cases do not populate interview records.

## Source register

| ID | Original publisher | Type and limitation | Publication / access |
|---|---|---|---|
| SRC-1 | [BILL — Teguar Computers][s1] | Vendor-published customer account; commercial selection/editing. | Publication date not shown; accessed 2026-10-27. |
| SRC-2 | [Zone & Co — Amigo Mobility][s2] | Vendor-published customer account; outcomes not independently checked. | Publication date not shown; accessed 2026-10-27. |
| SRC-3 | [Tipalti — JLab][s3] | Vendor-published customer account; one company, two speakers. | Publication date not shown; accessed 2026-10-27. |
| SRC-4 | [FBI — Business E-Mail Compromise][s4] | Government control guidance; not an interview or current loss estimate. | Published 2017-02-27; accessed 2026-10-27. |

[s1]: https://www.bill.com/case-study/teguar-computers
[s2]: https://www.zoneandco.com/case-studies/how-amigo-mobility-saves-four-months-of-full-time-work-annually-with-ap-automation-in-erp
[s3]: https://tipalti.com/resources/customer-stories/jlab/
[s4]: https://www.fbi.gov/news/stories/business-e-mail-compromise-on-the-rise
