/* SaveFinder — tema, auth e E2EE (DEK/KEK) do lado do cliente.
   A senha nunca sai desta página; a KEK/DEK nunca vão ao servidor. */

'use strict';

// ---------- Tema (Protea claro/escuro) ----------
(function initTheme() {
	const saved = localStorage.getItem('theme');
	const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
	document.documentElement.dataset.theme = saved || (prefersDark ? 'dark' : 'light');
})();

document.addEventListener('click', (event) => {
	const toggle = event.target.closest('[data-theme-toggle]');
	if (!toggle) return;
	const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
	document.documentElement.dataset.theme = next;
	localStorage.setItem('theme', next);
});

// ---------- E2EE (KEK via Argon2id, DEK aleatória, XChaCha20-Poly1305) ----------

const KEK_KEYBYTES = 32;
const DEK_KEYBYTES = 32;
const SALTBYTES = 16;
const NONCEBYTES = 24;
const ARGON_OPS = 2; // OWASP minimum: 2 iterations
const ARGON_MEM_KIB = 19456; // 19 MiB, OWASP minimum
const ARGON_MEM_BYTES = ARGON_MEM_KIB * 1024;
const ARGON_PAR = 1;

// DEK vive só na memória desta página — nunca em storage.
let dek = null;
let csrfToken = null;

const bytesB64e = (bytes) => window.sodium.to_base64(bytes, window.sodium.base64_variants.ORIGINAL);
const bytesB64d = (text) => window.sodium.from_base64(text, window.sodium.base64_variants.ORIGINAL);

async function sodiumReady() {
	if (!window.sodium) throw new Error('biblioteca de criptografia não carregou');
	try {
		await window.sodium.ready;
	} catch (err) {
		throw new Error('biblioteca de criptografia não inicializou');
	}
	// O build padrão do libsodium não conhece argon2id; usamos hash-wasm para isso.
	if (!window.hashwasm || !window.hashwasm.argon2id) {
		throw new Error('hash-wasm não carregou');
	}
	const argon2id = window.hashwasm.argon2id;
	return { sodium: window.sodium, argon2id };
}

async function deriveKek(password, salt) {
	const { argon2id } = await sodiumReady();
	const kek = await argon2id({
		password,
		salt,
		parallelism: ARGON_PAR,
		iterations: ARGON_OPS,
		memorySize: ARGON_MEM_KIB,
		hashLength: KEK_KEYBYTES,
		outputType: 'binary',
	});
	return kek;
}

async function wrapNewDek(password) {
	const { sodium } = await sodiumReady();
	const salt = sodium.randombytes_buf(SALTBYTES);
	const nonce = sodium.randombytes_buf(NONCEBYTES);
	const dekBytes = sodium.randombytes_buf(DEK_KEYBYTES);
	const kek = await deriveKek(password, salt);
	const wrapped = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(dekBytes, null, null, nonce, kek);
	return {
		dek: dekBytes,
		payload: {
			salt: bytesB64e(salt),
			wrapped_dek: bytesB64e(wrapped),
			dek_nonce: bytesB64e(nonce),
			kdf_ops_limit: ARGON_OPS,
			kdf_mem_limit: ARGON_MEM_BYTES,
			current_password: password,
		},
	};
}

async function unwrapDek(password, metadata) {
	const { sodium } = await sodiumReady();
	const kek = await deriveKek(password, bytesB64d(metadata.salt));
	try {
		const dekBytes = sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
			null,
			bytesB64d(metadata.wrapped_dek),
			null,
			bytesB64d(metadata.dek_nonce),
			kek,
		);
		return dekBytes;
	} catch (err) {
		return null;
	}
}

// ---------- API helper (mesma origem: o web faz proxy de /auth/* para a API) ----------

async function api(path, { method = 'GET', body, csrf } = {}) {
	const headers = { 'Content-Type': 'application/json' };
	if (csrf) headers['X-CSRF-Token'] = csrf;
	const res = await fetch(path, {
		method,
		credentials: 'same-origin',
		headers,
		body: body ? JSON.stringify(body) : undefined,
	});
	let json = null;
	try {
		json = await res.json();
	} catch (err) {
		json = null;
	}
	return { status: res.status, json };
}

// ---------- UI helpers ----------

function setLoading(button, loading, label) {
	if (!button) return;
	button.disabled = loading;
	if (loading) {
		button.dataset.previousText = button.textContent;
		button.textContent = label || 'Aguarde…';
	} else if (button.dataset.previousText) {
		button.textContent = button.dataset.previousText;
	}
}

function showFeedback(type, message) {
	const box = document.querySelector('.feedback');
	if (!box) return;
	box.hidden = false;
	box.className = `feedback is-visible is-${type}`;
	box.textContent = message;
}

