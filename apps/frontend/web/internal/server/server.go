// Package server wires the HTTP router for the remote web interface.
package server

import (
	"context"
	"encoding/json"
	"io/fs"
	"log/slog"
	"net/http"
	"net/http/httputil"
	"net/url"
	"time"

	"github.com/go-chi/chi/v5"

	webassets "save-finder/web"
	"save-finder/web/internal/assets"
	"save-finder/web/templates"
)

// Deps carries the dependencies required by the HTTP server.
type Deps struct {
	Logger      *slog.Logger
	APIUpstream string // e.g. "http://localhost:8080"
	// Static serves /static/; production wires the embedded FS from
	// save-finder/web, tests may pass a map FS.
	Static http.Handler
}

// New builds the chi router with pages, static assets and the /auth
// reverse proxy to the remote API.
func New(d Deps) (http.Handler, error) {
	upstream, err := url.Parse(d.APIUpstream)
	if err != nil {
		return nil, err
	}

	r := chi.NewRouter()
	r.Use(securityHeaders)

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
	})

	staticHandler := d.Static
	if staticHandler == nil {
		sub, err := fs.Sub(webassets.StaticFS, "static")
		if err != nil {
			return nil, err
		}
		staticHandler = assets.Handler(sub)
	}
	r.Get("/static/*", http.HandlerFunc(staticHandler.ServeHTTP))
	r.Handle("/auth/*", newProxy(d.Logger, upstream))

	r.Get("/login", render(func(ctx context.Context, w http.ResponseWriter) {
		_ = templates.Login().Render(ctx, w)
	}))
	r.Get("/register", render(func(ctx context.Context, w http.ResponseWriter) {
		_ = templates.Register().Render(ctx, w)
	}))
	r.Get("/change-password", render(func(ctx context.Context, w http.ResponseWriter) {
		_ = templates.ChangePassword().Render(ctx, w)
	}))
	r.Get("/", d.home(upstream))

	return r, nil
}

// home renders the authenticated home page. It validates the session
// cookie against the API (same cookie the SPA flow uses) and redirects
// to /login when unauthenticated.
func (d Deps) home(upstream *url.URL) http.HandlerFunc {
	client := &http.Client{Timeout: upstreamTimeout}
	return func(w http.ResponseWriter, r *http.Request) {
		account, err := d.lookupAccount(r.Context(), client, upstream, r.Header.Get("Cookie"))
		if err != nil {
			http.Redirect(w, r, "/login", http.StatusSeeOther)
			return
		}
		_ = templates.Home(account.Email, account.Username).Render(r.Context(), w)
	}
}

const upstreamTimeout = 5 * time.Second

type account struct {
	Email    string `json:"email"`
	Username string `json:"username"`
}

func (d Deps) lookupAccount(ctx context.Context, client *http.Client, upstream *url.URL, cookieHeader string) (account, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, upstream.String()+"/auth/account", nil)
	if err != nil {
		return account{}, err
	}
	req.Header.Set("Cookie", cookieHeader)
	res, err := client.Do(req)
	if err != nil {
		return account{}, err
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return account{}, errUpstream
	}
	var body struct {
		User account `json:"user"`
	}
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil {
		return account{}, err
	}
	return body.User, nil
}

var errUpstream = errString("upstream rejected session")

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func render(fn func(ctx context.Context, w http.ResponseWriter)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		fn(r.Context(), w)
	}
}

func securityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:")
		next.ServeHTTP(w, r)
	})
}

// newProxy reverse-proxies /auth/* to the remote API, preserving cookies.
func newProxy(logger *slog.Logger, upstream *url.URL) http.Handler {
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		logger.Error("proxy error", slog.String("path", r.URL.Path), slog.String("error", err.Error()))
		writeJSON(w, http.StatusBadGateway, map[string]any{
			"error": map[string]string{"code": "internal_error", "message": "upstream unavailable"},
		})
	}
	return proxy
}

type errString string

func (e errString) Error() string { return string(e) }
