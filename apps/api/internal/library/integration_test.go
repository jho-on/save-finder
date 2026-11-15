package library_test

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
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
	"save-finder/api/internal/library"
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
	handler := auth.NewHandler(
		users.NewStore(pool), sessions.NewStore(pool), false,
		ratelimit.NewRiskLimiter(),
		ratelimit.NewIdentityLimiter(1000),
		ratelimit.NewIdentityLimiter(1000),
		logger, keys.NewStore(pool), ratelimit.NewIdentityLimiter(1000))
	testServer = httptest.NewServer(httpserver.New(httpserver.Deps{
		Handler: handler,
		Crypto:  keys.NewHandler(keys.NewStore(pool), users.NewStore(pool), logger),
		Library: library.NewHandler(library.NewStore(pool), users.NewStore(pool), logger),
		Logger:  logger,
		Ready:   pool.Ping,
	}))
	code := m.Run()
	testServer.Close()
	pool.Close()
	os.Exit(code)
}

type authBody struct {
	CSRFToken string `json:"csrf_token"`
}

func newClient(t *testing.T) *http.Client {
	t.Helper()
	jar, err := cookiejar.New(nil)
	if err != nil {
		t.Fatal(err)
	}
	return &http.Client{Jar: jar, Timeout: 10 * time.Second}
}

func unique(prefix string) string { return fmt.Sprintf("%s-%s", prefix, uuid.NewString()[:8]) }

