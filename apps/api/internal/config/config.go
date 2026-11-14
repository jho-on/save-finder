// Package config loads environment-based configuration for the API.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
)

// Config holds the runtime configuration of the service.
type Config struct {
	// Port the HTTP server listens on (PORT, default 8080).
	Port int
	// DatabaseURL is the Postgres DSN (DATABASE_URL, required).
	DatabaseURL string
	// LogLevel is the minimum slog level (LOG_LEVEL, default "info").
	LogLevel string
	// LogFormat is "json" or "text" (LOG_FORMAT, default "json").
	LogFormat string
	// CookieSecure controls the Secure attribute on session cookies
	// (COOKIE_SECURE, default true).
	CookieSecure bool
	// TrustProxy enables chi's RealIP middleware, trusting X-Forwarded-For
	// and X-Real-IP. Only enable behind a trusted reverse proxy
	// (TRUST_PROXY, default false).
	TrustProxy bool
}

// Load reads configuration from the environment and validates it.
func Load() (Config, error) {
	cfg := Config{
		Port:         8080,
		DatabaseURL:  os.Getenv("DATABASE_URL"),
		LogLevel:     envOr("LOG_LEVEL", "info"),
		LogFormat:    envOr("LOG_FORMAT", "json"),
		CookieSecure: envBool("COOKIE_SECURE", true),
		TrustProxy:   envBool("TRUST_PROXY", false),
	}

	if v := os.Getenv("PORT"); v != "" {
		port, err := strconv.Atoi(v)
		if err != nil || port < 1 || port > 65535 {
			return Config{}, fmt.Errorf("invalid PORT %q", v)
		}
		cfg.Port = port
	}

	if cfg.DatabaseURL == "" {
		return Config{}, fmt.Errorf("DATABASE_URL is required")
	}
	cfg.LogLevel = strings.ToLower(cfg.LogLevel)
	cfg.LogFormat = strings.ToLower(cfg.LogFormat)
	switch cfg.LogFormat {
	case "json", "text":
	default:
		return Config{}, fmt.Errorf("invalid LOG_FORMAT %q", cfg.LogFormat)
	}
	return cfg, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envBool(key string, fallback bool) bool {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	b, err := strconv.ParseBool(v)
	if err != nil {
		return fallback
	}
	return b
}
