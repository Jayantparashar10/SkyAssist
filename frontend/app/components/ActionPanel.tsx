import type { ActionRecord, Booking, Customer, Escalation } from "@/app/lib/api";
import { EscalationCard } from "./EscalationCard";

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString([], {
      weekday: "short",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

const STATUS_STYLES: Record<Booking["status"], string> = {
  scheduled: "bg-accent-soft text-accent-hover",
  delayed: "bg-warning-soft text-warning",
  cancelled: "bg-danger-soft text-danger",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </h2>
      {children}
    </section>
  );
}

function formatParams(params: Record<string, unknown>) {
  return Object.entries(params)
    .map(([key, value]) => `${key.replace(/_/g, " ")}: ${value}`)
    .join(" · ");
}

export function ActionPanel({
  customer,
  bookings,
  actions,
  escalations,
}: {
  customer: Customer;
  bookings: Booking[];
  actions: ActionRecord[];
  escalations: Escalation[];
}) {
  return (
    <div className="flex h-full flex-col gap-6 overflow-y-auto p-4 sm:p-6">
      <Section title="Booking">
        <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <p className="font-semibold">{customer.name}</p>
            <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent-hover">
              {customer.tier}
            </span>
          </div>
          <p className="text-xs text-muted-foreground">PNR {customer.pnr}</p>
          {customer.history && (
            <p className="mt-1 text-xs text-muted-foreground">{customer.history}</p>
          )}
          <div className="mt-3 flex flex-col gap-2">
            {bookings.map((b) => (
              <div key={b.id} className="rounded-xl bg-background p-3 text-sm">
                <div className="flex items-center justify-between">
                  <span className="font-medium">
                    {b.flight_no ?? "Flight TBD"} · {b.origin} → {b.destination}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium capitalize ${STATUS_STYLES[b.status]}`}>
                    {b.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Scheduled {formatDate(b.sched_dep)}
                  {b.new_dep && <> · Now {formatDate(b.new_dep)}</>}
                </p>
              </div>
            ))}
          </div>
        </div>
      </Section>

      <Section title="Actions taken">
        {actions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing yet.</p>
        ) : (
          <ol className="flex flex-col gap-3 border-l border-border pl-4">
            {actions.map((a) => (
              <li key={a.id} className="relative">
                <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full bg-accent" />
                <div className="rounded-xl border border-border bg-card p-3 shadow-sm">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-medium capitalize">
                      {a.type.replace(/_/g, " ").toLowerCase()}
                    </p>
                    <span className="shrink-0 rounded-full bg-background px-2 py-0.5 text-[11px] font-mono text-muted-foreground">
                      {a.rule_id}
                    </span>
                  </div>
                  {Object.keys(a.params).length > 0 && (
                    <p className="mt-1 text-xs text-muted-foreground">{formatParams(a.params)}</p>
                  )}
                  <p className="mt-1 text-[11px] text-muted-foreground">{formatDate(a.created_at)}</p>
                </div>
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Escalations">
        {escalations.length === 0 ? (
          <p className="text-sm text-muted-foreground">None so far.</p>
        ) : (
          <div className="flex flex-col gap-2">
            {escalations.map((e) => (
              <EscalationCard key={e.id} escalation={e} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
