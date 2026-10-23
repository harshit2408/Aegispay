"""SPIKE-9 (PRD §18, ADR-005): dynamic interrupt() + AsyncPostgresSaver survives a process restart. No LLM.
Process 1 runs a minimal graph to the interrupt and hard-exits (os._exit, no cleanup). Process 2, a fresh Python
process, reads the frozen thread, resumes with Command(resume=approval_id) and completes. Each node writes a
side-effect row (with its pid) to Postgres, so the script records exactly what re-runs on resume.
Needs: pip install -r requirements-spike9.txt (separate venv) and Postgres (docker compose up -d postgres).
Run (password from env, not argv; values from the compose postgres service, localhost only):
  PGPASSWORD=... SPIKE9_DB_URL=postgresql://aegis_api@localhost:5432/aegis python spike9_checkpoint.py  (exit 0 on pass)"""

import asyncio
import os
import subprocess
import sys
import uuid
from typing import TypedDict

try:
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt
    from psycopg.conninfo import conninfo_to_dict
except ImportError as exc:
    sys.exit(f"missing dependency ({exc}); pip install -r requirements-spike9.txt")

DSN = os.environ.get("SPIKE9_DB_URL", "")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ROLES = ("p1", "p2")
SPAWN_TIMEOUT_S = 60
PAYABLE_CENTS = 1234
APPROVAL_ID = "appr_spike9"
# Expected side effects: (node, phase) -> list of process roles that ran it.
EXPECTED = {
    ("prepare", "run"): ["p1"],
    ("review", "before_interrupt"): ["p1", "p2"],  # interrupted node re-runs from the top on resume
    ("review", "after_interrupt"): ["p2"],
    ("finish", "run"): ["p2"],
}


class State(TypedDict, total=False):
    execution_id: str
    payable_cents: int
    approval_id: str
    done: bool


async def effect(thread_id: str, node: str, phase: str) -> None:
    async with await psycopg.AsyncConnection.connect(DSN, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO spike9_effects (thread_id, node, phase, pid) VALUES (%s, %s, %s, %s)",
            (thread_id, node, phase, os.getpid()),
        )


async def prepare(state: State) -> dict:
    await effect(state["execution_id"], "prepare", "run")
    return {"payable_cents": PAYABLE_CENTS}


async def review(state: State) -> dict:
    await effect(state["execution_id"], "review", "before_interrupt")
    approval_id = interrupt({"execution_id": state["execution_id"], "reasons": ["SPIKE"]})
    if not isinstance(approval_id, str):
        raise ValueError("resume payload must be an approval_id")
    await effect(state["execution_id"], "review", "after_interrupt")
    return {"approval_id": approval_id}


async def finish(state: State) -> dict:
    await effect(state["execution_id"], "finish", "run")
    return {"done": True}


def build(checkpointer):
    g = StateGraph(State)
    g.add_node("prepare", prepare)
    g.add_node("review", review)
    g.add_node("finish", finish)
    g.add_edge(START, "prepare")
    g.add_edge("prepare", "review")
    g.add_edge("review", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


async def process_1(thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncPostgresSaver.from_conn_string(DSN) as saver:
        await saver.setup()
        out = await build(saver).ainvoke({"execution_id": thread_id}, config)
        interrupts = out.get("__interrupt__") or []
        print(f"p1 pid={os.getpid()} interrupts={[i.value for i in interrupts]}", flush=True)
        if len(interrupts) != 1:
            os._exit(2)
        # hard exit inside the saver context: no graceful close, like a killed container
        os._exit(0)


async def process_2(thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id}}
    async with AsyncPostgresSaver.from_conn_string(DSN) as saver:
        graph = build(saver)
        snap = await graph.aget_state(config)
        pending = [i.value for t in snap.tasks for i in t.interrupts]
        print(f"p2 pid={os.getpid()} next={snap.next} values={snap.values} pending={pending}")
        if snap.next != ("review",) or snap.values.get("payable_cents") != PAYABLE_CENTS:
            raise RuntimeError(f"frozen thread not restored: next={snap.next} values={snap.values}")
        if not pending or pending[0]["execution_id"] != thread_id:
            raise RuntimeError(f"interrupt payload lost: {pending}")
        out = await graph.ainvoke(Command(resume=APPROVAL_ID), config)
        print(f"p2 final={out}")
        if not (out.get("done") is True and out.get("approval_id") == APPROVAL_ID):
            raise RuntimeError(f"resume did not complete: {out}")
        if out.get("payable_cents") != PAYABLE_CENTS:
            raise RuntimeError(f"state changed across resume: {out}")
        if (await graph.aget_state(config)).next != ():
            raise RuntimeError("thread should be finished")


def spawn(role: str, thread_id: str) -> int:
    try:
        r = subprocess.run(
            [sys.executable, __file__, role, thread_id], check=False, timeout=SPAWN_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        print(f"FAIL: {role} timed out after {SPAWN_TIMEOUT_S}s")
        return 124
    return r.returncode


def orchestrate() -> int:
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS spike9_effects (id serial PRIMARY KEY, thread_id text,"
            " node text, phase text, pid int, at timestamptz DEFAULT now())"
        )
    thread_id = f"spike9-{uuid.uuid4()}"
    print(f"thread_id={thread_id}", flush=True)
    rc1 = spawn("p1", thread_id)
    if rc1 != 0:
        print(f"FAIL: process 1 exit {rc1}")
        return 1
    rc2 = spawn("p2", thread_id)
    with psycopg.connect(DSN) as conn:
        rows = conn.execute(
            "SELECT node, phase, pid FROM spike9_effects WHERE thread_id = %s ORDER BY id",
            (thread_id,),
        ).fetchall()
    pids = list(dict.fromkeys(pid for _, _, pid in rows))  # first-seen order: p1, then p2
    role = {pid: f"p{i + 1}" for i, pid in enumerate(pids)}
    seen: dict = {}
    for node, phase, pid in rows:
        seen.setdefault((node, phase), []).append(role[pid])
    print("side effects:", [(n, ph, role[p]) for n, ph, p in rows])
    ok = rc2 == 0 and len(pids) == 2 and seen == EXPECTED
    print("PASS" if ok else f"FAIL: p2 exit {rc2}, effects {seen} != {EXPECTED}")
    return 0 if ok else 1


def main() -> int:
    if not DSN:
        sys.exit("set SPIKE9_DB_URL (postgresql://...) for the compose postgres service")
    host = conninfo_to_dict(DSN).get("host")
    if host not in LOCAL_HOSTS:
        sys.exit(f"refusing non-local database host {host!r}: this probe writes test rows")
    if len(sys.argv) == 3:
        if sys.argv[1] not in ROLES:
            sys.exit(f"role must be one of {ROLES}")
        step = process_1 if sys.argv[1] == "p1" else process_2
        # psycopg async cannot use the Windows default ProactorEventLoop
        asyncio.run(step(sys.argv[2]), loop_factory=asyncio.SelectorEventLoop)
        return 0
    return orchestrate()


if __name__ == "__main__":
    sys.exit(main())
