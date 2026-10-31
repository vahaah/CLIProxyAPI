package management

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/config"
	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
)

func TestStartOAuthV8RejectsUnsupportedTargetedRecovery(t *testing.T) {
	replaceOAuthSessionStoreForTest(t, newOAuthSessionStore(time.Minute))
	cfg := &config.Config{AuthDir: t.TempDir()}
	manager := coreauth.NewManager(nil, nil, nil)
	manager.SetConfig(cfg)
	auth, errRegister := manager.Register(context.Background(), &coreauth.Auth{
		ID: "claude-target.json", FileName: "claude-target.json", Provider: "claude",
	})
	if errRegister != nil {
		t.Fatal(errRegister)
	}
	handler := NewHandlerWithoutConfigFilePath(cfg, manager)
	router := gin.New()
	router.POST("/v8/management/oauth/auth-url", handler.StartOAuthV8)

	for _, test := range []struct {
		key, value string
	}{
		{key: "name", value: auth.FileName},
		{key: "auth_index", value: auth.Index},
	} {
		t.Run(test.key, func(t *testing.T) {
			bodyFields := map[string]any{"name": auth.FileName, "is_webui": false}
			bodyFields[test.key] = test.value
			payload, errMarshal := json.Marshal(bodyFields)
			if errMarshal != nil {
				t.Fatal(errMarshal)
			}
			request := httptest.NewRequest(http.MethodPost, "/v8/management/oauth/auth-url", bytes.NewReader(payload))
			request.Header.Set("Content-Type", "application/json")
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			var body struct {
				State string `json:"state"`
				Error string `json:"error"`
			}
			if errDecode := json.Unmarshal(response.Body.Bytes(), &body); errDecode != nil {
				t.Fatal(errDecode)
			}
			if body.State != "" {
				CancelOAuthSession(body.State)
			}
			if response.Code != http.StatusBadRequest || body.Error != "targeted recovery is only supported for codex" || body.State != "" {
				t.Fatalf("targeted Claude recovery returned status %d, error %q, started=%v; want rejection before login", response.Code, body.Error, body.State != "")
			}
		})
	}
}
