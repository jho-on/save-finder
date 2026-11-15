// Command api is the entry point for the SaveFinder remote API.
//
// @title           SaveFinder API
// @version         1.0
// @description     API remota do SaveFinder: autenticação, persistência e sincronização.
// @BasePath        /
// @schemes         https
package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"save-finder/api/internal/auth"
	"save-finder/api/internal/config"
	"save-finder/api/internal/db"
	"save-finder/api/internal/httpserver"
	"save-finder/api/internal/keys"
	"save-finder/api/internal/library"
	"save-finder/api/internal/ratelimit"
	"save-finder/api/internal/sessions"
	"save-finder/api/internal/users"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "api: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	cfg, err := config.Load()
	if err != nil {
		return err
	}

	var handler slog.Handler
	opts := &slog.HandlerOptions{Level: parseLevel(cfg.LogLevel)}
	if strings.ToLower(cfg.LogFormat) == "text" {
		handler = slog.NewTextHandler(os.Stdout, opts)
	} else {
		handler = slog.NewJSONHandler(os.Stdout, opts)
	}
	logger := slog.New(handler)
	slog.SetDefault(logger)

	logger.Info("starting", slog.Int("port", cfg.Port))

	if err := db.Migrate(ctx, cfg.DatabaseURL); err != nil {
		return err
	}

	pool, err := db.Connect(ctx, cfg.DatabaseURL)
	if err != nil {
		return err
	}
	defer pool.Close()

	sessionStore := sessions.NewStore(pool)
	handlerDeps := auth.NewHandler(
		users.NewStore(pool), sessionStore, cfg.CookieSecure,
		ratelimit.NewRiskLimiter(),
		ratelimit.NewIdentityLimiter(120), // 120 req/min per identity
		ratelimit.NewIdentityLimiter(10),  // 10 req/min for sensitive ops
		logger,
		keys.NewStore(pool),
		ratelimit.NewIdentityLimiter(600), // 600 req/min for library reads (lazy-loaded media)
	)
	cryptoDeps := keys.NewHandler(keys.NewStore(pool), users.NewStore(pool), logger)
	libraryDeps := library.NewHandler(library.NewStore(pool), users.NewStore(pool), logger)

	// Periodically purge expired sessions.
	go func() {
		ticker := time.NewTicker(time.Hour)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				mctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
				n, err := sessionStore.DeleteExpired(mctx)
				cancel()
				if err != nil {
					logger.Warn("purge expired sessions", slog.String("error", err.Error()))
				} else if n > 0 {
					logger.Info("purged expired sessions", slog.Int64("count", n))
				}
			}
		}
	}()

	srv := &http.Server{
		Addr: fmt.Sprintf(":%d", cfg.Port),
		Handler: httpserver.New(httpserver.Deps{
			Logger:       logger,
			Handler:      handlerDeps,
			Crypto:       cryptoDeps,
			Library:      libraryDeps,
			TrustProxy:   cfg.TrustProxy,
			SwaggerRoute: true,
			Ready: func(ctx context.Context) error {
				return pool.Ping(ctx)
			},
		}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		logger.Info("listening", slog.String("addr", srv.Addr))
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
	}()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		logger.Info("shutting down")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		return srv.Shutdown(shutdownCtx)
	}
}

func parseLevel(s string) slog.Level {
	switch strings.ToLower(s) {
	case "debug":
		return slog.LevelDebug
	case "warn":
		return slog.LevelWarn
	case "error":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
