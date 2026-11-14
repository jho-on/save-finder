// Package users provides data access to the users table.
package users

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ErrEmailTaken is returned when the email is already registered.
var ErrEmailTaken = errors.New("email already registered")

// ErrUsernameTaken is returned when the username is already registered.
var ErrUsernameTaken = errors.New("username already registered")

// ErrNotFound is returned when no user matches.
var ErrNotFound = errors.New("user not found")

// User is a row in the users table.
type User struct {
	ID           uuid.UUID
	Email        string
	Username     string
	PasswordHash string
	CreatedAt    time.Time
	UpdatedAt    time.Time
}

// Store accesses the users table.
type Store struct {
	pool *pgxpool.Pool
}

// NewStore creates a users store.
func NewStore(pool *pgxpool.Pool) *Store { return &Store{pool: pool} }

const userColumns = `id, email, username, password_hash, created_at, updated_at`

func scanUser(row pgx.Row) (User, error) {
	var u User
	err := row.Scan(&u.ID, &u.Email, &u.Username, &u.PasswordHash, &u.CreatedAt, &u.UpdatedAt)
	return u, err
}

// Create inserts a new user. Email and username must already be normalized.
func (s *Store) Create(ctx context.Context, email, username, passwordHash string) (User, error) {
	row := s.pool.QueryRow(ctx,
		`INSERT INTO users (email, username, password_hash) VALUES ($1, $2, $3) RETURNING `+userColumns,
		email, username, passwordHash)
	u, err := scanUser(row)
	if err != nil {
		return User{}, mapWriteErr(err)
	}
	return u, nil
}

// GetByIdentifier finds a user by email or username (case-insensitive).
func (s *Store) GetByIdentifier(ctx context.Context, identifier string) (User, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT `+userColumns+` FROM users WHERE email = $1 OR username = $1`, identifier)
	u, err := scanUser(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return u, err
}

// GetByID finds a user by id.
func (s *Store) GetByID(ctx context.Context, id uuid.UUID) (User, error) {
	row := s.pool.QueryRow(ctx,
		`SELECT `+userColumns+` FROM users WHERE id = $1`, id)
	u, err := scanUser(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return u, err
}

// UpdateEmail changes the email, keeping uniqueness.
func (s *Store) UpdateEmail(ctx context.Context, id uuid.UUID, email string) (User, error) {
	return s.update(ctx, id, `email = $2`, email)
}

// UpdateUsername changes the username, keeping uniqueness.
func (s *Store) UpdateUsername(ctx context.Context, id uuid.UUID, username string) (User, error) {
	return s.update(ctx, id, `username = $2`, username)
}

func (s *Store) update(ctx context.Context, id uuid.UUID, setClause string, value string) (User, error) {
	row := s.pool.QueryRow(ctx,
		`UPDATE users SET `+setClause+`, updated_at = now() WHERE id = $1 RETURNING `+userColumns,
		id, value)
	u, err := scanUser(row)
	if errors.Is(err, pgx.ErrNoRows) {
		return User{}, ErrNotFound
	}
	return u, mapWriteErr(err)
}

// UpdatePasswordHash changes the password hash.
func (s *Store) UpdatePasswordHash(ctx context.Context, id uuid.UUID, passwordHash string) error {
	tag, err := s.pool.Exec(ctx,
		`UPDATE users SET password_hash = $2, updated_at = now() WHERE id = $1`, id, passwordHash)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

// Delete removes a user; sessions and future per-user data cascade.
func (s *Store) Delete(ctx context.Context, id uuid.UUID) error {
	tag, err := s.pool.Exec(ctx, `DELETE FROM users WHERE id = $1`, id)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func mapWriteErr(err error) error {
	var pgErr *pgconn.PgError
	if errors.As(err, &pgErr) && pgErr.Code == "23505" {
		switch pgErr.ConstraintName {
		case "users_email_key":
			return ErrEmailTaken
		case "users_username_key":
			return ErrUsernameTaken
		}
		return fmt.Errorf("unique violation on %q", pgErr.ConstraintName)
	}
	return err
}
