package helps

import "testing"

func TestAntigravityStreamUsagePrefersNestedSnapshotRegardlessOfKeyOrder(t *testing.T) {
	const top = `"usageMetadata":{"promptTokenCount":900,"candidatesTokenCount":99,"totalTokenCount":999}`
	const nested = `"response":{"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":4,"totalTokenCount":9}}`
	for _, test := range []struct {
		name, fields         string
		input, output, total int64
	}{
		{name: "top-level first", fields: top + "," + nested, input: 5, output: 4, total: 9},
		{name: "nested first", fields: nested + "," + top, input: 5, output: 4, total: 9},
		{name: "top-level fallback", fields: top, input: 900, output: 99, total: 999},
	} {
		t.Run(test.name, func(t *testing.T) {
			var buffer StreamUsageBuffer
			buffer.ObserveAntigravityStream([]byte("data: {" + test.fields + "}"))
			detail, ok := buffer.Detail()
			if !ok {
				t.Fatal("stream usage was not recorded")
			}
			if detail.InputTokens != test.input || detail.OutputTokens != test.output || detail.TotalTokens != test.total {
				t.Fatalf("stream usage = %d input, %d output, %d total; want %d, %d, %d", detail.InputTokens, detail.OutputTokens, detail.TotalTokens, test.input, test.output, test.total)
			}
		})
	}
}
