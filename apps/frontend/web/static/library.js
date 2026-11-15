/* SaveFinder — Biblioteca cifrada (E2EE).
   A DEK vive só na memória desta página; o servidor nunca vê os dados em claro.
   Ciphertext é cacheado em IndexedDB para revisitas rápidas; plaintext nunca. */

"use strict";

// ---------- Estado ----------
const PAGE_SIZE = 25;

const SF = {
	dek: null,
	csrf: null,
	account: null,
	items: [],
	byKey: new Map(),
	thumbUrls: new Map(), // item_key -> blob URL (memória)
	query: "",
	type: "",
	status: "",
	specificTag: "", // filtro de tag específica no detalhe de tag ampla
	currentTagSlug: null,
	page: 1,
	searchIndex: [], // { item, haystack }
};

const IDB_NAME = "savefinder-library";
const IDB_VERSION = 1;

// ---------- IndexedDB (só ciphertext) ----------
function idbOpen() {
	return new Promise((resolve, reject) => {
		const req = indexedDB.open(IDB_NAME, IDB_VERSION);
		req.onupgradeneeded = () => {
			const db = req.result;
			if (!db.objectStoreNames.contains("items"))
				db.createObjectStore("items", { keyPath: "key" });
			if (!db.objectStoreNames.contains("thumbs"))
				db.createObjectStore("thumbs", { keyPath: "key" });
		};
		req.onsuccess = () => resolve(req.result);
		req.onerror = () => reject(req.error);
	});
}

function idbStore(db, name, mode) {
	return db.transaction(name, mode).objectStore(name);
}

function idbGetAll(db, name) {
	return new Promise((resolve, reject) => {
		const req = idbStore(db, name, "readonly").getAll();
		req.onsuccess = () => resolve(req.result || []);
		req.onerror = () => reject(req.error);
	});
}

function idbGet(db, name, key) {
	return new Promise((resolve, reject) => {
		const req = idbStore(db, name, "readonly").get(key);
		req.onsuccess = () => resolve(req.result || null);
		req.onerror = () => reject(req.error);
	});
}

function idbPut(db, name, value) {
	return new Promise((resolve, reject) => {
		const req = idbStore(db, name, "readwrite").put(value);
		req.onsuccess = () => resolve();
		req.onerror = () => reject(req.error);
	});
}

function idbDelete(db, name, key) {
	return new Promise((resolve, reject) => {
		const req = idbStore(db, name, "readwrite").delete(key);
		req.onsuccess = () => resolve();
		req.onerror = () => reject(req.error);
	});
}

// ---------- Criptografia (helpers globais de app.js) ----------

// sodiumReady é global (app.js).

async function decryptBlobB64(dek, b64) {
	const sodium = await sodiumReady();
	const blob = bytesB64d(b64);
	if (blob.length <= NONCEBYTES) throw new Error("blob cifrado inválido");
	const nonce = blob.slice(0, NONCEBYTES);
	const ct = blob.slice(NONCEBYTES);
	return sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
		null,
		ct,
		null,
		nonce,
		dek,
	);
}

function decryptBlobB64Sync(dek, b64) {
	const sodium = window.sodium;
	const blob = sodium.from_base64(b64, sodium.base64_variants.ORIGINAL);
	if (blob.length <= NONCEBYTES) throw new Error("blob cifrado inválido");
	const nonce = blob.slice(0, NONCEBYTES);
	const ct = blob.slice(NONCEBYTES);
	return sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
		null,
		ct,
		null,
		nonce,
		dek,
	);
}

function decryptJson(dek, b64) {
	const bytes = decryptBlobB64Sync(dek, b64);
	return JSON.parse(new TextDecoder().decode(bytes));
}

// ---------- API helper (mesma origem via proxy do web) ----------
async function apiFetch(path, { method = "GET" } = {}) {
	const res = await fetch(path, { method, credentials: "same-origin" });
	return res;
}

async function fetchAllItemPages() {
	const limit = 200;
	let cursor = "";
	const records = [];
	for (;;) {
		const params = new URLSearchParams({ limit: String(limit) });
		if (cursor) params.set("cursor", cursor);
		const res = await apiFetch(`/library/items?${params}`);
		if (!res.ok)
			throw new Error(
				`lista da biblioteca falhou (status ${res.status})`,
			);
		const page = await res.json();
		for (const item of page.items || []) {
			records.push({
				key: item.item_key,
				updated_at: item.updated_at,
				payload: item.payload,
			});
		}
		if (!page.next_cursor) break;
		cursor = page.next_cursor;
	}
	return records;
}

async function fetchItemDetail(key) {
	const res = await apiFetch(`/library/items/${key}`); // item_key: regex-validado no servidor
	if (!res.ok)
		throw new Error(`item ${key} indisponível (status ${res.status})`);
	return res.json();
}

async function fetchManifest() {
	const res = await apiFetch("/library/manifest");
	if (!res.ok) throw new Error(`manifest falhou (status ${res.status})`);
	return (await res.json()).items || [];
}

