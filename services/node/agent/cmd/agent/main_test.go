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
		if _, err := panelOrigin(s); err == nil {
			t.Fatalf("accepted %q", s)
		}
	}
	if s, err := panelOrigin("https://panel.example/"); err != nil || s != "https://panel.example" {
		t.Fatalf("expected HTTPS origin, got %s %v", s, err)
	}
}

func TestAPIUsesDedicatedCredentialsWithoutLeakingThem(t *testing.T) {
	const token = "wl_disc_TEST_PRIVATE_DO_NOT_LOG"
	const secret = "agent-identity-secret"
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/api/agent/") {
			t.Errorf("unexpected path %s", r.URL.Path)
		}
		if r.URL.Path == "/api/agent/enroll" && r.Header.Get("X-Discovery-Token") != token {
			t.Error("missing invitation")
		}
		if r.URL.Path == "/api/agent/heartbeat" && (r.Header.Get("X-Agent-Secret") != secret || r.Header.Get("X-Discovery-Token") != "") {
			t.Error("unexpected heartbeat authentication")
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"status": "pending"})
	}))
	defer srv.Close()
	for _, tc := range []struct{ path, header, credential string }{
		{"/api/agent/enroll", "X-Discovery-Token", token},
		{"/api/agent/heartbeat", "X-Agent-Secret", secret},
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
