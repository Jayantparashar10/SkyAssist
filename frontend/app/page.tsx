"use client";

import Image from "next/image";
import { FormEvent, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  ApiError,
  ActionRecord,
  Choice,
  Escalation,
  Message,
  SessionResponse,
  getSession,
  login,
  sendChoice,
  sendMessage,
} from "@/app/lib/api";
import { ChatBubble, TypingIndicator } from "@/app/components/ChatBubble";
import { ChoiceButtons } from "@/app/components/ChoiceButtons";
import { ActionPanel } from "@/app/components/ActionPanel";
import { Logo } from "@/app/components/Logo";

const DEMO_PNRS = [
  { pnr: "SK4821X", name: "Priya Nair" },
  { pnr: "TR1190B", name: "Arvind Kulkarni" },
  { pnr: "WL7742", name: "Meher Kaur" },
];

// Picked once per page load, client-only — the server snapshot stays null
// so SSR output matches the first client render before hydration picks one.
let cachedDemoHint: (typeof DEMO_PNRS)[number] | null = null;
function getClientDemoHint() {
  if (!cachedDemoHint) {
    cachedDemoHint = DEMO_PNRS[Math.floor(Math.random() * DEMO_PNRS.length)];
  }
  return cachedDemoHint;
}
function getServerDemoHint() {
  return null;
}
function subscribeNever() {
  return () => {};
}

function errorMessage(err: unknown, fallback: string) {
  if (err instanceof ApiError && err.message) return err.message;
  return fallback;
}

function mergeById<T extends { id: number }>(existing: T[], incoming: T[]): T[] {
  const map = new Map(existing.map((item) => [item.id, item]));
  for (const item of incoming) map.set(item.id, item);
  return Array.from(map.values()).sort((a, b) => a.id - b.id);
}

function tabClass(active: boolean) {
  return `rounded-full px-3 py-1 text-xs font-medium transition ${
    active ? "bg-accent text-accent-foreground" : "text-muted-foreground"
  }`;
}