// ---------- Sincronização com o cache local (ciphertext) ----------
async function loadCorpus() {
	const dek = SF.dek;
	const manifest = await fetchManifest();
	let db = null;
	try {
		db = await idbOpen();
	} catch (err) {
		db = null; // IndexedDB indisponível/quota: segue sem cache
	}

	const records = new Map();
	if (db) {
		for (const row of await idbGetAll(db, "items")) {
			records.set(row.key, row);
		}
		// Remove do cache o que sumiu do remoto.
		const wanted = new Set(manifest.map((e) => e.item_key));
		for (const key of [...records.keys()]) {
			if (!wanted.has(key)) {
				records.delete(key);
				try {
					await idbDelete(db, "items", key);
				} catch (_) {
					/* ignore */
				}
				try {
					await idbDelete(db, "thumbs", key);
				} catch (_) {
					/* ignore */
				}
			}
		}
	}

	const fullRefresh = db == null || records.size === 0;
	const changed = fullRefresh
		? null
		: manifest.filter(
				(entry) =>
					records.get(entry.item_key)?.updated_at !==
					entry.updated_at,
			);

	if (fullRefresh) {
		const fresh = await fetchAllItemPages();
		records.clear();
		for (const record of fresh) records.set(record.key, record);
		if (db) {
			for (const record of fresh) {
				try {
					await idbPut(db, "items", record);
				} catch (_) {
					/* quota: segue sem cache */
				}
			}
		}
	} else if (changed && changed.length > 0) {
		const concurrency = 6;
		let index = 0;
		const workers = Array.from({ length: concurrency }, async () => {
			while (index < changed.length) {
				const entry = changed[index++];
				try {
					const detail = await fetchItemDetail(entry.item_key);
					const record = {
						key: entry.item_key,
						updated_at: entry.updated_at,
						payload: detail.payload,
						transcript: detail.transcript || undefined,
					};
					records.set(entry.item_key, record);
					if (db) {
						try {
							await idbPut(db, "items", record);
						} catch (_) {
							/* ignore */
						}
						// Thumb pode ter mudado; força refetch na próxima renderização.
						try {
							await idbDelete(db, "thumbs", entry.item_key);
						} catch (_) {
							/* ignore */
						}
					}
				} catch (err) {
					console.warn(
						"item não atualizado no cache",
						entry.item_key,
						err,
					);
				}
			}
		});
		await Promise.all(workers);
	}

	const items = [];
	for (const record of records.values()) {
		try {
			const payload = decryptJson(dek, record.payload);
			payload._key = record.key;
			payload._updated_at = record.updated_at;
			if (record.transcript) {
				try {
					payload._transcript = decryptJson(dek, record.transcript);
				} catch (_) {
					/* ignora */
				}
			}
			items.push(payload);
		} catch (err) {
			console.warn("item não decifrado", record.key, err);
		}
	}
	items.sort((a, b) =>
		String(b.saved_at || b.created_at || "").localeCompare(
			String(a.saved_at || a.created_at || ""),
		),
	);
	SF.items = items;
	SF.byKey = new Map(items.map((item) => [item._key, item]));
	SF.searchIndex = items.map((item) => ({
		item,
		haystack: haystackFor(item),
	}));

	return items.length;
}

function normalize(text) {
	return String(text || "")
		.normalize("NFD")
		.replace(/\p{Diacritic}/gu, "")
		.toLowerCase();
}

function haystackFor(item) {
	const tagText = (item.tags || [])
		.map((t) => `${t.slug} ${t.name}`)
		.join(" ");
	return normalize(
		[item.author, item.caption, item.collection, item.url, tagText]
			.filter(Boolean)
			.join(" "),
	);
}

function matchesQuery(entry, tokens) {
	return tokens.every((token) => entry.haystack.includes(token));
}

function queryTokens(raw) {
	return normalize(raw)
		.split(/\s+/)
		.filter((t) => t.length > 0);
}

function filteredItems() {
	const tokens = queryTokens(SF.query);
	return SF.searchIndex
		.filter((entry) => {
			if (SF.type && entry.item.post_type !== SF.type) return false;
			if (SF.status && entry.item.status !== SF.status) return false;
			if (tokens.length && !matchesQuery(entry, tokens)) return false;
			return true;
		})
		.map((entry) => entry.item);
}

// ---------- DOM helpers ----------
function el(tag, className, text) {
	const node = document.createElement(tag);
	if (className) node.className = className;
	if (text != null) node.textContent = text;
	return node;
}

function root() {
	return document.getElementById("app-root");
}

function setMain(node) {
	const main = root();
	main.textContent = "";
	main.appendChild(node);
}

function showError(message) {
	const box = el("div", "app-error");
	box.appendChild(el("p", null, message));
	const retry = el("button", "button", "Tentar de novo");
	retry.type = "button";
	retry.addEventListener("click", () => {
		void bootApp();
	});
	box.appendChild(retry);
	setMain(box);
}

const STATUS_LABELS = {
	pending: "Importado",
	enriched: "Enriquecido",
	analyzed: "Analisado",
	classified: "Classificado",
	skipped: "Ignorado",
	unavailable: "Indisponível",
	error: "Erro",
};
const TYPE_LABELS = { video: "Vídeo", image: "Imagem", carousel: "Carrossel" };
const METHOD_LABELS = { ai: "IA", heuristic: "Heurística", manual: "Manual" };

// Ported from the local web UI: IA when any auto tag carries model evidence,
// heuristic when only auto rules fired, manual when tags were hand-picked.
function classificationMethod(item) {
	const tags = item.tags || [];
	if (tags.length === 0) return null;
	const auto = tags.filter((t) => t.source === "auto");
	if (auto.length === 0) return "manual";
	const evidence = auto.map((t) => t.evidence || "").join(" ");
	return /llm:|vision:|text\+visual|ollama:|open-clip:/.test(evidence)
		? "ai"
		: "heuristic";
}


