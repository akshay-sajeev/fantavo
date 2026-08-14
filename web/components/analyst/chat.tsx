"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Loader2, Send, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { MessageContent } from "@/components/analyst/message-content";
import type { AnalystChatResponse, AnalystCitation, AnalystMessage, AnalystSpan } from "@/lib/types";

/**
 * The AI league analyst's chat UI. No analytics logic here (CLAUDE.md) --
 * this component only sends the transcript to
 * app/api/league/[leagueId]/analyst/[teamId]/route.ts (a thin proxy to the
 * real sim API) and renders exactly what comes back. Every number a
 * message shows is either plain prose text or a real <StatChip> keyed off
 * `citations`/`spans` the server already computed -- nothing is derived,
 * reformatted, or estimated client-side.
 *
 * Keeps no server-side session: the full transcript lives in this
 * component's own state and is resent in full on every request, matching
 * sim.api.analyst_view.AnalystMessage's documented no-session-store design.
 */

interface ChatMessage extends AnalystMessage {
  citations?: AnalystCitation[];
  spans?: AnalystSpan[];
  isError?: boolean;
}

const SUGGESTED_PROMPTS_BASE = [
  "Why am I projected to lose the title this year?",
  "What is my biggest weakness?",
  "Who is my biggest threat?",
  "What should I target on waivers?",
];

export function AnalystChat({
  leagueId,
  teamId,
  teamName,
  rivalTeamName,
}: {
  leagueId: number;
  teamId: number;
  teamName: string;
  rivalTeamName: string | null;
}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, status]);

  const suggestedPrompts = rivalTeamName
    ? [...SUGGESTED_PROMPTS_BASE, `Should I trade with ${rivalTeamName}?`]
    : SUGGESTED_PROMPTS_BASE;

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || status === "loading") return;

    const nextMessages: ChatMessage[] = [...messages, { role: "user", text: trimmed }];
    setMessages(nextMessages);
    setInput("");
    setStatus("loading");
    setErrorMessage(null);

    try {
      const res = await fetch(`/api/league/${leagueId}/analyst/${teamId}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: nextMessages.map((m) => ({ role: m.role, text: m.text })),
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        throw new Error(body.error ?? `HTTP ${res.status}`);
      }
      const data = body as AnalystChatResponse;
      setMessages([
        ...nextMessages,
        { role: "model", text: data.reply, citations: data.citations, spans: data.spans },
      ]);
      setStatus("idle");
    } catch (error) {
      setStatus("error");
      setErrorMessage(error instanceof Error ? error.message : "Unknown error");
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    void send(input);
  }

  return (
    <div className="flex flex-col rounded-xl border border-border bg-card">
      <div className="max-h-[60vh] min-h-[16rem] space-y-4 overflow-y-auto p-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center gap-3 py-8 text-center">
            <Sparkles className="h-8 w-8 text-brand-accent" aria-hidden="true" />
            <p className="max-w-sm text-sm text-muted-foreground">
              Ask about {teamName}&apos;s real title odds, roster weaknesses, waiver targets,
              playoff outlook, rival threats, or a specific trade. Every number in the answer
              comes from this league&apos;s real simulation and roster data.
            </p>
          </div>
        )}

        {messages.map((message, i) => (
          <div
            key={i}
            className={
              message.role === "user"
                ? "ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground"
                : "mr-auto max-w-[85%] rounded-2xl rounded-bl-sm border border-border bg-background px-4 py-2.5 text-sm text-foreground"
            }
          >
            {message.isError ? (
              <span className="flex items-center gap-1.5 text-destructive">
                <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
                {message.text}
              </span>
            ) : (
              <MessageContent
                text={message.text}
                citations={message.citations ?? []}
                spans={message.spans ?? []}
              />
            )}
          </div>
        ))}

        {status === "loading" && (
          <div
            role="status"
            aria-live="polite"
            aria-busy="true"
            className="mr-auto flex max-w-[85%] items-center gap-2 rounded-2xl rounded-bl-sm border border-border bg-background px-4 py-2.5 text-sm text-muted-foreground"
          >
            <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
            Running real tool calls and asking the model&hellip;
          </div>
        )}

        {status === "error" && errorMessage && (
          <p className="flex items-center gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />
            {errorMessage}
          </p>
        )}
        <div ref={scrollRef} />
      </div>

      {messages.length === 0 && (
        <div className="flex flex-wrap gap-2 border-t border-border px-4 py-3">
          {suggestedPrompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              onClick={() => void send(prompt)}
              disabled={status === "loading"}
              className="cursor-pointer rounded-full border border-border bg-muted px-3 py-1.5 text-xs font-medium text-foreground transition-colors duration-150 hover:bg-muted/70 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              {prompt}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={handleSubmit} className="flex items-center gap-2 border-t border-border p-3">
        <label htmlFor="analyst-chat-input" className="sr-only">
          Ask the league analyst a question
        </label>
        <input
          id="analyst-chat-input"
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Ask about ${teamName}...`}
          disabled={status === "loading"}
          className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-60"
        />
        <Button type="submit" disabled={status === "loading" || !input.trim()} className="cursor-pointer">
          {status === "loading" ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Send className="h-4 w-4" data-icon="inline-start" aria-hidden="true" />
          )}
          Send
        </Button>
      </form>
    </div>
  );
}