export default function CustomerPage() {
  const [pnr, setPnr] = useState("");
  const [lastName, setLastName] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const demoHint = useSyncExternalStore(subscribeNever, getClientDemoHint, getServerDemoHint);

  const [session, setSession] = useState<SessionResponse | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [actions, setActions] = useState<ActionRecord[]>([]);
  const [escalations, setEscalations] = useState<Escalation[]>([]);

  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState<string | null>(null);
  const [mobileView, setMobileView] = useState<"chat" | "details">("chat");

  const scrollRef = useRef<HTMLDivElement>(null);
  const sendingRef = useRef(sending);

  useEffect(() => {
    sendingRef.current = sending;
  }, [sending]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, sending]);

  // Polls the session every 5s so supervisor decisions show up without a
  // refresh. sendingRef skips applying poll results while a send is in
  // flight, so a stale snapshot can't overwrite an optimistic local message.
  useEffect(() => {
    if (!session) return;
    let cancelled = false;

    const poll = async () => {
      if (sendingRef.current) return;
      try {
        const detail = await getSession(session.session_id);
        if (cancelled || sendingRef.current) return;
        setMessages(detail.messages);
        setActions(detail.actions);
        setEscalations(detail.escalations);
      } catch {
        // Silent: transient poll failures shouldn't interrupt an active chat.
      }
    };

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [session]);

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setLoggingIn(true);
    setLoginError(null);
    try {
      const res = await login(pnr.trim(), lastName.trim());
      setSession(res);
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 401
          ? "That PNR and last name don't match a booking."
          : errorMessage(err, "Couldn't sign in. Please try again.");
      setLoginError(message);
    } finally {
      setLoggingIn(false);
    }
  }

  function applyChatResponse(res: {
    reply: string;
    choices: Choice[];
    actions: ActionRecord[];
    escalations: Escalation[];
  }) {
    setMessages((prev) => [
      ...prev,
      {
        id: Date.now(),
        role: "agent",
        content: res.reply,
        created_at: new Date().toISOString(),
      },
    ]);
    setChoices(res.choices);
    setActions((prev) => mergeById(prev, res.actions));
    setEscalations((prev) => mergeById(prev, res.escalations));
  }

  async function handleSend(e: FormEvent) {
    e.preventDefault();
    if (!session || sending) return;
    const text = draft.trim();
    if (!text) return;

    setDraft("");
    setChatError(null);
    setChoices([]);
    setMessages((prev) => [
      ...prev,
      { id: -Date.now(), role: "customer", content: text, created_at: new Date().toISOString() },
    ]);
    setSending(true);
    try {
      const res = await sendMessage(session.session_id, text);
      applyChatResponse(res);
    } catch (err) {
      setChatError(errorMessage(err, "Couldn't reach the agent. Please try again."));
    } finally {
      setSending(false);
    }
  }

  async function handleChoice(choiceId: string) {
    if (!session || sending) return;
    const chosen = choices.find((c) => c.choice_id === choiceId);
    setChatError(null);
    setChoices([]);
    if (chosen) {
      setMessages((prev) => [
        ...prev,
        {
          id: -Date.now(),
          role: "customer",
          content: chosen.label,
          created_at: new Date().toISOString(),
        },
      ]);
    }
    setSending(true);
    try {
      const res = await sendChoice(session.session_id, choiceId);
      applyChatResponse(res);
    } catch (err) {
      setChatError(errorMessage(err, "Couldn't reach the agent. Please try again."));
    } finally {
      setSending(false);
    }
  }

  if (!session) {
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
                <h1 className="text-xl font-semibold">SkyAssist</h1>
                <p className="text-xs text-muted-foreground">Disruption resolution</p>
              </div>
            </div>
            <p className="mt-4 text-sm text-muted-foreground">
              Sign in with your booking to get help with a delay or cancellation.
            </p>
            <form onSubmit={handleLogin} className="mt-6 flex flex-col gap-4">
              <div>
                <label
                  htmlFor="pnr"
                  className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
                >
                  PNR
                </label>
                <input
                  id="pnr"
                  value={pnr}
                  onChange={(e) => setPnr(e.target.value)}
                  placeholder="SK4821X"
                  required
                  className="mt-1 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </div>
              <div>
                <label
                  htmlFor="lastName"
                  className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
                >
                  Last name
                </label>
                <input
                  id="lastName"
                  value={lastName}
                  onChange={(e) => setLastName(e.target.value)}
                  placeholder="Nair"
                  required
                  className="mt-1 w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
                />
              </div>
              {loginError && <p className="text-sm text-danger">{loginError}</p>}
              <button
                type="submit"
                disabled={loggingIn}
                className="rounded-full bg-accent px-4 py-2.5 text-sm font-medium text-accent-foreground transition hover:bg-accent-hover disabled:opacity-50"
              >
                {loggingIn ? "Signing in…" : "Continue"}
              </button>
            </form>
            {demoHint && (
              <div className="mt-6 rounded-xl bg-background px-3 py-2 text-center text-xs text-muted-foreground">
                Demo PNR: <span className="font-medium text-foreground">{demoHint.pnr}</span> —{" "}
                {demoHint.name}
              </div>
            )}
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="flex min-h-screen flex-1 flex-col">
      <header className="flex items-center justify-between border-b border-border bg-card px-4 py-3 sm:px-6">
        <div className="flex items-center gap-2.5">
          <Logo size="sm" />
          <div>
            <p className="text-sm font-semibold">SkyAssist</p>
            <p className="text-xs text-muted-foreground">
              {session.customer.name} · {session.customer.pnr}
            </p>
          </div>
        </div>
        <div className="flex gap-1 rounded-full bg-background p-1 lg:hidden">
          <button type="button" onClick={() => setMobileView("chat")} className={tabClass(mobileView === "chat")}>
            Chat
          </button>
          <button
            type="button"
            onClick={() => setMobileView("details")}
            className={tabClass(mobileView === "details")}
          >
            Details
          </button>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden lg:grid lg:grid-cols-[1fr_380px]">
        <section className={`flex-col ${mobileView === "chat" ? "flex" : "hidden"} lg:flex`}>
          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4 sm:p-6">
            {messages.map((m) => (
              <ChatBubble key={m.id} message={m} />
            ))}
            {sending && <TypingIndicator />}
          </div>

          {choices.length > 0 && (
            <div className="px-4 pb-3 sm:px-6">
              <ChoiceButtons choices={choices} onSelect={handleChoice} disabled={sending} />
            </div>
          )}

          {chatError && <p className="px-4 pb-2 text-xs text-danger sm:px-6">{chatError}</p>}

          <form onSubmit={handleSend} className="flex gap-2 border-t border-border bg-card p-4 sm:p-6">
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Type a message…"
              disabled={sending}
              className="flex-1 rounded-full border border-border bg-background px-4 py-2.5 text-sm outline-none focus:border-accent disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={sending || !draft.trim()}
              className="rounded-full bg-accent px-5 py-2.5 text-sm font-medium text-accent-foreground transition hover:bg-accent-hover disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </section>

        <aside
          className={`border-l border-border bg-background ${
            mobileView === "details" ? "flex" : "hidden"
          } flex-col lg:flex`}
        >
          <ActionPanel
            customer={session.customer}
            bookings={session.bookings}
            actions={actions}
            escalations={escalations}
          />
        </aside>
      </div>
    </main>
  );
}
