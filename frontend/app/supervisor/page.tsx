"use client";

import Image from "next/image";
import { FormEvent, useEffect, useState } from "react";
import {
  ApiError,
  AuditStatus,
  Escalation,
  decideEscalation,
  getEscalations,
  resetDemo,
  verifyAudit,
} from "@/app/lib/api";
import { EscalationCard } from "@/app/components/EscalationCard";
import { Logo } from "@/app/components/Logo";

function errorMessage(err: unknown, fallback: string) {
  if (err instanceof ApiError && err.message) return err.message;
  return fallback;
}

export default function SupervisorPage() {
  const [passcode, setPasscode] = useState("");
  const [authed, setAuthed] = useState(false);
  const [authing, setAuthing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);

  const [pending, setPending] = useState<Escalation[]>([]);
  const [resolved, setResolved] = useState<Escalation[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [audit, setAudit] = useState<AuditStatus | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [resetting, setResetting] = useState(false);

  const selected = [...pending, ...resolved].find((e) => e.id === selectedId) ?? null;

  async function refresh(code: string) {
    try {
      const [list, auditStatus] = await Promise.all([getEscalations(code), verifyAudit(code)]);
      setPending(list.pending);
      setResolved(list.resolved);
      setAudit(auditStatus);
      setListError(null);
    } catch (err) {
      setListError(errorMessage(err, "Couldn't refresh escalations."));
    }
  }

  async function handlePasscodeSubmit(e: FormEvent) {
    e.preventDefault();
    setAuthing(true);
    setAuthError(null);
    try {
      const [list, auditStatus] = await Promise.all([getEscalations(passcode), verifyAudit(passcode)]);
      setPending(list.pending);
      setResolved(list.resolved);
      setAudit(auditStatus);
      setAuthed(true);
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? "Incorrect passcode."
          : errorMessage(err, "Couldn't reach the server.");
      setAuthError(message);
    } finally {
      setAuthing(false);
    }
  }

  useEffect(() => {
    if (!authed) return;
    const interval = setInterval(() => refresh(passcode), 10000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed]);

  async function handleDecide(id: number, decision: "approve" | "deny" | "complete", note: string) {
    try {
      await decideEscalation(id, decision, note, passcode);
      await refresh(passcode);
    } catch (err) {
      setListError(errorMessage(err, "Couldn't submit the decision."));
    }
  }

  async function handleReset() {
    if (!window.confirm("Reset the demo? This re-seeds the database and clears all sessions.")) return;
    setResetting(true);
    try {
      await resetDemo(passcode);
      setSelectedId(null);
      await refresh(passcode);
    } catch (err) {
      setListError(errorMessage(err, "Couldn't reset the demo."));
    } finally {
      setResetting(false);
    }
  }

  if (!authed) {
    return (
      <main className="relative flex min-h-screen flex-1 flex-col overflow-hidden">
        <Image
          src="/plane-hero.png"
          alt=""
          fill
          priority
          sizes="100vw"
          className="object-cover"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-black/20 to-accent/30" />

        <div className="relative z-10 flex flex-1 items-center justify-center p-4">
          <div className="w-full max-w-sm rounded-2xl bg-card p-8 shadow-xl">
            <div className="flex items-center gap-3">
              <Logo />
              <div>
                <h1 className="text-xl font-semibold">Supervisor console</h1>
                <p className="text-xs text-muted-foreground">SkyAssist</p>
              </div>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">Enter the passcode to review escalations.</p>
            <form onSubmit={handlePasscodeSubmit} className="mt-6 flex flex-col gap-4">
              <div>
                <label
                  htmlFor="passcode"
                  className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
                >
                  Passcode
                </label>
                <input
                  id="passcode"
                  type="password"
                  value={passcode}
                  onChange={(e) => setPasscode(e.target.value)}
                  required
                  className="mt-1 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </div>
              {authError && <p className="text-sm text-danger">{authError}</p>}
              <button
                type="submit"
                disabled={authing}
                className="rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground transition hover:bg-accent-hover disabled:opacity-50"
              >
                {authing ? "Checking…" : "Enter"}
              </button>
            </form>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen flex-1 flex-col">
      <header className="flex items-center gap-2.5 border-b border-border bg-card px-4 py-3 sm:px-6">
        <Logo size="sm" />
        <div>
          <p className="text-sm font-semibold">Supervisor console</p>
          <p className="text-xs text-muted-foreground">
            {pending.length} pending · {resolved.length} resolved
          </p>
        </div>
      </header>

      {listError && (
        <p className="border-b border-border bg-danger-soft px-4 py-2 text-xs text-danger sm:px-6">
          {listError}
        </p>
      )}

      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4 sm:p-6 lg:grid lg:grid-cols-[360px_1fr] lg:overflow-hidden">
        <div className="flex flex-col gap-4 lg:overflow-y-auto lg:pr-2">
          <section className="flex flex-col gap-2">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Pending
            </h2>
            {pending.length === 0 ? (
              <p className="text-sm text-muted-foreground">Nothing waiting.</p>
            ) : (
              pending.map((e) => (
                <EscalationCard
                  key={e.id}
                  escalation={e}
                  selected={e.id === selectedId}
                  onClick={() => setSelectedId(e.id)}
                />
              ))
            )}
          </section>

          {resolved.length > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Resolved
              </h2>
              {resolved.map((e) => (
                <EscalationCard
                  key={e.id}
                  escalation={e}
                  selected={e.id === selectedId}
                  onClick={() => setSelectedId(e.id)}
                />
              ))}
            </section>
          )}
        </div>

        <div className="lg:overflow-y-auto lg:pl-2">
          {selected ? (
            <EscalationCard
              escalation={selected}
              detail
              onDecide={(decision, note) => handleDecide(selected.id, decision, note)}
            />
          ) : (
            <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              Select an escalation to see the full handoff packet.
            </div>
          )}
        </div>
      </div>

      <footer className="flex items-center justify-between border-t border-border bg-card px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">Audit chain</span>
          {audit ? (
            <span
              className={`rounded-full px-2.5 py-0.5 font-medium ${
                audit.valid ? "bg-success-soft text-success" : "bg-danger-soft text-danger"
              }`}
            >
              {audit.valid ? `valid · ${audit.entries} entries` : "broken"}
            </span>
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
        </div>
        <button
          type="button"
          onClick={handleReset}
          disabled={resetting}
          className="rounded-full border border-danger px-4 py-1.5 text-xs font-medium text-danger transition hover:bg-danger-soft disabled:opacity-50"
        >
          {resetting ? "Resetting…" : "Reset demo"}
        </button>
      </footer>
    </main>
  );
}
