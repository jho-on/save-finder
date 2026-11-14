// Package sessions implements server-side, per-device sessions stored in
// Postgres. The cookie carries an opaque random token; only its SHA-256
// hash is persisted. Idle and absolute expirations follow the
// "remember me" policy chosen at login.
package sessions

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	// DefaultIdleTimeout applies to standard sessions (1 day).
	DefaultIdleTimeout = 24 * time.Hour
	// DefaultAbsoluteTimeout applies to standard sessions (7 days).
	DefaultAbsoluteTimeout = 7 * 24 * time.Hour
	// RememberIdleTimeout applies to "remember me" sessions (7 days).
	RememberIdleTimeout = 7 * 24 * time.Hour
	// RememberAbsoluteTimeout applies to "remember me" sessions (30 days).
	RememberAbsoluteTimeout = 30 * 24 * time.Hour

	// touchInterval throttles sliding-expiration writes.
	touchInterval = 60 * time.Second
)

// ErrNotFound is returned when the session does not exist or expired.
var ErrNotFound = errors.New("session not found")

// Session is a row in the sessions table.
type Session struct {
	ID                uuid.UUID
	UserID            uuid.UUID
	CSRFToken         string
	RememberMe        bool
	UserAgent         string
	CreatedAt         time.Time
	LastSeenAt        time.Time
	IdleExpiresAt     time.Time
	AbsoluteExpiresAt time.Time
}

// Store accesses the sessions table.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a session store.
func NewStore(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

const sessionColumns = `id, user_id, csrf_token, remember_me, user_agent, created_at, last_seen_at, idle_expires_at, absolute_expires_at`

func scanSession(row pgx.Row) (Session, error) {
	var s Session
	err := row.Scan(&s.ID, &s.UserID, &s.CSRFToken, &s.RememberMe, &s.UserAgent,
		&s.CreatedAt, &s.LastSeenAt, &s.IdleExpiresAt, &s.AbsoluteExpiresAt)
	return s, err
}

func hashToken(token string) []byte {
	sum := sha256.Sum256([]byte(token))
	return sum[:]
}

func randomToken() (string, error) {
	buf := make([]byte, 32)
	if _, err := rand.Read(buf); err != nil {
		return "", fmt.Errorf("generate token: %w", err)
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}

// Create inserts a new session and returns it with the opaque cookie token.
func (s *Store) Create(ctx context.Context, userID uuid.UUID, userAgent string, rememberMe bool) (Session, string, error) {
	token, err := randomToken()
	if err != nil {
		return Session{}, "", err
	}
	csrf, err := randomToken()
	if err != nil {
		return Session{}, "", err
	}
	idle, absolute := DefaultIdleTimeout, DefaultAbsoluteTimeout
	if rememberMe {
		idle, absolute = RememberIdleTimeout, RememberAbsoluteTimeout
	}
	now := time.Now()
	row := s.pool.QueryRow(ctx,
		`INSERT INTO sessions (user_id, token_hash, csrf_token, remember_me, user_agent, idle_expires_at, absolute_expires_at)
		 VALUES ($1, $2, $3, $4, $5, $6, $7)
		 RETURNING `+sessionColumns,
		userID, hashToken(token), csrf, rememberMe, userAgent, now.Add(idle), now.Add(absolute))
	sess, err := scanSession(row)
	if err != nil {
		return Session{}, "", err
	}
	return sess, token, nil
}

// Lookup finds a valid (unexpired) session by its cookie token.
func (s *Store) Lookup(ctx context.Context, token string) (Session, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT `+sessionColumns+` FROM sessions
		 WHERE token_hash = $1 AND idle_expires_at > now() AND absolute_expires_at > now()`,
		hashToken(token))
	sess, err := scanSession(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return Session{}, ErrNotFound
	}
	return sess, err
}

// Touch applies sliding idle expiration, throttled to one write per minute.
func (s *Store) Touch(ctx context.Context, id uuid.UUID, rememberMe bool) error {
	idle := DefaultIdleTimeout
	if rememberMe {
		idle = RememberIdleTimeout
	}
	_, err := s.pool.Exec(ctx,
		`UPDATE sessions
		 SET last_seen_at = now(), idle_expires_at = now() + $2::interval
		 WHERE id = $1 AND last_seen_at < now() - $3::interval`,
		id, fmt.Sprintf("%d seconds", int64(idle.Seconds())),
		fmt.Sprintf("%d seconds", int64(touchInterval.Seconds())))
	return err
}

// List returns all active sessions for a user, newest first.
func (s *Store) List(ctx context.Context, userID uuid.UUID) ([]Session, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT `+sessionColumns+` FROM sessions
		 WHERE user_id = $1 AND idle_expires_at > now() AND absolute_expires_at > now()
		 ORDER BY last_seen_at DESC`, userID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Session
	for rows.Next() {
		sess, err := scanSession(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, sess)
	}
	return out, rows.Err()
}

// Revoke deletes a session owned by the user. Reports whether one was deleted.
func (s *Store) Revoke(ctx context.Context, id, userID uuid.UUID) (bool, error) {
	tag, err := s.pool.Exec(ctx,
		`DELETE FROM sessions WHERE id = $1 AND user_id = $2`, id, userID)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() > 0, nil
}

// RevokeAll deletes every session of the user (including the current one).
func (s *Store) RevokeAll(ctx context.Context, userID uuid.UUID) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM sessions WHERE user_id = $1`, userID)
	return err
}

// RevokeAllExcept deletes every session of the user except the given one.
func (s *Store) RevokeAllExcept(ctx context.Context, userID, keep uuid.UUID) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM sessions WHERE user_id = $1 AND id <> $2`, userID, keep)
	return err
}

// DeleteExpired removes expired sessions.
func (s *Store) DeleteExpired(ctx context.Context) (int64, error) {
	tag, err := s.pool.Exec(ctx,
		`DELETE FROM sessions WHERE idle_expires_at <= now() OR absolute_expires_at <= now()`)
	if err != nil {
		return 0, err
	}
	return tag.RowsAffected(), nil
}

// ConstantTimeCSRF compares a presented token with the stored one.
func ConstantTimeCSRF(stored, presented string) bool {
	return subtle.ConstantTimeCompare([]byte(stored), []byte(presented)) == 1
}
