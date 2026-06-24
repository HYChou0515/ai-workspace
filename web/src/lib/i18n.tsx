/**
 * Minimal hand-rolled i18n (#160). A central typed message catalog keyed by a
 * dotted string; `useT()` returns a `t(key)` bound to the active locale. No
 * dependency, no plural/interpolation machinery — every #160 string is static,
 * so the surface stays tiny. Adopted incrementally: a component routed through
 * `t()` adds its keys here; untouched components keep their inline English.
 */

import { createContext, useCallback, useContext, useState } from "react";

export type Locale = "zh-TW" | "en";

/** Each entry carries both locales, so a missing translation is a type error. */
type Entry = Record<Locale, string>;

export const messages = {
  // Settings panel (WorkspaceShell)
  "settings.title": { "zh-TW": "設定", en: "Settings" },
  "settings.theme": { "zh-TW": "外觀", en: "Appearance" },
  "settings.theme.note": {
    "zh-TW": "「系統」會跟隨你的作業系統外觀。",
    en: "“System” follows your OS appearance.",
  },
  "theme.system": { "zh-TW": "系統", en: "System" },
  "theme.light": { "zh-TW": "淺色", en: "Light" },
  "theme.dark": { "zh-TW": "深色", en: "Dark" },
  "settings.language": { "zh-TW": "語言", en: "Language" },
  "settings.about": { "zh-TW": "關於", en: "About" },
  "about.product": { "zh-TW": "產品", en: "Product" },
  "about.signin": { "zh-TW": "登入方式", en: "Sign-in" },
  "about.signin.value": { "zh-TW": "單人示範（免登入）", en: "Single-user demo (no sign-in)" },
  "about.docs": { "zh-TW": "開發者文件", en: "Developer docs" },
  "about.docs.link": { "zh-TW": "API 文件", en: "API reference" },

  // Model + reasoning-depth picker (ModelEffortPicker)
  "picker.aria": { "zh-TW": "模型與思考深度", en: "Model and thinking depth" },
  "picker.model": { "zh-TW": "模型", en: "Model" },
  "picker.default": { "zh-TW": "預設", en: "Default" },
  "picker.effort": { "zh-TW": "思考深度", en: "Thinking depth" },
  "effort.low": { "zh-TW": "快速", en: "Quick" },
  "effort.medium": { "zh-TW": "一般", en: "Standard" },
  "effort.high": { "zh-TW": "深入", en: "Deep" },
  "effort.low.note": { "zh-TW": "回答最快，思考較淺", en: "Fastest answer, lighter thinking" },
  "effort.medium.note": { "zh-TW": "深度均衡", en: "Balanced depth" },
  "effort.high.note": { "zh-TW": "較慢但更完整", en: "Slower but more thorough" },
  "picker.footer.low": { "zh-TW": "最快、最輕", en: "Fastest, lightest" },
  "picker.footer.medium": { "zh-TW": "速度適中", en: "Balanced speed" },
  "picker.footer.high": { "zh-TW": "較慢但更完整", en: "Slower, more thorough" },
  "picker.done": { "zh-TW": "完成", en: "Done" },

  // Knowledge-search scope (KB surface) — renamed from "depth" so it no longer
  // collides with the model's thinking depth (#171). Key stays picker.depth.
  "picker.depth": { "zh-TW": "搜尋範圍", en: "Search scope" },
  "picker.advanced": { "zh-TW": "進階", en: "Advanced" },
  "depth.quick": { "zh-TW": "快速", en: "Quick" },
  "depth.standard": { "zh-TW": "標準", en: "Standard" },
  "depth.thorough": { "zh-TW": "徹底", en: "Thorough" },
  "depth.quick.note": {
    "zh-TW": "最快——直接用你的字詞搜尋",
    en: "Fastest — searches your words as-is",
  },
  "depth.standard.note": {
    "zh-TW": "輕度擴充查詢（建議）",
    en: "Light query expansion (recommended)",
  },
  "depth.thorough.note": {
    "zh-TW": "搜尋最廣——最慢、命中率最高",
    en: "Widest search — slowest, highest recall",
  },
  "depth.custom.note": {
    "zh-TW": "已自訂——選上方任一級別會覆蓋它。",
    en: "Customised — picking a level above replaces it.",
  },
  "depth.expand": { "zh-TW": "換句話多問幾種", en: "Alternative phrasings" },
  "depth.expand.title": {
    "zh-TW": "用不同說法多找一些相關文件（0＝關閉）",
    en: "Find more by rephrasing your question (0 = off)",
  },
  "depth.hyde": { "zh-TW": "先擬假設答案再搜", en: "Hypothetical-answer probes" },
  "depth.hyde.title": {
    "zh-TW": "先猜可能的答案，用它找更貼近的文件（0＝關閉）",
    en: "Draft a likely answer first to find closer matches (0 = off)",
  },
  "depth.rerank": { "zh-TW": "讓 AI 重新排序結果", en: "Let AI re-rank results" },
  "depth.rerank.title": {
    "zh-TW": "讓 AI 把最相關的結果排到前面",
    en: "Let AI move the most relevant results to the top",
  },
  "picker.wiki": { "zh-TW": "一併查知識百科", en: "Also search the wiki" },
  "picker.wiki.title": {
    "zh-TW": "同時參考 AI 維護的知識百科",
    en: "Also consult the AI-maintained wiki for this question",
  },

  // Agent activity entries (AgentEntryView): tool cards, reasoning, notices
  "tool.exec": { "zh-TW": "執行指令", en: "Run command" },
  "tool.read_file": { "zh-TW": "讀取檔案", en: "Read file" },
  "tool.read_image": { "zh-TW": "閱讀圖片", en: "Read image" },
  "tool.write_file": { "zh-TW": "寫入檔案", en: "Write file" },
  "tool.edit_file": { "zh-TW": "編輯檔案", en: "Edit file" },
  "tool.delete_file": { "zh-TW": "刪除檔案", en: "Delete file" },
  "tool.ask_knowledge_base": { "zh-TW": "查詢知識庫", en: "Ask the knowledge base" },
  "tool.kb_search": { "zh-TW": "搜尋知識庫", en: "Search the knowledge base" },
  "tool.search_wiki": { "zh-TW": "搜尋知識百科", en: "Search the wiki" },
  "tool.resolve_collection": { "zh-TW": "確認知識集", en: "Resolve collection" },
  "tool.lookup_glossary": { "zh-TW": "查詢詞彙", en: "Look up glossary" },
  "tool.update_context_card": { "zh-TW": "更新詞彙卡", en: "Update glossary card" },
  "tool.create_context_card": { "zh-TW": "新增詞彙卡", en: "Create glossary card" },
  "tool.read_new_source": { "zh-TW": "讀取新文件", en: "Read new source" },
  "tool.list_sources": { "zh-TW": "列出文件", en: "List sources" },
  "tool.read_source": { "zh-TW": "讀取文件", en: "Read source" },
  "tool.read_skill": { "zh-TW": "讀取技能", en: "Read skill" },
  "tool.fallback": { "zh-TW": "使用工具", en: "Using a tool" },
  "tool.argSep": { "zh-TW": "：", en: ": " },
  "tool.result": { "zh-TW": "結果", en: "Result" },
  "tool.running": { "zh-TW": "執行中…", en: "Running…" },
  // Caption over still-streaming tool output (#170) — so a half-written stdout
  // isn't mistaken for the final result.
  "tool.streamingHint": { "zh-TW": "即時輸出，可能未完成", en: "Live output — may be incomplete" },
  "entry.retry": { "zh-TW": "重試：", en: "Retry: " },
  "entry.sources": { "zh-TW": "來源", en: "Sources" },
  "reasoning.thinking": { "zh-TW": "思考中…", en: "Thinking…" },
  "reasoning.thought": { "zh-TW": "已思考", en: "Thought" },
  "repetition.answered": {
    "zh-TW": "偵測到模型重複輸出，已為你收尾。",
    en: "The model started repeating itself — wrapped up for you.",
  },
  "repetition.thinking": {
    "zh-TW": "模型在思考時陷入重複，已中止。",
    en: "The model looped while thinking — stopped.",
  },
  "mention.agent": { "zh-TW": "代理", en: "The agent" },
  "mention.summoned": { "zh-TW": "召喚了", en: "summoned" },
  "entry.replay": { "zh-TW": "重跑這一步", en: "Replay this step with the current AI" },
  "entry.undo": { "zh-TW": "復原這一回合（含之後）", en: "Undo this turn and everything after it" },

  // App launcher (Launcher)
  "launcher.appsEyebrow": { "zh-TW": "應用程式", en: "APPS" },
  "launcher.yourApps": { "zh-TW": "你的應用程式", en: "Your apps" },
  "launcher.noApps": { "zh-TW": "尚無應用程式。", en: "No apps yet." },
  // Empty-state guidance (#170): apps are code/team-provisioned, so explain that
  // and point at the knowledge base (always available below) as the next step.
  "launcher.empty.title": { "zh-TW": "尚無應用程式", en: "No apps yet" },
  "launcher.empty.body": {
    "zh-TW": "應用程式由團隊設定。你仍可前往下方的知識庫瀏覽文件、與資料對話。",
    en: "Apps are set up by your team. You can still open the knowledge base below to browse docs and chat with your data.",
  },
  "launcher.kb.title": { "zh-TW": "知識庫", en: "Knowledge Base" },
  "launcher.kb.desc": {
    "zh-TW": "共用文件、知識百科與知識庫對話。",
    en: "Shared docs, wikis, and the KB chat.",
  },

  // KB shell (KbHome)
  "kb.brand": { "zh-TW": "知識庫", en: "Knowledge base" },
  "kb.collections": { "zh-TW": "知識集", en: "Collections" },
  "kb.chats": { "zh-TW": "對話", en: "Chats" },
  "kb.conversations": { "zh-TW": "對話", en: "Conversations" },
  "kb.empty": {
    "zh-TW": "選擇一個對話，或開始新的對話。",
    en: "Select a conversation, or start a new one.",
  },

  // Collection page — index-status strip + "how answers are found" panel (#171,
  // de-jargoned from "Indexing" / "Retrieval modes").
  "kb.status.uploading": { "zh-TW": "上傳中…", en: "Uploading…" },
  "kb.status.indexing": { "zh-TW": "處理 {n} 份中…", en: "Processing {n}…" },
  "kb.status.failed": { "zh-TW": "{n} 份處理失敗", en: "{n} couldn’t be processed" },
  "kb.retrieval.title": { "zh-TW": "答案如何查詢", en: "How answers are found" },
  "kb.retrieval.close": { "zh-TW": "收合答案如何查詢", en: "Close how answers are found" },

  // Per-doc index state (KbDocIde status bar / tree badge / editor header) — #171.
  "kb.doc.ready": { "zh-TW": "就緒", en: "Ready" },
  "kb.doc.processing": { "zh-TW": "處理中…", en: "Processing…" },
  "kb.doc.failed": { "zh-TW": "失敗", en: "Failed" },
  "kb.doc.processingFailed": { "zh-TW": "處理失敗", en: "Processing failed" },

  // Retrieval toggles (RetrievalToggles, used by the new-collection modal +
  // collection settings) — #171.
  "kb.retrieval.docSearch": { "zh-TW": "文件搜尋", en: "Document search" },
  "kb.retrieval.docSearch.desc": {
    "zh-TW": "從你上傳的文件中找出段落來回答問題。",
    en: "Find passages from your documents to answer questions.",
  },
  "kb.retrieval.wiki": { "zh-TW": "知識百科", en: "Knowledge wiki" },
  "kb.retrieval.wiki.desc": {
    "zh-TW": "AI 建立、彼此連結的摘要，助理會讀它來回答；上傳後會更新。",
    en: "An AI-built, cross-linked summary the assistant reads to answer. Updates as you upload.",
  },
  "kb.retrieval.recommended": { "zh-TW": "建議", en: "Recommended" },
  "kb.retrieval.both": {
    "zh-TW": "兩者都會用——段落看細節、百科看全貌。",
    en: "Answers will draw on both — passages for detail, the wiki for the big picture.",
  },

  // Index-status strip — async progress feedback (#170), de-jargoned to match
  // #171's "processing" wording: per-file progress, an explicit "all set"
  // confirmation (fades), and a clickable failure list.
  "kb.status.uploadingProgress": { "zh-TW": "上傳 {done}/{total}", en: "Uploading {done}/{total}" },
  "kb.status.allReady": { "zh-TW": "✓ 全部就緒", en: "✓ All set" },
  "kb.status.openFailed": { "zh-TW": "查看 {name} 的失敗原因", en: "View why {name} failed" },

  // Workflow review gate (WorkflowDecisionCard) — make "it's your turn" loud (#170)
  "wf.decision.cue": { "zh-TW": "需要你的決定", en: "Your decision needed" },
  "wf.decision.titleFallback": { "zh-TW": "需要你的決定", en: "Awaiting your decision" },

  // Agent run banners (agentLog reducer) — de-jargoned behavior descriptions
  "banner.sandboxIdle": {
    "zh-TW": "閒置太久，下次操作會重新啟動執行環境。",
    en: "Idle too long — the execution environment will restart on your next action.",
  },
  "banner.maxTurns": {
    "zh-TW": "已達回合上限（{turns}），對話已停止。",
    en: "Reached the turn limit ({turns}); the conversation stopped.",
  },
  "banner.cancelled": { "zh-TW": "已取消。", en: "Cancelled." },

  // Investigation terminal (TerminalPane) — sandbox → 執行環境 (#171).
  "terminal.help.lead": {
    "zh-TW": "在執行環境裡執行指令，試試",
    en: "Run shell commands in the execution environment. Try",
  },
  "terminal.help.clears": { "zh-TW": "可清除畫面。", en: "clears." },
  "replay.showThinking": { "zh-TW": "顯示思考", en: "Show thinking" },
  "replay.hideThinking": { "zh-TW": "隱藏思考", en: "Hide thinking" },
  "terminal.aborted": {
    "zh-TW": "^C 已中斷（指令仍在執行環境裡跑到結束）",
    en: "^C  interrupted (still running in the execution environment until it exits)",
  },

  // KB collection landing + in-place concept help (#173)
  "kb.lead": {
    "zh-TW": "每個集合是一組文件，AI 回答時可參考。挑選對話要用哪些當參考資料。",
    en: "Each collection is a set of documents the assistant can draw on. Pick which to use as context when chatting.",
  },
  // The collection page's collapsible "what's in here" orientation strip.
  "kb.col.overview.title": { "zh-TW": "這個集合裡有什麼", en: "What's in here" },
  "kb.col.overview.expand": { "zh-TW": "這些分頁是什麼", en: "What are these tabs?" },
  "kb.col.overview.collapse": { "zh-TW": "收合", en: "Collapse" },
  "kb.tab.documents": { "zh-TW": "文件", en: "Documents" },
  "kb.tab.cards": { "zh-TW": "詞彙表", en: "Glossary" },
  "kb.tab.wiki": { "zh-TW": "Wiki", en: "Wiki" },
  "kb.tab.documents.blurb": {
    "zh-TW": "你上傳的檔案。AI 搜尋會讀這些來回答。",
    en: "The files you upload. Search reads these to answer.",
  },
  "kb.tab.cards.blurb": {
    "zh-TW": "你親手寫的詞彙表——AI 遇到這些詞會照你的定義使用。",
    en: "A glossary you write — the assistant uses your wording when these terms come up.",
  },
  "kb.tab.wiki.blurb": {
    "zh-TW": "AI 自動整理、互相連結的全集摘要；上傳新文件會跟著更新。",
    en: "An AI-built, cross-linked summary; updates as you upload.",
  },
  // Glossary (Context Cards) tab empty states.
  "kb.cards.empty.none": {
    "zh-TW": "還沒有詞彙卡。詞彙表讓你定義 AI 該照字面使用的詞——例如「COGS 一律指 Cost of Goods Sold」。用左側「＋ 新增」開始。",
    en: 'No glossary cards yet. A glossary defines terms the assistant should use verbatim — e.g. "COGS always means Cost of Goods Sold." Use "+ New" on the left to start.',
  },
  "kb.cards.empty.unselected": {
    "zh-TW": "選一張詞彙卡，或新增一張。",
    en: "Select a glossary card, or create a new one.",
  },
  // Wiki: AI-written + editable badge, and the rebuild confirmation.
  "kb.wiki.badge": { "zh-TW": "AI 撰寫，可編輯", en: "AI-written, editable" },
  "kb.wiki.rebuild.confirm": {
    "zh-TW": "重建會依文件重新整理頁面，AI 可能改寫你手動編輯過的頁面（不會刪除任何頁面）。要繼續嗎？",
    en: "Rebuild refreshes pages from the documents and may rewrite pages you've edited (no pages are deleted). Continue?",
  },
  "kb.wiki.rebuild.confirm.go": { "zh-TW": "重建", en: "Rebuild" },
  "kb.wiki.rebuild.confirm.cancel": { "zh-TW": "取消", en: "Cancel" },
} satisfies Record<string, Entry>;

