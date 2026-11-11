-- Authoritative DB side of the S1 evidence (ids and cents only). Run as the database owner:
--   Get-Content backend/scripts/s1_evidence.sql | docker compose ... exec -T postgres \
--     psql -U aegis_owner -d aegis -At -v execution_id=<id>
-- PO close (R17) and the PayPal-confirmed payout item are read here, not inferred from the API.
SELECT json_build_object(
    'execution_id', e.execution_id,
    'status', e.status,
    'route', e.route,
    'paypal_invoice_id', e.paypal_invoice_id,
    'approved_cents', e.approved_cents,
    'po_id', po.po_id,
    'po_status', po.status,
    'payout_batch_id', p.payout_batch_id,
    'payout_item_id', p.payout_item_id,
    'sender_batch_id', p.sender_batch_id,
    'item_status', p.item_status,
    'payout_amount_cents', p.amount_cents,
    'paypal_amount_cents', p.paypal_amount_cents,
    'receiver_payer_id', p.receiver,
    'paypal_receiver', p.paypal_receiver,
    'vendor_master_payer_id', v.paypal_payer_id,
    'recordpay_sent_at', p.recordpay_sent_at
)
FROM invoice_executions e
JOIN payouts p ON p.execution_id = e.execution_id
JOIN purchase_orders po ON po.po_id = e.po_id
JOIN vendor_master v ON v.vendor_id = e.vendor_id
WHERE e.execution_id = :'execution_id';