function displayTitle(item) {
	const caption = (item.caption || "").replace(/\s+/g, " ").trim();
	if (caption)
		return caption.length > 120 ? caption.slice(0, 117) + "…" : caption;
	return item.external_id || "(sem título)";
}

// Explains a classification in plain language: tag name + confidence,
// with one line of context per method plus the model's own sentence when
// there is one. Technical noise (cosine values, error codes) stays out.
function splitEvidenceParts(evidence) {
	return String(evidence || "")
		.split(/;\s*(?=(?:llm|vision|text\+visual|heuristic|no usable signal)\b)/i)
		.map((part) => part.trim())
		.filter(Boolean);
}

function llmReason(item) {
	for (const tag of item.tags || []) {
		const part = splitEvidenceParts(tag.evidence).find((p) => /^llm:/i.test(p));
		if (part) {
			const text = part.replace(/^llm:\s*/i, "").trim();
			if (text) return text.length > 220 ? `${text.slice(0, 217)}…` : text;
		}
	}
	return null;
}

function methodFoot(item) {
	const method = classificationMethod(item);
	if (method === "manual") return "Escolhidas por você.";
	if (method === "heuristic") return "Escolhidas por regras locais — a IA não participou.";
	// IA: distinguish texto, visual ou concordância dos dois.
	const evidences = (item.tags || []).flatMap((t) => splitEvidenceParts(t.evidence));
	const hasText = evidences.some((p) => /^llm:|^text\+visual$/i.test(p));
	const hasVisual = evidences.some((p) => /^vision:|^text\+visual$/i.test(p));
	if (hasText && hasVisual) return "Escolhidas pela IA a partir do texto e da imagem, que concordaram.";
	if (hasVisual) return "Escolhidas pela IA a partir da imagem.";
	return "Escolhidas pela IA a partir da legenda e da transcrição.";
}

// Info tip: shows tag name + confidence for the item's classification.
// Hover e foco (clique/toque foca) abrem o popup; o clique não navega o card.
function infoTip(item) {
	const tags = [...(item.tags || [])].sort((a, b) => {
		if (a.level === b.level) return 0;
		return a.level === "broad" ? -1 : 1;
	});
	const wrap = el("span", "info-tip");
	wrap.tabIndex = 0;
	const button = el("span", "info-tip-button", "i");
	button.setAttribute("aria-hidden", "true");
	const popup = el("span", "info-tip-popup");
	popup.setAttribute("role", "tooltip");
	popup.appendChild(el("strong", "info-tip-title", "Por que estas tags?"));
	const list = el("span", "info-tip-list");
	for (const tag of tags) {
		const row = el("span", `info-tip-row${tag.level === "broad" ? " is-broad" : ""}`);
		row.appendChild(el("span", "info-tip-row-name", tag.name || tag.slug));
		const pct = typeof tag.confidence === "number"
			? `${Math.round(tag.confidence * 100)}%`
			: "";
		row.appendChild(el("span", "info-tip-row-pct", pct));
		list.appendChild(row);
	}
	popup.appendChild(list);
	const foot = methodFoot(item);
	popup.appendChild(el("span", "info-tip-foot", foot));
	const quote = llmReason(item);
	if (quote) {
		const quoteEl = el("span", "info-tip-quote", `“${quote}”`);
		popup.appendChild(quoteEl);
	}
	wrap.appendChild(button);
	wrap.appendChild(popup);
	const flip = () => {
		const rect = wrap.getBoundingClientRect();
		wrap.classList.toggle("info-tip--to-right", rect.left < 280);
	};
	wrap.addEventListener("mouseenter", flip);
	wrap.addEventListener("focus", flip);
	wrap.addEventListener("click", (event) => {
		event.preventDefault();
		event.stopPropagation();
		flip();
	});
	wrap.addEventListener("keydown", (event) => {
		if (event.key === "Escape") wrap.blur();
	});
	return wrap;
}

function tagBadges(item, limit) {
	const wrap = el("div", "tag-row");
	const tags = (item.tags || [])
		.filter((t) => t.level === "broad")
		.concat((item.tags || []).filter((t) => t.level !== "broad"));
	for (const tag of tags.slice(0, limit || 4)) {
		const badge = el(
			"span",
			`tag-chip tag-${tag.level || "specific"}`,
			tag.name || tag.slug,
		);
		wrap.appendChild(badge);
	}
	return wrap;
}

// Detail view: name + confidence + source per tag.
function tagDetailBadges(item) {
	const wrap = el("div", "tag-row");
	const sorted = [...(item.tags || [])].sort((a, b) => {
		if (a.level === b.level)
			return String(a.name || a.slug).localeCompare(
				String(b.name || b.slug),
				"pt-BR",
			);
		return a.level === "broad" ? -1 : 1;
	});
	for (const tag of sorted) {
		const badge = el("span", `tag-chip tag-${tag.level || "specific"}`);
		if (tag.level === "broad") badge.classList.add("tag-broad");
		badge.appendChild(el("span", "tag-chip-name", tag.name || tag.slug));
		const meta = [];
		if (typeof tag.confidence === "number")
			meta.push(`${Math.round(tag.confidence * 100)}%`);
		if (tag.source) meta.push(tag.source === "manual" ? "manual" : "auto");
		if (meta.length)
			badge.appendChild(el("span", "tag-chip-meta", meta.join(" · ")));
		wrap.appendChild(badge);
	}
	return wrap;
}

