package cliproxy

import (
	"testing"

	internalconfig "github.com/router-for-me/CLIProxyAPI/v8/internal/config"
	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
)

func TestSoonestResetRoutingConfiguration(t *testing.T) {
	for _, affinity := range []bool{false, true} {
		state := normalizedRoutingRuntimeState(&internalconfig.Config{Routing: internalconfig.RoutingConfig{
			Strategy: " SOONEST-RESET-FIRST ", SessionAffinity: affinity,
		}})
		if state.strategy != "soonest-reset-first" {
			t.Fatalf("strategy = %q", state.strategy)
		}
		selector := newRoutingSelector(state)
		if affinity {
			sticky, ok := selector.(*coreauth.SessionAffinitySelector)
			if !ok {
				t.Fatalf("selector = %T; want session affinity", selector)
			}
			sticky.Stop()
		} else if _, ok := selector.(*coreauth.SoonestResetSelector); !ok {
			t.Fatalf("selector = %T; want soonest reset", selector)
		}
	}
}
