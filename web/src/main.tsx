import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, Fact, ParagraphProvenance, session, Workspace } from "./api";
import "./styles.css";

const stageText: Record<string, string> = {
  idle: "等待输入", researching: "正在抽取和检索事实", planning: "正在生成逐段大纲",
  awaiting_approval: "等待确认事实清单或大纲", awaiting_decision: "等待你裁决事实冲突",
  generating: "正在写作", critic: "正在审查",
  revising: "正在修订", ready: "文章可定稿",
  finalized: "已定稿并写入个人知识库", needs_attention: "需要人工处理", failed: "执行失败",
};

const busyStages = new Set(["researching", "planning", "generating", "critic", "revising"]);

function PausePanel({ id, data, onResolved }: { id: string; data: Workspace; onResolved: () => void }) {
  const pause = data.pause_request;
  const [facts, setFacts] = useState<Array<Record<string, any>>>(pause?.facts ?? []);
  const [outline, setOutline] = useState<NonNullable<Workspace["outline"]>>([]);
  const [newFact, setNewFact] = useState("");
  const [actions, setActions] = useState<Record<string, { action: string; claim: string }>>({});
  const [error, setError] = useState("");
  useEffect(() => { setFacts(pause?.facts ?? []); setOutline(pause?.outline ?? []); setActions({}); setError(""); }, [data.waiting_job_id]);
  if (!pause || !data.waiting_job_id) return null;
  const submit = async (decision: Record<string, any>) => {
    setError("");
    try {
      await api.decision(id, { job_id: data.waiting_job_id!, fact_version: data.fact_version, decision });
      onResolved();
    } catch (err) { setError((err as Error).message); }
  };
  const sources = (raw: Record<string, any>) => <details><summary>查看来源</summary>
    {(raw.evidence ?? []).map((item: Record<string, any>, index: number) => <p key={index}>
      {item.source_kind} · {item.title} {item.url && <a href={item.url} target="_blank" rel="noreferrer">{item.url}</a>}
      <blockquote>{item.quote}</blockquote></p>)}
  </details>;
  return <section className="pause-panel panel">
    <div className="panel-title"><div><small>需要你确认</small><h2>{pause.kind === "facts" ? "事实清单" : pause.kind === "outline" ? "文章大纲" : "事实冲突"}</h2></div>
      <span>事实 v{data.fact_version}</span></div>
    {pause.kind === "facts" && <>
      <p>用户新增或修改的事实会列为必写项。请核对来源冲突并确认清单。</p>
      {facts.map((fact, index) => <div className="pause-fact" key={fact.fact_id}>
        <textarea value={fact.claim} onChange={event => setFacts(current => current.map((item, i) => i === index ? { ...item, claim: event.target.value } : item))} />
        <button className="quiet" onClick={() => setFacts(current => current.map((item, i) => i === index ? { ...item, excluded: true, marked: true, mark_reasons: [...new Set([...(item.mark_reasons ?? []), "user_deleted"])] } : item))}>删除</button>
        {sources(fact)}
      </div>)}
      <form className="fact-create" onSubmit={event => { event.preventDefault(); if (!newFact.trim()) return;
        setFacts(current => [...current, { fact_id: crypto.randomUUID(), version: 1, claim: newFact.trim(), origin: "user", verification: "unverified", marked: true, mark_reasons: ["user_created"], excluded: false, required_for_article: true, evidence: [] }]); setNewFact(""); }}>
        <input value={newFact} onChange={event => setNewFact(event.target.value)} placeholder="增加一条必写用户事实" /><button>添加</button>
      </form>
      {(pause.conflicts ?? []).map(conflict => {
        const selected = actions[String(conflict.fact_id)] ?? { action: "", claim: "" };
        return <div className="conflict-choice" key={conflict.fact_id}><strong>来源冲突：{conflict.claim}</strong><p>{conflict.reason}</p>{sources(conflict)}
          <select value={selected.action} onChange={event => setActions(current => ({ ...current, [String(conflict.fact_id)]: { ...selected, action: event.target.value } }))}>
            <option value="">请选择</option><option value="keep">保留原意</option><option value="replace">修改</option><option value="exclude">删除</option>
          </select>
          {selected.action === "replace" && <input value={selected.claim} placeholder="修改后的事实" onChange={event => setActions(current => ({ ...current, [String(conflict.fact_id)]: { ...selected, claim: event.target.value } }))} />}
        </div>;
      })}
      <button disabled={(pause.conflicts ?? []).some(item => !actions[String(item.fact_id)]?.action || (actions[String(item.fact_id)]?.action === "replace" && !actions[String(item.fact_id)]?.claim.trim()))}
        onClick={() => submit({ action: "confirm", fact_version: data.fact_version, facts, conflict_decisions: (pause.conflicts ?? []).map(item => ({ fact_id: item.fact_id, ...actions[String(item.fact_id)] })) })}>确认事实并生成大纲</button>
    </>}
    {pause.kind === "outline" && <><ol>{outline.map((section, sectionIndex) => <li key={section.section_id}>
      <input value={section.title} aria-label="章节标题" onChange={event => setOutline(current => current.map((item, i) => i === sectionIndex ? { ...item, title: event.target.value } : item))} />
      <textarea value={section.purpose} aria-label="章节目的" onChange={event => setOutline(current => current.map((item, i) => i === sectionIndex ? { ...item, purpose: event.target.value } : item))} />
      {section.paragraphs.map((paragraph, paragraphIndex) => <div className="pause-fact" key={paragraph.paragraph_id}>
        <textarea value={paragraph.main_points.join("\n")} aria-label="段落主要内容" onChange={event => setOutline(current => current.map((item, i) => i !== sectionIndex ? item : {
          ...item, paragraphs: item.paragraphs.map((part, j) => j === paragraphIndex ? { ...part, main_points: event.target.value.split("\n").filter(Boolean) } : part)
        }))} />
        <div className="fact-picks">{data.facts.filter(fact => !fact.excluded).map(fact => <label key={fact.id}>
          <input type="checkbox" checked={paragraph.fact_ids.includes(fact.id)} onChange={event => setOutline(current => current.map((item, i) => i !== sectionIndex ? item : {
            ...item, paragraphs: item.paragraphs.map((part, j) => j !== paragraphIndex ? part : { ...part, fact_ids: event.target.checked ? [...part.fact_ids, fact.id] : part.fact_ids.filter(id => id !== fact.id), required_fact_ids: [] })
          }))} />{fact.claim}
        </label>)}</div>
      </div>)}
    </li>)}</ol>
      <button onClick={() => {
        const updated = outline.map(section => ({ ...section, fact_ids: [...new Set(section.paragraphs.flatMap(part => part.fact_ids))] }));
        void submit({ action: "confirm", fact_version: data.fact_version, outline_version: data.outline_version, outline: updated });
      }}>确认大纲并开始生成</button>
    </>}
    {pause.kind === "plan_conflicts" && <>
      {(pause.facts ?? []).map(conflict => { const key = String(conflict.fact_id); const selected = actions[key] ?? { action: "", claim: "" };
        return <div className="conflict-choice" key={key}><strong>{conflict.claim}</strong><p>{conflict.reason}</p>{sources(conflict)}
          <select value={selected.action} onChange={event => setActions(current => ({ ...current, [key]: { ...selected, action: event.target.value } }))}>
            <option value="">请选择</option><option value="keep">保留原意</option><option value="replace">修改</option><option value="exclude">删除</option></select>
          {selected.action === "replace" && <input value={selected.claim} placeholder="修改后的事实" onChange={event => setActions(current => ({ ...current, [key]: { ...selected, claim: event.target.value } }))} />}
        </div>;
      })}
      <button disabled={(pause.facts ?? []).some(item => !actions[String(item.fact_id)]?.action || (actions[String(item.fact_id)]?.action === "replace" && !actions[String(item.fact_id)]?.claim.trim()))}
        onClick={() => submit({ fact_version: data.fact_version, decisions: (pause.facts ?? []).map(item => ({ fact_id: item.fact_id, ...actions[String(item.fact_id)] })) })}>提交全部裁决</button>
    </>}
    {pause.kind === "critic_conflicts" && <>
      {(pause.issues ?? []).map(issue => { const selected = actions[String(issue.issue_id)] ?? { action: "", claim: "" };
        return <div className="conflict-choice" key={issue.issue_id}><strong>{issue.claim || issue.message}</strong><p>{issue.message}</p><p>对应事实：{issue.expected_fact || issue.fact_id || "未匹配"}</p>{sources(issue)}
          <select value={selected.action} onChange={event => setActions(current => ({ ...current, [String(issue.issue_id)]: { ...selected, action: event.target.value } }))}>
            <option value="">请选择</option><option value="keep">保留原意</option><option value="replace">修改</option><option value="exclude">删除</option></select>
          {selected.action === "replace" && <input value={selected.claim} placeholder="修改后的事实" onChange={event => setActions(current => ({ ...current, [String(issue.issue_id)]: { ...selected, claim: event.target.value } }))} />}
        </div>;
      })}
      <button disabled={(pause.issues ?? []).some(item => !actions[String(item.issue_id)]?.action || (actions[String(item.issue_id)]?.action === "replace" && !actions[String(item.issue_id)]?.claim.trim()))}
        onClick={() => submit({ fact_version: data.fact_version, decisions: (pause.issues ?? []).map(item => ({ issue_id: item.issue_id, fact_id: item.fact_id, ...actions[String(item.issue_id)] })) })}>提交全部裁决</button>
    </>}
    {error && <p className="error">{error}</p>}
  </section>;
}

