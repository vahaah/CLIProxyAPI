package cliproxy

import (
	"context"
	"io"
	"net"
	"net/http"
	"runtime"
	"sync"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/api"
	"github.com/router-for-me/CLIProxyAPI/v8/internal/config"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executionregistry"
)

func TestServiceShutdownRetainsHomeResourcesDuringHTTPDrain(t *testing.T) {
	for _, tc := range []struct {
		name     string
		budget   int
		complete bool
	}{
		{"complete [control:shutdown-home]", 5, true},
		{"immediate", 0, false},
		{"budget exhausted", 1, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(t.Context(), 10*time.Second)
			defer cancel()
			probe, errListen := net.Listen("tcp", "127.0.0.1:0")
			if errListen != nil {
				t.Fatal(errListen)
			}
			address := probe.Addr().String()
			port := probe.Addr().(*net.TCPAddr).Port
			if errClose := probe.Close(); errClose != nil {
				t.Fatal(errClose)
			}
			upstream, cancelUpstream := context.WithCancel(context.Background())
			defer cancelUpstream()
			registry := executionregistry.New()
			pending, errBegin := registry.BeginDispatch()
			if errBegin != nil {
				t.Fatal(errBegin)
			}
			scope, errInstall := registry.Install(pending, executionregistry.ScopeSpec{RequestID: "shutdown-test"})
			if errInstall != nil {
				t.Fatal(errInstall)
			}
			if errBind := scope.Bind(func() error { cancelUpstream(); return nil }); errBind != nil {
				t.Fatal(errBind)
			}
			release, started := make(chan struct{}), make(chan struct{})
			var releaseOnce sync.Once
			releaseHandler := func() { releaseOnce.Do(func() { close(release) }) }
			cfg := &config.Config{Host: "127.0.0.1", Port: port, ShutdownTimeoutSeconds: tc.budget}
			cfg.Home.Enabled = true
			server := api.NewServer(cfg, nil, nil, "", api.WithRouterConfigurator(func(engine *gin.Engine, _ *handlers.BaseAPIHandler, _ *config.Config) {
				engine.GET("/v8/management/drain-test", func(c *gin.Context) {
					close(started)
					select {
					case <-release:
					case <-upstream.Done():
					case <-c.Request.Context().Done():
					}
					if upstream.Err() != nil {
						c.String(http.StatusServiceUnavailable, "Home resources canceled")
						return
					}
					c.String(http.StatusOK, "completed")
				})
			}))
			service := &Service{cfg: cfg, server: server, homeRegistry: registry}
			serveDone := make(chan error, 1)
			go func() { serveDone <- server.Start() }()
			t.Cleanup(func() {
				releaseHandler()
				if errShutdown := service.Shutdown(context.Background()); errShutdown != nil {
					t.Error(errShutdown)
				}
				<-serveDone
			})
			waitForListener := func(open bool) {
				for ctx.Err() == nil {
					conn, errDial := (&net.Dialer{}).DialContext(ctx, "tcp", address)
					if conn != nil {
						if errClose := conn.Close(); errClose != nil {
							t.Fatal(errClose)
						}
					}
					if (errDial == nil) == open {
						return
					}
					runtime.Gosched()
				}
				t.Fatal("listener did not reach the expected state")
			}
			waitForListener(true)
			type result struct {
				status int
				body   string
				err    error
			}
			requestDone := make(chan result, 1)
			go func() {
				request, errRequest := http.NewRequestWithContext(ctx, http.MethodGet, "http://"+address+"/v8/management/drain-test", nil)
				if errRequest != nil {
					requestDone <- result{err: errRequest}
					return
				}
				response, errGet := http.DefaultClient.Do(request)
				if errGet != nil {
					requestDone <- result{err: errGet}
					return
				}
				body, errRead := io.ReadAll(response.Body)
				if errClose := response.Body.Close(); errRead == nil {
					errRead = errClose
				}
				requestDone <- result{status: response.StatusCode, body: string(body), err: errRead}
			}()
			select {
			case <-started:
			case <-ctx.Done():
				t.Fatal("request did not start")
			}
			shutdownDone := make(chan error, 1)
			go func() { shutdownDone <- service.Shutdown(ctx) }()
			waitForListener(false)
			if tc.complete {
				releaseHandler()
			}
			select {
			case got := <-requestDone:
				if tc.complete && (got.err != nil || got.status != http.StatusOK || got.body != "completed") {
					t.Fatalf("drained response = %+v, want HTTP 200 completed", got)
				}
				if !tc.complete && got.err == nil {
					t.Fatalf("interrupted response = %+v, want a closed connection", got)
				}
			case <-ctx.Done():
				t.Fatal("request did not finish")
			}
			select {
			case errShutdown := <-shutdownDone:
				if errShutdown != nil {
					t.Fatal(errShutdown)
				}
			case <-ctx.Done():
				t.Fatal("shutdown did not finish")
			}
			if upstream.Err() != context.Canceled {
				t.Fatal("shutdown did not close the Home execution resource")
			}
		})
	}
}
