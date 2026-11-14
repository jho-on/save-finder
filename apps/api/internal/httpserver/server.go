// Package httpserver wires the HTTP router, middleware and routes.
package httpserver

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"runtime/debug"
	"time"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"
	httpswagger "github.com/swaggo/http-swagger/v2"

	"save-finder/api/internal/auth"

	_ "save-finder/api/docs"
)

// Deps carries the dependencies required by the HTTP server.
type Deps struct {
	Logger       *slog.Logger
	Handler      *auth.Handler
	Ready        func(ctx context.Context) error
	TrustProxy   bool
	SwaggerRoute bool
}

// New builds the chi router with all middleware and routes.
func New(d Deps) http.Handler {
	r := chi.NewRouter()

	r.Use(chimw.RequestID)
	if d.TrustProxy {
		r.Use(chimw.RealIP)
	}
	r.Use(recoverer(d.Logger))
	r.Use(logging(d.Logger))

	r.Get("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	r.Get("/readyz", func(w http.ResponseWriter, req *http.Request) {
		ctx, cancel := context.WithTimeout(req.Context(), 2*time.Second)
		defer cancel()
		if err := d.Ready(ctx); err != nil {
			http.Error(w, `{"status":"unavailable"}`, http.StatusServiceUnavailable)
			return
		}
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})

	h := d.Handler
	r.Route("/auth", func(r chi.Router) {
		r.With(h.UnauthLimit).Post("/register", h.Register)
		r.With(h.UnauthLimit).Post("/login", h.Login)
		r.Post("/logout", h.Logout)

		r.Group(func(r chi.Router) {
			r.Use(h.RequireAuth)
			r.Use(h.CSRF)

			r.Post("/logout-all", h.LogoutAll)
			r.Get("/sessions", h.ListSessions)
			r.Delete("/sessions/{id}", h.RevokeSession)

			r.Get("/account", h.GetAccount)
			r.Patch("/account", h.UpdateAccount)
			r.With(h.SensitiveLimit).Delete("/account", h.DeleteAccount)
			r.With(h.SensitiveLimit).Put("/account/password", h.ChangePassword)
		})
	})

	if d.SwaggerRoute {
		r.Get("/swagger/*", httpswagger.WrapHandler)
	}

	return r
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func logging(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			start := time.Now()
			rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
			next.ServeHTTP(rec, r)
			logger.InfoContext(r.Context(), "http",
				slog.String("method", r.Method),
				slog.String("path", r.URL.Path),
				slog.Int("status", rec.status),
				slog.Duration("duration", time.Since(start)),
				slog.String("request_id", chimw.GetReqID(r.Context())))
		})
	}
}

func recoverer(logger *slog.Logger) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			defer func() {
				if rec := recover(); rec != nil {
					logger.ErrorContext(r.Context(), "panic recovered",
						slog.Any("panic", rec),
						slog.String("stack", string(debug.Stack())))
					http.Error(w, `{"error":{"code":"internal_error","message":"internal error"}}`,
						http.StatusInternalServerError)
				}
			}()
			next.ServeHTTP(w, r)
		})
	}
}

// JSON is a small helper for ad-hoc JSON responses.
func JSON(w http.ResponseWriter, status int, v any) error {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	return json.NewEncoder(w).Encode(v)
}
