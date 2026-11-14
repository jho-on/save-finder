//go:build tools

// Package tools pins build-time tool dependencies so `go mod tidy` keeps them.
// templ generates type-safe Go code from .templ templates; run it with:
//
//	go run github.com/a-h/templ/cmd/templ generate
package tools

import (
	_ "github.com/a-h/templ"
	_ "github.com/a-h/templ/cmd/templ"
)
