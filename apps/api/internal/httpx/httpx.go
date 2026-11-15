// Package httpx provides JSON response and error helpers shared by handlers.
package httpx

import (
	"encoding/json"
	"log/slog"
	"net/http"
)

// Error codes returned in the response body. They are stable API surface.
const (
	CodeValidationFailed    = "validation_failed"
	CodeInvalidCredentials  = "invalid_credentials"
	CodeEmailTaken          = "email_taken"
	CodeUsernameTaken       = "username_taken"
	CodeUnauthorized        = "unauthorized"
	CodeSessionExpired      = "session_expired"
	CodeCSRFInvalid         = "csrf_invalid"
	CodeNotFound            = "not_found"
	CodeRateLimited         = "rate_limited"
	CodeInternal            = "internal_error"
	CodeKeyMetadataNotFound = "key_metadata_not_found"
	CodeKeyMetadataExists   = "key_metadata_exists"
	CodeLibraryItemNotFound = "library_item_not_found"
)

// ErrorDetail is the machine-readable part of an error response.
type ErrorDetail struct {
	Code    string `json:"code" example:"invalid_credentials"`
	Message string `json:"message" example:"invalid credentials"`
}

// ErrorBody is the standard error envelope.
type ErrorBody struct {
	Error ErrorDetail `json:"error"`
}

// WriteJSON serializes v as JSON with the given status code.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if v == nil {
		return
	}
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("encode response", slog.String("error", err.Error()))
	}
}

// WriteError writes a standardized error body.
func WriteError(w http.ResponseWriter, status int, code, message string) {
	WriteJSON(w, status, ErrorBody{Error: ErrorDetail{Code: code, Message: message}})
}

// DecodeJSON reads a JSON request body with a size limit.
func DecodeJSON(w http.ResponseWriter, r *http.Request, v any) bool {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // 1 MiB
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		WriteError(w, http.StatusBadRequest, CodeValidationFailed, "invalid request body")
		return false
	}
	return true
}
