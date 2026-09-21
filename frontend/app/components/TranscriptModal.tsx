"use client";

import { useEffect, useState } from "react";
import { ApiError, getSession, type Message } from "@/app/lib/api";
import { ChatBubble } from "@/app/components/ChatBubble";

export function TranscriptModal({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSession(sessionId)
      .then((detail) => {
        if (!cancelled) setMessages(detail.messages);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof ApiError && err.message ? err.message : "Couldn't load the transcript.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-2xl bg-card shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-5 py-3">
          <h3 className="text-sm font-semibold">Full transcript</h3>
          <button
            type="button"
            onClick={onClose}
            className="text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            Close
          </button>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto p-5">
          {error && <p className="text-sm text-danger">{error}</p>}
          {!error && messages === null && <p className="text-sm text-muted-foreground">Loading…</p>}
          {messages?.length === 0 && <p className="text-sm text-muted-foreground">No messages yet.</p>}
          {messages?.map((m) => <ChatBubble key={m.id} message={m} />)}
        </div>
      </div>
    </div>
  );
}
