/**
 * Behavior for the organizer console (/host, also /organizer-workflow and
 * /event-organizer), ported from the TableTalk organizer prototype
 * (tabletalk-admin-prototype-4.html) and wired to the host API.
 *
 * One workspace with five views: Overview, Tables, Signup QR, Matching, and
 * Updates, plus an event form for "Create event" and "Edit event". Tables owns
 * each event's table numbers, seats per table, enrollment cutoff, minimum
 * group size, automatic matching, and automatic publishing. Unsaved table
 * edits are kept per event, matching only uses saved settings, published
 * layouts are locked, and an unpublished seating draft can be discarded.
 *
 * It runs one event: the server's EventConfig. Saving tables or event details
 * calls PUT /api/host/event (times are entered in the event's time zone and
 * stored in UTC), matching creates a draft run with POST /api/host/runs,
 * publishing calls POST /api/host/runs/{id}/publish, discarding a draft calls
 * DELETE /api/host/runs, and updates go to attendees' in-app inbox through
 * POST /api/host/announcements. The detailed review stays at /host/review.
 */

import {
  ApiError,
  request,
  type DemoResult,
  type EventConfig,
  type HostState,
  type MatchRun,
} from "../api";

export const HOST_TOKEN_KEY = "tabletalk-host-token";
/** How often the console re-reads the server, e.g. after a cutoff run. */
export const REFRESH_MS = 20_000;

type View = "overview" | "setup" | "tables" | "signup" | "matching" | "updates";

const VIEWS: View[] = [
  "overview",
  "setup",
  "tables",
  "signup",
  "matching",
  "updates",
];

const isView = (value: string | undefined): value is View =>
  VIEWS.some((view) => view === value);

export interface TableSlot {
  number: number;
  seats: number;
}

export interface PlanTable {
  /** Zero-padded table number, e.g. "04". */
  number: string;
  seats: number;
  people: string[];
  name: string;
}

type Audience = "all" | "matched";
type Delivery = "now" | "schedule";

interface UpdateDraft {
  eventId: string;
  title: string;
  body: string;
  audience: Audience;
  count: number;
  when: Delivery;
  time: string;
}

interface SentUpdate extends UpdateDraft {
  status: string;
}

export interface OrganizerEvent {
  id: string;
  name: string;
  /** YYYY-MM-DD */
  date: string;
  zone: string;
  location: string;
  /** HH:MM, event local time */
  start: string;
  end: string;
  layout: TableSlot[];
  /** YYYY-MM-DDTHH:MM, event local time */
  cutoff: string;
  minSize: number;
  autoMatch: boolean;
  autoPublish: boolean;
  roomConfigured: boolean;
  registered: number;
  ready: number;
  /** When automatic matching runs, or null when it doesn't. */
  schedule: string | null;
  plan: PlanTable[] | null;
  published: boolean;
  enrollmentClosed: boolean;
  running?: boolean;
  messages: SentUpdate[];
}

/** Unsaved Tables edits. Values stay as typed until the setup is saved. */
interface RoomDraft {
  layout: { number: string; seats: string }[];
  cutoff: string;
  minSize: string;
  autoMatch: boolean;
  autoPublish: boolean;
}

interface RoomRow {
  number: HTMLInputElement;
  seats: HTMLInputElement;
  remove: HTMLButtonElement;
  row: HTMLDivElement;
}

interface OrganizerHook {
  qrMatrix: (text: string) => boolean[][];
  qrSvg: (text: string) => string;
  printableSign: () => string;
  getEvents: () => OrganizerEvent[];
  getActive: () => OrganizerEvent;
  getRoomDraft: () => RoomDraft;
}

declare global {
  interface Window {
    /** Development hook for checking the console, not an API. */
    tabletalkAdminDemo?: Readonly<OrganizerHook>;
  }
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function uniformLayout(count: number, seats: number): TableSlot[] {
  return Array.from({ length: count }, (_, i) => ({ number: i + 1, seats }));
}

function dateLabel(date: string) {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${date}T12:00:00Z`));
}

function timeLabel(time: string) {
  const [h, m] = time.split(":").map(Number);
  return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h >= 12 ? "PM" : "AM"}`;
}

function scheduleLabel(value: string) {
  return `${dateLabel(value.slice(0, 10))}, ${timeLabel(value.slice(11, 16))}`;
}

/** Fifteen minutes before lunch starts. */
function defaultCutoff(date: string, start: string) {
  return new Date(Date.parse(`${date}T${start}:00Z`) - 15 * 60000)
    .toISOString()
    .slice(0, 16);
}

const escapeHtml = (text: string) =>
  text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

/** Milliseconds `zone` is ahead of UTC at the instant `ms`. */
function zoneOffset(ms: number, zone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    })
      .formatToParts(new Date(ms))
      .map((p) => [p.type, p.value]),
  );
  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour) % 24,
    Number(parts.minute),
    Number(parts.second),
  );
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/** "YYYY-MM-DDTHH:MM" in `zone` to an ISO UTC timestamp. */
export function localToUtc(local: string, zone: string): string {
  const guess = Date.parse(`${local}:00Z`);
  let utc = guess - zoneOffset(guess, zone);
  // Correct once more around daylight-saving changes.
  utc = guess - zoneOffset(utc, zone);
  return new Date(utc).toISOString();
}

/** An ISO timestamp to "YYYY-MM-DDTHH:MM" in `zone`. */
export function utcToLocal(iso: string, zone: string): string {
  const ms = Date.parse(iso);
  return new Date(ms + zoneOffset(ms, zone)).toISOString().slice(0, 16);
}

/** Lunch hours as attendees see them, e.g. "12:30 PM–1:15 PM". */
function lunchLabel(start: string, end: string) {
  return `${timeLabel(start)}–${timeLabel(end)}`;
}

function sameLayout(run: MatchRun, event: EventConfig) {
  return (
    run.min_table_size === event.min_table_size &&
    JSON.stringify(run.tables) === JSON.stringify(event.tables)
  );
}

/**
 * A real QR encoder/**
 * A real QR encoder for short UTF-8 event links: QR version 4, error
 * correction L, one Reed–Solomon block (80 data + 20 correction codewords),
 * mask 0. Returns the 33×33 modules, true for dark.
 */
