package auth

import (
	"context"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
)

const resetObservationMaxAge = 15 * time.Minute

// SoonestResetSelector spends the subscription window that expires first.
// SessionAffinitySelector remains responsible for keeping established sessions
// on their original credential. Unknown or stale quotas receive an occasional
// cold session so one known account cannot starve the rest of the pool.
type SoonestResetSelector struct {
	mu         sync.Mutex
	sampled    map[string]time.Time
	roundRobin RoundRobinSelector
	nowFunc    func() time.Time
}

func (s *SoonestResetSelector) Pick(ctx context.Context, provider, model string, opts cliproxyexecutor.Options, auths []*Auth) (*Auth, error) {
	now := time.Now()
	if s.nowFunc != nil {
		now = s.nowFunc()
	}
	available, errAvailable := getSelectorAvailableAuths(ctx, auths, provider, model, now)
	if errAvailable != nil {
		return nil, errAvailable
	}
	available = preferCodexWebsocketAuths(ctx, provider, available)
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.sampled == nil || len(s.sampled) > 4096 {
		s.sampled = make(map[string]time.Time)
	}
	var unknown, earliest []*Auth
	var reset time.Time
	for _, candidate := range available {
		deadline := subscriptionReset(candidate.Quota, provider, now)
		if deadline.IsZero() {
			if last := s.sampled[provider+"::"+candidate.ID]; last.IsZero() || now.Sub(last) >= resetObservationMaxAge {
				unknown = append(unknown, candidate)
			}
			continue
		}
		if reset.IsZero() || deadline.Before(reset) {
			reset, earliest = deadline, []*Auth{candidate}
		} else if deadline.Equal(reset) {
			earliest = append(earliest, candidate)
		}
	}
	candidates := available
	if len(unknown) > 0 {
		candidates = unknown
	} else if len(earliest) > 0 {
		candidates = earliest
	}
	picked, errPick := s.roundRobin.Pick(ctx, provider, model, opts, candidates)
	if errPick == nil {
		s.sampled[provider+"::"+picked.ID] = now
	}
	return picked, errPick
}

// Prefer the weekly window, then a shorter window when no weekly reset is
// supplied. Relative reset times are anchored to the observation, never now.
// Malformed, expired, implausible, or stale watermarks do not affect ordering.
func subscriptionReset(quota QuotaState, provider string, now time.Time) time.Time {
	if quota.ObservedAt.IsZero() || quota.ObservedAt.After(now.Add(time.Minute)) || now.Sub(quota.ObservedAt) > resetObservationMaxAge {
		return time.Time{}
	}
	headers := make(http.Header, len(quota.Signals))
	for name, value := range quota.Signals {
		headers.Set(name, value)
	}
	valid := func(deadline time.Time) time.Time {
		if deadline.After(now) && !deadline.After(now.Add(8*24*time.Hour)) {
			return deadline
		}
		return time.Time{}
	}
	parseAbsolute := func(raw string) time.Time {
		if seconds, errParse := strconv.ParseInt(strings.TrimSpace(raw), 10, 64); errParse == nil && seconds > 0 {
			return valid(time.Unix(seconds, 0))
		}
		deadline, _ := time.Parse(time.RFC3339, strings.TrimSpace(raw))
		return valid(deadline)
	}
	switch strings.ToLower(strings.TrimSpace(provider)) {
	case "claude":
		for _, window := range []string{"7d-", "5h-", ""} {
			if deadline := parseAbsolute(headers.Get("Anthropic-Ratelimit-Unified-" + window + "Reset")); !deadline.IsZero() {
				return deadline
			}
		}
	case "codex":
		var weekly, shorter time.Time
		for _, window := range []string{"Primary", "Secondary"} {
			prefix := "X-Codex-" + window + "-"
			deadline := parseAbsolute(headers.Get(prefix + "Reset-At"))
			if deadline.IsZero() {
				if seconds, errParse := strconv.ParseInt(headers.Get(prefix+"Reset-After-Seconds"), 10, 64); errParse == nil && seconds > 0 && seconds <= 8*24*60*60 {
					deadline = valid(quota.ObservedAt.Add(time.Duration(seconds) * time.Second))
				}
			}
			if deadline.IsZero() {
				continue
			}
			minutes, _ := strconv.ParseInt(headers.Get(prefix+"Window-Minutes"), 10, 64)
			target := &shorter
			if minutes >= 7*24*60 {
				target = &weekly
			}
			if target.IsZero() || deadline.Before(*target) {
				*target = deadline
			}
		}
		if !weekly.IsZero() {
			return weekly
		}
		return shorter
	}
	return time.Time{}
}