function clearFeedback() {
	const box = document.querySelector('.feedback');
	if (box) {
		box.hidden = true;
		box.className = 'feedback';
		box.textContent = '';
	}
}

function friendlyError(json, fallback) {
	const code = json && json.error && json.error.code;
	switch (code) {
		case 'email_taken':
			return 'Este e-mail já está em uso.';
		case 'username_taken':
			return 'Este usuário já está em uso.';
		case 'invalid_credentials':
			return 'E-mail/usuário ou senha incorretos.';
		case 'rate_limited':
			return 'Muitas tentativas. Aguarde alguns minutos e tente de novo.';
		case 'validation_failed':
			return 'Verifique os campos informados.';
		default:
			return fallback;
	}
}

function validUsername(u) {
	return /^[a-z0-9_.-]{3,30}$/.test(u);
}

function validEmail(e) {
	return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e);
}

// ---------- Handlers por página ----------

async function handleRegister(form, button) {
	clearFeedback();
	const email = form.email.value.trim().toLowerCase();
	const username = form.username.value.trim().toLowerCase();
	const password = form.password.value;
	const confirmation = form.password_confirmation.value;

	if (!validEmail(email)) return showFeedback('error', 'Informe um e-mail válido.');
	if (!validUsername(username)) return showFeedback('error', 'Usuário deve ter 3–30 caracteres: a–z, 0–9, "_", "." ou "-".');
	if (password.length < 8) return showFeedback('error', 'A senha deve ter ao menos 8 caracteres.');
	if (password !== confirmation) return showFeedback('error', 'As senhas não coincidem.');

	setLoading(button, true, 'Criando conta…');
	const reg = await api('/auth/register', {
		method: 'POST',
		body: { email, username, password, password_confirmation: confirmation },
	});
	if (reg.status !== 201) {
		setLoading(button, false);
		return showFeedback('error', friendlyError(reg.json, 'Não foi possível criar a conta.'));
	}
	csrfToken = reg.json.csrf_token;

	// Criptografia ponta a ponta: cria a DEK e envia só a DEK cifrada.
	setLoading(button, true, 'Cifrando sua conta…');
	const { dek: newDek, payload } = await wrapNewDek(password);
	const setup = await api('/auth/crypto', { method: 'POST', csrf: csrfToken, body: payload });
	if (setup.status !== 201) {
		setLoading(button, false);
		window.__pendingCryptoRetry = payload;
		showFeedback('error', 'Conta criada, mas não foi possível configurar a criptografia: ' +
			friendlyError(setup.json, 'erro desconhecido') +
			' — toque no botão novamente para tentar de novo.');
		button.addEventListener('click', async function retryOnce() {
			button.removeEventListener('click', retryOnce);
			setLoading(button, true, 'Cifrando sua conta…');
			const again = await api('/auth/crypto', { method: 'POST', csrf: csrfToken, body: window.__pendingCryptoRetry });
			if (again.status === 201) {
				window.location.assign('/');
			} else {
				setLoading(button, false);
				showFeedback('error', 'Ainda não foi possível configurar a criptografia. Tente sair e entrar novamente.');
			}
		});
		button.textContent = 'Tentar novamente';
		button.disabled = false;
		return;
	}
	dek = newDek;
	window.location.assign('/');
}

async function handleLogin(form, button) {
	clearFeedback();
	const identifier = form.identifier.value.trim().toLowerCase();
	const password = form.password.value;
	const rememberMe = form.remember_me ? form.remember_me.checked : false;

	if (!identifier || !password) return showFeedback('error', 'Informe e-mail/usuário e senha.');

	setLoading(button, true, 'Entrando…');
	const login = await api('/auth/login', {
		method: 'POST',
		body: { identifier, password, remember_me: rememberMe },
	});
	if (login.status !== 200) {
		setLoading(button, false);
		return showFeedback('error', friendlyError(login.json, 'Não foi possível entrar.'));
	}
	csrfToken = login.json.csrf_token;

	// Destrava a DEK localmente com a senha digitada.
	setLoading(button, true, 'Desbloqueando seus dados…');
	const meta = await api('/auth/crypto');
	if (meta.status === 404) {
		// Conta sem metadados de chave (legado): configura agora.
		const { dek: newDek, payload } = await wrapNewDek(password);
		const setup = await api('/auth/crypto', { method: 'POST', csrf: csrfToken, body: payload });
		if (setup.status !== 201) {
			setLoading(button, false);
			return showFeedback('error', 'Conta autenticada, mas falhou ao criar a chave de criptografia. Tente novamente.');
		}
		dek = newDek;
		window.location.assign('/');
		return;
	}
	if (meta.status !== 200) {
		setLoading(button, false);
		return showFeedback('error', 'Não foi possível carregar sua chave. Tente novamente.');
	}
	const unwrapped = await unwrapDek(password, meta.json);
	if (!unwrapped) {
		setLoading(button, false);
		return showFeedback('error', 'Não foi possível desbloquear a chave de criptografia.');
	}
	dek = unwrapped;
	// Na casca principal (/), casa a transição para o app sem recarregar a página.
	if (typeof window.SaveFinderUnlockAndShow === 'function') {
		window.SaveFinderUnlockAndShow(dek, csrfToken);
	} else {
		window.location.assign('/');
	}
}

