package password

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"errors"
	"fmt"
	"strings"

	"golang.org/x/crypto/argon2"
)

// Argon2id parameters, following the OWASP recommendation:
// 19 MiB memory, 2 iterations, parallelism 1, individual salt per password.
const (
	argonMemory     uint32 = 19 * 1024 // 19 MiB
	argonTime       uint32 = 2
	argonThreads    uint8  = 1
	argonKeyLength  uint32 = 32
	argonSaltLength        = 16
)

// ErrMalformedHash is returned when a stored hash cannot be parsed.
var ErrMalformedHash = errors.New("malformed argon2id hash")

// HashPassword derives an Argon2id hash of the password, encoded in the
// standard $argon2id$v=19$m=...,t=...,p=...$<salt>$<key> format.
func HashPassword(pw string) (string, error) {
	salt := make([]byte, argonSaltLength)
	if _, err := rand.Read(salt); err != nil {
		return "", fmt.Errorf("generate salt: %w", err)
	}
	key := argon2.IDKey([]byte(pw), salt, argonTime, argonMemory, argonThreads, argonKeyLength)
	return fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
		argon2.Version, argonMemory, argonTime, argonThreads,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key)), nil
}

// VerifyPassword reports whether pw matches the encoded Argon2id hash.
func VerifyPassword(pw, encoded string) (bool, error) {
	parts := strings.Split(encoded, "$")
	if len(parts) != 6 || parts[1] != "argon2id" {
		return false, ErrMalformedHash
	}
	var version int
	if _, err := fmt.Sscanf(parts[2], "v=%d", &version); err != nil {
		return false, ErrMalformedHash
	}
	var m, t uint32
	var p uint8
	if _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &m, &t, &p); err != nil {
		return false, ErrMalformedHash
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[4])
	if err != nil {
		return false, ErrMalformedHash
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[5])
	if err != nil {
		return false, ErrMalformedHash
	}
	got := argon2.IDKey([]byte(pw), salt, t, m, p, uint32(len(want)))
	return subtle.ConstantTimeCompare(got, want) == 1, nil
}

// DummyHash computes a valid Argon2id hash of an arbitrary password.
// Used to equalize response time between unknown users and wrong passwords.
func DummyHash() (string, error) {
	return HashPassword("dummy-password-for-timing-equalization")
}
