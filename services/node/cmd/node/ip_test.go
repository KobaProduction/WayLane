package main

import (
    "context"
    "fmt"
    "net/http"
    "net/http/httptest"
    "testing"
)

func TestPublicIPHTTPSValidation(t *testing.T) {
    output := "8.8.4.4"
    srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
        fmt.Fprint(w, output)
    }))
    defer srv.Close()
    c := srv.Client()
    if ip := probePublicIP(context.Background(), c, srv.URL, false); ip != "8.8.4.4" {
        t.Fatalf("public IPv4 was not detected: %q", ip)
    }
    for _, invalid := range []string{"127.0.0.1", "192.168.1.11", "not an ip", "2001:db8::1"} {
        output = invalid
        if ip := probePublicIP(context.Background(), c, srv.URL, false); ip != "" {
            t.Errorf("invalid public IP %q accepted as %q", invalid, ip)
        }
    }
    output = "2606:4700:4700::1111"
    if ip := probePublicIP(context.Background(), c, srv.URL, true); ip != "2606:4700:4700::1111" {
        t.Errorf("public IPv6 not detected: %q", ip)
    }
    if ip := probePublicIP(context.Background(), c, "http://127.0.0.1", false); ip != "" {
        t.Fatalf("insecure public IP probe accepted: %q", ip)
    }
}