function postCard(item) {
	const link = el("a", "post-card");
	link.href = `#/item/${encodeURIComponent(item.platform)}/${encodeURIComponent(item.external_id)}`;

	const media = el("div", "post-card-media");
	const img = el("img", "post-thumb");
	img.alt = displayTitle(item);
	img.loading = "lazy";
	img.dataset.thumbKey = item._key;
	img.dataset.updatedAt = item._updated_at || "";
	media.appendChild(img);
	const placeholder = el(
		"span",
		"thumb-fallback",
		TYPE_LABELS[item.post_type] || "Post",
	);
	media.appendChild(placeholder);
	link.appendChild(media);

	const body = el("div", "post-card-body");
	body.appendChild(el("h3", "post-card-title", displayTitle(item)));
	if (item.author)
		body.appendChild(el("p", "post-card-author", `@${item.author}`));
	const meta = el("div", "post-card-meta");
	if (item.post_type)
		meta.appendChild(
			el(
				"span",
				"badge badge-type",
				TYPE_LABELS[item.post_type] || item.post_type,
			),
		);
	if (item.media_count)
		meta.appendChild(
			el(
				"span",
				"badge badge-muted",
				`${item.media_count} ${item.media_count === 1 ? "peça" : "peças"}`,
			),
		);
	if (item.status)
		meta.appendChild(
			el(
				"span",
				"badge badge-status",
				STATUS_LABELS[item.status] || item.status,
			),
		);
	const method = classificationMethod(item);
	if (method) {
		const methodBadge = el(
			"span",
			`badge method-badge method-${method}`,
			METHOD_LABELS[method] || method,
		);
		meta.appendChild(methodBadge);
	}
	if (classificationMethod(item))
		meta.appendChild(infoTip(item));
	body.appendChild(meta);
	body.appendChild(tagBadges(item, 3));
	link.appendChild(body);
	return link;
}

function hydrateThumbs(scopeNode) {
	const images = scopeNode.querySelectorAll("img[data-thumb-key]");
	if (!("IntersectionObserver" in window)) {
		images.forEach(loadThumb);
		return;
	}
	const observer = new IntersectionObserver(
		(entries) => {
			for (const entry of entries) {
				if (entry.isIntersecting) {
					observer.unobserve(entry.target);
					loadThumb(entry.target);
				}
			}
		},
		{ rootMargin: "300px 0px" },
	);
	images.forEach((img) => observer.observe(img));
}

async function loadThumb(img) {
	const key = img.dataset.thumbKey;
	const updatedAt = img.dataset.updatedAt || "";
	try {
		const cached = SF.thumbUrls.get(key);
		if (cached) {
			img.src = cached;
			img.closest(".post-card-media, .item-media")?.classList.add(
				"has-thumb",
			);
			return;
		}
		let bytes = null;
		let db = null;
		try {
			db = await idbOpen();
		} catch (_) {
			db = null;
		}
		if (db) {
			const row = await idbGet(db, "thumbs", key);
			if (row && row.updated_at === updatedAt) {
				bytes = bytesB64d(row.thumb);
			}
		}
		if (!bytes) {
			const res = await apiFetch(`/library/items/${key}/thumb`); // item_key: regex-validado no servidor
			if (!res.ok) {
				// sem thumb
				if (img.dataset.variant === "tag") img.remove();
				return;
			}
			bytes = new Uint8Array(await res.arrayBuffer());
			if (db) {
				const b64 = window.sodium.to_base64(
					bytes,
					window.sodium.base64_variants.ORIGINAL,
				);
				try {
					await idbPut(db, "thumbs", {
						key,
						updated_at: updatedAt,
						thumb: b64,
					});
				} catch (_) {
					/* quota */
				}
			}
		}
		const dek = SF.dek;
		const nonce = bytes.slice(0, NONCEBYTES);
		const ct = bytes.slice(NONCEBYTES);
		const plain = window.sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
			null,
			ct,
			null,
			nonce,
			dek,
		);
		const blob = new Blob([plain], { type: "image/webp" });
		const url = URL.createObjectURL(blob);
		SF.thumbUrls.set(key, url);
		img.src = url;
		img.closest(".post-card-media, .item-media")?.classList.add(
			"has-thumb",
		);
	} catch (err) {
		console.warn("thumb não decifrado", key, err);
		if (img.dataset.variant === "tag") img.remove();
	}
}

// ---------- Views ----------
function chip(label, active, onClick) {
	const button = el("button", `chip${active ? " chip-active" : ""}`, label);
	button.type = "button";
	button.addEventListener("click", onClick);
	return button;
}

