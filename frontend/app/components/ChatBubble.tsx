import type { Message } from "@/app/lib/api";

function formatTime(iso: string) {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function ChatBubble({ message }: { message: Message }) {
  if (message.role === "system" || message.role === "supervisor") {
    return (
      <div className="flex justify-center py-1">
        <div className="max-w-[85%] rounded-full bg-warning-soft px-4 py-1.5 text-center text-xs font-medium text-foreground/80">
          {message.content}
        </div>
      </div>
    );
  }

  const isCustomer = message.role === "customer";

  return (
    <div className={`flex ${isCustomer ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[80%] ${isCustomer ? "items-end" : "items-start"} flex flex-col gap-1`}>
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed shadow-sm ${
            isCustomer
              ? "bg-accent text-accent-foreground rounded-br-sm"
              : "bg-card text-foreground rounded-bl-sm border border-border"
          }`}
        >
          {message.content}
        </div>
        <span className="px-1 text-[11px] text-muted-foreground">
          {formatTime(message.created_at)}
        </span>
      </div>
    </div>
  );
}

export function TypingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-sm border border-border bg-card px-4 py-3 shadow-sm">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted-foreground"
            style={{ animationDelay: `${i * 0.15}s` }}
          />
        ))}
      </div>
    </div>
  );
}
