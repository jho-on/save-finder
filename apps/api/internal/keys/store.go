// Package keys stores and serves the encrypted key metadata (DEK/KEK)
// of a user. The server never sees the encryption password, the DEK
// or any plaintext: it only stores opaque blobs produced by the client.
package keys

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// Bounds for validation live in internal/cryptometa (shared with internal/auth).

// ErrNotFound is returned when no metadata exists for the user.
var ErrNotFound = errors.New("key metadata not found")

// ErrAlreadyExists is returned when metadata already exists for the user.
var ErrAlreadyExists = errors.New("key metadata already exists")

// Metadata is a row in user_key_metadata.
type Metadata struct {
	UserID      uuid.UUID
	Salt        []byte
	WrappedDEK  []byte
	DEKNonce    []byte
	KDFOpsLimit int64
	KDFMemLimit int64
	CreatedAt   time.Time
	UpdatedAt   time.Time
}

// Store accesses user_key_metadata.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a metadata store.
func NewStore(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

// Get returns the metadata for a user.
func (s *Store) Get(ctx context.Context, userID uuid.UUID) (Metadata, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT user_id, salt, wrapped_dek, dek_nonce, kdf_ops_limit, kdf_mem_limit, created_at, updated_at
		 FROM user_key_metadata WHERE user_id = $1`, userID)
	m, err := scanMetadata(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return Metadata{}, ErrNotFound
	}
	return m, err
}

// UpdatePasswordAndWrappedDEK atomically updates the login password hash in
// users and replaces (or inserts) the wrapped key metadata in
// user_key_metadata, in a single transaction. The DEK itself does not change:
// only the KEK wrapping it is re-derived client-side from the new password.
func (s *Store) UpdatePasswordAndWrappedDEK(ctx context.Context, userID uuid.UUID, passwordHash string, m Metadata) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	tag, err := tx.Exec(ctx,
		`UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1`,
		userID, passwordHash)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}

	_, err = tx.Exec(ctx, `
		INSERT INTO user_key_metadata (user_id, salt, wrapped_dek, dek_nonce, kdf_ops_limit, kdf_mem_limit)
		VALUES ($1, $2, $3, $4, $5, $6)
		ON CONFLICT (user_id) DO UPDATE
		SET salt = EXCLUDED.salt,
		    wrapped_dek = EXCLUDED.wrapped_dek,
		    dek_nonce = EXCLUDED.dek_nonce,
		    kdf_ops_limit = EXCLUDED.kdf_ops_limit,
		    kdf_mem_limit = EXCLUDED.kdf_mem_limit,
		    updated_at = now()`,
		userID, m.Salt, m.WrappedDEK, m.DEKNonce, m.KDFOpsLimit, m.KDFMemLimit)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// Create inserts the metadata. Fails with ErrAlreadyExists if present.
func (s *Store) Create(ctx context.Context, m Metadata) (Metadata, error) {
	tag, err := s.pool.Exec(ctx,
		`INSERT INTO user_key_metadata (user_id, salt, wrapped_dek, dek_nonce, kdf_ops_limit, kdf_mem_limit)
		 VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (user_id) DO NOTHING`,
		m.UserID, m.Salt, m.WrappedDEK, m.DEKNonce, m.KDFOpsLimit, m.KDFMemLimit)
	if err != nil {
		return Metadata{}, err
	}
	if tag.RowsAffected() == 0 {
		return Metadata{}, ErrAlreadyExists
	}
	return s.Get(ctx, m.UserID)
}

func scanMetadata(row pgx.Row) (Metadata, error) {
	var m Metadata
	err := row.Scan(&m.UserID, &m.Salt, &m.WrappedDEK, &m.DEKNonce,
		&m.KDFOpsLimit, &m.KDFMemLimit, &m.CreatedAt, &m.UpdatedAt)
	return m, err
}