function renderLibraryView() {
	const wrap = el("section", "view view-library");
	const toolbar = el("div", "toolbar");
	const search = el("input", "search-input");
	search.type = "search";
	search.placeholder = "Buscar por legenda, autor, tag…";
	search.value = SF.query;
	search.setAttribute("aria-label", "Buscar na biblioteca");
	toolbar.appendChild(search);

	const typeSelect = el("select", "filter-select");
	typeSelect.setAttribute("aria-label", "Tipo de post");
	for (const [value, label] of [
		["", "Todos os tipos"],
		["video", "Vídeo"],
		["image", "Imagem"],
		["carousel", "Carrossel"],
	]) {
		const opt = el("option", null, label);
		opt.value = value;
		if (SF.type === value) opt.selected = true;
		typeSelect.appendChild(opt);
	}
	toolbar.appendChild(typeSelect);

	const statusSelect = el("select", "filter-select");
	statusSelect.setAttribute("aria-label", "Status");
	const statusOptions = [["", "Todos os status"]].concat(
		Object.entries(STATUS_LABELS),
	);
	for (const [value, label] of statusOptions) {
		const opt = el("option", null, label);
		opt.value = value;
		if (SF.status === value) opt.selected = true;
		statusSelect.appendChild(opt);
	}
	toolbar.appendChild(statusSelect);
	wrap.appendChild(toolbar);

	const count = el("p", "result-count");
	wrap.appendChild(count);
	const grid = el("div", "post-grid");
	wrap.appendChild(grid);
	const pageInfo = el("p", "page-info");
	wrap.appendChild(pageInfo);
	const pagination = el("div", "pagination");
	pagination.setAttribute("role", "navigation");
	pagination.setAttribute("aria-label", "Paginação da biblioteca");
	wrap.appendChild(pagination);

	const renderControls = (totalItems, totalPages) => {
		pageInfo.textContent = `Página ${SF.page} de ${totalPages} (${totalItems} ${totalItems === 1 ? "post" : "posts"})`;
		pagination.textContent = "";
		const prev = el("button", "page-button", "← Anterior");
		prev.type = "button";
		prev.disabled = SF.page <= 1;
		prev.addEventListener("click", () => {
			SF.page = Math.max(1, SF.page - 1);
			renderGrid();
		});
		pagination.appendChild(prev);

		const windowPages = pageWindow(SF.page, totalPages);
		for (const entry of windowPages) {
			if (entry === "…") {
				pagination.appendChild(el("span", "page-ellipsis", "…"));
				continue;
			}
			const button = el(
				"button",
				`page-button page-number${entry === SF.page ? " is-current" : ""}`,
				String(entry),
			);
			button.type = "button";
			if (entry === SF.page) button.setAttribute("aria-current", "page");
			button.addEventListener("click", () => {
				SF.page = entry;
				renderGrid();
			});
			pagination.appendChild(button);
		}

		const next = el("button", "page-button", "Próximo →");
		next.type = "button";
		next.disabled = SF.page >= totalPages;
		next.addEventListener("click", () => {
			SF.page = Math.min(totalPages, SF.page + 1);
			renderGrid();
		});
		pagination.appendChild(next);
	};

	const renderGrid = () => {
		grid.textContent = "";
		const items = filteredItems();
		const totalItems = items.length;
		const totalPages = Math.max(1, Math.ceil(totalItems / PAGE_SIZE));
		if (SF.page > totalPages) SF.page = totalPages;
		if (SF.page < 1) SF.page = 1;
		const start = (SF.page - 1) * PAGE_SIZE;
		const slice = items.slice(start, start + PAGE_SIZE);
		count.textContent = `${totalItems} ${totalItems === 1 ? "post" : "posts"}${SF.query ? ` para “${SF.query}”` : ""}`;
		if (slice.length === 0) {
			grid.appendChild(
				el(
					"p",
					"empty-state",
					"Nada encontrado. Ajuste a busca ou os filtros.",
				),
			);
		} else {
			for (const item of slice) grid.appendChild(postCard(item));
			hydrateThumbs(grid);
		}
		renderControls(totalItems, totalPages);
	};

	search.addEventListener("input", () => {
		SF.query = search.value;
		SF.page = 1;
		renderGrid();
	});
	typeSelect.addEventListener("change", () => {
		SF.type = typeSelect.value;
		SF.page = 1;
		renderGrid();
	});
	statusSelect.addEventListener("change", () => {
		SF.status = statusSelect.value;
		SF.page = 1;
		renderGrid();
	});

	renderGrid();
	return wrap;
}

// Janela de páginas com reticências: 1 … n-1 n n+1 … último.
function pageWindow(current, total) {
	const pages = new Set([1, total, current - 1, current, current + 1]);
	const sorted = [...pages]
		.filter((p) => p >= 1 && p <= total)
		.sort((a, b) => a - b);
	const out = [];
	let previous = 0;
	for (const page of sorted) {
		if (page - previous > 1) out.push("…");
		out.push(page);
		previous = page;
	}
	return out;
}

function broadTags() {
	const counts = new Map();
	for (const item of SF.items) {
		for (const tag of item.tags || []) {
			if (tag.level !== "broad") continue;
			const entry = counts.get(tag.slug) || {
				slug: tag.slug,
				name: tag.name || tag.slug,
				count: 0,
				items: [],
			};
			entry.count += 1;
			entry.items.push(item);
			counts.set(tag.slug, entry);
		}
	}
	return [...counts.values()].sort(
		(a, b) => b.count - a.count || a.name.localeCompare(b.name, "pt-BR"),
	);
}

function renderTagsView() {
	const wrap = el("section", "view view-tags");
	wrap.appendChild(el("p", "eyebrow", "Organização"));
	wrap.appendChild(el("h1", null, "Tags amplas"));
	const grid = el("div", "tag-grid");
	for (const entry of broadTags()) {
		const card = el("a", "tag-card");
		card.href = `#/tag/${encodeURIComponent(entry.slug)}`;
		card.appendChild(el("h2", "tag-card-name", entry.name));
		card.appendChild(
			el(
				"p",
				"tag-card-count",
				`${entry.count} ${entry.count === 1 ? "post" : "posts"}`,
			),
		);
		const strip = el("div", "tag-card-thumbs");
		for (const item of entry.items.slice(0, 4)) {
			const img = el("img", "tag-card-thumb");
			img.alt = displayTitle(item);
			img.loading = "lazy";
			img.dataset.thumbKey = item._key;
			img.dataset.updatedAt = item._updated_at || "";
			img.dataset.variant = "tag";
			strip.appendChild(img);
		}
		card.appendChild(strip);
		grid.appendChild(card);
	}
	if (grid.childElementCount === 0) {
		grid.appendChild(
			el(
				"p",
				"empty-state",
				"Nenhuma tag ampla ainda. Envie sua biblioteca com o CLI: save-finder remote push.",
			),
		);
	}
	wrap.appendChild(grid);
	hydrateThumbs(grid);
	return wrap;
}

