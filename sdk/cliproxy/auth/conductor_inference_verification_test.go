package auth

import (
	"context"
	"testing"

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
