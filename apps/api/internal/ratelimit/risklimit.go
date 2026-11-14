// Package ratelimit implements the two rate-limiting strategies:
// an IP risk score for unauthenticated routes (CGNAT-friendly: no hard
// permanent blocks, score decays over time) and a token bucket per
// authenticated identity.
package ratelimit

import (
	"math"
	"net"
	"sync"
	"time"
)

const (
	// BlockScore is the score at which an IP is rejected.
	BlockScore = 50.0
	// halfLife is the time for the risk score to halve.
	halfLife = 10 * time.Minute

	// Points per event type.
	PointsLoginFailure  = 10.0
	PointsRegister      = 2.0
	PointsValidationErr = 1.0
)

type riskEntry struct {
	score    float64
	lastSeen time.Time
}

// RiskLimiter tracks a decaying risk score per IP. In-memory, per instance.
type RiskLimiter struct {
	mu      sync.Mutex
	entries map[string]*riskEntry
}

// NewRiskLimiter creates an empty risk limiter.
func NewRiskLimiter() *RiskLimiter {
	return &RiskLimiter{entries: make(map[string]*riskEntry)}
}

// IP extracts the host part of an address, falling back to the raw value.
func IP(remoteAddr string) string {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		return remoteAddr
	}
	return host
}

func decay(score float64, elapsed time.Duration) float64 {
	if elapsed <= 0 {
		return score
	}
	return score * math.Pow(0.5, float64(elapsed)/float64(halfLife))
}

// Allow reports whether the IP may proceed, and the retry delay if not.
func (l *RiskLimiter) Allow(ip string) (bool, time.Duration) {
	l.mu.Lock()
	defer l.mu.Unlock()
	e, ok := l.entries[ip]
	if !ok {
		return true, 0
	}
	e.score = decay(e.score, time.Since(e.lastSeen))
	e.lastSeen = time.Now()
	// Epsilon: decay() may shave the score microscopically below the
	// threshold right after penalizing.
	if e.score >= BlockScore-1e-3 {
		// Time for the score to decay back below the block threshold.
		return false, time.Duration(math.Log2(e.score/BlockScore)*float64(halfLife)) + time.Second
	}
	return true, 0
}

// Penalize adds points to the IP's risk score.
func (l *RiskLimiter) Penalize(ip string, points float64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.sweepLocked()
	e, ok := l.entries[ip]
	if !ok {
		e = &riskEntry{}
		l.entries[ip] = e
	}
	e.score = decay(e.score, time.Since(e.lastSeen)) + points
	e.lastSeen = time.Now()
}

// Reset clears the risk score of an IP (e.g. after a successful login).
func (l *RiskLimiter) Reset(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.entries, ip)
}

// sweepLocked drops entries that decayed to noise when the map is large.
func (l *RiskLimiter) sweepLocked() {
	if len(l.entries) < 4096 {
		return
	}
	now := time.Now()
	for ip, e := range l.entries {
		if decay(e.score, now.Sub(e.lastSeen)) < 1 {
			delete(l.entries, ip)
		}
	}
}

// IdentityLimiter is a per-user token bucket (in-memory, per instance).
type IdentityLimiter struct {
	mu       sync.Mutex
	buckets  map[string]*bucket
	rate     float64 // tokens per second
	capacity float64
}

type bucket struct {
	tokens   float64
	lastSeen time.Time
}

// NewIdentityLimiter creates a limiter allowing capacity burst tokens and
// refill per minute.
func NewIdentityLimiter(perMinute int) *IdentityLimiter {
	return &IdentityLimiter{
		buckets:  make(map[string]*bucket),
		rate:     float64(perMinute) / 60.0,
		capacity: float64(perMinute),
	}
}

// Allow reports whether key may proceed and refills its bucket accordingly.
func (l *IdentityLimiter) Allow(key string) bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	b, ok := l.buckets[key]
	now := time.Now()
	if !ok {
		l.buckets[key] = &bucket{tokens: l.capacity - 1, lastSeen: now}
		return true
	}
	elapsed := now.Sub(b.lastSeen).Seconds()
	b.tokens = math.Min(l.capacity, b.tokens+elapsed*l.rate)
	b.lastSeen = now
	if b.tokens < 1 {
		return false
	}
	b.tokens--
	return true
}
