package auth_test

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/google/uuid"

	"save-finder/api/internal/auth"
	"save-finder/api/internal/db"
	"save-finder/api/internal/httpserver"
	"save-finder/api/internal/keys"
	"save-finder/api/internal/ratelimit"
	"save-finder/api/internal/sessions"
	"save-finder/api/internal/users"
)

var testServer *httptest.Server
var testPoolCleanup func()

func TestMain(m *testing.M) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		fmt.Fprintln(os.Stderr, "TEST_DATABASE_URL not set; skipping integration tests")
		os.Exit(0)
	}
	ctx := context.Background()
	if err := db.Migrate(ctx, dsn); err != nil {
		fmt.Fprintf(os.Stderr, "migrate: %v\n", err)
		os.Exit(1)
	}
	pool, err := db.Connect(ctx, dsn)
	if err != nil {
		fmt.Fprintf(os.Stderr, "connect: %v\n", err)
		os.Exit(1)
	}
	testPoolCleanup = pool.Close

	sessionStore := sessions.NewStore(pool)
	handler := auth.NewHandler(
		users.NewStore(pool), sessionStore, false,
		ratelimit.NewRiskLimiter(),
		ratelimit.NewIdentityLimiter(1000),
		ratelimit.NewIdentityLimiter(1000),
		slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError})),
		keys.NewStore(pool),
	)
	testServer = httptest.NewServer(httpserver.New(httpserver.Deps{
		Handler: handler,
		Crypto: keys.NewHandler(keys.NewStore(pool), users.NewStore(pool),
			slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))),
		Logger: slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError})),
		Ready:  pool.Ping,
	}))
	code := m.Run()
	testServer.Close()
	testPoolCleanup()
	os.Exit(code)
}

func newClient(t *testing.T) *http.Client {
	t.Helper()
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return &http.Client{Jar: jar, Timeout: 10 * time.Second}
}

func unique(prefix string) string {
	return fmt.Sprintf("%s-%s", prefix, uuid.NewString()[:8])
}

func doJSON(t *testing.T, c *http.Client, method, path string, body any, headers map[string]string) *http.Response {
	t.Helper()
	var buf bytes.Buffer
	if body != nil {
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			t.Fatal(err)
		}
	}
	req, err := http.NewRequest(method, testServer.URL+path, &buf)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := c.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func readBody(t *testing.T, resp *http.Response) []byte {
	t.Helper()
	defer resp.Body.Close()
	buf := new(bytes.Buffer)
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		t.Fatal(err)
	}
	return buf.Bytes()
}

type authBody struct {
	User struct {
		ID       string `json:"id"`
		Email    string `json:"email"`
		Username string `json:"username"`
	} `json:"user"`
	CSRFToken string `json:"csrf_token"`
}

func register(t *testing.T, c *http.Client, email, username, pw string) (int, authBody) {
	t.Helper()
	resp := doJSON(t, c, http.MethodPost, "/auth/register", map[string]any{
		"email":                 email,
		"username":              username,
		"password":              pw,
		"password_confirmation": pw,
	}, nil)
	body := readBody(t, resp)
	var out authBody
	if resp.StatusCode == http.StatusCreated {
		if err := json.Unmarshal(body, &out); err != nil {
			t.Fatal(err)
		}
	}
	return resp.StatusCode, out
}

func csrfHeaders(token string) map[string]string {
	return map[string]string{"X-CSRF-Token": token}
}

func TestRegisterLoginAndAccountFlow(t *testing.T) {
	c := newClient(t)
	email := unique("ana") + "@example.com"
	username := unique("ana")
	password := "senha-super-secreta-1"

	code, reg := register(t, c, email, username, password)
	if code != http.StatusCreated {
		t.Fatalf("register: got %d", code)
	}
	if reg.User.Email != email || reg.CSRFToken == "" {
		t.Fatalf("unexpected register response: %+v", reg)
	}

	// Authenticated GET works right after register (auto-login).
	resp := doJSON(t, c, http.MethodGet, "/auth/account", nil, nil)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get account: got %d body=%s", resp.StatusCode, body)
	}

	// Mutating without CSRF token must be rejected.
	resp = doJSON(t, c, http.MethodPatch, "/auth/account", map[string]any{"username": unique("novouser")}, nil)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("patch without csrf: got %d", resp.StatusCode)
	}
	readBody(t, resp)

	// With CSRF token it works.
	newUsername := unique("novouser")
	resp = doJSON(t, c, http.MethodPatch, "/auth/account", map[string]any{"username": newUsername}, csrfHeaders(reg.CSRFToken))
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("patch: got %d body=%s", resp.StatusCode, readBody(t, resp))
	}
	readBody(t, resp)

	// Sessions list contains the current session.
	resp = doJSON(t, c, http.MethodGet, "/auth/sessions", nil, nil)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list sessions: got %d", resp.StatusCode)
	}
	var sessionsResp struct {
		Sessions []struct {
			ID      string `json:"id"`
			Current bool   `json:"current"`
		} `json:"sessions"`
	}
	if err := json.Unmarshal(body, &sessionsResp); err != nil {
		t.Fatal(err)
	}
	if len(sessionsResp.Sessions) != 1 || !sessionsResp.Sessions[0].Current {
		t.Fatalf("expected exactly one current session, got %s", body)
	}

	// Logout, then account must be 401.
	resp = doJSON(t, c, http.MethodPost, "/auth/logout", nil, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("logout: got %d", resp.StatusCode)
	}
	resp = doJSON(t, c, http.MethodGet, "/auth/account", nil, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("account after logout: got %d", resp.StatusCode)
	}

	// Log in again with identifier = (new) username, case-insensitively.
	loginResp := doJSON(t, c, http.MethodPost, "/auth/login", map[string]any{
		"identifier": newUsername, "password": password,
	}, nil)
	loginBody := readBody(t, loginResp)
	if loginResp.StatusCode != http.StatusOK {
		t.Fatalf("login: got %d body=%s", loginResp.StatusCode, loginBody)
	}
}

