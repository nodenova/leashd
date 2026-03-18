/* leashd WebUI — vanilla JS single-page application */
"use strict";

// ============================================================
// State
// ============================================================
const state = {
  ws: null,
  apiKey: "",
  chatId: "",
  sessionId: "",
  connected: false,
  streamingMessages: {},   // message_id -> DOM element
  pingInterval: null,
  reconnectTimeout: null,
  reconnectDelay: 1000,
  lastPongTime: 0,
  pongCheckInterval: null,
  sendDebounceTimer: null,
  pendingModals: [],       // queued modals when one is already open
  activeModalType: null,   // "question" | "plan_review" | "interrupt" | null
  previousFocus: null,     // element to restore focus to after modal closes
  commandPaletteIndex: -1, // highlighted item in command palette
  queuedBannerMsgId: null,  // server message_id for the queued-message banner
  currentAgent: null,      // name of the active agent group (null = main agent)
  agentGroupEl: null,      // current .agent-group DOM container
  sidebarRefreshTimer: null, // pending sidebar refresh after new message
  messageCountInSession: 0,  // number of messages sent in current session
};

/**
 * Authenticated fetch — sends API key via X-API-Key header instead of query params.
 */
function authFetch(url, opts = {}) {
  const headers = { ...opts.headers };
  if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  return fetch(url, { ...opts, headers });
}

// ============================================================
// Slash Commands
// ============================================================
const SLASH_COMMANDS = [
  { command: "/plan", description: "Switch to plan mode" },
  { command: "/edit", description: "Auto-approve file writes" },
  { command: "/default", description: "Return to default mode" },
  { command: "/test", description: "Activate test workflow" },
  { command: "/web", description: "Web automation" },
  { command: "/task", description: "Submit autonomous task" },
  { command: "/dir", description: "Switch working directory" },
  { command: "/workspace", description: "Activate workspace" },
  { command: "/ws", description: "Workspace (alias)" },
  { command: "/git", description: "Git operations" },
  { command: "/cancel", description: "Cancel current task" },
  { command: "/stop", description: "Stop all work" },
  { command: "/tasks", description: "List active tasks" },
  { command: "/status", description: "Session status" },
  { command: "/clear", description: "Clear session" },
];

// ============================================================
// SVG Icons
// ============================================================
const ICON_USER = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0zm4 8c0 1-1 1-1 1H3s-1 0-1-1 1-4 6-4 6 3 6 4zm-1-.004c-.001-.246-.154-.986-.832-1.664C11.516 10.68 10.289 10 8 10c-2.29 0-3.516.68-4.168 1.332-.678.678-.83 1.418-.832 1.664h10z"/></svg>';
const ICON_BOT = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M6 12.5a.5.5 0 0 1 .5-.5h3a.5.5 0 0 1 0 1h-3a.5.5 0 0 1-.5-.5zM3 8.062C3 6.76 4.235 5.765 5.53 5.886a26.58 26.58 0 0 0 4.94 0C11.765 5.765 13 6.76 13 8.062v1.157a.933.933 0 0 1-.765.935c-.845.147-2.34.346-4.235.346-1.895 0-3.39-.2-4.235-.346A.933.933 0 0 1 3 9.219V8.062zm4.542-.827a.25.25 0 0 0-.217.068l-.92.9a24.767 24.767 0 0 1-1.871-.183.25.25 0 0 0-.068.495c.55.076 1.232.149 2.02.193a.25.25 0 0 0 .189-.071l.754-.736.847 1.71a.25.25 0 0 0 .404.062l.932-.97a25.286 25.286 0 0 0 1.922-.188.25.25 0 0 0-.068-.495c-.538.074-1.207.145-1.98.189a.25.25 0 0 0-.166.076l-.754.785-.842-1.7a.25.25 0 0 0-.182-.135z"/><path d="M8.5 1.866a1 1 0 1 0-1 0V3h-2A4.5 4.5 0 0 0 1 7.5V8a1 1 0 0 0-1 1v2a1 1 0 0 0 1 1v1a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-1a1 1 0 0 0 1-1V9a1 1 0 0 0-1-1v-.5A4.5 4.5 0 0 0 10.5 3h-2V1.866zM14 7.5V13a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V7.5A3.5 3.5 0 0 1 5.5 4h5A3.5 3.5 0 0 1 14 7.5z"/></svg>';
const ICON_INFO = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14zm0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16z"/><path d="m8.93 6.588-2.29.287-.082.38.45.083c.294.07.352.176.288.469l-.738 3.468c-.194.897.105 1.319.808 1.319.545 0 1.178-.252 1.465-.598l.088-.416c-.2.176-.492.246-.686.246-.275 0-.375-.193-.304-.533L8.93 6.588zM9 4.5a1 1 0 1 1-2 0 1 1 0 0 1 2 0z"/></svg>';
const ICON_FOLDER = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M.54 3.87.5 3a2 2 0 0 1 2-2h3.672a2 2 0 0 1 1.414.586l.828.828A2 2 0 0 0 9.828 3H13.5a2 2 0 0 1 2 2v.5H.54zM1.059 5.5H15a1 1 0 0 1 .998 1.06l-.5 7A1 1 0 0 1 14.5 14.5h-13a1 1 0 0 1-.998-.94l-.5-7A1 1 0 0 1 1.059 5.5z"/></svg>';
const ICON_FOLDER_OPEN = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1 3.5A1.5 1.5 0 0 1 2.5 2h2.764c.958 0 1.76.56 2.311 1.184C7.985 3.648 8.48 4 9 4h4.5A1.5 1.5 0 0 1 15 5.5v.64c.57.265.94.876.856 1.546l-.64 5.124A2.5 2.5 0 0 1 12.733 15H3.266a2.5 2.5 0 0 1-2.481-2.19l-.64-5.124A1.5 1.5 0 0 1 1 6.14V3.5zM2 6h12v-.5a.5.5 0 0 0-.5-.5H9c-.964 0-1.71-.629-2.174-1.154C6.374 3.334 5.82 3 5.264 3H2.5a.5.5 0 0 0-.5.5V6zm-.367 1a.5.5 0 0 0-.496.562l.64 5.124A1.5 1.5 0 0 0 3.266 14h9.468a1.5 1.5 0 0 0 1.489-1.314l.64-5.124A.5.5 0 0 0 14.367 7H1.633z"/></svg>';
const ICON_WORKSPACE = '<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h3A1.5 1.5 0 0 1 7 2.5v3A1.5 1.5 0 0 1 5.5 7h-3A1.5 1.5 0 0 1 1 5.5v-3zm8 0A1.5 1.5 0 0 1 10.5 1h3A1.5 1.5 0 0 1 15 2.5v3A1.5 1.5 0 0 1 13.5 7h-3A1.5 1.5 0 0 1 9 5.5v-3zm-8 8A1.5 1.5 0 0 1 2.5 9h3A1.5 1.5 0 0 1 7 10.5v3A1.5 1.5 0 0 1 5.5 15h-3A1.5 1.5 0 0 1 1 13.5v-3zm8 0A1.5 1.5 0 0 1 10.5 9h3a1.5 1.5 0 0 1 1.5 1.5v3a1.5 1.5 0 0 1-1.5 1.5h-3A1.5 1.5 0 0 1 9 13.5v-3z"/></svg>';
const ICON_SHIELD = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M5.338 1.59a61.44 61.44 0 0 0-2.837.856.481.481 0 0 0-.328.39c-.554 4.157.726 7.19 2.253 9.188a10.725 10.725 0 0 0 2.287 2.233c.346.244.652.42.893.533.12.057.218.095.293.118a.55.55 0 0 0 .101.025.615.615 0 0 0 .1-.025c.076-.023.174-.061.294-.118.24-.113.547-.29.893-.533a10.726 10.726 0 0 0 2.287-2.233c1.527-1.997 2.807-5.031 2.253-9.188a.48.48 0 0 0-.328-.39c-.651-.213-1.75-.56-2.837-.855C9.552 1.29 8.531 1.067 8 1.067c-.53 0-1.552.223-2.662.524zM5.072.56C6.157.265 7.31 0 8 0s1.843.265 2.928.56c1.11.3 2.229.655 2.887.87a1.54 1.54 0 0 1 1.044 1.262c.596 4.477-.787 7.795-2.465 9.99a11.775 11.775 0 0 1-2.517 2.453 7.159 7.159 0 0 1-1.048.625c-.28.132-.581.24-.877.24s-.597-.108-.877-.24a7.158 7.158 0 0 1-1.048-.625 11.777 11.777 0 0 1-2.517-2.453C1.928 10.487.545 7.169 1.141 2.692A1.54 1.54 0 0 1 2.185 1.43 62.456 62.456 0 0 1 5.072.56z"/><path d="M8 4.5a.5.5 0 0 1 .5.5v2a.5.5 0 0 1-1 0V5a.5.5 0 0 1 .5-.5zM8 9a.5.5 0 1 1 0-1 .5.5 0 0 1 0 1z"/></svg>';
const ICON_TIMER = '<svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M6.5 0a.5.5 0 0 0 0 1H7v1.07A7.001 7.001 0 0 0 8 16 7 7 0 0 0 9 2.07V1h.5a.5.5 0 0 0 0-1h-3zm2 5.6a.5.5 0 1 0-1 0v2.9h-3a.5.5 0 0 0 0 1H8a.5.5 0 0 0 .5-.5V5.6z"/></svg>';

// ============================================================
// Markdown Configuration (marked.js + DOMPurify)
// ============================================================
if (typeof marked !== "undefined") {
  const renderer = new marked.Renderer();
  renderer.link = function ({ href, text }) {
    return `<a href="${href}" target="_blank" rel="noopener">${text}</a>`;
  };
  marked.setOptions({
    renderer,
    gfm: true,
    breaks: true,
    headerIds: false,
  });
}

const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "br", "hr",
    "strong", "em", "del", "s",
    "a", "code", "pre",
    "ul", "ol", "li",
    "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
    "input",
    "img",
    "span", "div",
    "sub", "sup",
  ],
  ALLOWED_ATTR: [
    "href", "target", "rel",
    "class",
    "type", "checked", "disabled",
    "src", "alt", "title", "width", "height",
  ],
  ALLOW_DATA_ATTR: false,
};