export type MsgKey = keyof typeof messages;

export type Vars = Record<string, string | number>;

/** Look up a message in `locale`, substituting any `{name}` placeholders. */
export function translate(locale: Locale, key: MsgKey, vars?: Vars): string {
  let out = messages[key][locale];
  if (vars) {
    for (const [name, value] of Object.entries(vars)) {
      out = out.split(`{${name}}`).join(String(value));
    }
  }
  return out;
}

/** Pick a locale from a BCP-47 tag (e.g. `navigator.language`): any `zh*`
 * stays Traditional Chinese, any other recognised tag is English, and an
 * absent/blank tag falls back to zh-TW (the primary audience). */
export function detectLocale(lang: string | undefined): Locale {
  if (!lang) return "zh-TW";
  return lang.toLowerCase().startsWith("zh") ? "zh-TW" : "en";
}

const STORE_KEY = "ws.locale";
const LOCALES: Locale[] = ["zh-TW", "en"];

/** The user's sticky locale override, or null if they've never picked one. */
export function getStoredLocale(): Locale | null {
  try {
    const v = localStorage.getItem(STORE_KEY);
    return v && (LOCALES as string[]).includes(v) ? (v as Locale) : null;
  } catch {
    return null;
  }
}

export function setStoredLocale(locale: Locale): void {
  try {
    localStorage.setItem(STORE_KEY, locale);
  } catch {
    /* localStorage unavailable (private mode / SSR) — choice just isn't sticky */
  }
}

