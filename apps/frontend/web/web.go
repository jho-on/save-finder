// Package web exposes the embedded static assets of the remote web
// interface so cmd/web can wire them into the HTTP server.
package web

import "embed"

// StaticFS holds the static assets served at /static/.
//
//go:embed static
var StaticFS embed.FS
