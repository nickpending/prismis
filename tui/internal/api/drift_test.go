package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func driftClient(t *testing.T, status int, body string) *APIClient {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/drift" || r.Header.Get("X-API-Key") != "k" {
			t.Errorf("unexpected request %s key %q", r.URL.Path, r.Header.Get("X-API-Key"))
		}
		w.WriteHeader(status)
		_, _ = w.Write([]byte(body))
	}))
	t.Cleanup(server.Close)
	return &APIClient{baseURL: server.URL, apiKey: "k", httpClient: &http.Client{Timeout: 5 * time.Second}}
}

func TestGetDriftNullDataIsNilCheckAndNoError(t *testing.T) {
	check, err := driftClient(t, 200, `{"success":true,"message":"No drift check has run yet","data":null}`).GetDrift()
	if err != nil {
		t.Fatalf("null data must not be an error: %v", err)
	}
	if check != nil {
		t.Fatalf("null data must be a nil check, got %+v", check)
	}
}

func TestGetDriftDecodesAFlaggedCheck(t *testing.T) {
	body := `{"success":true,"message":"ok","data":{"checked_at":"2026-10-16T00:00:00+00:00","window_items":40,"window_null":25,"share":0.62,"baseline":0.54,"baseline_windows":7,"deviation":0.08,"threshold":0.2,"flagged":true,"reason":null}}`
	check, err := driftClient(t, 200, body).GetDrift()
	if err != nil {
		t.Fatalf("GetDrift: %v", err)
	}
	if check == nil || !check.Flagged {
		t.Fatalf("want a flagged check, got %+v", check)
	}
	if check.Share == nil || *check.Share != 0.62 || check.Baseline == nil || *check.Baseline != 0.54 {
		t.Errorf("share/baseline not decoded: %v / %v", check.Share, check.Baseline)
	}

	thin := `{"success":true,"message":"ok","data":{"checked_at":"x","share":null,"baseline":null,"flagged":true,"reason":"thin"}}`
	check, err = driftClient(t, 200, thin).GetDrift()
	if err != nil || check == nil || !check.Flagged || check.Share != nil || check.Baseline != nil {
		t.Fatalf("thin-window check mis-decoded: %+v, %v", check, err)
	}
}

func TestGetDriftStatusErrors(t *testing.T) {
	check, err := driftClient(t, 403, `{"detail":"Invalid API key"}`).GetDrift()
	if err == nil || !strings.Contains(err.Error(), "authentication failed") {
		t.Errorf("403 must return the auth-failed error, got %v", err)
	}
	if check != nil {
		t.Errorf("403 must come with a nil check, got %+v", check)
	}

	check, err = driftClient(t, 500, `{"detail":"boom"}`).GetDrift()
	if err == nil {
		t.Error("500 must return an error")
	} else if strings.Contains(err.Error(), "authentication failed") {
		t.Errorf("500 must not read as an auth failure: %v", err)
	}
	if check != nil {
		t.Errorf("500 must come with a nil check, got %+v", check)
	}
}