func TestDuplicateEmailAndUsername(t *testing.T) {
	c1, c2 := newClient(t), newClient(t)
	email := unique("dup") + "@example.com"
	username := unique("dup")

	code, _ := register(t, c1, email, username, "senha-super-secreta-1")
	if code != http.StatusCreated {
		t.Fatalf("first register: got %d", code)
	}

	// Same email, different username.
	resp := doJSON(t, c2, http.MethodPost, "/auth/register", map[string]any{
		"email": email, "username": unique("outr"),
		"password": "senha-super-secreta-2", "password_confirmation": "senha-super-secreta-2",
	}, nil)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("duplicate email: got %d", resp.StatusCode)
	}
	if !bytes.Contains(body, []byte("email_taken")) {
		t.Fatalf("expected email_taken, got %s", body)
	}

	// Same username, different email.
	resp = doJSON(t, c2, http.MethodPost, "/auth/register", map[string]any{
		"email": unique("dup") + "@example.com", "username": username,
		"password": "senha-super-secreta-2", "password_confirmation": "senha-super-secreta-2",
	}, nil)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusConflict || !bytes.Contains(body, []byte("username_taken")) {
		t.Fatalf("duplicate username: got %d body=%s", resp.StatusCode, body)
	}
}

func TestLoginWrongPassword(t *testing.T) {
	c := newClient(t)
	email := unique("wrong") + "@example.com"
	username := unique("wrong")
	register(t, c, email, username, "senha-super-secreta-1")

	// Fresh client, no cookie.
	c2 := newClient(t)
	resp := doJSON(t, c2, http.MethodPost, "/auth/login", map[string]any{
		"identifier": email, "password": "senha-errada-123",
	}, nil)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("wrong password: got %d", resp.StatusCode)
	}
	if !bytes.Contains(body, []byte("invalid_credentials")) {
		t.Fatalf("expected invalid_credentials, got %s", body)
	}
}

func TestChangePasswordRevokesOtherSessions(t *testing.T) {
	c1, c2 := newClient(t), newClient(t)
	email := unique("pw") + "@example.com"
	username := unique("pw")
	password := "senha-super-secreta-1"
	code, reg := register(t, c1, email, username, password)
	if code != http.StatusCreated {
		t.Fatalf("register: got %d", code)
	}

	// Second session for the same account.
	loginResp := doJSON(t, c2, http.MethodPost, "/auth/login", map[string]any{
		"identifier": email, "password": password,
	}, nil)
	loginBody := readBody(t, loginResp)
	var loginAuth authBody
	if err := json.Unmarshal(loginBody, &loginAuth); err != nil {
		t.Fatal(err)
	}

	// Change password from the first session.
	resp := doJSON(t, c1, http.MethodPut, "/auth/account/password", map[string]any{
		"current_password":          password,
		"new_password":              "nova-senha-super-secreta",
		"new_password_confirmation": "nova-senha-super-secreta",
	}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("change password: got %d", resp.StatusCode)
	}

	// The second session must be revoked, the current one kept.
	resp = doJSON(t, c2, http.MethodGet, "/auth/account", nil, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("other session should be revoked, got %d", resp.StatusCode)
	}
	resp = doJSON(t, c1, http.MethodGet, "/auth/account", nil, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("current session should stay valid, got %d", resp.StatusCode)
	}
}

