package auth

import (
	"context"
	"net/http"
	"strconv"
	"sync"
	"testing"
	"time"

	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
)

func resetTestAuth(id string, now time.Time, delay time.Duration) *Auth {
	return &Auth{ID: id, Quota: QuotaState{ObservedAt: now, Signals: map[string]string{
		"Anthropic-Ratelimit-Unified-7d-Reset": strconv.FormatInt(now.Add(delay).Unix(), 10),
	}}}
}

func TestSoonestResetPrefersWeeklyWindowAndHonorsAvailability(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	selector := &SoonestResetSelector{nowFunc: func() time.Time { return now }}
	soon := resetTestAuth("soon", now, 24*time.Hour)
	later := resetTestAuth("later", now, 4*24*time.Hour)
	later.Quota.Signals["Anthropic-Ratelimit-Unified-5h-Reset"] = strconv.FormatInt(now.Add(time.Minute).Unix(), 10)
	disabled := resetTestAuth("disabled", now, time.Hour)
	disabled.Disabled = true
	cooling := resetTestAuth("cooling", now, 2*time.Hour)
	cooling.Quota.Exceeded, cooling.Quota.Reason, cooling.Quota.NextRecoverAt = true, "credential_quota", now.Add(time.Hour)
	for range 5 {
		got, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, []*Auth{later, disabled, cooling, soon})
		if errPick != nil || got.ID != "soon" {
			t.Fatalf("Pick() = %v, %v; want soon", got, errPick)
		}
	}
	soon.Disabled = true
	got, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, []*Auth{later, disabled, cooling, soon})
	if errPick != nil || got.ID != "later" {
		t.Fatalf("failover = %v, %v; want later", got, errPick)
	}
}

func TestSubscriptionResetParsesProviderSignalsAndRejectsStaleData(t *testing.T) {
	now := time.Date(2026, 10, 27, 12, 0, 0, 0, time.UTC)
	tests := []struct {
		name, provider string
		observed       time.Time
		signals        map[string]string
		want           time.Time
	}{
		{"claude RFC3339", "claude", now, map[string]string{"anthropic-ratelimit-unified-7d-reset": now.Add(time.Hour).Format(time.RFC3339)}, now.Add(time.Hour)},
		{"claude short fallback", "claude", now, map[string]string{"Anthropic-Ratelimit-Unified-5h-Reset": strconv.FormatInt(now.Add(time.Hour).Unix(), 10)}, now.Add(time.Hour)},
		{"codex weekly primary", "codex", now, map[string]string{
			"X-Codex-Primary-Window-Minutes": "10080", "X-Codex-Primary-Reset-After-Seconds": "86400",
			"X-Codex-Secondary-Window-Minutes": "300", "X-Codex-Secondary-Reset-After-Seconds": "60",
		}, now.Add(24 * time.Hour)},
		{"codex relative anchored to observation", "codex", now.Add(-5 * time.Minute), map[string]string{"X-Codex-Primary-Reset-After-Seconds": "600"}, now.Add(5 * time.Minute)},
		{"codex absolute wins", "codex", now, map[string]string{"X-Codex-Primary-Reset-At": strconv.FormatInt(now.Add(time.Hour).Unix(), 10), "X-Codex-Primary-Reset-After-Seconds": "600"}, now.Add(time.Hour)},
		{"expired", "claude", now, map[string]string{"Anthropic-Ratelimit-Unified-7d-Reset": strconv.FormatInt(now.Add(-time.Hour).Unix(), 10)}, time.Time{}},
		{"stale", "codex", now.Add(-16 * time.Minute), map[string]string{"X-Codex-Primary-Reset-After-Seconds": "86400"}, time.Time{}},
		{"missing observation", "codex", time.Time{}, map[string]string{"X-Codex-Primary-Reset-After-Seconds": "86400"}, time.Time{}},
		{"implausible", "codex", now, map[string]string{"X-Codex-Primary-Reset-After-Seconds": "999999999999999999"}, time.Time{}},
		{"future observation", "codex", now.Add(time.Hour), map[string]string{"X-Codex-Primary-Reset-After-Seconds": "600"}, time.Time{}},
		{"malformed", "claude", now, map[string]string{"Anthropic-Ratelimit-Unified-7d-Reset": "NaN"}, time.Time{}},
		{"unsupported provider", "gemini", now, map[string]string{"X-Codex-Primary-Reset-After-Seconds": "600"}, time.Time{}},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			got := subscriptionReset(QuotaState{ObservedAt: test.observed, Signals: test.signals}, test.provider, now)
			if !got.Equal(test.want) {
				t.Fatalf("reset = %v, want %v", got, test.want)
			}
		})
	}
}

func TestSoonestResetSamplesUnknownAccountsAndRotatesTies(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	selector := &SoonestResetSelector{nowFunc: func() time.Time { return now }}
	auths := []*Auth{resetTestAuth("known", now, time.Hour), {ID: "unknown-a"}, {ID: "unknown-b"}}
	for index, want := range []string{"unknown-a", "unknown-b", "known", "known"} {
		got, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, auths)
		if errPick != nil || got.ID != want {
			t.Fatalf("pick %d = %v, %v; want %s", index, got, errPick, want)
		}
	}
	auths[1] = resetTestAuth("unknown-a", now, time.Hour)
	auths[2] = resetTestAuth("unknown-b", now, time.Hour)
	seen := make(map[string]bool)
	for range 3 {
		got, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, auths)
		if errPick != nil {
			t.Fatal(errPick)
		}
		seen[got.ID] = true
	}
	if len(seen) != 3 {
		t.Fatalf("tied resets starved accounts: %v", seen)
	}
	// Expire the observation and sampling timestamps without sleeping.
	now = now.Add(16 * time.Minute)
	got, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, auths)
	if errPick != nil || got == nil {
		t.Fatalf("stale pool should fall back to round-robin: %v", errPick)
	}
}

func TestSoonestResetPreservesSessionsWhenQuotaOrderChanges(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	selector := NewSessionAffinitySelector(&SoonestResetSelector{})
	defer selector.Stop()
	auths := []*Auth{resetTestAuth("a", now, time.Hour), resetTestAuth("b", now, 24*time.Hour)}
	opts := cliproxyexecutor.Options{Headers: http.Header{"X-Claude-Code-Session-Id": {"sticky-session"}}}
	pick := func(want string) {
		t.Helper()
		got, errPick := selector.Pick(context.Background(), "claude", "model", opts, auths)
		if errPick != nil || got.ID != want {
			t.Fatalf("Pick() = %v, %v; want %s", got, errPick, want)
		}
	}
	pick("a")
	auths[1] = resetTestAuth("b", now, time.Minute)
	pick("a")
	opts.Headers.Set("X-Claude-Code-Session-Id", "new-session")
	pick("b")
	opts.Headers.Set("X-Claude-Code-Session-Id", "sticky-session")
	auths[0].Disabled = true
	pick("b")
}

func TestSoonestResetConcurrent(t *testing.T) {
	now := time.Now()
	selector := &SoonestResetSelector{}
	auths := []*Auth{resetTestAuth("a", now, time.Hour), resetTestAuth("b", now, time.Hour)}
	var wg sync.WaitGroup
	for range 32 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for range 20 {
				if _, errPick := selector.Pick(context.Background(), "claude", "model", cliproxyexecutor.Options{}, auths); errPick != nil {
					t.Error(errPick)
				}
			}
		}()
	}
	wg.Wait()
}