function renderTagView(slug) {
	const wrap = el("section", "view view-tag");
	if (SF.currentTagSlug !== slug) {
		SF.specificTag = "";
		SF.currentTagSlug = slug;
	}
	const items = SF.items.filter((item) =>
		(item.tags || []).some((t) => t.level === "broad" && t.slug === slug),
	);
	const broad = items
		.flatMap((item) => item.tags || [])
		.find((t) => t.level === "broad" && t.slug === slug);

	const back = el("a", "back-link", "← Tags");
	back.href = "#/tags";
	wrap.appendChild(back);
	wrap.appendChild(el("h1", null, broad ? broad.name : slug));
	wrap.appendChild(
		el(
			"p",
			"result-count",
			`${items.length} ${items.length === 1 ? "post" : "posts"}`,
		),
	);

	// Chips de tags específicas dentro desta ampla.
	const specificCounts = new Map();
	for (const item of items) {
		for (const tag of item.tags || []) {
			if (tag.level !== "specific" || tag.parent !== slug) continue;
			specificCounts.set(tag.slug, {
				slug: tag.slug,
				name: tag.name || tag.slug,
				count: (specificCounts.get(tag.slug)?.count || 0) + 1,
			});
		}
	}
	const chips = el("div", "chip-row");
	if (specificCounts.size > 0) {
		chips.appendChild(
			chip("Todas", !SF.specificTag, () => {
				SF.specificTag = "";
				renderRoute();
			}),
		);
		for (const entry of [...specificCounts.values()].sort(
			(a, b) =>
				b.count - a.count || a.name.localeCompare(b.name, "pt-BR"),
		)) {
			chips.appendChild(
				chip(
					`${entry.name} (${entry.count})`,
					SF.specificTag === entry.slug,
					() => {
						SF.specificTag =
							SF.specificTag === entry.slug ? "" : entry.slug;
						renderRoute();
					},
				),
			);
		}
	}
	wrap.appendChild(chips);

	const visible = SF.specificTag
		? items.filter((item) =>
				(item.tags || []).some(
					(t) => t.level === "specific" && t.slug === SF.specificTag,
				),
			)
		: items;
	const grid = el("div", "post-grid");
	for (const item of visible) grid.appendChild(postCard(item));
	wrap.appendChild(grid);
	hydrateThumbs(grid);
	return wrap;
}

function renderItemView(platform, externalId) {
	const key = `${platform}:${externalId}`;
	const item = SF.byKey.get(key);
	const wrap = el("section", "view view-item");
	const back = el("a", "back-link", "← Voltar");
	back.href = "#/biblioteca";
	wrap.appendChild(back);
	if (!item) {
		wrap.appendChild(el("h1", null, "Item não encontrado"));
		wrap.appendChild(
			el("p", null, "O item não está na biblioteca sincronizada."),
		);
		showToast("error", "Item não encontrado na biblioteca.");
		return wrap;
	}

	const hero = el("div", "item-hero");
	const media = el("div", "item-media");
	const img = el("img", "item-thumb");
	img.alt = displayTitle(item);
	img.dataset.thumbKey = item._key;
	img.dataset.updatedAt = item._updated_at || "";
	media.appendChild(img);
	media.appendChild(
		el("span", "thumb-fallback", TYPE_LABELS[item.post_type] || "Post"),
	);
	hero.appendChild(media);

	const body = el("div", "item-body");
	body.appendChild(el("h1", "item-title", displayTitle(item)));
	if (item.author)
		body.appendChild(el("p", "post-card-author", `@${item.author}`));
	if (item.caption) {
		const caption = el("p", "item-caption", item.caption);
		body.appendChild(caption);
	}
	const meta = el("dl", "item-meta");
	const metaRows = [
		["Plataforma", item.platform],
		[
			"Tipo",
			item.post_type
				? TYPE_LABELS[item.post_type] || item.post_type
				: null,
		],
		["Peças", item.media_count ? String(item.media_count) : null],
		[
			"Duração",
			item.duration_seconds
				? `${Math.round(item.duration_seconds)}s`
				: null,
		],
		["Coleção", item.collection || null],
		["Salvo em", formatDate(item.saved_at)],
		["Status", STATUS_LABELS[item.status] || item.status],
	];
	for (const [label, value] of metaRows) {
		if (!value) continue;
		const row = el("div", "item-meta-row");
		row.appendChild(el("dt", null, label));
		row.appendChild(el("dd", null, value));
		meta.appendChild(row);
	}
	body.appendChild(meta);
	const tagsHead = el("div", "item-tags-head");
	tagsHead.appendChild(el("p", "item-tags-label", "Tags de classificação"));
	const method = classificationMethod(item);
	if (method)
		tagsHead.appendChild(
			el(
				"span",
				`badge method-badge method-${method}`,
				METHOD_LABELS[method] || method,
			),
		);
	if (classificationMethod(item))
		tagsHead.appendChild(infoTip(item));
	body.appendChild(tagsHead);
	if (method) {
		const legend = {
			ai: "Tags decididas pelo modelo (LLM e/ou visão).",
			heuristic:
				"Tags escolhidas por heurística, o LLM não decidiu (indisponível, resposta inválida ou sem sinal).",
			manual: "Tags ajustadas manualmente por você.",
		};
		body.appendChild(el("p", "item-legend", legend[method] || ""));
	}
	body.appendChild(tagDetailBadges(item));
	if (item.url) {
		const link = el(
			"a",
			"button button-secondary",
			"Abrir post original ↗",
		);
		link.href = item.url;
		link.target = "_blank";
		link.rel = "noopener noreferrer";
		body.appendChild(link);
	}
	hero.appendChild(body);
	wrap.appendChild(hero);

	// A seção de transcrição só aparece se houver transcript — post de imagem
	// ou vídeo sem fala simplesmente não mostra o botão.
	const transcriptSection = el("section", "item-transcript");
	transcriptSection.hidden = true;
	const toggle = el("button", "chip", "Mostrar transcrição");
	toggle.type = "button";
	const pre = el("pre", "transcript-text");
	pre.hidden = true;
	transcriptSection.appendChild(toggle);
	transcriptSection.appendChild(pre);
	wrap.appendChild(transcriptSection);

	getTranscript(item).then((transcript) => {
		if (!transcript) return;
		transcriptSection.hidden = false;
		let loaded = false;
		toggle.addEventListener("click", () => {
			pre.hidden = !pre.hidden;
			if (!loaded) {
				pre.textContent = transcript.text;
				loaded = true;
			}
			toggle.textContent = pre.hidden
				? "Mostrar transcrição"
				: "Ocultar transcrição";
		});
	});

	hydrateThumbs(wrap);
	return wrap;
}

