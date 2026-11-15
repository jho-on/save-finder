package auth

import (
	"net/http"
	"time"

	"save-finder/api/internal/httpx"
	"save-finder/api/internal/principal"
	"save-finder/api/internal/ratelimit"
	"save-finder/api/internal/sessions"
)

// clientIP returns the IP to use for risk accounting. When the service is
// behind a trusted proxy, enable TRUST_PROXY so chi's RealIP middleware
// fixes RemoteAddr upstream.
func clientIP(r *http.Request) string {
	return ratelimit.IP(r.RemoteAddr)
}

// UnauthLimit applies the IP risk-score strategy to unauthenticated routes.
func (h *Handler) UnauthLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip := clientIP(r)
		if ok, retryAfter := h.IPRisk.Allow(ip); !ok {
			w.Header().Set("Retry-After", retryAfter.Round(time.Second).String())
			httpx.WriteError(w, http.StatusTooManyRequests, httpx.CodeRateLimited,
				"too many requests, try again later")
			return
		}
		next.ServeHTTP(w, r)
	})
}

type handlerLimiter = *ratelimit.IdentityLimiter

// RequireAuth authenticates the session cookie, applies sliding expiration
// and per-identity rate limiting, and stores the principal in the context.
func (h *Handler) RequireAuth(next http.Handler) http.Handler {
	return h.requireAuth(h.IdentityLim)(next)
}

// RequireAuthRead behaves like RequireAuth but applies the dedicated,
// more generous read limiter. Used by the library read routes, which power
// media-heavy client views (e.g. lazy-loaded thumbnails).
func (h *Handler) RequireAuthRead(next http.Handler) http.Handler {
	lim := h.ReadLim
	if lim == nil {
		lim = h.IdentityLim
	}
	return h.requireAuth(lim)(next)
}

func (h *Handler) requireAuth(lim handlerLimiter) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			cookie, err := r.Cookie(sessionCookieName)
			if err != nil {
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
				return
			}
			sess, err := h.Sessions.Lookup(r.Context(), cookie.Value)
			if err != nil {
				clearSessionCookie(w, h.CookieSecure)
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeSessionExpired, "session expired or invalid")
				return
			}
			user, err := h.Users.GetByID(r.Context(), sess.UserID)
			if err != nil {
				clearSessionCookie(w, h.CookieSecure)
				httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "account no longer exists")
				return
			}
			if err := h.Sessions.Touch(r.Context(), sess.ID, sess.RememberMe); err != nil {
				h.Logger.WarnContext(r.Context(), "touch session", "error", err)
			}
			if !lim.Allow(user.ID.String()) {
				httpx.WriteError(w, http.StatusTooManyRequests, httpx.CodeRateLimited,
					"too many requests, slow down")
				return
			}
			next.ServeHTTP(w, r.WithContext(principal.With(r.Context(), principal.Principal{User: user, Session: sess})))
		})
	}
}

// CSRF enforces the synchronizer-token pattern on mutating authenticated
// requests: the X-CSRF-Token header must match the session token.
func (h *Handler) CSRF(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet, http.MethodHead, http.MethodOptions:
			next.ServeHTTP(w, r)
			return
		}
		p, ok := principal.From(r.Context())
		if !ok {
			httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
			return
		}
		if !sessions.ConstantTimeCSRF(p.Session.CSRFToken, r.Header.Get("X-CSRF-Token")) {
			httpx.WriteError(w, http.StatusForbidden, httpx.CodeCSRFInvalid, "invalid CSRF token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// SensitiveLimit applies a stricter per-identity limiter for sensitive
// operations (password change, account deletion).
func (h *Handler) SensitiveLimit(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		p, ok := principal.From(r.Context())
		if ok && !h.SensitiveLim.Allow(p.User.ID.String()) {
			httpx.WriteError(w, http.StatusTooManyRequests, httpx.CodeRateLimited,
				"too many attempts, try again later")
			return
		}
		next.ServeHTTP(w, r)
	})
}
