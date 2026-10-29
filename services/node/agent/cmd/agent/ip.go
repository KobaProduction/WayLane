package main

import (
    "context"
    "io"
    "net"
    "net/http"
    "net/url"
    "strings"
    "time"
)

// The interface scan by itself misses the public egress IP behind NAT and
// Docker Desktop. The HTTPS probes are advisory metadata, NEVER an authority
// for inbound reachability, node identity or subscription route rewrites.
func probePublicIP(ctx context.Context, client *http.Client, endpoint string, wantIPv6 bool) string {
    parsed, err := url.Parse(endpoint)
    if err != nil || parsed.Scheme != "https" || parsed.User != nil {
        return ""
    }
    timeoutCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
    defer cancel()
    req, err := http.NewRequestWithContext(timeoutCtx, http.MethodGet, endpoint, nil)
    if err != nil {
        return ""
    }
    resp, err := client.Do(req)
    if err != nil {
        return ""
    }
    defer resp.Body.Close()
    if resp.StatusCode != http.StatusOK || resp.Request.URL.Hostname() != parsed.Hostname() {
        return ""
    }
    data, err := io.ReadAll(io.LimitReader(resp.Body, 65))
    if err != nil || len(data) > 64 {
        return ""
    }
    ip := net.ParseIP(strings.TrimSpace(string(data)))
    if ip == nil || !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() ||
        ip.IsLinkLocalUnicast() {
        return ""
    }
    if wantIPv6 && ip.To4() != nil || !wantIPv6 && ip.To4() == nil {
        return ""
    }
    return ip.String()
}

func reportPublicIP(ctx context.Context, client *http.Client) (string, string) {
    ipv4, ipv6 := reportIP()
    // An external service sees NAT egress instead of the container interface.
    // On lookup failure fall back to globally routable interface observations;
    // the backend retains the last valid value if both methods fail.
    if observed := probePublicIP(ctx, client, "https://api.ipify.org", false); observed != "" {
        ipv4 = observed
    }
    if observed := probePublicIP(ctx, client, "https://api6.ipify.org", true); observed != "" {
        ipv6 = observed
    }
    return ipv4, ipv6
}
