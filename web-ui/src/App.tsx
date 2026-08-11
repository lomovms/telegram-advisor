import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import {
  Bot,
  ArrowLeft,
  BookOpenText,
  Briefcase,
  CheckCircle2,
  ChevronDown,
  Clipboard,
  LoaderCircle,
  Maximize2,
  Menu,
  MessageSquareText,
  Minus,
  Pencil,
  MoreHorizontal,
  PanelRight,
  Plus,
  KeyRound,
  Mail,
  RadioTower,
  Reply,
  RefreshCw,
  Search,
  SendHorizontal,
  Settings2,
  Sparkles,
  Smartphone,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke } from "@tauri-apps/api/core";
import { api, apiUrl, type Account, type Analysis, type AnalysisModel, type AssistantRole, type AppSettings, type CrmLead, type CrmTaskPreview, type GmailAccount, type GmailContact, type GmailContactLabelStatus, type GoogleContactGroup, type MaxContact, type MemoryItem, type Message, type OnboardingStatus, type Profile, type ProfileDetail, type TelegramAuthStatus, type WorkingMemory, type WorkProfile } from "./api";

type Tab = "reply" | "soft" | "hard" | "strategy" | "memory" | "profile" | "work_profile" | "style";
type Source = Account | "gmail" | "max";
type RewriteMode = "my_style" | "formal" | "soft" | "hard" | "short" | "correct";
type RewriteEditorState = {
  source: string;
  start: number;
  end: number;
  mode: RewriteMode;
  result: string;
};
type ReplyTarget = { id: number; sender: string; text: string };

const tabs: Array<{ id: Tab; label: string }> = [
  { id: "reply", label: "Лучший" },
  { id: "soft", label: "Мягко" },
  { id: "hard", label: "Жёстко" },
  { id: "strategy", label: "Разбор" },
  { id: "memory", label: "Память" },
  { id: "profile", label: "Профиль" },
  { id: "work_profile", label: "Рабочий" },
  { id: "style", label: "Стиль" },
];

const rewriteModes: Array<{ id: RewriteMode; label: string }> = [
  { id: "my_style", label: "Мой стиль" },
  { id: "formal", label: "Деловой" },
  { id: "soft", label: "Мягче" },
  { id: "hard", label: "Жёстче" },
  { id: "short", label: "Короче" },
  { id: "correct", label: "Исправить" },
];
const isTauriApp = "__TAURI_INTERNALS__" in window;
const emptyWorkProfile: WorkProfile = {
  id: 1,
  about: "",
  skills: "",
  base_rate: 0,
  minimum_order: 0,
  pricing_rules: "",
  risk_rules: "",
  style: "",
  boundaries: "",
  updated_at: "",
};

