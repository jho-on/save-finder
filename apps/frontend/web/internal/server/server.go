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
	// /library/* is the same-origin proxy for the encrypted-library
	// endpoints; the browser talks to them as if they were local.
	r.Handle("/library/*", newProxy(d.Logger, upstream))
	r.Handle("/library", newProxy(d.Logger, upstream))

	r.Get("/login", func(w http.ResponseWriter, r *http.Request) {
		// The library lives in a single shell page; /login is kept as an
		// alias that lands users there (unauthenticated visitors get the
		// login view).
		http.Redirect(w, r, "/", http.StatusSeeOther)
	})
	r.Get("/register", render(func(ctx context.Context, w http.ResponseWriter) {
		_ = templates.Register().Render(ctx, w)
	}))
	r.Get("/change-password", render(func(ctx context.Context, w http.ResponseWriter) {
		_ = templates.ChangePassword().Render(ctx, w)
	}))
	r.Get("/", render(func(ctx context.Context, w http.ResponseWriter) {
		// The shell page contains no user data; it boots client-side and
		// shows the login form, an unlock prompt or the library.
		_ = templates.AppShell().Render(ctx, w)
	}))

	return r, nil
}

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
		h.Set("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data: blob:")
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