export function qrMatrix(text: string): boolean[][] {
  const bytes = Array.from(new TextEncoder().encode(text));
  if (bytes.length > 78) {
    throw new Error("Signup link is too long for this demo QR encoder.");
  }

  // Byte-mode data, terminator, and padding to 80 codewords.
  const bits: number[] = [];
  const put = (value: number, length: number) => {
    for (let i = length - 1; i >= 0; i--) bits.push((value >>> i) & 1);
  };
  put(4, 4);
  put(bytes.length, 8);
  bytes.forEach((b) => put(b, 8));
  put(0, Math.min(4, 640 - bits.length));
  while (bits.length % 8) bits.push(0);
  const data: number[] = [];
  for (let i = 0; i < bits.length; i += 8) {
    data.push(bits.slice(i, i + 8).reduce((n, b) => n * 2 + b, 0));
  }
  for (let p = 0; data.length < 80; p++) data.push(p % 2 ? 0x11 : 0xec);

  // Reed–Solomon error correction in GF(256).
  const mul = (a: number, b: number) => {
    let x = a;
    let y = b;
    let z = 0;
    while (y) {
      if (y & 1) z ^= x;
      y >>>= 1;
      x <<= 1;
      if (x & 256) x ^= 0x11d;
    }
    return z;
  };
  let generator = [1];
  let power = 1;
  for (let i = 0; i < 20; i++) {
    const next = new Array<number>(generator.length + 1).fill(0);
    generator.forEach((v, j) => {
      next[j] ^= v;
      next[j + 1] ^= mul(v, power);
    });
    generator = next;
    power = mul(power, 2);
  }
  const rem = [...data, ...new Array<number>(20).fill(0)];
  for (let i = 0; i < 80; i++) {
    const factor = rem[i];
    for (let j = 0; j < generator.length; j++) {
      rem[i + j] ^= mul(generator[j], factor);
    }
  }
  const codewords = [...data, ...rem.slice(80)];

  const size = 33;
  const matrix = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  );
  const fixed = Array.from({ length: size }, () =>
    new Array<boolean>(size).fill(false),
  );
  const set = (x: number, y: number, v: boolean | number) => {
    if (x >= 0 && y >= 0 && x < size && y < size) {
      matrix[y][x] = Boolean(v);
      fixed[y][x] = true;
    }
  };

  // Finder patterns and their separators.
  for (const [x, y] of [
    [0, 0],
    [size - 7, 0],
    [0, size - 7],
  ]) {
    for (let dy = -1; dy <= 7; dy++) {
      for (let dx = -1; dx <= 7; dx++) {
        set(
          x + dx,
          y + dy,
          dx >= 0 &&
            dx <= 6 &&
            dy >= 0 &&
            dy <= 6 &&
            (dx === 0 ||
              dx === 6 ||
              dy === 0 ||
              dy === 6 ||
              (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4)),
        );
      }
    }
  }
  // Timing patterns.
  for (let i = 8; i < size - 8; i++) {
    set(6, i, i % 2 === 0);
    set(i, 6, i % 2 === 0);
  }
  // Alignment pattern.
  for (let dy = -2; dy <= 2; dy++) {
    for (let dx = -2; dx <= 2; dx++) {
      set(26 + dx, 26 + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
    }
  }
  // Format information (error correction L, mask 0) and the dark module.
  const formatData = 8;
  let bch = formatData << 10;
  for (let i = 14; i >= 10; i--) {
    if ((bch >>> i) & 1) bch ^= 0x537 << (i - 10);
  }
  const format = ((formatData << 10) | bch) ^ 0x5412;
  const bit = (i: number) => (format >>> i) & 1;
  for (let i = 0; i <= 5; i++) set(8, i, bit(i));
  set(8, 7, bit(6));
  set(8, 8, bit(7));
  set(7, 8, bit(8));
  for (let i = 9; i < 15; i++) set(14 - i, 8, bit(i));
  for (let i = 0; i < 8; i++) set(size - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) set(8, size - 15 + i, bit(i));
  set(8, size - 8, true);

  // Data bits, in two-column zigzags from the bottom right, with mask 0.
  let index = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < size; vert++) {
      const y = ((right + 1) & 2) === 0 ? size - 1 - vert : vert;
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        if (fixed[y][x]) continue;
        const v =
          index < codewords.length * 8
            ? (codewords[index >>> 3] >>> (7 - (index & 7))) & 1
            : 0;
        matrix[y][x] = Boolean(v ^ ((x + y) % 2 === 0 ? 1 : 0));
        index++;
      }
    }
  }
  return matrix;
}