export default function App() {
  const [bootstrap, setBootstrap] = useState<OnboardingStatus | null>(null);
  const [bootstrapError, setBootstrapError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const load = async () => {
      try {
        const status = await api.onboarding();
        if (!cancelled) {
          setBootstrap(status);
          setBootstrapError("");
        }
      } catch (error) {
        if (!cancelled) {
          setBootstrapError(messageFrom(error));
          timer = window.setTimeout(() => void load(), 1200);
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  if (!bootstrap) return <DesktopWindow><StartupScreen error={bootstrapError} /></DesktopWindow>;
  if (bootstrap.required && !bootstrap.complete) {
    return <DesktopWindow><Onboarding initial={bootstrap} onReady={setBootstrap} /></DesktopWindow>;
  }
  return <DesktopWindow><AdvisorApp /></DesktopWindow>;
}

function DesktopWindow({ children }: { children: React.ReactNode }) {
  if (!isTauriApp) return <>{children}</>;
  return <div className="desktop-window"><AppTitleBar /><div className="desktop-window-content">{children}</div></div>;
}

function AppTitleBar() {
  const windowRef = getCurrentWindow();
  const run = (action: () => Promise<void>) => void action().catch(() => undefined);
  return <header
    className="app-titlebar"
    data-tauri-drag-region
    onDoubleClick={(event) => {
      if (!(event.target instanceof Element && event.target.closest("button"))) run(() => windowRef.toggleMaximize());
    }}
  >
    <div className="app-titlebar-brand" data-tauri-drag-region><MessageSquareText size={15} /> Telegram Advisor</div>
    <div className="app-titlebar-actions">
      <button type="button" className="app-titlebar-button" title="Свернуть" aria-label="Свернуть" onClick={() => run(() => windowRef.minimize())}><Minus size={16} /></button>
      <button type="button" className="app-titlebar-button" title="Развернуть" aria-label="Развернуть" onClick={() => run(() => windowRef.toggleMaximize())}><Maximize2 size={14} /></button>
      <button type="button" className="app-titlebar-button close" title="Закрыть" aria-label="Закрыть" onClick={() => run(() => windowRef.close())}><X size={16} /></button>
    </div>
  </header>;
}

function AdvisorApp() {
  const [account, setAccount] = useState<Account>("work");
  const [source, setSource] = useState<Source>("work");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ProfileDetail | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [workingMemory, setWorkingMemory] = useState<WorkingMemory | null>(null);
  const [workProfile, setWorkProfile] = useState<WorkProfile>(emptyWorkProfile);
  const [tab, setTab] = useState<Tab>("reply");
  const [tone, setTone] = useState("мой стиль");
  const [analysisModel, setAnalysisModel] = useState<AnalysisModel["id"]>("gpt-5.6-luna");
  const [analysisModels, setAnalysisModels] = useState<AnalysisModel[]>([]);
  const [assistantRole, setAssistantRole] = useState<AssistantRole["id"]>("general");
  const [assistantRoles, setAssistantRoles] = useState<AssistantRole[]>([]);
  const [comment, setComment] = useState("");
  const [draft, setDraft] = useState("");
  const [replyTarget, setReplyTarget] = useState<ReplyTarget | null>(null);
  const [replyDraft, setReplyDraft] = useState("");
  const [autopilotDelayInput, setAutopilotDelayInput] = useState("45");
  const [userStyle, setUserStyle] = useState("");
  const [weekendPolicy, setWeekendPolicy] = useState("");
  const [afterHoursPolicy, setAfterHoursPolicy] = useState("");
  const [workdayStartHour, setWorkdayStartHour] = useState(9);
  const [workdayEndHour, setWorkdayEndHour] = useState(19);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const [notificationChat, setNotificationChat] = useState("");
  const [gmailConnected, setGmailConnected] = useState(false);
  const [gmailCanSend, setGmailCanSend] = useState(false);
  const [gmailClientId, setGmailClientId] = useState("");
  const [gmailClientSecret, setGmailClientSecret] = useState("");
  const [gmailLabel, setGmailLabel] = useState("Advisor");
  const [gmailEmail, setGmailEmail] = useState("");
  const [gmailAccounts, setGmailAccounts] = useState<GmailAccount[]>([]);
  const [gmailActiveAccountId, setGmailActiveAccountId] = useState("");
  const [gmailContacts, setGmailContacts] = useState<GmailContact[]>([]);
  const [gmailBrief, setGmailBrief] = useState("");
  const [gmailAnalysis, setGmailAnalysis] = useState<Analysis | null>(null);
  const [gmailReplyDraft, setGmailReplyDraft] = useState("");
  const [gmailDraft, setGmailDraft] = useState("");
  const [maxConnected, setMaxConnected] = useState(false);
  const [maxBotName, setMaxBotName] = useState("");
  const [maxToken, setMaxToken] = useState("");
  const [connectionsOpen, setConnectionsOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const [activeProjectCount, setActiveProjectCount] = useState(0);
  const [gmailAddOpen, setGmailAddOpen] = useState(false);
  const [gmailGroupsOpen, setGmailGroupsOpen] = useState(false);
  const [gmailGroups, setGmailGroups] = useState<GoogleContactGroup[]>([]);
  const [selectedGmailGroupIds, setSelectedGmailGroupIds] = useState<string[]>([]);
  const [gmailContactLabelStatus, setGmailContactLabelStatus] = useState<GmailContactLabelStatus>({ state: "idle", message: "", current: 0, total: 0 });
  const [selectedGmailId, setSelectedGmailId] = useState<string | null>(null);
  const [maxContacts, setMaxContacts] = useState<MaxContact[]>([]);
  const [selectedMaxId, setSelectedMaxId] = useState<string | null>(null);
  const [maxDraft, setMaxDraft] = useState("");
  const [maxBrief, setMaxBrief] = useState("");
  const [maxAnalysis, setMaxAnalysis] = useState<Analysis | null>(null);
  const [maxReplyDraft, setMaxReplyDraft] = useState("");
  const [search, setSearch] = useState("");
  const [mobileView, setMobileView] = useState<"list" | "chat" | "ai">("list");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [peer, setPeer] = useState("");
  const [contextMenu, setContextMenu] = useState<{ profile: Profile; x: number; y: number } | null>(null);
  const [profileTasks, setProfileTasks] = useState<{ profile: Profile; tasks: CrmTaskPreview[] } | null>(null);
  const [projectTasksByProfile, setProjectTasksByProfile] = useState<Record<number, CrmTaskPreview[]>>({});
  const [htmlImportProfileId, setHtmlImportProfileId] = useState<number | null>(null);
  const [draftMenu, setDraftMenu] = useState<{ x: number; y: number; start: number; end: number } | null>(null);
  const [rewriteEditor, setRewriteEditor] = useState<RewriteEditorState | null>(null);
  const [activeChatDate, setActiveChatDate] = useState<{ key: string; label: string } | null>(null);
  const chatScrollRef = useRef<HTMLDivElement>(null);
  const dayStartRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const profilesByAccountRef = useRef<Record<Account, Profile[]>>({ work: [], personal: [] });
  const activeAccountRef = useRef<Account>(account);
  const htmlImportRef = useRef<HTMLInputElement>(null);
  const taskbarOverlayRef = useRef<Uint8Array | null>(null);
  const syncInFlightRef = useRef(new Set<number>());
  const olderHistoryInFlightRef = useRef(new Set<number>());
  const viewedInFlightRef = useRef(new Set<number>());
  const autoScrollingRef = useRef(false);

  const selectedProfile = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? null,
    [profiles, selectedId],
  );
  const lastMessageId = detail?.messages.at(-1)?.telegram_message_id ?? null;

  function updateActiveChatDate() {
    const container = chatScrollRef.current;
    if (!container) return;
    const days = Object.entries(dayStartRefs.current)
      .filter((entry): entry is [string, HTMLDivElement] => Boolean(entry[1]))
      .map(([key, element]) => ({ key, label: element.dataset.dateLabel ?? "", top: element.offsetTop }))
      .sort((left, right) => left.top - right.top);
    const active = days.filter((day) => day.top <= container.scrollTop + 28).at(-1) ?? days[0];
    if (active) setActiveChatDate((current) => current?.key === active.key ? current : { key: active.key, label: active.label });
  }

  function scrollToActiveChatDate() {
    const target = activeChatDate ? dayStartRefs.current[activeChatDate.key] : null;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  const filteredProfiles = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return query
      ? profiles.filter((profile) => `${profile.chat_name} ${profile.preview}`.toLocaleLowerCase().includes(query))
      : profiles;
  }, [profiles, search]);

  const filteredGmailContacts = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    return query
      ? gmailContacts.filter((contact) => `${contact.name} ${contact.email} ${contact.preview}`.toLocaleLowerCase().includes(query))
      : gmailContacts;
  }, [gmailContacts, search]);

  const selectedGmailContact = useMemo(
    () => gmailContacts.find((contact) => contact.id === selectedGmailId) ?? null,
    [gmailContacts, selectedGmailId],
  );
  const selectedMaxContact = useMemo(() => maxContacts.find((contact) => contact.id === selectedMaxId) ?? null, [maxContacts, selectedMaxId]);

  function applyGmailSettings(payload: Pick<AppSettings, "gmail_connected" | "gmail_can_send" | "gmail_client_id" | "gmail_label" | "gmail_email" | "gmail_active_account_id" | "gmail_accounts" | "gmail_reply_brief">) {
    setGmailConnected(payload.gmail_connected);
    setGmailCanSend(payload.gmail_can_send);
    setGmailClientId(payload.gmail_client_id);
    setGmailLabel(payload.gmail_label);
    setGmailEmail(payload.gmail_email);
    setGmailActiveAccountId(payload.gmail_active_account_id);
    setGmailAccounts(payload.gmail_accounts);
    setGmailBrief(payload.gmail_reply_brief);
  }

  async function loadProfiles(targetAccount = account) {
    const payload = await api.profiles(targetAccount);
    profilesByAccountRef.current[targetAccount] = payload.profiles;
    if (targetAccount !== activeAccountRef.current) return payload;
    setProfiles(payload.profiles);
    setSelectedId((current) => {
      if (current && payload.profiles.some((profile) => profile.id === current)) return current;
      return payload.profiles[0]?.id ?? null;
    });
    return payload;
  }

  function switchTelegramAccount(next: Account) {
    const cachedProfiles = profilesByAccountRef.current[next];
    activeAccountRef.current = next;
    setAccount(next);
    setSource(next);
    setDetail(null);
    setAnalysis(null);
    setWorkingMemory(null);
    setProfiles(cachedProfiles);
    setSelectedId((current) => cachedProfiles.some((profile) => profile.id === current) ? current : cachedProfiles[0]?.id ?? null);
    setMobileView("list");
  }

  async function loadMaxMessages(refresh = false) {
    const payload = refresh ? await api.refreshMax() : await api.maxMessages();
    setMaxContacts(payload.contacts);
    setSelectedMaxId((current) => current && payload.contacts.some((contact) => contact.id === current) ? current : payload.contacts[0]?.id ?? null);
  }

  async function loadDetail(profileId: number, quiet = false) {
    try {
      const payload = await api.profile(profileId);
      setDetail((current) => quiet && current?.id === profileId ? {
        ...payload,
        messages: mergeMessageHistory(current.messages, payload.messages),
        history_has_more: current.history_has_more,
      } : payload);
      setAnalysis((current) => current ?? payload.last_analysis);
      if (!quiet) setComment(payload.conversation_goal);
    } catch (requestError) {
      if (!quiet) setError(messageFrom(requestError));
    }
  }

  async function loadWorkingMemory(profileId: number) {
    try {
      setWorkingMemory(await api.workingMemory(profileId));
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function syncWorkingMemory() {
    if (!selectedId) return;
    setPending("working-memory");
    setError("");
    try {
      setWorkingMemory(await api.updateWorkingMemory(selectedId));
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function updateMemoryItem(item: MemoryItem, changes: Partial<MemoryItem>) {
    try {
      await api.updateMemoryItem(item.id, changes);
      if (selectedId) await loadWorkingMemory(selectedId);
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function editMemoryItem(item: MemoryItem) {
    const title = window.prompt("Исправить формулировку", item.title)?.trim();
    if (!title || title === item.title) return;
    await updateMemoryItem(item, { title });
  }

  async function deleteMemoryItem(item: MemoryItem) {
    if (!window.confirm(`Удалить из рабочей памяти: «${item.title}»?`)) return;
    try {
      await api.deleteMemoryItem(item.id);
      if (selectedId) await loadWorkingMemory(selectedId);
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function openMemorySource(messageId: number | null | undefined) {
    if (!selectedId || !messageId) return;
    try {
      const payload = await api.messagesAround(selectedId, messageId);
      setDetail((current) => current ? { ...current, messages: mergeMessageHistory(current.messages, payload.messages) } : current);
      setMobileView("chat");
      window.setTimeout(() => document.getElementById(`message-${messageId}`)?.scrollIntoView({ behavior: "smooth", block: "center" }), 80);
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function saveWorkProfile() {
    setPending("work-profile");
    setError("");
    try {
      setWorkProfile(await api.saveWorkProfile(workProfile));
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function openCrm() {
    setAccountMenuOpen(false);
    if (!isTauriApp) {
      window.open("/projects", "_blank", "noopener");
      return;
    }
    try {
      await invoke("open_projects", { profileId: "" });
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function openProfileTasks(profile: Profile) {
    setContextMenu(null);
    setProfileTasks({ profile, tasks: projectTasksByProfile[profile.id] ?? [] });
    void api.profileProjects(profile.id)
      .then((payload) => {
        setProjectTasksByProfile((current) => ({ ...current, [profile.id]: payload.projects }));
        setProfileTasks((current) => current?.profile.id === profile.id ? { profile, tasks: payload.projects } : current);
      })
      .catch((requestError) => setError(messageFrom(requestError)));
  }

  async function markTaskCompletedByMe(projectId: number) {
    try {
      const updated = await api.markProjectCompletedByMe(projectId);
      setProfileTasks((current) => current ? { ...current, tasks: current.tasks.map((task) => task.id === projectId ? updated : task) } : null);
      if (updated.profile_id) setProjectTasksByProfile((current) => ({ ...current, [updated.profile_id as number]: (current[updated.profile_id as number] ?? []).map((task) => task.id === projectId ? updated : task) }));
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  useEffect(() => {
    if (!accountMenuOpen) return;
    void api.projects()
      .then((payload) => setActiveProjectCount(payload.projects.filter((project) => !["done", "completed_by_me"].includes(project.status)).length))
      .catch(() => setActiveProjectCount(0));
  }, [accountMenuOpen]);

  useEffect(() => {
    void api.projects().then((payload) => {
      const grouped: Record<number, CrmTaskPreview[]> = {};
      for (const task of payload.projects) {
        if (task.profile_id) (grouped[task.profile_id] ??= []).push(task);
      }
      setProjectTasksByProfile(Object.fromEntries(Object.entries(grouped).map(([profileId, tasks]) => [profileId, tasks.slice(0, 3)])));
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    activeAccountRef.current = account;
    setDetail(null);
    setAnalysis(null);
    const cachedProfiles = profilesByAccountRef.current[account];
    if (cachedProfiles.length) {
      setProfiles(cachedProfiles);
      setSelectedId((current) => cachedProfiles.some((profile) => profile.id === current) ? current : cachedProfiles[0]?.id ?? null);
    }
    void loadProfiles(account).catch((requestError) => setError(messageFrom(requestError)));
    const timer = window.setInterval(() => void loadProfiles(account).catch(() => undefined), 1000);
    return () => window.clearInterval(timer);
  }, [account]);

  useEffect(() => {
    void Promise.all([api.profiles("work"), api.profiles("personal")]).then(([work, personal]) => {
      profilesByAccountRef.current.work = work.profiles;
      profilesByAccountRef.current.personal = personal.profiles;
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setDetail(null);
    setAnalysis(null);
    setWorkingMemory(null);
    void loadDetail(selectedId);
    void loadWorkingMemory(selectedId);
    const focusTimer = window.setTimeout(() => composerRef.current?.focus(), 180);
    const timer = window.setInterval(() => void loadDetail(selectedId, true), 500);
    return () => {
      window.clearTimeout(focusTimer);
      window.clearInterval(timer);
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId) return;
    void autoRefreshProfile(selectedId);
    const timer = window.setInterval(() => void autoRefreshProfile(selectedId), 30_000);
    return () => window.clearInterval(timer);
  }, [selectedId]);

  useEffect(() => {
    void api.settings().then((payload) => {
      setUserStyle(payload.user_style);
      setAnalysisModel(payload.analysis_model);
      setAnalysisModels(payload.analysis_models);
      setAssistantRole(payload.assistant_role);
      setAssistantRoles(payload.assistant_roles);
      setWeekendPolicy(payload.weekend_policy);
      setAfterHoursPolicy(payload.after_hours_policy);
      setWorkdayStartHour(payload.workday_start_hour);
      setWorkdayEndHour(payload.workday_end_hour);
      setNotificationsEnabled(payload.notifications_enabled);
      setNotificationChat(payload.notification_chat);
      applyGmailSettings(payload);
      setMaxConnected(payload.max_connected);
      setMaxBotName(payload.max_bot_name);
    }).catch(() => undefined);
    void api.workProfile().then(setWorkProfile).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (source === "gmail" && gmailConnected && !gmailContacts.length) void loadGmailMessages();
  }, [source, gmailConnected]);

  useEffect(() => {
    if (!gmailAddOpen || pending !== "gmail-connect") return;
    const timer = window.setInterval(() => {
      void api.settings().then((payload) => {
        const isNewAccount = payload.gmail_accounts.length > gmailAccounts.length;
        if (isNewAccount) {
          applyGmailSettings(payload);
          setGmailAddOpen(false);
          setGmailClientSecret("");
          setSource("gmail");
          void loadGmailMessages(true);
        }
      }).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [gmailAddOpen, pending, gmailAccounts.length]);

  useEffect(() => {
    if (!gmailGroupsOpen || gmailContactLabelStatus.state !== "running") return;
    const timer = window.setInterval(() => void api.gmailContactLabelStatus().then(setGmailContactLabelStatus).catch((requestError) => setError(messageFrom(requestError))), 1200);
    return () => window.clearInterval(timer);
  }, [gmailGroupsOpen, gmailContactLabelStatus.state]);

  useEffect(() => {
    if (source !== "max" || !maxConnected) return;
    void loadMaxMessages(true).catch((requestError) => setError(messageFrom(requestError)));
    const timer = window.setInterval(() => void loadMaxMessages(true).catch(() => undefined), 5000);
    return () => window.clearInterval(timer);
  }, [source, maxConnected]);

  useEffect(() => {
    let cancelled = false;
    const connectRecentContacts = async () => {
      const results = await Promise.allSettled([
        api.importRecentContacts("work", 10),
        api.importRecentContacts("personal", 10),
      ]);
      const changed = results.some((result) => result.status === "fulfilled" && result.value.total > 0);
      if (changed && !cancelled) await loadProfiles().catch(() => undefined);
    };
    void connectRecentContacts();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!("__TAURI_INTERNALS__" in window)) return;
    let cancelled = false;
    const syncTaskbarIndicator = async () => {
      try {
        const [work, personal] = await Promise.all([api.profiles("work"), api.profiles("personal")]);
        const unread = [...work.profiles, ...personal.profiles].filter((profile) => profile.ai_unread).length;
        if (!taskbarOverlayRef.current) {
          const response = await fetch("/notification-overlay.png");
          taskbarOverlayRef.current = new Uint8Array(await response.arrayBuffer());
        }
        if (!cancelled) await getCurrentWindow().setOverlayIcon(unread ? taskbarOverlayRef.current : undefined);
      } catch {
        // Browser development mode and non-Windows platforms may not expose a taskbar overlay.
      }
    };
    void syncTaskbarIndicator();
    const timer = window.setInterval(() => void syncTaskbarIndicator(), 3500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      void getCurrentWindow().setOverlayIcon(undefined).catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    setReplyDraft(replyForTab(analysis, tab));
  }, [analysis, tab]);

  useEffect(() => {
    setGmailReplyDraft(replyForTab(gmailAnalysis, tab));
  }, [gmailAnalysis, tab]);

  useEffect(() => {
    setMaxReplyDraft(replyForTab(maxAnalysis, tab));
  }, [maxAnalysis, tab]);

  useEffect(() => {
    if (detail?.autopilot_delay_seconds) {
      setAutopilotDelayInput(String(detail.autopilot_delay_seconds));
    }
  }, [selectedId, detail?.autopilot_delay_seconds]);

  useLayoutEffect(() => {
    const composer = composerRef.current;
    if (composer) {
      composer.style.height = "auto";
      const maxHeight = 160;
      composer.style.height = `${Math.min(composer.scrollHeight, maxHeight)}px`;
      composer.style.overflowY = composer.scrollHeight > maxHeight ? "auto" : "hidden";
    }
  }, [draft]);

  useLayoutEffect(() => {
    const container = chatScrollRef.current;
    if (!container) return;
    autoScrollingRef.current = true;
    const scrollToBottom = () => {
      container.scrollTop = container.scrollHeight;
    };
    scrollToBottom();
    const frame = requestAnimationFrame(scrollToBottom);
    const timer = window.setTimeout(() => {
      scrollToBottom();
      autoScrollingRef.current = false;
      updateActiveChatDate();
    }, 120);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      autoScrollingRef.current = false;
    };
  }, [selectedId, lastMessageId]);

  async function markChatViewed() {
    const profileId = selectedId;
    const container = chatScrollRef.current;
    if (!profileId || !detail?.unread || !container || autoScrollingRef.current || viewedInFlightRef.current.has(profileId)) return;
    if (container.scrollHeight - container.scrollTop - container.clientHeight > 40) return;
    viewedInFlightRef.current.add(profileId);
    try {
      const viewed = await api.markViewed(profileId);
      setDetail((current) => current && current.id === profileId ? { ...current, unread: false, ui_last_seen_message_id: viewed.ui_last_seen_message_id } : current);
      setProfiles((current) => current.map((profile) => profile.id === profileId ? { ...profile, unread: false, ui_last_seen_message_id: viewed.ui_last_seen_message_id } : profile));
    } finally {
      viewedInFlightRef.current.delete(profileId);
    }
  }

  async function loadOlderHistory() {
    const profileId = selectedId;
    const container = chatScrollRef.current;
    const firstMessageId = detail?.messages[0]?.telegram_message_id;
    if (!profileId || !container || !detail?.history_has_more || !firstMessageId || container.scrollTop > 80 || olderHistoryInFlightRef.current.has(profileId)) return;
    olderHistoryInFlightRef.current.add(profileId);
    const previousHeight = container.scrollHeight;
    try {
      const page = await api.olderMessages(profileId, firstMessageId);
      setDetail((current) => current?.id === profileId ? {
        ...current,
        messages: mergeMessageHistory(page.messages, current.messages),
        history_has_more: page.history_has_more,
      } : current);
      requestAnimationFrame(() => { if (chatScrollRef.current) chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight - previousHeight; });
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      olderHistoryInFlightRef.current.delete(profileId);
    }
  }

  async function sendMessage(text: string) {
    if (!selectedId || !text.trim()) return;
    const profileId = selectedId;
    const cleanText = text.trim();
    const optimisticId = -Date.now();
    const optimisticMessage: Message = {
      telegram_message_id: optimisticId,
      message_date: new Date().toISOString(),
      sender: "Вы",
      out: true,
      delivery_status: "sent",
      text: cleanText,
    };
    syncInFlightRef.current.add(profileId);
    setDetail((current) => current ? { ...current, messages: [...current.messages, optimisticMessage] } : current);
    setDraft("");
    setPending("send");
    setError("");
    try {
      await api.send(profileId, cleanText, replyTarget?.id);
      setReplyTarget(null);
      await Promise.all([loadProfiles(), loadDetail(profileId)]);
    } catch (requestError) {
      setDetail((current) => current ? { ...current, messages: current.messages.filter((message) => message.telegram_message_id !== optimisticId) } : current);
      setError(messageFrom(requestError));
    } finally {
      syncInFlightRef.current.delete(profileId);
      setPending("");
      window.setTimeout(() => {
        if (selectedId === profileId) composerRef.current?.focus();
      }, 0);
    }
  }

  async function refreshProfile(profileId = selectedId) {
    if (!profileId) return;
    if (syncInFlightRef.current.has(profileId)) {
      setError("Обновление этого чата уже выполняется.");
      return;
    }
    syncInFlightRef.current.add(profileId);
    setPending("refresh");
    setError("");
    try {
      const result = await api.refresh(profileId);
      await Promise.all([loadProfiles(), loadDetail(profileId)]);
      setError(result.added ? `Добавлено новых сообщений: ${result.added}` : "Новых сообщений пока нет.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      syncInFlightRef.current.delete(profileId);
      setPending("");
    }
  }

  async function rebuildProfile(profileId: number) {
    if (syncInFlightRef.current.has(profileId)) return;
    syncInFlightRef.current.add(profileId);
    setPending("profile-refresh");
    setError("");
    try {
      await api.rebuildProfile(profileId);
      await Promise.all([loadProfiles(), loadDetail(profileId)]);
      setError("AI-профиль построен.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      syncInFlightRef.current.delete(profileId);
      setPending("");
    }
  }

  async function autoRefreshProfile(profileId: number) {
    if (syncInFlightRef.current.has(profileId)) return;
    syncInFlightRef.current.add(profileId);
    try {
      const result = await api.refresh(profileId);
      if (result.added) {
        await Promise.all([loadProfiles(), loadDetail(profileId, true)]);
      }
    } catch {
      // Live sync is best-effort; the visible button reports errors explicitly.
    } finally {
      syncInFlightRef.current.delete(profileId);
    }
  }

  async function syncHistory(profileId: number) {
    if (syncInFlightRef.current.has(profileId)) {
      setError("Обновление этого чата уже выполняется.");
      return;
    }
    syncInFlightRef.current.add(profileId);
    setPending("history");
    setError("");
    try {
      const result = await api.syncHistory(profileId);
      await Promise.all([loadProfiles(), loadDetail(profileId)]);
      setError(result.added ? `Добавлено новых сообщений: ${result.added}` : "История экспортирована, фотографии сохранены.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      syncInFlightRef.current.delete(profileId);
      setPending("");
    }
  }

  async function importHtmlHistory(files: File[]) {
    const profileId = htmlImportProfileId;
    const htmlFiles = files.filter((file) => /\.html?$/i.test(file.name));
    if (!profileId || !htmlFiles.length) return;
    setPending("html-history");
    setError("");
    try {
      const payload = await Promise.all(htmlFiles.map(async (file) => ({ name: file.name, content: await file.text() })));
      const result = await api.importHtmlHistory(profileId, payload);
      await Promise.all([loadProfiles(), loadDetail(profileId)]);
      setError(`Импортировано сообщений: ${result.messages} из ${result.files} HTML-файлов.`);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
      setHtmlImportProfileId(null);
    }
  }

  function chooseHtmlHistory(profileId: number) {
    setSelectedId(profileId);
    setHtmlImportProfileId(profileId);
    window.setTimeout(() => htmlImportRef.current?.click(), 0);
  }

  async function setAutopilot(enabled: boolean, delaySeconds = normalizedAutopilotDelay(autopilotDelayInput)) {
    if (!selectedId) return;
    if (enabled && !detail?.autopilot_enabled && !window.confirm(`Автопилот будет сам отправлять ответы этому контакту после ${delaySeconds} секунд тишины. Включить?`)) return;
    setPending("autopilot");
    setError("");
    try {
      await api.setAutopilot(selectedId, enabled, delaySeconds);
      await Promise.all([loadProfiles(), loadDetail(selectedId, true)]);
      setError(enabled ? "Автопилот включён. Он ответит только на новые входящие сообщения." : "Автопилот выключен.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function setContactKind(profile: Profile, kind: "business" | "personal") {
    setPending("contact-kind");
    setError("");
    try {
      await api.setContactKind(profile.id, kind);
      await loadProfiles();
      if (selectedId === profile.id) await loadDetail(profile.id, true);
      setError(kind === "business" ? "Контакт помечен как рабочий." : "Рабочая метка снята.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function setProfileAutopilot(profile: Profile, enabled: boolean) {
    const delay = selectedId === profile.id ? normalizedAutopilotDelay(autopilotDelayInput) : 45;
    if (enabled && !window.confirm(`Автопилот будет сам отвечать контакту «${profile.chat_name}» после ${delay} секунд тишины. Включить?`)) return;
    setPending("autopilot");
    setError("");
    try {
      await api.setAutopilot(profile.id, enabled, delay);
      await loadProfiles();
      if (selectedId === profile.id) await loadDetail(profile.id, true);
      setError(enabled ? "Автопилот включён для рабочего контакта." : "Автопилот выключен.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function analyze() {
    if (!selectedId) return;
    setPending("analyze");
    setError("");
    try {
      const result = await api.analyze(selectedId, tone, comment);
      setAnalysis(result);
      setTone(result.recommended_tone || tone);
      setTab("reply");
      await Promise.all([loadProfiles(), loadDetail(selectedId, true)]);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function saveConversationGoal() {
    if (!selectedId) return;
    try {
      await api.setConversationGoal(selectedId, comment);
    } catch (requestError) {
      setError(messageFrom(requestError));
    }
  }

  async function createProjectFromChat() {
    if (!selectedId) return;
    setPending("project");
    setError("");
    try {
      if (isTauriApp) await invoke("open_projects", { profileId: String(selectedId) });
      await saveConversationGoal();
      const result = await api.createProjectFromProfile(selectedId);
      setError(result.projects.length ? `В реестр добавлено задач: ${result.projects.length}.` : "В переписке не найдено подтверждённых рабочих задач.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  function openRewriteEditor(start?: number, end?: number) {
    const textarea = composerRef.current;
    const selectionStart = start ?? textarea?.selectionStart ?? 0;
    const selectionEnd = end ?? textarea?.selectionEnd ?? selectionStart;
    const hasSelection = selectionEnd > selectionStart;
    const sourceStart = hasSelection ? selectionStart : 0;
    const sourceEnd = hasSelection ? selectionEnd : draft.length;
    const source = draft.slice(sourceStart, sourceEnd);
    setDraftMenu(null);
    if (!source.trim()) {
      setError("Сначала напишите или выделите текст.");
      return;
    }
    setRewriteEditor({
      source,
      start: sourceStart,
      end: sourceEnd,
      mode: "my_style",
      result: "",
    });
  }

  async function runRewrite(mode: RewriteMode) {
    if (!selectedId || !rewriteEditor) return;
    setRewriteEditor({ ...rewriteEditor, mode, result: "" });
    setPending("rewrite");
    setError("");
    try {
      const response = await api.rewrite(selectedId, rewriteEditor.source, mode);
      setRewriteEditor((current) => current ? { ...current, mode, result: response.text } : null);
      await loadDetail(selectedId, true);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  function applyRewrite() {
    if (!rewriteEditor?.result) return;
    const caret = rewriteEditor.start + rewriteEditor.result.length;
    setDraft((current) =>
      current.slice(0, rewriteEditor.start) + rewriteEditor.result + current.slice(rewriteEditor.end)
    );
    setRewriteEditor(null);
    window.setTimeout(() => {
      composerRef.current?.focus();
      composerRef.current?.setSelectionRange(caret, caret);
    }, 0);
  }

  async function changeAnalysisModel(model: AnalysisModel["id"]) {
    const previous = analysisModel;
    setAnalysisModel(model);
    setPending("model");
    setError("");
    try {
      await api.setAnalysisModel(model);
      setError(`Модель переключена на ${analysisModels.find((item) => item.id === model)?.label ?? model}.`);
    } catch (requestError) {
      setAnalysisModel(previous);
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function changeAssistantRole(role: AssistantRole["id"]) {
    setAssistantRole(role);
    setPending("role");
    try {
      await api.setAssistantRole(role);
    } catch (requestError) {
      setError(messageFrom(requestError));
      const settings = await api.settings();
      setAssistantRole(settings.assistant_role);
    } finally {
      setPending("");
    }
  }

  async function changeTopic(topic: string, rule?: string) {
    if (!selectedId) return;
    setPending("topic");
    try {
      const payload = await api.setTopic(selectedId, topic, rule);
      setDetail((current) => current ? { ...current, ...payload } : current);
      setAnalysis(payload.last_analysis);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  function addTopic() {
    const topic = window.prompt("Название темы, например «Редизайн сайта»:")?.trim();
    if (!topic) return;
    const rule = window.prompt(
      "Что относится к этой теме? Укажите проекты, задачи, ключевые слова и что нужно исключить:",
      "",
    );
    if (rule !== null) void changeTopic(topic, rule);
  }

  function editTopicRule() {
    if (!detail) return;
    const topic = detail.active_topic;
    if (topic === "Авто") {
      setError("Для режима «Авто» используются описания остальных тем.");
      return;
    }
    const rule = window.prompt(
      `Что относится к теме «${topic}»?`,
      detail.topic_rules[topic] ?? "",
    );
    if (rule !== null) void changeTopic(topic, rule);
  }

  async function importChat() {
    if (!peer.trim()) return;
    setPending("import");
    setError("");
    try {
      const profile = await api.importTelegram(peer.trim(), account);
      setImportOpen(false);
      setPeer("");
      await loadProfiles();
      setSelectedId(profile.id);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function deleteProfile(profile: Profile) {
    setContextMenu(null);
    if (!window.confirm(`Удалить «${profile.chat_name}» и локальную историю?`)) return;
    setPending("delete");
    try {
      await api.deleteProfile(profile.id);
      await loadProfiles();
      if (selectedId === profile.id) setDetail(null);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function saveStyle() {
    setPending("style");
    try {
      await api.saveSettings(userStyle, weekendPolicy, afterHoursPolicy, workdayStartHour, workdayEndHour, notificationsEnabled, notificationChat);
      setError("Стиль сохранен.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function testNotifications() {
    setPending("notification-test");
    setError("");
    try {
      await api.saveSettings(userStyle, weekendPolicy, afterHoursPolicy, workdayStartHour, workdayEndHour, notificationsEnabled, notificationChat);
      const result = await api.testNotifications();
      const sent = Object.entries(result.results).filter(([, status]) => status === "sent").map(([account]) => account === "work" ? "рабочий" : "личный");
      setError(`Тест отправлен: ${sent.join(", ") || "нет доступных аккаунтов"}.`);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function loadGmailMessages(forceRefresh = false) {
    setPending("gmail-refresh");
    try {
      const payload = forceRefresh ? await api.refreshGmail() : await api.gmailMessages();
      setGmailContacts(payload.contacts);
      setSelectedGmailId((current) => current && payload.contacts.some((contact) => contact.id === current) ? current : payload.contacts[0]?.id ?? null);
      applyGmailSettings(payload);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function connectGmail() {
    setPending("gmail-connect");
    try {
      const payload = await api.connectGmail(gmailClientId, gmailClientSecret, gmailLabel);
      applyGmailSettings(payload);
      setGmailClientSecret("");
      setGmailAddOpen(false);
      await loadGmailMessages(true);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function reconnectGmail() {
    setPending("gmail-connect");
    setError("");
    try {
      const payload = await api.reconnectGmail();
      applyGmailSettings(payload);
      await loadGmailMessages(true);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function analyzeGmail() {
    if (!selectedGmailContact) return;
    setPending("gmail-analyze");
    setError("");
    try {
      const result = await api.analyzeGmail(selectedGmailContact.id, tone, gmailBrief);
      setGmailAnalysis(result);
      setTab("reply");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function sendGmail(text: string, clearManualDraft = false) {
    if (!selectedGmailContact || !text.trim()) return;
    setPending("gmail-send");
    setError("");
    try {
      await api.sendGmail(selectedGmailContact.id, text);
      if (clearManualDraft) setGmailDraft("");
      setError("Письмо отправлено через Gmail.");
      await loadGmailMessages(true);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function saveGmailLead(lead: Pick<CrmLead, "status" | "category" | "summary" | "next_action">) {
    if (!selectedGmailContact) return;
    setPending("gmail-lead");
    setError("");
    try {
      const saved = await api.saveGmailLead(selectedGmailContact.id, lead);
      setGmailContacts((contacts) => contacts.map((contact) => contact.id === selectedGmailContact.id ? { ...contact, crm_lead: saved } : contact));
      setError("Контакт добавлен в CRM.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function sendMax(text = maxDraft) {
    if (!selectedMaxContact || !text.trim()) return;
    setPending("max-send");
    try {
      await api.sendMax(selectedMaxContact.id, text);
      setMaxDraft("");
      setError("Сообщение отправлено через MAX.");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function analyzeMax() {
    if (!selectedMaxContact) return;
    setPending("max-analyze");
    setError("");
    try {
      const result = await api.analyzeMax(selectedMaxContact.id, tone, maxBrief);
      setMaxAnalysis(result);
      setTab("reply");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function saveGmailLabel() {
    setPending("gmail-label");
    try {
      applyGmailSettings(await api.setGmailLabel(gmailLabel));
      await loadGmailMessages();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function openGmailContactGroups() {
    setPending("gmail-groups");
    setError("");
    try {
      const payload = await api.gmailContactGroups();
      setGmailGroups(payload.groups);
      setSelectedGmailGroupIds(payload.selected_ids);
      setGmailContactLabelStatus(await api.gmailContactLabelStatus());
      setGmailGroupsOpen(true);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function labelGoogleContacts() {
    setPending("gmail-label-contacts");
    setError("");
    try {
      setGmailContactLabelStatus(await api.labelGmailContactGroups(selectedGmailGroupIds));
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function selectGmailAccount(accountId: string) {
    setPending("gmail-switch");
    setError("");
    try {
      applyGmailSettings(await api.setActiveGmail(accountId));
      setGmailContacts([]);
      setSelectedGmailId(null);
      setSource("gmail");
      setMobileView("list");
      setTab("reply");
      setAccountMenuOpen(false);
      await loadGmailMessages();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function copyReply() {
    if (!replyDraft.trim()) return;
    try {
      await navigator.clipboard.writeText(replyDraft);
      setError("Ответ скопирован.");
    } catch {
      setError("Не удалось скопировать текст.");
    }
  }

  async function connectMax() {
    setPending("max-connect");
    setError("");
    try {
      const result = await api.connectMax(maxToken);
      setMaxConnected(result.max_connected);
      setMaxBotName(result.max_bot_name);
      setMaxToken("");
      setConnectionsOpen(false);
      setError(`MAX подключён: ${result.max_bot_name}.`);
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage(draft);
    }
  }

  return (
    <main
      className={`app-shell mobile-view-${mobileView} h-screen min-h-[640px] overflow-hidden bg-ink text-slate-100`}
      onPointerDownCapture={(event) => {
        if (event.button !== 0 || (event.target instanceof Element && event.target.closest(".context-menu"))) return;
        setContextMenu(null);
        setDraftMenu(null);
      }}
      onClick={() => { setContextMenu(null); setDraftMenu(null); }}
    >
      <Group
        orientation="horizontal"
        className="app-group h-full"
        defaultLayout={savedLayout()}
        onLayoutChanged={(layout) => localStorage.setItem("telegram-advisor-layout", JSON.stringify(layout))}
      >
        <Panel id="contacts" className="mobile-panel mobile-contacts" defaultSize="24%" minSize="18%" maxSize="36%">
          <aside className="flex h-full min-w-0 flex-col bg-panel">
            <div className="border-b border-line px-4 pb-3 pt-4">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
                  <button className="icon-button -ml-2" title="Аккаунты и подключения" onClick={(event) => { event.stopPropagation(); setAccountMenuOpen(true); }}><Menu size={20} /></button>
                  <MessageSquareText size={18} className="text-accent" />
                  Telegram Advisor
                </div>
                <div className="flex gap-1"><button className="icon-button" title="Импортировать Telegram-чат" disabled={source !== "work" && source !== "personal"} onClick={() => setImportOpen(true)}><Plus size={18} /></button></div>
              </div>
              <div className="segmented" role="group" aria-label="Telegram-аккаунт">
                <button className={source === "work" ? "active" : ""} onClick={() => switchTelegramAccount("work")}>Рабочий</button>
                <button className={source === "personal" ? "active personal" : ""} onClick={() => switchTelegramAccount("personal")}>Личный</button>
              </div>
              <label className="search-field mt-3">
                <Search size={16} />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Поиск" />
              </label>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
              {source === "gmail" ? <GmailSourceList
                connected={gmailConnected}
                clientId={gmailClientId}
                clientSecret={gmailClientSecret}
                label={gmailLabel}
                contacts={filteredGmailContacts}
                selectedId={selectedGmailId}
                pending={pending}
                setClientId={setGmailClientId}
                setClientSecret={setGmailClientSecret}
                setLabel={setGmailLabel}
                setSelectedId={(contactId) => {
                  setSelectedGmailId(contactId);
                  setGmailAnalysis(null);
                  setGmailReplyDraft("");
                  setTab("reply");
                  setMobileView("chat");
                }}
                connect={connectGmail}
                refresh={() => void loadGmailMessages(true)}
                saveLabel={saveGmailLabel}
                openGroups={() => void openGmailContactGroups()}
                reconnect={() => void reconnectGmail()}
              /> : source === "max" ? <MaxSourceList connected={maxConnected} botName={maxBotName} contacts={maxContacts} selectedId={selectedMaxId} pending={pending} onSelect={(contactId) => { setSelectedMaxId(contactId); setMobileView("chat"); }} onRefresh={() => void loadMaxMessages(true)} onOpenSettings={() => setConnectionsOpen(true)} /> : <>{filteredProfiles.map((profile) => (
                <button
                  key={profile.id}
                  className={`contact-row ${profile.unread ? "unread" : ""} ${profile.id === selectedId ? "selected" : ""}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelectedId(profile.id);
                    setMobileView("chat");
                    void autoRefreshProfile(profile.id);
                  }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setContextMenu({
                      profile,
                      x: Math.max(8, Math.min(event.clientX, window.innerWidth - 220)),
                    y: Math.max(8, Math.min(event.clientY, window.innerHeight - 316)),
                    });
                  }}
                >
                  <Avatar profile={profile} />
                  <span className="min-w-0 flex-1 text-left">
                    <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-100">
                      <span className="truncate">{profile.chat_name}</span>
                      {profile.contact_kind === "business" && <Briefcase size={12} className="shrink-0 text-amber-300" aria-label="Рабочий контакт" />}
                      {profile.autopilot_enabled && <RadioTower size={13} className="shrink-0 text-emerald-300" aria-label="Отслеживается автопилотом" />}
                      {profile.unread && <span className="h-2 w-2 shrink-0 rounded-full bg-sky-400" aria-label="Новые сообщения" />}
                      {profile.ai_unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-red-400" />}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-muted">{profile.preview}</span>
                  </span>
                  <span className="self-start text-[11px] text-muted">{formatListTimestamp(profile.last_message_date)}</span>
                </button>
              ))}
              {!filteredProfiles.length && <EmptyContacts onImport={() => setImportOpen(true)} />}</>}
            </div>
          </aside>
        </Panel>

        <ResizeHandle />

        <Panel id="chat" className="mobile-panel mobile-chat" defaultSize="46%" minSize="32%">
          {source === "gmail" ? <GmailWorkspace contact={selectedGmailContact} canSend={gmailCanSend} draft={gmailDraft} pending={pending} setDraft={setGmailDraft} onSend={() => void sendGmail(gmailDraft, true)} onAuthorize={() => void reconnectGmail()} onSaveLead={(lead) => void saveGmailLead(lead)} onMobileBack={() => setMobileView("list")} onMobileAi={() => setMobileView("ai")} /> : source === "max" ? <MaxWorkspace contact={selectedMaxContact} draft={maxDraft} pending={pending} setDraft={setMaxDraft} onSend={() => void sendMax()} onMobileBack={() => setMobileView("list")} onMobileAi={() => setMobileView("ai")} /> : <section className="relative flex h-full min-w-0 flex-col bg-[#0b1621]">
            <header className="chat-header flex h-[70px] shrink-0 items-center justify-between border-b border-line px-5">
              <button className="mobile-nav-button" title="К списку чатов" onClick={() => setMobileView("list")}><ArrowLeft size={20} /></button>
              <div className="flex min-w-0 items-center gap-3">
                {selectedProfile ? <Avatar profile={selectedProfile} compact /> : <div className="avatar-placeholder"><UserRound size={18} /></div>}
                <div className="min-w-0">
                  <h1 className="truncate text-[15px] font-semibold">{selectedProfile?.chat_name ?? "Выберите контакт"}</h1>
                  <p className="mt-0.5 truncate text-xs text-muted">{selectedProfile ? autopilotStatusText(detail) : "Импортируйте Telegram-чат слева"}</p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {selectedId && <label className="autopilot-delay" title="Задержка ответа автопилота от 1 до 1800 секунд">
                  <input type="number" min="1" max="1800" step="1" value={autopilotDelayInput} disabled={Boolean(pending)} onChange={(event) => setAutopilotDelayInput(event.target.value)} onBlur={() => { const delay = normalizedAutopilotDelay(autopilotDelayInput); setAutopilotDelayInput(String(delay)); if (detail?.autopilot_enabled && delay !== detail.autopilot_delay_seconds) void setAutopilot(true, delay); }} onKeyDown={(event) => { if (event.key === "Enter") event.currentTarget.blur(); }} />
                  <span>сек</span>
                </label>}
                <button className={`autopilot-button ${detail?.autopilot_enabled ? "active" : ""}`} disabled={!selectedId || Boolean(pending)} onClick={() => void setAutopilot(!detail?.autopilot_enabled)}>
                  <Bot size={15} className={pending === "autopilot" ? "animate-pulse" : ""} />
                  {detail?.autopilot_enabled ? "Бот включён" : "Включить бота"}
                </button>
                <button className="icon-button shrink-0 border border-line" title="Найти подтверждённые задачи в этом чате и добавить в реестр" disabled={!selectedId || Boolean(pending)} onClick={() => void createProjectFromChat()}>
                  <Briefcase size={15} className={pending === "project" ? "animate-pulse" : ""} />
                </button>
                <button className="secondary-button" title="Получить новые сообщения сейчас" disabled={!selectedId || Boolean(pending)} onClick={() => void refreshProfile()}>
                  <RefreshCw size={15} className={pending === "refresh" ? "animate-spin" : ""} />
                  Обновить
                </button>
                <button className="mobile-nav-button" title="AI-ответ" onClick={() => setMobileView("ai")}><PanelRight size={19} /></button>
              </div>
            </header>

            <div ref={chatScrollRef} className="chat-scroll min-h-0 flex-1 overflow-y-auto px-5 py-5" onScroll={() => { updateActiveChatDate(); void markChatViewed(); void loadOlderHistory(); }}>
              {detail?.history_has_more && <div className="pb-3 text-center text-xs text-muted">Прокрутите выше, чтобы загрузить более ранние сообщения</div>}
              {detail?.messages.map((message, index, messages) => (
                <Fragment key={`${message.telegram_message_id ?? "local"}-${index}`}>
                  {isFirstMessageOfDay(message, messages[index - 1]) && <div ref={(element) => { dayStartRefs.current[dayKey(new Date(message.message_date))] = element; }} className="date-divider" data-date-label={formatDateLabel(message.message_date)}><span>{formatDateLabel(message.message_date)}</span></div>}
                  <MessageBubble message={message} isNew={!message.out && Number(message.telegram_message_id) > (detail?.ui_last_seen_message_id ?? 0)} outsideHoursLabel={outsideHoursLabel(message.message_date, workdayStartHour, workdayEndHour)} onReply={(target) => setReplyTarget(target)} />
                </Fragment>
              ))}
              {!detail?.messages.length && <div className="pt-24 text-center text-sm text-muted">В этом чате пока нет сообщений.</div>}
            </div>
            {activeChatDate && <div className="chat-date-hover-zone"><button className="chat-active-date" title="Перейти к началу этого дня" onClick={scrollToActiveChatDate}>{activeChatDate.label}</button></div>}

            <div className="shrink-0 border-t border-line bg-[#0e1925] px-4 py-3">
              {replyTarget && <div className="reply-preview"><div><span>Ответ на {replyTarget.sender}</span><p>{replyTarget.text}</p></div><button className="icon-button" title="Отменить ответ" onClick={() => setReplyTarget(null)}><X size={16} /></button></div>}
              <div className="flex items-end gap-3">
                <button
                  className="rewrite-trigger"
                  disabled={!selectedId || !draft.trim() || Boolean(pending)}
                  title="Переработать текст с ИИ"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => openRewriteEditor()}
                >
                  <Sparkles size={19} />
                </button>
                <textarea
                  ref={composerRef}
                  className="composer"
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={handleComposerKeyDown}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setDraftMenu({
                      x: event.clientX,
                      y: event.clientY,
                      start: event.currentTarget.selectionStart,
                      end: event.currentTarget.selectionEnd,
                    });
                  }}
                  placeholder="Сообщение"
                  rows={1}
                  disabled={!selectedId || Boolean(pending)}
                />
                <button className="send-button" disabled={!selectedId || !draft.trim() || Boolean(pending)} onClick={() => void sendMessage(draft)} title="Отправить">
                  <SendHorizontal size={18} />
                </button>
              </div>
              <p className="mt-2 text-[11px] text-muted">Enter отправляет, Shift+Enter добавляет строку</p>
            </div>
          </section>}
        </Panel>

        <ResizeHandle />

        <Panel id="assistant" className="mobile-panel mobile-ai" defaultSize="30%" minSize="24%" maxSize="44%">
          <aside className="flex h-full min-w-0 flex-col bg-panel">
            <header className="border-b border-line px-4 py-4">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 text-sm font-semibold"><button className="mobile-nav-button" title="К чату" onClick={() => setMobileView("chat")}><ArrowLeft size={20} /></button><Bot size={18} className="text-accent" /> AI-ответ</div>
                <button className="primary-button" disabled={source === "max" ? !selectedMaxContact || Boolean(pending) : source === "gmail" ? !selectedGmailContact || Boolean(pending) : !selectedId || Boolean(pending)} onClick={() => void (source === "max" ? analyzeMax() : source === "gmail" ? analyzeGmail() : analyze())}>
                  {["analyze", "gmail-analyze", "max-analyze"].includes(pending) ? <LoaderCircle size={15} className="animate-spin" /> : <Sparkles size={15} />}
                  Сгенерировать
                </button>
              </div>
              {(source === "work" || source === "personal") && <div className="mt-3 grid grid-cols-[52px_minmax(0,1fr)] items-center gap-x-2 gap-y-2">
                <span className="text-xs text-muted">Тема</span>
                <div className="flex min-w-0 gap-2">
                  <select className="tone-select" value={detail?.active_topic ?? "Авто"} disabled={!selectedId || Boolean(pending)} onChange={(event) => void changeTopic(event.target.value)}>
                    {(detail?.topics ?? ["Авто", "Работа", "Флуд"]).map((topic) => <option key={topic} value={topic}>{topic}</option>)}
                  </select>
                  <button className="icon-button shrink-0 border border-line" title="Описать выбранную тему" disabled={!selectedId || Boolean(pending)} onClick={editTopicRule}><Settings2 size={14} /></button>
                  <button className="icon-button shrink-0 border border-line" title="Добавить тему" disabled={!selectedId || Boolean(pending)} onClick={addTopic}><Plus size={14} /></button>
                </div>
              </div>}
              {source === "gmail" && selectedGmailContact && <p className="mt-3 truncate text-xs text-pink-300" title={selectedGmailContact.email}>{selectedGmailContact.name} · {selectedGmailContact.messages.at(-1)?.subject ?? "Письмо"}</p>}
              {source === "max" && <p className="mt-3 text-xs text-emerald-300">{selectedMaxContact ? `${selectedMaxContact.name} · MAX` : "MAX: выберите контакт"}</p>}
              {(source === "work" || source === "personal") && detail?.active_topic !== "Авто" && <p className="mt-2 truncate text-[11px] text-muted" title={detail?.topic_rules[detail.active_topic] || "Описание темы не задано"}>
                {detail?.topic_rules[detail.active_topic] || "Описание темы не задано — нажмите на шестерёнку"}
              </p>}
              <details className="ai-settings mt-3">
                <summary>Настройки <span>{assistantRoles.find((item) => item.id === assistantRole)?.label ?? assistantRole} · {analysisModels.find((item) => item.id === analysisModel)?.label ?? analysisModel} · {tone}</span></summary>
                <div className="mt-3 grid grid-cols-[52px_minmax(0,1fr)] items-center gap-x-2 gap-y-2">
                  <span className="text-xs text-muted">Роль</span>
                  <select className="tone-select" value={assistantRole} disabled={Boolean(pending)} onChange={(event) => void changeAssistantRole(event.target.value as AssistantRole["id"])}>
                    {assistantRoles.map((role) => <option key={role.id} value={role.id}>{role.label}</option>)}
                  </select>
                  <span className="text-xs text-muted">Модель</span>
                  <select className="tone-select" value={analysisModel} disabled={Boolean(pending)} onChange={(event) => void changeAnalysisModel(event.target.value as AnalysisModel["id"])}>
                    {analysisModels.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
                  </select>
                  <span className="text-xs text-muted">Тон</span>
                  <select className="tone-select" value={tone} onChange={(event) => setTone(event.target.value)}>
                    {['мой стиль', 'стратегичный', 'спокойный', 'деловой', 'жёсткий', 'короткий'].map((option) => <option key={option}>{option}</option>)}
                  </select>
                </div>
              </details>
              {(source === "work" || source === "personal") && detail?.ai_unread && <div className="mt-3 border-l-2 border-red-400 bg-red-400/10 px-2 py-1 text-xs text-red-300">ИИ не прочел диалог после нового сообщения</div>}
            </header>

            <div className="tab-strip shrink-0 overflow-x-auto border-b border-line px-3">
              {tabs.filter((item) => (source === "gmail" || source === "max") ? !["memory", "profile", "work_profile", "style"].includes(item.id) : true).map((item) => <button key={item.id} data-tab={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}</button>)}
              <button title="Открыть CRM: проекты и оплата" onClick={() => void openCrm()}><Briefcase size={13} className="mr-1 inline" />CRM</button>
            </div>

            <div className="ai-tab-panel min-h-0 flex-1 overflow-y-auto p-4" data-tab={tab}>
              {source === "max" ? <><ContextSummary analysis={maxAnalysis} detail={null} fallbackTone={tone} fallbackGoal={maxBrief} fallbackRole={assistantRoles.find((item) => item.id === assistantRole)?.label ?? assistantRole} contactName={selectedMaxContact?.name} messageCount={selectedMaxContact?.messages.length ?? 0} relationship="MAX" topic="MAX" /><AiTab tab={tab} analysis={maxAnalysis} detail={null} replyDraft={maxReplyDraft} setReplyDraft={setMaxReplyDraft} userStyle={userStyle} setUserStyle={setUserStyle} weekendPolicy={weekendPolicy} setWeekendPolicy={setWeekendPolicy} afterHoursPolicy={afterHoursPolicy} setAfterHoursPolicy={setAfterHoursPolicy} workdayStartHour={workdayStartHour} setWorkdayStartHour={setWorkdayStartHour} workdayEndHour={workdayEndHour} setWorkdayEndHour={setWorkdayEndHour} notificationsEnabled={notificationsEnabled} setNotificationsEnabled={setNotificationsEnabled} notificationChat={notificationChat} setNotificationChat={setNotificationChat} testNotifications={() => void testNotifications()} saveStyle={() => void saveStyle()} /></> : source === "gmail" ? <>
              <ContextSummary analysis={gmailAnalysis} detail={null} fallbackTone={tone} fallbackGoal={gmailBrief} fallbackRole={assistantRoles.find((item) => item.id === assistantRole)?.label ?? assistantRole} contactName={selectedGmailContact ? `${selectedGmailContact.name} <${selectedGmailContact.email}>` : "не выбран"} messageCount={selectedGmailContact?.messages.length ?? 0} relationship="деловая почта" topic={selectedGmailContact?.messages.at(-1)?.subject ?? "Письмо"} />
              <AiTab
                tab={tab}
                analysis={gmailAnalysis}
                detail={null}
                replyDraft={gmailReplyDraft}
                setReplyDraft={setGmailReplyDraft}
                userStyle={userStyle}
                setUserStyle={setUserStyle}
                weekendPolicy={weekendPolicy}
                setWeekendPolicy={setWeekendPolicy}
                afterHoursPolicy={afterHoursPolicy}
                setAfterHoursPolicy={setAfterHoursPolicy}
                workdayStartHour={workdayStartHour}
                setWorkdayStartHour={setWorkdayStartHour}
                workdayEndHour={workdayEndHour}
                setWorkdayEndHour={setWorkdayEndHour}
                notificationsEnabled={notificationsEnabled}
                setNotificationsEnabled={setNotificationsEnabled}
                notificationChat={notificationChat}
                setNotificationChat={setNotificationChat}
                testNotifications={() => void testNotifications()}
                saveStyle={() => void saveStyle()}
              /></> : tab === "memory" ? <WorkingMemoryPanel
                memory={workingMemory}
                pending={pending === "working-memory"}
                onSync={() => void syncWorkingMemory()}
                onEdit={(item) => void editMemoryItem(item)}
                onUpdate={(item, changes) => void updateMemoryItem(item, changes)}
                onDelete={(item) => void deleteMemoryItem(item)}
                onOpenSource={(messageId) => void openMemorySource(messageId)}
              /> : tab === "work_profile" ? <WorkProfilePanel
                profile={workProfile}
                pending={pending === "work-profile"}
                onChange={setWorkProfile}
                onSave={() => void saveWorkProfile()}
              /> : <>
              {!['profile', 'style'].includes(tab) && <ContextSummary analysis={analysis} detail={detail} fallbackTone={tone} fallbackGoal={comment} fallbackRole={assistantRoles.find((item) => item.id === assistantRole)?.label ?? assistantRole} />}
              <AiTab
                tab={tab}
                analysis={analysis}
                detail={detail}
                replyDraft={replyDraft}
                setReplyDraft={setReplyDraft}
                userStyle={userStyle}
                setUserStyle={setUserStyle}
                weekendPolicy={weekendPolicy}
                setWeekendPolicy={setWeekendPolicy}
                afterHoursPolicy={afterHoursPolicy}
                setAfterHoursPolicy={setAfterHoursPolicy}
                workdayStartHour={workdayStartHour}
                setWorkdayStartHour={setWorkdayStartHour}
                workdayEndHour={workdayEndHour}
                setWorkdayEndHour={setWorkdayEndHour}
                notificationsEnabled={notificationsEnabled}
                setNotificationsEnabled={setNotificationsEnabled}
                notificationChat={notificationChat}
                setNotificationChat={setNotificationChat}
                testNotifications={() => void testNotifications()}
                saveStyle={() => void saveStyle()}
              /></>}
            </div>

            {source === "gmail" ? <div className="shrink-0 border-t border-line p-4">
              <label className="mb-2 block text-xs font-medium text-slate-300">ТЗ для ответа</label>
              <textarea className="comment-input" rows={3} value={gmailBrief} onChange={(event) => setGmailBrief(event.target.value)} placeholder="Необязательно. Например: уточнить CMS и объём страниц, предложить созвон" />
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                {!gmailCanSend && <button className="secondary-button" disabled={Boolean(pending)} onClick={() => void reconnectGmail()}><KeyRound size={15} /> Разрешить отправку</button>}
                <button className="secondary-button" onClick={() => void navigator.clipboard.writeText(gmailReplyDraft)} disabled={!gmailReplyDraft.trim()}><Clipboard size={15} /> Copy</button>
                <button className="primary-button" onClick={() => void sendGmail(gmailReplyDraft)} disabled={!gmailCanSend || !selectedGmailContact || !gmailReplyDraft.trim() || Boolean(pending)}>{pending === "gmail-send" ? <LoaderCircle size={15} className="animate-spin" /> : <SendHorizontal size={15} />} Отправить</button>
              </div>
            </div> : source === "max" ? <div className="shrink-0 border-t border-line p-4"><label className="mb-2 block text-xs font-medium text-slate-300">Цель / комментарий</label><textarea className="comment-input" rows={2} value={maxBrief} onChange={(event) => setMaxBrief(event.target.value)} placeholder="Необязательно. Например: уточнить задачу и предложить созвон" /><div className="mt-3 flex justify-end gap-2"><button className="secondary-button" onClick={() => void navigator.clipboard.writeText(maxReplyDraft)} disabled={!maxReplyDraft.trim()}><Clipboard size={15} /> Copy</button><button className="primary-button" onClick={() => void sendMax(maxReplyDraft)} disabled={!selectedMaxContact || !maxReplyDraft.trim() || Boolean(pending)}>{pending === "max-send" ? <LoaderCircle size={15} className="animate-spin" /> : <SendHorizontal size={15} />} Отправить</button></div></div> : !["memory", "work_profile"].includes(tab) ? <div className="shrink-0 border-t border-line p-4">
              <label className="mb-2 block text-xs font-medium text-slate-300">Цель / комментарий</label>
              <textarea className="comment-input" rows={2} value={comment} onChange={(event) => setComment(event.target.value)} onBlur={() => void saveConversationGoal()} placeholder="Например: зафиксировать срок и оплату" />
              <div className="mt-3 flex justify-end gap-2">
                <button className="secondary-button" onClick={() => void copyReply()} disabled={!replyDraft.trim()}><Clipboard size={15} /> Copy</button>
                <button className="secondary-button" onClick={() => setDraft(replyDraft)} disabled={!replyDraft.trim()}><MessageSquareText size={15} /> В чат</button>
                <button className="primary-button" onClick={() => void sendMessage(replyDraft)} disabled={!selectedId || !replyDraft.trim() || Boolean(pending)}><SendHorizontal size={15} /> Отправить</button>
              </div>
            </div> : null}
          </aside>
        </Panel>
      </Group>

      {error && <div className="toast" role="status"><span>{error}</span><button title="Закрыть" onClick={() => setError("")}><X size={15} /></button></div>}
      <AccountDrawer
        open={accountMenuOpen}
        source={source}
        gmailAccounts={gmailAccounts}
        gmailActiveAccountId={gmailActiveAccountId}
        gmailEmail={gmailEmail}
        maxConnected={maxConnected}
        maxBotName={maxBotName}
        activeProjectCount={activeProjectCount}
        onClose={() => setAccountMenuOpen(false)}
        onSelectTelegram={(next) => { switchTelegramAccount(next); setAccountMenuOpen(false); }}
        onSelectGmail={(accountId) => void selectGmailAccount(accountId)}
        onAddGmail={() => { setAccountMenuOpen(false); setGmailAddOpen(true); }}
        onSelectMax={() => { setSource("max"); setTab("reply"); setMobileView("list"); setAccountMenuOpen(false); }}
        onOpenCrm={() => void openCrm()}
      />
      {importOpen && <ImportDialog account={account} peer={peer} pending={pending === "import"} setPeer={setPeer} onClose={() => setImportOpen(false)} onSubmit={() => void importChat()} />}
      {gmailAddOpen && <GmailAccountDialog clientId={gmailClientId} clientSecret={gmailClientSecret} label={gmailLabel} pending={pending === "gmail-connect"} setClientId={setGmailClientId} setClientSecret={setGmailClientSecret} setLabel={setGmailLabel} onClose={() => setGmailAddOpen(false)} onConnect={() => void connectGmail()} />}
      {gmailGroupsOpen && <GmailContactGroupsDialog groups={gmailGroups} selectedIds={selectedGmailGroupIds} status={gmailContactLabelStatus} pending={pending === "gmail-label-contacts"} onToggle={(groupId) => setSelectedGmailGroupIds((current) => current.includes(groupId) ? current.filter((item) => item !== groupId) : [...current, groupId])} onClose={() => setGmailGroupsOpen(false)} onStart={() => void labelGoogleContacts()} />}
      {connectionsOpen && <ConnectionsDialog maxToken={maxToken} setMaxToken={setMaxToken} maxConnected={maxConnected} maxBotName={maxBotName} pending={pending === "max-connect"} onClose={() => setConnectionsOpen(false)} onConnect={() => void connectMax()} />}
      <input ref={htmlImportRef} className="hidden" type="file" accept=".html,.htm,text/html" multiple onChange={(event) => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ""; void importHtmlHistory(files); }} />
      {contextMenu && <ContextMenu menu={contextMenu} onOpenTasks={() => void openProfileTasks(contextMenu.profile)} onToggleKind={() => { void setContactKind(contextMenu.profile, contextMenu.profile.contact_kind === "business" ? "personal" : "business"); setContextMenu(null); }} onToggleAutopilot={() => { void setProfileAutopilot(contextMenu.profile, !contextMenu.profile.autopilot_enabled); setContextMenu(null); }} onRefresh={() => { setSelectedId(contextMenu.profile.id); void refreshProfile(contextMenu.profile.id); setContextMenu(null); }} onRebuildProfile={() => { void rebuildProfile(contextMenu.profile.id); setContextMenu(null); }} onSyncHistory={() => { setSelectedId(contextMenu.profile.id); void syncHistory(contextMenu.profile.id); setContextMenu(null); }} onImportHtml={() => { chooseHtmlHistory(contextMenu.profile.id); setContextMenu(null); }} onDelete={() => void deleteProfile(contextMenu.profile)} />}
      {profileTasks && <ProfileTasksDialog profile={profileTasks.profile} tasks={profileTasks.tasks} onClose={() => setProfileTasks(null)} onMarkCompleted={(projectId) => void markTaskCompletedByMe(projectId)} />}
      {draftMenu && <div className="context-menu" style={{ left: draftMenu.x, top: draftMenu.y }} onClick={(event) => event.stopPropagation()}><button onClick={() => openRewriteEditor(draftMenu.start, draftMenu.end)}><Sparkles size={15} /> Переработать ИИ</button></div>}
      {rewriteEditor && (
        <RewriteEditor
          editor={rewriteEditor}
          pending={pending === "rewrite"}
          onMode={(mode) => void runRewrite(mode)}
          onApply={applyRewrite}
          onClose={() => setRewriteEditor(null)}
        />
      )}
    </main>
  );
}

function StartupScreen({ error }: { error: string }) {
  return (
    <main className="startup-screen">
      <LoaderCircle size={26} className="animate-spin text-sky-400" />
      <div>
        <p className="font-semibold text-slate-100">Запускаю Telegram Advisor</p>
        <p className="mt-1 text-sm text-muted">{error || "Подготавливаю локальную базу и backend..."}</p>
      </div>
    </main>
  );
}

function Onboarding({ initial, onReady }: { initial: OnboardingStatus; onReady: (status: OnboardingStatus) => void }) {
  const [status, setStatus] = useState(initial);
  const [account, setAccount] = useState<Account>("work");
  const [apiId, setApiId] = useState("");
  const [apiHash, setApiHash] = useState("");
  const [phone, setPhone] = useState("");
  const [auth, setAuth] = useState<TelegramAuthStatus | null>(null);
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");

  const accountStatus = status.accounts[account];

  useEffect(() => {
    const timer = window.setInterval(() => {
      void api.onboarding().then(setStatus).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!auth || !["starting", "waiting_scan", "password_required"].includes(auth.status)) return;
    const timer = window.setInterval(() => {
      void api.telegramAuthStatus(account).then((next) => {
        setAuth(next);
        if (next.status === "authorized") void api.onboarding().then(setStatus);
      }).catch((requestError) => setError(messageFrom(requestError)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [account, auth?.status]);

  useEffect(() => {
    setAuth(null);
    setApiId("");
    setApiHash("");
    setPhone("");
    setPassword("");
    setError("");
    if (status.accounts[account].credentials_saved && !status.accounts[account].authorized) {
      void api.telegramAuthStatus(account).then(setAuth).catch(() => undefined);
    }
  }, [account]);

  async function connectTelegram() {
    setPending("telegram");
    setError("");
    try {
      if (!accountStatus.credentials_saved || apiId || apiHash) {
        const next = await api.saveTelegramCredentials(account, apiId, apiHash, phone);
        setStatus(next);
      }
      setAuth(await api.startTelegramAuth(account));
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function sendPassword() {
    setPending("password");
    setError("");
    try {
      setAuth(await api.submitTelegramPassword(account, password));
      setPassword("");
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  async function loginCodex() {
    setPending("codex");
    setError("");
    try {
      await api.startCodexLogin();
    } catch (requestError) {
      setError(messageFrom(requestError));
    } finally {
      setPending("");
    }
  }

  return (
    <main className="onboarding-shell">
      <section className="onboarding-panel">
        <header className="onboarding-header">
          <div className="brand-mark"><MessageSquareText size={22} /></div>
          <div>
            <h1>Настройка Telegram Advisor</h1>
            <p>Все чаты, ключи и Telegram-сессии останутся только на этом компьютере.</p>
          </div>
        </header>

        <div className="onboarding-grid">
          <section className="setup-card">
            <div className="setup-card-title">
              <Smartphone size={18} />
              <div>
                <h2>Telegram</h2>
                <p>Подключите минимум рабочий аккаунт.</p>
              </div>
              {status.accounts.work.authorized && <CheckCircle2 size={19} className="ml-auto text-emerald-400" />}
            </div>

            <div className="segmented mt-4">
              <button className={account === "work" ? "active" : ""} onClick={() => setAccount("work")}>Рабочий</button>
              <button className={`personal ${account === "personal" ? "active" : ""}`} onClick={() => setAccount("personal")}>Личный</button>
            </div>

            {accountStatus.authorized ? (
              <div className="setup-success">
                <CheckCircle2 size={22} />
                <div><strong>Аккаунт подключён</strong><span>Можно импортировать его чаты.</span></div>
              </div>
            ) : (
              <>
                {!accountStatus.credentials_saved && (
                  <div className="setup-fields">
                    <label>Telegram API ID<input inputMode="numeric" value={apiId} onChange={(event) => setApiId(event.target.value)} placeholder="Например, 12345678" /></label>
                    <label>Telegram API Hash<input type="password" value={apiHash} onChange={(event) => setApiHash(event.target.value)} placeholder="32 символа" /></label>
                    <label>Телефон, необязательно<input value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+79990000000" /></label>
                    <p className="field-hint">API ID и Hash создаются на my.telegram.org в разделе API development tools.</p>
                  </div>
                )}

                {auth?.qr_data_url && <div className="qr-box"><img src={auth.qr_data_url} alt="QR для входа в Telegram" /><p>{auth.message}</p></div>}
                {auth?.status === "password_required" && (
                  <div className="password-row">
                    <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} onKeyDown={(event) => event.key === "Enter" && void sendPassword()} placeholder="Пароль 2FA Telegram" />
                    <button className="primary-button" disabled={!password || pending === "password"} onClick={() => void sendPassword()}>Подтвердить</button>
                  </div>
                )}
                {auth?.status === "error" && <p className="setup-error">{auth.message}</p>}
                {!auth?.qr_data_url && auth?.status !== "password_required" && (
                  <button className="primary-button mt-4 w-full" disabled={pending === "telegram" || (!accountStatus.credentials_saved && (!apiId || !apiHash))} onClick={() => void connectTelegram()}>
                    {pending === "telegram" ? <LoaderCircle size={15} className="animate-spin" /> : <Smartphone size={15} />}
                    {accountStatus.credentials_saved ? "Показать QR для входа" : "Сохранить и показать QR"}
                  </button>
                )}
              </>
            )}
          </section>

          <section className="setup-card">
            <div className="setup-card-title">
              <KeyRound size={18} />
              <div>
                <h2>Codex</h2>
                <p>AI будет работать по подписке владельца компьютера.</p>
              </div>
              {status.codex.authenticated && <CheckCircle2 size={19} className="ml-auto text-emerald-400" />}
            </div>

            {status.codex.authenticated ? (
              <div className="setup-success">
                <CheckCircle2 size={22} />
                <div><strong>Codex подключён</strong><span>{status.codex.message || "Авторизация подтверждена."}</span></div>
              </div>
            ) : (
              <div className="mt-5">
                <p className="text-sm leading-6 text-muted">Нажмите кнопку и завершите вход в открывшемся окне. Обычно браузер попросит войти в ChatGPT.</p>
                <button className="primary-button mt-4 w-full" disabled={!status.codex.installed || pending === "codex"} onClick={() => void loginCodex()}>
                  <KeyRound size={15} /> Войти в Codex
                </button>
                {!status.codex.installed && <p className="setup-error">Codex CLI отсутствует в установке.</p>}
              </div>
            )}
          </section>
        </div>

        <footer className="onboarding-footer">
          <div>
            <p className="text-xs text-muted">Папка данных</p>
            <p className="max-w-[680px] truncate text-xs text-slate-400">{status.data_dir}</p>
          </div>
          <button className="primary-button h-10 px-5" disabled={!status.complete} onClick={() => onReady(status)}>Открыть приложение</button>
        </footer>
        {error && <div className="setup-error mt-4">{error}</div>}
      </section>
    </main>
  );
}

function ResizeHandle() {
  return <Separator className="resize-handle" title="Потяните, чтобы изменить ширину панели"><span /></Separator>;
}

function AccountDrawer({ open, source, gmailAccounts, gmailActiveAccountId, gmailEmail, maxConnected, maxBotName, activeProjectCount, onClose, onSelectTelegram, onSelectGmail, onAddGmail, onSelectMax, onOpenCrm }: {
  open: boolean;
  source: Source;
  gmailAccounts: GmailAccount[];
  gmailActiveAccountId: string;
  gmailEmail: string;
  maxConnected: boolean;
  maxBotName: string;
  activeProjectCount: number;
  onClose: () => void;
  onSelectTelegram: (account: Account) => void;
  onSelectGmail: (accountId: string) => void;
  onAddGmail: () => void;
  onSelectMax: () => void;
  onOpenCrm: () => void;
}) {
  const swipeStart = useRef<{ x: number; y: number } | null>(null);

  const beginDrawerSwipe = (event: ReactPointerEvent<HTMLElement>) => {
    if (event.pointerType === "touch") swipeStart.current = { x: event.clientX, y: event.clientY };
  };

  const finishDrawerSwipe = (event: ReactPointerEvent<HTMLElement>) => {
    const start = swipeStart.current;
    swipeStart.current = null;
    if (!start || event.pointerType !== "touch") return;
    const horizontalDistance = event.clientX - start.x;
    const verticalDistance = event.clientY - start.y;
    if (horizontalDistance < -64 && Math.abs(horizontalDistance) > Math.abs(verticalDistance)) onClose();
  };

  return <div className={`account-drawer-backdrop ${open ? "open" : ""}`} aria-hidden={!open} onMouseDown={onClose}>
    <aside
      className="account-drawer"
      onMouseDown={(event) => event.stopPropagation()}
      onPointerDown={beginDrawerSwipe}
      onPointerUp={finishDrawerSwipe}
      onPointerCancel={() => { swipeStart.current = null; }}
    >
      <header className="account-drawer-header">
        <div className="account-avatar">М</div>
        <div><strong>Михаил</strong><p>Аккаунты и подключения</p></div>
        <button className="icon-button" title="Закрыть" onClick={onClose}><X size={18} /></button>
      </header>
      <section className="account-drawer-section">
        <p className="account-drawer-label">Telegram</p>
        <div className="account-grid">
          <button className={source === "work" ? "active" : ""} onClick={() => onSelectTelegram("work")}><Briefcase size={16} /><span>Рабочий</span></button>
          <button className={source === "personal" ? "active personal" : ""} onClick={() => onSelectTelegram("personal")}><UserRound size={16} /><span>Личный</span></button>
        </div>
      </section>
      <section className="account-drawer-section">
        <button className="account-row" onClick={onOpenCrm}><Briefcase size={17} className="text-sky-300" /><span><strong>CRM: проекты и оплата</strong><small>Задачи, лиды и расчёты</small></span><b className="rounded-full bg-sky-400/15 px-2 py-0.5 text-xs text-sky-200">{activeProjectCount}</b></button>
      </section>
      <section className="account-drawer-section">
        <div className="flex items-center justify-between"><p className="account-drawer-label">Почта Gmail</p><button className="icon-button" title="Добавить Gmail" onClick={onAddGmail}><Plus size={17} /></button></div>
        {gmailAccounts.map((account) => <button key={account.id} className={`account-row ${source === "gmail" && gmailActiveAccountId === account.id ? "active" : ""}`} onClick={() => onSelectGmail(account.id)}>
          <Mail size={17} className="text-pink-300" /><span><strong>{account.email || "Подключённая почта"}</strong><small>Ярлык: {account.label}</small></span>{gmailActiveAccountId === account.id && <CheckCircle2 size={16} className="text-emerald-300" />}
        </button>)}
        {!gmailAccounts.length && <button className="account-row" onClick={onAddGmail}><Mail size={17} className="text-pink-300" /><span><strong>Подключить Gmail</strong><small>Можно добавить несколько ящиков</small></span></button>}
        {source === "gmail" && gmailEmail && <p className="mt-2 truncate text-[11px] text-pink-300">Открыт: {gmailEmail}</p>}
      </section>
      <section className="account-drawer-section">
        <p className="account-drawer-label">Другие каналы</p>
        <button className={`account-row ${source === "max" ? "active" : ""}`} onClick={onSelectMax}><Smartphone size={17} className="text-emerald-300" /><span><strong>MAX</strong><small>{maxConnected ? `Бот: ${maxBotName || "подключён"}` : "Подключается в настройках"}</small></span></button>
      </section>
    </aside>
  </div>;
}

function GmailAccountDialog({ clientId, clientSecret, label, pending, setClientId, setClientSecret, setLabel, onClose, onConnect }: {
  clientId: string;
  clientSecret: string;
  label: string;
  pending: boolean;
  setClientId: (value: string) => void;
  setClientSecret: (value: string) => void;
  setLabel: (value: string) => void;
  onClose: () => void;
  onConnect: () => void;
}) {
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal" onMouseDown={(event) => event.stopPropagation()}>
    <div className="flex items-center justify-between"><h2>Добавить Gmail</h2><button className="icon-button" title="Закрыть" onClick={onClose}><X size={18} /></button></div>
    <p>Для каждого ящика Google откроет отдельное окно авторизации. Можно использовать тот же OAuth Client ID.</p>
    <input value={clientId} onChange={(event) => setClientId(event.target.value)} placeholder="OAuth Client ID" />
    <input type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} placeholder="OAuth Client Secret" />
    <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Ярлык Gmail, например Advisor" />
    <div className="mt-5 flex justify-end gap-2"><button className="secondary-button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={!clientId.trim() || !clientSecret.trim() || pending} onClick={onConnect}>{pending ? <LoaderCircle size={15} className="animate-spin" /> : <Mail size={15} />} Подключить</button></div>
  </section></div>;
}

function Avatar({ profile, compact = false }: { profile: Profile; compact?: boolean }) {
  const initial = profile.chat_name.trim().slice(0, 1).toUpperCase() || "?";
  return (
    <span className={`avatar ${compact ? "avatar-compact" : ""}`}>
      <img src={apiUrl(profile.avatar_url)} alt="" onError={(event) => { event.currentTarget.style.display = "none"; }} />
      <span>{initial}</span>
    </span>
  );
}

function MessageBubble({ message, isNew, outsideHoursLabel, onReply }: { message: Message; isNew: boolean; outsideHoursLabel: string; onReply: (target: ReplyTarget) => void }) {
  const canReply = Number(message.telegram_message_id) > 0;
  return (
    <article id={message.telegram_message_id ? `message-${message.telegram_message_id}` : undefined} className={`message-row ${message.out ? "out" : "in"}`}>
      <div className={`bubble ${message.out ? "bubble-out" : "bubble-in"} ${isNew ? "message-new" : ""}`}>
        {message.reply_to_telegram_message_id && <div className="message-reply-link">Ответ на сообщение</div>}
        <div className="mb-1 flex items-center gap-2 text-[11px] font-semibold text-slate-200/80">
          <span>{message.out ? "Вы" : message.sender}</span>
          {outsideHoursLabel && <span className="after-hours-badge">{outsideHoursLabel}</span>}
        </div>
        {message.media_url && <img className="message-media" src={apiUrl(message.media_url)} alt={message.text || "Изображение из Telegram"} loading="lazy" />}
        {message.text && <p className="whitespace-pre-wrap break-words text-[14px] leading-5">{message.text}</p>}
        <div className="mt-1.5 flex items-center justify-end gap-1 text-[11px] text-slate-200/70">
          <span>{formatTime(message.message_date)}</span>
          {message.out && <Delivery status={message.delivery_status} />}
        </div>
        {canReply && <button className="reply-button" title="Ответить" aria-label="Ответить" onClick={() => onReply({ id: message.telegram_message_id as number, sender: message.out ? "ваше сообщение" : message.sender, text: message.text || "Медиа" })}><Reply size={13} /></button>}
      </div>
    </article>
  );
}

function Delivery({ status }: { status: string }) {
  return <span className="tracking-[-2px] text-sky-100" aria-label={status === "read" ? "Прочитано" : "Отправлено"}>{status === "read" ? "✓✓" : "✓"}</span>;
}

function RewriteEditor({ editor, pending, onMode, onApply, onClose }: {
  editor: RewriteEditorState;
  pending: boolean;
  onMode: (mode: RewriteMode) => void;
  onApply: () => void;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section className="ai-editor" onMouseDown={(event) => event.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles size={19} className="text-sky-400" />
            <h2>ИИ-редактор</h2>
          </div>
          <button className="icon-button" title="Закрыть" onClick={onClose}><X size={18} /></button>
        </div>

        <div className="rewrite-modes">
          {rewriteModes.map((mode) => (
            <button
              key={mode.id}
              className={editor.mode === mode.id ? "active" : ""}
              disabled={pending}
              onClick={() => onMode(mode.id)}
            >
              {mode.label}
            </button>
          ))}
        </div>

        <div className="rewrite-card">
          <span className="rewrite-label">Оригинал</span>
          <p>{editor.source}</p>
        </div>

        <div className={`rewrite-card result ${pending ? "loading" : ""}`}>
          <span className="rewrite-label">Результат</span>
          {pending ? (
            <div className="flex items-center gap-2 text-sm text-sky-300"><LoaderCircle size={16} className="animate-spin" /> Перерабатываю текст...</div>
          ) : (
            <p>{editor.result || "Выберите вариант переработки выше."}</p>
          )}
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <button className="secondary-button" onClick={onClose}>Отмена</button>
          <button className="primary-button" disabled={!editor.result || pending} onClick={onApply}>Заменить текст</button>
        </div>
      </section>
    </div>
  );
}

function GmailSourceList({ connected, clientId, clientSecret, label, contacts, selectedId, pending, setClientId, setClientSecret, setLabel, setSelectedId, connect, refresh, saveLabel, openGroups, reconnect }: {
  connected: boolean;
  clientId: string;
  clientSecret: string;
  label: string;
  contacts: GmailContact[];
  selectedId: string | null;
  pending: string;
  setClientId: (value: string) => void;
  setClientSecret: (value: string) => void;
  setLabel: (value: string) => void;
  setSelectedId: (value: string) => void;
  connect: () => void;
  refresh: () => void;
  saveLabel: () => void;
  openGroups: () => void;
  reconnect: () => void;
}) {
  if (!connected) {
    return <div className="space-y-3 p-2">
      <div><h3 className="text-sm font-semibold text-slate-100">Подключить Gmail</h3><p className="mt-1 text-xs leading-5 text-muted">Нужен OAuth-клиент типа Desktop app с включённым Gmail API. Приложение запросит чтение и отправку писем, без удаления и изменения почты.</p></div>
      <input className="settings-input" value={clientId} onChange={(event) => setClientId(event.target.value)} placeholder="OAuth Client ID" />
      <input className="settings-input" type="password" value={clientSecret} onChange={(event) => setClientSecret(event.target.value)} placeholder="OAuth Client Secret" />
      <input className="settings-input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Ярлык Gmail, например Advisor" />
      <button className="primary-button" disabled={!clientId.trim() || !clientSecret.trim() || Boolean(pending)} onClick={connect}><Mail size={15} /> {pending === "gmail-connect" ? "Ожидаю Google…" : "Подключить Gmail"}</button>
    </div>;
  }
  return <div className="space-y-2">
    <div className="flex gap-2 px-1 pb-2">
      <input className="settings-input" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Ярлык Gmail" />
      <button className="icon-button shrink-0 border border-line" title="Сохранить ярлык" disabled={Boolean(pending)} onClick={saveLabel}><CheckCircle2 size={14} /></button>
      <button className="icon-button shrink-0 border border-line" title="Пометить письма по группам Google Contacts" disabled={Boolean(pending)} onClick={openGroups}><UserRound size={14} /></button>
      <button className="icon-button shrink-0 border border-line" title="Обновить разрешения Google" disabled={Boolean(pending)} onClick={reconnect}><KeyRound size={14} /></button>
      <button className="icon-button shrink-0 border border-line" title="Обновить почту" disabled={Boolean(pending)} onClick={refresh}><RefreshCw size={14} /></button>
    </div>
    <p className="px-1 pb-1 text-[11px] text-muted">Все цепочки · {label}</p>
      {contacts.map((contact) => <button key={contact.id} className={`block w-full rounded-md border p-3 text-left transition ${selectedId === contact.id ? "border-pink-400/70 bg-pink-400/10" : "border-line bg-[#0a141e] hover:border-pink-400/60"}`} onClick={() => setSelectedId(contact.id)}>
        <div className="flex items-start justify-between gap-3"><strong className="truncate text-sm text-slate-100">{contact.name}</strong><span className="text-[10px] text-muted">{contact.message_count}</span></div>
        <p className="mt-1 truncate text-xs text-pink-300">{contact.email}</p>
        <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{contact.preview}</p>
      </button>)}
      {!contacts.length && pending !== "gmail-refresh" && <div className="py-12 text-center text-sm text-muted">Контактов с этим ярлыком нет.</div>}
      {pending === "gmail-refresh" && <div className="py-12 text-center text-sm text-muted">Загружаю письма…</div>}
  </div>;
}

function GmailContactGroupsDialog({ groups, selectedIds, status, pending, onToggle, onClose, onStart }: {
  groups: GoogleContactGroup[];
  selectedIds: string[];
  status: GmailContactLabelStatus;
  pending: boolean;
  onToggle: (groupId: string) => void;
  onClose: () => void;
  onStart: () => void;
}) {
  const running = status.state === "running";
  const progress = status.total ? Math.round(status.current / status.total * 100) : 0;
  return <div className="modal-backdrop" onMouseDown={running ? undefined : onClose}><section className="modal max-w-lg" onMouseDown={(event) => event.stopPropagation()}>
    <div className="flex items-center justify-between"><h2>Google Contacts → {"Advisor"}</h2><button className="icon-button" title="Закрыть" disabled={running} onClick={onClose}><X size={18} /></button></div>
    <p>Выберите группы. Найденные письма от их контактов получат ярлык Advisor.</p>
    <div className="mt-4 max-h-64 space-y-1 overflow-y-auto rounded-md border border-line bg-[#0a141e] p-2">
      {groups.map((group) => <label key={group.id} className="contact-group-option"><input type="checkbox" checked={selectedIds.includes(group.id)} disabled={running} onChange={() => onToggle(group.id)} /><span className="min-w-0 flex-1 truncate">{group.name}</span><span>{group.member_count}</span></label>)}
      {!groups.length && <p className="px-2 py-5 text-center text-sm text-muted">Группы не найдены.</p>}
    </div>
    {status.message && <div className={`mt-4 rounded-md border px-3 py-2 text-xs ${status.state === "error" ? "border-red-400/40 bg-red-400/10 text-red-200" : "border-sky-400/30 bg-sky-400/10 text-sky-200"}`}><p>{status.message}</p>{running && <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-700"><div className="h-full bg-sky-400 transition-all" style={{ width: `${Math.max(4, progress)}%` }} /></div>}</div>}
    <div className="mt-5 flex justify-end gap-2"><button className="secondary-button" disabled={running} onClick={onClose}>Закрыть</button><button className="primary-button" disabled={!selectedIds.length || pending || running} onClick={onStart}>{pending || running ? <LoaderCircle size={15} className="animate-spin" /> : <CheckCircle2 size={15} />} Пометить письма</button></div>
  </section></div>;
}

function GmailWorkspace({ contact, canSend, draft, pending, setDraft, onSend, onAuthorize, onSaveLead, onMobileBack, onMobileAi }: {
  contact: GmailContact | null;
  canSend: boolean;
  draft: string;
  pending: string;
  setDraft: (value: string) => void;
  onSend: () => void;
  onAuthorize: () => void;
  onSaveLead: (lead: Pick<CrmLead, "status" | "category" | "summary" | "next_action">) => void;
  onMobileBack: () => void;
  onMobileAi: () => void;
}) {
  const [leadOpen, setLeadOpen] = useState(false);
  const [leadStatus, setLeadStatus] = useState<CrmLead["status"]>("reply");
  const [leadCategory, setLeadCategory] = useState("");
  const [leadSummary, setLeadSummary] = useState("");
  const [leadNextAction, setLeadNextAction] = useState("");
  useEffect(() => {
    const lead = contact?.crm_lead;
    setLeadStatus(lead?.status ?? "reply");
    setLeadCategory(lead?.category ?? "");
    setLeadSummary(lead?.summary ?? contact?.preview ?? "");
    setLeadNextAction(lead?.next_action ?? "");
    setLeadOpen(false);
  }, [contact?.id, contact?.crm_lead?.updated_at]);
  if (!contact) return <section className="flex h-full items-center justify-center bg-[#0b1621] text-sm text-muted">Выберите контакт слева</section>;
  const lastThreadId = contact.messages.at(-1)?.thread_id;
  return <section className="flex h-full min-w-0 flex-col bg-[#0b1621]">
    <header className="gmail-chat-header border-b border-line px-6 py-4"><div className="flex items-start justify-between gap-4"><button className="mobile-nav-button" title="К списку писем" onClick={onMobileBack}><ArrowLeft size={20} /></button><div className="min-w-0 flex-1"><h1 className="truncate text-base font-semibold text-slate-100">{contact.name}</h1><p className="mt-1 truncate text-xs text-pink-300">{contact.email} · {contact.message_count} сообщений</p></div><button className={`secondary-button shrink-0 ${contact.crm_lead ? "border-sky-400/50 text-sky-200" : ""}`} onClick={() => setLeadOpen((open) => !open)}><Briefcase size={14} /> {contact.crm_lead ? "В CRM" : "Добавить в CRM"}</button><button className="mobile-nav-button" title="AI-ответ" onClick={onMobileAi}><PanelRight size={19} /></button></div>
      {leadOpen && <div className="mt-4 grid gap-2 rounded-md border border-sky-400/30 bg-sky-400/5 p-3 sm:grid-cols-2"><label className="text-xs text-muted">Статус<select className="settings-input mt-1" value={leadStatus} onChange={(event) => setLeadStatus(event.target.value as CrmLead["status"])}><option value="interested">Заинтересован</option><option value="reply">Нужно ответить</option><option value="not_relevant">Неактуален</option></select></label><label className="text-xs text-muted">Категория<input className="settings-input mt-1" value={leadCategory} onChange={(event) => setLeadCategory(event.target.value)} placeholder="Например: сайт, вакансия" /></label><label className="text-xs text-muted sm:col-span-2">Суть<textarea className="settings-input mt-1 min-h-16" value={leadSummary} onChange={(event) => setLeadSummary(event.target.value)} placeholder="Коротко: о чём договорились" /></label><label className="text-xs text-muted sm:col-span-2">Следующее действие<input className="settings-input mt-1" value={leadNextAction} onChange={(event) => setLeadNextAction(event.target.value)} placeholder="Например: ответить завтра и уточнить бюджет" /></label><div className="flex justify-end gap-2 sm:col-span-2"><button className="secondary-button" onClick={() => setLeadOpen(false)}>Отмена</button><button className="primary-button" disabled={Boolean(pending)} onClick={() => { onSaveLead({ status: leadStatus, category: leadCategory, summary: leadSummary, next_action: leadNextAction }); setLeadOpen(false); }}><Briefcase size={14} /> Сохранить</button></div></div>}
    </header>
    <div className="chat-scroll min-h-0 flex-1 overflow-y-auto px-5 py-5">
      {contact.messages.map((message, index) => <Fragment key={message.id}>
        {(!contact.messages[index - 1] || dayKey(new Date(message.date)) !== dayKey(new Date(contact.messages[index - 1].date))) && <div className="date-divider"><span>{formatDateLabel(message.date)}</span></div>}
        <article className={`message-row ${message.out ? "out" : "in"}`}>
          <div className={`bubble ${message.out ? "bubble-out" : "bubble-in"}`}>
            <div className="mb-1 text-[11px] font-semibold text-slate-200/80">{message.out ? "Вы" : message.sender || contact.name}</div>
            <GmailMessageText text={message.text} />
            {message.technical_text && <details className="mt-2 border-t border-white/10 pt-2 text-xs text-slate-300/80">
              <summary className="cursor-pointer select-none text-[11px] font-medium text-slate-300/70">Техническая часть</summary>
              <p className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap break-words leading-5">{message.technical_text}</p>
            </details>}
            <div className="mt-1.5 text-right text-[11px] text-slate-200/70">{formatTime(message.date)}</div>
          </div>
        </article>
      </Fragment>)}
    </div>
    <div className="shrink-0 border-t border-line bg-[#0e1925] px-4 py-3">
      <div className="flex items-end gap-3">
        <textarea
          className="composer"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSend();
            }
          }}
          placeholder={canSend ? "Написать письмо" : "Разрешите отправку писем"}
          rows={1}
          disabled={!canSend || Boolean(pending)}
        />
        {!canSend && <button className="secondary-button shrink-0" disabled={Boolean(pending)} onClick={onAuthorize}><KeyRound size={15} /> Разрешить</button>}
        <button className="send-button shrink-0" disabled={!canSend || !draft.trim() || Boolean(pending)} onClick={onSend} title="Отправить письмо">
          {pending === "gmail-send" ? <LoaderCircle size={18} className="animate-spin" /> : <SendHorizontal size={18} />}
        </button>
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-[11px] text-muted">Enter отправляет, Shift+Enter добавляет строку</p>
        {lastThreadId && <button className="text-xs text-sky-400 hover:text-sky-300" onClick={() => isTauriApp ? void invoke("open_gmail_thread", { threadId: lastThreadId }) : window.open(`https://mail.google.com/mail/u/0/#all/${lastThreadId}`, "_blank", "noopener")}><Mail size={13} className="inline" /> Открыть в Gmail</button>}
      </div>
    </div>
  </section>;
}

function MaxSourceList({ connected, botName, contacts, selectedId, pending, onSelect, onRefresh, onOpenSettings }: { connected: boolean; botName: string; contacts: MaxContact[]; selectedId: string | null; pending: string; onSelect: (id: string) => void; onRefresh: () => void; onOpenSettings: () => void }) {
  if (!connected) return <div className="px-4 py-12 text-center text-sm text-muted"><p>Подключите бота MAX, чтобы получать новые сообщения.</p><button className="secondary-button mt-4" onClick={onOpenSettings}><KeyRound size={15} /> Подключить MAX</button></div>;
  return <div className="space-y-2"><div className="flex items-center justify-between px-1 pb-2"><p className="text-[11px] text-muted">Бот: {botName}</p><div className="flex gap-1"><button className="icon-button shrink-0 border border-line" title="Настройки MAX" onClick={onOpenSettings}><KeyRound size={14} /></button><button className="icon-button shrink-0 border border-line" title="Обновить MAX" disabled={Boolean(pending)} onClick={onRefresh}><RefreshCw size={14} /></button></div></div>{contacts.map((contact) => <button key={contact.id} className={`block w-full rounded-md border p-3 text-left transition ${selectedId === contact.id ? "border-emerald-400/70 bg-emerald-400/10" : "border-line bg-[#0a141e] hover:border-emerald-400/60"}`} onClick={() => onSelect(contact.id)}><div className="flex items-start justify-between gap-3"><strong className="truncate text-sm text-slate-100">{contact.name}</strong><span className="text-[10px] text-muted">{contact.message_count}</span></div><p className="mt-1 truncate text-xs text-emerald-300">{contact.email}</p><p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{contact.preview}</p></button>)}{!contacts.length && <div className="py-12 text-center text-sm text-muted">Пока нет сообщений. Откройте бота в MAX и напишите ему.</div>}</div>;
}

function MaxWorkspace({ contact, draft, pending, setDraft, onSend, onMobileBack, onMobileAi }: { contact: MaxContact | null; draft: string; pending: string; setDraft: (value: string) => void; onSend: () => void; onMobileBack: () => void; onMobileAi: () => void }) {
  if (!contact) return <section className="flex h-full items-center justify-center bg-[#0b1621] text-sm text-muted">Выберите контакт MAX слева</section>;
  return <section className="flex h-full min-w-0 flex-col bg-[#0b1621]"><header className="max-chat-header flex items-start gap-3 border-b border-line px-6 py-4"><button className="mobile-nav-button" title="К списку MAX" onClick={onMobileBack}><ArrowLeft size={20} /></button><div className="min-w-0 flex-1"><h1 className="truncate text-base font-semibold text-slate-100">{contact.name}</h1><p className="mt-1 truncate text-xs text-emerald-300">{contact.email} · {contact.message_count} сообщений</p></div><button className="mobile-nav-button" title="AI-ответ" onClick={onMobileAi}><PanelRight size={19} /></button></header><div className="chat-scroll min-h-0 flex-1 overflow-y-auto px-5 py-5">{contact.messages.map((message) => <article key={message.id} className={`message-row ${message.out ? "out" : "in"}`}><div className={`bubble ${message.out ? "bubble-out" : "bubble-in"}`}><div className="mb-1 text-[11px] font-semibold text-slate-200/80">{message.out ? "Вы" : message.sender}</div><p className="whitespace-pre-wrap break-words text-[14px] leading-5">{message.text}</p><div className="mt-1.5 text-right text-[11px] text-slate-200/70">{formatTime(message.date)}</div></div></article>)}</div><div className="shrink-0 border-t border-line bg-[#0e1925] px-4 py-3"><div className="flex items-end gap-3"><textarea className="composer" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); onSend(); } }} placeholder="Сообщение в MAX" rows={1} disabled={Boolean(pending)} /><button className="send-button shrink-0" disabled={!draft.trim() || Boolean(pending)} onClick={onSend} title="Отправить в MAX">{pending === "max-send" ? <LoaderCircle size={18} className="animate-spin" /> : <SendHorizontal size={18} />}</button></div><p className="mt-2 text-[11px] text-muted">Enter отправляет, Shift+Enter добавляет строку</p></div></section>;
}

function GmailMessageText({ text }: { text: string }) {
  if (text.length <= 800) return <p className="whitespace-pre-wrap break-words text-[14px] leading-5">{text}</p>;
  return <details className="gmail-long-message">
    <summary><span className="gmail-long-preview">{text.slice(0, 800).trimEnd()}…</span><span className="gmail-long-toggle" /></summary>
    <p className="whitespace-pre-wrap break-words text-[14px] leading-5">{text}</p>
  </details>;
}

function ContextSummary({ analysis, detail, fallbackTone, fallbackGoal, fallbackRole, contactName, messageCount, relationship, topic }: { analysis: Analysis | null; detail: ProfileDetail | null; fallbackTone: string; fallbackGoal: string; fallbackRole: string; contactName?: string; messageCount?: number; relationship?: string; topic?: string }) {
  const context = analysis?.context;
  const rows = [
    `${context?.message_count ?? detail?.messages.length ?? messageCount ?? 0} сообщений${context?.topic_memory ? " + память темы" : ""}`,
    `контакт: ${context?.contact ?? detail?.chat_name ?? contactName ?? "не выбран"}`,
    `отношения: ${context?.relationship ?? relationship ?? (detail?.contact_kind === "business" ? "рабочие" : "личные")}`,
    `тема: ${context?.topic ?? detail?.active_topic ?? topic ?? "Авто"}`,
    `роль: ${context?.role ?? fallbackRole}`,
    `тон: ${context?.tone ?? fallbackTone}`,
    `цель: ${context?.goal ?? (fallbackGoal.trim() || "ответить по текущей ситуации")}`,
  ];
  return <details className="context-summary mb-4" open><summary>Контекст</summary><div>{rows.map((row) => <span key={row}><CheckCircle2 size={12} /> {row}</span>)}</div></details>;
}

function WorkingMemoryPanel({ memory, pending, onSync, onEdit, onUpdate, onDelete, onOpenSource }: {
  memory: WorkingMemory | null;
  pending: boolean;
  onSync: () => void;
  onEdit: (item: MemoryItem) => void;
  onUpdate: (item: MemoryItem, changes: Partial<MemoryItem>) => void;
  onDelete: (item: MemoryItem) => void;
  onOpenSource: (messageId: number | null | undefined) => void;
}) {
  if (!memory) return <div className="flex min-h-48 items-center justify-center text-sm text-muted"><LoaderCircle size={17} className="mr-2 animate-spin" /> Загружаю рабочую память</div>;
  const groups: Array<[MemoryItem["kind"], string]> = [["commitment", "Обязательства"], ["task", "Задачи"], ["money_event", "Деньги"], ["follow_up", "Следующее касание"], ["status", "Статус"], ["note", "Важные заметки"]];
  return <div className="space-y-4">
    <div className="flex items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-slate-100">{memory.contact.display_name}</h3><p className="mt-1 text-xs text-muted">{memory.contact.relationship_status || "Статус пока не определён"}</p></div><button className="secondary-button shrink-0" disabled={pending} onClick={onSync}>{pending ? <LoaderCircle size={14} className="animate-spin" /> : <RefreshCw size={14} />} Обновить память</button></div>
    {memory.last_run?.status === "error" && <div className="border-l-2 border-red-400 bg-red-400/10 px-3 py-2 text-xs text-red-300">{memory.last_run.error}</div>}
    <section className="rounded-lg border border-line bg-[#0d1d29] p-3"><p className="text-[11px] font-semibold uppercase text-muted">Что происходит</p><p className="mt-2 whitespace-pre-wrap text-sm leading-5 text-slate-200">{memory.contact.context_summary || "Память ещё не построена. Нажмите «Обновить память»."}</p>{memory.contact.next_action && <div className="mt-3 border-l-2 border-sky-400 pl-3"><p className="text-[11px] text-sky-300">Следующий шаг</p><p className="mt-1 text-sm">{memory.contact.next_action}</p></div>}</section>
    <div className="grid grid-cols-3 gap-2"><MemoryMoney label="Согласовано" value={formatRubles(memory.totals.agreed)} /><MemoryMoney label="Получено" value={formatRubles(memory.totals.received)} /><MemoryMoney label="Остаток" value={formatRubles(memory.totals.remaining)} accent /></div>
    {groups.map(([kind, label]) => { const items = memory.items.filter((item) => item.kind === kind && item.status !== "deleted"); if (!items.length) return null; return <section key={kind} className="space-y-2"><h4 className="text-xs font-semibold uppercase text-muted">{label}</h4>{items.map((item) => <MemoryItemCard key={item.id} item={item} onEdit={onEdit} onUpdate={onUpdate} onDelete={onDelete} onOpenSource={onOpenSource} />)}</section>; })}
    {!memory.items.length && <p className="py-8 text-center text-sm text-muted">Значимых договорённостей пока не найдено.</p>}
  </div>;
}

function MemoryItemCard({ item, onEdit, onUpdate, onDelete, onOpenSource }: { item: MemoryItem; onEdit: (item: MemoryItem) => void; onUpdate: (item: MemoryItem, changes: Partial<MemoryItem>) => void; onDelete: (item: MemoryItem) => void; onOpenSource: (messageId: number | null | undefined) => void }) {
  return <article className="rounded-lg border border-line bg-[#0d1d29] p-3">
    <div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="flex flex-wrap items-center gap-1.5"><span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${certaintyClass(item.certainty)}`}>{certaintyLabel(item.certainty)}</span><span className="text-[10px] text-muted">{item.created_by === "AI" ? "AI" : "вручную"} · {item.actor === "me" ? "я" : item.actor === "them" ? "контакт" : "кто-то"}</span></div><p className={`mt-2 text-sm font-medium leading-5 ${item.status === "done" ? "text-muted line-through" : "text-slate-100"}`}>{item.title}</p></div><div className="flex shrink-0"><button className="icon-button" title="Исправить" onClick={() => onEdit(item)}><Pencil size={13} /></button><button className="icon-button text-red-300" title="Удалить" onClick={() => onDelete(item)}><Trash2 size={13} /></button></div></div>
    {(item.amount > 0 || item.due_at) && <p className="mt-2 text-xs text-amber-200">{item.amount > 0 ? formatRubles(item.amount) : ""}{item.amount > 0 && item.due_at ? " · " : ""}{item.due_at ? `срок ${item.due_at}` : ""}</p>}
    {item.details && <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{item.details}</p>}
    {item.sources.length > 0 && <div className="mt-3 space-y-1.5">{item.sources.map((source) => <button key={source.id} className="block w-full rounded border border-sky-900/80 bg-sky-950/30 px-2 py-1.5 text-left text-[11px] text-sky-200 hover:border-sky-500" onClick={() => onOpenSource(source.locator.telegram_message_id)} title={source.source_excerpt || source.text}><span className="font-semibold">Источник #{source.external_message_id}</span><span className="ml-2 text-sky-300/70">{(source.source_excerpt || source.text).slice(0, 90)}</span></button>)}</div>}
    <div className="mt-3 flex items-center justify-between gap-2"><select className="rounded border border-line bg-[#0b1621] px-2 py-1 text-[11px]" value={item.certainty} onChange={(event) => onUpdate(item, { certainty: event.target.value as MemoryItem["certainty"] })}><option value="CONFIRMED">Подтверждено</option><option value="INFERRED">Вывод</option><option value="UNCERTAIN">Неопределённо</option></select>{["task", "commitment", "follow_up"].includes(item.kind) && <button className="secondary-button !px-2 !py-1 text-[11px]" onClick={() => onUpdate(item, { status: item.status === "done" ? "active" : "done" })}><CheckCircle2 size={13} /> {item.status === "done" ? "Вернуть" : "Сделано"}</button>}</div>
  </article>;
}

function MemoryMoney({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return <div className={`rounded-lg border p-2 ${accent ? "border-amber-500/40 bg-amber-500/10" : "border-line bg-[#0d1d29]"}`}><span className="block text-[10px] text-muted">{label}</span><strong className="mt-1 block text-xs">{value}</strong></div>;
}

function WorkProfilePanel({ profile, pending, onChange, onSave }: { profile: WorkProfile; pending: boolean; onChange: (profile: WorkProfile) => void; onSave: () => void }) {
  const field = (key: keyof WorkProfile, value: string | number) => onChange({ ...profile, [key]: value });
  return <div className="space-y-4"><div><h3 className="text-sm font-semibold">Рабочий профиль</h3><p className="mt-1 text-xs leading-5 text-muted">Постоянный контекст для оценки заказов, рабочей памяти и ответов.</p></div><WorkProfileField label="Кто я" value={profile.about} onChange={(value) => field("about", value)} placeholder="Frontend / web developer, опыт и специализация" /><WorkProfileField label="Навыки" value={profile.skills} onChange={(value) => field("skills", value)} placeholder="HTML, CSS, JavaScript, React, WordPress" /><div className="grid grid-cols-2 gap-3"><label className="text-xs font-medium text-slate-300">Ставка, ₽/час<input className="mt-2 w-full rounded border border-line bg-[#0b1621] px-3 py-2" type="number" min="0" value={profile.base_rate} onChange={(event) => field("base_rate", Number(event.target.value))} /></label><label className="text-xs font-medium text-slate-300">Минимальный заказ, ₽<input className="mt-2 w-full rounded border border-line bg-[#0b1621] px-3 py-2" type="number" min="0" value={profile.minimum_order} onChange={(event) => field("minimum_order", Number(event.target.value))} /></label></div><WorkProfileField label="Правила оценки" value={profile.pricing_rules} onChange={(value) => field("pricing_rules", value)} placeholder="Когда нужен диапазон, что входит в оценку" /><WorkProfileField label="Риски" value={profile.risk_rules} onChange={(value) => field("risk_rules", value)} placeholder="Legacy +20%, неясное ТЗ — сначала уточнения" /><WorkProfileField label="Стиль общения" value={profile.style} onChange={(value) => field("style", value)} placeholder="Лексика, длина ответа, допустимый тон" /><WorkProfileField label="Границы" value={profile.boundaries} onChange={(value) => field("boundaries", value)} placeholder="Что не обещать, какие задачи не брать" /><div className="flex justify-end"><button className="primary-button" disabled={pending} onClick={onSave}>{pending ? <LoaderCircle size={15} className="animate-spin" /> : <BookOpenText size={15} />} Сохранить профиль</button></div></div>;
}

function WorkProfileField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder: string; onChange: (value: string) => void }) {
  return <label className="block text-xs font-medium text-slate-300">{label}<textarea className="detail-editor mt-2 min-h-24" value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></label>;
}

function certaintyLabel(certainty: MemoryItem["certainty"]): string { return certainty === "CONFIRMED" ? "Подтверждено" : certainty === "INFERRED" ? "Вывод" : "Неопределённо"; }
function certaintyClass(certainty: MemoryItem["certainty"]): string { return certainty === "CONFIRMED" ? "bg-emerald-500/15 text-emerald-300" : certainty === "INFERRED" ? "bg-sky-500/15 text-sky-300" : "bg-amber-500/15 text-amber-300"; }
function formatRubles(value: number): string { return new Intl.NumberFormat("ru-RU").format(value || 0) + " ₽"; }

function AiTab({ tab, analysis, detail, replyDraft, setReplyDraft, userStyle, setUserStyle, weekendPolicy, setWeekendPolicy, afterHoursPolicy, setAfterHoursPolicy, workdayStartHour, setWorkdayStartHour, workdayEndHour, setWorkdayEndHour, notificationsEnabled, setNotificationsEnabled, notificationChat, setNotificationChat, testNotifications, saveStyle }: {
  tab: Tab;
  analysis: Analysis | null;
  detail: ProfileDetail | null;
  replyDraft: string;
  setReplyDraft: (value: string) => void;
  userStyle: string;
  setUserStyle: (value: string) => void;
  weekendPolicy: string;
  setWeekendPolicy: (value: string) => void;
  afterHoursPolicy: string;
  setAfterHoursPolicy: (value: string) => void;
  workdayStartHour: number;
  setWorkdayStartHour: (value: number) => void;
  workdayEndHour: number;
  setWorkdayEndHour: (value: number) => void;
  notificationsEnabled: boolean;
  setNotificationsEnabled: (value: boolean) => void;
  notificationChat: string;
  setNotificationChat: (value: string) => void;
  testNotifications: () => void;
  saveStyle: () => void;
}) {
  if (tab === "profile") {
    const profile = detail?.profile;
    return <dl className="profile-grid">
      <ProfileLine title="Договоренности" value={profile?.agreements} />
      <ProfileLine title="Оплата" value={profile?.payment_promises} />
      <ProfileLine title="Спорные моменты" value={profile?.disputed_points} />
      <ProfileLine title="Паттерны" value={profile?.behavior_patterns} />
      <ProfileLine title="Стиль клиента" value={profile?.communication_style} />
      <ProfileLine title="Рекомендации" value={profile?.tone_recommendations} />
    </dl>;
  }
  if (tab === "style") {
    return <div className="flex h-full flex-col gap-4">
      <label className="text-xs font-semibold uppercase text-slate-400">Ваш голос и лексика</label>
      <textarea className="detail-editor min-h-[150px]" value={userStyle} onChange={(event) => setUserStyle(event.target.value)} placeholder="Ваш голос, лексика и типичные формулировки" />
      <label className="text-xs font-semibold uppercase text-amber-300">Правило работы в выходные</label>
      <textarea className="detail-editor min-h-[120px]" value={weekendPolicy} onChange={(event) => setWeekendPolicy(event.target.value)} placeholder="Например: в выходные не работаю; срочные задачи — по повышенному тарифу" />
      <div className="flex items-center justify-between gap-3">
        <label className="text-xs font-semibold uppercase text-amber-300">Правило вне рабочего времени</label>
        <div className="flex items-center gap-2"><label className="settings-hour">с <input type="number" min="0" max="23" value={workdayStartHour} onChange={(event) => setWorkdayStartHour(Math.max(0, Math.min(23, Number(event.target.value))))} />:00</label><label className="settings-hour">до <input type="number" min="0" max="23" value={workdayEndHour} onChange={(event) => setWorkdayEndHour(Math.max(0, Math.min(23, Number(event.target.value))))} />:00</label></div>
      </div>
      <textarea className="detail-editor min-h-[120px] flex-1" value={afterHoursPolicy} onChange={(event) => setAfterHoursPolicy(event.target.value)} placeholder="Например: вне 09:00–19:00 отвечаю в следующий рабочий период; срочно — повышенный тариф" />
      <div className="border-t border-line pt-4">
        <label className="flex items-center gap-2 text-xs font-semibold uppercase text-emerald-300"><input type="checkbox" checked={notificationsEnabled} onChange={(event) => setNotificationsEnabled(event.target.checked)} /> Уведомления автопилота в Telegram</label>
        <input className="settings-input mt-3" value={notificationChat} onChange={(event) => setNotificationChat(event.target.value)} placeholder="Ссылка на общую Telegram-группу" />
      </div>
      <div className="flex justify-end gap-2"><button className="secondary-button" onClick={testNotifications} disabled={!notificationChat.trim()}><Bot size={15} /> Проверить группу</button><button className="secondary-button" onClick={saveStyle}><Settings2 size={15} /> Сохранить настройки</button></div>
    </div>;
  }
  if (tab === "strategy") {
    return <div className="analysis-text">
      <Section title="Ситуация" value={analysis?.situation_summary} />
      <Section title="Намерение" value={analysis?.client_intent} />
      <Section title="Риск" value={analysis?.risk} />
      <Section title="Стратегия" value={analysis?.recommended_strategy} />
      {!!analysis?.do_not_do?.length && <Section title="Не делать" value={analysis.do_not_do.map((item) => `• ${item}`).join("\n")} />}
    </div>;
  }
  return <textarea className="detail-editor h-full min-h-[260px]" value={replyDraft} onChange={(event) => setReplyDraft(event.target.value)} placeholder="После анализа здесь появится вариант ответа" />;
}

function Section({ title, value }: { title: string; value?: string }) {
  return <section><h3>{title}</h3><p>{value || "Пока нет данных"}</p></section>;
}

function ProfileLine({ title, value }: { title: string; value?: string }) {
  return <div><dt>{title}</dt><dd>{value || "Нет данных"}</dd></div>;
}

function EmptyContacts({ onImport }: { onImport: () => void }) {
  return <div className="px-5 py-16 text-center"><PanelRight size={26} className="mx-auto mb-3 text-muted" /><p className="text-sm text-muted">В этом аккаунте пока нет импортированных чатов.</p><button className="mt-4 text-sm font-medium text-accent hover:text-sky-300" onClick={onImport}>Импортировать Telegram-чат</button></div>;
}

function ImportDialog({ account, peer, setPeer, pending, onClose, onSubmit }: { account: Account; peer: string; setPeer: (value: string) => void; pending: boolean; onClose: () => void; onSubmit: () => void }) {
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal" onMouseDown={(event) => event.stopPropagation()}><div className="flex items-center justify-between"><h2>Импорт Telegram-чата</h2><button className="icon-button" onClick={onClose}><X size={18} /></button></div><p>Аккаунт: {account === "work" ? "рабочий" : "личный"}. Укажите `@username`, id или точное имя чата.</p><input autoFocus value={peer} onChange={(event) => setPeer(event.target.value)} onKeyDown={(event) => event.key === "Enter" && onSubmit()} placeholder="@username" /><div className="mt-5 flex justify-end gap-2"><button className="secondary-button" onClick={onClose}>Отмена</button><button className="primary-button" disabled={!peer.trim() || pending} onClick={onSubmit}>{pending && <LoaderCircle size={15} className="animate-spin" />} Импортировать</button></div></section></div>;
}

function ConnectionsDialog({ maxToken, setMaxToken, maxConnected, maxBotName, pending, onClose, onConnect }: { maxToken: string; setMaxToken: (value: string) => void; maxConnected: boolean; maxBotName: string; pending: boolean; onClose: () => void; onConnect: () => void }) {
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal" onMouseDown={(event) => event.stopPropagation()}><div className="flex items-center justify-between"><h2>Подключения</h2><button className="icon-button" onClick={onClose}><X size={18} /></button></div><div className="mt-5"><div className="flex items-center gap-2 text-sm font-semibold"><Bot size={17} className="text-accent" /> MAX {maxConnected ? <span className="text-emerald-300">подключён: {maxBotName}</span> : <span className="text-muted">не подключён</span>}</div><p>Токен хранится локально в защищённом хранилище Windows.</p><input autoFocus className="settings-input" type="password" value={maxToken} onChange={(event) => setMaxToken(event.target.value)} onKeyDown={(event) => event.key === "Enter" && onConnect()} placeholder="Токен бота MAX" /><div className="mt-4 flex justify-end gap-2"><button className="secondary-button" onClick={onClose}>Закрыть</button><button className="primary-button" disabled={!maxToken.trim() || pending} onClick={onConnect}>{pending && <LoaderCircle size={15} className="animate-spin" />} Проверить и подключить</button></div></div></section></div>;
}

function ContextMenu({ menu, onOpenTasks, onToggleKind, onToggleAutopilot, onRefresh, onRebuildProfile, onSyncHistory, onImportHtml, onDelete }: { menu: { profile: Profile; x: number; y: number }; onOpenTasks: () => void; onToggleKind: () => void; onToggleAutopilot: () => void; onRefresh: () => void; onRebuildProfile: () => void; onSyncHistory: () => void; onImportHtml: () => void; onDelete: () => void }) {
  return <div className="context-menu" style={{ left: menu.x, top: menu.y }} onClick={(event) => event.stopPropagation()}><button onClick={onOpenTasks}><Briefcase size={15} /> Открыть задачи</button><button onClick={onToggleKind}><Briefcase size={15} /> {menu.profile.contact_kind === "business" ? "Сделать личным" : "Пометить рабочим"}</button><button onClick={onToggleAutopilot}><RadioTower size={15} /> {menu.profile.autopilot_enabled ? "Не отслеживать" : "Отслеживать (автоответ)"}</button><button onClick={onRefresh}><RefreshCw size={15} /> Получить новые</button><button onClick={onRebuildProfile}><Sparkles size={15} /> Построить AI-профиль</button><button onClick={onSyncHistory}><RefreshCw size={15} /> Экспорт истории и фото</button><button onClick={onImportHtml}><Clipboard size={15} /> Импортировать HTML-архив</button><button className="danger" onClick={onDelete}><Trash2 size={15} /> Удалить</button></div>;
}

function ProfileTasksDialog({ profile, tasks, onClose, onMarkCompleted }: { profile: Profile; tasks: CrmTaskPreview[]; onClose: () => void; onMarkCompleted: (projectId: number) => void }) {
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal max-w-md" onMouseDown={(event) => event.stopPropagation()}>
    <div className="flex items-center justify-between gap-4"><div><h2>Задачи: {profile.chat_name}</h2><p>Последние три записи из CRM.</p></div><button className="icon-button" title="Закрыть" onClick={onClose}><X size={18} /></button></div>
    <div className="mt-4 space-y-2">{tasks.map((task) => { const completed = task.status === "completed_by_me"; return <label key={task.id} className={`flex cursor-pointer items-start gap-3 rounded-md border p-3 ${completed ? "border-emerald-400/40 bg-emerald-400/10" : "border-line bg-[#0a141e]"}`}><input className="mt-1 h-4 w-4 shrink-0 accent-emerald-400" type="checkbox" checked={completed} disabled={completed || task.status === "done"} onChange={() => onMarkCompleted(task.id)} /><span className="min-w-0 flex-1"><strong className="block text-sm text-slate-100">{task.title}</strong><small className="mt-1 block text-xs text-muted">{completed ? "Сделал" : task.status === "done" ? "Готово" : task.deadline ? `Срок: ${task.deadline}` : "Без срока"}</small>{task.notes && <span className="mt-1 block line-clamp-2 text-xs text-slate-400">{task.notes}</span>}</span></label>; })}</div>
    {!tasks.length && <p className="py-8 text-center text-sm text-muted">У этого контакта пока нет задач в CRM.</p>}
  </section></div>;
}

function replyForTab(analysis: Analysis | null, tab: Tab): string {
  if (!analysis) return "";
  if (tab === "soft") return analysis.soft_reply || analysis.best_reply;
  if (tab === "hard") return analysis.hard_reply || analysis.best_reply;
  return analysis.best_reply;
}

function mergeMessageHistory(...groups: Message[][]): Message[] {
  const messages = new Map<string, Message>();
  for (const message of groups.flat()) {
    const key = message.telegram_message_id === null
      ? `${message.message_date}:${message.sender}:${message.text}`
      : String(message.telegram_message_id);
    messages.set(key, message);
  }
  return [...messages.values()].sort((left, right) => {
    const leftId = left.telegram_message_id ?? Number.MAX_SAFE_INTEGER;
    const rightId = right.telegram_message_id ?? Number.MAX_SAFE_INTEGER;
    return leftId - rightId;
  });
}

function formatTime(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "" : parsed.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function formatListTimestamp(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const today = new Date();
  return dayKey(parsed) === dayKey(today)
    ? parsed.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
    : parsed.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
}

function isFirstMessageOfDay(message: Message, previous?: Message): boolean {
  if (!previous) return true;
  return dayKey(new Date(message.message_date)) !== dayKey(new Date(previous.message_date));
}

function formatDateLabel(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Дата неизвестна";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (dayKey(parsed) === dayKey(today)) return "Сегодня";
  if (dayKey(parsed) === dayKey(yesterday)) return "Вчера";
  return parsed.toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long", year: parsed.getFullYear() === today.getFullYear() ? undefined : "numeric" });
}

function dayKey(value: Date): string {
  if (Number.isNaN(value.getTime())) return "";
  return `${value.getFullYear()}-${value.getMonth()}-${value.getDate()}`;
}

function outsideHoursLabel(value: string, workdayStartHour: number, workdayEndHour: number): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  if (parsed.getHours() < workdayStartHour) return `до ${String(workdayStartHour).padStart(2, "0")}:00`;
  if (parsed.getHours() >= workdayEndHour) return `после ${String(workdayEndHour).padStart(2, "0")}:00`;
  return "";
}

function messageFrom(error: unknown): string {
  return error instanceof Error ? error.message : "Неизвестная ошибка.";
}

function autopilotStatusText(detail: ProfileDetail | null): string {
  if (!detail?.autopilot_enabled) return "Сообщения поступают в реальном времени";
  const delay = detail.autopilot_delay_seconds;
  const labels: Record<string, string> = {
    watching: `Автопилот включён, ждёт новые сообщения (${delay} сек)`,
    waiting: `Автопилот ждёт паузу ${delay} сек`,
    thinking: "Автопилот готовит ответ",
    sending: "Автопилот отправляет ответ",
    error: "Ошибка автопилота, повторит попытку",
  };
  return labels[detail.autopilot_status] || `Автопилот включён (${delay} сек)`;
}

function normalizedAutopilotDelay(value: string): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.max(1, Math.min(1800, parsed)) : 45;
}

function savedLayout(): Record<string, number> {
  try {
    const saved = JSON.parse(localStorage.getItem("telegram-advisor-layout") || "{}") as Record<string, number>;
    return Object.keys(saved).length ? saved : { contacts: 24, chat: 46, assistant: 30 };
  } catch {
    return { contacts: 24, chat: 46, assistant: 30 };
  }
}
