"""SPIKE-6 (PRD §18): PayPal Agent Toolkit (Python) tools inside a LangGraph ToolNode, read-only config. No LLM.
Needs: pip install -r requirements-spike6.txt (use a separate venv). Pass: only get_invoice/list_invoices exposed,
get_invoice returns data through the graph, create_invoice is rejected. Run: python spike6_toolkit.py"""

import json
import sys

from common import Sandbox

READ_ONLY = {"invoices": {"list": True, "get": True}}
WRITE_HINTS = (
    "create",
    "send",
    "cancel",
    "remind",
    "update",
    "delete",
    "record",
    "capture",
    "pay",
    "refund",
)


def main() -> int:
    try:
        from langchain_core.messages import AIMessage
        from langgraph.graph import END, START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode
        from paypal_agent_toolkit.langchain.toolkit import PayPalToolkit
        from paypal_agent_toolkit.shared.configuration import Configuration, Context
    except ImportError as exc:
        sys.exit(f"missing dependency ({exc}); pip install -r requirements-spike6.txt")

    sandbox = Sandbox()
    vendor_token, _ = sandbox.token("VENDOR")
    invoice_id = sandbox.new_invoice(vendor_token)

    toolkit = PayPalToolkit(
        sandbox.env["VENDOR_CLIENT_ID"],
        sandbox.env["VENDOR_CLIENT_SECRET"],
        Configuration(actions=READ_ONLY, context=Context(sandbox=True)),
    )
    names = sorted(t.name for t in toolkit.get_tools())
    write_like = [n for n in names if any(h in n for h in WRITE_HINTS)]
    print("exposed tools:", names, "| write-looking:", write_like)

    graph = StateGraph(MessagesState)
    graph.add_node("tools", ToolNode(toolkit.get_tools()))
    graph.add_edge(START, "tools")
    graph.add_edge("tools", END)
    app = graph.compile()

    read = app.invoke(
        {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_invoice", "args": {"invoice_id": invoice_id}, "id": "t1"}
                    ],
                )
            ]
        }
    )
    message = read["messages"][-1]
    body = json.loads(message.content) if isinstance(message.content, str) else {}
    print(
        "get_invoice via graph: status",
        getattr(message, "status", None),
        "| invoice status:",
        body.get("status"),
    )

    blocked = app.invoke(
        {
            "messages": [
                AIMessage(
                    content="", tool_calls=[{"name": "create_invoice", "args": {}, "id": "t2"}]
                )
            ]
        }
    )
    rejected = "not a valid tool" in str(blocked["messages"][-1].content)
    print("create_invoice via graph rejected:", rejected)

    ok = (
        names == ["get_invoice", "list_invoices"]
        and not write_like
        and bool(body.get("status"))
        and rejected
    )
    print("SPIKE-6", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
