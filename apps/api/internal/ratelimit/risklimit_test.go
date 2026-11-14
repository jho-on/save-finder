package ratelimit

import "testing"

func TestRiskLimiterBlocksAfterThreshold(t *testing.T) {
	l := NewRiskLimiter()
	ip := "203.0.113.10"

	if ok, _ := l.Allow(ip); !ok {
		t.Fatal("new IP should be allowed")
	}
	for i := 0; i < 5; i++ {
		l.Penalize(ip, PointsLoginFailure)
	}
	if ok, retry := l.Allow(ip); ok {
		t.Fatal("expected IP to be blocked")
	} else if retry <= 0 {
		t.Fatal("expected a positive retry delay")
	}
}

func TestRiskLimiterResetOnSuccess(t *testing.T) {
	l := NewRiskLimiter()
	ip := "203.0.113.11"
	for i := 0; i < 5; i++ {
		l.Penalize(ip, PointsLoginFailure)
	}
	l.Reset(ip)
	if ok, _ := l.Allow(ip); !ok {
		t.Fatal("expected IP to be allowed after reset")
	}
}

func TestIdentityLimiter(t *testing.T) {
	l := NewIdentityLimiter(2) // 2 per minute
	key := "user-1"
	if !l.Allow(key) || !l.Allow(key) {
		t.Fatal("first two requests should be allowed")
	}
	if l.Allow(key) {
		t.Fatal("third request should be rejected")
	}
}
