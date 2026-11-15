package auth

import (
	"errors"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/google/uuid"

	"save-finder/api/internal/httpx"
	"save-finder/api/internal/password"
	"save-finder/api/internal/principal"
	"save-finder/api/internal/ratelimit"
	"save-finder/api/internal/users"
	"save-finder/api/internal/validation"
)

// Register creates a new account and logs the user in.
//
// @Summary     Register a new account
// @Tags        auth
// @Accept      json
// @Produce     json
// @Param       request body RegisterRequest true "Registration data"
// @Success     201 {object} AuthResponse
// @Failure     400 {object} httpx.ErrorBody
// @Failure     409 {object} httpx.ErrorBody
// @Failure     429 {object} httpx.ErrorBody
// @Router      /auth/register [post]
func (h *Handler) Register(w http.ResponseWriter, r *http.Request) {
	var req RegisterRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	ip := clientIP(r)
	email := validation.NormalizeEmail(req.Email)
	username := validation.NormalizeUsername(req.Username)

	if err := validation.ValidateEmail(email); err != nil {
		h.IPRisk.Penalize(ip, ratelimit.PointsValidationErr)
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid email")
		return
	}
	if err := validation.ValidateUsername(username); err != nil {
		h.IPRisk.Penalize(ip, ratelimit.PointsValidationErr)
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
		return
	}
	if err := validation.ValidatePassword(req.Password); err != nil {
		h.IPRisk.Penalize(ip, ratelimit.PointsValidationErr)
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
		return
	}
	if req.Password != req.PasswordConfirmation {
		h.IPRisk.Penalize(ip, ratelimit.PointsValidationErr)
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "password confirmation does not match")
		return
	}
	h.IPRisk.Penalize(ip, ratelimit.PointsRegister)

	hash, err := password.HashPassword(req.Password)
	if err != nil {
		h.internalError(w, r, "hash password", err)
		return
	}
	u, err := h.Users.Create(r.Context(), email, username, hash)
	if err != nil {
		switch {
		case errors.Is(err, users.ErrEmailTaken):
			httpx.WriteError(w, http.StatusConflict, httpx.CodeEmailTaken, "email is already registered")
		case errors.Is(err, users.ErrUsernameTaken):
			httpx.WriteError(w, http.StatusConflict, httpx.CodeUsernameTaken, "username is already taken")
		default:
			h.internalError(w, r, "create user", err)
		}
		return
	}

	sess, token, err := h.Sessions.Create(r.Context(), u.ID, r.UserAgent(), false)
	if err != nil {
		h.internalError(w, r, "create session", err)
		return
	}
	setSessionCookie(w, token, h.CookieSecure, sess.AbsoluteExpiresAt)
	h.Logger.InfoContext(r.Context(), "user registered", slog.String("user_id", u.ID.String()))
	httpx.WriteJSON(w, http.StatusCreated, AuthResponse{User: userResponse(u), CSRFToken: sess.CSRFToken})
}

// Login authenticates with email or username plus password.
//
// @Summary     Log in
// @Tags        auth
// @Accept      json
// @Produce     json
// @Param       request body LoginRequest true "Credentials"
// @Success     200 {object} AuthResponse
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Failure     429 {object} httpx.ErrorBody
// @Router      /auth/login [post]
func (h *Handler) Login(w http.ResponseWriter, r *http.Request) {
	var req LoginRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	ip := clientIP(r)
	identifier := validation.NormalizeIdentifier(req.Identifier)

	u, err := h.Users.GetByIdentifier(r.Context(), identifier)
	if err != nil {
		if errors.Is(err, users.ErrNotFound) {
			// Equalize timing with the wrong-password path.
			_, _ = password.VerifyPassword(req.Password, h.dummyHash)
			h.IPRisk.Penalize(ip, ratelimit.PointsLoginFailure)
			h.Logger.InfoContext(r.Context(), "login failed: unknown identifier", slog.String("ip", ip))
			httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
			return
		}
		h.internalError(w, r, "lookup user", err)
		return
	}

	ok, err := password.VerifyPassword(req.Password, u.PasswordHash)
	if err != nil || !ok {
		h.IPRisk.Penalize(ip, ratelimit.PointsLoginFailure)
		h.Logger.InfoContext(r.Context(), "login failed: wrong password", slog.String("user_id", u.ID.String()))
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
		return
	}

	h.IPRisk.Reset(ip)
	sess, token, err := h.Sessions.Create(r.Context(), u.ID, r.UserAgent(), req.RememberMe)
	if err != nil {
		h.internalError(w, r, "create session", err)
		return
	}
	setSessionCookie(w, token, h.CookieSecure, sess.AbsoluteExpiresAt)
	h.Logger.InfoContext(r.Context(), "login", slog.String("user_id", u.ID.String()), slog.Bool("remember_me", req.RememberMe))
	httpx.WriteJSON(w, http.StatusOK, AuthResponse{User: userResponse(u), CSRFToken: sess.CSRFToken})
}

