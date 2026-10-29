package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestIdentitySurvivesRestartAndPrivatePermissions(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "private")
	original, err := newIdentity()
	if err != nil {
		t.Fatal(err)
	}
	if err := writeIdentity(dir, original); err != nil {
		t.Fatal(err)
	}
	loaded, exists, err := loadIdentity(dir)
	if err != nil || !exists || loaded != original {
		t.Fatalf("identity load: %+v %v %v", loaded, exists, err)
	}
	stat, err := os.Stat(filepath.Join(dir, "identity.json"))
	if err != nil || stat.Mode().Perm() != 0600 {
		t.Fatalf("identity permissions: %v %v", stat, err)
	}
	original.Registered = true
	if err := writeIdentity(dir, original); err != nil {
		t.Fatal(err)
	}
	loaded, _, err = loadIdentity(dir)
	if err != nil || !loaded.Registered {
		t.Fatal("registered identity lost")
	}
	if err := os.Chmod(filepath.Join(dir, "identity.json"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, _, err := loadIdentity(dir); err == nil {
		t.Fatal("accepted public identity secret")
	}
}

func TestPanelOriginRequiresVerifiedHTTPS(t *testing.T) {
	for _, s := range []string{"http://panel.example", "https://user:secret@panel.example",
		"https://panel.example/path", "https://panel.example?key=value", "https://panel.example#fragment", ""} {
		if _, err := panelOrigin(s, false); err == nil {
			t.Fatalf("accepted %q", s)
		}
	}
	if s, err := panelOrigin("https://panel.example/", false); err != nil || s != "https://panel.example" {
		t.Fatalf("expected HTTPS origin, got %s %v", s, err)
	}
}

func TestAPIUsesDedicatedCredentialsWithoutLeakingThem(t *testing.T) {
	const token = "wl_disc_TEST_PRIVATE_DO_NOT_LOG"
	const secret = "agent-identity-secret"
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/node-control/") {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		if r.URL.Path == "/api/node-control/enroll" && r.Header.Get("X-Discovery-Token") != token {
			t.Error("missing invitation")
		}
		if r.URL.Path == "/api/node-control/heartbeat" && (r.Header.Get("X-Node-Secret") != secret || r.Header.Get("X-Discovery-Token") != "") {
			t.Error("unexpected heartbeat authentication")
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"status": "pending"})
	}))
	defer srv.Close()
	for _, tc := range []struct{ path, header, credential string }{
		{"/api/node-control/enroll", "X-Discovery-Token", token},
		{"/api/node-control/heartbeat", "X-Node-Secret", secret},
	} {
		reply, err := post(context.Background(), srv.Client(), srv.URL, tc.path, tc.header, tc.credential, map[string]any{"node_uuid": "synthetic"})
		if err != nil || reply.Status != "pending" {
			t.Fatalf("%s: %v %+v", tc.path, err, reply)
		}
	}
}

