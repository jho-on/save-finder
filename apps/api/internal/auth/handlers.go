// Package auth implements the HTTP handlers, service logic and middleware
// for registration, login, sessions and account management.
package auth

import (
	"log/slog"
	"net/http"
	"time"

	"save-finder/api/internal/ratelimit"
	"save-finder/api/internal/sessions"
	"save-finder/api/internal/users"
)

const sessionCookieName = "save_finder_session"

// Handler holds the dependencies of the auth HTTP handlers.
type Handler struct {
	Users        *users.Store
	Sessions     *sessions.Store
	CookieSecure bool
	IPRisk       *ratelimit.RiskLimiter
	IdentityLim  *ratelimit.IdentityLimiter
	SensitiveLim *ratelimit.IdentityLimiter
	Logger       *slog.Logger

	dummyHash string
}

// NewHandler creates a Handler and precomputes the dummy hash used to
// equalize timing between unknown users and wrong passwords.
func NewHandler(u *users.Store, s *sessions.Store, cookieSecure bool,
	ipRisk *ratelimit.RiskLimiter, identityLim, sensitiveLim *ratelimit.IdentityLimiter,
	logger *slog.Logger) *Handler {
	h := &Handler{Users: u, Sessions: s, CookieSecure: cookieSecure,
		IPRisk: ipRisk, IdentityLim: identityLim, SensitiveLim: sensitiveLim, Logger: logger}
	// Precompute to avoid leaking timing differences on unknown identifiers.
	h.dummyHash, _ = DummyHash()
	return h
}

// --- request/response bodies (exported for swaggo) ---

// RegisterRequest is the body of POST /auth/register.
type RegisterRequest struct {
	Email                string `json:"email" example:"ana@example.com"`
	Username             string `json:"username" example:"ana"`
	Password             string `json:"password" example:"s3cret-s3cret"`
	PasswordConfirmation string `json:"password_confirmation" example:"s3cret-s3cret"`
}

// LoginRequest is the body of POST /auth/login.
type LoginRequest struct {
	Identifier string `json:"identifier" example:"ana@example.com"`
	Password   string `json:"password" example:"s3cret-s3cret"`
	RememberMe bool   `json:"remember_me"`
}

// UpdateAccountRequest is the body of PATCH /auth/account.
// At least one field must be present.
type UpdateAccountRequest struct {
	Email    *string `json:"email,omitempty" example:"ana@example.com"`
	Username *string `json:"username,omitempty" example:"ana"`
}

// ChangePasswordRequest is the body of PUT /auth/account/password.
type ChangePasswordRequest struct {
	CurrentPassword         string `json:"current_password" example:"s3cret-s3cret"`
	NewPassword             string `json:"new_password" example:"new-s3cret-s3cret"`
	NewPasswordConfirmation string `json:"new_password_confirmation" example:"new-s3cret-s3cret"`
}

// DeleteAccountRequest is the body of DELETE /auth/account.
type DeleteAccountRequest struct {
	Password string `json:"password" example:"s3cret-s3cret"`
}

// UserResponse is the public representation of a user.
type UserResponse struct {
	ID        string `json:"id" example:"3f6b0f4a-6f2d-4d4a-9c6c-1d8c0f0d2f7a"`
	Email     string `json:"email" example:"ana@example.com"`
	Username  string `json:"username" example:"ana"`
	CreatedAt string `json:"created_at" example:"2026-11-14T10:00:00Z"`
}

// AuthResponse is returned by register and login.
type AuthResponse struct {
	User      UserResponse `json:"user"`
	CSRFToken string       `json:"csrf_token" example:"p0K..."`
}

// SessionResponse is one item in the session list.
type SessionResponse struct {
	ID                string `json:"id"`
	UserAgent         string `json:"user_agent"`
	RememberMe        bool   `json:"remember_me"`
	CreatedAt         string `json:"created_at"`
	LastSeenAt        string `json:"last_seen_at"`
	AbsoluteExpiresAt string `json:"absolute_expires_at"`
	Current           bool   `json:"current"`
}

// SessionListResponse is returned by GET /auth/sessions.
type SessionListResponse struct {
	Sessions []SessionResponse `json:"sessions"`
}

// AccountResponse is returned by GET /auth/account.
type AccountResponse struct {
	User      UserResponse `json:"user"`
	CSRFToken string       `json:"csrf_token" example:"p0K..."`
}

func userResponse(u users.User) UserResponse {
	return UserResponse{
		ID:        u.ID.String(),
		Email:     u.Email,
		Username:  u.Username,
		CreatedAt: u.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
	}
}

func setSessionCookie(w http.ResponseWriter, token string, secure bool, absoluteExpires time.Time) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookieName,
		Value:    token,
		Path:     "/",
		HttpOnly: true,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		Expires:  absoluteExpires,
	})
}

func clearSessionCookie(w http.ResponseWriter, secure bool) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookieName,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		Secure:   secure,
		SameSite: http.SameSiteStrictMode,
		MaxAge:   -1,
	})
}
