package executor

import (
	"testing"

	"github.com/router-for-me/CLIProxyAPI/v8/internal/runtime/executor/helps"
)

func TestAntigravityAssemblyUsesLatestUsageSnapshot(t *testing.T) {
	for _, test := range []struct {
		name   string
		chunks []string
	}{
		{
			name: "top-level snapshots",
			chunks: []string{
				`{"response":{"candidates":[]},"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":1,"totalTokenCount":11}}`,
				`{"response":{"candidates":[{"finishReason":"STOP"}]},"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":4,"totalTokenCount":14}}`,
			},
		},
		{
			name: "nested then top-level",
			chunks: []string{
				`{"response":{"candidates":[],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":1,"totalTokenCount":11}}}`,
				`{"response":{"candidates":[{"finishReason":"STOP"}]},"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":4,"totalTokenCount":14}}`,
			},
		},
		{
			name: "nested wins within the current chunk",
			chunks: []string{
				`{"response":{"candidates":[],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":1,"totalTokenCount":11}}}`,
				`{"response":{"candidates":[{"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":4,"totalTokenCount":14}},"usageMetadata":{"totalTokenCount":999}}`,
			},
		},
		{
			name: "cpa snapshots",
			chunks: []string{
				`{"response":{"candidates":[],"cpaUsageMetadata":{"promptTokenCount":10,"candidatesTokenCount":1,"totalTokenCount":11}}}`,
				`{"response":{"candidates":[{"finishReason":"STOP"}],"cpaUsageMetadata":{"promptTokenCount":10,"candidatesTokenCount":4,"totalTokenCount":14}}}`,
			},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			chunks := make([][]byte, len(test.chunks))
			for index, chunk := range test.chunks {
				chunks[index] = []byte(chunk)
			}
			executor := &AntigravityExecutor{}
			assembled := executor.convertStreamChunksToNonStream(chunks)
			usage := helps.ParseAntigravityUsage(assembled)
			if usage.InputTokens != 10 || usage.OutputTokens != 4 || usage.TotalTokens != 14 {
				t.Fatalf("assembled usage = %d input, %d output, %d total; want 10, 4, 14: %s", usage.InputTokens, usage.OutputTokens, usage.TotalTokens, assembled)
			}
		})
	}
}
