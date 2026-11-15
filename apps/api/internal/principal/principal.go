// Package principal carries the authenticated identity (user + session)
// through the request context. Shared by all handlers that need it.
package principal

import (
	"context"

	"save-finder/api/internal/sessions"
	"save-finder/api/internal/users"
)

type contextKey struct{}

// Principal is the authenticated user together with its session.
type Principal struct {
	User    users.User
	Session sessions.Session
}

// With stores the principal in the context.
func With(ctx context.Context, p Principal) context.Context {
	return context.WithValue(ctx, contextKey{}, p)
}

// From extracts the principal, if any.
func From(ctx context.Context) (Principal, bool) {
	p, ok := ctx.Value(contextKey{}).(Principal)
	return p, ok
}