function paragraphForQuote(source: string, quote: string) {
  if (!source || !quote) return 1;
  const offset = source.indexOf(quote);
  if (offset < 0) return 1;
  const before = source.slice(0, offset);
  return before.split(/\r?\n/).filter(line => line.trim()).length + 1;
}

function EvidenceCard({ item, claim, sourceText }: {
  item: NonNullable<Fact["evidence"]>[number]; claim: string; sourceText: string;
}) {
  const metadata = item.metadata ?? {};
  const tables = Array.isArray(metadata.tables) ? metadata.tables.map(String) : [];
  const paragraph = Number(metadata.paragraph) || paragraphForQuote(sourceText, item.quote);
  const position = Number(metadata.position);
  const sameAsClaim = item.quote.replace(/\s+/g, " ").trim() === claim.replace(/\s+/g, " ").trim();
  let source = item.title || item.source_kind;
  if (item.source_kind === "web") source = "Tavily 网络检索";
  if (item.source_kind === "database") {
    source = `PostgreSQL · ${tables.length ? tables.join("、") : "公司结构化数据表"}`;
  }
  if (item.source_kind === "company_document") {
    source = `PostgreSQL pgvector · chunk_embeddings${Number.isFinite(position) ? ` · 分块 ${position + 1}` : ""}`;
  }
  if (item.source_kind === "source_article") source = `原文 · 第 ${paragraph} 段`;
  if (item.source_kind === "skill_md") source = `Skill Markdown · ${String(metadata.resource ?? item.title)} · 未验证`;
  return <div className="evidence">
    <div className="evidence-source"><strong>{source}</strong>
      {item.document_id && <span>文档 {item.document_id}{item.document_version ? ` · v${item.document_version}` : ""}</span>}
      {item.source_kind === "skill_md" && Boolean(metadata.file) && <span>{String(metadata.file)}</span>}
    </div>
    {item.source_kind === "skill_md" && Boolean(metadata.warning) && <p className="evidence-warning">{String(metadata.warning)}</p>}
    {item.url && <a className="evidence-url" href={item.url} target="_blank" rel="noreferrer">{item.url}</a>}
    {!sameAsClaim && item.quote && <p className="evidence-quote"><span>证据摘录</span>{item.quote}</p>}
  </div>;
}

