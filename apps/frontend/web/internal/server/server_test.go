package server

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"testing/fstest"

	"save-finder/web/internal/assets"
)

const cookie = "save_finder_session=test-token-abc123"

type stubUpstream struct {
	target string
}

func newStubUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc("/auth/account", func(w http.ResponseWriter, r *http.Request) {
		if !strings.Contains(r.Header.Get("Cookie"), cookie) {
			writeJSONStub(w, http.StatusUnauthorized, map[string]any{"error": map[string]string{"code": "session_expired", "message": "session expired"}})
			return
		}
		writeJSONStub(w, http.StatusOK, map[string]any{
			"user":       map[string]string{"id": "1", "email": "ana@example.com", "username": "ana", "created_at": "2026-11-14T10:00:00Z"},
			"csrf_token": "csrf-token-123",
		})
	})
	mux.HandleFunc("/auth/login", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			writeJSONStub(w, http.StatusMethodNotAllowed, nil)
			return
		}
		body, _ := io.ReadAll(r.Body)
		writeJSONStub(w, http.StatusOK, map[string]any{
			"user":       nil,
			"csrf_token": "csrf-token-from-login",
			"echo":       json.RawMessage(body),
		})
	})
	mux.HandleFunc("/auth/logout", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func writeJSONStub(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if v != nil {
		_ = json.NewEncoder(w).Encode(v)
	}
}

func newTestHandler(t *testing.T, upstream string) http.Handler {
	t.Helper()
	staticFS := fstest.MapFS{
		"style.css": {Data: []byte("body { color: black }")},
		"app.js":    {Data: []byte("console.log('ok')")},
	}
	staticHandler := assets.Handler(fstest.MapFS(staticFS))
	h, err := New(Deps{
		Logger:      slog.New(slog.NewTextHandler(io.Discard, nil)),
		APIUpstream: upstream,
		Static:      staticHandler,
	})
	if err != nil {
		t.Fatalf("build router: %v", err)
	}
	return h
}

func TestPagesRender(t *testing.T) {
	upstream := newStubUpstream(t)
	h := newTestHandler(t, upstream.URL)
	for _, path := range []string{"/login", "/register", "/change-password"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		rec := httptest.NewRecorder()
		h.ServeHTTP(rec, req)
		if rec.Code != http.StatusOK {
			t.Fatalf("GET %s: got %d", path, rec.Code)
		}
		if !strings.Contains(rec.Body.String(), "SaveFinder") {
			t.Fatalf("GET %s: body does not contain SaveFinder", path)
		}
		if got := rec.Header().Get("X-Content-Type-Options"); got != "nosniff" {
			t.Fatalf("GET %s: missing nosniff header", path)
		}
	}
}

func TestHomeRedirectsWithoutSession(t *testing.T) {
	upstream := newStubUpstream(t)
	h := newTestHandler(t, upstream.URL)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusSeeOther {
		t.Fatalf("expected redirect, got %d", rec.Code)
	}
	if loc := rec.Header().Get("Location"); loc != "/login" {
		t.Fatalf("expected redirect to /login, got %q", loc)
	}
}

func TestHomeRendersAccountWithSession(t *testing.T) {
	upstream := newStubUpstream(t)
	h := newTestHandler(t, upstream.URL)
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Header.Set("Cookie", cookie)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "ana@example.com") || !strings.Contains(body, "ana") {
		t.Fatalf("expected account details in home page, got: %s", body)
	}
}

func TestAuthProxyForwardsRequests(t *testing.T) {
	upstream := newStubUpstream(t)
	h := newTestHandler(t, upstream.URL)

	// Login request is forwarded to the API and the response is proxied back.
	body := strings.NewReader(`{"identifier":"ana@example.com","password":"senha-super-secreta-1"}`)
	req := httptest.NewRequest(http.MethodPost, "/auth/login", body)
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("proxy login: got %d body=%s", rec.Code, rec.Body.String())
	}
	var res map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &res); err != nil {
		t.Fatal(err)
	}
	if res["csrf_token"] != "csrf-token-from-login" {
		t.Fatalf("unexpected proxied body: %v", res)
	}

	// Logout forwarded as-is.
	req = httptest.NewRequest(http.MethodPost, "/auth/logout", nil)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusNoContent {
		t.Fatalf("proxy logout: got %d", rec.Code)
	}
}

func TestStaticHandler(t *testing.T) {
	upstream := newStubUpstream(t)
	h := newTestHandler(t, upstream.URL)
	req := httptest.NewRequest(http.MethodGet, "/static/style.css", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("static style.css: got %d", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); !strings.Contains(ct, "text/css") {
		t.Fatalf("static content-type: %q", ct)
	}

	req = httptest.NewRequest(http.MethodGet, "/static/app.js", nil)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("static app.js: got %d", rec.Code)
	}
}