/** The locale to start with: the stored override wins; otherwise detect from
 * the browser. */
export function initialLocale(): Locale {
  return (
    getStoredLocale() ??
    detectLocale(typeof navigator === "undefined" ? undefined : navigator.language)
  );
}

type LocaleCtx = { locale: Locale; setLocale: (locale: Locale) => void };

// Default value lets `useT()` work outside a provider (untouched components,
// isolated unit tests) — it renders zh-TW and `setLocale` is a no-op.
const LocaleContext = createContext<LocaleCtx>({ locale: "zh-TW", setLocale: () => {} });

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);
  const setLocale = useCallback((next: Locale) => {
    setStoredLocale(next);
    setLocaleState(next);
  }, []);
  return <LocaleContext.Provider value={{ locale, setLocale }}>{children}</LocaleContext.Provider>;
}

/** The active locale and a sticky setter. */
export function useLocale(): [Locale, (locale: Locale) => void] {
  const { locale, setLocale } = useContext(LocaleContext);
  return [locale, setLocale];
}

/** `t(key, vars?)` bound to the active locale. */
export function useT(): (key: MsgKey, vars?: Vars) => string {
  const { locale } = useContext(LocaleContext);
  return useCallback((key: MsgKey, vars?: Vars) => translate(locale, key, vars), [locale]);
}
