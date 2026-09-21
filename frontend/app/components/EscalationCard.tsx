"use client";

import { useState } from "react";
import type { Escalation } from "@/app/lib/api";

const STATUS_STYLES: Record<Escalation["status"], string> = {
  pending: "bg-warning-soft text-warning",
  approved: "bg-success-soft text-success",
  denied: "bg-danger-soft text-danger",
  completed: "bg-success-soft text-success",
};

const KIND_LABELS: Record<Escalation["kind"], string> = {
  approval: "Approval",
  handoff: "Handoff",
  immediate: "Immediate",
};

export function StatusPill({ status }: { status: Escalation["status"] }) {
  return (
    <span
      className={`rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}

function KindBadge({ kind }: { kind: Escalation["kind"] }) {
  return (
    <span className="rounded-full bg-background px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
      {KIND_LABELS[kind]}
    </span>
  );
}

function elapsed(from: string, to: number) {
  const ms = to - new Date(from).getTime();
  const mins = Math.max(0, Math.round(ms / 60000));
  if (mins < 60) return `${mins}m`;
  return `${Math.round(mins / 60)}h ${mins % 60}m`;
}

/**
 * Compact card: used in the customer action panel and the supervisor list.
 * Pass `onDecide` + `detail` to render the full handoff packet with
 * Approve/Deny (approval/immediate kind) or Mark complete (handoff kind)
 * controls (supervisor console detail pane).
 */
export function EscalationCard({
  escalation,
  detail = false,
  onDecide,
  selected,
  onClick,
  onViewTranscript,
}: {
  escalation: Escalation;
  detail?: boolean;
  onDecide?: (decision: "approve" | "deny" | "complete", note: string) => void;
  selected?: boolean;
  onClick?: () => void;
  onViewTranscript?: (sessionId: string) => void;
}) {
  const [note, setNote] = useState(escalation.supervisor_note ?? "");
  const [submitting, setSubmitting] = useState<"approve" | "deny" | "complete" | null>(null);

  if (!detail) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`w-full rounded-xl border px-4 py-3 text-left transition ${
          selected ? "border-accent bg-accent-soft" : "border-border bg-card hover:border-accent/50"
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-medium text-foreground">{escalation.packet.customer.name}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{escalation.reason}</p>
          </div>
          <div className="flex flex-col items-end gap-1">
            <StatusPill status={escalation.status} />
            <KindBadge kind={escalation.kind} />
          </div>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          {escalation.rule_id} ·{" "}
          {escalation.status === "pending" || !escalation.resolved_at
            ? `waiting ${elapsed(escalation.created_at, Date.now())}`
            : `resolved in ${elapsed(escalation.created_at, new Date(escalation.resolved_at).getTime())}`}
        </p>
      </button>
    );
  }

  const packet = escalation.packet;
  const isPending = escalation.status === "pending";
  const isHandoff = escalation.kind === "handoff";

  const submit = (decision: "approve" | "deny" | "complete") => {
    setSubmitting(decision);
    onDecide?.(decision, note);
  };

  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-base font-semibold">{packet.customer.name}</h3>
          <p className="text-sm text-muted-foreground">
            {packet.customer.tier} · {packet.customer.pnr}
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusPill status={escalation.status} />
          <KindBadge kind={escalation.kind} />
        </div>
      </div>

      <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Booking</dt>
          <dd className="mt-0.5">{packet.booking}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Rule</dt>
          <dd className="mt-0.5">{escalation.rule_id}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Already done
          </dt>
          <dd className="mt-0.5">
            {packet.already_done.length ? (
              <ul className="list-inside list-disc space-y-0.5">
                {packet.already_done.map((item, i) => (
                  <li key={i}>{item}</li>
                ))}
              </ul>
            ) : (
              <span className="text-muted-foreground">Nothing yet</span>
            )}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Requested</dt>
          <dd className="mt-0.5">{packet.requested}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Blocked by
          </dt>
          <dd className="mt-0.5 text-danger">{packet.blocked_by}</dd>
        </div>
        <div>
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Sentiment
          </dt>
          <dd className="mt-0.5 capitalize">{packet.sentiment}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Customer said
          </dt>
          <dd className="mt-0.5 italic text-foreground/80">&ldquo;{packet.customer_quote}&rdquo;</dd>
        </div>
        {packet.notes.length > 0 && (
          <div className="sm:col-span-2">
            <dt className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Notes</dt>
            <dd className="mt-0.5">
              <ul className="list-inside list-disc space-y-0.5">
                {packet.notes.map((n, i) => (
                  <li key={i}>{n}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      <button
        type="button"
        onClick={() => onViewTranscript?.(packet.transcript_session_id)}
        className="self-start text-xs font-medium text-accent hover:underline"
      >
        View full transcript →
      </button>

      {isPending ? (
        <div className="flex flex-col gap-3 border-t border-border pt-4">
          <label className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
            Note to customer
          </label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            placeholder="Optional note explaining the decision…"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
          />
          {isHandoff ? (
            <button
              type="button"
              disabled={submitting !== null}
              onClick={() => submit("complete")}
              className="rounded-full bg-success px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
            >
              {submitting === "complete" ? "Marking complete…" : "Mark complete"}
            </button>
          ) : (
            <div className="flex gap-3">
              <button
                type="button"
                disabled={submitting !== null}
                onClick={() => submit("approve")}
                className="flex-1 rounded-full bg-success px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {submitting === "approve" ? "Approving…" : "Approve"}
              </button>
              <button
                type="button"
                disabled={submitting !== null}
                onClick={() => submit("deny")}
                className="flex-1 rounded-full bg-danger px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:opacity-50"
              >
                {submitting === "deny" ? "Denying…" : "Deny"}
              </button>
            </div>
          )}
        </div>
      ) : (
        escalation.supervisor_note && (
          <div className="border-t border-border pt-4 text-sm">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Supervisor note
            </p>
            <p className="mt-0.5">{escalation.supervisor_note}</p>
          </div>
        )
      )}
    </div>
  );
}