func TestChangePasswordAtomicRewrap(t *testing.T) {
	c := newClient(t)
	email := unique("rewrap") + "@example.com"
	username := unique("rewrap")
	password := "senha-super-secreta-1"
	newPassword := "nova-senha-super-secreta-2"

	code, reg := register(t, c, email, username, password)
	if code != http.StatusCreated {
		t.Fatalf("register: got %d", code)
	}

	// Set up crypto metadata (valid, arbitrary blobs).
	originalSalt := bytes.Repeat([]byte{0xAB}, 16)
	setup := map[string]any{
		"salt":             base64.StdEncoding.EncodeToString(originalSalt),
		"wrapped_dek":      base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
		"dek_nonce":        base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 24)),
		"kdf_ops_limit":    3,
		"kdf_mem_limit":    67108864,
		"current_password": password,
	}
	resp := doJSON(t, c, http.MethodPost, "/auth/crypto", setup, csrfHeaders(reg.CSRFToken))
	setupBody := readBody(t, resp)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("setup crypto: got %d body=%s", resp.StatusCode, setupBody)
	}

	// Partial re-wrap block must be rejected.
	resp = doJSON(t, c, http.MethodPut, "/auth/account/password", map[string]any{
		"current_password": password, "new_password": newPassword,
		"new_password_confirmation": newPassword, "new_salt": base64.StdEncoding.EncodeToString([]byte{1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16}),
	}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("partial rewrap: got %d", resp.StatusCode)
	}

	// Wrong current password must not update anything.
	newSaltWrong := bytes.Repeat([]byte{0x11}, 16)
	resp = doJSON(t, c, http.MethodPut, "/auth/account/password", map[string]any{
		"current_password": "senha-errada-123", "new_password": newPassword,
		"new_password_confirmation": newPassword,
		"new_salt":                  base64.StdEncoding.EncodeToString(newSaltWrong),
		"new_wrapped_dek":           base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x22}, 48)),
		"new_dek_nonce":             base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0x33}, 24)),
		"new_kdf_ops_limit":         3, "new_kdf_mem_limit": 67108864,
	}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("rewrap with wrong password: got %d", resp.StatusCode)
	}
	// Metadata unchanged.
	resp = doJSON(t, c, http.MethodGet, "/auth/crypto", nil, nil)
	body := readBody(t, resp)
	var meta map[string]any
	if err := json.Unmarshal(body, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["salt"] != base64.StdEncoding.EncodeToString(originalSalt) {
		t.Fatalf("metadata should be unchanged after failed re-wrap, got salt=%v", meta["salt"])
	}

	// Correct re-wrap: atomic password + metadata update.
	newSalt := bytes.Repeat([]byte{0x42}, 16)
	newWrapped := bytes.Repeat([]byte{0x51}, 48)
	newNonce := bytes.Repeat([]byte{0x61}, 24)
	resp = doJSON(t, c, http.MethodPut, "/auth/account/password", map[string]any{
		"current_password": password, "new_password": newPassword,
		"new_password_confirmation": newPassword,
		"new_salt":                  base64.StdEncoding.EncodeToString(newSalt),
		"new_wrapped_dek":           base64.StdEncoding.EncodeToString(newWrapped),
		"new_dek_nonce":             base64.StdEncoding.EncodeToString(newNonce),
		"new_kdf_ops_limit":         4, "new_kdf_mem_limit": 33554432,
	}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("change password with rewrap: got %d", resp.StatusCode)
	}

	// Metadata now reflects the new wrap.
	resp = doJSON(t, c, http.MethodGet, "/auth/crypto", nil, nil)
	body = readBody(t, resp)
	if err := json.Unmarshal(body, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["salt"] != base64.StdEncoding.EncodeToString(newSalt) ||
		meta["wrapped_dek"] != base64.StdEncoding.EncodeToString(newWrapped) ||
		meta["dek_nonce"] != base64.StdEncoding.EncodeToString(newNonce) {
		t.Fatalf("metadata not updated: %s", body)
	}
	if meta["kdf_ops_limit"] != float64(4) || meta["kdf_mem_limit"] != float64(33554432) {
		t.Fatalf("kdf params not updated: %s", body)
	}

	// Old password no longer logs in; new one does.
	c2 := newClient(t)
	resp = doJSON(t, c2, http.MethodPost, "/auth/login", map[string]any{
		"identifier": email, "password": password,
	}, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("old password should fail, got %d", resp.StatusCode)
	}
	resp = doJSON(t, c2, http.MethodPost, "/auth/login", map[string]any{
		"identifier": email, "password": newPassword,
	}, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("new password should work, got %d", resp.StatusCode)
	}
}

func TestDeleteAccount(t *testing.T) {
	c := newClient(t)
	email := unique("del") + "@example.com"
	username := unique("del")
	password := "senha-super-secreta-1"
	code, reg := register(t, c, email, username, password)
	if code != http.StatusCreated {
		t.Fatalf("register: got %d", code)
	}

	// Wrong password must not delete.
	resp := doJSON(t, c, http.MethodDelete, "/auth/account",
		map[string]string{"password": "senha-errada-123"}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("delete with wrong password: got %d", resp.StatusCode)
	}

	resp = doJSON(t, c, http.MethodDelete, "/auth/account",
		map[string]string{"password": password}, csrfHeaders(reg.CSRFToken))
	readBody(t, resp)
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete account: got %d", resp.StatusCode)
	}

	// Login with the deleted account must fail.
	c2 := newClient(t)
	resp = doJSON(t, c2, http.MethodPost, "/auth/login", map[string]any{
		"identifier": email, "password": password,
	}, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("login after deletion: got %d", resp.StatusCode)
	}
}
