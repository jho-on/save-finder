package auth

import (
	"strings"
	"testing"
)

func TestHashPasswordRoundTrip(t *testing.T) {
	h1, err := HashPassword("correct horse battery staple")
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	if !strings.HasPrefix(h1, "$argon2id$v=19$m=19456,t=2,p=1$") {
		t.Fatalf("unexpected parameters in hash: %q", h1)
	}
	h2, err := HashPassword("correct horse battery staple")
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	if h1 == h2 {
		t.Fatal("expected unique salt per password")
	}
}

func TestVerifyPassword(t *testing.T) {
	h, err := HashPassword("s3cret-pw")
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	ok, err := VerifyPassword("s3cret-pw", h)
	if err != nil || !ok {
		t.Fatalf("expected valid password to verify (ok=%v err=%v)", ok, err)
	}
	ok, err = VerifyPassword("wrong", h)
	if err != nil || ok {
		t.Fatalf("expected wrong password to be rejected (ok=%v err=%v)", ok, err)
	}
}

func TestVerifyPasswordMalformed(t *testing.T) {
	if _, err := VerifyPassword("x", "not-a-hash"); err == nil {
		t.Fatal("expected error for malformed hash")
	}
}
