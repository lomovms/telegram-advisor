export type Account = "work" | "personal";

export type Profile = {
  id: number;
  chat_name: string;
  account: Account;
  preview: string;
  last_message_date: string;
  avatar_url: string;
  ai_unread: boolean;
  unread: boolean;
  ui_last_seen_message_id: number;
  contact_kind: "business" | "personal";
  autopilot_enabled: boolean;
};

export type Message = {
  telegram_message_id: number | null;
  message_date: string;
  sender: string;
  out: boolean;
  delivery_status: "" | "sent" | "read";
  text: string;
  media_url?: string;
  media_type?: string;
  reply_to_telegram_message_id?: number | null;
};

export type ProfileDetail = Profile & {
  profile: {
    agreements: string;
    payment_promises: string;
    disputed_points: string;
    behavior_patterns: string;
    communication_style: string;
    tone_recommendations: string;
  };
  messages: Message[];
  last_analysis: Analysis | null;
  autopilot_enabled: boolean;
  autopilot_delay_seconds: number;
  autopilot_status: "off" | "watching" | "waiting" | "thinking" | "sending" | "error";
  topics: string[];
  active_topic: string;
  topic_rules: Record<string, string>;
  conversation_goal: string;
  history_has_more: boolean;
};

export type Analysis = {
  situation_summary: string;
  client_intent: string;
  risk: string;
  recommended_tone: string;
  tone_reason: string;
  recommended_strategy: string;
  do_not_do: string[];
  best_reply: string;
  soft_reply: string;
  hard_reply: string;
  confidence: number;
  context?: {
    message_count: number;
    contact: string;
    relationship: string;
    tone: string;
    goal: string;
    topic: string;
    role: string;
    topic_memory: boolean;
  };
};

export type OnboardingStatus = {
  required: boolean;
  complete: boolean;
  accounts: Record<Account, { credentials_saved: boolean; authorized: boolean }>;
  codex: { installed: boolean; authenticated: boolean; message: string };
  data_dir: string;
};

export type TelegramAuthStatus = {
  status: "idle" | "starting" | "waiting_scan" | "password_required" | "authorized" | "error";
  qr_data_url: string;
  message: string;
};

export type AnalysisModel = {
  id: "gpt-5.6-luna" | "gpt-5.6-terra" | "gpt-5.6-sol";
  label: string;
};

export type AssistantRole = {
  id: "general" | "marketer" | "developer" | "project_manager";
  label: string;
};

export type GmailMailMessage = {
  id: string;
  thread_id: string;
  sender: string;
  subject: string;
  rfc_message_id?: string;
  date: string;
  timestamp: number;
  text: string;
  technical_text: string;
  out: boolean;
};

export type CrmLead = {
  id: number;
  source: string;
  external_id: string;
  contact_name: string;
  email: string;
  status: "interested" | "reply" | "not_relevant";
  category: string;
  summary: string;
  next_action: string;
  last_message_date: string;
  updated_at: string;
};

export type CrmTaskPreview = {
  id: number;
  profile_id: number | null;
  title: string;
  status: "planned" | "progress" | "waiting_payment" | "done" | "completed_by_me";
  deadline: string;
  notes: string;
};

export type GmailContact = {
  id: string;
  name: string;
  email: string;
  date: string;
  preview: string;
  message_count: number;
  thread_ids: string[];
  messages: GmailMailMessage[];
  crm_lead?: CrmLead | null;
};

export type GmailAccount = {
  id: string;
  email: string;
  label: string;
  connected: boolean;
  can_send: boolean;
};

export type GoogleContactGroup = { id: string; name: string; member_count: number };
export type GmailContactLabelStatus = { state: "idle" | "running" | "done" | "error"; message: string; current: number; total: number };

export type MaxContact = GmailContact;

export type WorkProfile = {
  id: number;
  about: string;
  skills: string;
  base_rate: number;
  minimum_order: number;
  pricing_rules: string;
  risk_rules: string;
  style: string;
  boundaries: string;
  updated_at: string;
};

