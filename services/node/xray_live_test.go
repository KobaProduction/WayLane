package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestInjectPrivateXrayControlAPI(t *testing.T) {
	raw := json.RawMessage(`{"inbounds":[{"tag":"socks-qa","protocol":"socks","port":18443,"settings":{"auth":"password"}}],"outbounds":[{"tag":"direct","protocol":"freedom"}],"routing":{"rules":[]}}`)
	out, err := injectXrayControlAPI(raw)
	if err != nil {
		t.Fatal(err)
	}
	var config map[string]json.RawMessage
	if err := json.Unmarshal(out, &config); err != nil {
		t.Fatal(err)
	}
	if !hasXrayControlAPI(config) {
		t.Fatal("Agent-only API was not injected")
	}
	if !strings.Contains(string(out), "127.0.0.1:10085") {
		t.Fatal("no private loopback")
	}
	if _, err := injectXrayControlAPI(json.RawMessage(`{"api":{"listen":"0.0.0.0:10085"}}`)); err == nil {
		t.Fatal("public Xray API override accepted")
	}
	again, err := injectXrayControlAPI(out)
	if err != nil || !sameJSON(again, out) {
		t.Fatal("agent API injection must be idempotent", err)
	}
}

func TestXrayLiveOnlyAllowsHandlerAndRoutingChanges(t *testing.T) {
	orig := map[string]json.RawMessage{
		"api":      json.RawMessage(`{"tag":"waylane-agent-control","listen":"127.0.0.1:10085"}`),
		"inbounds": json.RawMessage(`[{"tag":"a"}]`), "outbounds": json.RawMessage(`[{"tag":"direct"}]`),
		"routing": json.RawMessage(`{"rules":[]}`),
		"log":     json.RawMessage(`{"loglevel":"warning"}`),
	}
	next := map[string]json.RawMessage{}
	for k, v := range orig {
		next[k] = v
	}
	next["routing"] = json.RawMessage(`{"rules":[{"outboundTag":"direct"}]}`)
	if err := checkLiveFields(orig, next); err != nil {
		t.Fatal(err)
	}
	next["log"] = json.RawMessage(`{"loglevel":"debug"}`)
	if err := checkLiveFields(orig, next); err != errLiveRequiresRestart {
		t.Fatal("unsupported log hot change accepted")
	}
	next["log"] = orig["log"]
	next["api"] = json.RawMessage(`{"listen":"0.0.0.0:10085"}`)
	if err := checkLiveFields(orig, next); err != errLiveRequiresRestart {
		t.Fatal("unsafe API change accepted")
	}
}

func TestXrayScopedUserDelta(t *testing.T) {
	old := json.RawMessage(`{"tag":"vless-in","protocol":"vless","port":18445,"settings":{"clients":[{"id":"7ac0b3ef-cfc5-4868-8ab9-5379c66a4c50","email":"qa1@test.invalid"}],"decryption":"none"}}`)
	newValue := json.RawMessage(`{"tag":"vless-in","protocol":"vless","port":18445,"settings":{"clients":[{"id":"e9242bc9-aae0-413b-92f0-3b7d4580f025","email":"qa2@test.invalid"}],"decryption":"none"}}`)
	protocol, removed, added, ok := usersOnlyDelta(old, newValue)
	if !ok || protocol != "vless" || len(removed) != 1 || len(added) != 1 {
		t.Fatalf("missing scoped user delta %v %v %d %d", protocol, ok, len(removed), len(added))
	}
	scoped := makeUserDeltaInbound(newValue, added[0])
	if !strings.Contains(string(scoped), "qa2@test.invalid") || strings.Contains(string(scoped), "qa1@test.invalid") {
		t.Fatal("client delta not narrowed")
	}
	noEmail := json.RawMessage(`{"tag":"vless-in","protocol":"vless","port":18445,"settings":{"clients":[{"id":"e9242bc9-aae0-413b-92f0-3b7d4580f025"}],"decryption":"none"}}`)
	if _, _, _, valid := usersOnlyDelta(old, noEmail); valid {
		t.Fatal("anonymous client accepted as hot user")
	}
	socks := json.RawMessage(`{"tag":"socks","protocol":"socks","settings":{"accounts":[]}}`)
	if _, _, _, valid := usersOnlyDelta(socks, socks); valid {
		t.Fatal("SOCKS user API not supported")
	}
}

func TestAgentTelemetryParser(t *testing.T) {
	total, available := parseProcMemory("MemTotal: 1048576 kB\nMemAvailable: 256000 kB\nMemFree: 1 kB\n")
	if total != 1073741824 || available != 262144000 {
		t.Fatalf("invalid KiB parsing: %d %d", total, available)
	}
	snap := collectNodeRuntimeMetrics(t.TempDir())
	if snap.Scope != "agent-runtime" || snap.CPULogicalCount < 1 || snap.OS == "" {
		t.Fatalf("invalid Agent runtime metrics %+v", snap)
	}
}
