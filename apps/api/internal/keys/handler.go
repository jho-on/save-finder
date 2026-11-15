package keys

import (
	"encoding/base64"
	"log/slog"
	"net/http"

	"save-finder/api/internal/cryptometa"
	"save-finder/api/internal/httpx"
	"save-finder/api/internal/password"
	"save-finder/api/internal/principal"
	"save-finder/api/internal/users"
)

// KeySetupRequest is the body of POST /auth/crypto. Binary fields are
// base64-encoded. CurrentPassword confirms the caller owns the account,
// so a stolen session alone cannot set up the user's keys.
type KeySetupRequest struct {
	Salt            string `json:"salt" example:"e3NhbHQ="`
	WrappedDEK      string `json:"wrapped_dek" example:"e3dyYXBwZWQ="`
	DEKNonce        string `json:"dek_nonce" example:"e25vbmNl"`
	KDFOpsLimit     int64  `json:"kdf_ops_limit" example:"3"`
	KDFMemLimit     int64  `json:"kdf_mem_limit" example:"268435456"`
	CurrentPassword string `json:"current_password" example:"s3cret-s3cret"`
}

// KeyMetadataResponse is returned by GET and POST /auth/crypto.
type KeyMetadataResponse struct {
	Salt        string `json:"salt"`
	WrappedDEK  string `json:"wrapped_dek"`
	DEKNonce    string `json:"dek_nonce"`
	KDFOpsLimit int64  `json:"kdf_ops_limit"`
	KDFMemLimit int64  `json:"kdf_mem_limit"`
}

// Handler exposes the crypto metadata endpoints.
type Handler struct {
	Store  *Store
	Users  *users.Store
	Logger *slog.Logger
}

// NewHandler creates a handler.
func NewHandler(store *Store, usersStore *users.Store, logger *slog.Logger) *Handler {
	return &Handler{Store: store, Users: usersStore, Logger: logger}
}

func response(m Metadata) KeyMetadataResponse {
	return KeyMetadataResponse{
		Salt:        base64.StdEncoding.EncodeToString(m.Salt),
		WrappedDEK:  base64.StdEncoding.EncodeToString(m.WrappedDEK),
		DEKNonce:    base64.StdEncoding.EncodeToString(m.DEKNonce),
		KDFOpsLimit: m.KDFOpsLimit,
		KDFMemLimit: m.KDFMemLimit,
	}
}

// Get returns the current user's crypto metadata.
//
// @Summary     Get the user's key metadata
// @Tags        crypto
// @Produce     json
// @Success     200 {object} KeyMetadataResponse
// @Failure     401 {object} httpx.ErrorBody
// @Failure     404 {object} httpx.ErrorBody
// @Router      /auth/crypto [get]
func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	m, err := h.Store.Get(r.Context(), p.User.ID)
	if err != nil {
		if err == ErrNotFound {
			httpx.WriteError(w, http.StatusNotFound, httpx.CodeKeyMetadataNotFound, "key metadata not set up yet")
			return
		}
		h.internalError(w, r, "get key metadata", err)
		return
	}
	httpx.WriteJSON(w, http.StatusOK, response(m))
}

// Setup stores the crypto metadata produced by the client. It can only be
// called once per account: replacing the metadata happens atomically
// through PUT /auth/account/password during a password change. Requires
// the current password so a stolen session alone cannot set up (or
// hijack) the user's keys.
//
// @Summary     Store the user's key metadata (first time only)
// @Tags        crypto
// @Accept      json
// @Produce     json
// @Param       request body KeySetupRequest true "Wrapped DEK, KDF parameters and current password"
// @Success     201 {object} KeyMetadataResponse
// @Failure     400 {object} httpx.ErrorBody
// @Failure     401 {object} httpx.ErrorBody
// @Failure     409 {object} httpx.ErrorBody
// @Router      /auth/crypto [post]
func (h *Handler) Setup(w http.ResponseWriter, r *http.Request) {
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req KeySetupRequest
	if !httpx.DecodeJSON(w, r, &req) {
		return
	}
	params, err := cryptometa.Validate(req.Salt, req.WrappedDEK, req.DEKNonce, req.KDFOpsLimit, req.KDFMemLimit)
	if err != nil {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
		return
	}
	if req.CurrentPassword == "" {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "current_password is required")
		return
	}
	okPassword, err := password.VerifyPassword(req.CurrentPassword, p.User.PasswordHash)
	if err != nil || !okPassword {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
		return
	}
	m := Metadata{
		UserID:      p.User.ID,
		Salt:        params.Salt,
		WrappedDEK:  params.WrappedDEK,
		DEKNonce:    params.DEKNonce,
		KDFOpsLimit: params.KDFOpsLimit,
		KDFMemLimit: params.KDFMemLimit,
	}
	created, err := h.Store.Create(r.Context(), m)
	if err != nil {
		if err == ErrAlreadyExists {
			httpx.WriteError(w, http.StatusConflict, httpx.CodeKeyMetadataExists, "key metadata already set up")
			return
		}
		h.internalError(w, r, "store key metadata", err)
		return
	}
	h.Logger.InfoContext(r.Context(), "key metadata set up", slog.String("user_id", p.User.ID.String()))
	httpx.WriteJSON(w, http.StatusCreated, response(created))
}

func (h *Handler) internalError(w http.ResponseWriter, r *http.Request, op string, err error) {
	h.Logger.ErrorContext(r.Context(), op, slog.String("error", err.Error()))
	httpx.WriteError(w, http.StatusInternalServerError, httpx.CodeInternal, "internal error")
}
