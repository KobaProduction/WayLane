package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestDecodeXrayTrafficStatsStrictUUIDAndCumulativeCounters(t *testing.T) {
	const uid = "11111111-1111-4111-8111-111111111111"
	raw := []byte(`{"stat":[{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":"20"},` +
		`{"name":"user>>>` + uid + `>>>traffic>>>downlink","value":"31"},` +
		`{"name":"user>>>other@example.com>>>traffic>>>uplink","value":"999"},` +
		`{"name":"inbound>>>qa>>>traffic>>>uplink","value":"42"}]}`)
	samples, err := parseXrayTrafficStats(raw)
	if err != nil || len(samples) != 1 || samples[0].SubscriptionUUID != uid || samples[0].Uplink != 20 || samples[0].Downlink != 31 {
		t.Fatalf("invalid canonical subscription stats: %v %+v", err, samples)
	}
	// A query may return only one direction before its counterpart is created.
	one, err := parseXrayTrafficStats([]byte(`{"stat":[{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":13}]}`))
	if err != nil || len(one) != 1 || one[0].Downlink != 0 {
		t.Fatalf("partial stats: %+v %v", one, err)
	}
}
func TestXrayTrafficStatsBoundsAndNoReset(t *testing.T) {
	const uid = "11111111-1111-4111-8111-111111111111"
	for _, raw := range []string{
		`{"stat":[{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":"-1"}]}`,
		`{"stat":[{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":"9007199254740992"}]}`,
		`{"stat":[{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":"1"},` +
			`{"name":"user>>>` + uid + `>>>traffic>>>uplink","value":"2"}]}`,
		`not-json`,
	} {
		if _, err := parseXrayTrafficStats([]byte(raw)); err == nil {
			t.Fatalf("accepted invalid stats %q", raw)
		}
	}
	huge, _ := json.Marshal(map[string]any{"stat": []any{map[string]string{"name": "user>>>" + uid + ">>>traffic>>>uplink", "value": strings.Repeat("9", maxXrayStatsOutput)}}})
	if _, err := parseXrayTrafficStats(huge); err == nil {
		t.Fatal("accepted unbounded stats")
	}
}

func TestReadOnlyTrafficHasExplicitOptInAndCapability(t *testing.T) {
	t.Setenv("WAYLANE_XRAY_STATS_READONLY", "")
	if xrayReadOnlyTrafficEnabled() {
		t.Fatal("traffic collection must default off")
	}
	t.Setenv("WAYLANE_XRAY_STATS_READONLY", "True")
	if xrayReadOnlyTrafficEnabled() {
		t.Fatal("only explicit true should enable sampling")
	}
	t.Setenv("WAYLANE_XRAY_STATS_READONLY", "true")
	if !xrayReadOnlyTrafficEnabled() {
		t.Fatal("explicit read-only opt-in ignored")
	}
	payload, err := controlTelemetryPayload(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	list, ok := payload["capabilities"].([]any)
	if !ok {
		t.Fatal("telemetry capabilities missing")
	}
	found := false
	for _, value := range list {
		if value == "xray.stats.readonly" {
			found = true
		}
	}
	if !found {
		t.Fatal("read-only traffic capability not advertised")
	}
}
