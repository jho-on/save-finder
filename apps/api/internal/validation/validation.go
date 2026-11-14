// Package validation implements input validation rules for auth and account data.
package validation

import (
	"errors"
	"net/mail"
	"regexp"
	"strings"
)

// MinPasswordLength is the OWASP-recommended minimum password length.
const MinPasswordLength = 8

// MaxPasswordLength caps hashing work and avoids denial of service.
const MaxPasswordLength = 128

var usernameRe = regexp.MustCompile(`^[a-z0-9_.-]{3,30}$`)

// NormalizeEmail lowercases and trims an email address.
func NormalizeEmail(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

// NormalizeIdentifier lowercases and trims a login identifier.
func NormalizeIdentifier(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

// NormalizeUsername lowercases and trims a username.
func NormalizeUsername(s string) string {
	return strings.ToLower(strings.TrimSpace(s))
}

// ValidateEmail checks format after normalization.
func ValidateEmail(s string) error {
	if len(s) > 254 {
		return errors.New("email is too long")
	}
	addr, err := mail.ParseAddress(s)
	if err != nil || addr.Address != s {
		return errors.New("invalid email format")
	}
	return nil
}

// ValidateUsername checks the 3-30 char [a-z0-9_.-] rule.
func ValidateUsername(s string) error {
	if !usernameRe.MatchString(s) {
		return errors.New("username must be 3-30 characters of a-z, 0-9, '_', '.', '-'")
	}
	return nil
}

// ValidatePassword checks the password policy (OWASP-aligned: no composition rules).
func ValidatePassword(s string) error {
	if len(s) < MinPasswordLength {
		return errors.New("password must be at least 8 characters")
	}
	if len(s) > MaxPasswordLength {
		return errors.New("password must be at most 128 characters")
	}
	return nil
}