func TestRootlessDaemonRejectsRootfulUnixSocket(t *testing.T) {
	if err := checkRootlessDocker(context.Background(), "/var/run/docker.sock"); err == nil {
		t.Fatal("rootful Docker access accepted")
	}
	dir := t.TempDir()
	sock := filepath.Join(dir, "docker.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	binaries := filepath.Join(dir, "bin")
	if err := os.Mkdir(binaries, 0700); err != nil {
		t.Fatal(err)
	}
	docker := filepath.Join(binaries, "docker")
	if err := os.WriteFile(docker, []byte("#!/bin/sh\necho '[\"name=seccomp\"]'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", binaries+":"+os.Getenv("PATH"))
	if err := checkRootlessDocker(context.Background(), sock); err == nil {
		t.Fatal("rootful daemon accepted")
	}
	if err := os.WriteFile(docker, []byte("#!/bin/sh\necho '[\"name=rootless\"]'\n"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := checkRootlessDocker(context.Background(), sock); err != nil {
		t.Fatalf("rootless denied: %v", err)
	}
}

func TestManagedNodeTokenPrefix(t *testing.T) {
	const uuid = "0192b532-184f-41bc-9952-78e15b14cc55"
	const nonce = "0123456789012345678901234567890123456789012"
	actual, err := directTokenUUID("wl_node_" + uuid + "_" + nonce)
	if err != nil || actual != uuid {
		t.Fatalf("managed node token: %s %v", actual, err)
	}
	for _, prefix := range []string{"wl_agent_", "wl_disc_"} {
		if _, err := directTokenUUID(prefix + uuid + "_" + nonce); err == nil {
			t.Fatalf("unsupported token type %s accepted", prefix)
		}
	}
}

func TestDirectAgentTokenIdentityAndModeSelection(t *testing.T) {
	const uuid = "0192b532-184f-41bc-9952-78e15b14cc55"
	const directToken = "wl_node_" + uuid + "_0123456789012345678901234567890123456789012"
	dir := filepath.Join(t.TempDir(), "direct")
	ident, err := prepareIdentity(dir, "", directToken)
	if err != nil || ident.UUID != uuid || ident.Secret != directToken || !ident.Registered {
		t.Fatalf("direct registration identity: %+v %v", ident, err)
	}
	if _, err := prepareIdentity(dir, "", ""); err != nil {
		t.Fatalf("direct identity must survive restart with no environment secrets: %v", err)
	}
	for _, bad := range []struct{ discovery, token string }{
		{"wl_disc_abc", directToken},
		{"", "invalid"},
		{"", "wl_node_" + uuid + "_bad"},
		{"", "wl_node_00000000-0000-0000-0000-000000000000_0123456789012345678901234567890123456789012"},
	} {
		if _, err := prepareIdentity(dir, bad.discovery, bad.token); err == nil {
			t.Errorf("bad mode accepted: %+v", bad)
		}
	}
	freshDir := filepath.Join(t.TempDir(), "discovery")
	discovery, err := prepareIdentity(freshDir, "wl_disc_synthetic", "")
	if err != nil || discovery.Registered || discovery.UUID == "" {
		t.Fatalf("discovery identity: %+v %v", discovery, err)
	}
	if _, err := prepareIdentity(freshDir, "", ""); err == nil {
		t.Fatal("unregistered discovery identity must require discovery token")
	}
	switched, err := prepareIdentity(freshDir, "", directToken)
	if err != nil || switched.UUID != uuid || !switched.Registered {
		t.Fatalf("explicit direct credentials did not replace unregistered identity: %+v %v", switched, err)
	}
	if persisted, _, err := loadIdentity(freshDir); err != nil || persisted != switched {
		t.Fatalf("direct identity not persisted: %+v %v", persisted, err)
	}
	if _, err := prepareIdentity(freshDir, "wl_disc_synthetic", ""); err != nil {
		t.Fatalf("registered direct identity should survive restart: %v", err)
	}
	if err := os.Remove(filepath.Join(freshDir, "identity.json")); err != nil {
		t.Fatal(err)
	}
	discovery, err = prepareIdentity(freshDir, "wl_disc_synthetic", "")
	if err != nil {
		t.Fatal(err)
	}
	discovery.Registered = true
	if err := writeIdentity(freshDir, discovery); err != nil {
		t.Fatal(err)
	}
	if resumed, err := prepareIdentity(freshDir, "", ""); err != nil || resumed != discovery {
		t.Fatalf("discovery identity should survive without original invitation: %+v %v", resumed, err)
	}
}

func TestLocalHTTPOnlyWithExplicitDevelopmentGate(t *testing.T) {
	for _, good := range []string{
		"http://host.docker.internal:18117",
		"http://127.0.0.1:18117",
		"http://localhost:18117",
		"http://[::1]:18117",
	} {
		if _, err := panelOrigin(good, true); err != nil {
			t.Errorf("allowed DEV address rejected %s: %v", good, err)
		}
		if _, err := panelOrigin(good, false); err == nil {
			t.Errorf("DEV HTTP accepted without opt-in: %s", good)
		}
	}
	for _, bad := range []string{
		"http://192.168.1.11:18117", "http://10.0.0.5:18117",
		"http://panel.example.com", "http://evil.internal",
		"http://user:pass@host.docker.internal:18117",
		"http://host.docker.internal:18117/path",
		"https://panel.example.com/subpath",
	} {
		if _, err := panelOrigin(bad, true); err == nil {
			t.Errorf("unsafe DEV endpoint accepted: %s", bad)
		}
	}
}

func TestObsoletePersistedManagedNodeSecretIsRejected(t *testing.T) {
	const uuid = "0192b532-184f-41bc-9952-78e15b14cc55"
	dir := filepath.Join(t.TempDir(), "node-identity")
	obsolete := identity{UUID: uuid, Secret: "wl_agent_" + uuid + "_0123456789012345678901234567890123456789012", Registered: true}
	if err := writeIdentity(dir, obsolete); err != nil {
		t.Fatal(err)
	}
	if _, err := prepareIdentity(dir, "", ""); err == nil {
		t.Fatal("old persisted Agent token accepted by strict Node runtime")
	}
}