export type MemorySource = {
  id: number;
  channel: "telegram" | "gmail" | "max";
  external_message_id: string;
  occurred_at: string;
  sender: string;
  text: string;
  source_excerpt: string;
  locator: { profile_id?: number; telegram_message_id?: number | null };
};

export type MemoryItem = {
  id: number;
  contact_id: number;
  project_id: number | null;
  kind: "task" | "commitment" | "money_event" | "follow_up" | "status" | "note";
  category: string;
  title: string;
  details: string;
  actor: "me" | "them" | "unknown";
  status: string;
  amount: number;
  currency: string;
  due_at: string;
  certainty: "CONFIRMED" | "INFERRED" | "UNCERTAIN";
  confidence: number;
  created_by: "AI" | "manual";
  created_at: string;
  updated_at: string;
  sources: MemorySource[];
};

export type WorkingMemory = {
  contact: {
    id: number;
    legacy_profile_id: number;
    display_name: string;
    relationship_status: string;
    context_summary: string;
    next_action: string;
    next_contact_at: string;
    last_analyzed_at: string;
  };
  items: MemoryItem[];
  totals: {
    agreed: number;
    received: number;
    expected: number;
    payable: number;
    paid_out: number;
    remaining: number;
  };
  last_run: { status: string; error: string; updated_at: string } | null;
  changed_item_ids?: number[];
};

export type AppSettings = {
  user_style: string;
  analysis_model: AnalysisModel["id"];
  analysis_models: AnalysisModel[];
  assistant_role: AssistantRole["id"];
  assistant_roles: AssistantRole[];
  weekend_policy: string;
  after_hours_policy: string;
  workday_start_hour: number;
  workday_end_hour: number;
  notifications_enabled: boolean;
  notification_chat: string;
  calendar_context: string;
  gmail_connected: boolean;
  gmail_can_send: boolean;
  gmail_client_id: string;
  gmail_label: string;
  gmail_email: string;
  gmail_active_account_id: string;
  gmail_accounts: GmailAccount[];
  gmail_reply_brief: string;
  max_connected: boolean;
  max_bot_name: string;
};

const isViteDev =
  ["127.0.0.1", "localhost"].includes(location.hostname) &&
  location.port === "5173";
const isTauriApp = "__TAURI_INTERNALS__" in window;

const apiBase =
  import.meta.env.VITE_API_URL ||
  (isViteDev || !isTauriApp ? "" : "http://127.0.0.1:18791");

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    cache: "no-store",
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || "Локальный API вернул ошибку.");
  }
  return payload;
}

