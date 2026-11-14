package auth

import (
	"errors"
	"log/slog"
	"net/http"

	"save-finder/api/internal/httpx"
	"save-finder/api/internal/users"
	"save-finder/api/internal/validation"
)

// GetAccount returns the current account and a fresh CSRF token.
//
// @Summary     Get the current account
// @Tags        account
// @Produce     json
// @Success     200 {object} AccountResponse
// @Failure     401 {object} httpx.ErrorBody
// @Router      /auth/account [get]
func (h *Handler) GetAccount(w http.ResponseWriter, r *http.Request) {
	p, ok := PrincipalFrom(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	httpx.WriteJSON(w, http.StatusOK, AccountResponse{User: userResponse(p.User), CSRFToken: p.Session.CSRFToken})
}

// UpdateAccount changes the email and/or username of the account.
//
// @Summary     Update the account
// @Tags        account
// @Accept      json
// @Produce     json
// @Param       request body UpdateAccountRequest true "Fields to update (at least one)"
// @Success     200 {object} AccountResponse
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Failure     409 {object} httpx.ErrorBody
// @Router      /auth/account [patch]
func (h *Handler) UpdateAccount(w http.ResponseWriter, r *http.Request) {
	p, ok := PrincipalFrom(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req UpdateAccountRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	if req.Email == nil && req.Username == nil {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "at least one field must be provided")
		return
	}

	u := p.User
	var err error
	if req.Email != nil {
		email := validation.NormalizeEmail(*req.Email)
		if err := validation.ValidateEmail(email); err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid email")
			return
		}
		u, err = h.Users.UpdateEmail(r.Context(), u.ID, email)
		if err != nil {
			h.writeUpdateErr(w, r, err, "email")
			return
		}
	}
	if req.Username != nil {
		username := validation.NormalizeUsername(*req.Username)
		if err := validation.ValidateUsername(username); err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
			return
		}
		u, err = h.Users.UpdateUsername(r.Context(), u.ID, username)
		if err != nil {
			h.writeUpdateErr(w, r, err, "username")
			return
		}
	}

	h.Logger.InfoContext(r.Context(), "account updated", slog.String("user_id", u.ID.String()))
	httpx.WriteJSON(w, http.StatusOK, AccountResponse{User: userResponse(u), CSRFToken: p.Session.CSRFToken})
}

func (h *Handler) writeUpdateErr(w http.ResponseWriter, r *http.Request, err error, field string) {
	switch {
	case errors.Is(err, users.ErrEmailTaken):
		httpx.WriteError(w, http.StatusConflict, httpx.CodeEmailTaken, "email is already registered")
	case errors.Is(err, users.ErrUsernameTaken):
		httpx.WriteError(w, http.StatusConflict, httpx.CodeUsernameTaken, "username is already taken")
	case errors.Is(err, users.ErrNotFound):
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "account no longer exists")
	default:
		h.internalError(w, r, "update "+field, err)
	}
}

// DeleteAccount permanently deletes the account, all associated data
// (via cascade) and every session. Requires the current password.
//
// @Summary     Delete the account
// @Tags        account
// @Accept      json
// @Produce     json
// @Param       request body DeleteAccountRequest true "Current password for confirmation"
// @Success     204
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Router      /auth/account [delete]
func (h *Handler) DeleteAccount(w http.ResponseWriter, r *http.Request) {
	p, ok := PrincipalFrom(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req DeleteAccountRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	okPassword, err := VerifyPassword(req.Password, p.User.PasswordHash)
	if err != nil || !okPassword {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
		return
	}
	if err := h.Users.Delete(r.Context(), p.User.ID); err != nil {
		h.internalError(w, r, "delete account", err)
		return
	}
	clearSessionCookie(w, h.CookieSecure)
	h.Logger.InfoContext(r.Context(), "account deleted", slog.String("user_id", p.User.ID.String()))
	w.WriteHeader(http.StatusNoContent)
}

// ChangePassword changes the password and revokes all other sessions,
// keeping the current one.
//
// @Summary     Change the password
// @Tags        account
// @Accept      json
// @Produce     json
// @Param       request body ChangePasswordRequest true "Current and new password"
// @Success     204
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Router      /auth/account/password [put]
func (h *Handler) ChangePassword(w http.ResponseWriter, r *http.Request) {
	p, ok := PrincipalFrom(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req ChangePasswordRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	okPassword, err := VerifyPassword(req.CurrentPassword, p.User.PasswordHash)
	if err != nil || !okPassword {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
		return
	}
	if err := validation.ValidatePassword(req.NewPassword); err != nil {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
		return
	}
	if req.NewPassword != req.NewPasswordConfirmation {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "password confirmation does not match")
		return
	}
	hash, err := HashPassword(req.NewPassword)
	if err != nil {
		h.internalError(w, r, "hash password", err)
		return
	}
	if err := h.Users.UpdatePasswordHash(r.Context(), p.User.ID, hash); err != nil {
		h.internalError(w, r, "update password", err)
		return
	}
	if err := h.Sessions.RevokeAllExcept(r.Context(), p.User.ID, p.Session.ID); err != nil {
		h.Logger.ErrorContext(r.Context(), "revoke other sessions", slog.String("error", err.Error()))
	}
	h.Logger.InfoContext(r.Context(), "password changed", slog.String("user_id", p.User.ID.String()))
	w.WriteHeader(http.StatusNoContent)
}