// ============================================================
// DOM refs
// ============================================================
const $ = (sel) => document.querySelector(sel);
const authScreen = $("#auth-screen");
const chatScreen = $("#chat-screen");
const settingsScreen = $("#settings-screen");
const authForm = $("#auth-form");
const apiKeyInput = $("#api-key-input");
const authBtn = $("#auth-btn");
const authError = $("#auth-error");
const messagesEl = $("#messages");
const messageInput = $("#message-input");
const sendBtn = $("#send-btn");
const uploadBtn = $("#upload-btn");
const fileInput = $("#file-input");
const attachmentPreview = $("#attachment-preview");
const connectionDot = $("#connection-dot");
const sidebar = $("#sidebar");
const sidebarBody = $("#sidebar-body");
const sidebarOverlay = $("#sidebar-overlay");
const sidebarToggle = $("#sidebar-toggle");
const sidebarClose = $(".sidebar-close");
const sidebarWorkingDir = $("#sidebar-working-dir");
const emptyState = $("#empty-state");
const modalOverlay = $("#modal-overlay");
const modalContent = $("#modal-content");
const queuedBanner = $("#queued-banner");
const themeToggleBtn = $("#theme-toggle-btn");
const settingsBtn = $("#settings-btn");
const settingsBackBtn = $("#settings-back-btn");
const settingsBody = $("#settings-body");
const saveBar = $("#save-bar");
const saveBtn = $("#save-btn");
const toastEl = $("#toast");

// ============================================================
// Sidebar Manager
// ============================================================
const SidebarManager = {
  open() {
    sidebar.classList.add("open");
    sidebarOverlay.classList.add("visible");
    document.body.style.overflow = "hidden";
  },

  close() {
    sidebar.classList.remove("open");
    sidebarOverlay.classList.remove("visible");
    document.body.style.overflow = "";
  },

  toggle() {
    if (sidebar.classList.contains("open")) {
      this.close();
    } else {
      this.open();
    }
  },

  isDesktop() {
    return window.matchMedia("(min-width: 1024px)").matches;
  },
};

sidebarToggle.addEventListener("click", () => SidebarManager.toggle());
sidebarClose.addEventListener("click", () => SidebarManager.close());
sidebarOverlay.addEventListener("click", () => SidebarManager.close());

window.addEventListener("resize", () => {
  if (SidebarManager.isDesktop()) {
    SidebarManager.close();
  }
});

// ============================================================
// Theme Manager
// ============================================================
const ThemeManager = {
  _mediaQuery: window.matchMedia("(prefers-color-scheme: dark)"),

  init() {
    const saved = localStorage.getItem("leashd_theme") || "auto";
    this.apply(saved);
    this._mediaQuery.addEventListener("change", () => {
      if (this.current() === "auto") {
        this._updateIcon("auto");
        this._updateHljsTheme("auto");
      }
    });
  },

  apply(theme) {
    const html = document.documentElement;
    html.setAttribute("data-theme", theme);
    localStorage.setItem("leashd_theme", theme);
    this._updateIcon(theme);
    this._updateHljsTheme(theme);
  },

  toggle() {
    const order = ["auto", "dark", "light"];
    const idx = order.indexOf(this.current());
    const next = order[(idx + 1) % order.length];
    this.apply(next);
  },

  current() {
    return document.documentElement.getAttribute("data-theme") || "auto";
  },

  _updateIcon(theme) {
    const auto = $("#theme-icon-auto");
    const dark = $("#theme-icon-dark");
    const light = $("#theme-icon-light");
    if (!auto || !dark || !light) return;
    auto.hidden = theme !== "auto";
    dark.hidden = theme !== "dark";
    light.hidden = theme !== "light";
    themeToggleBtn.dataset.tooltip = `Theme: ${theme}`;
  },

  _updateHljsTheme(theme) {
    const darkSheet = $("#hljs-theme-dark");
    const lightSheet = $("#hljs-theme-light");
    if (!darkSheet || !lightSheet) return;
    let wantLight = theme === "light";
    if (theme === "auto") {
      wantLight = !this._mediaQuery.matches;
    }
    darkSheet.disabled = wantLight;
    lightSheet.disabled = !wantLight;
  },
};

// ============================================================
// Empty State
// ============================================================
function hideEmptyState() {
  if (emptyState) emptyState.hidden = true;
}

function showEmptyState() {
  if (emptyState) emptyState.hidden = false;
}

function updateEmptyState() {
  if (messagesEl.children.length > 0) {
    hideEmptyState();
  } else {
    showEmptyState();
  }
}

