package main

import (
	"testing"
)

func TestExperimentalAWGIsOffUnlessExplicitLocalPilot(t *testing.T) {
	t.Setenv("AWG_EXPERIMENTAL_DEV", "false")
	t.Setenv("DEV_ALLOW_HTTP", "true")
	t.Setenv("CONTROL_PANEL_URL", "http://host.docker.internal:18650")
	if experimentalAWGEnabled() {
		t.Fatal("AWG enabled without explicit operator opt-in")
	}
	t.Setenv("AWG_EXPERIMENTAL_DEV", "true")
	t.Setenv("DEV_ALLOW_HTTP", "false")
	if experimentalAWGEnabled() {
		t.Fatal("AWG enabled outside strict local development")
	}
	t.Setenv("DEV_ALLOW_HTTP", "true")
	for _, origin := range []string{"https://vpn.example.invalid", "http://vpn.example.invalid", "http://192.0.2.55", "http://8.8.8.8"} {
		t.Setenv("CONTROL_PANEL_URL", origin)
		if experimentalAWGEnabled() {
			t.Fatal("AWG experimental driver enabled for remote origin", origin)
		}
	}
}
func TestProductionTelemetryNeverAdvertisesAWG(t *testing.T) {
	t.Setenv("AWG_EXPERIMENTAL_DEV", "false")
	t.Setenv("DEV_ALLOW_HTTP", "false")
	payload, err := controlTelemetryPayload(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	caps, ok := payload["capabilities"].([]any)
	if !ok {
		t.Fatal("telemetry lacks capabilities array")
	}
	for _, item := range caps {
		if item == "awg.protobuf.binary" {
			t.Fatal("unaccepted AWG was advertised")
		}
	}
}
