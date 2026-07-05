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
  // Settings panel (global, GlobalNav gear)
  "settings.title": { "zh-TW": "設定", en: "Settings" },
  "settings.close": { "zh-TW": "關閉", en: "Close" },
  "settings.fontsize": { "zh-TW": "字體大小", en: "Text size" },
  "settings.fontsize.note": {
    "zh-TW": "調整介面文字大小；其餘版面維持不變。",
    en: "Scales the interface text; the rest of the layout stays put.",
  },
  "settings.fontsize.reset": { "zh-TW": "重置為預設大小", en: "Reset to default size" },
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
  "about.signin.value": { "zh-TW": "示範模式（尚未接 SSO）", en: "Demo mode (SSO not configured)" },
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
  // #334: per-message cap on how many times this reply searches the KB.
  "searchmax.label": { "zh-TW": "最多搜尋次數", en: "Max searches" },
  "searchmax.title": {
    "zh-TW": "這則回覆最多搜尋知識庫幾次（0＝不搜尋，直接作答）",
    en: "How many times this reply may search the KB (0 = don't search, answer directly)",
  },
  "searchmax.zero": { "zh-TW": "0＝不搜尋", en: "0 = no search" },
  "searchmax.dec": { "zh-TW": "減少搜尋次數", en: "Fewer searches" },
  "searchmax.inc": { "zh-TW": "增加搜尋次數", en: "More searches" },

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
  // #254 — citation source-location chip labels. The formatter adds one space
  // before the value, so labels carry none (the i18n layer has no interpolation).
  "cite.loc.page": { "zh-TW": "頁碼", en: "p." },
  "cite.loc.slide": { "zh-TW": "投影片", en: "Slide" },
  "cite.loc.sheet": { "zh-TW": "工作表", en: "Sheet" },
  "cite.loc.line": { "zh-TW": "行", en: "Line" },
  "cite.loc.row": { "zh-TW": "列", en: "Row" },
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
  // Compact label revealed on hover/focus of the undo control (#172).
  "entry.undo.label": { "zh-TW": "復原此回合之後", en: "Undo this turn onward" },
  // #397: report a wrong answer so the wiki gets corrected.
  "entry.reportWiki": { "zh-TW": "回報有誤", en: "Report a wiki error in this answer" },

  // #397: wiki-correction dialog ("回報有誤")
  "wikiCorrection.title": { "zh-TW": "回報 wiki 有誤", en: "Report a wiki error" },
  "wikiCorrection.intro": {
    "zh-TW": "告訴維護者哪裡錯、應該怎樣，AI 會去修正 wiki（你不用自己改）。",
    en: "Tell the maintainer what's wrong and how it should read; the AI corrects the wiki for you.",
  },
  "wikiCorrection.generate": { "zh-TW": "AI 幫我草擬", en: "Draft with AI" },
  "wikiCorrection.generating": { "zh-TW": "草擬中…", en: "Drafting…" },
  "wikiCorrection.instructionLabel": { "zh-TW": "哪裡錯 / 應該怎樣", en: "What's wrong / how it should read" },
  "wikiCorrection.instructionPlaceholder": {
    "zh-TW": "例如：Foo 成立於 1998 年，不是 1989 年。（可留空按「AI 幫我草擬」）",
    en: "e.g. Foo was founded in 1998, not 1989. (Leave blank and click “Draft with AI”.)",
  },
  "wikiCorrection.targetLabel": { "zh-TW": "wiki 頁面（可選）", en: "Wiki page (optional)" },
  "wikiCorrection.targetPlaceholder": { "zh-TW": "/entities/foo.md（留空讓維護者自己找）", en: "/entities/foo.md (blank = let the maintainer find it)" },
  "wikiCorrection.questionsIntro": {
    "zh-TW": "AI 需要多一點資訊，請回答：",
    en: "The AI needs a little more to go on:",
  },
  "wikiCorrection.submit": { "zh-TW": "送出修正", en: "Submit correction" },
  "wikiCorrection.submitting": { "zh-TW": "送出中…", en: "Submitting…" },
  "wikiCorrection.cancel": { "zh-TW": "取消", en: "Cancel" },
  "wikiCorrection.done": { "zh-TW": "已送出，wiki 更新中。", en: "Submitted — the wiki is being updated." },
  "wikiCorrection.error": { "zh-TW": "送出失敗，請再試一次。", en: "Couldn't submit — please try again." },

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
  "launcher.help.title": { "zh-TW": "說明", en: "Help" },
  "launcher.help.desc": {
    "zh-TW": "使用說明、更新紀錄,以及問 AI 怎麼用。",
    en: "Usage guides, release notes, and ask the AI how to use it.",
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

  // #356 Tune parsing modal — user-facing framing is "adjust how the AI reads
  // this document" (root cause ③: no "parse"/"parsing" jargon in the entry).
  "kb.tuneParsing.button": { "zh-TW": "調整解讀方式", en: "Adjust reading" },
  "kb.tuneParsing.buttonTitle": {
    "zh-TW": "調整 AI 解讀這份文件的方式,再試答看看",
    en: "Adjust how the AI reads this document, then try answering",
  },
  "kb.tuneParsing.title": { "zh-TW": "調整解讀方式", en: "Adjust reading" },
  "kb.tuneParsing.description": {
    "zh-TW": "打一個使用者會問的問題,看這份文件在檢索中排到多深(會排得比使用者實際看到的更深,所以埋很深的段落會顯示成像「#37」而不只是「找不到」)。編輯下面的解讀指引後可重新解讀、並「試答」這題;只套用到這份文件,或套用到整個 collection。套用前不會寫入任何東西。",
    en: 'Type a question your users would ask and see how deep this document ranks for it — we rank far past the top results a user sees, so a buried passage reads as "#37" rather than just "absent". Edit the reading instructions below, re-read, and "Try answer" the question; save it for this document only, or apply it to the whole collection. Nothing is written until you apply.',
  },
  "kb.tuneParsing.close": { "zh-TW": "關閉", en: "Close" },
  "kb.tuneParsing.question": { "zh-TW": "問題", en: "Question" },
  "kb.tuneParsing.questionPlaceholder": {
    "zh-TW": "例如:焊接空洞的根本原因是什麼?",
    en: "e.g. what is the root cause of the solder void?",
  },
  "kb.tuneParsing.k": { "zh-TW": "前 k 段 (k={k})", en: "Top-k (k={k})" },
  "kb.tuneParsing.kTitle": {
    "zh-TW": "排名前 k 段才算「使用者看得到」、才會進入試答的 context;同時決定排名高亮。",
    en: 'The top-k passages count as "what a user sees" and feed Try answer; also drives the rank highlight.',
  },
  "kb.tuneParsing.checkRanks": { "zh-TW": "檢查排名", en: "Check ranks" },
  "kb.tuneParsing.reparse": { "zh-TW": "用指引重新解讀", en: "Re-read with these instructions" },
  "kb.tuneParsing.reparseTitle": {
    "zh-TW": "用下面的指引讓 AI 重新解讀這份文件(可能要一下子)",
    en: "Have the AI re-read this document with the instructions below (may take a moment)",
  },
  "kb.tuneParsing.guidance": { "zh-TW": "解讀指引", en: "Reading instructions" },
  "kb.tuneParsing.guidancePlaceholder": {
    "zh-TW": "例如:看到魚骨圖就輸出 JSON;看到表格就輸出 Markdown。",
    en: "e.g. If you see a fishbone diagram, emit JSON; a table, emit Markdown.",
  },
  "kb.tuneParsing.sourceCustom": { "zh-TW": "此文件:專屬設定", en: "This document: custom override" },
  "kb.tuneParsing.sourceInherited": {
    "zh-TW": "此文件:繼承自 collection",
    en: "This document: inherited from collection",
  },
  "kb.tuneParsing.before": { "zh-TW": "現況", en: "Before (current)" },
  "kb.tuneParsing.after": { "zh-TW": "套用新指引後", en: "After (these instructions)" },
  "kb.tuneParsing.tryAnswer": { "zh-TW": "試答", en: "Try answer" },
  "kb.tuneParsing.bestRank": { "zh-TW": "最佳 #{rank}", en: "best #{rank}" },
  "kb.tuneParsing.notInTop": { "zh-TW": "不在前 {k} 名", en: "not in top {k}" },
  "kb.tuneParsing.saveDoc": { "zh-TW": "只套用到這份文件", en: "Save for this document" },
  "kb.tuneParsing.applyCollection": {
    "zh-TW": "套用到整個 collection",
    en: "Apply to whole collection",
  },
  "kb.tuneParsing.applyConfirm": {
    "zh-TW": "這會改變整個 collection 之後所有文件的解讀方式。確定要套用嗎?",
    en: "This changes how every document in the collection is read on the next re-read. Continue?",
  },
  "kb.tuneParsing.clearOverride": { "zh-TW": "清除專屬設定", en: "Clear document override" },
  "kb.tuneParsing.savedNudge": {
    "zh-TW": "已儲存,尚未生效 — 讓 AI 重新讀取後才會套用。",
    en: "Saved — not in effect yet; re-read to apply.",
  },
  "kb.tuneParsing.reindexDoc": { "zh-TW": "重新讀取這份文件", en: "Re-read this document" },
  "kb.tuneParsing.running": { "zh-TW": "執行中…", en: "running…" },
  "kb.tuneParsing.applied": { "zh-TW": "已套用", en: "Applied" },
  "kb.tuneParsing.probeFailed": { "zh-TW": "探測失敗", en: "probe failed" },
  "kb.tuneParsing.answerFailed": { "zh-TW": "回答失敗", en: "answer failed" },
  "kb.tuneParsing.emptyForQuestion": {
    "zh-TW": "在前 {k} 名內,這份文件沒有任何段落 — 使用者問這題時用不到它(這就是要修的訊號)。",
    en: "This document has no passage in the top {k} for the question — it won't help a user asking it (the red flag to fix).",
  },

  // #105: document quality (badge + status-bar verdict + rubric editor).
  "kb.quality.good": { "zh-TW": "品質良好", en: "Good quality" },
  "kb.quality.ok": { "zh-TW": "品質普通", en: "Fair quality" },
  "kb.quality.bad": { "zh-TW": "品質偏低", en: "Low quality" },
  "kb.quality.badge": {
    "zh-TW": "品質 {score}/100 · {label}",
    en: "Quality {score}/100 · {label}",
  },
  "kb.quality.heading": { "zh-TW": "品質", en: "Quality" },
  "kb.quality.unscored": { "zh-TW": "尚未評分", en: "Not yet scored" },
  "kb.quality.rubric.title": { "zh-TW": "品質評分標準", en: "Quality rubric" },
  "kb.quality.rubric.hint": {
    "zh-TW": "用一段文字說明：什麼樣的文件算好/壞的知識來源、要從哪些面向評。留空＝不評分，搜尋排序不受影響。",
    en: "Describe what makes a document a good/bad knowledge source here, and which dimensions to assess. Leave blank to turn scoring off (search ranking is unaffected).",
  },
  "kb.quality.rubric.placeholder": {
    "zh-TW": "例如：評這份缺陷報告作為知識來源的品質，面向：清晰度、完整度、雜訊。",
    en: "e.g. Judge this defect report as a knowledge source. Dimensions: clarity, completeness, noise.",
  },
  "kb.quality.rubric.save": { "zh-TW": "儲存評分標準", en: "Save rubric" },
  "kb.quality.rubric.saved": { "zh-TW": "已儲存", en: "Saved" },

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
  "kb.status.retryFailed": { "zh-TW": "重試", en: "Retry" },
  // #325: files refused at upload (encrypted/unreadable) — distinct from a
  // background processing failure: there's no doc to open, just a reason +
  // what to do about it.
  "kb.status.cantAccept": {
    "zh-TW": "{n} 份無法接受",
    en: "{n} couldn’t be accepted",
  },
  "kb.status.cantAcceptDismiss": { "zh-TW": "知道了", en: "Dismiss" },
  "kb.upload.blocked.unreadable": {
    "zh-TW": "這個檔案打不開，可能已加密或損壞。若有設密碼，請先解密再上傳。",
    en: "We couldn’t open this file — it may be encrypted or damaged. If it’s password-protected, remove the password and upload again.",
  },

  // Workflow review gate (WorkflowDecisionCard) — make "it's your turn" loud (#170)
  "wf.decision.cue": { "zh-TW": "需要你的決定", en: "Your decision needed" },
  "wf.decision.titleFallback": { "zh-TW": "需要你的決定", en: "Awaiting your decision" },

  // #205 — context-card diff review (before approving an overwrite)
  "cardDiff.view": { "zh-TW": "查看變更", en: "View changes" },
  "cardDiff.title": { "zh-TW": "檢查卡片變更", en: "Review card changes" },
  "cardDiff.current": { "zh-TW": "目前（唯讀）", en: "Current (read-only)" },
  "cardDiff.proposed": { "zh-TW": "提案（可編輯）", en: "Proposed (editable)" },
  "cardDiff.hint": {
    "zh-TW": "左為現有卡片、右為將寫入的版本，可直接修改右側再核准。",
    en: "Left is the existing card, right is what will be saved — edit the right side, then approve.",
  },
  "cardDiff.empty": {
    "zh-TW": "沒有要檢查的卡片變更。",
    en: "No card changes to review.",
  },
  "cardDiff.loading": { "zh-TW": "載入變更中…", en: "Loading changes…" },
  "cardDiff.close": { "zh-TW": "關閉", en: "Close" },
  "cardDiff.allNew": {
    "zh-TW": "全部都是新增卡片（沒有會被覆寫的既有卡片）。",
    en: "All cards are new — nothing existing will be overwritten.",
  },

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

  // App dashboard (AppDashboard) — filter strip (#172)
  "dash.clearFilters": { "zh-TW": "清除篩選", en: "Clear filters" },

  // Global nav (GlobalNav) — make the switcher + brand legible (#172)
  "nav.switch": { "zh-TW": "切換", en: "Switch" },
  "nav.switch.tip": {
    "zh-TW": "切換 App、知識庫或診斷",
    en: "Switch app, knowledge base, or diagnostics",
  },
  "nav.home": { "zh-TW": "回首頁", en: "Home" },

  // Topic Hub collection-set picker entry (CollectionsButton) — frame it as the
  // agent's search scope, not a generic count (#172).
  "collections.set": { "zh-TW": "設定搜尋範圍", en: "Set search scope" },
  "collections.scope": { "zh-TW": "搜尋範圍 · {n}", en: "Search scope · {n}" },
  "collections.tip": {
    "zh-TW": "AI 回答時會在這些知識集裡找資料",
    en: "The AI searches these collections when answering",
  },

  // Shared collections checklist (CollectionsChecklist) — used by both the
  // topic-hub picker modal and the KB chat collection modal (#271).
  "collections.search": { "zh-TW": "搜尋知識庫…", en: "Search collections…" },
  "collections.docCount": { "zh-TW": "{n} 份", en: "{n} docs" },
  "collections.selectAll": { "zh-TW": "全選", en: "Select all" },
  "collections.clear": { "zh-TW": "清除", en: "Clear" },
  "collections.noMatch": {
    "zh-TW": "沒有符合「{q}」的知識庫。",
    en: "No collections match “{q}”.",
  },
  "collections.none": { "zh-TW": "目前沒有任何知識庫可選。", en: "No collections to choose from." },
  // #298 — the Skills panel (co-created skills in a workspace).
  "skills.button": { "zh-TW": "技能", en: "Skills" },
  "skills.tip": {
    "zh-TW": "查看技能、開啟關閉，或讓助理這回合套用",
    en: "See skills, turn them on or off, or apply one for this turn",
  },
  "skills.title": { "zh-TW": "技能", en: "Skills" },
  "skills.intro": {
    "zh-TW": "開啟或關閉助理能用的技能，或選「套用」讓它這回合照著某個技能做。",
    en: "Turn the assistant's skills on or off, or Apply one to have it follow that skill this turn.",
  },
  "skills.empty": {
    "zh-TW": "還沒有技能。在對話中請助理「幫我做一個技能」就能一起做一個。",
    en: 'No skills yet. Ask the assistant "help me make a skill" to build one together.',
  },
  "skills.download": { "zh-TW": "下載", en: "Download" },
  "skills.import": { "zh-TW": "匯入", en: "Import" },
  "skills.importHint": {
    "zh-TW": "選擇一個技能資料夾以加入這個工作區",
    en: "Pick a skill folder to add it to this workspace",
  },
  "skills.close": { "zh-TW": "關閉", en: "Close" },
  "skills.apply": { "zh-TW": "套用", en: "Apply" },
  "skills.applyTip": {
    "zh-TW": "這回合讓助理套用此技能",
    en: "Apply this skill for the assistant's next turn",
  },
  "skills.save": { "zh-TW": "儲存", en: "Save" },
  "skills.applied": { "zh-TW": "這回合套用：", en: "Applying:" },
  "skills.appliedTip": {
    "zh-TW": "助理這回合會照這個技能做（送出後自動移除）",
    en: "The assistant follows this skill this turn (removed after you send)",
  },
  // #323 — the Workflows panel (co-created workflows in a workspace).
  "workflows.button": { "zh-TW": "工作流程", en: "Workflows" },
  "workflows.tip": {
    "zh-TW": "查看、執行與下載你和助理一起做的工作流程",
    en: "See, run, and download the workflows you built with the assistant",
  },
  "workflows.title": { "zh-TW": "這個工作區的工作流程", en: "Workflows in this workspace" },
  "workflows.intro": {
    "zh-TW": "工作流程是你和助理一起定下、可以重複執行的步驟。",
    en: "A workflow is a repeatable set of steps you built with the assistant.",
  },
  "workflows.empty": {
    "zh-TW": "還沒有工作流程。在對話中請助理「幫我做一個工作流程」就能一起做一個。",
    en: 'No workflows yet. Ask the assistant "help me make a workflow" to build one together.',
  },
  "workflows.run": { "zh-TW": "執行", en: "Run" },
  "workflows.download": { "zh-TW": "下載全部", en: "Download all" },
  "workflows.import": { "zh-TW": "匯入", en: "Import" },
  "workflows.importHint": {
    "zh-TW": "選擇一個 .json 工作流程檔以加入這個工作區",
    en: "Pick a .json workflow file to add it to this workspace",
  },
  "workflows.close": { "zh-TW": "關閉", en: "Close" },
  "workflows.steps": { "zh-TW": "{n} 個步驟", en: "{n} steps" },
  // KB chat collection modal (KbCollectionsModal) — #271.
  "collections.more": { "zh-TW": "更多 · {n}", en: "More · {n}" },
  "collections.kbTitle": { "zh-TW": "在哪些知識庫裡搜尋", en: "Which collections to search" },
  "collections.kbDesc": {
    "zh-TW": "勾選這次對話要搜尋的知識庫；預設是你最常用的幾個。",
    en: "Pick the collections this chat searches; defaults to the ones you use most.",
  },

  // KB collection page (KbCollectionPage) (#172)
  "kb.reindexAll": { "zh-TW": "全部重新讀取", en: "Re-read all" },
  "kb.uploadFiles": { "zh-TW": "上傳檔案", en: "Upload files" },
  "kb.uploadFolder": { "zh-TW": "上傳資料夾", en: "Upload folder" },
  "kb.dropToUpload": { "zh-TW": "放開以上傳", en: "Drop to upload" },
  "kb.dropHint": { "zh-TW": "把檔案拖到這裡開始", en: "Drag files here to start" },

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
  "kb.tab.review": { "zh-TW": "待審核", en: "Review" },
  "kb.tab.review.blurb": {
    "zh-TW": "自動生成的卡片提案等你審核；套用後才會進入詞彙表。",
    en: "Auto-generated card proposals awaiting your review; applied ones join the glossary.",
  },
  // Review inbox shell (CollectionReviewTab) — was hardcoded zh-TW (#456).
  "kb.review.subtitle": {
    "zh-TW": "自動生成的卡片提案與待釐清的問題；審核後套用、回答或略過。",
    en: "Auto-generated card proposals and open questions to clarify — apply, answer, or skip after review.",
  },
  "kb.review.empty": {
    "zh-TW": "目前沒有待審核項目。",
    en: "Nothing to review right now.",
  },
  // Glossary auto-generate controls — were hardcoded zh-TW (#456).
  "kb.cards.autogen": { "zh-TW": "⚡ 自動生成", en: "⚡ Auto-generate" },
  "kb.cards.autogen.count": { "zh-TW": "自動生成（{n}）", en: "Auto-generate ({n})" },
  // Glossary (Context Cards) tab empty states.
  "kb.cards.empty.none": {
    "zh-TW": "還沒有詞彙卡。詞彙表讓你定義 AI 該照字面使用的詞——例如「COGS 一律指 Cost of Goods Sold」。用左側「＋ 新增」開始。",
    en: 'No glossary cards yet. A glossary defines terms the assistant should use verbatim — e.g. "COGS always means Cost of Goods Sold." Use "+ New" on the left to start.',
  },
  "kb.cards.empty.unselected": {
    "zh-TW": "選一張詞彙卡，或新增一張。",
    en: "Select a glossary card, or create a new one.",
  },
  // #377: the global clarification-question inbox.
  "docq.title": { "zh-TW": "待釐清", en: "Clarifications" },
  "docq.subtitle": {
    "zh-TW": "AI 讀文件時看不懂、需要你補充的地方。回答後：名詞會寫成詞彙卡，段落說明會寫進 wiki。",
    en: "Things the assistant couldn't understand while reading your documents. Your answers become glossary cards (terms) or wiki notes (passages).",
  },
  "docq.empty": { "zh-TW": "目前沒有待釐清的問題。", en: "Nothing to clarify right now." },
  "docq.kind.term": { "zh-TW": "名詞", en: "Term" },
  "docq.kind.description": { "zh-TW": "段落", en: "Passage" },
  "docq.sources": { "zh-TW": "{n} 份文件提到", en: "raised in {n} document(s)" },
  "docq.answerPlaceholder": { "zh-TW": "輸入你的答案…", en: "Type your answer…" },
  "docq.answer": { "zh-TW": "送出", en: "Submit" },
  "docq.discard": { "zh-TW": "丟棄", en: "Discard" },
  // Wiki: AI-written + editable badge, and the rebuild confirmation.
  "kb.wiki.badge": { "zh-TW": "AI 撰寫，可編輯", en: "AI-written, editable" },
  "kb.wiki.rebuild.confirm": {
    "zh-TW": "重建會依文件重新整理頁面，AI 可能改寫你手動編輯過的頁面（不會刪除任何頁面）。要繼續嗎？",
    en: "Rebuild refreshes pages from the documents and may rewrite pages you've edited (no pages are deleted). Continue?",
  },
  "kb.wiki.rebuild.confirm.go": { "zh-TW": "重建", en: "Rebuild" },
  "kb.wiki.rebuild.confirm.cancel": { "zh-TW": "取消", en: "Cancel" },

  // #245: workspace storage usage bar + over-quota upload error.
  "workspace.usage": {
    "zh-TW": "已使用 {used} / {quota}",
    en: "{used} of {quota} used",
  },
  "workspace.usage.full": {
    "zh-TW": "空間已滿——請先刪除一些檔案再上傳。",
    en: "Storage is full — delete some files before uploading more.",
  },
  "workspace.overQuota": {
    "zh-TW": "空間不足，未能上傳:{names}",
    en: "Out of space, not uploaded: {names}",
  },

  // #283: workflow launch pre-flight dialog + progress views.
  "wf.launch.title": { "zh-TW": "執行前確認", en: "Before you run" },
  "wf.launch.steps": { "zh-TW": "步驟", en: "Steps" },
  "wf.launch.checklist": { "zh-TW": "開始前檢查", en: "Pre-flight checks" },
  "wf.launch.loading": { "zh-TW": "檢查中…", en: "Checking…" },
  "wf.launch.error": { "zh-TW": "無法載入預覽，請稍後再試。", en: "Couldn’t load the preview — try again." },
  "wf.launch.run": { "zh-TW": "開始執行", en: "Run" },
  "wf.launch.cancel": { "zh-TW": "取消", en: "Cancel" },
  "wf.launch.blocked": {
    "zh-TW": "尚未具備執行條件——請先處理上方標示的項目。",
    en: "Not ready to run — resolve the flagged items above first.",
  },
  "wf.launch.required": { "zh-TW": "必要", en: "Required" },
  "wf.launch.advisory": { "zh-TW": "提醒", en: "Heads-up" },
  // #343: launch a workflow in the CURRENT chat (takeover), after preparing in it.
  "wf.launchHere.trigger": { "zh-TW": "在此對話執行", en: "Run in this chat" },
  "wf.view.steps": { "zh-TW": "步驟清單", en: "Steps" },
  "wf.view.timeline": { "zh-TW": "時間軸", en: "Timeline" },
  "wf.timeline.now": { "zh-TW": "回到現在", en: "Jump to now" },
  "wf.timeline.zoomIn": { "zh-TW": "放大", en: "Zoom in" },
  "wf.timeline.zoomOut": { "zh-TW": "縮小", en: "Zoom out" },
  "wf.timeline.waited": { "zh-TW": "等待 {mins} 分", en: "waited {mins}m" },
  "wf.timeline.empty": {
    "zh-TW": "尚無已計時的步驟。",
    en: "No timed steps yet.",
  },
  "wf.metrics.elapsed": { "zh-TW": "經過", en: "Elapsed" },
  "wf.metrics.steps": { "zh-TW": "步驟", en: "Steps" },
  "wf.metrics.retries": { "zh-TW": "重試 {n}", en: "{n} retries" },
  "wf.runs.title": { "zh-TW": "執行紀錄", en: "Runs" },
  "wf.progress.expand": { "zh-TW": "展開細節", en: "Show details" },
  "wf.progress.collapse": { "zh-TW": "收合細節", en: "Hide details" },
  "wf.progress.step": { "zh-TW": "第 {n} 步 · {title}", en: "step {n} · {title}" },
  "wf.progress.noop": { "zh-TW": "已完成，但未執行任何步驟", en: "Finished without running any steps" },
  "wf.stop": { "zh-TW": "停止", en: "Stop" },
  "wf.disconnected": {
    "zh-TW": "連線中斷，可能已停止。正在嘗試重新連線…",
    en: "Connection lost — it may have stopped. Reconnecting…",
  },
  "wf.status.pending": { "zh-TW": "排隊中", en: "queued" },
  "wf.status.running": { "zh-TW": "進行中", en: "running" },
  "wf.status.awaiting_human": { "zh-TW": "等待你的決定", en: "awaiting you" },
  "wf.status.done": { "zh-TW": "已完成", en: "done" },
  "wf.status.error": { "zh-TW": "失敗", en: "failed" },
  "wf.status.cancelled": { "zh-TW": "已取消", en: "cancelled" },

  // #230: the platform Help page.
  "help.title": { "zh-TW": "說明", en: "Help" },
  "help.intro": {
    "zh-TW": "使用說明、更新紀錄,以及一個能回答你「怎麼用」的 AI 助手。",
    en: "Usage guides, release notes, and an AI that answers your how-to questions.",
  },
  "help.guides": { "zh-TW": "使用說明", en: "Guides" },
  "help.releaseNotes": { "zh-TW": "更新紀錄", en: "Release notes" },
  "help.ask": { "zh-TW": "問 AI", en: "Ask the AI" },
  "help.ask.note": {
    "zh-TW": "依使用說明與更新紀錄回答,並附上引用。",
    en: "Answers from the guides and release notes, with citations.",
  },
  "help.empty": {
    "zh-TW": "尚無說明文件。",
    en: "No help documents yet.",
  },

  // #322: per-item tool picker — the AgentHeader button + the picker modal +
  // the per-tool tri-state control (Default / On / Off).
  "tools.button": { "zh-TW": "工具", en: "Tools" },
  "tools.button.tip": {
    "zh-TW": "選擇這個工作區的助理能用哪些工具",
    en: "Choose which tools the assistant can use in this workspace",
  },
  "tools.title": { "zh-TW": "助理可用的工具", en: "Tools the assistant can use" },
  "tools.desc": {
    "zh-TW": "預設沿用範本設定。把任一工具改成「開啟」或「關閉」只會影響這個工作區；其餘維持「預設」會跟著範本變動。",
    en: "Defaults follow the template. Set a tool to On or Off to override it for this workspace only; the rest stay on Default and follow the template.",
  },
  "tools.search": { "zh-TW": "搜尋工具…", en: "Search tools…" },
  "tools.resetVisible": { "zh-TW": "全部回到預設", en: "Reset to defaults" },
  "tools.follow": { "zh-TW": "預設", en: "Default" },
  "tools.on": { "zh-TW": "開啟", en: "On" },
  "tools.off": { "zh-TW": "關閉", en: "Off" },
  "tools.state.aria": { "zh-TW": "{tool} 的設定", en: "Setting for {tool}" },
  "tools.defaultOn": { "zh-TW": "預設開啟", en: "On by default" },
  "tools.defaultOff": { "zh-TW": "預設關閉", en: "Off by default" },
  "tools.noMatch": { "zh-TW": "沒有符合「{q}」的工具。", en: "No tools match “{q}”." },
  "tools.none": { "zh-TW": "這個工作區沒有可選的工具。", en: "No tools to choose from." },
  "tools.save": { "zh-TW": "儲存", en: "Save" },
  "tools.cancel": { "zh-TW": "取消", en: "Cancel" },
  "tools.loading": { "zh-TW": "載入工具中…", en: "Loading tools…" },
  "tools.discard": {
    "zh-TW": "尚未儲存的變更會遺失，要關閉嗎？",
    en: "Discard unsaved changes?",
  },

  // Diagnostics page (#465) — AI health checks + model sanity + activity.
  "diag.crumb": { "zh-TW": "診斷", en: "Diagnostics" },
  "diag.back": { "zh-TW": "返回", en: "Back" },
  "diag.title": { "zh-TW": "AI 診斷", en: "AI diagnostics" },
  "diag.subtitle": {
    "zh-TW":
      "快速檢查支撐這個工作區的 AI 功能是否正常回應並完成任務。這裡的警告不會擋住你，只是說明為什麼有些地方看起來怪怪的。",
    en: "Quick checks that verify the AI features behind this workspace are responding and doing their jobs. A warning here never blocks you — it explains why something might look off.",
  },
  "diag.runAll": { "zh-TW": "全部重新檢查", en: "Run all checks" },
  "diag.tab.checks": { "zh-TW": "健康檢查", en: "Health checks" },
  "diag.tab.matrix": { "zh-TW": "模型體檢", en: "Model sanity" },
  "diag.tab.activity": { "zh-TW": "活動", en: "Activity" },
  "diag.checking": {
    "zh-TW": "檢查中…每項檢查完成後會即時更新。",
    en: "Checking… results update as each probe finishes.",
  },
  "diag.outcome.pass": { "zh-TW": "正常", en: "Normal" },
  "diag.outcome.fail": { "zh-TW": "發現問題", en: "Issue found" },
  "diag.outcome.error": { "zh-TW": "無法執行", en: "Couldn't run" },
  "diag.outcome.skip": { "zh-TW": "未設定", en: "Not configured" },
  "diag.outcome.none": { "zh-TW": "尚未檢查", en: "Not checked yet" },
  "diag.lastChecked": { "zh-TW": "上次檢查 {when}", en: "Last checked {when}" },
  "diag.took": { "zh-TW": " · 耗時 {sec}s", en: " · took {sec}s" },
  "diag.rerunAria": { "zh-TW": "重新檢查：{name}", en: "Re-run: {name}" },
  "diag.rerunTitle": { "zh-TW": "重新執行這項檢查", en: "Re-run this check" },

  // Diagnostics → Activity tab: LLM-run traces + durable-store telemetry (#465).
  "telemetry.span.tool": { "zh-TW": "工具", en: "tool" },
  "telemetry.span.agent": { "zh-TW": "代理", en: "agent" },
  "telemetry.span.handoff": { "zh-TW": "交接", en: "handoff" },
  "telemetry.noSteps": { "zh-TW": "沒有記錄任何步驟。", en: "No steps recorded." },
  "telemetry.step": { "zh-TW": "{n} 步", en: "{n} step" },
  "telemetry.steps": { "zh-TW": "{n} 步", en: "{n} steps" },
  "telemetry.live": { "zh-TW": "即時", en: "live" },
  "telemetry.empty": {
    "zh-TW": "目前沒有任何活動。跑一次 AI（對話、建立 wiki）後，它的 LLM 呼叫與工具呼叫就會即時出現在這裡。",
    en: "No activity yet. Run an agent turn (a chat, a wiki build) and its LLM calls + tool calls appear here live.",
  },
  "telemetry.durable.aria": { "zh-TW": "持久儲存遙測", en: "Durable store telemetry" },
  "telemetry.durable.title": { "zh-TW": "持久儲存", en: "Durable store" },
  "telemetry.durable.files": { "zh-TW": "每次鏡射檔案數（p95）", en: "Files / mirror (p95)" },
  "telemetry.durable.restore": { "zh-TW": "冷喚醒還原（p95）", en: "Cold-wake restore (p95)" },
  "telemetry.durable.rows": { "zh-TW": "WorkspaceFile 列數", en: "WorkspaceFile rows" },
  "telemetry.durable.trend": { "zh-TW": "WorkspaceFile 列數趨勢", en: "WorkspaceFile rows trend" },
  "telemetry.durable.samples": {
    "zh-TW": "{mirror} 次鏡射 · {restore} 次還原取樣",
    en: "{mirror} mirror · {restore} restore samples",
  },
} satisfies Record<string, Entry>;

export type MsgKey = keyof typeof messages;

export type Vars = Record<string, string | number>;

/** Look up a message in `locale`, substituting any `{name}` placeholders. */
/** Narrow an arbitrary string (e.g. a server-supplied message key) to a known
 * `MsgKey` so callers can guard before translating — `translate` would throw on
 * an unknown key. */
export function isMsgKey(key: string): key is MsgKey {
  return key in messages;
}

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