function ParagraphSources({ record, data }: { record?: ParagraphProvenance; data: Workspace }) {
  if (!record) return null;
  return <details className="paragraph-sources"><summary>查看本段依据 · {record.fact_refs.length} 条事实</summary>
    <p>{record.step === "revise" ? "修订" : "生成"}于草稿 v{record.draft_version}
      {record.previous_draft_version ? `，依据草稿 v${record.previous_draft_version} 修订` : ""}</p>
    {record.fact_refs.map(ref => <div className="paragraph-source-fact" key={`${ref.fact_id}-${ref.version}`}>
      <strong>事实 v{ref.version}：{ref.claim}</strong>
      {ref.decision_id && <small> · 用户裁决 {data.user_decisions?.find(item => item.id === ref.decision_id)?.action ?? ref.decision_id}</small>}
      {ref.evidence.map(item => <EvidenceCard key={item.evidence_id} item={item} claim={ref.claim} sourceText={data.source_text ?? ""} />)}
    </div>)}
    {!!record.issues.length && <div>本次修订处理的问题：{record.issues.map((issue, index) =>
      <p key={index}>{issue.message ?? issue.reason ?? issue.claim ?? "需要复核的事实"}</p>)}</div>}
  </details>;
}

function Login({ onReady }: { onReady: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState("");
  return <main className="login-shell"><form className="login-card" onSubmit={async e => {
    e.preventDefault(); setError("");
    try { mode === "login" ? await api.login(email, password) : await api.register(email, password); onReady(); }
    catch (err) { setError((err as Error).message); }
  }}>
    <div className="wordmark">FORGE</div><h1>事实驱动的文章工作台</h1>
    <label>邮箱<input type="email" required value={email} onChange={e => setEmail(e.target.value)} /></label>
    <label>密码<input type="password" required minLength={mode === "register" ? 10 : undefined} value={password} onChange={e => setPassword(e.target.value)} /></label>
    {error && <p className="error">{error}</p>}<button>{mode === "login" ? "登录" : "注册访客账号"}</button>
    <button type="button" className="quiet mode-switch" onClick={() => setMode(mode === "login" ? "register" : "login")}>{mode === "login" ? "没有账号？注册" : "已有账号？登录"}</button>
  </form></main>;
}

function FactCard({ id, fact, version, sourceText, onChanged, busy }: { id: string; fact: Fact; version: number; sourceText: string; onChanged: () => void; busy: boolean }) {
  const [editing, setEditing] = useState(false);
  const [claim, setClaim] = useState(fact.claim);
  const save = async () => {
    await api.fact(id, {
      operation: "update", fact_id: fact.id, expected_version: version,
      fact: { ...fact, fact_id: fact.id, claim, mark_reasons: [...new Set([...fact.mark_reasons, "user_edited"])] },
    });
    setEditing(false); onChanged();
  };
  return <article className={`fact ${fact.marked ? "marked" : ""}`}>
    <div className="fact-meta"><span>{fact.origin}</span><span>{fact.verification}</span>{fact.marked && <span>用户标记</span>}</div>
    {editing ? <textarea value={claim} onChange={e => setClaim(e.target.value)} /> : <p>{fact.claim}</p>}
    {!!fact.evidence?.length && <details><summary>{fact.evidence.length} 条证据与来源</summary>{fact.evidence.map(item =>
      <EvidenceCard item={item} claim={fact.claim} sourceText={sourceText} key={item.evidence_id} />
    )}</details>}
    <fieldset className="fact-actions" disabled={busy}>
      {editing ? <><button onClick={save}>保存</button><button className="quiet" onClick={() => setEditing(false)}>取消</button></> :
        <><button className="quiet" onClick={() => setEditing(true)}>编辑</button><button className="danger" onClick={async () => {
          await api.fact(id, { operation: "delete", fact_id: fact.id, expected_version: version }); onChanged();
        }}>排除</button></>}
    </fieldset>
  </article>;
}

function WorkspaceView({ id }: { id: string }) {
  const [data, setData] = useState<Workspace | null>(null);
  const [message, setMessage] = useState("");
  const [source, setSource] = useState("");
  const [newFact, setNewFact] = useState("");
  const [factQuery, setFactQuery] = useState("");
  const [error, setError] = useState("");
  const refresh = async () => { try { setData(await api.workspace(id)); setError(""); } catch (err) { setError((err as Error).message); } };
  useEffect(() => { void refresh(); const timer = setInterval(refresh, 3000); return () => clearInterval(timer); }, [id]);
  const activeFacts = useMemo(() => data?.facts.filter(item =>
    !item.excluded && item.claim.toLocaleLowerCase().includes(factQuery.trim().toLocaleLowerCase())
  ) ?? [], [data, factQuery]);
  const notices = data?.events.filter(item => ["failed", "degraded", "retry_scheduled"].includes(item.event_type)).slice(-3) ?? [];
  const progress = [...(data?.events ?? [])].reverse().find(item => item.event_type === "progress");
  const busy = busyStages.has(data?.stage ?? "");
  if (!data) return <main className="loading">正在打开工作台…{error}</main>;
  return <main className="app-shell">
    <header><div className="wordmark">FORGE</div><div className="status"><i />{stageText[data.stage] ?? data.stage}</div></header>
    {busy && <section className="progress-banner" aria-live="polite"><span className="spinner" /><div>
      <strong>{progress?.payload.message ?? stageText[data.stage]}</strong>
      <small>任务在后台继续执行，页面每 3 秒更新一次</small>
    </div></section>}
    {!!notices.length && <section className="notices">{notices.map(item => <div key={item.sequence}>{item.event_type === "degraded" ? item.payload.warnings?.join("；") : item.payload.error || "任务正在重试"}</div>)}</section>}
    <PausePanel id={id} data={data} onResolved={() => void refresh()} />
    {!!data.system_decisions?.length && <section className="notices">{data.system_decisions.map(item =>
      <div key={item.id}><strong>系统决定 · 事实 v{item.fact_version}</strong><p>{item.note}</p><p>{item.conflict.reason || item.conflict.message}</p></div>)}</section>}
    {!!data.user_decisions?.length && <section className="notices"><strong>用户事实裁决记录</strong>{data.user_decisions.map((item, index) =>
      <div key={`${item.fact_id}-${item.fact_version}-${index}`}>事实 v{item.fact_version} · {item.action} · {item.note}</div>)}</section>}
    {data.review?.stop_reason && <section className="notices">{data.review.stop_reason}</section>}
    <section className="planning-grid">
      <div className="panel facts-panel"><div className="panel-title"><div><small>01</small><h2>事实清单</h2></div><span>{activeFacts.length} 条 · v{data.fact_version}</span></div>
        <form className="fact-create" onSubmit={async e => { e.preventDefault(); if (!newFact.trim()) return; await api.fact(id, { operation: "create", expected_version: data.fact_version, fact: { claim: newFact, origin: "user" } }); setNewFact(""); await refresh(); }}>
          <input placeholder="添加一条用户事实" value={newFact} onChange={e => setNewFact(e.target.value)} disabled={!data.article_id || busy || Boolean(data.pause_request)} /><button disabled={!data.article_id || busy || Boolean(data.pause_request)}>添加</button>
        </form>
        <input className="fact-search" placeholder="查询当前事实…" value={factQuery} onChange={e => setFactQuery(e.target.value)} />
        <div className="fact-list">{activeFacts.map(f => <FactCard busy={busy || Boolean(data.pause_request)} id={id} key={`${f.id}-${f.version}`} fact={f} version={data.fact_version} sourceText={data.source_text ?? ""} onChanged={refresh} />)}
          {!activeFacts.length && <p className="empty">粘贴原文后，planner 会在这里汇总原文、公司资料和网络证据。</p>}</div>
      </div>
      <div className="panel outline-panel"><div className="panel-title"><div><small>02</small><h2>逐段大纲</h2></div><span>v{data.outline_version}</span></div>
        <ol>{data.outline.map(section => <li key={section.section_id}><h3>{section.title}</h3><p>{section.purpose}</p><div>{section.main_points.join(" · ")}</div><small>使用 {section.fact_ids.length} 条事实</small></li>)}</ol>
      </div>
    </section>
    {data.draft && <section className="draft panel"><div className="panel-title"><div><small>03</small><h2>当前文章</h2></div><span>v{data.draft.version}</span></div><article>{data.draft.paragraphs?.length ? data.draft.paragraphs.map(item =>
      <div className="draft-paragraph" key={item.paragraph_id}><small>第 {item.section_order} 节 · 第 {item.paragraph_order} 段</small><p>{item.content}</p>
        <ParagraphSources record={data.paragraph_provenance.find(record => record.paragraph_id === item.paragraph_id)} data={data} /></div>
    ) : data.draft.content}</article><div className="draft-actions">
      {data.plan_dirty && <button onClick={async () => { await api.revise(id); await refresh(); }}>按事实变更修订文章</button>}
      {data.stage === "ready" && !data.plan_dirty && <button onClick={async () => { await api.finalize(id); await refresh(); }}>确认定稿并写入个人知识库</button>}
    </div></section>}
    {data.review && data.review.draft_version === data.draft?.version && <section className="review panel"><div className="panel-title"><div><small>04</small><h2>事实审查</h2></div><span>{data.review.status}</span></div>
      <div className="review-content"><p>已逐条核对 {data.review.fact_checks?.filter(item => item.check_type === "baseline").length ?? 0} 项大纲事实。</p>
        {!!data.review.issues?.length && <ol>{data.review.issues.map((issue, index) => <li key={issue.issue_id ?? index}>
          <strong>{issue.message ?? issue.reason}</strong>
          {issue.paragraph_id && <small> · 段落 {data.draft?.paragraphs?.find(item => item.paragraph_id === issue.paragraph_id)?.paragraph_order ?? issue.paragraph_id}</small>}
          {issue.claim && <p>正文：{issue.claim}</p>}{issue.expected_fact && <p>正确事实：{issue.expected_fact}</p>}
        </li>)}</ol>}
        {data.review.stop_reason && <p>{data.review.stop_reason}</p>}
      </div></section>}
    <section className="conversation panel"><div className="messages">{data.messages.map(item => <div key={item.id} className={`message ${item.role}`}><span>{item.role === "user" ? "你" : "Forge"}</span><p>{item.content}</p></div>)}</div>
      {!data.article_id && <textarea className="source" placeholder="先粘贴员工复制的原文…" value={source} onChange={e => setSource(e.target.value)} />}
      <form className="composer" onSubmit={async e => { e.preventDefault(); if (!message.trim()) return; setError(""); try { await api.message(id, { content: message, source_text: source }); setMessage(""); setSource(""); await refresh(); } catch (err) { setError((err as Error).message); } }}>
        <textarea disabled={busy || Boolean(data.pause_request)} placeholder={data.pause_request ? "请先完成当前确认" : busy ? "当前任务执行中…" : "提问、提出修改要求，或说明写作需求…"} value={message} onChange={e => setMessage(e.target.value)} /><button disabled={busy || Boolean(data.pause_request)}>{busy ? "处理中" : "发送"}</button>
      </form>{error && <p className="error">{error}</p>}
    </section>
  </main>;
}

function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(session.token));
  const [id, setId] = useState(location.hash.slice(1));
  if (!loggedIn) return <Login onReady={() => setLoggedIn(true)} />;
  if (!id) return <main className="new-shell"><div><div className="wordmark">FORGE</div><h1>从一篇文章开始</h1><p>每段对话只处理一篇文章；问答可以随时插入，不会修改当前稿件。</p><button onClick={async () => { const result = await api.createConversation(); location.hash = result.conversation_id; setId(result.conversation_id); }}>新建对话</button></div></main>;
  return <WorkspaceView id={id} />;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