// ============================================================
// Tab Manager
// ============================================================
const TabManager = {
  tabs: [],
  activeTab: null,
  activeSessionId: null,
  tabStates: {},
  conversations: {},
  expandedTabs: new Set(),
  _pendingWorkspace: null,
  _pendingDir: null,
  _optimisticEntries: {},

  async init() {
    try {
      const res = await authFetch("/api/tabs");
      if (!res.ok) return;
      const data = await res.json();
      if (data.error) return;
      this._buildTabs(data);
    } catch { /* ignore */ }
  },

  _buildTabs(data) {
    const dirs = data.directories || [];
    const workspaces = data.workspaces || [];

    if (dirs.length <= 1 && workspaces.length === 0) {
      if (dirs.length === 1) {
        sidebarWorkingDir.textContent = dirs[0].path;
        sidebarWorkingDir.title = dirs[0].path;
      }
      return;
    }

    this.tabs = [];
    sidebarBody.innerHTML = "";

    if (dirs.length > 0) {
      const label = document.createElement("div");
      label.className = "sidebar-section-label";
      label.textContent = "Directories";
      sidebarBody.appendChild(label);

      for (const d of dirs) {
        const tab = { id: `dir:${d.name}`, label: d.name, path: d.path, type: "dir" };
        this.tabs.push(tab);
        this._createFolderButton(tab);
      }
    }

    if (workspaces.length > 0) {
      const label = document.createElement("div");
      label.className = "sidebar-section-label";
      label.textContent = "Workspaces";
      sidebarBody.appendChild(label);

      for (const ws of workspaces) {
        const dirs = ws.directories || [];
        const tab = {
          id: `ws:${ws.name}`,
          label: ws.name,
          type: "ws",
          description: ws.description,
          directories: dirs,
          path: dirs[0] || "",
        };
        this.tabs.push(tab);
        this._createWorkspaceButton(tab);
      }
    }

    // Restore: URL hash → localStorage → default
    const route = Router.init();
    if (route && route.tabId && this.tabs.find(t => t.id === route.tabId)) {
      this.expandedTabs.add(route.tabId);
      this._renderConversationList(route.tabId);
      this.selectConversation(route.tabId, route.sessionId);
      this._fetchConversations(route.tabId);
    } else {
      const savedSession = localStorage.getItem("leashd_active_session");
      const savedTab = localStorage.getItem("leashd_active_tab");
      if (savedTab && savedSession && this.tabs.find(t => t.id === savedTab)) {
        this.expandedTabs.add(savedTab);
        this._renderConversationList(savedTab);
        this.selectConversation(savedTab, savedSession);
        this._fetchConversations(savedTab);
      } else if (this.tabs[0]) {
        this.startNewConversation(this.tabs[0].id, this.tabs[0].path || "");
      }
    }
  },

  _createFolderButton(tab) {
    const btn = document.createElement("button");
    btn.className = "sidebar-item";
    btn.innerHTML = `<span class="folder-icon">${ICON_FOLDER}</span> <span>${escapeHtml(tab.label)}</span><span class="chevron">&#9656;</span>`;
    btn.title = tab.path;
    btn.dataset.tabId = tab.id;
    btn.onclick = () => this.toggleFolder(tab.id);

    const listContainer = document.createElement("div");
    listContainer.className = "conversation-list";
    listContainer.dataset.tabId = tab.id;
    listContainer.hidden = true;

    sidebarBody.appendChild(btn);
    sidebarBody.appendChild(listContainer);
  },

  _createWorkspaceButton(tab) {
    const dirs = tab.directories || [];
    const dirNames = dirs.map(d => {
      const parts = d.replace(/\\/g, "/").split("/").filter(Boolean);
      return parts[parts.length - 1] || d;
    });
    const dirSummary = dirNames.length === 0 ? "" :
      dirNames.length <= 2 ? dirNames.join(" · ") :
      `${dirNames.slice(0, 2).join(" · ")} +${dirNames.length - 2}`;

    const btn = document.createElement("button");
    btn.className = "sidebar-item ws-button";
    btn.innerHTML = `${ICON_WORKSPACE}<span class="ws-info"><span class="ws-name">${escapeHtml(tab.label)}</span>${dirSummary ? `<span class="ws-dirs">${escapeHtml(dirSummary)}</span>` : ""}</span><span class="chevron">&#9656;</span>`;
    btn.title = tab.description
      ? `${tab.description}\n\n${dirs.join("\n")}`.trim()
      : dirs.join("\n") || tab.label;
    btn.dataset.tabId = tab.id;
    btn.onclick = () => this.toggleFolder(tab.id);

    const listContainer = document.createElement("div");
    listContainer.className = "conversation-list";
    listContainer.dataset.tabId = tab.id;
    listContainer.hidden = true;

    sidebarBody.appendChild(btn);
    sidebarBody.appendChild(listContainer);
  },

  toggleFolder(tabId) {
    if (this.expandedTabs.has(tabId)) {
      this.expandedTabs.delete(tabId);
      this._collapseFolder(tabId);
    } else {
      this.expandedTabs.add(tabId);
      this._expandFolder(tabId);
    }
  },

  _expandFolder(tabId) {
    const btn = sidebarBody.querySelector(`.sidebar-item[data-tab-id="${tabId}"]`);
    if (btn) {
      btn.classList.add("expanded");
      const iconEl = btn.querySelector(".folder-icon");
      if (iconEl) iconEl.innerHTML = ICON_FOLDER_OPEN;
    }
    const list = sidebarBody.querySelector(`.conversation-list[data-tab-id="${tabId}"]`);
    if (list) list.hidden = false;
    this._fetchConversations(tabId);
  },

  _collapseFolder(tabId) {
    const btn = sidebarBody.querySelector(`.sidebar-item[data-tab-id="${tabId}"]`);
    if (btn) {
      btn.classList.remove("expanded");
      const iconEl = btn.querySelector(".folder-icon");
      if (iconEl) iconEl.innerHTML = ICON_FOLDER;
    }
    const list = sidebarBody.querySelector(`.conversation-list[data-tab-id="${tabId}"]`);
    if (list) list.hidden = true;
  },

  async _fetchConversations(tabId) {
    const tab = this.tabs.find(t => t.id === tabId);
    if (!tab) return;
    const url = tab.type === "ws"
      ? `/api/sessions?workspace=${encodeURIComponent(tab.label)}`
      : tab.path
        ? `/api/sessions?path=${encodeURIComponent(tab.path)}`
        : null;
    if (!url) return;
    try {
      const res = await authFetch(url);
      if (!res.ok) return;
      const data = await res.json();
      this.conversations[tabId] = data.sessions || [];
      const opt = this._optimisticEntries[tabId] || {};
      for (const [sid, entry] of Object.entries(opt)) {
        if (this.conversations[tabId].some(c => c.session_id === sid)) {
          delete opt[sid];
        } else {
          this.conversations[tabId].unshift(entry);
        }
      }
      this._renderConversationList(tabId);
    } catch { /* ignore */ }
  },

  _renderConversationList(tabId) {
    const list = sidebarBody.querySelector(`.conversation-list[data-tab-id="${tabId}"]`);
    if (!list) return;
    list.innerHTML = "";

    const tab = this.tabs.find(t => t.id === tabId);
    const newBtn = document.createElement("div");
    newBtn.className = "new-conversation-btn";
    newBtn.textContent = "+ New conversation";
    newBtn.onclick = () => this.startNewConversation(tabId, tab?.path || "");
    list.appendChild(newBtn);

    const convos = (this.conversations[tabId] || []).slice();
    if (this.activeTab === tabId && this.activeSessionId
        && !convos.some(c => c.session_id === this.activeSessionId)) {
      convos.unshift({
        session_id: this.activeSessionId,
        preview: "",
        last_used: new Date().toISOString(),
      });
    }
    for (const c of convos) {
      const item = document.createElement("div");
      item.className = "conversation-item";
      if (c.session_id === this.activeSessionId) item.classList.add("active");
      item.dataset.sessionId = c.session_id;

      const preview = document.createElement("span");
      preview.className = "conversation-preview";
      preview.textContent = c.preview
        ? (c.preview.length > 50 ? c.preview.slice(0, 50) + "..." : c.preview)
        : "New conversation";

      const meta = document.createElement("span");
      meta.className = "conversation-meta";
      meta.textContent = this._formatRelativeDate(c.last_used);

      item.appendChild(preview);
      item.appendChild(meta);
      item.onclick = () => this.selectConversation(tabId, c.session_id);
      list.appendChild(item);
    }
  },

  _formatRelativeDate(isoStr) {
    if (!isoStr) return "";
    const date = new Date(isoStr);
    const now = new Date();
    const diff = now - date;
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return "now";
    if (mins < 60) return `${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 7) return `${days}d`;
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  },

  selectConversation(tabId, sessionId) {
    // Capture previous state before updating
    const prevTab = this.activeTab;
    const prevSession = this.activeSessionId;
    const prevMessageCount = state.messageCountInSession;

    // Update active state FIRST so any renders use correct values
    this.activeTab = tabId;
    this.activeSessionId = sessionId;
    state.sessionId = sessionId;

    // Clean up empty optimistic conversations when switching away
    if (prevSession && prevTab) {
      const opt = this._optimisticEntries[prevTab];
      if (opt?.[prevSession] && prevMessageCount === 0) {
        delete opt[prevSession];
        const convArr = this.conversations[prevTab];
        if (convArr) {
          const idx = convArr.findIndex(c => c.session_id === prevSession);
          if (idx !== -1) convArr.splice(idx, 1);
        }
        delete this.tabStates[prevSession];
        this._renderConversationList(prevTab);
      }
    }

    // Save previous session's state (messages, scroll, draft)
    if (prevSession) {
      this.tabStates[prevSession] = {
        scrollPosition: messagesEl.scrollTop,
        html: messagesEl.innerHTML,
        streamingMessages: {},
        draft: messageInput.value,
        draftAttachments: pendingAttachments.slice(),
      };
    }

    localStorage.setItem("leashd_active_tab", tabId);
    localStorage.setItem("leashd_active_session", sessionId);
    if (!Router._suppressPush) Router.navigate(tabId, sessionId);

    // Update folder button active state and expand active folder
    for (const btn of sidebarBody.querySelectorAll(".sidebar-item")) {
      btn.classList.toggle("active", btn.dataset.tabId === tabId);
    }
    if (!this.expandedTabs.has(tabId)) {
      this.expandedTabs.add(tabId);
      this._expandFolder(tabId);
    }

    // Update conversation item highlights
    for (const item of sidebarBody.querySelectorAll(".conversation-item")) {
      item.classList.toggle("active", item.dataset.sessionId === sessionId);
    }

    // Update working dir in sidebar footer
    const tab = this.tabs.find(t => t.id === tabId);
    if (tab && tab.path) {
      sidebarWorkingDir.textContent = tab.path;
      sidebarWorkingDir.title = tab.path;
    }

    // Restore cached state or load fresh
    const cached = this.tabStates[sessionId];
    if (cached) {
      messagesEl.innerHTML = cached.html;
      messagesEl.scrollTop = cached.scrollPosition;
    } else {
      messagesEl.innerHTML = "";
    }

    // Restore draft text and attachments, or clear for a fresh conversation
    if (cached && cached.draft) {
      messageInput.value = cached.draft;
      messageInput.style.height = "auto";
      messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
    } else {
      messageInput.value = "";
      messageInput.style.height = "auto";
    }
    pendingAttachments.length = 0;
    if (cached && cached.draftAttachments && cached.draftAttachments.length > 0) {
      pendingAttachments.push(...cached.draftAttachments);
    }
    renderAttachmentPreviews();

    state.streamingMessages = {};
    state.messageCountInSession = 0;
    updateEmptyState();
    if (!SidebarManager.isDesktop()) SidebarManager.close();
    this._reconnect();
  },

  startNewConversation(tabId, path) {
    const sessionId = tabId + ":" + crypto.randomUUID().slice(0, 8);
    const tab = this.tabs.find(t => t.id === tabId);

    // Auto-activate workspace/directory after the WebSocket reconnects
    if (tab?.type === "ws") {
      this._pendingWorkspace = tab.label;
    } else if (tab?.type === "dir") {
      this._pendingDir = tab.label;
    }

    // Optimistic sidebar entry
    const entry = {
      session_id: sessionId,
      chat_id: `web:${sessionId}`,
      preview: "",
      last_used: new Date().toISOString(),
    };
    if (!this._optimisticEntries[tabId]) this._optimisticEntries[tabId] = {};
    this._optimisticEntries[tabId][sessionId] = entry;
    if (!this.conversations[tabId]) this.conversations[tabId] = [];
    this.conversations[tabId].unshift(entry);

    // Ensure folder is expanded
    if (!this.expandedTabs.has(tabId)) {
      this.expandedTabs.add(tabId);
      this._expandFolder(tabId);
    }

    this._renderConversationList(tabId);
    this.selectConversation(tabId, sessionId);
    if (!SidebarManager.isDesktop()) SidebarManager.close();
  },

  _reconnect() {
    if (!state.apiKey) return;
    if (state.ws) {
      state.ws.onclose = null;
      state.ws.close();
    }
    connect();
  },
};

// ============================================================
// Router — hash-based URL routing (#/{tabId}/{sessionId})
// ============================================================
const Router = {
  _suppressPush: false,

  init() {
    window.addEventListener("popstate", () => this._onHashChange());
    return this.parse();
  },

  parse() {
    const hash = location.hash;
    if (!hash || !hash.startsWith("#/")) return null;
    const parts = hash.slice(2).split("/");
    if (parts.length === 1 && parts[0]) {
      return { tabId: null, sessionId: parts[0] };
    }
    if (parts.length < 2) return null;
    const tabId = parts[0];
    const sessionId = parts.slice(1).join("/");
    if (!tabId || !sessionId) return null;
    return { tabId, sessionId };
  },

  navigate(tabId, sessionId) {
    const hash = tabId ? `#/${tabId}/${sessionId}` : `#/${sessionId}`;
    if (location.hash !== hash) {
      history.pushState(null, "", hash);
    }
  },

  _onHashChange() {
    const parsed = this.parse();
    if (!parsed) return;
    const { tabId, sessionId } = parsed;

    // Single-directory mode (no tabs)
    if (!tabId) {
      if (TabManager.tabs.length === 0 && state.sessionId !== sessionId) {
        state.sessionId = sessionId;
        if (state.ws) TabManager._reconnect();
      }
      return;
    }

    if (!TabManager.tabs.find(t => t.id === tabId)) return;
    if (TabManager.activeSessionId === sessionId) return;
    this._suppressPush = true;
    TabManager.selectConversation(tabId, sessionId);
    this._suppressPush = false;
  },
};

// ============================================================
// Auth
// ============================================================
authForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const key = apiKeyInput.value.trim();
  if (!key) return;
  state.apiKey = key;
  authBtn.disabled = true;
  authError.hidden = true;
  connect();
});

function showAuthError(msg) {
  authError.textContent = msg;
  authError.hidden = false;
  authBtn.disabled = false;
}