export const api = {
  health: () => request<{ ok: boolean; backend: string }>("/api/health"),
  projects: () => request<{ projects: CrmTaskPreview[] }>("/api/projects"),
  onboarding: () => request<OnboardingStatus>("/api/onboarding"),
  saveTelegramCredentials: (account: Account, apiId: string, apiHash: string, phone: string) =>
    request<OnboardingStatus>("/api/telegram/credentials", {
      method: "POST",
      body: JSON.stringify({ account, api_id: apiId, api_hash: apiHash, phone }),
    }),
  startTelegramAuth: (account: Account) =>
    request<TelegramAuthStatus>(`/api/telegram/auth/${account}/start`, { method: "POST", body: "{}" }),
  telegramAuthStatus: (account: Account) =>
    request<TelegramAuthStatus>(`/api/telegram/auth/${account}`),
  submitTelegramPassword: (account: Account, password: string) =>
    request<TelegramAuthStatus>(`/api/telegram/auth/${account}/password`, {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  startCodexLogin: () => request<{ ok: true }>("/api/codex/login", { method: "POST", body: "{}" }),
  profiles: (account: Account) => request<{ profiles: Profile[] }>(`/api/profiles?account=${account}`),
  profile: (id: number) => request<ProfileDetail>(`/api/profiles/${id}`),
  workProfile: () => request<WorkProfile>("/api/work-profile"),
  saveWorkProfile: (profile: WorkProfile) => request<WorkProfile>("/api/work-profile", {
    method: "POST",
    body: JSON.stringify(profile),
  }),
  workingMemory: (id: number) => request<WorkingMemory>(`/api/profiles/${id}/working-memory`),
  updateWorkingMemory: (id: number) => request<WorkingMemory>(`/api/profiles/${id}/working-memory`, { method: "POST", body: "{}" }),
  updateMemoryItem: (id: number, changes: Partial<MemoryItem>) => request<MemoryItem>(`/api/memory-items/${id}`, {
    method: "POST",
    body: JSON.stringify(changes),
  }),
  deleteMemoryItem: (id: number) => request<{ ok: true }>(`/api/memory-items/${id}`, { method: "DELETE" }),
  messagesAround: (profileId: number, messageId: number) => request<{ messages: Message[] }>(`/api/profiles/${profileId}/messages/around?message_id=${messageId}`),
  send: (id: number, text: string, replyTo?: number | null) => request<Message>(`/api/profiles/${id}/send`, { method: "POST", body: JSON.stringify({ text, reply_to: replyTo ?? null }) }),
  refresh: (id: number) => request<{ added: number }>(`/api/profiles/${id}/refresh`, { method: "POST", body: "{}" }),
  rebuildProfile: (id: number) => request<Profile>(`/api/profiles/${id}/profile-refresh`, { method: "POST", body: "{}" }),
  syncHistory: (id: number) => request<{ added: number }>(`/api/profiles/${id}/history`, { method: "POST", body: "{}" }),
  importHtmlHistory: (id: number, files: Array<{ name: string; content: string }>) =>
    request<{ messages: number; files: number }>(`/api/profiles/${id}/html-history`, { method: "POST", body: JSON.stringify({ files }) }),
  setAutopilot: (id: number, enabled: boolean, delaySeconds: number) =>
    request<{ autopilot_enabled: boolean; autopilot_delay_seconds: number; autopilot_status: string }>(`/api/profiles/${id}/autopilot`, {
      method: "POST",
      body: JSON.stringify({ enabled, delay_seconds: delaySeconds }),
    }),
  markViewed: (id: number) => request<Profile>(`/api/profiles/${id}/viewed`, { method: "POST", body: "{}" }),
  setContactKind: (id: number, kind: "business" | "personal") =>
    request<Profile>(`/api/profiles/${id}/contact-kind`, {
      method: "POST",
      body: JSON.stringify({ kind }),
    }),
  setTopic: (id: number, topic: string, rule?: string) =>
    request<{ topics: string[]; active_topic: string; topic_rules: Record<string, string>; last_analysis: Analysis | null }>(`/api/profiles/${id}/topic`, {
      method: "POST",
      body: JSON.stringify({ topic, ...(rule === undefined ? {} : { rule }) }),
    }),
  setConversationGoal: (id: number, goal: string) => request<{ conversation_goal: string }>(`/api/profiles/${id}/goal`, {
    method: "POST",
    body: JSON.stringify({ goal }),
  }),
  createProjectFromProfile: (id: number) => request<{ projects: Array<{ id: number; title: string }>; found: number }>(`/api/profiles/${id}/project`, {
    method: "POST",
    body: "{}",
  }),
  profileProjects: (id: number) => request<{ projects: CrmTaskPreview[] }>(`/api/profiles/${id}/projects`),
  markProjectCompletedByMe: (id: number) => request<CrmTaskPreview>(`/api/projects/${id}/completed-by-me`, { method: "POST", body: "{}" }),
  olderMessages: (id: number, beforeMessageId: number) => request<{ messages: Message[]; history_has_more: boolean }>(`/api/profiles/${id}/messages?before=${beforeMessageId}&limit=100`),
  analyze: (id: number, tone: string, userComment: string) =>
    request<Analysis>(`/api/profiles/${id}/analyze`, { method: "POST", body: JSON.stringify({ tone, user_comment: userComment }) }),
  rewrite: (id: number, text: string, mode: string) =>
    request<{ text: string; mode: string }>(`/api/profiles/${id}/rewrite`, {
      method: "POST",
      body: JSON.stringify({ text, mode }),
    }),
  importTelegram: (peer: string, account: Account) =>
    request<Profile>("/api/telegram/import", { method: "POST", body: JSON.stringify({ peer, account }) }),
  importRecentContacts: (account: Account, limit = 10) =>
    request<{ added: number; updated: number; total: number; account: Account }>("/api/telegram/import-recent", {
      method: "POST",
      body: JSON.stringify({ account, limit }),
    }),
  deleteProfile: (id: number) => request<{ ok: true }>(`/api/profiles/${id}`, { method: "DELETE" }),
  settings: () => request<AppSettings>("/api/settings"),
  setAnalysisModel: (model: AnalysisModel["id"]) =>
    request<{ analysis_model: AnalysisModel["id"] }>("/api/settings/model", {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  setAssistantRole: (role: AssistantRole["id"]) =>
    request<{ assistant_role: AssistantRole["id"] }>("/api/settings/role", {
      method: "POST",
      body: JSON.stringify({ role }),
    }),
  saveSettings: (userStyle: string, weekendPolicy: string, afterHoursPolicy: string, workdayStartHour: number, workdayEndHour: number, notificationsEnabled: boolean, notificationChat: string) => request<{ ok: true }>("/api/settings", {
    method: "POST",
    body: JSON.stringify({ user_style: userStyle, weekend_policy: weekendPolicy, after_hours_policy: afterHoursPolicy, workday_start_hour: workdayStartHour, workday_end_hour: workdayEndHour, notifications_enabled: notificationsEnabled, notification_chat: notificationChat }),
  }),
  testNotifications: () => request<{ results: Record<string, string> }>("/api/settings/notifications/test", { method: "POST", body: "{}" }),
  connectMax: (token: string) => request<{ max_connected: boolean; max_bot_name: string }>("/api/max/connect", {
    method: "POST",
    body: JSON.stringify({ token }),
  }),
  connectGmail: (clientId: string, clientSecret: string, label: string) => request<AppSettings>("/api/gmail/connect", {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret, label }),
  }),
  reconnectGmail: () => request<AppSettings>("/api/gmail/reconnect", { method: "POST", body: "{}" }),
  setGmailLabel: (label: string) => request<AppSettings>("/api/gmail/settings", {
    method: "POST",
    body: JSON.stringify({ label }),
  }),
  setActiveGmail: (accountId: string) => request<AppSettings>("/api/gmail/active", { method: "POST", body: JSON.stringify({ account_id: accountId }) }),
  gmailMessages: () => request<{ contacts: GmailContact[] } & AppSettings>("/api/gmail/messages"),
  refreshGmail: () => request<{ contacts: GmailContact[] } & AppSettings>("/api/gmail/refresh", { method: "POST", body: "{}" }),
  gmailContactGroups: () => request<{ groups: GoogleContactGroup[]; selected_ids: string[] }>("/api/gmail/contact-groups"),
  gmailContactLabelStatus: () => request<GmailContactLabelStatus>("/api/gmail/contact-label-status"),
  labelGmailContactGroups: (groupIds: string[]) => request<GmailContactLabelStatus>("/api/gmail/contact-label", { method: "POST", body: JSON.stringify({ group_ids: groupIds }) }),
  saveGmailLead: (contactId: string, lead: Pick<CrmLead, "status" | "category" | "summary" | "next_action">) => request<CrmLead>("/api/gmail/lead", {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, ...lead }),
  }),
  analyzeGmail: (contactId: string, tone: string, brief: string) => request<Analysis>("/api/gmail/analyze", {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, tone, brief }),
  }),
  sendGmail: (contactId: string, text: string) => request<{ sent: boolean; message_id: string; thread_id: string }>("/api/gmail/send", {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, text }),
  }),
  maxMessages: () => request<{ contacts: MaxContact[]; max_connected: boolean; max_bot_name: string }>("/api/max/messages"),
  refreshMax: () => request<{ contacts: MaxContact[]; max_connected: boolean; max_bot_name: string }>("/api/max/refresh", { method: "POST", body: "{}" }),
  sendMax: (contactId: string, text: string) => request<{ sent: boolean }>("/api/max/send", {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, text }),
  }),
  analyzeMax: (contactId: string, tone: string, brief: string) => request<Analysis>("/api/max/analyze", {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, tone, brief }),
  }),
};

export function apiUrl(path: string): string {
  return `${apiBase}${path}`;
}
