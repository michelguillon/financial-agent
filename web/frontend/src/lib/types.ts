// Shape of the JSON returned by POST /api/sessions.
export interface SessionInfo {
  session_id: string;
  budget_total_usd: number;
  budget_used_usd: number;
  turns_so_far: number;
  sessions_remaining_today: number;
}

// Discriminated union of SSE events the backend can emit during a turn.
export type AgentEvent =
  | { type: 'session.info';     data: { session_id: string; budget_total_usd: number; budget_used_usd: number; turns_so_far: number } }
  | { type: 'tool_call';        data: { name: string; input: Record<string, unknown> } }
  | { type: 'tool_result';      data: { name: string; result: string; is_error: boolean } }
  | { type: 'assistant_text';   data: { text: string } }
  | { type: 'usage';            data: { input_tokens: number; output_tokens: number; cache_read: number; cache_creation: number; cost_usd: number; turn: number } }
  | { type: 'error';            data: { where: string; detail: string } }
  | { type: 'turn.completed';   data: { final_text: string; cumulative_cost_usd: number; budget_remaining_usd: number; turns_so_far: number } }
  | { type: 'budget.exceeded';  data: { used_usd: number; budget_usd: number } };

// Items rendered in the chat scroll area.
export type ChatItem =
  | { kind: 'user'; text: string; id: string }
  | { kind: 'assistant'; text: string; id: string }
  | { kind: 'tool'; name: string; input: Record<string, unknown>; result?: string; is_error?: boolean; id: string }
  | { kind: 'notice'; level: 'error' | 'info' | 'budget'; text: string; id: string };
