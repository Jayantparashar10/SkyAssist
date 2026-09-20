// Typed fetch wrappers for the FastAPI backend. Every function throws
// ApiError on a non-2xx response.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export type Role = "customer" | "agent" | "system" | "supervisor";
export type Tier = "Silver" | "Gold" | "Platinum";
export type BookingStatus = "scheduled" | "cancelled" | "delayed";
export type EscalationStatus = "pending" | "approved" | "denied" | "completed";
export type EscalationKind = "approval" | "handoff" | "immediate";
export type DecisionStatus = "execute" | "offer" | "confirm" | "ask" | "escalate" | "decline" | "inform";

export interface Customer {
  name: string;
  tier: Tier;
  pnr: string;
  history?: string | null;
}

export interface Booking {
  id: number;
  pnr: string;
  leg: number;
  flight_no: string | null;
  origin: string;
  destination: string;
  sched_dep: string;
  status: BookingStatus;
  new_dep: string | null;
  cause: string | null;
}

export interface Message {
  id: number;
  role: Role;
  content: string;
  meta?: Record<string, unknown> | null;
  created_at: string;
}

export interface Decision {
  decision_id: string;
  action: string;
  status: DecisionStatus;
  rule_id: string;
  params: Record<string, unknown>;
  customer_facing_facts: string[];
  assumption_ids: string[];
}

export interface ActionRecord {
  id: number;
  pnr: string;
  type: string;
  params: Record<string, unknown>;
  rule_id: string;
  status: string;
  created_at: string;
}

export interface EscalationPacket {
  kind: EscalationKind;
  customer: { name: string; tier: Tier; pnr: string };
  booking: string;
  already_done: string[];
  requested: string;
  blocked_by: string;
  sentiment: string;
  customer_quote: string;
  notes: string[];
  transcript_session_id: string;
}

export interface Escalation {
  id: number;
  session_id: string;
  pnr: string;
  kind: EscalationKind;
  reason: string;
  rule_id: string;
  packet: EscalationPacket;
  status: EscalationStatus;
  supervisor_note?: string | null;
  created_at: string;
  resolved_at?: string | null;
}

export interface Choice {
  choice_id: string;
  label: string;
}

export interface SessionResponse {
  session_id: string;
  customer: Customer;
  bookings: Booking[];
}

export interface ChatResponse {
  reply: string;
  choices: Choice[];
  decisions: Decision[];
  actions: ActionRecord[];
  escalations: Escalation[];
  assumptions: string[];
}

export interface SessionDetail {
  messages: Message[];
  actions: ActionRecord[];
  escalations: Escalation[];
  assumptions: string[];
}

export interface EscalationList {
  pending: Escalation[];
  resolved: Escalation[];
}

export interface AuditStatus {
  valid: boolean;
  entries: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(0, "Could not reach the server. Is the backend running?");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new ApiError(res.status, body || res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function login(pnr: string, lastName: string) {
  return request<SessionResponse>("/session", {
    method: "POST",
    body: JSON.stringify({ pnr, last_name: lastName }),
  });
}

export function sendMessage(sessionId: string, message: string) {
  return request<ChatResponse>("/chat", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

export function sendChoice(sessionId: string, choiceId: string) {
  return request<ChatResponse>("/choice", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, choice_id: choiceId }),
  });
}

export function getSession(sessionId: string) {
  return request<SessionDetail>(`/session/${sessionId}`);
}

export function getEscalations(passcode: string) {
  return request<EscalationList>("/escalations", {
    headers: { "X-Supervisor-Passcode": passcode },
  });
}

export function decideEscalation(
  id: number,
  decision: "approve" | "deny" | "complete",
  note: string,
  passcode: string
) {
  return request<Escalation>(`/escalations/${id}/decide`, {
    method: "POST",
    headers: { "X-Supervisor-Passcode": passcode },
    body: JSON.stringify({ decision, note }),
  });
}

export function verifyAudit(passcode: string) {
  return request<AuditStatus>("/audit/verify", {
    headers: { "X-Supervisor-Passcode": passcode },
  });
}

export function resetDemo(passcode: string) {
  return request<void>("/reset", {
    method: "POST",
    headers: { "X-Supervisor-Passcode": passcode },
  });
}

export function checkHealth() {
  return request<{ db: boolean; llm: boolean }>("/health");
}