func register(t *testing.T, c *http.Client, email, username, pw string) authBody {
	t.Helper()
	resp := doJSON(t, c, http.MethodPost, "/auth/register", map[string]any{
		"email": email, "username": username, "password": pw, "password_confirmation": pw,
	}, nil)
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusCreated {
		body, _ := io.ReadAll(resp.Body)
		t.Fatalf("register: got %d body=%s", resp.StatusCode, body)
	}
	var out authBody
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
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

func csrfHeaders(token string) map[string]string { return map[string]string{"X-CSRF-Token": token} }

func readBody(t *testing.T, resp *http.Response) []byte {
	t.Helper()
	defer func() { _ = resp.Body.Close() }()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

func blob(t *testing.T, n int) []byte {
	t.Helper()
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		t.Fatal(err)
	}
	return b
}

func b64(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

func TestBatchListDetailAndWipe(t *testing.T) {
	c := newClient(t)
	email := unique("lib") + "@example.com"
	username := unique("lib")
	password := "senha-super-secreta-1"
	reg := register(t, c, email, username, password)
	headers := csrfHeaders(reg.CSRFToken)

	thumb := blob(t, 96)
	transcript := blob(t, 64)
	items := []map[string]any{
		{"item_key": "instagram:POST_1", "payload": b64(blob(t, 48)), "transcript": b64(transcript), "thumb": b64(thumb)},
		{"item_key": "instagram:POST_2", "payload": b64(blob(t, 48))},
		{"item_key": "manual:OTHER-2.x", "payload": b64(blob(t, 48))},
	}
	// Batch upsert without CSRF must be rejected.
	resp := doJSON(t, c, http.MethodPost, "/library/items/batch", map[string]any{"items": items}, nil)
	if resp.StatusCode != http.StatusForbidden {
		readBody(t, resp)
		t.Fatalf("batch without csrf: got %d", resp.StatusCode)
	}
	readBody(t, resp)

	resp = doJSON(t, c, http.MethodPost, "/library/items/batch", map[string]any{"items": items}, headers)
	body := readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("batch: got %d body=%s", resp.StatusCode, body)
	}
	var upsert struct {
		Upserted int `json:"upserted"`
	}
	if err := json.Unmarshal(body, &upsert); err != nil {
		t.Fatal(err)
	}
	if upsert.Upserted != 3 {
		t.Fatalf("upserted: got %d", upsert.Upserted)
	}

	// List page 1 (limit=2) then page 2.
	resp = doJSON(t, c, http.MethodGet, "/library/items?limit=2", nil, nil)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list: got %d body=%s", resp.StatusCode, body)
	}
	var page1 struct {
		Items []struct {
			ItemKey string `json:"item_key"`
			Payload string `json:"payload"`
		} `json:"items"`
		NextCursor string `json:"next_cursor"`
	}
	if err := json.Unmarshal(body, &page1); err != nil {
		t.Fatal(err)
	}
	if len(page1.Items) != 2 || page1.NextCursor == "" {
		t.Fatalf("page1: %+v", page1)
	}
	resp = doJSON(t, c, http.MethodGet, "/library/items?limit=2&cursor="+page1.NextCursor, nil, nil)
	body = readBody(t, resp)
	var page2 struct {
		Items []struct {
			ItemKey string `json:"item_key"`
		} `json:"items"`
		NextCursor string `json:"next_cursor"`
	}
	if err := json.Unmarshal(body, &page2); err != nil {
		t.Fatal(err)
	}
	if len(page2.Items) != 1 || page2.NextCursor != "" {
		t.Fatalf("page2: %+v", page2)
	}

	// Detail returns the transcript blob for item 1, null for item 2.
	resp = doJSON(t, c, http.MethodGet, "/library/items/instagram:POST_1", nil, nil)
	body = readBody(t, resp)
	var detail struct {
		Transcript *string `json:"transcript"`
	}
	if err := json.Unmarshal(body, &detail); err != nil {
		t.Fatal(err)
	}
	if detail.Transcript == nil || *detail.Transcript != b64(transcript) {
		t.Fatalf("detail transcript mismatch: %+v", detail)
	}
	resp = doJSON(t, c, http.MethodGet, "/library/items/instagram:POST_2", nil, nil)
	body = readBody(t, resp)
	var detail2 struct {
		Transcript *string `json:"transcript"`
	}
	if err := json.Unmarshal(body, &detail2); err != nil {
		t.Fatal(err)
	}
	if detail2.Transcript != nil {
		t.Fatalf("expected null transcript for POST_2")
	}

	// Thumbnail endpoint returns the raw ciphertext bytes.
	req, err := http.NewRequest(http.MethodGet, testServer.URL+"/library/items/instagram:POST_1/thumb", nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err = c.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	thumbBytes := readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("thumb: got %d", resp.StatusCode)
	}
	if !bytes.Equal(thumbBytes, thumb) {
		t.Fatalf("thumb mismatch: got %d bytes", len(thumbBytes))
	}
	resp = doJSON(t, c, http.MethodGet, "/library/items/instagram:POST_2/thumb", nil, nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("thumb missing: got %d", resp.StatusCode)
	}
	readBody(t, resp)

	// Manifest lists all three keys.
	resp = doJSON(t, c, http.MethodGet, "/library/manifest", nil, nil)
	body = readBody(t, resp)
	var manifest struct {
		Items []struct {
			ItemKey string `json:"item_key"`
		} `json:"items"`
	}
	if err := json.Unmarshal(body, &manifest); err != nil {
		t.Fatal(err)
	}
	if len(manifest.Items) != 3 {
		t.Fatalf("manifest: got %d entries", len(manifest.Items))
	}

	// Re-push (idempotent upsert) keeps the count at 3.
	resp = doJSON(t, c, http.MethodPost, "/library/items/batch",
		map[string]any{"items": items[:1]}, headers)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("re-push: got %d body=%s", resp.StatusCode, body)
	}

	// Wrong password cannot wipe.
	resp = doJSON(t, c, http.MethodDelete, "/library",
		map[string]string{"password": "wrong-password-1"}, headers)
	if resp.StatusCode != http.StatusUnauthorized {
		readBody(t, resp)
		t.Fatalf("wipe with wrong password: got %d", resp.StatusCode)
	}
	readBody(t, resp)

	// Wipe with the right password removes everything.
	resp = doJSON(t, c, http.MethodDelete, "/library",
		map[string]string{"password": password}, headers)
	body = readBody(t, resp)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("wipe: got %d body=%s", resp.StatusCode, body)
	}
	var wiped struct {
		Deleted int64 `json:"deleted"`
	}
	if err := json.Unmarshal(body, &wiped); err != nil {
		t.Fatal(err)
	}
	if wiped.Deleted != 3 {
		t.Fatalf("deleted: got %d", wiped.Deleted)
	}

	resp = doJSON(t, c, http.MethodGet, "/library/manifest", nil, nil)
	body = readBody(t, resp)
	var manifest2 struct {
		Items []struct {
			ItemKey string `json:"item_key"`
		} `json:"items"`
	}
	if err := json.Unmarshal(body, &manifest2); err != nil {
		t.Fatal(err)
	}
	if len(manifest2.Items) != 0 {
		t.Fatalf("manifest after wipe: %+v", manifest2)
	}
}

func TestBatchValidation(t *testing.T) {
	c := newClient(t)
	username := unique("val")
	pw := "senha-super-secreta-1"
	reg := register(t, c, unique("val")+"@example.com", username, pw)
	headers := csrfHeaders(reg.CSRFToken)

	cases := []struct {
		name string
		body map[string]any
	}{
		{"empty items", map[string]any{"items": []any{}}},
		{"bad key format", map[string]any{"items": []any{
			map[string]any{"item_key": "no colon here!!", "payload": b64(blob(t, 48))}}}},
		{"payload too small", map[string]any{"items": []any{
			map[string]any{"item_key": "instagram:OK_1", "payload": b64(blob(t, 10))}}}},
		{"payload not base64", map[string]any{"items": []any{
			map[string]any{"item_key": "instagram:OK_1", "payload": "!!!not-base64!!!"}}}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resp := doJSON(t, c, http.MethodPost, "/library/items/batch", tc.body, headers)
			defer func() { _ = resp.Body.Close() }()
			if resp.StatusCode != http.StatusBadRequest {
				body, _ := io.ReadAll(resp.Body)
				t.Fatalf("got %d, want 400; body=%s", resp.StatusCode, body)
			}
		})
	}
}
