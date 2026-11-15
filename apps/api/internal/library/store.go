// Package library stores and serves the E2EE-encrypted library items of a
// user. The server never sees the plaintext, the keys or the nonces beyond
// the ones embedded in the opaque ciphertext blobs: every payload is
// produced and consumed by the clients. The only clear-text field is
// item_key ("platform:external_id"), an opaque identifier required for
// idempotent upserts and keyset pagination.
package library

import (
	"context"
	"encoding/base64"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ErrNotFound is returned when no item exists for the given key.
var ErrNotFound = errors.New("library item not found")

// Item is a row in library_items as handed to the store (decoded blobs).
type Item struct {
	ItemKey    string
	Payload    []byte
	Transcript []byte
	Thumb      []byte
}

// SummaryItem is what list responses carry: the key and the payload only.
type SummaryItem struct {
	ItemKey   string    `json:"item_key"`
	Payload   []byte    `json:"payload"`
	UpdatedAt time.Time `json:"updated_at"`
}

// Detail is the full per-item read model (everything but the thumbnail).
type Detail struct {
	ItemKey    string    `json:"item_key"`
	Payload    []byte    `json:"payload"`
	Transcript []byte    `json:"transcript"`
	UpdatedAt  time.Time `json:"updated_at"`
}

// ManifestEntry is a lightweight change-detection record for clients.
type ManifestEntry struct {
	ItemKey   string    `json:"item_key"`
	UpdatedAt time.Time `json:"updated_at"`
}

// Store accesses library_items.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a store.
func NewStore(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

// UpsertBatch inserts or replaces the given items for the user in a single
// transaction. Idempotent: re-uploading the same item_key overwrites the
// previous blobs.
func (s *Store) UpsertBatch(ctx context.Context, userID uuid.UUID, items []Item) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	for _, it := range items {
		var transcript, thumb any
		if it.Transcript != nil {
			transcript = it.Transcript
		}
		if it.Thumb != nil {
			thumb = it.Thumb
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO library_items (user_id, item_key, payload, transcript, thumb)
			VALUES ($1, $2, $3, $4, $5)
			ON CONFLICT (user_id, item_key) DO UPDATE
			SET payload = EXCLUDED.payload,
			    transcript = EXCLUDED.transcript,
			    thumb = EXCLUDED.thumb,
			    updated_at = now()`,
			userID, it.ItemKey, it.Payload, transcript, thumb)
		if err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

// Cursor is a keyset pagination cursor over (updated_at, item_key).
type Cursor struct {
	UpdatedAt time.Time
	ItemKey   string
}

// EncodeCursor serializes a cursor as base64url("<rfc3339nano>|<item_key>").
func EncodeCursor(c Cursor) string {
	return base64.RawURLEncoding.EncodeToString(
		[]byte(c.UpdatedAt.UTC().Format(time.RFC3339Nano) + "|" + c.ItemKey))
}

// DecodeCursor parses a cursor produced by EncodeCursor.
func DecodeCursor(s string) (Cursor, error) {
	b, err := base64.RawURLEncoding.DecodeString(s)
	if err != nil {
		return Cursor{}, err
	}
	for i := len(b) - 1; i >= 0; i-- {
		if b[i] == '|' {
			ts, err := time.Parse(time.RFC3339Nano, string(b[:i]))
			if err != nil {
				return Cursor{}, err
			}
			return Cursor{UpdatedAt: ts, ItemKey: string(b[i+1:])}, nil
		}
	}
	return Cursor{}, errors.New("invalid cursor")
}

// List returns up to limit items after the cursor (or from the beginning).
// The second return value is the cursor to continue with, or nil when done.
func (s *Store) List(ctx context.Context, userID uuid.UUID, limit int, after *Cursor) ([]SummaryItem, *Cursor, error) {
	var rows pgx.Rows
	var err error
	if after == nil {
		rows, err = s.pool.Query(ctx, `
			SELECT item_key, payload, updated_at FROM library_items
			WHERE user_id = $1
			ORDER BY updated_at, item_key
			LIMIT $2`, userID, limit+1)
	} else {
		rows, err = s.pool.Query(ctx, `
			SELECT item_key, payload, updated_at FROM library_items
			WHERE user_id = $1 AND (updated_at, item_key) > ($2, $3)
			ORDER BY updated_at, item_key
			LIMIT $4`, userID, after.UpdatedAt, after.ItemKey, limit+1)
	}
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	items := make([]SummaryItem, 0, limit+1)
	for rows.Next() {
		var it SummaryItem
		if err := rows.Scan(&it.ItemKey, &it.Payload, &it.UpdatedAt); err != nil {
			return nil, nil, err
		}
		items = append(items, it)
	}
	if err := rows.Err(); err != nil {
		return nil, nil, err
	}
	if len(items) > limit {
		last := items[limit-1]
		next := Cursor{UpdatedAt: last.UpdatedAt, ItemKey: last.ItemKey}
		return items[:limit], &next, nil
	}
	return items, nil, nil
}

// Get returns the detail of one item (no thumbnail).
func (s *Store) Get(ctx context.Context, userID uuid.UUID, itemKey string) (Detail, error) {
	var d Detail
	err := s.pool.QueryRow(ctx, `
		SELECT item_key, payload, transcript, updated_at FROM library_items
		WHERE user_id = $1 AND item_key = $2`, userID, itemKey).
		Scan(&d.ItemKey, &d.Payload, &d.Transcript, &d.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return Detail{}, ErrNotFound
	}
	return d, err
}

// GetThumb returns the encrypted thumbnail bytes of one item, or ErrNotFound.
func (s *Store) GetThumb(ctx context.Context, userID uuid.UUID, itemKey string) ([]byte, error) {
	var thumb []byte
	err := s.pool.QueryRow(ctx, `
		SELECT thumb FROM library_items
		WHERE user_id = $1 AND item_key = $2`, userID, itemKey).Scan(&thumb)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, ErrNotFound
	}
	if err != nil {
		return nil, err
	}
	if thumb == nil {
		return nil, ErrNotFound
	}
	return thumb, nil
}

// Manifest returns the (item_key, updated_at) pair of every item of the
// user, lightest possible change-detection feed for clients.
func (s *Store) Manifest(ctx context.Context, userID uuid.UUID) ([]ManifestEntry, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT item_key, updated_at FROM library_items
		WHERE user_id = $1 ORDER BY item_key`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	entries := make([]ManifestEntry, 0, 256)
	for rows.Next() {
		var e ManifestEntry
		if err := rows.Scan(&e.ItemKey, &e.UpdatedAt); err != nil {
			return nil, err
		}
		entries = append(entries, e)
	}
	return entries, rows.Err()
}

// Count returns the number of items of the user.
func (s *Store) Count(ctx context.Context, userID uuid.UUID) (int64, error) {
	var n int64
	err := s.pool.QueryRow(ctx, `SELECT count(*) FROM library_items WHERE user_id = $1`, userID).Scan(&n)
	return n, err
}

// DeleteAll removes every library item of the user and returns the count.
func (s *Store) DeleteAll(ctx context.Context, userID uuid.UUID) (int64, error) {
	tag, err := s.pool.Exec(ctx, `DELETE FROM library_items WHERE user_id = $1`, userID)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}
