/* Fix-It Assistant front-end. Plain JavaScript, no frameworks, no build step. */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
  const show = (el) => el.removeAttribute("hidden");
  const hide = (el) => el.setAttribute("hidden", "");
  let toastTimer = null;
  function toast(msg, kind) { const t = $("toast"); t.textContent = msg; t.className = "toast" + (kind ? " " + kind : ""); show(t); clearTimeout(toastTimer); toastTimer = setTimeout(() => hide(t), kind === "bad" ? 7000 : 3500); }
  const SERVER_DOWN = "The app's server isn't answering. Go back to the terminal window and start it again with: python app.py";
  async function api(path, opts = {}) {
    let res;
    try { res = await fetch(path, { headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" }, ...opts }); }
    catch (e) { if (e.name === "AbortError") throw e; throw new Error(SERVER_DOWN); }
    let data = null; try { data = await res.json(); } catch (_) { /* none */ }
    if (!res.ok) { const msg = (data && (data.detail || data.message)) || `Something went wrong (${res.status}).`; throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg)); }
    return data;
  }
  async function copyText(text) { try { await navigator.clipboard.writeText(text); toast("Copied!", "ok"); } catch (_) { toast("Couldn't copy automatically — select the text and press Ctrl+C.", "bad"); } }

  const state = { status: null, repair: null, card: null, candidates: [], area: "", photo: null, answers: {} };
  const screens = ["setup", "input", "results"];
  function go(name) { hide($("toast")); screens.forEach((s) => hide($("screen-" + s))); show($("screen-" + name)); window.scrollTo({ top: 0, behavior: "smooth" }); }

  // ---------- status ----------
  async function refreshStatus(probe) {
    try { state.status = await api("/api/status" + (probe ? "?probe=1" : "")); }
    catch (e) { $("engineText").textContent = "Can't reach the app"; $("engineDot").className = "dot bad"; toast(e.message, "bad"); return; }
    const p = state.status.provider;
    const names = { ollama: "AI on my computer", openai: "Cloud AI (OpenAI)", gemini: "Cloud AI (Gemini)", offline: "No AI (guide only)" };
    $("engineText").textContent = names[p.name] + (p.ok ? "" : " — problem");
    $("engineDot").className = "dot " + (p.ok ? (p.name === "offline" ? "warn" : "ok") : "bad");
    $("enginePill").title = p.message;
    const aiOff = p.name === "offline";
    $("photoHint").textContent = aiOff ? "Photos need an AI option (Settings). Describing it in words works just as well." : (p.vision_ready ? "JPG or PNG. The AI will describe what it sees." : (p.name === "ollama" ? `To read photos, run: ollama pull ${p.vision_model || "moondream"}` : "JPG or PNG."));
    $("askHint").textContent = aiOff ? "No AI: I can answer simple questions (tools, cost, time, safety, when to call a pro)." : (!p.ok ? "The AI option has a problem (see the pill at the top). You'll get simple answers until it's fixed." : "");
    renderAreaChips();
  }
  function renderAreaChips() {
    const areas = state.status ? state.status.areas : {};
    $("areaChips").innerHTML = Object.entries(areas).map(([k, v]) => `<button class="chip area ${state.area === k ? "active" : ""}" data-area="${k}">${v.emoji} ${esc(v.label)}</button>`).join("");
    $("areaChips").querySelectorAll("[data-area]").forEach((b) => (b.onclick = () => { state.area = state.area === b.dataset.area ? "" : b.dataset.area; renderAreaChips(); }));
  }

  // ---------- first run ----------
  async function initSetup() {
    const o = state.status.ollama, el = $("ollamaStatus");
    if (o.running && o.models.length) { el.textContent = "✓ Found Ollama with " + o.models[0]; el.className = "choice-status ok"; }
    else if (o.running) { el.textContent = "Ollama is running but has no model yet (we'll show you how)"; el.className = "choice-status"; }
    else { el.textContent = "Not found — we'll show you how to install it (free)"; el.className = "choice-status bad"; }
    document.querySelectorAll(".choice").forEach((btn) => {
      btn.onclick = async () => {
        try {
          const c = btn.dataset.choice;
          const textModel = (o.models || []).find((m) => !/moondream|llava|vision|minicpm|bakllava/i.test(m)) || "llama3.2:3b";
          const body = c === "ollama" ? { provider: "ollama", model: textModel } : c === "cloud" ? { provider: "openai" } : { provider: "offline" };
          await api("/api/settings", { method: "POST", body: JSON.stringify({ ...body, setup_complete: true }) });
          await refreshStatus(); go("input");
          if (c === "cloud") { openSettings(); toast("Paste your API key, click Test, then Save.", ""); }
          else if (c === "ollama" && !(o.running && o.models.length)) { openSettings(); toast("Follow the steps in the box to install Ollama, then click Test.", ""); }
        } catch (e) { toast("Couldn't save that choice: " + e.message, "bad"); }
      };
    });
  }

  // ---------- input ----------
  $("descText").oninput = () => { const n = $("descText").value.trim().split(/\s+/).filter(Boolean).length; $("descCount").textContent = n ? `${n} words` : ""; };
  document.querySelectorAll("[data-example]").forEach((b) => (b.onclick = () => { $("descText").value = b.dataset.example; $("descText").dispatchEvent(new Event("input")); $("descText").focus(); }));
  const dz = $("dropzone"), photoInput = $("photoInput");
  dz.onclick = () => photoInput.click();
  dz.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); photoInput.click(); } };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
  dz.addEventListener("drop", (e) => { const f = e.dataTransfer.files && e.dataTransfer.files[0]; if (f) setPhoto(f); });
  photoInput.onchange = () => { if (photoInput.files[0]) setPhoto(photoInput.files[0]); photoInput.value = ""; };
  function setPhoto(file) {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type)) { toast("Photos must be JPG, PNG or WebP. (iPhone HEIC: take a screenshot of the photo, or change the camera format to 'Most compatible'.)", "bad"); return; }
    if (file.size > 8 * 1024 * 1024) { toast("That photo is over 8 MB. Share a smaller size, or take a screenshot of it.", "bad"); return; }
    state.photo = file; $("photoImg").src = URL.createObjectURL(file); show($("photoPreview")); hide(dz);
    if (state.status && state.status.provider.name === "offline") toast("Heads up: with No AI, the photo is ignored. Describe what you see in words too.", "");
  }
  $("photoRemove").onclick = () => { state.photo = null; hide($("photoPreview")); show(dz); };
  $("browseLink").onclick = (e) => { e.preventDefault(); openBrowse(); };

  $("goBtn").onclick = async () => {
    const desc = $("descText").value.trim();
    if (desc.length < 8 && !state.photo) { toast("Tell me a bit more: what is the thing, and what is it doing?", "bad"); $("descText").focus(); return; }
    const fd = new FormData(); fd.append("description", desc); fd.append("area", state.area); if (state.photo) fd.append("photo", state.photo);
    $("goBtn").disabled = true; show($("working"));
    const p = state.status.provider; $("workingText").textContent = state.photo && p.name === "ollama" ? "Looking at your photo… this takes 1–3 minutes with a local AI." : p.name === "ollama" ? "Thinking… 20–60 seconds with a local AI." : p.name === "offline" ? "Checking the guide…" : "Thinking…";
    try {
      const d = await api("/api/diagnose", { method: "POST", body: fd });
      state.repair = { id: d.id, description: desc, status: "open", chat: [] }; state.answers = {};
      renderResults(d); go("results");
    } catch (e) { toast(e.message, "bad"); }
    finally { $("goBtn").disabled = false; hide($("working")); }
  };
  $("backBtn").onclick = () => go("input");
  $("noMatchBack").onclick = () => go("input");
  $("noMatchBrowse").onclick = () => openBrowse();
  $("brandLink").onclick = (e) => { e.preventDefault(); if (state.status && (state.status.settings.setup_complete || (state.status.env_overrides || []).includes("FIXIT_PROVIDER"))) go("input"); };

  // ---------- results ----------
  function renderOtherMatches(activeId) {
    const others = (state.candidates || []).filter((c) => !activeId || c.id !== activeId);
    if (others.length) {
      $("otherMatches").innerHTML = `<span class="muted small">Not quite right? Other matches:</span> ` + others.map((c) => `<button class="chip" data-pick="${c.id}">${esc(c.title)}</button>`).join("");
      $("otherMatches").querySelectorAll("[data-pick]").forEach((b) => (b.onclick = () => pickProblem(b.dataset.pick)));
      show($("otherMatches"));
    } else hide($("otherMatches"));
  }
  function renderResults(d) {
    state.candidates = d.candidates || [];
    $("rStatus").value = (state.repair && state.repair.status) || "open";
    // emergencies
    const hasFlags = !!(d.flags && d.flags.length);
    if (hasFlags) {
      $("flags").innerHTML = d.flags.map((f) => `<div class="flag"><div class="flag-title">🔴 ${esc(f.title)} — do this first</div><ol>${f.do.map((x) => `<li>${esc(x)}</li>`).join("")}</ol></div>`).join("");
      show($("flags"));
    } else hide($("flags"));
    // explanation
    $("explainText").innerHTML = md(d.explanation || "");
    if (d.photo && d.photo.used) { $("photoNote").innerHTML = d.photo.text ? `📷 What the AI saw in your photo: <i>${esc(d.photo.text)}</i>` : `📷 ${esc(d.photo.error || "The photo couldn't be read.")}`; show($("photoNote")); } else hide($("photoNote"));
    if (d.error) toast(d.error, "bad");
    renderOtherMatches(d.card ? d.card.id : "");
    if (d.card) { hide($("noMatch")); show($("explainCard")); renderCard(d.card); show($("cardWrap")); }
    else { hide($("cardWrap")); hasFlags ? hide($("noMatch")) : show($("noMatch")); (!hasFlags && d.explanation) ? show($("explainCard")) : hide($("explainCard")); }
    $("chat").innerHTML = ""; $("askText").value = "";
  }
  async function pickProblem(pid) {
    try {
      const r = await api("/api/answers", { method: "POST", body: JSON.stringify({ repair_id: (state.repair && state.repair.id) || "", problem_id: pid, answers: {} }) });
      if (r.id && state.repair) state.repair.id = r.id;
      state.answers = {};
      renderCard(r.card);
      renderOtherMatches(pid);
      $("chat").innerHTML = ""; $("askText").value = "";
      show($("cardWrap")); hide($("noMatch"));
      $("explainText").innerHTML = `You picked <b>${esc(r.card.title)}</b>.`;
      window.scrollTo({ top: $("cardWrap").offsetTop - 80, behavior: "smooth" });
    }
    catch (e) { toast(e.message, "bad"); }
  }
  const DIFF = ["", "Very easy", "Easy", "Medium", "Hard", "Expert"];
  function renderCard(c) {
    state.card = c;
    const s = c.safety; const sb = $("safetyBanner"); sb.className = "safety-banner " + s.level;
    $("safetyEmoji").textContent = s.emoji; $("safetyLabel").textContent = s.label; $("safetyBlurb").textContent = s.blurb;
    $("safetyNotes").innerHTML = (s.notes || []).map((n) => `<li>${esc(n)}</li>`).join("");
    $("cardTitle").textContent = c.title; $("cardArea").textContent = `${c.area_emoji} ${c.area_label}`;
    $("quickFacts").innerHTML = [["⏱️ Time", c.time], ["💪 Difficulty", `${DIFF[c.difficulty] || ""} (${c.difficulty}/5)`], ["💵 Do it yourself", c.cost.diy], ["👷 Hire someone", c.cost.pro]].map(([k, v]) => `<div class="fact"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
    // questions
    if (c.questions && c.questions.length) {
      $("questions").innerHTML = c.questions.map((q) => `<div class="q"><div class="q-text">${esc(q.q)}</div><div class="q-opts">${q.options.map((o) => `<button class="chip ${state.answers[q.id] === o ? "active" : ""}" data-q="${esc(q.id)}" data-o="${esc(o)}">${esc(o)}</button>`).join("")}</div></div>`).join("");
      $("questions").querySelectorAll("[data-q]").forEach((b) => (b.onclick = () => answer(b.dataset.q, b.dataset.o)));
      show($("questionsCard"));
    } else hide($("questionsCard"));
    $("causes").innerHTML = c.causes.map((x, i) => `<div class="cause ${i === 0 ? "top" : ""}"><div class="cause-rank">${i + 1}</div><div><div class="cause-name">${esc(x.name)} <span class="likely ${x.how_likely.replace(" ", "-")}">${esc(x.how_likely)}</span>${x.note ? `<span class="note">${esc(x.note)}</span>` : ""}</div><div class="muted small">${esc(x.signs || "")}</div></div></div>`).join("");
    $("steps").innerHTML = c.steps.map((st) => `<li><label class="step"><input type="checkbox"><span>${esc(st)}</span></label></li>`).join("");
    $("tip").innerHTML = c.tip ? `💡 ${esc(c.tip)}` : ""; c.tip ? show($("tip")) : hide($("tip"));
    if (c.call_pro_if && c.call_pro_if.length) { $("callPro").innerHTML = c.call_pro_if.map((x) => `<li>${esc(x)}</li>`).join(""); show($("proCard")); } else hide($("proCard"));
    $("tools").innerHTML = c.tools.length ? c.tools.map((t) => `<li><label><input type="checkbox"><span>${esc(t)}</span></label></li>`).join("") : '<li class="muted">Nothing special.</li>';
    $("parts").innerHTML = c.parts.length ? c.parts.map((p) => `<li><label><input type="checkbox"><span>${esc(p.name)} <span class="muted">· ${esc(p.cost)}</span></span></label></li>`).join("") : '<li class="muted">Probably no parts needed.</li>';
  }
  async function answer(qid, opt) {
    state.answers[qid] = state.answers[qid] === opt ? "" : opt;
    try {
      const r = await api("/api/answers", { method: "POST", body: JSON.stringify({ repair_id: (state.repair && state.repair.id) || "", problem_id: state.card.id, answers: state.answers }) });
      if (r.id && state.repair) state.repair.id = r.id;
      renderCard(r.card);
    }
    catch (e) { toast(e.message, "bad"); }
  }
  $("copyList").onclick = () => { const c = state.card; if (!c) return; copyText(`Shopping list — ${c.title}\n\nTools:\n${c.tools.map((t) => "- " + t).join("\n")}\n\nParts:\n${c.parts.map((p) => `- ${p.name} (${p.cost})`).join("\n") || "- none"}`); };
  $("rStatus").onchange = async () => {
    try {
      const rid = (state.repair && state.repair.id) || "000000000000";
      const r = await api(`/api/repairs/${rid}/status`, { method: "POST", body: JSON.stringify({ status: $("rStatus").value, problem_id: state.card ? state.card.id : null }) });
      if (r.id && state.repair) state.repair.id = r.id;
      state.repair.status = $("rStatus").value;
      toast($("rStatus").value === "fixed" ? "Nice work! 🎉 Saved." : "Saved.", "ok");
    } catch (e) { toast(e.message, "bad"); }
  };

  // ---------- ask ----------
  function addChat(role, text) { const div = document.createElement("div"); div.className = "msg " + role; div.innerHTML = esc(text).replace(/\n/g, "<br>"); $("chat").appendChild(div); $("chat").scrollTop = $("chat").scrollHeight; }
  async function ask() {
    const q = $("askText").value.trim(); if (!q) { toast("Type a question first.", "bad"); return; }
    if (!state.card) { toast("Pick a problem from the matches first.", "bad"); return; }
    const forId = (state.repair && state.repair.id) || "";
    const forPid = state.card.id;
    addChat("user", q); $("askText").value = ""; $("askBtn").disabled = true; show($("askWorking"));
    try {
      const d = await api("/api/ask", { method: "POST", body: JSON.stringify({ repair_id: forId, problem_id: forPid, question: q }) });
      if (!state.card || state.card.id !== forPid) return;
      if (d.id && state.repair) state.repair.id = d.id;
      addChat("assistant", d.answer);
      if (d.error) toast(d.error, "bad");
    }
    catch (e) { toast(e.message, "bad"); }
    finally { $("askBtn").disabled = false; hide($("askWorking")); }
  }
  $("askBtn").onclick = ask; $("askText").onkeydown = (e) => { if (e.key === "Enter") ask(); };

  // ---------- notebook ----------
  $("notebookBtn").onclick = async () => {
    let list; try { list = await api("/api/repairs"); } catch (e) { toast(e.message, "bad"); return; }
    const st = { open: "working on it", fixed: "fixed ✓", called_pro: "called a pro", gave_up: "living with it" };
    const lv = { diy: "🟢", careful: "🟡", pro: "🟠", emergency: "🔴" };
    $("notebookList").innerHTML = list.length ? list.map((r) => `<div class="app-row"><div><div class="t">${lv[r.safety_level] || ""} ${esc(r.title)}<span class="status-chip">${st[r.status] || esc(r.status)}</span>${r.unsaved ? '<span class="status-chip" title="Saving is off in Settings; this disappears when the app restarts">not saved</span>' : ""}</div><div class="muted small">${new Date(r.updated * 1000).toLocaleDateString()}</div></div><div class="row-gap" style="margin:0"><button class="btn small" data-open="${r.id}">Open</button><button class="btn small danger-ghost" data-del="${r.id}">Delete</button></div></div>`).join("") : '<p class="muted">Nothing saved yet.</p>';
    $("notebookList").querySelectorAll("[data-open]").forEach((b) => (b.onclick = async () => {
      try {
        const d = await api("/api/repairs/" + b.dataset.open);
        state.repair = { id: d.id, description: d.description, status: d.status, chat: d.chat || [] }; state.answers = d.answers || {};
        $("descText").value = d.description || ""; $("descText").dispatchEvent(new Event("input"));
        closeModals(); renderResults({ flags: d.flags, candidates: d.candidates, explanation: d.explanation, card: d.card, photo: { used: !!d.photo_text, text: d.photo_text } }); (d.chat || []).forEach((m) => addChat(m.role, m.text)); go("results");
      } catch (e) { toast(e.message, "bad"); }
    }));
    $("notebookList").querySelectorAll("[data-del]").forEach((b) => (b.onclick = async () => { if (!confirm("Delete this saved repair? This can't be undone.")) return; try { await api("/api/repairs/" + b.dataset.del, { method: "DELETE" }); if (state.repair && state.repair.id === b.dataset.del) { state.repair = null; go("input"); } toast("Deleted.", "ok"); } catch (e) { toast(e.message, "bad"); } $("notebookBtn").click(); }));
    openModal("modal-notebook");
  };
  $("notebookNew").onclick = () => { closeModals(); $("descText").value = ""; $("descText").dispatchEvent(new Event("input")); $("photoRemove").click(); state.area = ""; renderAreaChips(); go("input"); $("descText").focus(); };
  $("notebookDeleteAll").onclick = async () => { if (!confirm("Delete every saved repair from this computer?")) return; try { await api("/api/repairs", { method: "DELETE" }); closeModals(); toast("Deleted.", "ok"); } catch (e) { toast(e.message, "bad"); } };

  // ---------- browse ----------
  let browseData = null;
  async function openBrowse() {
    if (!browseData) { try { browseData = (await api("/api/browse")).areas; } catch (e) { toast(e.message, "bad"); return; } }
    $("browseFilter").value = ""; renderBrowse(""); openModal("modal-browse"); $("browseFilter").focus();
  }
  function renderBrowse(filter) {
    const f = filter.toLowerCase(); const lv = { diy: "🟢", careful: "🟡", pro: "🟠", emergency: "🔴" };
    $("browseList").innerHTML = browseData.map((a) => { const items = a.problems.filter((p) => !f || p.title.toLowerCase().includes(f) || a.label.toLowerCase().includes(f)); return items.length ? `<h3>${a.emoji} ${esc(a.label)}</h3><div class="browse-grid">${items.map((p) => `<button class="browse-item" data-pid="${p.id}"><span>${lv[p.safety_level]}</span> ${esc(p.title)}</button>`).join("")}</div>` : ""; }).join("") || '<p class="muted">Nothing matches that word. Try another.</p>';
    $("browseList").querySelectorAll("[data-pid]").forEach((b) => (b.onclick = () => openFromBrowse(b.dataset.pid)));
  }
  $("browseFilter").oninput = () => renderBrowse($("browseFilter").value);
  $("browseBtn").onclick = openBrowse;
  async function openFromBrowse(pid) {
    closeModals();
    try {
      const c = await api("/api/problem/" + pid);
      state.repair = { id: "", description: c.title, status: "open", chat: [] };
      state.answers = {};
      renderResults({ id: "", flags: [], candidates: [], explanation: "", card: c, photo: { used: false } });
      $("explainText").innerHTML = `You picked <b>${esc(c.title)}</b> from the guide.`;
      go("results");
    } catch (e) { toast(e.message, "bad"); }
  }

  // ---------- modals & settings ----------
  function openModal(id) { show($("modalBack")); show($(id)); }
  function closeModals() { hide($("modalBack")); document.querySelectorAll(".modal").forEach(hide); }
  $("modalBack").onclick = closeModals; document.querySelectorAll("[data-close]").forEach((b) => (b.onclick = closeModals));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModals(); });
  $("helpBtn").onclick = () => openModal("modal-help");
  const VISION_RX = /moondream|llava|vision|minicpm|bakllava|qwen2-vl|gemma3/i;
  async function openSettings() {
    if (!state.status) { toast("Settings aren't available until the app's server answers.", "bad"); return; }
    await refreshStatus(true);
    const s = state.status.settings, o = state.status.ollama;
    document.querySelector(`input[name=prov][value=${s.provider}]`).checked = true; showProvPanel(s.provider);
    const models = o.models.length ? o.models : [s.provider === "ollama" ? s.model : "llama3.2:3b"];
    $("setOllamaModel").innerHTML = models.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join(""); if (s.provider === "ollama" && models.includes(s.model)) $("setOllamaModel").value = s.model;
    const vis = o.models.filter((m) => VISION_RX.test(m)); const vdef = s.vision_model || "moondream";
    $("setVisionModel").innerHTML = (vis.length ? vis : [vdef]).map((m) => `<option value="${esc(m)}">${esc(m)}${vis.length ? "" : " (not downloaded yet)"}</option>`).join(""); if (vis.includes(vdef)) $("setVisionModel").value = vdef;
    vis.length ? hide($("visionHelp")) : show($("visionHelp"));
    $("setOllamaHost").value = s.ollama_host; $("setOllamaInfo").textContent = o.running ? (o.models.length ? `Found ${o.models.length} model${o.models.length > 1 ? "s" : ""}.` : "Running, but no model downloaded.") : "Ollama is not running.";
    o.running && o.models.length ? hide($("ollamaHelp")) : show($("ollamaHelp"));
    $("setOpenaiKey").value = s.openai_api_key || ""; $("setOpenaiModel").value = s.provider === "openai" ? s.model : ""; $("setOpenaiUrl").value = s.openai_base_url;
    $("setGeminiKey").value = s.gemini_api_key || ""; $("setGeminiModel").value = s.provider === "gemini" ? s.model : "";
    $("setSaveHistory").checked = !!s.save_history; $("setTestOut").textContent = "";
    $("setDataInfo").textContent = `${state.status.repairs.length} saved repair${state.status.repairs.length !== 1 ? "s" : ""}, stored only in ${state.status.data_dir}`;
    const env = state.status.env_overrides || []; env.length ? (show($("setEnvNote")), $("setEnvNote").textContent = `Note: environment variables (${env.join(", ")}) currently override these settings.`) : hide($("setEnvNote"));
    openModal("modal-settings");
  }
  function showProvPanel(p) { document.querySelectorAll(".prov-panel").forEach((el) => el.classList.toggle("show", el.dataset.prov === p)); }
  document.querySelectorAll("input[name=prov]").forEach((r) => (r.onchange = () => { showProvPanel(r.value); $("setTestOut").textContent = ""; }));
  $("settingsBtn").onclick = openSettings; $("enginePill").onclick = openSettings;
  function collectSettings() {
    const provider = document.querySelector("input[name=prov]:checked").value;
    const body = { provider, ollama_host: $("setOllamaHost").value.trim() || "http://127.0.0.1:11434", openai_base_url: $("setOpenaiUrl").value.trim() || "https://api.openai.com/v1", save_history: $("setSaveHistory").checked, vision_model: $("setVisionModel").value || "" };
    if (provider === "ollama") body.model = $("setOllamaModel").value; if (provider === "openai") body.model = $("setOpenaiModel").value.trim(); if (provider === "gemini") body.model = $("setGeminiModel").value.trim(); if (provider === "offline") body.model = "";
    const ok = $("setOpenaiKey").value.trim(); if (ok) body.openai_api_key = ok; const gk = $("setGeminiKey").value.trim(); if (gk) body.gemini_api_key = gk;
    return body;
  }
  $("setTest").onclick = async () => { $("setTestOut").textContent = "Testing…"; $("setTestOut").className = "small"; try { const d = await api("/api/test-connection", { method: "POST", body: JSON.stringify(collectSettings()) }); $("setTestOut").textContent = (d.ok ? "✓ " : "✗ ") + d.message; $("setTestOut").className = "small " + (d.ok ? "status-ok" : "status-bad"); } catch (e) { $("setTestOut").textContent = "✗ " + e.message; $("setTestOut").className = "small status-bad"; } };
  $("setSave").onclick = async () => { try { const d = await api("/api/settings", { method: "POST", body: JSON.stringify({ ...collectSettings(), setup_complete: true }) }); await refreshStatus(); closeModals(); toast(d.provider.ok ? "Saved. " + d.provider.message : "Saved, but: " + d.provider.message, d.provider.ok ? "ok" : "bad"); } catch (e) { toast(e.message, "bad"); } };
  document.querySelectorAll("[data-clear-key]").forEach((b) => (b.onclick = async () => { const f = b.dataset.clearKey; try { await api("/api/settings", { method: "POST", body: JSON.stringify({ [f]: "" }) }); await refreshStatus(); $(f === "openai_api_key" ? "setOpenaiKey" : "setGeminiKey").value = ""; toast("Key removed from this computer.", "ok"); } catch (e) { toast(e.message, "bad"); } }));

  // ---------- boot ----------
  (async function boot() {
    await refreshStatus();
    if (!state.status) { go("input"); toast("The app couldn't load its settings. Try again in a moment, or run: python scripts/selfcheck.py", "bad"); return; }
    if (!state.status.settings.setup_complete) { await refreshStatus(true); await initSetup(); go("setup"); return; }
    go("input");
  })();
})();
