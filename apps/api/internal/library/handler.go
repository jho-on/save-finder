package library

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"

	"save-finder/api/internal/httpx"
	"save-finder/api/internal/password"
	"save-finder/api/internal/principal"
	"save-finder/api/internal/users"
)

// Limits for the encrypted blobs, decoded.
const (
	maxPayloadBytes    = 2 << 20 // 2 MiB
	maxTranscriptBytes = 4 << 20 // 4 MiB
	maxThumbBytes      = 4 << 20 // 4 MiB
	maxBatchItems      = 50
	maxBodyBytes       = 40 << 20 // 40 MiB envelope for batches with thumbnails
	maxListLimit       = 200
	defaultListLimit   = 100
)

var itemKeyRe = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,32}:[A-Za-z0-9_.-]{1,128}$`)

// Handler exposes the library endpoints.
type Handler struct {
	Store  *Store
	Users  *users.Store
	Logger *slog.Logger
}

// NewHandler creates a handler.
func NewHandler(store *Store, usersStore *users.Store, logger *slog.Logger) *Handler {
	return &Handler{Store: store, Users: usersStore, Logger: logger}
}

func noStore(w http.ResponseWriter) {
	w.Header().Set("Cache-Control", "no-store")
}

// BatchItem is one encrypted item in an upsert batch. Blobs are base64.
type BatchItem struct {
	ItemKey    string `json:"item_key" example:"instagram:ABC123"`
	Payload    string `json:"payload" example:"base64-ciphertext"`
	Transcript string `json:"transcript,omitempty"`
	Thumb      string `json:"thumb,omitempty"`
}

// BatchRequest is the body of POST /library/items/batch.
type BatchRequest struct {
	Items []BatchItem `json:"items"`
}

// BatchResponse reports how many items were upserted.
type BatchResponse struct {
	Upserted int `json:"upserted"`
}

// ListResponse is a page of items (payload only, no transcript/thumb).
type ListResponse struct {
	Items      []listItemJSON `json:"items"`
	NextCursor string         `json:"next_cursor,omitempty"`
}

type listItemJSON struct {
	ItemKey   string `json:"item_key"`
	Payload   string `json:"payload"`
	UpdatedAt string `json:"updated_at"`
}

// DetailResponse is the per-item read model.
type DetailResponse struct {
	ItemKey    string  `json:"item_key"`
	Payload    string  `json:"payload"`
	Transcript *string `json:"transcript"`
	UpdatedAt  string  `json:"updated_at"`
}

// ManifestResponse is the lightweight change-detection feed.
type ManifestResponse struct {
	Items []manifestEntryJSON `json:"items"`
}

type manifestEntryJSON struct {
	ItemKey   string `json:"item_key"`
	UpdatedAt string `json:"updated_at"`
}

// WipeRequest is the body of DELETE /library. The password is required:
// wiping every stored post must not be possible with a stolen session alone.
type WipeRequest struct {
	Password string `json:"password" example:"s3cret-s3cret"`
}

// WipeResponse reports how many items were deleted.
type WipeResponse struct {
	Deleted int64 `json:"deleted"`
}

func decodeBlob(field, value string, max int, required bool) ([]byte, error) {
	if value == "" {
		if required {
			return nil, fmt.Errorf("%s is required", field)
		}
		return nil, nil
	}
	b, err := base64.StdEncoding.DecodeString(value)
	if err != nil {
		if b, err = base64.RawStdEncoding.DecodeString(value); err != nil {
			return nil, fmt.Errorf("%s is not valid base64", field)
		}
	}
	if len(b) < 40 || len(b) > max {
		return nil, fmt.Errorf("%s must be between 40 and %d bytes", field, max)
	}
	return b, nil
}

func validItemKey(key string) bool {
	return itemKeyRe.MatchString(key)
}

// decodeJSON is DecodeJSON with a per-route size limit.
func decodeJSON(w http.ResponseWriter, r *http.Request, v any, limit int64) bool {
	r.Body = http.MaxBytesReader(w, r.Body, limit)
	dec := json.NewDecoder(r.Body)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid request body")
		return false
	}
	return true
}

// Batch upserts a batch of encrypted items (idempotent).
//
// @Summary      Upsert a batch of encrypted library items
// @Tags         library
// @Accept       json
// @Produce      json
// @Security     cookieAuth
// @Param        request body BatchRequest true "Up to 50 encrypted items"
// @Success      200 {object} BatchResponse
// @Failure      400 {object} httpx.ErrorBody
// @Failure      401 {object} httpx.ErrorBody
// @Router       /library/items/batch [post]
func (h *Handler) Batch(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req BatchRequest
	if !decodeJSON(w, r, &req, maxBodyBytes) {
		return
	}
	if len(req.Items) == 0 || len(req.Items) > maxBatchItems {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed,
			fmt.Sprintf("items must contain between 1 and %d entries", maxBatchItems))
		return
	}
	items := make([]Item, 0, len(req.Items))
	for i, raw := range req.Items {
		if !validItemKey(raw.ItemKey) {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed,
				fmt.Sprintf("items[%d]: item_key has an invalid format", i))
			return
		}
		payload, err := decodeBlob(fmt.Sprintf("items[%d].payload", i), raw.Payload, maxPayloadBytes, true)
		if err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
			return
		}
		transcript, err := decodeBlob(fmt.Sprintf("items[%d].transcript", i), raw.Transcript, maxTranscriptBytes, false)
		if err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
			return
		}
		thumb, err := decodeBlob(fmt.Sprintf("items[%d].thumb", i), raw.Thumb, maxThumbBytes, false)
		if err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, err.Error())
			return
		}
		items = append(items, Item{ItemKey: raw.ItemKey, Payload: payload, Transcript: transcript, Thumb: thumb})
	}
	if err := h.Store.UpsertBatch(r.Context(), p.User.ID, items); err != nil {
		h.internalError(w, r, "upsert library batch", err)
		return
	}
	h.Logger.InfoContext(r.Context(), "library batch upserted",
		slog.String("user_id", p.User.ID.String()), slog.Int("items", len(items)))
	httpx.WriteJSON(w, http.StatusOK, BatchResponse{Upserted: len(items)})
}

// List returns a page of items (payload only).
//
// @Summary      List encrypted library items (paginated)
// @Tags         library
// @Produce      json
// @Security     cookieAuth
// @Param        limit  query int    false "Page size (default 100, max 200)"
// @Param        cursor query string false "Opaque keyset cursor"
// @Success      200 {object} ListResponse
// @Failure      401 {object} httpx.ErrorBody
// @Router       /library/items [get]
func (h *Handler) List(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	limit := defaultListLimit
	if raw := r.URL.Query().Get("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n < 1 {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "limit must be a positive integer")
			return
		}
		limit = min(n, maxListLimit)
	}
	var after *Cursor
	if raw := strings.TrimSpace(r.URL.Query().Get("cursor")); raw != "" {
		c, err := DecodeCursor(raw)
		if err != nil {
			httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid cursor")
			return
		}
		after = &c
	}
	items, next, err := h.Store.List(r.Context(), p.User.ID, limit, after)
	if err != nil {
		h.internalError(w, r, "list library items", err)
		return
	}
	resp := ListResponse{Items: make([]listItemJSON, 0, len(items))}
	for _, it := range items {
		resp.Items = append(resp.Items, listItemJSON{
			ItemKey:   it.ItemKey,
			Payload:   base64.StdEncoding.EncodeToString(it.Payload),
			UpdatedAt: it.UpdatedAt.UTC().Format(time.RFC3339Nano),
		})
	}
	if next != nil {
		resp.NextCursor = EncodeCursor(*next)
	}
	httpx.WriteJSON(w, http.StatusOK, resp)
}

// Get returns the detail of one item (payload + transcript).
//
// @Summary      Get one encrypted library item
// @Tags         library
// @Produce      json
// @Security     cookieAuth
// @Param        key path string true "item_key (platform:external_id)"
// @Success      200 {object} DetailResponse
// @Failure      401 {object} httpx.ErrorBody
// @Failure      404 {object} httpx.ErrorBody
// @Router       /library/items/{key} [get]
func (h *Handler) Get(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	key := chi.URLParam(r, "key")
	if !validItemKey(key) {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid item key")
		return
	}
	d, err := h.Store.Get(r.Context(), p.User.ID, key)
	if err != nil {
		if err == ErrNotFound {
			httpx.WriteError(w, http.StatusNotFound, httpx.CodeLibraryItemNotFound, "library item not found")
			return
		}
		h.internalError(w, r, "get library item", err)
		return
	}
	resp := DetailResponse{
		ItemKey:   d.ItemKey,
		Payload:   base64.StdEncoding.EncodeToString(d.Payload),
		UpdatedAt: d.UpdatedAt.UTC().Format(time.RFC3339Nano),
	}
	if d.Transcript != nil {
		s := base64.StdEncoding.EncodeToString(d.Transcript)
		resp.Transcript = &s
	}
	httpx.WriteJSON(w, http.StatusOK, resp)
}

// GetThumb returns the encrypted thumbnail bytes of one item.
//
// @Summary      Get the encrypted thumbnail of a library item
// @Tags         library
// @Produce      octet-stream
// @Security     cookieAuth
// @Param        key path string true "item_key (platform:external_id)"
// @Success      200 {file} binary
// @Failure      401 {object} httpx.ErrorBody
// @Failure      404 {object} httpx.ErrorBody
// @Router       /library/items/{key}/thumb [get]
func (h *Handler) GetThumb(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	key := chi.URLParam(r, "key")
	if !validItemKey(key) {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "invalid item key")
		return
	}
	thumb, err := h.Store.GetThumb(r.Context(), p.User.ID, key)
	if err != nil {
		if err == ErrNotFound {
			httpx.WriteError(w, http.StatusNotFound, httpx.CodeLibraryItemNotFound, "thumbnail not found")
			return
		}
		h.internalError(w, r, "get library thumbnail", err)
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", fmt.Sprint(len(thumb)))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(thumb)
}

// Manifest returns (item_key, updated_at) for every item of the user.
//
// @Summary      Manifest of the encrypted library (for client-side caches)
// @Tags         library
// @Produce      json
// @Security     cookieAuth
// @Success      200 {object} ManifestResponse
// @Failure      401 {object} httpx.ErrorBody
// @Router       /library/manifest [get]
func (h *Handler) Manifest(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	entries, err := h.Store.Manifest(r.Context(), p.User.ID)
	if err != nil {
		h.internalError(w, r, "library manifest", err)
		return
	}
	resp := ManifestResponse{Items: make([]manifestEntryJSON, 0, len(entries))}
	for _, e := range entries {
		resp.Items = append(resp.Items, manifestEntryJSON{
			ItemKey:   e.ItemKey,
			UpdatedAt: e.UpdatedAt.UTC().Format(time.RFC3339Nano),
		})
	}
	httpx.WriteJSON(w, http.StatusOK, resp)
}

// Wipe deletes every library item of the user. Requires the current
// password so a stolen session alone cannot destroy the remote copy.
//
// @Summary      Delete every library item of the account
// @Tags         library
// @Accept       json
// @Produce      json
// @Security     cookieAuth
// @Param        request body WipeRequest true "Current password for confirmation"
// @Success      200 {object} WipeResponse
// @Failure      401 {object} httpx.ErrorBody
// @Router       /library [delete]
func (h *Handler) Wipe(w http.ResponseWriter, r *http.Request) {
	noStore(w)
	p, ok := principal.From(r.Context())
	if !ok {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeUnauthorized, "authentication required")
		return
	}
	var req WipeRequest
	if !decodeJSON(w, r, &req, 1<<20) {
		return
	}
	if req.Password == "" {
		httpx.WriteError(w, http.StatusBadRequest, httpx.CodeValidationFailed, "password is required")
		return
	}
	okPassword, err := password.VerifyPassword(req.Password, p.User.PasswordHash)
	if err != nil || !okPassword {
		httpx.WriteError(w, http.StatusUnauthorized, httpx.CodeInvalidCredentials, "invalid credentials")
		return
	}
	deleted, err := h.Store.DeleteAll(r.Context(), p.User.ID)
	if err != nil {
		h.internalError(w, r, "wipe library", err)
		return
	}
	h.Logger.InfoContext(r.Context(), "library wiped",
		slog.String("user_id", p.User.ID.String()), slog.Int64("deleted", deleted))
	httpx.WriteJSON(w, http.StatusOK, WipeResponse{Deleted: deleted})
}

func (h *Handler) internalError(w http.ResponseWriter, r *http.Request, op string, err error) {
	h.Logger.ErrorContext(r.Context(), op, slog.String("error", err.Error()))
	h.internalErrorPlain(w, r)
}

func (h *Handler) internalErrorPlain(w http.ResponseWriter, _ *http.Request) {
	httpx.WriteError(w, http.StatusInternalServerError, httpx.CodeInternal, "internal error")
}
