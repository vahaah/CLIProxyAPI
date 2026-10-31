package auth

import (
	"context"
	"fmt"
	"testing"

	"github.com/router-for-me/CLIProxyAPI/v8/internal/registry"
	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"
	"github.com/tidwall/gjson"
)

func TestVerifyInferenceAllowsAHealthyMultiTokenResponse(t *testing.T) {
	ctx := context.Background()
	manager := NewManager(nil, nil, nil)
	auth, errRegister := manager.Register(ctx, &Auth{
		ID: "verification-budget", Provider: "claude", Status: StatusActive,
	})
	if errRegister != nil {
		t.Fatal(errRegister)
	}
	manager.RegisterExecutor(&customStreamMockExecutor{
		streamFn: func(_ context.Context, _ *Auth, request cliproxyexecutor.Request, _ cliproxyexecutor.Options) (*cliproxyexecutor.StreamResult, error) {
			budget := gjson.GetBytes(request.Payload, "max_output_tokens").Int()
			if budget <= 0 || budget > 128 {
				t.Fatalf("verification token budget = %d, want a bounded probe of at most 128 tokens", budget)
			}
			payload := `data: {"type":"response.completed","response":{"status":"completed","output":[{"type":"message","role":"assistant","content":[{"type":"output_text","text":"OK ready"}]}]}}` + "\n\n"
			if budget < 4 {
				payload = `data: {"type":"response.incomplete","response":{"status":"incomplete","incomplete_details":{"reason":"max_output_tokens"}}}` + "\n\n"
			}
			chunks := make(chan cliproxyexecutor.StreamChunk, 1)
			chunks <- cliproxyexecutor.StreamChunk{Payload: []byte(payload)}
			close(chunks)
			return &cliproxyexecutor.StreamResult{Chunks: chunks}, nil
		},
	})

	verified, errVerify := manager.VerifyInference(ctx, auth.ID, "claude-test")
	if errVerify != nil {
		t.Fatalf("healthy credential verification failed: %v", errVerify)
	}
	if verified.ID != auth.ID || !verified.InferenceVerified {
		t.Fatalf("verification result = %#v, want the original credential marked verified", verified)
	}
}

func TestExecuteVerifiesOnlyCompletedClaudeMessages(t *testing.T) {
	for _, test := range []struct {
		stopReason string
		verified   bool
	}{
		{stopReason: "max_tokens", verified: false},
		{stopReason: "end_turn", verified: true},
		{stopReason: "tool_use", verified: true},
		{stopReason: "unknown_stop", verified: false},
	} {
		t.Run(test.stopReason, func(t *testing.T) {
			ctx := context.Background()
			manager := NewManager(nil, nil, nil)
			auth, errRegister := manager.Register(ctx, &Auth{
				ID: t.Name(), Provider: "claude", Status: StatusActive,
			})
			if errRegister != nil {
				t.Fatal(errRegister)
			}
			const model = "claude-completion"
			models := registry.GetGlobalRegistry()
			models.RegisterClient(auth.ID, "claude", []*registry.ModelInfo{{ID: model}})
			t.Cleanup(func() { models.UnregisterClient(auth.ID) })
			payload := fmt.Sprintf(`{"id":"msg-test","type":"message","role":"assistant","content":[{"type":"text","text":"OK"}],"stop_reason":%q}`, test.stopReason)
			manager.RegisterExecutor(&mockCustomErrorExecutor{
				identifier: "claude",
				executeFn: func(context.Context, *Auth, cliproxyexecutor.Request, cliproxyexecutor.Options) (cliproxyexecutor.Response, error) {
					return cliproxyexecutor.Response{Payload: []byte(payload)}, nil
				},
			})

			response, errExecute := manager.Execute(ctx, []string{"claude"}, cliproxyexecutor.Request{Model: model}, cliproxyexecutor.Options{})
			if errExecute != nil {
				t.Fatal(errExecute)
			}
			if string(response.Payload) != payload {
				t.Fatalf("response = %s, want %s", response.Payload, payload)
			}
			updated, ok := manager.GetByID(auth.ID)
			if !ok {
				t.Fatal("credential is missing after execution")
			}
			if updated.InferenceVerified != test.verified {
				t.Fatalf("InferenceVerified = %v, want %v", updated.InferenceVerified, test.verified)
			}
		})
	}
}

func TestVerifyInferenceUsesCredentialExecutorKey(t *testing.T) {
	for _, registerRawProvider := range []bool{false, true} {
		t.Run(fmt.Sprintf("raw_provider_registered=%t", registerRawProvider), func(t *testing.T) {
			ctx := context.Background()
			manager := NewManager(nil, nil, nil)
			auth, errRegister := manager.Register(ctx, &Auth{
				ID: "compat-verification", Provider: "plugin-provider", Status: StatusActive,
				Attributes: map[string]string{"compat_name": "custom", "provider_key": "custom"},
			})
			if errRegister != nil {
				t.Fatal(errRegister)
			}
			mapped := &unauthorizedRefreshExecutor{id: "openai-compatible-custom"}
			raw := &unauthorizedRefreshExecutor{id: "plugin-provider"}
			manager.RegisterExecutor(mapped)
			if registerRawProvider {
				manager.RegisterExecutor(raw)
			}

			verified, errVerify := manager.VerifyInference(ctx, auth.ID, "custom-model")
			if errVerify != nil {
				t.Fatalf("mapped credential verification failed: %v", errVerify)
			}
			mappedCalls := mapped.StreamCalls()
			rawCalls := raw.StreamCalls()
			if len(mappedCalls) != 1 || mappedCalls[0] != auth.ID || len(rawCalls) != 0 {
				t.Fatalf("executor calls = mapped %v, raw %v; want only the mapped executor for %s", mappedCalls, rawCalls, auth.ID)
			}
			if verified.ID != auth.ID || !verified.InferenceVerified {
				t.Fatalf("verification result = %#v, want the mapped credential marked verified", verified)
			}
		})
	}
}
