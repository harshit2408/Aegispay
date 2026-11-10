"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import type { components } from "@/lib/api";
import { APIError, api } from "@/lib/client";

type Session = components["schemas"]["SessionView"];
const SessionContext = createContext<{
  operator: Session | null; busy: boolean; refresh: () => Promise<void>;
}>({ operator: null, busy: true, refresh: async () => {} });

export const useSession = () => useContext(SessionContext);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [operator, setOperator] = useState<Session | null>(null);
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState("");
  const [operatorId, setOperatorId] = useState("");
  const [password, setPassword] = useState("");
  const generation = useRef(0);

  const readSession = useCallback(() => {
    const current = ++generation.current;
    return api.session().then((session) => {
      if (current === generation.current) { setOperator(session); setMessage(""); }
    }).catch((error: unknown) => {
      if (current === generation.current) {
        setOperator(null);
        setMessage(error instanceof APIError && error.status === 401 ? "" : "Session service unavailable. Retry to sign in.");
      }
    }).finally(() => { if (current === generation.current) setBusy(false); });
  }, []);

  const refresh = useCallback(async () => {
    setBusy(true);
    await readSession();
  }, [readSession]);

  useEffect(() => {
    void readSession();
    return () => { generation.current += 1; };
  }, [readSession]);

  async function login(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const current = ++generation.current;
    setBusy(true); setMessage("");
    try {
      const session = await api.login(operatorId.trim(), password);
      if (current === generation.current) { setOperator(session); setPassword(""); setMessage("Signed in."); }
    } catch (error) {
      if (current === generation.current) { setOperator(null); setPassword(""); setMessage(error instanceof Error ? error.message : "Sign in failed."); }
    } finally { if (current === generation.current) setBusy(false); }
  }

  async function logout() {
    if (!operator || busy) return;
    const current = ++generation.current;
    setBusy(true); setMessage("");
    try {
      await api.logout(operator.csrf_token);
      if (current === generation.current) { setOperator(null); setMessage("Signed out."); }
    } catch (error) {
      if (error instanceof APIError && error.status === 401) {
        if (current === generation.current) { setOperator(null); setMessage("Session expired."); }
      } else if (current === generation.current) setMessage("Sign out failed. Retry to end the session.");
    } finally { if (current === generation.current) setBusy(false); }
  }

  return <SessionContext.Provider value={{ operator, busy, refresh }}>
    <section className="session-panel" aria-label="Operator session" aria-busy={busy}>
      {operator ? <><p><strong>{operator.operator_id}</strong> · {operator.role}</p><button type="button" disabled={busy} onClick={() => void logout()}>Sign out</button></>
        : <details><summary>Operator sign in</summary><form className="decision-form" onSubmit={login}>
          <label htmlFor="operator-id">Operator ID</label><input id="operator-id" autoComplete="username" required maxLength={128} value={operatorId} onChange={(event) => setOperatorId(event.target.value)} disabled={busy} />
          <label htmlFor="operator-password">Password</label><input id="operator-password" type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={(event) => setPassword(event.target.value)} disabled={busy} />
          <button type="submit" disabled={busy}>{busy ? "Checking session…" : "Sign in"}</button>
        </form></details>}
      {message && <p role="status">{message}</p>}
      {!operator && !busy && <button type="button" onClick={() => void refresh()}>Check session</button>}
    </section>
    {children}
  </SessionContext.Provider>;
}