function formatDate(value) {
	if (!value) return null;
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) return String(value);
	return date.toLocaleDateString("pt-BR", {
		day: "2-digit",
		month: "short",
		year: "numeric",
	});
}

const transcriptCache = new Map();

async function getTranscript(item) {
	if (transcriptCache.has(item._key)) return transcriptCache.get(item._key);
	let transcript = item._transcript || null;
	if (!transcript) {
		try {
			const detail = await fetchItemDetail(item._key);
			if (detail.transcript) {
				transcript = decryptJson(SF.dek, detail.transcript);
				try {
					const db = await idbOpen();
					const row = await idbGet(db, "items", item._key);
					if (row)
						await idbPut(db, "items", {
							...row,
							transcript: detail.transcript,
						});
				} catch (_) {
					/* cache opcional */
				}
			}
		} catch (err) {
			console.warn("transcrição indisponível", item._key, err);
			showToast("error", "Não foi possível carregar a transcrição.");
		}
	}
	transcriptCache.set(item._key, transcript);
	return transcript;
}

// ---------- Roteamento ----------
function renderRoute() {
	const hash = window.location.hash || "#/biblioteca";
	let view;
	const itemMatch = hash.match(/^#\/item\/([^/]+)\/([^/]+)$/);
	const tagMatch = hash.match(/^#\/tag\/([^/]+)$/);
	if (itemMatch) {
		view = renderItemView(
			decodeURIComponent(itemMatch[1]),
			decodeURIComponent(itemMatch[2]),
		);
	} else if (tagMatch) {
		view = renderTagView(decodeURIComponent(tagMatch[1]));
	} else if (hash.startsWith("#/tags")) {
		view = renderTagsView();
	} else {
		view = renderLibraryView();
	}
	setMain(view);
	markActiveNav(hash);
	window.scrollTo(0, 0);
}

function markActiveNav(hash) {
	const active =
		hash.startsWith("#/tags") || hash.startsWith("#/tag/")
			? "tags"
			: "biblioteca";
	for (const link of document.querySelectorAll("#app-nav a[data-nav]")) {
		link.classList.toggle("nav-active", link.dataset.nav === active);
	}
}

// ---------- Telas de autenticação / desbloqueio ----------
function setChromeVisible(visible, username) {
	const bar = document.querySelector(".appbar");
	if (bar) bar.hidden = !visible;
	for (const id of [
		"app-nav",
		"app-username",
		"app-change-password",
		"app-logout",
	]) {
		const node = document.getElementById(id);
		if (node) node.hidden = !visible;
	}
	if (username)
		document.getElementById("app-username").textContent = username;
}

function renderAuthView() {
	setChromeVisible(false);
	const wrap = el("section", "view view-gate");
	const card = el("div", "gate-card");
	card.innerHTML = `
		<p class="eyebrow">Acesso</p>
		<h1>Entrar</h1>
		<p class="lede">Use o e-mail ou usuário e sua senha. A mesma senha desbloqueia seus dados cifrados.</p>
		<p class="feedback" role="alert" hidden></p>
		<form data-form="login" novalidate>
			<div class="field">
				<label for="identifier">E-mail ou usuário</label>
				<input id="identifier" name="identifier" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required/>
			</div>
			<div class="field">
				<label for="password">Senha</label>
				<input id="password" name="password" type="password" autocomplete="current-password" minlength="8" required/>
			</div>
			<div class="row">
				<label class="checkbox"><input type="checkbox" name="remember_me"/> Lembrar de mim</label>
			</div>
			<button class="button" type="submit" data-submit>Entrar</button>
		</form>
		<p class="switch">Ainda não tem conta? <a href="/register">Criar conta</a></p>`;
	wrap.appendChild(card);
	wrap.appendChild(el("p", "foot", "SaveFinder · seus dados ficam com você, cifrados ponta a ponta."));
	setMain(wrap);
	const form = card.querySelector('form[data-form="login"]');
	const button = form.querySelector("[data-submit]");
	form.addEventListener("submit", (event) => {
		event.preventDefault();
		// handleLogin é global (app.js) e redireciona via SaveFinderUnlockAndShow.
		if (typeof window.handleLogin === "function") {
			window.handleLogin(form, event.submitter || button).catch(() => {
				if (typeof window.setLoading === "function")
					window.setLoading(button, false);
			});
		}
	});
}

function renderUnlockView() {
	setChromeVisible(false);
	const wrap = el("section", "view view-gate");
	const card = el("div", "gate-card");
	const who = SF.account
		? escapeHtml(SF.account.username || SF.account.email || "")
		: "";
	card.innerHTML = `
		<p class="eyebrow">Sessão ativa</p>
		<h1>Desbloquear</h1>
		<p class="lede">${who ? `Você entrou como <strong>${who}</strong>. ` : ""}Digite sua senha de novo para desembrulhar a chave local — ela nunca sai deste navegador.</p>
		<p class="feedback" role="alert" hidden></p>
		<form data-form="unlock" novalidate>
			<div class="field">
				<label for="unlock_password">Senha</label>
				<input id="unlock_password" name="unlock_password" type="password" autocomplete="current-password" required/>
			</div>
			<button class="button" type="submit" data-submit>Desbloquear</button>
		</form>
		<p class="switch"><a href="/auth/logout-via-form" id="unlock-logout">Sair desta conta</a></p>`;
	wrap.appendChild(card);
	wrap.appendChild(el("p", "foot", "SaveFinder · seus dados ficam com você, cifrados ponta a ponta."));
	setMain(wrap);

	const form = card.querySelector('form[data-form="unlock"]');
	const button = form.querySelector("[data-submit]");
	const feedback = card.querySelector(".feedback");
	form.addEventListener("submit", async (event) => {
		event.preventDefault();
		feedback.hidden = true;
		button.disabled = true;
		button.textContent = "Desbloqueando…";
		try {
			const res = await apiFetch("/auth/crypto");
			if (!res.ok)
				throw new Error(`crypto metadata (status ${res.status})`);
			const meta = await res.json();
			const unwrapped = await window.unwrapDek(
				form.unlock_password.value,
				meta,
			);
			if (!unwrapped) throw new Error("senha incorreta");
			window.SaveFinderUnlockAndShow(unwrapped, null);
		} catch (err) {
			feedback.hidden = false;
			feedback.className = "feedback is-visible is-error";
			feedback.textContent =
				"Não foi possível desbloquear. Verifique sua senha.";
			button.disabled = false;
			button.textContent = "Desbloquear";
		}
	});
	const logoutLink = card.querySelector("#unlock-logout");
	logoutLink.addEventListener("click", async (event) => {
		event.preventDefault();
		await apiFetch("/auth/logout", { method: "POST" });
		window.location.assign("/");
	});
}

function escapeHtml(text) {
	const div = document.createElement("div");
	div.textContent = String(text);
	return div.innerHTML;
}

// ---------- Toast (feedback global) ----------
let toastTimer = null;
function showToast(type, message) {
	const toast = document.getElementById("app-toast");
	if (!toast) return;
	toast.hidden = false;
	toast.className = `app-toast is-${type}`;
	toast.textContent = message;
	clearTimeout(toastTimer);
	toastTimer = setTimeout(() => {
		toast.hidden = true;
	}, 4500);
}

async function enterApp(dek, csrfToken) {
	SF.dek = dek;
	SF.csrf = csrfToken;
	setChromeVisible(
		true,
		SF.account && (SF.account.username || SF.account.email),
	);
	const main = root();
	main.textContent = "";
	main.appendChild(
		el("p", "app-loading", "Descriptografando sua biblioteca…"),
	);
	try {
		await sodiumReady();
		const loaded = await loadCorpus();
		showToast(
			"success",
			`${loaded} ${loaded === 1 ? "post carregado" : "posts carregados"}.`,
		);
		renderRoute();
	} catch (err) {
		console.error(err);
		showError(
			"Não foi possível carregar sua biblioteca cifrada. Verifique a conexão e tente de novo.",
		);
	}
}

async function bootApp() {
	const main = root();
	main.textContent = "";
	main.appendChild(el("p", "app-loading", "Carregando…"));
	try {
		const res = await apiFetch("/auth/account");
		if (!res.ok) {
			renderAuthView();
			return;
		}
		const body = await res.json();
		SF.account = body.user || body;
		SF.csrf = body.csrf_token || SF.csrf;
		renderUnlockView();
	} catch (err) {
		console.error(err);
		showError("Não foi possível falar com o servidor. Tente de novo.");
	}
}

window.SaveFinderUnlockAndShow = (dek, csrfToken) => {
	if (!SF.account) {
		// Veio do fluxo de login/registro dentro da casca.
		apiFetch("/auth/account")
			.then((res) => res.json())
			.then((body) => {
				SF.account = body.user || body;
			})
			.catch(() => {})
			.finally(() => {
				void enterApp(dek, csrfToken);
			});
		return;
	}
	void enterApp(dek, csrfToken);
};

window.addEventListener("hashchange", () => {
	if (SF.dek) renderRoute();
});

document.addEventListener("DOMContentLoaded", () => {
	if (document.body.dataset.page !== "app") return;
	void bootApp();
});