async function handleChangePassword(form, button) {
	clearFeedback();
	const current = form.current_password.value;
	const next = form.new_password.value;
	const confirmation = form.new_password_confirmation.value;

	if (!current || !next) return showFeedback('error', 'Preencha os campos.');
	if (next.length < 8) return showFeedback('error', 'A nova senha deve ter ao menos 8 caracteres.');
	if (next !== confirmation) return showFeedback('error', 'As senhas não coincidem.');
	if (next === current) return showFeedback('error', 'A nova senha deve ser diferente da atual.');

	setLoading(button, true, 'Atualizando…');
	// Re-wrap: desembrulha a DEK com a senha atual e embrulha de novo com a nova.
	const meta = await api('/auth/crypto');
	if (meta.status !== 200) {
		setLoading(button, false);
		return showFeedback('error', 'Não foi possível preparar a troca. Tente novamente.');
	}
	const dekBytes = await unwrapDek(current, meta.json);
	if (!dekBytes) {
		setLoading(button, false);
		return showFeedback('error', 'Senha atual incorreta.');
	}
	const { sodium } = await sodiumReady();
	const newSalt = sodium.randombytes_buf(SALTBYTES);
	const newNonce = sodium.randombytes_buf(NONCEBYTES);
	const newKek = await deriveKek(next, newSalt);
	const newWrapped = sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(dekBytes, null, null, newNonce, newKek);

	// Busca o csrf token da sessão ativa (via /auth/account).
	const account = await api('/auth/account');
	const csrf = account.json && account.json.csrf_token;
	if (!csrf) {
		setLoading(button, false);
		return showFeedback('error', 'Sessão expirada. Entre novamente.');
	}

	const updated = await api('/auth/account/password', {
		method: 'PUT',
		csrf,
		body: {
			current_password: current,
			new_password: next,
			new_password_confirmation: confirmation,
			new_salt: bytesB64e(newSalt),
			new_wrapped_dek: bytesB64e(newWrapped),
			new_dek_nonce: bytesB64e(newNonce),
			new_kdf_ops_limit: ARGON_OPS,
			new_kdf_mem_limit: ARGON_MEM_BYTES,
		},
	});
	if (updated.status !== 204) {
		setLoading(button, false);
		return showFeedback('error', friendlyError(updated.json, 'Não foi possível trocar a senha.'));
	}
	dek = dekBytes;
	showFeedback('success', 'Senha alterada. Seus dados continuam acessíveis com a nova senha.');
	setLoading(button, false);
	setTimeout(() => window.location.assign('/'), 1200);
}

async function handleLogout(button) {
	clearFeedback();
	setLoading(button, true, 'Saindo…');
	await api('/auth/logout', { method: 'POST' });
	dek = null;
	csrfToken = null;
	window.location.assign('/login');
}

// ---------- Bootstrap por página ----------

document.addEventListener('DOMContentLoaded', () => {
	const form = document.querySelector('form[data-form]');
	const button = form ? form.querySelector('[data-submit]') : null;
	if (!form || !button) return;

	form.addEventListener('submit', (event) => {
		event.preventDefault();
		const submit = event.submitter || button;
		// O handler é responsável pelo estado de loading; o catch do listener
		// garante restauração em qualquer erro inesperado.
		switch (form.dataset.form) {
			case 'register':
				handleRegister(form, submit).catch(() => {
					setLoading(submit, false);
					showFeedback('error', 'Erro inesperado. Tente novamente.');
				});
				break;
			case 'login':
				handleLogin(form, submit).catch(() => {
					setLoading(submit, false);
					showFeedback('error', 'Erro inesperado. Tente novamente.');
				});
				break;
			case 'change-password':
				handleChangePassword(form, submit).catch(() => {
					setLoading(submit, false);
					showFeedback('error', 'Erro inesperado. Tente novamente.');
				});
				break;
			case 'logout':
				handleLogout(submit);
				break;
		}
	});
});
