const pad = (n: number) => String(n).padStart(2, "0");

export const shortId = (id: string) => "RA-" + id.slice(0, 4).toUpperCase();
export const clock = (iso: string) => {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
export const dayTime = (iso: string) =>
  new Date(iso).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).replace(",", " ·");
export const duration = (seconds: number) => {
  const s = Math.max(0, Math.round(seconds));
  return `${pad(Math.floor(s / 60))}:${pad(s % 60)}`;
};
export const money = (usd: number) => "$" + usd.toFixed(2);
export const percent = (ratio: number | null) => (ratio === null ? "—" : (ratio * 100).toFixed(1) + "%");
export const truncate = (text: string, max: number) => (text.length > max ? text.slice(0, max - 1).trimEnd() + "…" : text);
export function elapsedSeconds(startedAt: string | null, completedAt: string | null, now = Date.now()) {
  if (!startedAt) return 0;
  return ((completedAt ? Date.parse(completedAt) : now) - Date.parse(startedAt)) / 1000;
}
export function greeting(date = new Date()) {
  const hour = date.getHours();
  return hour < 12 ? "good morning" : hour < 18 ? "good afternoon" : "good evening";
}