// Logout revokes the current session and clears the cookie.
//
// @Summary     Log out of the current session
// @Tags        auth
// @Produce     json
// @Success     204
// @Router      /auth/logout [post]
func (h *Handler) Logout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie(sessionCookieName); err == nil && cookie.Value != "" {
		if sess, err := h.Sessions.Lookup(r.Context(), cookie.Value); err == nil {
			_, _ = h.Sessions.Revoke(r.Context(), sess.ID, sess.UserID)
			h.Logger.InfoContext(r.Context(), "logout", slog.String("user_id", sess.UserID.String()))
		}
	}
	clearSessionCookie(w, h.CookieSecure)
	w.WriteHeader(http.StatusNoContent)
}

// LogoutAll revokes every session of the account, including the current one.
//
// @Summary     Log out of all sessions
// @Tags        auth
// @Produce     json
// @Success     204
// @Failure     401 {object} httpx.ErrorBody
// @Router      /auth/logout-all [post]
func (h *Handler) LogoutAll(w http.ResponseWriter, r *http.Request) {
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	if err := h.Sessions.RevokeAll(r.Context(), p.User.ID); err != nil {
		h.internalError(w, r, "revoke sessions", err)
		return
	}
	clearSessionCookie(w, h.CookieSecure)
	h.Logger.InfoContext(r.Context(), "logout-all", slog.String("user_id", p.User.ID.String()))
	w.WriteHeader(http.StatusNoContent)
}

// ListSessions lists the active sessions of the account, one per device.
//
// @Summary     List active sessions
// @Tags        auth
// @Produce     json
// @Success     200 {object} SessionListResponse
// @Failure     401 {object} httpx.ErrorBody
// @Router      /auth/sessions [get]
func (h *Handler) ListSessions(w http.ResponseWriter, r *http.Request) {
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	list, err := h.Sessions.List(r.Context(), p.User.ID)
	if err != nil {
		h.internalError(w, r, "list sessions", err)
		return
	}
	out := SessionListResponse{Sessions: make([]SessionResponse, 0, len(list))}
	for _, s := range list {
		out.Sessions = append(out.Sessions, SessionResponse{
			ID:                s.ID.String(),
			UserAgent:         s.UserAgent,
			RememberMe:        s.RememberMe,
			CreatedAt:         s.CreatedAt.UTC().Format("2006-01-02T15:04:05Z"),
			LastSeenAt:        s.LastSeenAt.UTC().Format("2006-01-02T15:04:05Z"),
			AbsoluteExpiresAt: s.AbsoluteExpiresAt.UTC().Format("2006-01-02T15:04:05Z"),
			Current:           s.ID == p.Session.ID,
		})
	}
	httpx.WriteJSON(w, http.StatusOK, out)
}

// RevokeSession revokes a single session of the account.
//
// @Summary     Revoke a session
// @Tags        auth
// @Produce     json
// @Param       id path string true "Session ID"
// @Success     204
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Failure     404 {object} httpx.ErrorBody
// @Router      /auth/sessions/{id} [delete]
func (h *Handler) RevokeSession(w http.ResponseWriter, r *http.Request) {
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	id, err := uuid.Parse(chi.URLParam(r, "id"))
	if err != nil {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid session id")
		return
	}
	deleted, err := h.Sessions.Revoke(r.Context(), id, p.User.ID)
	if err != nil {
		h.internalError(w, r, "revoke session", err)
		return
	}
	if !deleted {
		httpx.WriteError(w, http.StatusNotFound, httpx.CodeNotFound, "session not found")
		return
	}
	if id == p.Session.ID {
		clearSessionCookie(w, h.CookieSecure)
	}
	h.Logger.InfoContext(r.Context(), "session revoked", slog.String("user_id", p.User.ID.String()))
	w.WriteHeader(http.StatusNoContent)
}

func (h *Handler) internalError(w http.ResponseWriter, r *http.Request, op string, err error) {
	h.Logger.ErrorContext(r.Context(), op, slog.String("error", err.Error()))
	httpx.WriteError(w, http.StatusInternalServerError, httpx.CodeInternal, "internal error")
}
