// Package cryptometa validates the opaque key metadata blobs produced by
// the client. Kept separate so both the account password change flow
// (internal/auth) and the setup flow (internal/keys) share the exact
// same validation.
package cryptometa

import (
	"encoding/base64"
	"fmt"
)

// Bounds validated on write. Generous but prevents abuse and garbage.
const (
	SaltMinLen    = 16
	SaltMaxLen    = 64
	NonceLen      = 24 // XChaCha20
	WrappedDekMin = 16
	WrappedDekMax = 512
	OpsLimitMin   = 1
	OpsLimitMax   = 64
	MemLimitMin   = 8 * 1024 * 1024    // 8 MiB
	MemLimitMax   = 1024 * 1024 * 1024 // 1 GiB
)

// Params are validated, decoded key metadata fields.
type Params struct {
	Salt        []byte
	WrappedDEK  []byte
	DEKNonce    []byte
	KDFOpsLimit int64
	KDFMemLimit int64
}

// Validate decodes and validates base64 fields and KDF limits.
func Validate(saltB64, wrappedB64, nonceB64 string, opsLimit, memLimit int64) (Params, error) {
	var p Params
	var err error
	if p.Salt, err = decode("salt", saltB64, SaltMinLen, SaltMaxLen); err != nil {
		return Params{}, err
	}
	if p.WrappedDEK, err = decode("wrapped_dek", wrappedB64, WrappedDekMin, WrappedDekMax); err != nil {
		return Params{}, err
	}
	if p.DEKNonce, err = decode("dek_nonce", nonceB64, NonceLen, NonceLen); err != nil {
		return Params{}, err
	}
	if opsLimit < OpsLimitMin || opsLimit > OpsLimitMax {
		return Params{}, fmt.Errorf("kdf_ops_limit must be between %d and %d", OpsLimitMin, OpsLimitMax)
	}
	if memLimit < MemLimitMin || memLimit > MemLimitMax {
		return Params{}, fmt.Errorf("kdf_mem_limit must be between %d and %d bytes", MemLimitMin, MemLimitMax)
	}
	p.KDFOpsLimit = opsLimit
	p.KDFMemLimit = memLimit
	return p, nil
}

func decode(field, value string, minLen, maxLen int) ([]byte, error) {
	// Accept the most common base64 variants; clients may use URL-safe
	// (e.g. libsodium's default) or padded standard encoding.
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawStdEncoding, base64.URLEncoding, base64.RawURLEncoding} {
		if b, err := enc.DecodeString(value); err == nil {
			if len(b) < minLen || len(b) > maxLen {
				return nil, fmt.Errorf("%s must be between %d and %d bytes", field, minLen, maxLen)
			}
			return b, nil
		}
	}
	return nil, fmt.Errorf("%s is not valid base64", field)
}
