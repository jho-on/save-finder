package validation

import "testing"

func TestValidateEmail(t *testing.T) {
	valid := []string{"ana@example.com", "a.b+c_d@sub.domain.co"}
	for _, v := range valid {
		if err := ValidateEmail(NormalizeEmail(v)); err != nil {
			t.Errorf("expected %q to be valid: %v", v, err)
		}
	}
	invalid := []string{"", "not-an-email", "a@", "@b.com", "a b@c.com"}
	for _, v := range invalid {
		if err := ValidateEmail(v); err == nil {
			t.Errorf("expected %q to be invalid", v)
		}
	}
}

func TestNormalizeEmail(t *testing.T) {
	if got := NormalizeEmail("  Ana@Example.COM "); got != "ana@example.com" {
		t.Fatalf("got %q", got)
	}
}

func TestValidateUsername(t *testing.T) {
	valid := []string{"ana", "a_b-c.d", "0x7f", "abc123def456ghi789jkl012mno"}
	for _, v := range valid {
		if err := ValidateUsername(v); err != nil {
			t.Errorf("expected %q to be valid: %v", v, err)
		}
	}
	invalid := []string{"", "ab", "a" + string(make([]byte, 40)), "Ana", "ana!", "espaço x"}
	for _, v := range invalid {
		if err := ValidateUsername(v); err == nil {
			t.Errorf("expected %q to be invalid", v)
		}
	}
}

func TestValidatePassword(t *testing.T) {
	if err := ValidatePassword("1234567"); err == nil {
		t.Error("expected too-short password to fail")
	}
	if err := ValidatePassword("12345678"); err != nil {
		t.Errorf("expected 8-char password to pass: %v", err)
	}
	if err := ValidatePassword(string(make([]byte, 129))); err == nil {
		t.Error("expected too-long password to fail")
	}
}