// ============================================================
// WebSocket Connection
// ============================================================
function getWsUrl() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/ws`;
}

function connect() {
  if (state.ws) {
    state.ws.onclose = null;
    state.ws.close();
  }

  const ws = new WebSocket(getWsUrl());
  state.ws = ws;

  ws.onopen = () => {
    // Send auth message
    ws.send(JSON.stringify({
      type: "auth",
      payload: {
        api_key: state.apiKey,
        session_id: state.sessionId || undefined,
      },
    }));
  };

  ws.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); }
    catch { return; }
    handleServerMessage(msg);
  };

  ws.onclose = () => {
    setConnected(false);
    if (state.connected) {
      scheduleReconnect();
    }
  };

  ws.onerror = () => {
    // onclose will fire after this
  };
}

function scheduleReconnect() {
  if (state.reconnectTimeout) return;
  const jitter = state.reconnectDelay * (0.5 + Math.random());
  state.reconnectTimeout = setTimeout(() => {
    state.reconnectTimeout = null;
    connect();
  }, jitter);
  state.reconnectDelay = Math.min(state.reconnectDelay * 2, 30000);
}

function setConnected(val) {
  state.connected = val;
  connectionDot.className = val ? "dot dot-connected" : "dot dot-disconnected";
  connectionDot.title = val ? "Connected" : "Disconnected";
  sendBtn.disabled = !val || !(messageInput.value.trim() || (typeof pendingAttachments !== "undefined" && pendingAttachments.length > 0));
}

function startPing() {
  clearInterval(state.pingInterval);
  clearInterval(state.pongCheckInterval);
  state.lastPongTime = Date.now();

  state.pingInterval = setInterval(() => {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify({ type: "ping", payload: {} }));
    }
  }, 25000);

  state.pongCheckInterval = setInterval(() => {
    if (!state.connected) return;
    const elapsed = Date.now() - state.lastPongTime;
    if (elapsed > 50000) {
      connectionDot.className = "dot dot-unstable";
      connectionDot.title = "Connection unstable";
    }
  }, 10000);
}

function wsSend(type, payload) {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send(JSON.stringify({ type, payload }));
  }
}

// ============================================================
// Server Message Dispatch
// ============================================================
function handleServerMessage(msg) {
  const { type, payload } = msg;

  switch (type) {
    case "auth_ok":
      onAuthOk(payload);
      break;
    case "auth_error":
      onAuthError(payload);
      break;
    case "message":
      onMessage(payload);
      break;
    case "stream_token":
      onStreamToken(payload);
      break;
    case "message_complete":
      onMessageComplete(payload);
      break;
    case "message_delete":
      onMessageDelete(payload);
      break;
    case "tool_start":
      onToolStart(payload);
      break;
    case "tool_end":
      onToolEnd();
      break;
    case "approval_request":
      onApprovalRequest(payload);
      break;
    case "approval_resolved":
      onApprovalResolved(payload);
      break;
    case "question":
      onQuestion(payload);
      break;
    case "plan_review":
      onPlanReview(payload);
      break;
    case "interrupt_prompt":
      onInterruptPrompt(payload);
      break;
    case "task_update":
      onTaskUpdate(payload);
      break;
    case "status":
      onStatus(payload);
      break;
    case "error":
      onError(payload);
      break;
    case "history":
      onHistory(payload);
      break;
    case "pong":
      state.lastPongTime = Date.now();
      if (connectionDot.className.includes("unstable")) {
        connectionDot.className = "dot dot-connected";
        connectionDot.title = "Connected";
      }
      break;
    case "reload":
      location.reload();
      break;
    case "config_updated":
      // Config was updated externally
      break;
    default:
      console.warn("Unknown WebSocket message type:", type, payload);
      break;
  }
}

// ============================================================
// Auth Handlers
// ============================================================
function onAuthOk(payload) {
  state.chatId = payload.chat_id || "";
  state.sessionId = payload.session_id || "";
  state.reconnectDelay = 1000;

  for (const el of Object.values(state.streamingMessages)) {
    el?.querySelector?.(".msg-content")?.classList?.remove("streaming");
  }
  state.streamingMessages = {};

  authScreen.hidden = true;
  chatScreen.hidden = false;
  settingsScreen.hidden = true;
  setConnected(true);
  startPing();
  messageInput.focus();

  // Store API key for reconnection
  try { sessionStorage.setItem("leashd_key", state.apiKey); }
  catch { /* ignore */ }

  // Initialize tabs if not done yet
  if (TabManager.tabs.length === 0) {
    TabManager.init().then(() => {
      // Single-directory mode: no tabs created, handle hash routing here
      if (TabManager.tabs.length === 0) {
        const route = Router.init();
        if (route && route.sessionId && !route.tabId) {
          state.sessionId = route.sessionId;
        }
        if (state.sessionId) {
          Router.navigate(null, state.sessionId);
        }
      }
    });
  }

  // Auto-activate workspace or directory for new conversations
  if (TabManager._pendingWorkspace) {
    const wsName = TabManager._pendingWorkspace;
    TabManager._pendingWorkspace = null;
    wsSend("message", { text: `/workspace ${wsName}` });
  } else if (TabManager._pendingDir) {
    const dirName = TabManager._pendingDir;
    TabManager._pendingDir = null;
    wsSend("message", { text: `/dir ${dirName}` });
  }

  fetchStatus();
  loadHistory();
}

async function loadHistory() {
  const tab = TabManager.tabs.find(t => t.id === TabManager.activeTab);
  const path = tab?.path || "";
  try {
    const res = await authFetch(
      `/api/history?session_id=${encodeURIComponent(state.sessionId)}&path=${encodeURIComponent(path)}`
    );
    if (!res.ok) return;
    const data = await res.json();
    if (data.messages?.length > 0) {
      messagesEl.innerHTML = "";
      onHistory({ messages: data.messages });
    }
  } catch { /* ignore */ }
}

function onAuthError(payload) {
  const reason = payload.reason || "Authentication failed";
  if (chatScreen.hidden === false) {
    setConnected(false);
    addSystemMessage(`Reconnection failed: ${reason}`);
  } else {
    showAuthError(reason);
  }
}

// ============================================================
// Message Handlers
// ============================================================
function onMessage(payload) {
  const text = payload.text || "";
  const messageId = payload.message_id;
  const buttons = payload.buttons;

  if (messageId && state.streamingMessages[messageId]) {
    const el = state.streamingMessages[messageId];
    const content = el.querySelector(".msg-content");
    content.innerHTML = renderMarkdown(text);
    content.classList.remove("streaming");
    addCopyButtons(content);
    delete state.streamingMessages[messageId];
    scrollToBottom();
    return;
  }

  addAssistantMessage(text, { messageId, buttons });
}

function onStreamToken(payload) {
  const text = payload.text || "";
  const messageId = payload.message_id;

  if (!messageId) {
    addAssistantMessage(text);
    return;
  }

  // Server edits the interrupt message when the task finishes — resolve banner
  if (isQueuedBannerMsg(messageId)) {
    resolveQueuedBanner();
    return;
  }

  if (state.streamingMessages[messageId]) {
    const el = state.streamingMessages[messageId];
    // Batch rapid token arrivals via requestAnimationFrame (~16ms at 60fps)
    el._pendingText = text;
    if (!el._rafId) {
      el._rafId = requestAnimationFrame(() => {
        el._rafId = null;
        const content = el.querySelector(".msg-content");
        if (el._pendingText != null) {
          content.innerHTML = renderMarkdown(el._pendingText);
          el._pendingText = null;
        }
        scrollToBottom();
      });
    }
  } else {
    const existing = messagesEl.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
    if (existing) {
      const content = existing.querySelector(".msg-content");
      content.innerHTML = renderMarkdown(text);
      content.classList.add("streaming");
      state.streamingMessages[messageId] = existing;
    } else {
      const el = addAssistantMessage(text, { messageId, streaming: true });
      state.streamingMessages[messageId] = el;
    }
    scrollToBottom();
  }
}

function onMessageComplete(payload) {
  const messageId = payload.message_id;
  if (messageId && state.streamingMessages[messageId]) {
    const el = state.streamingMessages[messageId];
    // Cancel pending animation frame and flush any buffered text
    if (el._rafId) {
      cancelAnimationFrame(el._rafId);
      el._rafId = null;
    }
    const content = el.querySelector(".msg-content");
    if (el._pendingText != null) {
      content.innerHTML = renderMarkdown(el._pendingText);
      el._pendingText = null;
    }
    content.classList.remove("streaming");
    addCopyButtons(content);
    delete state.streamingMessages[messageId];
  }
}

function onMessageDelete(payload) {
  const messageId = payload.message_id;
  if (!messageId) return;

  // Server deletes the interrupt message after the task finishes — dismiss banner
  if (isQueuedBannerMsg(messageId)) {
    dismissQueuedBanner();
    return;
  }

  const el = messagesEl.querySelector(`[data-message-id="${CSS.escape(messageId)}"]`);
  if (el) el.remove();
  delete state.streamingMessages[messageId];
  updateEmptyState();
}

// ============================================================
// Tool Activity — inline pill indicator in the chat flow
// ============================================================

const ICON_AGENT = '<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M8 4.754a3.246 3.246 0 1 0 0 6.492 3.246 3.246 0 0 0 0-6.492zM5.754 8a2.246 2.246 0 1 1 4.492 0 2.246 2.246 0 0 1-4.492 0z"/><path d="M9.796 1.343c-.527-1.79-3.065-1.79-3.592 0l-.094.319a.873.873 0 0 1-1.255.52l-.292-.16c-1.64-.892-3.433.902-2.54 2.541l.159.292a.873.873 0 0 1-.52 1.255l-.319.094c-1.79.527-1.79 3.065 0 3.592l.319.094a.873.873 0 0 1 .52 1.255l-.16.292c-.892 1.64.901 3.434 2.541 2.54l.292-.159a.873.873 0 0 1 1.255.52l.094.319c.527 1.79 3.065 1.79 3.592 0l.094-.319a.873.873 0 0 1 1.255-.52l.292.16c1.64.893 3.434-.902 2.54-2.541l-.159-.292a.873.873 0 0 1 .52-1.255l.319-.094c1.79-.527 1.79-3.065 0-3.592l-.319-.094a.873.873 0 0 1-.52-1.255l.16-.292c.893-1.64-.902-3.433-2.541-2.54l-.292.159a.873.873 0 0 1-1.255-.52l-.094-.319zm-2.633.283c.246-.835 1.428-.835 1.674 0l.094.319a1.873 1.873 0 0 0 2.693 1.115l.291-.16c.764-.415 1.6.42 1.184 1.185l-.159.292a1.873 1.873 0 0 0 1.116 2.692l.318.094c.835.246.835 1.428 0 1.674l-.319.094a1.873 1.873 0 0 0-1.115 2.693l.16.291c.415.764-.421 1.6-1.185 1.184l-.291-.159a1.873 1.873 0 0 0-2.693 1.116l-.094.318c-.246.835-1.428.835-1.674 0l-.094-.319a1.873 1.873 0 0 0-2.692-1.115l-.292.16c-.764.415-1.6-.421-1.184-1.185l.159-.291A1.873 1.873 0 0 0 1.945 8.93l-.319-.094c-.835-.246-.835-1.428 0-1.674l.319-.094A1.873 1.873 0 0 0 3.06 4.377l-.16-.292c-.415-.764.42-1.6 1.185-1.184l.292.159a1.873 1.873 0 0 0 2.692-1.116l.094-.318z"/></svg>';

function onToolStart(payload) {
  const tool = payload.tool || "";
  const command = payload.command || "";
  const messageId = payload.message_id;
  const agent = payload.agent || null;

  if (!messageId) return;

  // Agent tool itself opens a group — don't show as a regular tool indicator
  if (tool === "Agent" && agent) {
    openAgentGroup(agent, messageId);
    return;
  }

  addToolMessage(tool, command, { messageId, agent });
}

function onToolEnd() {
  closeAgentGroup();
}

function openAgentGroup(agentName, messageId) {
  if (state.currentAgent === agentName && state.agentGroupEl) {
    return;
  }
  closeAgentGroup();

  hideEmptyState();
  const group = document.createElement("div");
  group.className = "agent-group";
  if (messageId) group.setAttribute("data-message-id", messageId);

  const header = document.createElement("div");
  header.className = "agent-header";
  header.innerHTML =
    `<span class="agent-icon">${ICON_AGENT}</span>` +
    `<span class="agent-label">${escapeHtml(agentName)}</span>` +
    `<span class="tool-dots"><span></span><span></span><span></span></span>`;

  group.appendChild(header);
  messagesEl.appendChild(group);
  state.currentAgent = agentName;
  state.agentGroupEl = group;
  scrollToBottom();
}

function closeAgentGroup() {
  if (state.agentGroupEl) {
    state.agentGroupEl.classList.add("agent-done");
    // Stop bouncing dots
    const dots = state.agentGroupEl.querySelector(".agent-header .tool-dots");
    if (dots) dots.remove();
  }
  state.currentAgent = null;
  state.agentGroupEl = null;
}

function addToolMessage(tool, command, opts = {}) {
  hideEmptyState();
  const row = document.createElement("div");
  row.className = "msg-row msg-row-tool";
  if (opts.messageId) {
    row.setAttribute("data-message-id", opts.messageId);
  }

  const inner = document.createElement("div");
  inner.className = "msg-row-inner";

  const indicator = document.createElement("div");
  indicator.className = "tool-indicator";
  indicator.innerHTML =
    `<span class="tool-dots"><span></span><span></span><span></span></span>` +
    `<span class="tool-name">${escapeHtml(tool)}</span>` +
    (command ? `<span class="tool-cmd">${escapeHtml(command)}</span>` : "");

  inner.appendChild(indicator);
  row.appendChild(inner);

  // Nest inside agent group if one is open
  if (state.agentGroupEl && opts.agent) {
    state.agentGroupEl.appendChild(row);
  } else {
    messagesEl.appendChild(row);
  }
  scrollToBottom();
  return row;
}

// ============================================================
// Modal Queue — prevents stacking
// ============================================================
function isModalOpen() {
  return !modalOverlay.hidden;
}

function showModalOrQueue(modalType, renderFn) {
  if (isModalOpen()) {
    state.pendingModals.push({ type: modalType, render: renderFn });
    return;
  }
  state.activeModalType = modalType;
  state.previousFocus = document.activeElement;
  renderFn();
  modalOverlay.hidden = false;
  trapFocusInModal();
}

function closeModal() {
  modalOverlay.hidden = true;
  modalContent.innerHTML = "";
  state.activeModalType = null;

  if (state.previousFocus && state.previousFocus.focus) {
    state.previousFocus.focus();
    state.previousFocus = null;
  }

  // Process next queued modal
  if (state.pendingModals.length > 0) {
    const next = state.pendingModals.shift();
    setTimeout(() => showModalOrQueue(next.type, next.render), 100);
  }
}

// ============================================================
// Focus Trap for Modals
// ============================================================
function trapFocusInModal() {
  requestAnimationFrame(() => {
    const focusable = modalContent.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    if (focusable.length > 0) {
      focusable[0].focus();
    }
  });
}

function handleModalKeydown(e) {
  if (modalOverlay.hidden) return;

  if (e.key === "Escape") {
    closeModal();
    return;
  }

  if (e.key === "Tab") {
    const focusable = Array.from(modalContent.querySelectorAll(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    ));
    if (focusable.length === 0) return;

    const first = focusable[0];
    const last = focusable[focusable.length - 1];

    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }
}

document.addEventListener("keydown", handleModalKeydown);

// ============================================================
// Approvals — inline cards in the message stream
// ============================================================
function onApprovalRequest(payload) {
  const { request_id, tool, description } = payload;
  hideEmptyState();

  const row = document.createElement("div");
  row.className = "msg-row msg-row-approval";
  row.setAttribute("data-approval-id", request_id);

  const inner = document.createElement("div");
  inner.className = "msg-row-inner";

  const card = document.createElement("div");
  card.className = "approval-card";
  card.innerHTML =
    `<div class="approval-header">` +
    `<span class="approval-icon">${ICON_SHIELD}</span>` +
    `<span class="approval-title">Approval Required</span>` +
    `</div>` +
    `<div class="approval-body">` +
    `<span class="approval-tool">${escapeHtml(tool || "unknown")}</span>` +
    (description ? `<span class="approval-desc">${escapeHtml(description)}</span>` : "") +
    `</div>` +
    `<div class="approval-actions">` +
    `<button class="btn-deny approval-btn" data-action="deny">Deny</button>` +
    `<button class="btn-approve approval-btn" data-action="approve">Approve</button>` +
    `</div>`;

  card.querySelector('[data-action="approve"]').onclick = () => {
    wsSend("approval_response", { approval_id: request_id, approved: true });
    resolveApprovalCard(row, true);
  };
  card.querySelector('[data-action="deny"]').onclick = () => {
    wsSend("approval_response", { approval_id: request_id, approved: false });
    resolveApprovalCard(row, false);
  };

  inner.appendChild(card);
  row.appendChild(inner);
  messagesEl.appendChild(row);
  scrollToBottom();

  // Auto-focus approve button for keyboard users
  requestAnimationFrame(() => {
    card.querySelector('[data-action="approve"]')?.focus();
  });
}

function resolveApprovalCard(row, approved) {
  const card = row.querySelector(".approval-card");
  if (!card) return;
  card.classList.add(approved ? "approved" : "denied");
  const actions = card.querySelector(".approval-actions");
  if (actions) {
    actions.innerHTML = `<span class="approval-resolved">${approved ? "✓ Approved" : "✗ Denied"}</span>`;
  }
  // Auto-dismiss with fallback if animation doesn't fire
  setTimeout(() => {
    row.classList.add("approval-dismissing");
    const fallback = setTimeout(() => row.remove(), 500);
    row.addEventListener("animationend", () => {
      clearTimeout(fallback);
      row.remove();
    }, { once: true });
  }, 1500);
}

function onApprovalResolved(payload) {
  const id = payload?.request_id || payload?.approval_id;
  if (!id) return;
  const row = messagesEl.querySelector(`[data-approval-id="${CSS.escape(id)}"]`);
  if (row && !row.querySelector(".approval-resolved")) {
    resolveApprovalCard(row, payload.approved !== false);
  }
}

// ============================================================
// Questions / Interactions
// ============================================================
function onQuestion(payload) {
  const { interaction_id, question, header, options } = payload;
  hideEmptyState();

  let answered = false;
  function submitAnswer(answer, label) {
    if (answered) return;
    answered = true;
    wsSend("interaction_response", { interaction_id, answer });
    resolveQuestionCard(row, label);
  }

  const messageId = `question-${interaction_id}`;
  const row = document.createElement("div");
  row.className = "msg-row msg-row-question";
  row.setAttribute("data-message-id", messageId);
  row.setAttribute("data-interaction-id", interaction_id);

  const inner = document.createElement("div");
  inner.className = "msg-row-inner";

  const card = document.createElement("div");
  card.className = "question-card";

  // Header
  const headerEl = document.createElement("div");
  headerEl.className = "question-header";
  headerEl.innerHTML =
    `<span class="question-icon">${ICON_INFO}</span>` +
    `<span class="question-title">${escapeHtml(header || "Question")}</span>`;
  card.appendChild(headerEl);

  // Question text
  if (question) {
    const body = document.createElement("div");
    body.className = "question-body";
    body.textContent = question;
    card.appendChild(body);
  }

  // Option buttons — single click sends immediately
  if (options && options.length > 0) {
    const optionsWrap = document.createElement("div");
    optionsWrap.className = "question-options";
    for (const opt of options) {
      const btn = document.createElement("button");
      btn.className = "question-option-btn";
      btn.textContent = opt.label || opt.text || opt.value || "";
      const value = opt.value || opt.label || "";
      btn.onclick = () => submitAnswer(value, opt.label || opt.text || value);
      optionsWrap.appendChild(btn);
    }
    card.appendChild(optionsWrap);
  }

  // Textarea + action buttons for custom / edited answers
  const inputArea = document.createElement("div");
  inputArea.className = "question-input-area";
  const textarea = document.createElement("textarea");
  textarea.className = "question-textarea";
  textarea.placeholder = "Type your answer...";
  textarea.rows = 3;
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      sendBtn.click();
    }
  });
  // Auto-resize as user types
  textarea.addEventListener("input", () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 160) + "px";
  });
  inputArea.appendChild(textarea);

  const btnRow = document.createElement("div");
  btnRow.className = "question-btn-row";
  const sendBtn = document.createElement("button");
  sendBtn.className = "question-submit-btn";
  sendBtn.textContent = "Send";
  sendBtn.onclick = () => {
    const answer = textarea.value.trim();
    if (!answer) return;
    submitAnswer(answer, answer.length > 40 ? answer.slice(0, 40) + "…" : answer);
  };
  const skipBtn = document.createElement("button");
  skipBtn.className = "question-skip-btn";
  skipBtn.textContent = "Skip";
  skipBtn.onclick = () => submitAnswer("", "Skipped");
  btnRow.appendChild(skipBtn);
  btnRow.appendChild(sendBtn);
  inputArea.appendChild(btnRow);
  card.appendChild(inputArea);

  inner.appendChild(card);
  row.appendChild(inner);
  messagesEl.appendChild(row);
  scrollToBottom();

  // Focus the first option button or the textarea
  requestAnimationFrame(() => {
    const firstOption = card.querySelector(".question-option-btn");
    if (firstOption) firstOption.focus();
    else textarea.focus();
  });
}

function resolveQuestionCard(row, chosenLabel) {
  const card = row.querySelector(".question-card");
  if (!card) return;
  card.classList.add("resolved");
  // Replace input area with resolved label
  const inputArea = card.querySelector(".question-input-area");
  if (inputArea) inputArea.remove();
  const options = card.querySelector(".question-options");
  if (options) options.remove();
  const resolvedEl = document.createElement("div");
  resolvedEl.className = "question-resolved";
  resolvedEl.textContent = `→ ${chosenLabel}`;
  card.appendChild(resolvedEl);
  // Auto-dismiss with fallback if animation doesn't fire
  setTimeout(() => {
    row.classList.add("approval-dismissing");
    const fallback = setTimeout(() => row.remove(), 500);
    row.addEventListener("animationend", () => {
      clearTimeout(fallback);
      row.remove();
    }, { once: true });
  }, 2000);
}

function onPlanReview(payload) {
  const { interaction_id, description, message_id } = payload;
  const msgId = message_id || `plan-review-${interaction_id}`;

  const buttons = [
    [
      { text: "Accept", data: `plan:${interaction_id}:clean_edit` },
      { text: "Accept (manual edits)", data: `plan:${interaction_id}:default` },
      { text: "Adjust", data: `plan:${interaction_id}:adjust` },
    ],
  ];

  addAssistantMessage(description || "", { messageId: msgId, buttons });
}

// ============================================================
// Interrupt — queued-message banner above the input
// ============================================================
function onInterruptPrompt(payload) {
  const { interrupt_id, message_preview, message_id } = payload;

  // Track the banner's server message ID so stream_token / message_delete
  // can update and dismiss it instead of creating phantom chat rows.
  state.queuedBannerMsgId = message_id || null;

  queuedBanner.innerHTML =
    `<span class="queued-icon">${ICON_TIMER}</span>` +
    `<span class="queued-text">${escapeHtml(message_preview || "")}</span>` +
    `<button class="queued-send" data-action="send">Send now</button>` +
    `<button class="queued-dismiss" data-action="dismiss" aria-label="Dismiss">&times;</button>`;

  queuedBanner.hidden = false;
  queuedBanner.classList.remove("resolved");

  queuedBanner.querySelector('[data-action="send"]').onclick = () => {
    wsSend("interrupt_response", { interrupt_id, send_now: true });
    dismissQueuedBanner();
  };
  queuedBanner.querySelector('[data-action="dismiss"]').onclick = () => {
    wsSend("interrupt_response", { interrupt_id, send_now: false });
    resolveQueuedBanner();
  };
}

function resolveQueuedBanner() {
  queuedBanner.classList.add("resolved");
  setTimeout(() => {
    queuedBanner.hidden = true;
    queuedBanner.innerHTML = "";
    queuedBanner.classList.remove("resolved");
    state.queuedBannerMsgId = null;
  }, 400);
}

function dismissQueuedBanner() {
  queuedBanner.hidden = true;
  queuedBanner.innerHTML = "";
  state.queuedBannerMsgId = null;
}

function isQueuedBannerMsg(messageId) {
  return messageId && state.queuedBannerMsgId === messageId;
}

// ============================================================
// Task Updates
// ============================================================
function sanitizeCssClass(str) {
  return str.toLowerCase().replace(/[^a-z0-9-]/g, "");
}

function onTaskUpdate(payload) {
  const phase = payload.phase || "";
  const status = payload.status || "";
  const description = payload.description || "";

  const sanitized = sanitizeCssClass(phase || status);
  const badgeClass = sanitized ? `task-badge-${sanitized}` : "";
  const badge = phase ? `<span class="task-badge ${badgeClass}">${escapeHtml(phase)}</span> ` : "";
  const text = `${badge}${escapeHtml(description || status)}`;

  addSystemMessage(text, { raw: true });
}

// ============================================================
// Status & History
// ============================================================
function onStatus(payload) {
  if (payload.typing) {
    // Could show typing indicator, but activity bar covers this
  }
}

function onError(payload) {
  addSystemMessage(`Error: ${payload.reason || "Unknown error"}`);
}

function onHistory(payload) {
  const messages = payload.messages || [];
  for (const msg of messages) {
    const opts = { timestamp: msg.created_at || null };
    if (msg.role === "user") {
      opts.attachments = msg.attachments;
      addUserMessage(msg.content || msg.text || "", opts);
    } else {
      addAssistantMessage(msg.content || msg.text || "", opts);
    }
  }
}

async function fetchStatus() {
  try {
    const res = await authFetch("/api/status");
    if (res.ok) {
      const data = await res.json();
      if (data.working_directory && !TabManager.activeTab) {
        sidebarWorkingDir.textContent = data.working_directory;
        sidebarWorkingDir.title = data.working_directory;
      }
    }
  } catch { /* ignore */ }
}

// ============================================================
// Send Messages
// ============================================================
messageInput.addEventListener("input", () => {
  // Auto-resize
  messageInput.style.height = "auto";
  messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
  sendBtn.disabled = !state.connected || !(messageInput.value.trim() || pendingAttachments.length > 0);

  // Command palette
  const val = messageInput.value;
  if (val.startsWith("/") && !val.includes("\n")) {
    const cmdPart = val.split(" ")[0];
    const hasArgs = val.includes(" ");
    const exactMatch = SLASH_COMMANDS.some(c => c.command === cmdPart);
    if (hasArgs && exactMatch) {
      CommandPalette.hide();
    } else {
      CommandPalette.show(cmdPart);
    }
  } else {
    CommandPalette.hide();
  }
});

messageInput.addEventListener("keydown", (e) => {
  if (CommandPalette.isVisible()) {
    if (e.key === "ArrowDown") { e.preventDefault(); CommandPalette.moveSelection(1); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); CommandPalette.moveSelection(-1); return; }
    if (e.key === "Tab") { e.preventDefault(); CommandPalette.selectCurrent(); return; }
    if (e.key === "Escape") { e.preventDefault(); CommandPalette.hide(); return; }
    if (e.key === "Enter") { e.preventDefault(); if (CommandPalette.selectCurrent()) return; }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener("click", sendMessage);

// ============================================================
// File Upload
// ============================================================
const pendingAttachments = [];
const SUPPORTED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf"];
const MAX_FILE_SIZE = 10 * 1024 * 1024; // 10 MB
const MAX_ATTACHMENTS = 5;

uploadBtn.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", () => {
  for (const file of fileInput.files) {
    addPendingFile(file);
  }
  fileInput.value = "";
});

// Drag and drop
const inputPill = $(".input-pill");
inputPill.addEventListener("dragover", (e) => {
  e.preventDefault();
  inputPill.classList.add("drag-over");
});
inputPill.addEventListener("dragleave", () => {
  inputPill.classList.remove("drag-over");
});
inputPill.addEventListener("drop", (e) => {
  e.preventDefault();
  inputPill.classList.remove("drag-over");
  for (const file of e.dataTransfer.files) {
    addPendingFile(file);
  }
});

// Clipboard paste (screenshots via Cmd+V / Ctrl+V)
messageInput.addEventListener("paste", (e) => {
  const items = e.clipboardData?.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) addPendingFile(file);
    }
  }
});

function addPendingFile(file) {
  if (pendingAttachments.length >= MAX_ATTACHMENTS) return;
  if (!SUPPORTED_TYPES.includes(file.type)) return;
  if (file.size > MAX_FILE_SIZE) return;

  const reader = new FileReader();
  reader.onload = () => {
    const b64 = reader.result.split(",")[1];
    const att = { filename: file.name, media_type: file.type, data: b64, _file: file };
    pendingAttachments.push(att);
    renderAttachmentPreviews();
  };
  reader.readAsDataURL(file);
}

function renderAttachmentPreviews() {
  attachmentPreview.innerHTML = "";
  attachmentPreview.hidden = pendingAttachments.length === 0;
  pendingAttachments.forEach((att, idx) => {
    const thumb = document.createElement("div");
    thumb.className = "attachment-thumb";
    if (att.media_type.startsWith("image/")) {
      const img = document.createElement("img");
      img.src = `data:${att.media_type};base64,${att.data}`;
      img.alt = att.filename;
      thumb.appendChild(img);
    }
    const label = document.createElement("span");
    label.textContent = att.filename.length > 20 ? att.filename.slice(0, 17) + "..." : att.filename;
    thumb.appendChild(label);
    const remove = document.createElement("span");
    remove.className = "remove-attachment";
    remove.textContent = "\u00d7";
    remove.onclick = () => { pendingAttachments.splice(idx, 1); renderAttachmentPreviews(); };
    thumb.appendChild(remove);
    attachmentPreview.appendChild(thumb);
  });
  // Enable send if we have attachments even without text
  sendBtn.disabled = !(messageInput.value.trim() || pendingAttachments.length > 0) || !state.connected;
}

function sendMessage() {
  const text = messageInput.value.trim();
  if ((!text && pendingAttachments.length === 0) || !state.connected) return;

  // Debounce: prevent double-sends within 300ms
  if (state.sendDebounceTimer) return;
  state.sendDebounceTimer = setTimeout(() => {
    state.sendDebounceTimer = null;
  }, 300);

  // Remove any resolved approval cards immediately
  messagesEl.querySelectorAll(".approval-card.approved, .approval-card.denied").forEach(card => {
    const row = card.closest(".msg-row-approval");
    if (row) row.remove();
  });

  const attachments = pendingAttachments.map(a => ({
    filename: a.filename,
    media_type: a.media_type,
    data: a.data,
  }));

  const displayText = text || `[${attachments.length} file(s) attached]`;
  addUserMessage(displayText, { attachments });

  const payload = { text: text || "Describe this image." };
  if (attachments.length > 0) {
    payload.attachments = attachments;
  }
  wsSend("message", payload);

  messageInput.value = "";
  messageInput.style.height = "auto";
  pendingAttachments.length = 0;
  renderAttachmentPreviews();
  sendBtn.disabled = true;
  CommandPalette.hide();

  // Refresh sidebar after first message so new conversations appear
  state.messageCountInSession++;
  if (state.messageCountInSession <= 2) {
    scheduleSidebarRefresh();
  }
}

function scheduleSidebarRefresh() {
  if (state.sidebarRefreshTimer) clearTimeout(state.sidebarRefreshTimer);
  state.sidebarRefreshTimer = setTimeout(() => {
    state.sidebarRefreshTimer = null;
    if (TabManager.activeTab) {
      TabManager._fetchConversations(TabManager.activeTab);
    }
  }, 3000);
}


// ============================================================
// DOM Helpers — Message Rendering (Bubble-style)
// ============================================================
function addUserMessage(text, opts = {}) {
  hideEmptyState();
  const row = createMessageRow("user", text, opts);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function addAssistantMessage(text, opts = {}) {
  hideEmptyState();
  const row = createMessageRow("assistant", text, opts);
  messagesEl.appendChild(row);
  scrollToBottom();
  return row;
}

function addSystemMessage(text, opts = {}) {
  hideEmptyState();
  const row = document.createElement("div");
  row.className = "msg-row msg-row-system";
  if (opts.messageId) {
    row.setAttribute("data-message-id", opts.messageId);
  }

  const inner = document.createElement("div");
  inner.className = "msg-row-inner";

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar msg-avatar-system";
  avatar.innerHTML = ICON_INFO;

  const content = document.createElement("div");
  content.className = "msg-content";
  if (opts.raw) {
    content.innerHTML = text;
  } else {
    content.innerHTML = renderMarkdown(text);
  }

  inner.appendChild(avatar);
  inner.appendChild(content);
  row.appendChild(inner);
  messagesEl.appendChild(row);
  addCopyButtons(content);
  scrollToBottom();
  return row;
}

function formatMessageTime(date) {
  const now = new Date();
  const hours = date.getHours();
  const mins = String(date.getMinutes()).padStart(2, "0");
  const ampm = hours >= 12 ? "PM" : "AM";
  const h12 = hours % 12 || 12;
  const time = `${h12}:${mins} ${ampm}`;
  // Same day — just time
  if (date.toDateString() === now.toDateString()) return time;
  // Same year — month day + time
  const month = date.toLocaleString(undefined, { month: "short" });
  const day = date.getDate();
  if (date.getFullYear() === now.getFullYear()) return `${month} ${day}, ${time}`;
  return `${month} ${day}, ${date.getFullYear()}, ${time}`;
}

function createMessageRow(role, text, opts = {}) {
  const row = document.createElement("div");
  row.className = `msg-row msg-row-${role}`;
  if (opts.messageId) {
    row.setAttribute("data-message-id", opts.messageId);
  }

  const inner = document.createElement("div");
  inner.className = "msg-row-inner";

  const avatar = document.createElement("div");
  avatar.className = `msg-avatar msg-avatar-${role}`;
  avatar.innerHTML = role === "user" ? ICON_USER : ICON_BOT;

  const content = document.createElement("div");
  content.className = "msg-content";
  if (opts.streaming) {
    content.classList.add("streaming");
  }
  content.innerHTML = renderMarkdown(text);

  if (opts.attachments && opts.attachments.length > 0) {
    const attachWrap = document.createElement("div");
    attachWrap.className = "msg-attachments";
    for (const att of opts.attachments) {
      if (att.media_type && att.media_type.startsWith("image/")) {
        const img = document.createElement("img");
        img.className = "msg-attachment-img";
        img.src = `data:${att.media_type};base64,${att.data}`;
        img.alt = att.filename || "Attached image";
        img.loading = "lazy";
        img.addEventListener("click", () => window.open(img.src, "_blank"));
        attachWrap.appendChild(img);
      } else {
        const chip = document.createElement("div");
        chip.className = "msg-attachment-chip";
        chip.textContent = att.filename || "file";
        attachWrap.appendChild(chip);
      }
    }
    content.appendChild(attachWrap);
  }

  // Wrap content + timestamp in a column container
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.appendChild(content);

  if (opts.buttons && opts.buttons.length) {
    const btnRow = document.createElement("div");
    btnRow.className = "msg-buttons";
    for (const rowBtns of opts.buttons) {
      for (const btn of rowBtns) {
        const el = document.createElement("button");
        el.className = "msg-btn";
        el.textContent = btn.text;
        el.onclick = () => {
          if (btn.data && btn.data.startsWith("plan:")) {
            const [, interactionId, decision] = btn.data.split(":");
            wsSend("interaction_response", {
              interaction_id: interactionId,
              answer: decision,
            });
            for (const b of btnRow.querySelectorAll(".msg-btn")) {
              b.disabled = true;
              b.style.opacity = "0.5";
            }
          } else if (btn.data) {
            wsSend("message", { text: btn.data });
          }
        };
        btnRow.appendChild(el);
      }
    }
    bubble.appendChild(btnRow);
  }

  // Timestamp
  const ts = document.createElement("span");
  ts.className = "msg-timestamp";
  const msgDate = opts.timestamp ? new Date(opts.timestamp) : new Date();
  ts.textContent = formatMessageTime(msgDate);
  ts.title = msgDate.toLocaleString();
  bubble.appendChild(ts);

  inner.appendChild(avatar);
  inner.appendChild(bubble);
  row.appendChild(inner);

  if (!opts.streaming) {
    addCopyButtons(content);
  }
  return row;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

// Close modal on overlay click
modalOverlay.addEventListener("click", (e) => {
  if (e.target === modalOverlay) {
    closeModal();
  }
});

// ============================================================
// Copy buttons for code blocks
// ============================================================
function addCopyButtons(container) {
  if (!container) return;
  const pres = container.querySelectorAll("pre");
  for (const pre of pres) {
    if (pre.querySelector(".copy-btn")) continue;
    const code = pre.querySelector("code");

    // Syntax highlighting via highlight.js
    if (code && typeof hljs !== "undefined" && !code.classList.contains("hljs")) {
      hljs.highlightElement(code);
    }

    // Language label extracted from class="language-xxx" that marked produces
    if (code) {
      const langClass = [...code.classList].find((c) => c.startsWith("language-"));
      if (langClass) {
        const lang = langClass.replace("language-", "");
        const label = document.createElement("span");
        label.className = "code-lang-label";
        label.textContent = lang;
        pre.appendChild(label);
      }
    }

    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "Copy";
    btn.onclick = (e) => {
      e.stopPropagation();
      const text = (code || pre).textContent || "";
      navigator.clipboard.writeText(text).then(() => {
        btn.textContent = "Copied!";
        setTimeout(() => { btn.textContent = "Copy"; }, 1500);
      }).catch(() => {
        const range = document.createRange();
        range.selectNodeContents(code || pre);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
      });
    };
    pre.style.position = "relative";
    pre.appendChild(btn);
  }
}

// ============================================================
// Markdown renderer (marked.js + DOMPurify with regex fallback)
// ============================================================
function renderMarkdown(text) {
  if (!text) return "";

  if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
    const raw = marked.parse(text);
    return DOMPurify.sanitize(raw, PURIFY_CONFIG);
  }

  // Fallback when CDN libraries fail to load
  return escapeHtml(text).replace(/\n/g, "<br>");
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ============================================================
// Command Palette
// ============================================================
const CommandPalette = {
  _el: null,
  _items: [],

  _getOrCreate() {
    if (!this._el) {
      const el = document.createElement("div");
      el.id = "command-palette";
      el.className = "command-palette";
      el.setAttribute("role", "listbox");
      el.hidden = true;
      const inputArea = $("#input-area");
      inputArea.insertBefore(el, inputArea.querySelector(".input-pill"));
      this._el = el;

      document.addEventListener("click", (e) => {
        if (!this._el.contains(e.target) && e.target !== messageInput) {
          this.hide();
        }
      });
    }
    return this._el;
  },

  show(filter) {
    const el = this._getOrCreate();
    const query = filter.toLowerCase();
    this._items = SLASH_COMMANDS.filter(c => c.command.startsWith(query));
    if (this._items.length === 0) { this.hide(); return; }

    state.commandPaletteIndex = 0;
    el.innerHTML = "";
    this._items.forEach((item, idx) => {
      const row = document.createElement("div");
      row.className = "command-palette-item" + (idx === 0 ? " active" : "");
      row.setAttribute("role", "option");
      row.setAttribute("aria-selected", idx === 0 ? "true" : "false");
      row.innerHTML = `<span class="command-palette-name">${escapeHtml(item.command)}</span>`
        + `<span class="command-palette-desc">${escapeHtml(item.description)}</span>`;
      row.addEventListener("mouseenter", () => this._highlight(idx));
      row.addEventListener("click", () => this._select(idx));
      el.appendChild(row);
    });
    el.hidden = false;
  },

  hide() {
    if (this._el) this._el.hidden = true;
    state.commandPaletteIndex = -1;
  },

  isVisible() {
    return this._el && !this._el.hidden;
  },

  moveSelection(delta) {
    if (!this.isVisible() || this._items.length === 0) return;
    const newIdx = Math.max(0, Math.min(this._items.length - 1, state.commandPaletteIndex + delta));
    this._highlight(newIdx);
  },

  selectCurrent() {
    if (!this.isVisible() || state.commandPaletteIndex < 0) return false;
    this._select(state.commandPaletteIndex);
    return true;
  },

  _highlight(idx) {
    state.commandPaletteIndex = idx;
    const children = this._el.children;
    for (let i = 0; i < children.length; i++) {
      children[i].classList.toggle("active", i === idx);
      children[i].setAttribute("aria-selected", i === idx ? "true" : "false");
    }
    children[idx]?.scrollIntoView({ block: "nearest" });
  },

  _select(idx) {
    const item = this._items[idx];
    if (!item) return;
    messageInput.value = item.command + " ";
    messageInput.dispatchEvent(new Event("input"));
    this.hide();
    messageInput.focus();
    messageInput.selectionStart = messageInput.selectionEnd = messageInput.value.length;
  },
};

// ============================================================
// Toast Notifications
// ============================================================
function showToast(message, type = "success") {
  toastEl.textContent = message;
  toastEl.className = `toast toast-${type}`;
  toastEl.hidden = false;
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => {
    toastEl.hidden = true;
  }, 3000);
}

// ============================================================
// Settings Manager
// ============================================================
const SettingsManager = {
  _config: null,
  _dirty: false,

  show() {
    chatScreen.hidden = true;
    settingsScreen.hidden = false;
    this._dirty = false;
    saveBar.hidden = true;
    this._fetchAndRender();
  },

  hide() {
    settingsScreen.hidden = true;
    chatScreen.hidden = false;
    messageInput.focus();
  },

  async _fetchAndRender() {
    settingsBody.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);">Loading...</div>';
    try {
      const res = await authFetch("/api/config");
      if (!res.ok) throw new Error("Failed to fetch config");
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      this._config = data;
      this._render(data);
    } catch (e) {
      settingsBody.innerHTML = `<div style="text-align:center;padding:40px;color:var(--danger);">Failed to load settings: ${escapeHtml(e.message)}</div>`;
    }
  },

  _render(config) {
    const html = `<div class="settings-inner">
      ${this._renderDisplaySection()}
      ${this._renderAgentSection(config.agent)}
      ${this._renderAutonomousSection(config.autonomous)}
      ${this._renderBrowserSection(config.browser)}
    </div>`;
    settingsBody.innerHTML = html;
    this._bindEvents();
  },

  _renderDisplaySection() {
    const theme = ThemeManager.current();
    return `<div class="settings-section">
      <h3>Display</h3>
      <div class="setting-row">
        <div><div class="setting-label">Theme</div></div>
        <div class="segmented-control" data-setting="theme">
          <button data-value="auto" class="${theme === 'auto' ? 'active' : ''}">Auto</button>
          <button data-value="dark" class="${theme === 'dark' ? 'active' : ''}">Dark</button>
          <button data-value="light" class="${theme === 'light' ? 'active' : ''}">Light</button>
        </div>
      </div>
    </div>`;
  },

  _renderAgentSection(agent) {
    const effort = agent.effort || "medium";
    const runtime = agent.runtime || "claude-code";
    const mode = agent.default_mode || "default";

    return `<div class="settings-section">
      <h3>Agent</h3>
      <div class="setting-row">
        <div><div class="setting-label">Effort</div></div>
        <div class="segmented-control" data-setting="agent.effort">
          ${["low", "medium", "high", "max"].map(v =>
            `<button data-value="${v}" class="${effort === v ? 'active' : ''}">${v}</button>`
          ).join("")}
        </div>
      </div>
      <div class="setting-row">
        <div><div class="setting-label">Runtime</div></div>
        <select class="select-control" data-setting="agent.runtime">
          <option value="claude-code" ${runtime === 'claude-code' ? 'selected' : ''}>Claude Code</option>
          <option value="codex" ${runtime === 'codex' ? 'selected' : ''}>Codex</option>
        </select>
      </div>
      <div class="setting-row">
        <div><div class="setting-label">Default Mode</div></div>
        <select class="select-control" data-setting="agent.default_mode">
          <option value="default" ${mode === 'default' ? 'selected' : ''}>Default</option>
          <option value="plan" ${mode === 'plan' ? 'selected' : ''}>Plan</option>
          <option value="auto" ${mode === 'auto' ? 'selected' : ''}>Auto</option>
        </select>
      </div>
    </div>`;
  },

  _renderAutonomousSection(auto) {
    const toggleRow = (label, key, value) => `
      <div class="setting-row">
        <div><div class="setting-label">${escapeHtml(label)}</div></div>
        <label class="toggle-switch">
          <input type="checkbox" data-setting="autonomous.${key}" ${value ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>`;

    return `<div class="settings-section">
      <h3>Autonomous</h3>
      ${toggleRow("Enabled", "enabled", auto.enabled)}
      ${toggleRow("Auto Approver", "auto_approver", auto.auto_approver)}
      ${toggleRow("Auto Plan", "auto_plan", auto.auto_plan)}
      ${toggleRow("Auto PR", "auto_pr", auto.auto_pr)}
      <div class="setting-row" id="base-branch-row" ${!auto.auto_pr ? 'style="display:none"' : ''}>
        <div><div class="setting-label">PR Base Branch</div></div>
        <input type="text" class="text-input" data-setting="autonomous.auto_pr_base_branch" value="${escapeHtml(auto.auto_pr_base_branch || 'main')}">
      </div>
      ${toggleRow("Autonomous Loop", "autonomous_loop", auto.autonomous_loop)}
      <div class="setting-row">
        <div><div class="setting-label">Max Retries</div></div>
        <input type="number" class="number-input" data-setting="autonomous.max_retries" value="${auto.max_retries || 3}" min="0" max="10">
      </div>
    </div>`;
  },

  _renderBrowserSection(browser) {
    const backend = browser.backend || "playwright";
    return `<div class="settings-section">
      <h3>Browser</h3>
      <div class="setting-row">
        <div><div class="setting-label">Backend</div></div>
        <select class="select-control" data-setting="browser.backend">
          <option value="playwright" ${backend === 'playwright' ? 'selected' : ''}>Playwright</option>
          <option value="agent-browser" ${backend === 'agent-browser' ? 'selected' : ''}>Agent Browser</option>
        </select>
      </div>
      <div class="setting-row">
        <div><div class="setting-label">Headless</div></div>
        <label class="toggle-switch">
          <input type="checkbox" data-setting="browser.headless" ${browser.headless ? 'checked' : ''}>
          <span class="toggle-slider"></span>
        </label>
      </div>
    </div>`;
  },

  _bindEvents() {
    // Segmented controls
    for (const seg of settingsBody.querySelectorAll(".segmented-control")) {
      for (const btn of seg.querySelectorAll("button")) {
        btn.onclick = () => {
          seg.querySelectorAll("button").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");

          // Theme is client-side only
          if (seg.dataset.setting === "theme") {
            ThemeManager.apply(btn.dataset.value);
          } else {
            this._markDirty();
          }
        };
      }
    }

    // Selects
    for (const sel of settingsBody.querySelectorAll("select")) {
      sel.onchange = () => this._markDirty();
    }

    // Toggles
    for (const input of settingsBody.querySelectorAll('input[type="checkbox"]')) {
      input.onchange = () => {
        this._markDirty();
        // Show/hide base branch row
        if (input.dataset.setting === "autonomous.auto_pr") {
          const row = document.getElementById("base-branch-row");
          if (row) row.style.display = input.checked ? "" : "none";
        }
      };
    }

    // Text / number inputs
    for (const input of settingsBody.querySelectorAll('input[type="text"], input[type="number"]')) {
      input.oninput = () => this._markDirty();
    }
  },

  _markDirty() {
    this._dirty = true;
    saveBar.hidden = false;
  },

  async save() {
    if (!this._dirty) return;

    const updates = {};

    // Collect agent settings
    const agent = {};
    const effortSeg = settingsBody.querySelector('[data-setting="agent.effort"]');
    if (effortSeg) {
      const active = effortSeg.querySelector("button.active");
      if (active) agent.effort = active.dataset.value;
    }
    const runtimeSel = settingsBody.querySelector('[data-setting="agent.runtime"]');
    if (runtimeSel) agent.runtime = runtimeSel.value;
    const modeSel = settingsBody.querySelector('[data-setting="agent.default_mode"]');
    if (modeSel) agent.default_mode = modeSel.value;
    if (Object.keys(agent).length) updates.agent = agent;

    // Collect autonomous settings
    const autonomous = {};
    for (const input of settingsBody.querySelectorAll('[data-setting^="autonomous."]')) {
      const key = input.dataset.setting.replace("autonomous.", "");
      if (input.type === "checkbox") {
        autonomous[key] = input.checked;
      } else if (input.type === "number") {
        autonomous[key] = parseInt(input.value, 10) || 0;
      } else {
        autonomous[key] = input.value;
      }
    }
    if (Object.keys(autonomous).length) updates.autonomous = autonomous;

    // Collect browser settings
    const browser = {};
    const backendSel = settingsBody.querySelector('[data-setting="browser.backend"]');
    if (backendSel) browser.backend = backendSel.value;
    const headlessCb = settingsBody.querySelector('[data-setting="browser.headless"]');
    if (headlessCb) browser.headless = headlessCb.checked;
    if (Object.keys(browser).length) updates.browser = browser;

    try {
      const res = await authFetch("/api/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updates),
      });
      const data = await res.json();
      if (data.success) {
        showToast("Settings saved");
        this._dirty = false;
        saveBar.hidden = true;
      } else {
        showToast(data.reason || "Save failed", "error");
      }
    } catch (e) {
      showToast("Save failed: " + e.message, "error");
    }
  },
};

// Wire settings buttons
settingsBtn.addEventListener("click", () => SettingsManager.show());
settingsBackBtn.addEventListener("click", () => SettingsManager.hide());
saveBtn.addEventListener("click", () => SettingsManager.save());

// Escape key to leave settings
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !settingsScreen.hidden && modalOverlay.hidden) {
    SettingsManager.hide();
  }
});

// Wire theme toggle
themeToggleBtn.addEventListener("click", () => ThemeManager.toggle());

// ============================================================
// Auto-reconnect with saved key
// ============================================================
(function init() {
  ThemeManager.init();
  try {
    const savedKey = sessionStorage.getItem("leashd_key");
    if (savedKey) {
      state.apiKey = savedKey;
      authBtn.disabled = true;
      connect();
    }
  } catch { /* ignore */ }
})();
