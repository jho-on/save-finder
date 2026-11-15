package keys_test

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

func TestMain(m *testing.M) {
	dsn := os.Getenv("TEST_DATABASE_URL")
	if dsn == "" {
		if os.Getenv("CI") == "true" {
			fmt.Fprintln(os.Stderr, "TEST_DATABASE_URL not set in CI; refusing to skip integration tests")
			os.Exit(1)
		}
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

	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	poolForServer := pool
	testServer = httptest.NewServer(httpserver.New(httpserver.Deps{
		Handler: auth.NewHandler(
			users.NewStore(pool), sessions.NewStore(pool), false,
			ratelimit.NewRiskLimiter(),
			ratelimit.NewIdentityLimiter(1000), ratelimit.NewIdentityLimiter(1000),
			logger, keys.NewStore(pool)),
		Crypto: keys.NewHandler(keys.NewStore(pool), users.NewStore(pool), logger),
		Logger: logger,
		Ready:  poolForServer.Ping,
	}))
	code := m.Run()
	testServer.Close()
	pool.Close()
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

func registerAndLogin(t *testing.T, c *http.Client) string {
	t.Helper()
	email := unique("e2ee") + "@example.com"
	username := unique("e2ee")
	resp := doJSON(t, c, http.MethodPost, "/auth/register", map[string]any{
		"email": email, "username": username,
		"password": "senha-super-secreta-1", "password_confirmation": "senha-super-secreta-1",
	}, nil)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("register: got %d body=%s", resp.StatusCode, body)
	}
	var out struct {
		CSRFToken string `json:"csrf_token"`
	}
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatal(err)
	}
	return out.CSRFToken
}

func validPayload() map[string]any {
	return map[string]any{
		"salt":             base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xAB}, 16)),
		"wrapped_dek":      base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
		"dek_nonce":        base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 24)),
		"kdf_ops_limit":    2,
		"kdf_mem_limit":    67108864, // 64 MiB, libsodium MEMLIMIT_INTERACTIVE
		"current_password": "senha-super-secreta-1",
	}
}

func TestKeySetupAndGet(t *testing.T) {
	c := newClient(t)
	csrf := registerAndLogin(t, c)

	// No metadata yet.
	resp := doJSON(t, c, http.MethodGet, "/auth/crypto", nil, nil)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("get before setup: got %d body=%s", resp.StatusCode, body)
	}
	if !bytes.Contains(body, []byte("key_metadata_not_found")) {
		t.Fatalf("expected key_metadata_not_found, got %s", body)
	}

	// Setup without CSRF must be rejected.
	resp = doJSON(t, c, http.MethodPost, "/auth/crypto", validPayload(), nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("setup without csrf: got %d", resp.StatusCode)
	}

	// Valid setup.
	resp = doJSON(t, c, http.MethodPost, "/auth/crypto", validPayload(), map[string]string{"X-CSRF-Token": csrf})
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("setup: got %d body=%s", resp.StatusCode, body)
	}

	// Re-setup must be rejected.
	resp = doJSON(t, c, http.MethodPost, "/auth/crypto", validPayload(), map[string]string{"X-CSRF-Token": csrf})
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("re-setup: got %d body=%s", resp.StatusCode, body)
	}

	// Now GET returns the stored metadata.
	resp = doJSON(t, c, http.MethodGet, "/auth/crypto", nil, nil)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("get after setup: got %d body=%s", resp.StatusCode, body)
	}
	var got struct {
		Salt        string `json:"salt"`
		WrappedDEK  string `json:"wrapped_dek"`
		DEKNonce    string `json:"dek_nonce"`
		KDFOpsLimit int64  `json:"kdf_ops_limit"`
		KDFMemLimit int64  `json:"kdf_mem_limit"`
	}
	if err := json.Unmarshal(body, &got); err != nil {
		t.Fatal(err)
	}
	payload := validPayload()
	if got.Salt != payload["salt"] || got.WrappedDEK != payload["wrapped_dek"] || got.DEKNonce != payload["dek_nonce"] {
		t.Fatalf("base64 fields mismatch: got %+v", got)
	}
	if got.KDFOpsLimit != int64(payload["kdf_ops_limit"].(int)) || got.KDFMemLimit != int64(payload["kdf_mem_limit"].(int)) {
		t.Fatalf("kdf params mismatch: got %+v", got)
	}
}

func TestKeySetupValidationAndAuth(t *testing.T) {
	c := newClient(t)
	csrf := registerAndLogin(t, c)

	cases := map[string]map[string]any{
		"short salt": {
			"salt":          base64.StdEncoding.EncodeToString([]byte{1, 2, 3}),
			"wrapped_dek":   base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
			"dek_nonce":     base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 24)),
			"kdf_ops_limit": 2, "kdf_mem_limit": 67108864, "current_password": "senha-super-secreta-1",
		},
		"short nonce": {
			"salt":          base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xAB}, 16)),
			"wrapped_dek":   base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
			"dek_nonce":     base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 12)),
			"kdf_ops_limit": 2, "kdf_mem_limit": 67108864, "current_password": "senha-super-secreta-1",
		},
		"huge mem limit": {
			"salt":          base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xAB}, 16)),
			"wrapped_dek":   base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
			"dek_nonce":     base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 24)),
			"kdf_ops_limit": 2, "kdf_mem_limit": 1 << 40, "current_password": "senha-super-secreta-1",
		},
		"invalid base64": {
			"salt":          "!!!not-base64!!!",
			"wrapped_dek":   base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xCD}, 48)),
			"dek_nonce":     base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{0xEF}, 24)),
			"kdf_ops_limit": 2, "kdf_mem_limit": 67108864, "current_password": "senha-super-secreta-1",
		},
	}
	for name, payload := range cases {
		t.Run(name, func(t *testing.T) {
			resp := doJSON(t, c, http.MethodPost, "/auth/crypto", payload, map[string]string{"X-CSRF-Token": csrf})
			body := readBody(t, resp)
			if resp.StatusCode != http.StatusBadRequest {
				t.Fatalf("got %d body=%s", resp.StatusCode, body)
			}
			if !bytes.Contains(body, []byte("validation_failed")) {
				t.Fatalf("expected validation_failed, got %s", body)
			}
		})
	}

	// Unauthenticated access is rejected.
	fresh := newClient(t)
	resp := doJSON(t, fresh, http.MethodGet, "/auth/crypto", nil, nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated get: got %d", resp.StatusCode)
	}
	resp = doJSON(t, fresh, http.MethodPost, "/auth/crypto", validPayload(), nil)
	readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unauthenticated post: got %d", resp.StatusCode)
	}
}

func TestKeySetupWrongPassword(t *testing.T) {
	c := newClient(t)
	csrf := registerAndLogin(t, c)
	payload := validPayload()
	payload["current_password"] = "senha-errada-999"
	resp := doJSON(t, c, http.MethodPost, "/auth/crypto", payload, map[string]string{"X-CSRF-Token": csrf})
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("setup with wrong password: got %d body=%s", resp.StatusCode, body)
	}
	if !bytes.Contains(body, []byte("invalid_credentials")) {
		t.Fatalf("expected invalid_credentials, got %s", body)
	}
}
