import type { EventRecord, HistoryEntry, UserAccount } from "../types";

export function titleCase(value: string): string {
  return value
    .split(/[\s_-]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function initials(name: string): string {
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}

export function formatEventDate(date: string): { day: string; month: string } {
  const parsed = new Date(`${date}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return { day: "--", month: "---" };
  }
  return {
    day: new Intl.DateTimeFormat("en", { day: "2-digit" }).format(parsed),
    month: new Intl.DateTimeFormat("en", { month: "short" }).format(parsed).toUpperCase(),
  };
}

export function formatDateLong(date: string): string {
  const parsed = new Date(`${date}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? "Date not set"
    : new Intl.DateTimeFormat("en", {
        weekday: "short",
        day: "numeric",
        month: "short",
        year: "numeric",
      }).format(parsed);
}

export function formatTimeAgo(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "Recently";
  }
  const minutes = Math.max(0, Math.floor((Date.now() - date.getTime()) / 60000));
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function eventReadiness(event: EventRecord): number {
  const items = event.status === "out" ? event.return_checklist : event.checklist;
  if (event.conflicts.length > 0 || event.plan.total_missing > 0) {
    return 0;
  }
  if (!items.length) {
    return event.plan.is_ready ? 100 : 0;
  }
  const done = items.filter((item) => item.done).length;
  const checklistScore = Math.round((done / items.length) * 100);
  return event.status === "confirmed" && checklistScore === 0 ? 72 : checklistScore;
}

export function eventCrew(event: EventRecord, users: UserAccount[]): UserAccount[] {
  const userMap = new Map(users.map((user) => [user.id, user]));
  return event.assigned_user_ids
    .map((id) => userMap.get(id))
    .filter((user): user is UserAccount => Boolean(user));
}

export function allActivity(events: EventRecord[]): Array<HistoryEntry & { event: EventRecord }> {
  return events
    .flatMap((event) => event.history.map((entry) => ({ ...entry, event })))
    .sort((a, b) => b.timestamp.localeCompare(a.timestamp));
}

export function todayGreeting(name: string): string {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  return `${greeting}, ${name.split(" ")[0] || "there"}.`;
}