/** The QR code as SVG markup, with a 4-module quiet zone. */
export function qrSvg(text: string): string {
  const m = qrMatrix(text);
  let path = "";
  for (let y = 0; y < m.length; y++) {
    for (let x = 0; x < m.length; x++) {
      if (m[y][x]) path += `M${x + 4} ${y + 4}h1v1h-1z`;
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 41 41" width="410" height="410" shape-rendering="crispEdges" role="img" aria-label="Event signup QR code"><rect width="41" height="41" fill="#fff"/><path d="${path}" fill="#000"/></svg>`;
}

// Styles for the downloadable printable sign (A4 or US Letter).
const SIGN_STYLES =
  "@page{size:auto;margin:15mm}*{box-sizing:border-box}" +
  "body{margin:0;color:#000;background:#fff;font-family:'Inter Tight','Helvetica Neue',Arial,sans-serif}" +
  ".poster{max-width:180mm;margin:0 auto;text-align:center}" +
  ".brand{background:#000;color:#fff;text-align:left;padding:10mm;font-size:30pt;font-weight:700;letter-spacing:-1.5pt}" +
  ".brand span{color:#1e6ef3}" +
  ".event{font-size:12pt;text-transform:uppercase;letter-spacing:1pt;margin:12mm 0 6mm}" +
  "h1{font-size:44pt;line-height:1.02;letter-spacing:-2pt;margin:0 0 7mm}" +
  ".qr{width:70mm;margin:auto}.qr svg{width:100%;height:auto}" +
  "p{font-size:15pt;line-height:1.5}.meta{font-size:12pt}.url{font-size:10pt;overflow-wrap:anywhere}" +
  ".print{border:0;padding:12px 20px;background:#145dd9;color:#fff;margin:20px;font-weight:700;cursor:pointer}" +
  "@media screen{body{padding:16px}h1{font-size:clamp(30px,9vw,58px)}.brand{padding:24px}.poster{width:100%}.qr{max-width:85%}}" +
  "@media print{.print{display:none}body{-webkit-print-color-adjust:exact;print-color-adjust:exact}}";

/**
 * Wires up the organizer markup inside `root` (the #tt-admin element).
 * Returns a function that removes the page's listeners and timers.
 */
export function initOrganizer(root: HTMLElement): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  const timers = new Set<number>();

  function later(callback: () => void, ms: number) {
    const id = window.setTimeout(() => {
      timers.delete(id);
      callback();
    }, ms);
    timers.add(id);
  }

  function q<T extends HTMLElement = HTMLElement>(id: string): T {
    const node = root.querySelector<T>(`#ad-${id}`);
    if (!node) throw new Error(`Missing #ad-${id} in the organizer markup.`);
    return node;
  }
  const input = (id: string) => q<HTMLInputElement>(id);
  const select = (id: string) => q<HTMLSelectElement>(id);
  const textarea = (id: string) => q<HTMLTextAreaElement>(id);
  const button = (id: string) => q<HTMLButtonElement>(id);

  function on<E extends Event = Event>(
    target: EventTarget,
    type: string,
    listener: (event: E) => void,
  ) {
    target.addEventListener(type, listener as EventListener, { signal });
  }

  const media = (query: string) =>
    typeof window.matchMedia === "function" && window.matchMedia(query).matches;
  const reduced = () => media("(prefers-reduced-motion: reduce)");
  // Below 960px the layout is one column, so results can land off screen.
  const singleColumn = () => media("(max-width: 959px)");

  function animate(
    node: Element,
    keyframes: Keyframe[],
    options: KeyframeAnimationOptions,
  ) {
    if (reduced() || typeof node.animate !== "function") return;
    node.animate(keyframes, options);
  }

  function scrollToStart(node: Element) {
    if (typeof node.scrollIntoView !== "function") return;
    node.scrollIntoView({
      block: "start",
      behavior: reduced() ? "auto" : "smooth",
    });
  }

  function el<K extends keyof HTMLElementTagNameMap>(
    tag: K,
    attrs: Record<string, string> = {},
    text?: string,
  ): HTMLElementTagNameMap[K] {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") e.className = v;
      else e.setAttribute(k, v);
    }
    if (text !== undefined) e.textContent = text;
    return e;
  }

  // Server state

  let host: HostState | null = null;
  /** The published run, or the newest draft made for the saved layout. */
  let draft: MatchRun | null = null;
  const runs = new Map<string, MatchRun>();
  let running = false;
  let busy = false;
  let loads = 0;
  let token = sessionStorage.getItem(HOST_TOKEN_KEY) ?? "";
  let currentView: View = "overview";
  let editing = false;
  let messageDraft: UpdateDraft | null = null;
  const roomDrafts = new Map<string, RoomDraft>();
  let roomRows: RoomRow[] = [];

  function api<T>(
    path: string,
    body?: unknown,
    method?: "GET" | "POST" | "PUT" | "DELETE",
  ): Promise<T> {
    return request<T>(path, body, {
      method,
      hostToken: token,
      invalidMessage: "Please check these settings and try again.",
    });
  }

  async function run(path: string): Promise<MatchRun> {
    const cached = runs.get(path);
    if (cached) return cached;
    const fetched = await api<MatchRun>(path);
    runs.set(path, fetched);
    return fetched;
  }

  async function load() {
    // Only the newest read wins, so a slow background refresh can't undo a save.
    const mine = ++loads;
    const state = await api<HostState>("/api/host/state");
    const id = state.published_run_id ?? state.runs[0]?.id;
    let current: MatchRun | null = null;
    if (id) {
      try {
        const found = await run(`/api/host/runs/${id}`);
        if (state.published_run_id || sameLayout(found, state.event)) {
          current = found;
        }
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) throw error;
      }
    }
    if (signal.aborted || mine !== loads) return;
    host = state;
    draft = current;
  }

  function tablesFrom(r: MatchRun): PlanTable[] {
    const seats = new Map(r.tables.map((t) => [t.number, t.seats]));
    return r.plan.groups.map((group) => ({
      number: String(group.table_number ?? "").padStart(2, "0"),
      seats: seats.get(group.table_number ?? -1) ?? group.members.length,
      people: group.members.map((m) => m.name),
      name: group.name ?? "",
    }));
  }

  function localToday(zone: string) {
    return utcToLocal(new Date().toISOString(), zone).slice(0, 10);
  }

  /** The console's view of the server's single event. */
  function event(): OrganizerEvent {
    const ev = host?.event;
    const zone = ev?.timezone ?? "America/Denver";
    // Without a saved date, the saved cutoff's day is the best guess.
    const date =
      ev?.date ??
      (ev?.enrollment_closes_at
        ? utcToLocal(ev.enrollment_closes_at, zone).slice(0, 10)
        : localToday(zone));
    const start = ev?.lunch_start ?? "12:30";
    const registered = host?.attendees.length ?? 0;
    const plan = draft ? tablesFrom(draft) : null;
    const roomConfigured = Boolean(ev?.enrollment_closes_at);
    const cutoff = ev?.enrollment_closes_at
      ? utcToLocal(ev.enrollment_closes_at, zone)
      : defaultCutoff(date, start);
    const autoMatch = ev?.auto_match ?? false;
    const now = Date.now();
    return {
      id: "event",
      name: ev?.name ?? "Your event",
      date,
      zone,
      location: ev?.location ?? "",
      start,
      end: ev?.lunch_end ?? "13:15",
      layout: ev?.tables ?? [],
      cutoff,
      minSize: ev?.min_table_size ?? 2,
      autoMatch,
      autoPublish: ev?.auto_publish ?? false,
      roomConfigured,
      registered,
      ready: registered,
      schedule:
        roomConfigured && autoMatch && !plan && !running ? cutoff : null,
      plan,
      published: host?.status === "published",
      enrollmentClosed: host ? host.status !== "open" : false,
      running,
      messages: (host?.announcements ?? []).map((a) => ({
        eventId: "event",
        title: a.title,
        body: a.body,
        audience: a.audience,
        count: a.recipients,
        when: a.send_at ? "schedule" : "now",
        time: a.send_at ? utcToLocal(a.send_at, zone) : "",
        status:
          a.send_at && Date.parse(a.send_at) > now
            ? "Scheduled for the inbox"
            : "Delivered to the inbox",
      })),
    };
  }

  /** The attendee app, where the QR code and signup link point. */
  function signupUrl() {
    return `${window.location.origin}/`;
  }

  function askForToken() {
    q("token-form").hidden = false;
    q("toast").hidden = true;
    input("host-token").focus();
  }

  /** Run a server action, then re-read the server and re-render. */
  async function act(
    action: () => Promise<string | void>,
    { reload = true } = {},
  ) {
    if (busy) {
      toast("Still saving your last change. Try again in a moment.");
      return;
    }
    busy = true;
    try {
      const message = await action();
      if (reload) await load();
      if (signal.aborted) return;
      refresh();
      if (message) toast(message);
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) {
        askForToken();
      } else {
        refresh();
        toast(
          error instanceof Error
            ? error.message
            : "Something went wrong. Please try again.",
        );
      }
    } finally {
      busy = false;
    }
  }

  function eventConfig(changes: Partial<EventConfig>): EventConfig {
    if (!host) throw new Error("The event hasn't loaded yet.");
    return { ...host.event, ...changes };
  }

  function toast(text: string) {
    q("toast").textContent = text;
    q("toast").hidden = false;
  }

  function fieldError(id: string, message: string) {
    q(id).textContent = message;
    q(id).hidden = !message;
  }

  function updateBreadcrumb() {
    const view = currentView;
    const place =
      view === "setup"
        ? editing
          ? "EDIT EVENT"
          : "NEW EVENT"
        : view === "signup"
          ? "SIGNUP & QR"
          : view.toUpperCase();
    q("breadcrumb").textContent = `${event().name.toUpperCase()} / ${place}`;
  }

  function show(view: View, { focus = true } = {}) {
    currentView = view;
    if (view === "tables") renderRoom();
    if (view === "matching") renderMatchingSettings();
    VIEWS.forEach((v) => {
      q(v).hidden = v !== view;
    });
    root.querySelectorAll<HTMLElement>("[data-view]").forEach((b) => {
      if (b.dataset.view === (view === "setup" ? "overview" : view)) {
        b.setAttribute("aria-current", "page");
      } else {
        b.removeAttribute("aria-current");
      }
    });
    updateBreadcrumb();
    animate(
      q(view),
      [
        { opacity: 0, transform: "translateY(6px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 190, easing: "ease-out" },
    );
    if (focus) {
      const target =
        view === "setup"
          ? q("event-name")
          : view === "tables"
            ? roomLocked()
              ? q("room-view-plan")
              : q("bulk-count")
            : view === "matching"
              ? q("mode-schedule")
              : view === "signup"
                ? q("copy-link")
                : view === "updates"
                  ? q("template")
                  : q("edit-event");
      target.focus({ preventScroll: true });
      if (window.scrollY > 0) window.scrollTo({ top: 0, behavior: "auto" });
    }
  }

  function refreshEventSelect() {
    const e = event();
    select("event-select").replaceChildren(
      el("option", { value: e.id }, e.name),
    );
    select("event-select").disabled = true;
  }

  function matchedCount() {
    const plan = event().plan;
    return plan ? plan.reduce((n, t) => n + t.people.length, 0) : 0;
  }

  function recipients() {
    return select("audience").value === "matched"
      ? event().published
        ? matchedCount()
        : 0
      : event().registered;
  }

  function capacity(e = event()) {
    return e.layout.reduce((sum, t) => sum + t.seats, 0);
  }

  // Tables: room setup, drafts, and locking

  function roomSnapshot(e: OrganizerEvent): RoomDraft {
    return {
      layout: e.layout.map((t) => ({
        number: String(t.number),
        seats: String(t.seats),
      })),
      cutoff: e.cutoff,
      minSize: String(e.minSize),
      autoMatch: e.autoMatch,
      autoPublish: e.autoPublish,
    };
  }

  function roomDraft(e = event()): RoomDraft {
    let draft = roomDrafts.get(e.id);
    if (!draft) {
      draft = roomSnapshot(e);
      roomDrafts.set(e.id, draft);
    }
    return draft;
  }

  function roomDirty(e = event()) {
    const draft = roomDrafts.get(e.id);
    return (
      draft !== undefined &&
      JSON.stringify(draft) !== JSON.stringify(roomSnapshot(e))
    );
  }

  function roomLocked(e = event()) {
    return Boolean(e.plan) || Boolean(e.running);
  }

  function roomCanEdit() {
    if (!roomLocked()) return true;
    toast(
      event().published
        ? "Published tables are locked to keep attendee assignments consistent."
        : "Discard the draft seating plan before changing the room.",
    );
    return false;
  }

  function updateRoomState() {
    const e = event();
    const d = roomDraft();
    const total = d.layout.reduce((n, t) => n + (Number(t.seats) || 0), 0);
    const dirty = roomDirty();
    const locked = roomLocked();
    q("room-total").textContent =
      `${d.layout.length} table${d.layout.length === 1 ? "" : "s"} · ${total} seats`;
    q("room-save-state").textContent = e.published
      ? "Published layout"
      : locked
        ? "Layout locked"
        : dirty
          ? "Unsaved changes"
          : e.roomConfigured
            ? "Saved setup"
            : "Setup needed";
    q("room-capacity-note").textContent =
      total < e.ready
        ? `${total} seats for ${e.ready} ready attendees. Add ${e.ready - total} seats before matching.`
        : `${e.registered} registered · ${e.ready} ready to match`;
    q("room-capacity-note").classList.toggle(
      "ad-capacity-shortfall",
      total < e.ready,
    );
    input("auto-publish").disabled = locked || !d.autoMatch;
    q("room-behavior").textContent = !d.autoMatch
      ? "Enrollment closes at the cutoff. Run matching manually from the Matching page."
      : d.autoPublish
        ? "At the cutoff, TableTalk creates the seating plan and opens the table reveal automatically."
        : "At the cutoff, TableTalk makes a draft for you to review and publish.";
    button("save-room").disabled = locked || (!dirty && e.roomConfigured);
    button("add-table").disabled = locked || d.layout.length >= 50;
    q("room-demo-cutoff").hidden = !e.roomConfigured || !e.autoMatch || locked;
    button("room-demo-cutoff").disabled = dirty;
  }

  function renderRoomRows() {
    const d = roomDraft();
    roomRows = [];
    q("room-rows").replaceChildren();
    d.layout.forEach((table, index) => {
      const row = el("div", { class: "ad-table-row" });
      const numberCell = el("div", { class: "ad-row-cell" });
      const seatCell = el("div", { class: "ad-row-cell" });
      const number = el("input", {
        id: `ad-room-number-${index}`,
        type: "number",
        min: "1",
        max: "999",
        inputmode: "numeric",
        required: "",
      });
      const seats = el("input", {
        id: `ad-room-seats-${index}`,
        type: "number",
        min: "2",
        max: "12",
        inputmode: "numeric",
        required: "",
      });
      const remove = el(
        "button",
        {
          type: "button",
          class: "ad-row-remove cursor-interaction",
          "aria-label": `Remove table ${table.number}`,
        },
        "Remove",
      );
      number.value = table.number;
      seats.value = table.seats;
      numberCell.append(
        el("label", { for: `ad-room-number-${index}` }, "Table #"),
        number,
      );
      seatCell.append(
        el("label", { for: `ad-room-seats-${index}` }, "Seats"),
        seats,
      );
      row.append(numberCell, seatCell, remove);
      q("room-rows").append(row);
      roomRows.push({ number, seats, remove, row });

      on(number, "input", () => {
        d.layout[index].number = number.value;
        remove.setAttribute(
          "aria-label",
          `Remove table ${number.value || index + 1}`,
        );
        number.setAttribute("aria-invalid", "false");
        updateRoomState();
      });
      on(seats, "input", () => {
        d.layout[index].seats = seats.value;
        seats.setAttribute("aria-invalid", "false");
        updateRoomState();
      });
      on(remove, "click", () => {
        if (!roomCanEdit()) return;
        d.layout.splice(index, 1);
        renderRoomRows();
        updateRoomState();
        const next = roomRows[Math.min(index, roomRows.length - 1)];
        (next ? next.remove : q("add-table")).focus({ preventScroll: true });
      });
    });
  }

  function renderRoom() {
    const e = event();
    const d = roomDraft();
    const locked = roomLocked();
    input("bulk-count").value = String(d.layout.length || 1);
    input("bulk-seats").value = d.layout[0]?.seats || "5";
    input("cutoff").value = d.cutoff;
    input("minimum-size").value = d.minSize;
    input("auto-match").checked = d.autoMatch;
    input("auto-publish").checked = d.autoPublish;
    q("cutoff-zone").textContent = `${e.zone} · event local time`;
    q<HTMLFieldSetElement>("room-controls").disabled = locked;
    q("room-lock").hidden = !locked;
    q("room-lock-message").textContent = e.running
      ? "Matching is running. The room is temporarily locked."
      : e.published
        ? "These tables have published assignments. The setup is read-only to keep everyone’s seat consistent."
        : "A draft seating plan uses this layout. Discard the draft if you need to change the room.";
    q("discard-plan").hidden = !e.plan || e.published || Boolean(e.running);
    renderRoomRows();
    fieldError("room-error", "");
    updateRoomState();
  }

  on(q("use-layout"), "click", () => {
    if (!roomCanEdit()) return;
    const count = Number(input("bulk-count").value);
    const seats = Number(input("bulk-seats").value);
    if (
      !Number.isInteger(count) ||
      count < 1 ||
      count > 50 ||
      !Number.isInteger(seats) ||
      seats < 2 ||
      seats > 12
    ) {
      fieldError("room-error", "Use 1–50 tables and 2–12 seats each.");
      return;
    }
    roomDraft().layout = uniformLayout(count, seats).map((t) => ({
      number: String(t.number),
      seats: String(t.seats),
    }));
    renderRoomRows();
    fieldError("room-error", "");
    updateRoomState();
  });

  on(q("add-table"), "click", () => {
    if (!roomCanEdit()) return;
    const d = roomDraft();
    if (d.layout.length >= 50) return;
    const used = new Set(d.layout.map((t) => Number(t.number)));
    let number = 1;
    while (used.has(number)) number++;
    d.layout.push({
      number: String(number),
      seats: String(Number(input("bulk-seats").value) || 5),
    });
    renderRoomRows();
    updateRoomState();
    const last = roomRows[roomRows.length - 1];
    last.number.focus();
    animate(
      last.row,
      [
        { opacity: 0, transform: "translateY(-5px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 190 },
    );
  });

  on(q("cutoff"), "input", () => {
    roomDraft().cutoff = input("cutoff").value;
    updateRoomState();
  });
  on(q("minimum-size"), "input", () => {
    roomDraft().minSize = input("minimum-size").value;
    updateRoomState();
  });
  on(q("auto-match"), "change", () => {
    const d = roomDraft();
    d.autoMatch = input("auto-match").checked;
    if (!d.autoMatch) {
      d.autoPublish = false;
      input("auto-publish").checked = false;
    }
    updateRoomState();
  });
  on(q("auto-publish"), "change", () => {
    roomDraft().autoPublish =
      input("auto-match").checked && input("auto-publish").checked;
    updateRoomState();
  });

  on(q("room-form"), "submit", (ev) => {
    ev.preventDefault();
    if (!roomCanEdit()) return;
    const e = event();
    const d = roomDraft();
    d.layout = roomRows.map((r) => ({
      number: r.number.value,
      seats: r.seats.value,
    }));
    d.cutoff = input("cutoff").value;
    d.minSize = input("minimum-size").value;
    d.autoMatch = input("auto-match").checked;
    d.autoPublish = d.autoMatch && input("auto-publish").checked;

    let message = "";
    let invalid: HTMLElement | null = null;
    const seen = new Set<number>();
    if (!d.layout.length) message = "Add at least one table.";
    for (let i = 0; i < d.layout.length && !message; i++) {
      const n = Number(d.layout[i].number);
      const s = Number(d.layout[i].seats);
      if (!Number.isInteger(n) || n < 1 || n > 999) {
        message = "Use a table number from 1 to 999.";
        invalid = roomRows[i].number;
      } else if (seen.has(n)) {
        message = `Each table needs a unique number. Table ${n} is listed twice.`;
        invalid = roomRows[i].number;
      } else if (!Number.isInteger(s) || s < 2 || s > 12) {
        message = "Use 2–12 seats for each table.";
        invalid = roomRows[i].seats;
      }
      seen.add(n);
    }
    const minimum = Number(d.minSize);
    if (
      !message &&
      (!Number.isInteger(minimum) || minimum < 2 || minimum > 6)
    ) {
      message = "The smallest table must have 2–6 people.";
      invalid = q("minimum-size");
    }
    if (!message && d.layout.some((t) => Number(t.seats) < minimum)) {
      message = "The minimum group size cannot exceed any table’s seat count.";
      invalid = q("minimum-size");
    }
    if (
      !message &&
      (!d.cutoff ||
        !Number.isFinite(Date.parse(d.cutoff)) ||
        d.cutoff >= `${e.date}T${e.start}`)
    ) {
      message = "Choose an enrollment cutoff before lunch starts.";
      invalid = q("cutoff");
    }
    if (
      !message &&
      d.cutoff !== roomSnapshot(e).cutoff &&
      Date.parse(localToUtc(d.cutoff, e.zone)) <= Date.now()
    ) {
      // A past cutoff would close sign-ups (and maybe match) the moment it saves.
      message = "Choose an enrollment cutoff that hasn’t passed yet.";
      invalid = q("cutoff");
    }
    if (message) {
      fieldError("room-error", message);
      if (invalid) {
        invalid.setAttribute("aria-invalid", "true");
        invalid.focus();
      }
      updateRoomState();
      return;
    }

    fieldError("room-error", "");
    const saved = {
      tables: d.layout.map((t) => ({
        number: Number(t.number),
        seats: Number(t.seats),
      })),
      enrollment_closes_at: localToUtc(d.cutoff, e.zone),
      min_table_size: minimum,
      auto_match: d.autoMatch,
      auto_publish: d.autoMatch && d.autoPublish,
    };
    void act(async () => {
      await api<EventConfig>("/api/host/event", eventConfig(saved), "PUT");
      roomDrafts.delete(e.id);
      return `Room setup saved. ${
        !saved.auto_match
          ? "Run matching manually when you’re ready."
          : saved.auto_publish
            ? "The cutoff run will publish assignments automatically."
            : "The cutoff run will create a draft for review."
      }`;
    });
  });

  on(q("manage-tables"), "click", () => show("tables"));
  on(q("room-view-plan"), "click", () => show("matching"));
  on(q("room-demo-cutoff"), "click", () => {
    show("matching");
    runMatching({ automatic: true });
  });
  on(q("discard-plan"), "click", () => {
    const e = event();
    if (!e.plan || e.published || e.running) return;
    void act(async () => {
      const { deleted } = await api<{ deleted: number }>(
        "/api/host/runs",
        undefined,
        "DELETE",
      );
      runs.clear();
      show("tables");
      return `Discarded ${deleted} draft seating plan${deleted === 1 ? "" : "s"}. Attendee profiles and table settings are unchanged.`;
    });
  });

  // Matching settings

  function matchingBlock(e = event()) {
    if (!e.roomConfigured) return "Finish and save your Tables setup first.";
    if (roomDirty(e)) {
      return "You have unsaved table changes. Save them in Tables before matching.";
    }
    if (e.ready < e.minSize) {
      return `At least ${e.minSize} completed profiles are needed for your minimum group size.`;
    }
    if (capacity(e) < e.ready) {
      return `There are ${e.ready} ready attendees but only ${capacity(e)} seats. Add seats in Tables before matching.`;
    }
    return "";
  }

  function renderMatchingSettings() {
    const e = event();
    const blocked = matchingBlock();
    q("match-zone").textContent = `${e.zone} · event local time`;
    q("cutoff-summary").textContent = scheduleLabel(e.cutoff);
    q("schedule-copy").textContent = !e.roomConfigured
      ? "Finish your room setup to schedule matching."
      : !e.autoMatch
        ? "Automatic matching is off. Run it manually, or enable it in Tables."
        : e.autoPublish
          ? "At the cutoff, the seating plan and table reveal are published automatically."
          : "At the cutoff, TableTalk creates a draft seating plan for you to review and publish.";
    q("room-matching-note").textContent =
      blocked ||
      `${e.layout.length} configured tables · ${capacity(e)} total seats · minimum ${e.minSize} people per table`;
    q("match-badge").textContent = e.published
      ? "Published"
      : e.running
        ? "Matching…"
        : e.plan
          ? "Plan ready"
          : !e.roomConfigured
            ? "Setup needed"
            : e.autoMatch
              ? "Scheduled"
              : "Manual";
    button("run-now").disabled =
      Boolean(blocked) || Boolean(e.plan) || Boolean(e.running);
    q("run-help").textContent = e.plan
      ? "A seating plan already exists for this event."
      : blocked ||
        "Creates a draft using the saved Tables setup. Attendees see it when you publish.";
    q("demo-schedule").hidden =
      !e.roomConfigured || !e.autoMatch || Boolean(e.plan);
    button("demo-schedule").disabled = Boolean(blocked) || Boolean(e.running);
  }

  // Everything that depends on the active event

  function refresh() {
    const e = event();
    const total = capacity(e);
    refreshEventSelect();
    updateBreadcrumb();
    q("top-status").textContent = e.published
      ? "Tables published"
      : e.enrollmentClosed
        ? "Enrollment closed"
        : "Registration open";
    q("overview-title").textContent = e.name;
    q("event-meta").textContent =
      `${dateLabel(e.date)} · ${e.location} · ${timeLabel(e.start)}–${timeLabel(e.end)}`;
    q("registered").textContent = String(e.registered);
    q("ready-summary").textContent =
      `${e.ready} ready to match · ${e.registered - e.ready} still completing their profile`;
    q("capacity-summary").textContent = `${total} seats total`;
    q("setup-detail").textContent = e.roomConfigured
      ? `${e.layout.length} numbered tables · ${total} seats · cutoff ${timeLabel(e.cutoff.slice(11, 16))}`
      : "Choose your tables, seat counts, and enrollment cutoff.";
    q("matching-detail").textContent = e.published
      ? "Table assignments are published."
      : e.plan
        ? "Your seating plan is ready for review."
        : e.schedule
          ? `Scheduled for ${scheduleLabel(e.schedule)}.`
          : "Set the cutoff in Tables or run matching manually.";
    q("updates-detail").textContent = e.messages.length
      ? `${e.messages.length} update${e.messages.length === 1 ? "" : "s"} recorded for this event.`
      : "Reminders, table reveals, and the occasional change of plan.";
    const [clock, meridiem] = timeLabel(e.start).split(" ");
    q("lunch-time").replaceChildren(
      document.createTextNode(clock),
      el("span", {}, ` ${meridiem}`),
    );
    q("location").textContent = e.location;
    q("plan-date").textContent = dateLabel(e.date);
    q("plan-matching").textContent = e.plan
      ? e.published
        ? "Published"
        : "Ready for review"
      : !e.roomConfigured
        ? "Setup needed"
        : e.schedule
          ? timeLabel(e.schedule.slice(11, 16))
          : "Manual";
    q("plan-tables").textContent = `${e.layout.length} tables · ${total} seats`;
    q("plan-zone").textContent = e.zone;
    q("poster-event").textContent = e.name;
    q("poster-details").textContent =
      `${dateLabel(e.date)} · ${timeLabel(e.start)} · ${e.location}`;
    input("signup-url").value = signupUrl();
    // qrSvg only writes numbers into fixed markup, so it is safe as HTML.
    q("qr").innerHTML = qrSvg(signupUrl());
    q("qr").setAttribute("aria-label", `QR code for ${signupUrl()}`);
    q("ready-label").textContent = `${e.ready} attendees ready to match`;
    const incomplete = e.registered - e.ready;
    q("incomplete-label").textContent =
      `${incomplete} incomplete profile${incomplete === 1 ? " stays" : "s stay"} out of this round.`;
    renderMatchingSettings();
    q<HTMLOptionElement>("ready-template").disabled = !e.published;
    if (select("template").value === "ready" && !e.published) {
      select("template").value = "reminder";
      fillTemplate();
    }
    q<HTMLOptionElement>("matched-option").disabled = !e.published;
    if (select("audience").value === "matched" && !e.published) {
      select("audience").value = "all";
    }
    renderPlan();
    renderHistory();
    updatePreview();
    if (currentView === "tables") renderRoom();
  }

  // Creating and editing events

  function fillSetup(edit: boolean) {
    editing = edit;
    const e = event();
    q("setup-title").textContent = edit
      ? "The scene, adjusted."
      : "Create your event.";
    q("save-event").textContent = edit ? "Save changes →" : "Create event →";
    input("event-name").value = edit ? e.name : "";
    input("event-date").value = edit ? e.date : "2026-11-20";
    const zones = select("event-zone");
    if (![...zones.options].some((o) => o.value === e.zone)) {
      zones.append(el("option", { value: e.zone }, e.zone));
    }
    zones.value = edit ? e.zone : "America/Denver";
    input("event-location").value = edit ? e.location : "";
    input("lunch-start").value = edit ? e.start : "12:30";
    input("lunch-end").value = edit ? e.end : "13:15";
    fieldError("event-error", "");
    show("setup");
  }

  // One event per deployment: "Create event" edits it instead.
  on(q("create"), "click", () => fillSetup(true));
  on(q("edit-event"), "click", () => fillSetup(true));
  on(q("cancel-event"), "click", () => show("overview"));
  root.querySelectorAll<HTMLElement>("[data-view]").forEach((b) =>
    on(b, "click", () => {
      q("toast").hidden = true;
      if (isView(b.dataset.view)) show(b.dataset.view);
    }),
  );
  root.querySelectorAll<HTMLElement>("[data-action]").forEach((b) =>
    on(b, "click", () => {
      const action = b.dataset.action;
      if (action === "edit") fillSetup(true);
      else if (isView(action)) show(action);
    }),
  );
  on(q("overview-qr"), "click", () => show("signup"));
  on(q("event-form"), "submit", (ev) => {
    ev.preventDefault();
    const name = input("event-name").value.trim();
    const date = input("event-date").value;
    const start = input("lunch-start").value;
    const end = input("lunch-end").value;
    const location = input("event-location").value.trim();
    if (!name || !date || !start || !end || !location) {
      fieldError(
        "event-error",
        "Add the event name, date, lunch times, and location.",
      );
      return;
    }
    if (end <= start) {
      fieldError(
        "event-error",
        "Choose a lunch end time after the start time.",
      );
      return;
    }
    const settings = {
      name,
      date,
      start,
      end,
      location,
      zone: select("event-zone").value,
    };
    // Move the cutoff with the event date, and keep it before lunch starts.
    const e = event();
    const days =
      (Date.parse(`${date}T12:00:00Z`) - Date.parse(`${e.date}T12:00:00Z`)) /
      86400000;
    const shift = (value: string) => {
      const stamp = Date.parse(`${value}Z`);
      return Number.isFinite(stamp)
        ? new Date(stamp + days * 86400000).toISOString().slice(0, 16)
        : value;
    };
    const moved =
      date !== e.date || start !== e.start || settings.zone !== e.zone;
    let cutoff = e.cutoff;
    if (moved) {
      cutoff = shift(e.cutoff);
      if (cutoff >= `${date}T${start}`) cutoff = defaultCutoff(date, start);
    }
    const previous = host?.event.enrollment_closes_at ?? null;
    const closesAt = !e.roomConfigured
      ? null
      : moved
        ? localToUtc(cutoff, settings.zone)
        : previous;
    if (
      closesAt &&
      (!previous || Date.parse(closesAt) !== Date.parse(previous)) &&
      Date.parse(closesAt) <= Date.now()
    ) {
      // Saving would close sign-ups (and maybe match) immediately.
      fieldError(
        "event-error",
        `This change would move the enrollment cutoff to ${scheduleLabel(cutoff)}, which has already passed. Pick a later date or update the cutoff in Tables first.`,
      );
      return;
    }
    const roomDraftNow = roomDrafts.get(e.id);
    if (roomDraftNow && moved) {
      roomDraftNow.cutoff = shift(roomDraftNow.cutoff);
      if (roomDraftNow.cutoff >= `${date}T${start}`) {
        roomDraftNow.cutoff = defaultCutoff(date, start);
      }
    }
    const config = eventConfig({
      name: settings.name,
      date: settings.date,
      timezone: settings.zone,
      location: settings.location,
      lunch_start: settings.start,
      lunch_end: settings.end,
      lunch_time: lunchLabel(settings.start, settings.end),
      enrollment_closes_at: closesAt,
    });
    void act(async () => {
      await api<EventConfig>("/api/host/event", config, "PUT");
      fillTemplate();
      show("overview");
      return "Event details saved. Attendees see the new name, time, and place.";
    });
  });

  on(q("load-samples"), "click", () => {
    const e = event();
    if (e.enrollmentClosed) {
      toast("Sign-ups are closed, so demo attendees can’t be added.");
      return;
    }
    const source = select("sample-source").value;
    if (e.plan) runs.clear();
    void act(async () => {
      const result = await api<DemoResult>("/api/host/demo/attendees", {
        source,
        count: 24,
      });
      return result.created
        ? `Added ${result.created} demo attendee${result.created === 1 ? "" : "s"}.${
            e.plan
              ? " Discard the draft and run matching again to seat them."
              : ""
          }`
        : "Those demo attendees are already here.";
    });
  });

  // Signup link, QR code, and printable sign

  on(q("copy-link"), "click", async () => {
    try {
      if (!navigator.clipboard) throw new Error("unavailable");
      await navigator.clipboard.writeText(signupUrl());
      toast("Signup link copied.");
    } catch {
      input("signup-url").focus();
      input("signup-url").select();
      toast("Signup link selected. Copy it with your keyboard.");
    }
  });

  function download(text: string, mime: string, filename: string) {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const link = el("a", { href: url, download: filename });
    root.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function printableSign() {
    const e = event();
    const url = signupUrl();
    const details = `${dateLabel(e.date)} · ${timeLabel(e.start)} · ${e.location}`;
    return (
      `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">` +
      `<title>${escapeHtml(e.name)} — TableTalk signup sign</title><style>${SIGN_STYLES}</style></head>` +
      `<body><div class="poster"><div class="brand">TableTalk</div><div class="event">${escapeHtml(e.name)}</div>` +
      `<h1>Lunch with<br>your kind of people.</h1><div class="qr">${qrSvg(url)}</div>` +
      `<p><b>Scan to join the conversation.</b></p><p class="meta">${escapeHtml(details)}</p>` +
      `<p class="url">${escapeHtml(url)}</p>` +
      `<button class="print" onclick="window.print()">Print this sign</button></div></body></html>`
    );
  }

  on(q("download-qr"), "click", () => {
    download(
      qrSvg(signupUrl()),
      "image/svg+xml",
      `tabletalk-${event().id}-qr.svg`,
    );
    toast("QR code downloaded. It opens the signup link shown above.");
  });
  on(q("print-sign"), "click", () => {
    download(
      printableSign(),
      "text/html",
      `tabletalk-${event().id}-print-sign.html`,
    );
    toast("Printable sign downloaded. Open the HTML file, then choose Print.");
  });

  // Matching: schedule or run now, review, publish

  function setMode(mode: "schedule" | "now") {
    q("mode-schedule").setAttribute(
      "aria-pressed",
      String(mode === "schedule"),
    );
    q("mode-now").setAttribute("aria-pressed", String(mode === "now"));
    q("schedule-panel").hidden = mode !== "schedule";
    q("now-panel").hidden = mode !== "now";
  }
  on(q("mode-schedule"), "click", () => setMode("schedule"));
  on(q("mode-now"), "click", () => setMode("now"));

  function runMatching({ automatic = false } = {}) {
    const e = event();
    if (e.running) return;
    if (e.plan) {
      toast("This event already has a seating plan.");
      return;
    }
    const block = matchingBlock(e);
    if (block) {
      toast(block);
      return;
    }
    if (busy) {
      toast("Still saving your last change. Try again in a moment.");
      return;
    }
    running = true;
    refresh();
    q("seating-state").textContent = "FINDING THEIR PEOPLE…";
    void act(async () => {
      let published = false;
      try {
        const made = await api<MatchRun>("/api/host/runs", {});
        runs.set(`/api/host/runs/${made.id}`, made);
        if (automatic && e.autoPublish) {
          await api(`/api/host/runs/${made.id}/publish`, {});
          published = true;
        }
      } finally {
        running = false;
      }
      if (singleColumn()) later(() => scrollToStart(q("seating-state")), 0);
      return published
        ? "Tables are matched and published. Attendees can see their tables now."
        : "Seating plan created. Review the tables, then publish.";
    });
  }
  on(q("run-now"), "click", () => runMatching());
  on(q("demo-schedule"), "click", () => runMatching({ automatic: true }));

  function renderPlan() {
    const e = event();
    q("seating-empty").hidden = Boolean(e.plan);
    q("plan-grid").hidden = !e.plan;
    q("publish-area").hidden = !e.plan;
    q("seating-state").textContent = e.published
      ? "PUBLISHED"
      : e.plan
        ? "READY TO REVIEW"
        : e.running
          ? "FINDING THEIR PEOPLE…"
          : "WAITING TO MATCH";
    q("plan-grid").replaceChildren();
    if (!e.plan) return;
    for (const table of e.plan) {
      const details = el("details", { class: "ad-plan-table" });
      const summary = el("summary", { class: "cursor-interaction" });
      summary.append(
        el("strong", {}, table.number),
        el(
          "span",
          {},
          `${table.name ? `${table.name} · ` : ""}${table.people.length} / ${table.seats} seats`,
        ),
        el("small", {}, "View tablemates ↓"),
      );
      details.append(summary, el("p", {}, table.people.join(" · ")));
      q("plan-grid").append(details);
    }
    const unused = e.layout.length - e.plan.length;
    const late = e.registered - matchedCount();
    q("plan-summary").textContent =
      `${matchedCount()} attendees · ${e.plan.length} active tables${unused ? ` · ${unused} unused` : ""}${
        late > 0 && !e.published
          ? ` · ${late} enrolled after this plan. Discard it in Tables and run again so they get a seat.`
          : ""
      }`;
    button("publish").disabled = e.published;
    q("publish").textContent = e.published
      ? "Table assignments published ✓"
      : "Publish table assignments ↗";
  }

  on(q("publish"), "click", () => {
    const e = event();
    if (!e.plan || e.published || !draft) return;
    const id = draft.id;
    void act(async () => {
      await api(`/api/host/runs/${id}/publish`, {});
      return "Table assignments published. Attendees see their tables now; send an update to let them know.";
    });
  });

  // Updates: compose, preview, review, and record

  function fillTemplate() {
    const e = event();
    const template = select("template").value;
    input("message-title").value =
      template === "ready"
        ? "Your table is ready."
        : template === "announcement"
          ? ""
          : "Lunch is almost here.";
    textarea("message-body").value =
      template === "ready"
        ? `Your lunch table has been matched. Open TableTalk to see your table number and meet your people. See you in ${e.location} at ${timeLabel(e.start)}.`
        : template === "announcement"
          ? ""
          : `Lunch starts at ${timeLabel(e.start)} in ${e.location}. Come back to TableTalk for your table assignment. Good conversations are on the menu.`;
    input("message-time").value = `${e.date}T12:20`;
    q("message-review").hidden = true;
    updatePreview();
  }

  function updatePreview() {
    q("preview-title").textContent =
      input("message-title").value.trim() || "Your announcement title";
    q("preview-body").textContent =
      textarea("message-body").value.trim() ||
      "A short, useful update for the people coming to lunch.";
    const count = recipients();
    q("preview-audience").textContent =
      `${count} RECIPIENT${count === 1 ? "" : "S"}`;
  }

  on(q("template"), "change", fillTemplate);
  ["message-title", "message-body"].forEach((id) =>
    on(q(id), "input", () => {
      q("message-review").hidden = true;
      updatePreview();
    }),
  );
  on(q("audience"), "change", () => {
    q("message-review").hidden = true;
    updatePreview();
  });
  on(q("message-when"), "change", () => {
    q("message-time-wrap").hidden = select("message-when").value !== "schedule";
    q("message-review").hidden = true;
  });
  on(q("message-time"), "change", () => {
    q("message-review").hidden = true;
  });

  on(q("message-form"), "submit", (ev) => {
    ev.preventDefault();
    const e = event();
    const title = input("message-title").value.trim();
    const body = textarea("message-body").value.trim();
    const when = select("message-when").value as Delivery;
    const time = input("message-time").value;
    if (!title || !body) {
      fieldError("message-error", "Add a title and message before reviewing.");
      return;
    }
    if (!recipients()) {
      fieldError("message-error", "No attendees are in this audience yet.");
      return;
    }
    if (
      when === "schedule" &&
      (!time || time.slice(0, 10) !== e.date || time.slice(11, 16) >= e.end)
    ) {
      fieldError(
        "message-error",
        "Choose a send time on the event date, before lunch ends.",
      );
      return;
    }
    fieldError("message-error", "");
    messageDraft = {
      eventId: "event",
      title,
      body,
      audience: select("audience").value as Audience,
      count: recipients(),
      when,
      time,
    };
    q("review-detail").textContent =
      `${messageDraft.count} attendees · in-app inbox · ${
        when === "now" ? "Send now" : `${scheduleLabel(time)} · ${e.zone}`
      }`;
    q("review-send").textContent =
      when === "now" ? "Send update" : "Schedule update";
    q("message-review").hidden = false;
    q("review-send").focus({ preventScroll: true });
    if (singleColumn()) scrollToStart(q("preview-title"));
  });
  on(q("review-cancel"), "click", () => {
    q("message-review").hidden = true;
  });

  on(q("review-send"), "click", () => {
    if (!messageDraft) return;
    const sent = messageDraft;
    const zone = event().zone;
    void act(async () => {
      await api("/api/host/announcements", {
        title: sent.title,
        body: sent.body,
        audience: sent.audience,
        send_at: sent.when === "schedule" ? localToUtc(sent.time, zone) : null,
      });
      q("message-review").hidden = true;
      messageDraft = null;
      return sent.when === "now"
        ? `Update sent to ${sent.count} attendee inbox${sent.count === 1 ? "" : "es"}.`
        : `Update scheduled for ${scheduleLabel(sent.time)}.`;
    });
  });

  function renderHistory() {
    const messages = event().messages;
    q("history-empty").hidden = messages.length > 0;
    q("history-list").replaceChildren(
      ...messages.map((m) => {
        const item = el("div", { class: "ad-history-item" });
        item.append(
          el("b", {}, m.title),
          el(
            "span",
            {},
            `${m.status} · ${m.count} recipients${m.when === "schedule" ? ` · ${scheduleLabel(m.time)}` : ""}`,
          ),
        );
        return item;
      }),
    );
  }

  const hook: Readonly<OrganizerHook> = Object.freeze({
    qrMatrix,
    qrSvg,
    printableSign,
    getEvents: () => [clone(event())],
    getActive: () => clone(event()),
    getRoomDraft: () => clone(roomDraft()),
  });
  window.tabletalkAdminDemo = hook;

  on(q("token-form"), "submit", (ev) => {
    ev.preventDefault();
    token = input("host-token").value.trim();
    sessionStorage.setItem(HOST_TOKEN_KEY, token);
    q("token-form").hidden = true;
    void act(async () => "Unlocked.");
  });

  refresh();
  setMode("schedule");
  show("overview", { focus: false });
  q("seating-state").textContent = "LOADING…";
  void act(
    async () => {
      await load();
      fillTemplate();
    },
    { reload: false },
  );

  // Pick up changes made elsewhere: new sign-ups, or the cutoff run.
  const poll = window.setInterval(() => {
    const typing = currentView === "tables" || currentView === "setup";
    if (busy || running || typing || document.hidden || !host) return;
    // A background read never blocks the organizer's own actions.
    load()
      .then(() => {
        if (!busy && !signal.aborted) refresh();
      })
      .catch(() => undefined);
  }, REFRESH_MS);

  return () => {
    window.clearInterval(poll);
    controller.abort();
    timers.forEach((id) => window.clearTimeout(id));
    timers.clear();
    if (window.tabletalkAdminDemo === hook) delete window.tabletalkAdminDemo;
  };
}
