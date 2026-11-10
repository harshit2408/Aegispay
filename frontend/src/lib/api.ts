export interface paths {
    "/api/executions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Executions */
        get: operations["list_executions"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/executions/{execution_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Execution */
        get: operations["get_execution"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/executions/{execution_id}/decisions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Submit Decision */
        post: operations["submit_decision"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_api_health_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Execution Stream */
        get: operations["execution_stream"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/webhooks/paypal/simulator/{account}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Simulator */
        post: operations["simulator_api_webhooks_paypal_simulator__account__post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/webhooks/paypal/treasury": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Treasury */
        post: operations["treasury_api_webhooks_paypal_treasury_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/webhooks/paypal/vendor": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Vendor */
        post: operations["vendor_api_webhooks_paypal_vendor_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** APIError */
        APIError: {
            /** Detail */
            detail: string;
        };
        /** ApprovalView */
        ApprovalView: {
            /**
             * Action
             * @enum {string}
             */
            action: "APPROVE" | "SHORT_PAY" | "REJECT";
            actor: components["schemas"]["OperatorView"];
            /** Approval Id */
            approval_id: string;
            /** Gate Hash */
            gate_hash: string;
            /** Reason */
            reason: string;
            /** Void Reason */
            void_reason: string | null;
        };
        /** AuditAnchorView */
        AuditAnchorView: {
            /** Anchored At */
            anchored_at: string | null;
            /** Head Hmac */
            head_hmac: string | null;
            /** Verified */
            verified: boolean;
        };
        /** CaseDetail */
        CaseDetail: {
            /** Approvals */
            approvals: components["schemas"]["ApprovalView"][];
            audit_anchor: components["schemas"]["AuditAnchorView"];
            evidence: components["schemas"]["EvidencePack"] | null;
            /** Execution Id */
            execution_id: string;
            extraction: components["schemas"]["InvoiceExtraction"];
            forensics: components["schemas"]["ForensicsReport"];
            gate: components["schemas"]["GateDecision"];
            goods_receipt: components["schemas"]["GoodsReceiptView"];
            match: components["schemas"]["MatchReport"];
            paypal_invoice: components["schemas"]["PayPalInvoiceView"];
            po_consumption: components["schemas"]["POConsumptionView"];
            policy: components["schemas"]["MandatePolicy"];
            purchase_order: components["schemas"]["PurchaseOrderView"];
            /** Replayed */
            replayed: boolean;
            risk: components["schemas"]["RiskAssessment"];
            /** Settlement Timeline */
            settlement_timeline: components["schemas"]["SettlementEventView"][];
            short_pay: components["schemas"]["ShortPayDecision"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "RECEIVED" | "ANALYZING" | "FROZEN" | "REJECTED" | "DUPLICATE_BLOCKED" | "APPROVED_FOR_PAYMENT" | "PAYOUT_SENDING" | "PAYOUT_SUBMITTED" | "PAYOUT_SUCCEEDED" | "PAYOUT_FAILED" | "SETTLED" | "NEEDS_MANUAL_REVIEW";
            /** Uploaded By */
            uploaded_by: string;
            vendor: components["schemas"]["VendorView"];
            viewer: components["schemas"]["OperatorView"];
        };
        /** DecisionReceipt */
        DecisionReceipt: {
            /** Approval Id */
            approval_id: string;
        };
        /** DecisionRequest */
        DecisionRequest: {
            /**
             * Action
             * @enum {string}
             */
            action: "APPROVE" | "SHORT_PAY" | "REJECT";
            /** Gate Hash */
            gate_hash: string;
            /** Reason */
            reason: string;
        };
        /** EvidenceFact */
        EvidenceFact: {
            /** Claim */
            claim: string;
            /** Excerpt */
            excerpt: string;
            /** Tool Call Id */
            tool_call_id: string;
            /** Vendor Asserted */
            vendor_asserted: boolean;
        };
        /** EvidencePack */
        EvidencePack: {
            /**
             * Advisory Recommendation
             * @enum {string}
             */
            advisory_recommendation: "APPROVE" | "SHORT_PAY" | "REJECT" | "NEEDS_INFO";
            /** Facts */
            facts: components["schemas"]["EvidenceFact"][];
            /** Summary */
            summary: string;
        };
        /** ExecutionRow */
        ExecutionRow: {
            /** Billed Cents */
            billed_cents: number;
            /** Execution Id */
            execution_id: string;
            /** Payable Cents */
            payable_cents: number;
            /** Replayed */
            replayed: boolean;
            /** Risk Score */
            risk_score: number;
            /**
             * Status
             * @enum {string}
             */
            status: "RECEIVED" | "ANALYZING" | "FROZEN" | "REJECTED" | "DUPLICATE_BLOCKED" | "APPROVED_FOR_PAYMENT" | "PAYOUT_SENDING" | "PAYOUT_SUBMITTED" | "PAYOUT_SUCCEEDED" | "PAYOUT_FAILED" | "SETTLED" | "NEEDS_MANUAL_REVIEW";
            /** Vendor Name */
            vendor_name: string;
        };
        /** ForensicsReport */
        ForensicsReport: {
            /**
             * Classifier Label
             * @enum {string}
             */
            classifier_label: "CLEAN" | "SUSPICIOUS" | "INJECTION";
            /** Classifier Rationale */
            classifier_rationale: string;
            /** Detector Enabled */
            detector_enabled: boolean;
            /** Detector Version */
            detector_version: string;
            /** Hidden Segments */
            hidden_segments: components["schemas"]["HiddenSegment"][];
        };
        /** GateDecision */
        GateDecision: {
            /** Allowed Actions */
            allowed_actions: ("APPROVE" | "SHORT_PAY" | "REJECT")[];
            /** Gate Hash */
            gate_hash: string;
            /** Payable Cents */
            payable_cents: number;
            /** Policy Id */
            policy_id: string;
            /** Policy Version */
            policy_version: number;
            /** Reasons */
            reasons: string[];
            /**
             * Required Approvals
             * @enum {integer}
             */
            required_approvals: 0 | 1 | 2 | 99;
            /**
             * Route
             * @enum {string}
             */
            route: "AUTO_SETTLE" | "SHORT_PAY" | "FREEZE" | "DUPLICATE_BLOCKED" | "REJECTED";
        };
        /** GoodsReceiptView */
        GoodsReceiptView: {
            /** Accepted Qty */
            accepted_qty: number;
            /** Grn Id */
            grn_id: string;
            /** Recorded By */
            recorded_by: string;
            /** Version */
            version: number;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HiddenSegment */
        HiddenSegment: {
            /** Bbox */
            bbox: [
                number,
                number,
                number,
                number
            ];
            /** Page */
            page: number;
            /**
             * Technique
             * @enum {string}
             */
            technique: "COLOR_MATCH" | "TINY_FONT" | "OFF_PAGE" | "INVISIBLE_MODE" | "COVERED" | "ANNOTATION" | "METADATA" | "OCR_DIFF";
            /** Text */
            text: string;
        };
        /** InvoiceExtraction */
        InvoiceExtraction: {
            /** Billed Qty */
            billed_qty: number;
            /**
             * Currency
             * @constant
             */
            currency: "USD";
            /** Field Confidence */
            field_confidence: {
                [key: string]: number;
            };
            /** Payee Email */
            payee_email: string | null;
            /** Po Reference */
            po_reference: string | null;
            /** Total Cents */
            total_cents: number;
            /** Unit Price Cents */
            unit_price_cents: number;
            /** Vendor Invoice Number */
            vendor_invoice_number: string | null;
        };
        /** MandatePolicy */
        MandatePolicy: {
            /**
             * Auto Short Pay Enabled
             * @default true
             */
            auto_short_pay_enabled: boolean;
            /**
             * Cap Timezone
             * @default UTC
             */
            cap_timezone: string;
            /** Daily Cap Cents */
            daily_cap_cents: number;
            /**
             * Dual Approval Risk Threshold
             * @default 80
             */
            dual_approval_risk_threshold: number;
            /**
             * Max Price Variance Bps
             * @default 0
             */
            max_price_variance_bps: number;
            /**
             * Max Short Pay Delta Cents
             * @default 10000
             */
            max_short_pay_delta_cents: number;
            /** Max Single Invoice Cents */
            max_single_invoice_cents: number;
            /** Policy Id */
            policy_id: string;
            /**
             * @default {
             *       "annual_cost_of_capital": 0.1,
             *       "claim_days": 45,
             *       "claim_ops_cost_cents": 3500,
             *       "contest_cost_cents": 10000,
             *       "notice_ops_cost_cents": 500,
             *       "p_recover": 0.6,
             *       "p_vendor_contests": 0.1
             *     }
             */
            short_pay_assumptions: components["schemas"]["ShortPayAssumptions"];
            /** Vendor Daily Cap Cents */
            vendor_daily_cap_cents: number;
            /** Version */
            version: number;
        };
        /** MatchReport */
        MatchReport: {
            /** Accepted Qty */
            accepted_qty: number;
            /** Accepted Value Cents */
            accepted_value_cents: number;
            /** Billed Cents */
            billed_cents: number;
            /** Billed Qty */
            billed_qty: number;
            /** Billed Unit Price Cents */
            billed_unit_price_cents: number;
            /** Duplicate Of */
            duplicate_of: string | null;
            /** Fuzzy Duplicate Of */
            fuzzy_duplicate_of: string | null;
            /** Grn Id */
            grn_id: string;
            /** Invoice Number Norm */
            invoice_number_norm: string | null;
            /** Mismatched Fields */
            mismatched_fields: string[];
            /** Ordered Qty */
            ordered_qty: number;
            /** Payee Lookalike */
            payee_lookalike: boolean;
            /** Payee Matches Vendor Master */
            payee_matches_vendor_master: boolean;
            /** Paypal Invoice Id */
            paypal_invoice_id: string | null;
            /** Paypal Invoice Matches Pdf */
            paypal_invoice_matches_pdf: boolean;
            /** Paypal Invoice Status */
            paypal_invoice_status: string | null;
            /** Po Id */
            po_id: string;
            /** Po Remaining Cents */
            po_remaining_cents: number;
            /** Po Unit Price Cents */
            po_unit_price_cents: number;
            /** Po Version */
            po_version: number;
            /** Precision Ok */
            precision_ok: boolean;
            /** Vendor Age Days */
            vendor_age_days: number;
            /** Vendor Committed Today Cents */
            vendor_committed_today_cents: number;
            /** Vendor Invoices Today */
            vendor_invoices_today: number;
            /** Vendor Version */
            vendor_version: number;
        };
        /** OperatorView */
        OperatorView: {
            /** Operator Id */
            operator_id: string;
            /**
             * Role
             * @enum {string}
             */
            role: "controller" | "cfo" | "vendor_admin" | "receiver" | "admin";
        };
        /** POConsumptionView */
        POConsumptionView: {
            /** Accepted Value Cents */
            accepted_value_cents: number;
            /** Paid Cents */
            paid_cents: number;
            /** Reserved Cents */
            reserved_cents: number;
            /** This Payable Cents */
            this_payable_cents: number;
        };
        /** PayPalInvoiceView */
        PayPalInvoiceView: {
            /** Billed Qty */
            billed_qty: number;
            /** Invoice Id */
            invoice_id: string;
            /** Payee Reference */
            payee_reference: string;
            /** Status */
            status: string;
            /** Total Cents */
            total_cents: number;
            /** Unit Price Cents */
            unit_price_cents: number;
        };
        /** PurchaseOrderView */
        PurchaseOrderView: {
            /** Description */
            description: string;
            /** Ordered Qty */
            ordered_qty: number;
            /** Po Id */
            po_id: string;
            /** Unit Price Cents */
            unit_price_cents: number;
            /** Version */
            version: number;
        };
        /** RiskAssessment */
        RiskAssessment: {
            /** Contributions */
            contributions: components["schemas"]["RiskContribution"][];
            /** Score */
            score: number;
        };
        /** RiskContribution */
        RiskContribution: {
            /** Finding */
            finding: string;
            /** Points */
            points: number;
        };
        /** SettlementEventView */
        SettlementEventView: {
            /** Occurred At */
            occurred_at: string | null;
            /** Paypal Id */
            paypal_id: string | null;
            /** Simulated */
            simulated: boolean;
            /**
             * Status
             * @enum {string}
             */
            status: "RECEIVED" | "ANALYZING" | "FROZEN" | "REJECTED" | "DUPLICATE_BLOCKED" | "APPROVED_FOR_PAYMENT" | "PAYOUT_SENDING" | "PAYOUT_SUBMITTED" | "PAYOUT_SUCCEEDED" | "PAYOUT_FAILED" | "SETTLED" | "NEEDS_MANUAL_REVIEW";
            /** Verified */
            verified: boolean;
        };
        /** ShortPayAssumptions */
        ShortPayAssumptions: {
            /**
             * Annual Cost Of Capital
             * @default 0.1
             */
            annual_cost_of_capital: number;
            /**
             * Claim Days
             * @default 45
             */
            claim_days: number;
            /**
             * Claim Ops Cost Cents
             * @default 3500
             */
            claim_ops_cost_cents: number;
            /**
             * Contest Cost Cents
             * @default 10000
             */
            contest_cost_cents: number;
            /**
             * Notice Ops Cost Cents
             * @default 500
             */
            notice_ops_cost_cents: number;
            /**
             * P Recover
             * @default 0.6
             */
            p_recover: number;
            /**
             * P Vendor Contests
             * @default 0.1
             */
            p_vendor_contests: number;
        };
        /** ShortPayDecision */
        ShortPayDecision: {
            assumptions: components["schemas"]["ShortPayAssumptions"];
            /**
             * Chosen
             * @enum {string}
             */
            chosen: "SHORT_PAY" | "PAY_AND_CLAIM" | "NOT_APPLICABLE";
            /** Delta Cents */
            delta_cents: number;
            /** Expected Cost Pay And Claim Cents */
            expected_cost_pay_and_claim_cents: number;
            /** Expected Cost Short Pay Cents */
            expected_cost_short_pay_cents: number;
        };
        /**
         * StreamEvent
         * @description Minimal state notification; clients fetch authorized case details over REST (R25).
         */
        StreamEvent: {
            /** Execution Id */
            execution_id: string;
            /** Name */
            name: string;
            /** Status */
            status: string;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /**
             * Type
             * @enum {string}
             */
            type: "node" | "settlement" | "webhook";
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** VendorView */
        VendorView: {
            /** Change Approved By */
            change_approved_by: string | null;
            /** Change Effective At */
            change_effective_at: string | null;
            /** Change Requested By */
            change_requested_by: string | null;
            /** Name */
            name: string;
            /** Onboarded At */
            onboarded_at: string;
            /** Paypal Payer Id */
            paypal_payer_id: string;
            /** Vendor Id */
            vendor_id: string;
            /** Version */
            version: number;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    list_executions: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExecutionRow"][];
                };
            };
            /** @description Case service/auth not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["APIError"];
                };
            };
        };
    };
    get_execution: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                execution_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CaseDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Case service/auth not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["APIError"];
                };
            };
        };
    };
    submit_decision: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                execution_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DecisionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DecisionReceipt"];
                };
            };
            /** @description Case changed; review again */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["APIError"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Case service/auth not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["APIError"];
                };
            };
        };
    };
    health_api_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    execution_stream: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Each SSE data field is a StreamEvent JSON object */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/event-stream": components["schemas"]["StreamEvent"];
                };
            };
            /** @description Authenticated stream not implemented */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["APIError"];
                };
            };
        };
    };
    simulator_api_webhooks_paypal_simulator__account__post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                account: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    treasury_api_webhooks_paypal_treasury_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
    vendor_api_webhooks_paypal_vendor_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
